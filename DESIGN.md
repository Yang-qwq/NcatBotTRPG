# NcatBotTRPG 第一版设计（v0.1.0）

本文件记录 NcatBotTRPG 第一版的定稿设计，作为后续迭代的依据。

## 0. 决策记录

| 项 | 结论 |
|---|---|
| 架构路线 | 不依赖外部服务，不移植整套引擎 |
| 规则系统 | 通用掷骰 + 自由叙事 |
| LLM 接入 | 内置 openai SDK（`ApiKey`/`Model`/`BaseUrl`，与 OpenAIChatPlugin 一致） |
| v1 范围 | 骨架 + RBAC + 掷骰 + 极简 AI 主持 |
| 掷骰触发 | `/trpg roll` / `/trpg rh` 始终可用 + 局内关键词 `.r` / `.rh` |
| 插件许可证 | **AGPL-3.0**（与同仓库插件一致，且兼容参考项目） |

## 1. 目录结构

```
plugins/NcatBotTRPG/
├── manifest.toml           # name="NcatBotTRPG" entry_class="NcatBotTRPGPlugin" deps: openai
├── __init__.py             # 导出 NcatBotTRPGPlugin
├── main.py                 # 入口：init_defaults、RBAC 注册 + root 自动授权、消息触发、AI 主持
├── command_handler.py      # Mixin：/trpg、/trpg-admin、/trpgrbac；三层权限；帮助文本
├── room_commands.py        # Mixin：/trpg room *、/trpg-admin room-admin *
├── player_status_commands.py # Mixin：/trpg away|offline|back|ai-control
├── prompt_commands.py      # Mixin：/trpg prompt list|switch|show（模板管理）
├── dice.py                 # 掷骰引擎（纯函数）
├── llm.py                  # LLM 客户端封装（openai SDK，可替换单元）+ 默认主持人提示词
├── session.py              # 团会话状态存取（DataMixin data['sessions']）
├── LICENSE                 # AGPL-3.0
├── NOTICE                  # 参考来源与许可证声明
├── requirements.txt        # openai
├── DESIGN.md               # 本文件
├── README.md / AGENTS.md
└── tests/                  # conftest + ncatbot_test_config.yaml + fake_llm + fixtures/prompts.yaml + 9 个测试文件
```

MRO：`NcatBotTRPGPlugin(RoomCommandMixin, PlayerStatusCommandMixin, PromptCommandMixin, NcatBotTRPGCommandMixin, NcatBotPlugin)`。
掷骰与 LLM 保持独立单元，未来可替换为 NcatBot 内置 AI 适配器（`api.ai`）或 diceframe 后端而不改动命令层。

## 2. 命令总览

| 命令 | 权限 | 说明 |
|---|---|---|
| `/trpg roll <骰式> [判定]` | 所有人 | 公开掷骰/检定（群+私聊） |
| `/trpg rh <骰式> [判定]` | 所有人 | 暗骰，结果私聊发起者 |
| `.r` / `.rh <骰式>` | 所有人 | 局内关键词，仅在团激活时生效 |
| `/trpg start [设定]` | 三层管理员(群)/本人(私聊) | 激活本会话 AI 跑团 |
| `/trpg act <行动>` | 所有人 | 向 AI 主持提交行动（`@机器人` 等价） |
| `/trpg status` | 所有人 | 查看当前团状态 |
| `/trpg prompt [set <设定>\|show\|reset]` | 三层管理员(群)/本人(私聊) | 查看/设置本团专属设定 |
| `/trpg reset` | 三层管理员(群)/本人(私聊) | 清空剧情历史，保留设定 |
| `/trpg stop` | 三层管理员(群)/本人(私聊) | 结束本会话团 |
| `/trpg help` | 所有人 | 用户帮助 |
| `/trpg-admin stop\|reset\|prompt [group:<id>\|user:<id>]` | 三层管理员 | 跨会话管理 |
| `/trpgrbac grant\|revoke\|list <qq>` | **仅全局管理员** | 管理全局管理员（防群主提权） |

## 3. 掷骰引擎（`dice.py`）

支持语法：

- `NdM` / `dM`：掷 N 个 M 面骰（N 缺省 1），如 `1d100`、`d20`、`2d6`
- `NdM±K`：总和加减修正，如 `1d20+3`、`3d8-2`
- `adv` / `dis`（或 advantage / disadvantage）：仅单个 d20，掷两次取高/取低，同时出现则抵消
- `bonusN` / `penaltyN`：仅单个 d100，克苏鲁式奖励/惩罚骰（十位取小/取大，N 缺省 1）
- 判定：`<=` / `>=` / `<` / `>` / `==` 后接目标值，如 `1d100 <= 50`

不支持重复投掷（`N#expr`）：一次行动只能掷一次，防止玩家刷骰。

