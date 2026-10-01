# NcatBotTRPG 插件架构与修改指南

## 概述

NcatBotTRPG（NcatBot 5）：通用掷骰 + 极简 AI 跑团主持（openai SDK）+ 三层 RBAC 权限体系。
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
| 命令 | `command_handler.py:NcatBotTRPGCommandMixin` | `/trpg`（roll/start/act/status/prompt/reset/stop）、`/trpg-admin`、`/trpgrbac`、三层权限、帮助文本 |
| 房间命令 | `room_commands.py:RoomCommandMixin` | `/trpg join\|leave\|participants` 与 `/trpg-admin room-admin *` |
| 玩家状态 | `player_status_commands.py:PlayerStatusCommandMixin` | `/trpg away`/`offline`/`back`/`ai-control` |
| 提示词命令 | `prompt_commands.py:PromptCommandMixin` | `/trpg prompt list`/`switch`/`show`（模板管理） |
| 掷骰 | `dice.py` | 纯函数掷骰引擎（解析/投掷/判定/格式化） |
| LLM | `llm.py` | openai 客户端封装、工具循环、内置默认提示词、消息前缀格式 |
| 工具 | `tools.py` | Function Calling 注册表/分发：`request_check` + 只读 + 受控写 |
| 状态 | `state.py:StateStore` | 世界状态权威层：原子 `apply_ops` + 字典 + 投影 + 事件账本 |
| 字典 | `entities.yaml` | 实体 ID -> 展示名/描述（物品/属性/状态） |
| 世界书 | `lore.py` | 世界书检索接口（阶段 3 预留，当前返回空） |
| 会话 | `session.py:SessionStore` | `data['sessions']` 的读写与截断、房间/玩家状态、检定请求 |

MRO：`NcatBotTRPGPlugin(RoomCommandMixin, PlayerStatusCommandMixin, PromptCommandMixin, NcatBotTRPGCommandMixin, NcatBotPlugin)`。
各命令 Mixin 通过 MRO 复用 `command_handler` 的 `_scope_sid`/`_check_admin`/`_save_data` 等基础设施。

## 消息触发（main.py）

- 群聊 `on_group_message`：跳过 `/` 命令；仅当会话激活时处理；先匹配局内掷骰关键词，
  再按 `MustAtBot`（默认需 @机器人）判定是否作为 AI 行动。
- 私聊 `on_private_message`：跳过 `/` 命令；激活时匹配关键词，否则作为 AI 行动。
- `_handle_action`：拼接 `[system] + history` → `llm.chat` → 回复 → 追加助手消息 → `_save_data`。
  其中 system 会注入缓存的掷骰结果（`build_roll_context`）、群聊房间的参与人员/玩家状态/AI 托管请求
  （房间未创建或为空时自动跳过）。LLM 同步调用经 `asyncio.to_thread`，异常统一 `_log.error(traceback)` + 友好回复。
- **跨插件协调**：公开 `is_group_session_active(group_id)` / `is_user_session_active(user_id)`；
  OpenAIChatPlugin 在分发 @机器人 消息前调用，跑团进行中时让出处理权，避免两插件重复回复。

## 命令体系

命令通过 `@registrar.qq.on_command('/xxx')` 独立注册（群+私聊均可触发，方法内用
`isinstance(event, GroupMessageEvent)` 判群）。参数经 `_parse_command(event)`（shlex 封装，
失败回复“命令格式错误”）。管理/团操作按 scope 决定是否校验权限。

- **`/trpg`**：`roll` `rh` `start` `act` `status` `prompt` `reset` `stop` `help`
  `join` `leave` `participants`、`away` `offline` `back` `ai-control`
  （房间命令为一级命令，已移除 `/trpg room` 路由；房间/玩家状态为群聊功能；房间为默认存在，无需创建；
  房间状态已合并进 `/trpg status`）
- **`/trpg cheat <系统提示词> [true|false]`**（仅管理员）：向会话历史插入一条 system 消息，
  布尔参数可选、默认 `true`（立即触发一次主持人回复，复用 `_run_host`），`false` 仅插入；
  末尾 token 为布尔值时才作为开关解析，否则整体视为系统提示词。用于调试。
  发送前由 `llm.LLMClient._normalize` 按配置 `MergeSystemMessages` 规整 system：默认 `True` 会把所有
  system 合并为**唯一置顶** system（兼容智谱 GLM 等仅接受单条 system 的厂商，否则 400/1214）；
  置 `False` 则保留多条 system（**缓存友好**，要求后端支持）。无工具与工具循环两条路径均应用。
  工具路径若遇 4xx 仅本次回退，不永久关闭工具（`_tools_unsupported`）。
