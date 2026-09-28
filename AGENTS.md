# NcatBotTRPG 插件架构与修改指南

## 概述

NcatBotTRPG（NcatBot 5）自研纯插件：通用掷骰 + 极简 AI 跑团主持（openai SDK）+ 三层 RBAC 权限体系。
本插件为 git 子模块（仓库 `Yang-qwq/NcatBotTRPG`）——修改后须在子模块目录内 `git commit`。

## 仓库事实

- **无 linter、无 typechecker、无 pyproject.toml**；依赖见 `requirements.txt`，同时在 `manifest.toml`
  的 `[pip_dependencies]` 声明（框架可自动安装）。
- 仅第三方依赖 `openai`（Apache-2.0）。
- 许可证 **AGPL-3.0**；引用第三方代码须遵守 `NOTICE` 的规范（见「许可证与引用」）。

## 目录结构

| 层 | 文件 | 职责 |
|---|---|---|
| 入口 | `__init__.py` → `main.py:NcatBotTRPGPlugin` | 生命周期、配置默认值、RBAC 注册、消息触发、AI 主持 |
| 命令 | `command_handler.py:NcatBotTRPGCommandMixin` | `/trpg`、`/trpg-admin`、`/trpgrbac`、三层权限、帮助文本 |
| 掷骰 | `dice.py` | 纯函数掷骰引擎（解析/投掷/判定/格式化） |
| LLM | `llm.py` | openai 客户端封装、默认主持人提示词、PromptFile 导入机制、消息前缀格式 |
| 会话 | `session.py:SessionStore` | `data['sessions']` 的读写与截断 |

MRO：`NcatBotTRPGPlugin(NcatBotTRPGCommandMixin, NcatBotPlugin)`。

## 消息触发（main.py）

- 群聊 `on_group_message`：跳过 `/` 命令；仅当会话激活时处理；先匹配局内掷骰关键词，
  再按 `MustAtBot`（默认需 @机器人）判定是否作为 AI 行动。
- 私聊 `on_private_message`：跳过 `/` 命令；激活时匹配关键词，否则作为 AI 行动。
- `_handle_action`：拼接 `[system] + history` → `llm.chat` → 回复 → 追加助手消息 → `_save_data`。
  其中 system 会注入缓存的掷骰结果（`build_roll_context`）。LLM 同步调用经 `asyncio.to_thread`，
  异常统一 `_log.error(traceback)` + 友好回复。
- **跨插件协调**：公开 `is_group_session_active(group_id)` / `is_user_session_active(user_id)`；
  OpenAIChatPlugin 在分发 @机器人 消息前调用，跑团进行中时让出处理权，避免两插件重复回复。

## 命令体系

命令通过 `@registrar.qq.on_command('/xxx')` 独立注册（群+私聊均可触发，方法内用
`isinstance(event, GroupMessageEvent)` 判群）。参数经 `_parse_command(event)`（shlex 封装，
失败回复“命令格式错误”）。管理/团操作按 scope 决定是否校验权限。

- **`/trpg`**：`roll` `rh` `start` `act` `status` `prompt` `reset` `stop` `help`
- **`/trpg-admin`**：`stop` `reset` `prompt` `help`，支持 `group:<id>` / `user:<id>` 目标
- **`/trpgrbac`**：`grant` `revoke` `list` `help`

内部复用：`_apply_prompt` / `_apply_reset` / `_apply_stop` 返回回复文本，供 `/trpg` 与
`/trpg-admin` 共用。

## 权限体系（三层）

权限点 `NcatBotTRPG.admin`：

1. **全局管理员**（RBAC）— `_check_global_admin()` → `check_permission(uid, 'NcatBotTRPG.admin')`；
   `on_load()` 幂等自动授权 `config.yaml` 的 `root`；`/trpgrbac` 管理。
2. **群主/群管理自动放行** — `_is_group_privileged()` 查 `get_group_member_list` 的
   `role in ('owner','admin')`，300s TTL 缓存（`_group_role_cache`），受 `EnableGroupOwnerAutoAuth` 控制。
3. **默认拒绝** — RBAC 不可用一律拒绝（fail-closed）。

校验方法：`_check_admin`（全局或本群群管理，用于群内 `/trpg start|prompt|reset|stop` 与
`/trpg-admin`）、`_check_global_admin`（仅全局，用于 `/trpgrbac`）。

**新增命令约定**：
- 用户命令（所有人）：直接处理，方法内判群/私聊。
- 群管理命令：群聊首行 `if scope == 'group' and not await self._check_admin(event): return`。
- 授予/撤销全局权限命令：首行 `if not await self._check_global_admin(event): return`（防提权）。
- 修改权限逻辑后须同步更新 `tests/test_permission.py`（API 级断言）与本文件。

## 掷骰引擎（dice.py）

- `parse(expr_text, *, max_count, max_sides, ...)` → `ParsedRoll`；非法抛 `DiceError`。
- `roll(parsed, rng=None, enable_critical=True)` → `RollOutcome`；`roll_expression` 为便捷封装。
- `format_outcome(outcome)` → 可直接发送的中文文本。
- 语法：`NdM±K`、`adv`/`dis`、`bonusN`/`penaltyN`、`<=|>=|<|>|==` 判定。
- **不支持重复投掷（`N#expr`）**：含 `#` 一律抛 `DiceError`，一次行动只能掷一次（防刷骰）。
- 优势/劣势仅单个 d20；奖励/惩罚骰仅单个 d100（CoC 十位取大/取小，00+0=100）且与优势互斥。
- 上限：骰数/面数/修正/奖励骰数量。
- 大成功/大失败：d20 20/1；d100 1/100，目标 <50 时 96~99 大失败。