大成功/大失败（可由 `EnableCriticalDice` 关闭）：d20 的 20/1；d100 的 1/100，且目标成功率
低于 50 时 96~99 计大失败。防刷上限：骰数 ≤ `MaxDiceCount`、面数 ≤ `MaxDiceSides`、
修正 ≤ 100、奖励/惩罚骰 ≤ 10。

公开骰直接回复；暗骰在群聊中私聊发起者、群内仅提示，私聊中直接回复。

## 4. AI 主持（`llm.py` + `main.py`）

- 团激活后，`@机器人 <文本>`（`MustAtBot=True` 时）或 `/trpg act <文本>` 作为玩家行动。
- 消息序列 = `[system: 激活的提示词模板 + 本团专属设定] + 最近 MaxHistoryMessages 条历史`；
  提示词模板来自 `PromptConfigFile`（默认插件工作区的 `prompts.yaml`），按 `ActivePrompt`
  选用，缺失/无效时回退内置 `llm.DEFAULT_KEEPER_PROMPT`。
- `openai` SDK 的同步调用经 `asyncio.to_thread` 执行，避免阻塞事件循环。
- **思维链隔离**：`llm.strip_reasoning()` 剥离 `<think>…</think>`、`<reasoning>…</reasoning>` 等
  思维链标签及 DeepSeek-R1 全角分隔符（未闭合开标签连同其后内容移除；缺少开标签的
  孤儿闭标签如仅剩 `</think>` 连同其前思维链一并移除）；仅返回正文，
  避免推理模型把思维链暴露给群成员或写入历史（`StripReasoning` 可关闭）。
- **思维链日志**：`llm.extract_reasoning()` 提取思维链内容，`LLMClient.chat()` 以
  `AI思维链: …` 的 INFO 日志打印（截断到 `OMITTED_TEXT_LENGTH` 字），便于调试排查。
- v1 **不调用 Function Calling、不修改任何状态**；需要检定时由主持人在叙事中提示，玩家手动掷骰。
- **掷骰结果传递**：团内掷骰（`/trpg roll|rh` 或局内关键词）会缓存到会话
  `pending_rolls`（含掷骰人、骰式、结果、暗骰标记）；玩家下一次行动时经
  `llm.build_roll_context()` 注入 system prompt 交给主持人，随后清空（避免重复注入）。
  同一玩家在结果被结算前**不能重复投掷**（`has_pending_roll` 拦截），防止刷骰；
  不同玩家可各自持有一条待结算掷骰。
- 群聊行动默认附带 `昵称(QQ)：` 前缀（`InsertUserdataAsPrefix`），便于区分玩家。
- **与聊天插件协调**：公开 `is_group_session_active` / `is_user_session_active` 查询接口；
  OpenAIChatPlugin 在分发 @机器人 消息前查询，跑团进行中的会话由本插件接管，
  避免两插件重复回复（仅读取状态，不依赖 handler 执行顺序）。

## 4.1 提示词模板机制

`PromptConfigFile`（默认 `prompts.yaml`，相对插件工作区或绝对路径）为 YAML 模板集合，
形如 `{name: {name, description, content}}`；`on_load()` 经 `main.load_prompts_config()`
加载并保存到 `plugin.available_prompts`，当前使用的模板由 `ActivePrompt` 决定
（`plugin.get_active_prompt()`）。`llm.build_system_prompt(custom, default_prompt, plugin)`
组装最终 system 提示词：优先取激活模板，缺省时兜底内置 `llm.DEFAULT_KEEPER_PROMPT`。
文件缺失/格式错误仅告警并回退内置默认（`main._get_builtin_prompts()`），不阻塞加载。
`reload_prompt()` 支持配置变更后重新加载；`/trpg prompt list|switch|show` 需全局管理员。
测试用模板见 `tests/fixtures/prompts.yaml`。

## 5. 团会话（`session.py`）

会话键：群聊按 `group_id`，私聊按 `user_id`。每个会话保存
`{active, prompt, history, pending_rolls, started_at}`，其中 `pending_rolls` 为
`{user_id, text}` 列表（每位玩家至多一条未结算掷骰）。`reset` 清空 `history` 与
`pending_rolls` 并保留 `prompt` 与 `active`；`stop` 置 `active=False` 并保留历史
（群聊同时 `finish_room` 房间）。

**房间系统（与会话绑定，默认存在）**：`sessions['rooms']`（`group_id -> room_info`）。
每群有且只有一个房间，`get_or_create_room()` 惰性创建并持久化默认房间（0 人），无需
`/trpg room create`；`room_view()` 为只读视图供状态展示（不落库）。玩家经 `/trpg room join`
加入，`/trpg start` 自动把发起人加入房间。状态 `preparing/running/finished` 由
`start_room`/`finish_room` 管理；`leave_room` 清空后仍保留房间（不删除）。
`player_statuses` 记录 `active/away/offline/requested_ai_control`，经
`build_participants_info` / `build_player_status_info` 注入 system prompt。

## 6. 权限体系（三层 RBAC）

权限点 `NcatBotTRPG.admin`：