- **`/trpg-admin`**：`stop` `reset` `prompt` `room-admin` `help`，支持 `group:<id>` / `user:<id>` 目标
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
  会话结构 `{active, prompt, history, pending_rolls, started_at}`；
  另有 `rooms`（`group_id -> room_info`）支撑房间系统。
- 修改后调用 `self._save_data()`。`history` 按 `MaxHistoryMessages` 截断。
- `session.py` 的 `create/update/clear_history/append_turn` 只操作内存，由调用方持久化。
- **房间系统**（与会话绑定）：每群默认有且只有一个房间，`get_or_create_room()` 惰性创建并持久化
  （默认 0 人）；`room_view()` 为只读视图（不落库，供 `/trpg status` 展示房间信息）；
  `join_room` 加入、`leave_room` 离开（清空后仍保留房间，不删除）；
  `/trpg start` 自动把发起人加入房间再 `start_room`；`finish_room` 标记结束（可重新 start/join）；
  `delete_room()` 仅管理员重置房间（下次访问会重新惰性创建）。
- **玩家状态**：`set_player_status` 等以 `requested_ai_control/away/offline/active` 标记，
  经 `build_player_status_info` 注入 system prompt。
- **掷骰结果传递**：`pending_rolls` 为 `{user_id, text}` 列表，每位玩家至多一条未结算掷骰。
  `_do_roll` 在团激活时先 `has_pending_roll` 拦截重复投掷，再 `add_pending_roll(user_id, ...)`；
  `_handle_action` 用 `peek_pending_rolls` 经 `llm.build_roll_context()` 注入 system prompt，
  成功回复后 `clear_pending_rolls`；`_apply_reset` 一并清空。

## 配置项

见 `main.py:on_load()` 的 `init_defaults`；读取整型配置统一用 `_get_int_config(key, default)`
（兼容字符串含 `|` 的复合值）。提示词配置：`PromptConfigFile`（默认 `prompts.yaml`，相对插件
工作区或绝对路径）、`ActivePrompt`（当前激活的模板名）、`AvailablePrompts`（运行时加载的模板名
列表）。`StripReasoning` 控制思维链剥离（默认开）。写配置用 `self.set_config(key, value)`。

## AI 主持与 LLM

- `llm.build_system_prompt(custom, default_prompt, plugin)` = 激活的提示词模板 + 可选「本团专属设定」；
  传 `plugin` 时取 `plugin.get_active_prompt()`，否则用 `default_prompt`，最终兜底内置
  `DEFAULT_KEEPER_PROMPT`。
- **提示词模板**：`main.load_prompts_config()` 从 `PromptConfigFile`（默认插件工作区的
  `prompts.yaml`）加载 `{name: {content, ...}}`，`reload_prompt()` 重新加载并按 `ActivePrompt`
  设置当前提示词；文件缺失/格式错误回退内置默认（`_get_builtin_prompts()`，default 复用
  `llm.DEFAULT_KEEPER_PROMPT`）。`/trpg prompt list|switch|show` 管理（需全局管理员）。
- `llm.LLMClient.chat(messages, ...)` 经 `asyncio.to_thread` 调 openai SDK。
- **思维链隔离**：`llm.strip_reasoning()` 剥离 `<think>`/`<reasoning>` 等标签及 DeepSeek-R1
  全角分隔符（未闭合开标签连同其后内容移除；缺少开标签的孤儿闭标签如仅剩 `</think>`
  连同其前的思维链残留一并移除），`StripReasoning` 配置（默认开）控制；
  仅当正文为空时忽略独立 `reasoning_content` 字段，避免暴露思维链。
- **思维链日志**：`llm.extract_reasoning()` 提取思维链内容，`chat()` 以
  `_log.info(f'AI思维链: {...截断到 OMITTED_TEXT_LENGTH}')` 打印（参考其他插件日志风格）。
- **Function Calling（v1.1 起启用）**：工具循环见下节；核心原则是
  「数值与状态由系统裁定、LLM 只提议与叙事」（diceframe 权威模型）。

## Function Calling / 检定下发

- **接入方式**：插件自带 `openai` SDK 直连 `tools=`（不用框架 `api.ai` 的 MCP）。默认开
  （`EnableFunctionCalling=True`）；首轮工具调用异常时回退无工具并置内存标志
  `_tools_unsupported`（`ToolCallFallback`，不持久化）。