**修改约定**：保持纯函数、可注入 `rng`；新增语法须补 `tests/test_dice_engine.py` 用例；
文档与 `format_outcome` 同步更新。

## 会话与持久化

- `self.data['sessions'] = {'group': {}, 'user': {}}`，`SessionStore` 持其引用；
  结构 `{active, prompt, history, pending_rolls, started_at}`。
- 修改后调用 `self._save_data()`。`history` 按 `MaxHistoryMessages` 截断。
- `session.py` 的 `create/update/clear_history/append_turn` 只操作内存，由调用方持久化。
- **掷骰结果传递**：`pending_rolls` 为 `{user_id, text}` 列表，每位玩家至多一条未结算掷骰。
  `_do_roll` 在团激活时先 `has_pending_roll` 拦截重复投掷，再 `add_pending_roll(user_id, ...)`；
  `_handle_action` 用 `peek_pending_rolls` 经 `llm.build_roll_context()` 注入 system prompt，
  成功回复后 `clear_pending_rolls`；`_apply_reset` 一并清空。

## 配置项

见 `main.py:on_load()` 的 `init_defaults`；读取整型配置统一用 `_get_int_config(key, default)`
（兼容字符串含 `|` 的复合值）。`PromptFile` 为主持人提示词文件路径，`StripReasoning`
控制思维链剥离（默认开）。写配置用 `self.set_config(key, value)`。

## AI 主持与 LLM

- `llm.build_system_prompt(custom, default_prompt)` = 默认主持人提示词 + 可选「本团专属设定」；
  默认提示词优先取 `PromptFile` 导入内容，否则用内置 `DEFAULT_KEEPER_PROMPT`。
- **Prompt 导入**：`main.reload_prompt()` → `llm.load_prompt_file(plugin, path)`；
  相对路径基于 `plugin.workspace` 解析；缺失/为空/IO 错误仅告警并回退内置默认；`on_load` 自动调用。
- `llm.LLMClient.chat(messages, ...)` 经 `asyncio.to_thread` 调 openai SDK。
- **思维链隔离**：`llm.strip_reasoning()` 剥离 `<think>`/`<reasoning>` 等标签及 DeepSeek-R1
  全角分隔符（未闭合开标签连同其后内容移除；缺少开标签的孤儿闭标签如仅剩 `</think>`
  连同其前的思维链残留一并移除），`StripReasoning` 配置（默认开）控制；
  仅当正文为空时忽略独立 `reasoning_content` 字段，避免暴露思维链。
- **思维链日志**：`llm.extract_reasoning()` 提取思维链内容，`chat()` 以
  `_log.info(f'AI思维链: {...截断到 OMITTED_TEXT_LENGTH}')` 打印（参考其他插件日志风格）。
- **v1 不启用 Function Calling / 不修改状态**；若后续引入工具调用，须遵循
  「数值与状态由系统裁定、LLM 只提议与叙事」的原则（参考 diceframe 权威模型）。

## 测试要求

- `tests/` 使用 `ncatbot.testing.PluginTestHarness` + MockAdapter 离线驱动；
  事件驱动测试文件须 `pytestmark = pytest.mark.asyncio(mode="strict")`。
- **必须隔离真实数据**：每用例 `monkeypatch.chdir(tmp_path)` + `conftest.py` 设
  `NCATBOT_CONFIG_PATH` 指向 `tests/ncatbot_test_config.yaml`（root=123456），禁止触碰真实
  `config.yaml` / `data/`。
- AI 链路测试用 `tests/fake_llm.FakeLLMClient` 替换 `plugin.llm._client`，禁止真实网络。
- Prompt 导入测试用 `tests/fixtures/example_prompt.md`（仅测试用），断言 system 消息内容。
- 权限用例须断言底层 API 行为（`get_group_member_list` 是否被调用、缓存命中、严格路径不查询群角色）。
- `test_dice_engine.py`、`test_reasoning_unit.py` 为纯函数测试（无需 async 标记），其余为事件链路测试。
- 运行（插件目录内）：`python -m pytest tests -v -o "addopts="`。

## 编码约定

- 中文注释与 `:param`/`:return` docstring；日志用 `get_log('ncatbot_trpg')`。
- 回复统一 `at_sender=False`；暗骰在群聊走 `self.api.qq.post_private_msg(...)`。
- 错误处理：`try/except` → `_log.error` + 失败回复。
- 文件保留 `# -*- coding: utf-8 -*-`（与同仓库插件一致）。

## 许可证与引用

- 本插件整体 AGPL-3.0。**新增/修改代码时，若复制或改写第三方代码，必须**：
  1. 文件头标注来源（仓库、路径、commit/tag、许可证）；
  2. 在 `NOTICE` 登记并说明修改；
  3. 保留原版权声明与许可证全文；
  4. 仅使用许可证兼容的代码（AGPL-3.0 / MIT 可；未兼容许可证不可）。
- 设计参考 diceframe（AGPL-3.0）、SillyTavern（AGPL-3.0）、TRPG-AI-DM（MIT），v1 未复制其代码。

## 新增功能指引

1. 掷骰类：改 `dice.py` + 补 `tests/test_dice_engine.py`。
2. 命令类：`command_handler.py` 加方法并在 `on_trpg`/`on_trpg_admin` 分派；加帮助文本。
3. AI/会话类：改 `llm.py` / `session.py` / `main.py`，注意持久化与异常处理。
4. 涉及权限：按「权限体系」约定校验，并补 `tests/test_permission.py`。