1. **全局管理员**（RBAC，跨会话）— `check_permission(uid, 'NcatBotTRPG.admin')`；
   `on_load()` 幂等自动授权 `config.yaml` 的 `root`；`/trpgrbac` 授予/撤销/查看。
2. **群主/群管理自动放行**（本群）— `_is_group_privileged()` 查
   `get_group_member_list` 的 `role in ('owner','admin')`，300s TTL 缓存，
   受 `EnableGroupOwnerAutoAuth` 控制。
3. **默认拒绝 / fail-closed** — RBAC 不可用一律拒绝。

`/trpg-admin` 与群内 `/trpg start|reset|stop|prompt` → `_check_admin()`；
`/trpgrbac` → `_check_global_admin()`（仅 RBAC，群主/群管理不可越权）。

## 7. 配置项

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ApiKey` | str | `sk-xxx…` | OpenAI API Key |
| `Model` | str | `openai/gpt-4o-mini` | 模型 |
| `BaseUrl` | str | `https://api.openai.com/v1` | API 地址 |
| `IsConfigured` | bool | false | 是否已配置 |
| `PromptConfigFile` | str | `prompts.yaml` | 提示词模板 YAML（相对工作区或绝对路径） |
| `ActivePrompt` | str | `default` | 当前激活的提示词模板名 |
| `StripReasoning` | bool | true | 剥离推理模型思维链（`<think>`/`<reasoning>`/DeepSeek 全角分隔符） |
| `MustAtBot` | bool | true | 群内需 @机器人 触发 AI 主持 |
| `InsertUserdataAsPrefix` | bool | true | 群聊行动附带 昵称(QQ) 前缀 |
| `MaxHistoryMessages` | int | 30 | 每团保留的历史消息条数 |
| `EnableCriticalDice` | bool | true | 大成功/大失败判定 |
| `EnableGroupOwnerAutoAuth` | bool | true | 群主/群管理自动放行 |
| `EnableInGameDiceKeyword` | bool | true | 启用局内掷骰关键词 |
| `InGameDiceKeyword` | str | `.r` | 局内关键词（`h` 后缀为暗骰） |
| `MaxDiceCount` / `MaxDiceSides` | int | 100 / 10000 | 防刷上限 |

默认值 `on_load()` 经 `init_defaults()` 注册（仅内存）；可被全局 `config.yaml` 的
`plugin.plugin_configs.NcatBotTRPG` 覆盖。

## 8. 数据持久化

- `data.json`（DataMixin，`<workspace>/data.json`）：`sessions = {group: {...}, user: {...}}`。
- RBAC 持久化于 `data/rbac.json`（框架管理）。

## 9. 测试

`tests/` 使用 `PluginTestHarness` + MockAdapter 离线驱动；事件注入走真实分发链路；
AI 链路用 `fake_llm.FakeLLMClient` 替换 `plugin.llm._client`，禁止真实网络。
每用例 `monkeypatch.chdir(tmp_path)` + `NCATBOT_CONFIG_PATH` 指向测试 config，隔离真实数据。

- `test_dice_engine.py` — 掷骰纯函数（无框架）
- `test_dice.py` — 掷骰命令（含暗骰私聊）
- `test_permission.py` — 三层 RBAC（API 级断言）
- `test_session.py` — 团会话管理
- `test_room.py` — 房间系统（默认房间惰性创建 / join-leave 保留 / 开团自动加入）
- `test_ai.py` — AI 主持链路（含思维链剥离与仅思维链兜底）
- `test_prompt.py` — 提示词模板机制（导入生效 / 缺失回退 / ActivePrompt 切换 / 与专属设定叠加）
- `test_reasoning_unit.py` — `strip_reasoning` 纯函数（标签 / 未闭合 / DeepSeek 全角分隔符）
- `test_roll_context.py` — 掷骰结果缓存与 system prompt 注入、重复投掷拦截、多玩家累积、暗骰标记、reset 清空

运行（插件目录内）：`python -m pytest tests -v -o "addopts="`。

## 10. 参考与许可证

本插件整体以 **AGPL-3.0** 发布。设计参考（仅借鉴思路、v1 未复制任何代码）：
diceframe（AGPL-3.0）、SillyTavern（AGPL-3.0）、TRPG-AI-DM（MIT）。详见 `NOTICE`。

后续若复制/改写第三方代码，须按 `NOTICE` 第三节规范：文件头标注来源、登记 NOTICE、
保留原许可证、确保许可证兼容（AGPL-3.0 / MIT 可，未兼容许可证不可）。

## 11. 延后项与预留

角色卡/属性、World Info 世界书、多 NPC 群聊扮演、剧本/模组导入、完整存档/读档、
CoC/DnD 完整规则、AI 工具调用改状态、对接 diceframe 后端、`api.ai` 适配器切换。
掷骰引擎、LLM 客户端、会话结构均为独立可替换单元，为上述扩展预留了接口。