- **工具循环**（`llm.LLMClient._chat_with_tools`）：`finish_reason=='stop'` 或无 `tool_calls`
  即结束；否则追加 assistant(含 tool_calls) → 逐个执行 → 追加 `role:tool` → 重试。每次调用前
  `repair_tool_message_pairs` 保证协议顺序；错误以 `role:tool` 回灌（ReAct）；`MaxToolCallRounds`
  限制轮数，`MaxToolCallsPerRound` 限制单轮工具数；同轮 `name+sorted(args)` 去重。
- **工具**（`tools.build_tools(enable_mutation)` / `tools.execute`）：
  - `request_check`（**成功下发后终止本回合**）：把「骰式 + 目标值」下发给玩家，玩家自行掷骰。`target` 为
    玩家 → 本人掷；为 NPC/实体 → 系统**随机指定在场玩家代掷**。`difficulty` 必填（由 LLM 给出、
    系统 `dice.parse` 校验）；缺失/非法时返回 error 由模型重试（不终止），成功才写入
    `session['pending_checks']` 并结束本回合。
  - 只读：`get_state`（短投影/明细）、`search_lore`（阶段 3 前返回空）。
  - 受控写（`EnableStateMutationTools` 门控）：`change_hp` / `change_attribute` /
    `manage_inventory` / `update_scene` / `record_event`。数值统一走 `StateStore.apply_ops`
    （草稿整体校验后提交 + `rev+1`，失败回滚，fail-closed）。
- **掷骰权归玩家**：`dice.py`、`/trpg roll|rh`、`.r|.rh`、`pending_rolls` **保持原样**。
  `command_handler._do_roll` 在团激活时用 `dice.roll_key(parsed)` 匹配 `pending_checks`：
  **骰式一致才算完成**，成败由系统按请求记录的目标值用 `dice.judge` 裁定（玩家命令里的判定
  被忽略，保证权威），并写入 `resolved_checks`；随后经 `build_check_context` 注入主持人。
- **伤害门控**：`change_hp` 负向变更要求该玩家存在未消费的已结算检定
  （`SessionStore.has_resolved_check`），否则拒绝（`discard_unresolved_player_damage` 思路）。
- **状态四层**（`state.py` + `entities.yaml`）：权威层紧凑（ID + `rev` + 事件账本）→ 注入层
  短可读投影（`StateStore.project`，`StateProjectionDetail/Budget`）→ 展示层懒渲染
  （`detail='full'`）→ 工具按需查询（`get_state`）。
- **并发**：`main._handle_action` 用会话级 `asyncio.Lock` 串行化同一会话回合；LLM 前后记录
  `StateStore.rev` 作栅栏日志。工具轮中间消息不落库，仅持久化最终 assistant 正文。
- **系统提示硬约束**：`llm.TOOL_GUIDANCE` 追加到 system（必须 `request_check`、必须用写工具、
  下发后等待、只按系统结果叙事）。
- **新增工具约定**：在 `tools.py` 加 schema + 执行器，写工具须经 `apply_ops` 校验并纳入
  `build_tools(enable_mutation)`；终止型工具在执行器返回 `terminal=True`；补 `tests/test_tools.py`。

## 测试要求

- `tests/` 使用 `ncatbot.testing.PluginTestHarness` + MockAdapter 离线驱动；
  事件驱动测试文件须 `pytestmark = pytest.mark.asyncio(mode="strict")`。
- **必须隔离真实数据**：每用例 `monkeypatch.chdir(tmp_path)` + `conftest.py` 设
  `NCATBOT_CONFIG_PATH` 指向 `tests/ncatbot_test_config.yaml`（root=123456），禁止触碰真实
  `config.yaml` / `data/`。
- AI 链路测试用 `tests/fake_llm.FakeLLMClient` 替换 `plugin.llm._client`，禁止真实网络。
- 提示词模板测试用 `tests/fixtures/prompts.yaml`（仅测试用），断言 system 消息内容；
  切换提示词会 `set_config` 持久化，测试中须 stub `plugin.set_config` 避免污染真实配置。
- 权限用例须断言底层 API 行为（`get_group_member_list` 是否被调用、缓存命中、严格路径不查询群角色）。
- `test_dice_engine.py`、`test_reasoning_unit.py`、`test_state.py` 为纯函数测试（无需 async 标记），其余为事件链路测试。
- Function Calling：`test_tools.py`（schema 组合 / 写工具校验 / 伤害门控）、
  `test_check_request.py`（`request_check` 下发 → 玩家掷骰匹配结算 → 注入主持人 / NPC 代掷 / 暗骰 / 骰式不匹配不结算）。
  `fake_llm.FakeLLMClient` 支持脚本化 `tool_calls`（dict 形式的脚本项）。
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
