# NcatBotTRPG

NcatBot 5 的 TRPG 跑团插件：**通用掷骰** + **极简 AI 主持人** + **三层权限管理**。

- 🎲 通用掷骰：`NdM±K`、优势/劣势、d100 奖励/惩罚骰、成功率/DC 判定、公开/暗骰（不支持重复投掷）
- 🎭 AI 主持：兼容 OpenAI 接口，玩家行动由 AI 以主持人身份叙事推进剧情
- 🛡 权限：全局管理员（RBAC）+ 本群群主/群管理自动放行 + 默认拒绝
- 📝 提示词：内置默认主持词，可用配置文件导入自定义主持词，也可为单个团单独设定

> 版本 v0.1.0（第一版）。角色卡、世界书、多 NPC、剧本导入、完整存档等将在后续版本提供，
> 设计细节见 [DESIGN.md](./DESIGN.md)。

---

## 一、快速开始

### 1. 放置插件

本插件是工作区 `plugins/NcatBotTRPG` 下的 git 子模块，克隆工作区后即存在。若手动部署，
把整个 `NcatBotTRPG/` 目录放入工作区的 `plugins/` 下即可。

### 2. 安装依赖

框架会读取 `manifest.toml` 自动安装依赖，也可手动：

```powershell
pip install -r plugins/NcatBotTRPG/requirements.txt
```

### 3. 配置机器人

编辑工作区根目录的 `config.yaml`（**含凭据，勿提交版本库**），在
`plugin.plugin_configs.NcatBotTRPG` 下填写：

```yaml
plugin:
  plugin_configs:
    NcatBotTRPG:
      ApiKey: "sk-xxxxxxxxxxxxxxxx"        # 你的 API Key
      Model: "openai/gpt-4o-mini"          # 模型名（兼容 OpenAI 的任意服务）
      BaseUrl: "https://api.openai.com/v1"  # API 地址
      IsConfigured: true                    # 必须置 true，否则无法开团
      # 可选：从文件导入主持人提示词（相对插件工作区或绝对路径）
      # PromptFile: "prompt.md"
```

> 只使用掷骰功能时 **无需配置** API Key。

### 4. 启动并验证

```powershell
.\.venv\Scripts\ncatbot run
```

在群里发送 `/trpg help`，收到帮助文本即加载成功。

---

## 二、配置项

默认值由插件加载时注册；全局 `config.yaml` 中的同名键优先级更高。

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ApiKey` | str | `sk-xxx…` | OpenAI 兼容 API Key |
| `Model` | str | `openai/gpt-4o-mini` | 使用的模型 |
| `BaseUrl` | str | `https://api.openai.com/v1` | API 地址 |
| `IsConfigured` | bool | `false` | **置 true 后**才允许 `/trpg start` |
| `PromptFile` | str | `""` | 主持人提示词文件（空=内置默认）。相对路径基于插件工作区解析 |
| `StripReasoning` | bool | `true` | 剥离推理模型返回的思维链，避免暴露给用户 |
| `MustAtBot` | bool | `true` | 群聊是否必须 @机器人 才触发 AI 主持 |
| `InsertUserdataAsPrefix` | bool | `true` | 群聊行动是否附带 `昵称(QQ)：` 前缀 |
| `MaxHistoryMessages` | int | `30` | 每团保留的最近消息条数 |
| `EnableCriticalDice` | bool | `true` | 是否判定大成功/大失败 |
| `EnableGroupOwnerAutoAuth` | bool | `true` | 群主/群管理自动放行本群管理命令 |
| `EnableInGameDiceKeyword` | bool | `true` | 是否启用局内掷骰关键词 |
| `InGameDiceKeyword` | str | `.r` | 局内掷骰关键词（`h` 后缀为暗骰） |
| `MaxDiceCount` | int | `100` | 单次最大骰数（防刷） |
| `MaxDiceSides` | int | `10000` | 骰子最大面数（防刷） |

---

## 三、主持人提示词（三层）

最终发给模型的 system 提示词按以下顺序组装：

```
[ 默认提示词 ] + [ 本团专属设定 ]
   ↑                    ↑
   ├─ PromptFile 导入（若配置且文件有效）
   └─ 否则内置默认主持词
```

1. **内置默认主持词**：插件自带，开箱即用（见 `llm.py:DEFAULT_KEEPER_PROMPT`）。
2. **PromptFile 导入**：配置 `PromptFile` 指向一个 UTF-8 文本/Markdown 文件，
   插件加载时读入，作为默认提示词。**这是推荐的全局自定义方式。**
3. **本团专属设定**：通过 `/trpg start <设定>` 或 `/trpg prompt set <设定>` 单独为某个
   群/会话设置，追加在默认提示词之后。

### 如何创建提示词文件

在插件工作区（`data/NcatBotTRPG/`）放置 `prompt.md`，然后配置 `PromptFile: "prompt.md"`：

```markdown
# 我的主持人设定

你是《克苏鲁的呼唤》风格的调查团主持人（KP）。
- 用第二人称描述场景，营造压抑、细思极恐的氛围。
- 每次回复 2~4 句，结尾给出 2~3 个可调查的方向。
- 绝不编造骰子结果，需要检定时提示玩家「请掷 1d100」。
```

路径解析：绝对路径直接使用；相对路径相对**插件工作区**（`data/NcatBotTRPG/`）解析。
文件缺失或为空时自动回退内置默认提示词，并在日志中告警。

> 仓库内 `tests/fixtures/example_prompt.md` 是**仅供自动化测试**的示例，请勿直接用于生产。

---

## 四、命令使用指南

### 4.1 掷骰（所有人，群/私聊均可）

| 命令 | 说明 |
|---|---|
| `/trpg roll <骰式> [判定]` | 公开掷骰 / 检定 |
| `/trpg rh <骰式> [判定]` | 暗骰，结果私聊发起者 |
| `.r <骰式>` / `.rh <骰式>` | 局内关键词（**仅团激活后可用**） |

示例：

```
/trpg roll 1d20+3           → 🎲 d20+3 = 17
/trpg roll 2d6              → 🎲 2d6 = [4, 2] = 6
/trpg roll 1d100 <= 50      → 🎲 d100 = 37 / 判定 <= 50：成功
/trpg roll d20+5 >= 15      → 🎲 d20+5 = 18 / 判定 >= 15：成功
/trpg roll d20 adv          → 🎲 d20 优势 [18, 7] 取18 = 18
/trpg roll d100 bonus1      → d100 奖励骰（penalty 为惩罚骰）
/trpg rh 1d20               → 群内仅提示「🤫 暗骰结果已私聊发送」
```

掷骰语法速查：

| 语法 | 含义 |
|---|---|
| `NdM` / `dM` | 掷 N 个 M 面骰（N 缺省 1） |
| `NdM±K` | 总和加减修正 |
| `adv` / `dis` | 单个 d20 的优势 / 劣势（同时出现则抵消） |
| `bonusN` / `penaltyN` | 单个 d100 的奖励 / 惩罚骰 |
| `<=` `>=` `<` `>` `==` | 判定运算符 + 目标值 |

> 不支持重复投掷（`N#expr`）：一次行动只能掷一次，防止刷骰。

### 4.2 开团与主持

| 命令 | 权限 | 说明 |
|---|---|---|
| `/trpg start [设定]` | 群内：管理员；私聊：本人 | 开启本会话的 AI 跑团 |
| `/trpg act <行动>` | 所有人 | 向 AI 主持提交一次行动 |
| `/trpg status` | 所有人 | 查看团状态（是否进行中、设定、消息数） |
| `/trpg prompt [set <设定>\|show\|reset]` | 群内：管理员；私聊：本人 | 查看/设置本团专属设定 |
| `/trpg reset` | 群内：管理员；私聊：本人 | 清空剧情历史（保留设定） |
| `/trpg stop` | 群内：管理员；私聊：本人 | 结束本会话的团（保留记录） |
| `/trpg help` | 所有人 | 帮助 |

**群聊开团后如何行动**：发送 `@机器人 <你的行动>`（`MustAtBot=true` 时），
或使用 `/trpg act <行动>`。

**掷骰与主持的衔接**：团进行中时，掷骰结果会被缓存，并在你的**下一次行动**时通过
system prompt 交给主持人（LLM），使其能据此推进剧情与判定。推荐流程：先掷骰，再提交行动。

> **每回合一次**：在结果被行动结算前，同一位玩家**不能重复掷骰**（防止刷骰）。提交行动后
> 即可再次掷骰。多位玩家可各自持有一条待结算掷骰，一起交给主持人。

**完整示例**

```
用户: /trpg start 迷雾笼罩的港口小镇
Bot:  ✅ 已开启跑团。用 @我 或 /trpg act <行动> 开始行动；掷骰用 /trpg roll 或 局内关键词 .r / .rh

用户: @Bot 我推开酒馆的木门，观察里面的人
Bot:  门轴发出刺耳的吱呀声，昏黄灯光下，三个码头工人模样的男人停下交谈望向你……
      （屋内弥漫着鱼腥味。想看清他们的神情，或许需要一次侦查。）

用户: .r 1d100 <= 50
Bot:  🎲 1d100 = 37
      判定 <= 50：成功
      （主持人将在你的下一次行动中参考此掷骰结果）

用户: @Bot 我借着灯光仔细打量他们的脸
Bot:  侦查成功。你注意到最里侧的男人袖口沾着暗红色的污渍，手指无意识地攥紧……
```

### 4.3 跨会话管理（管理员）

| 命令 | 说明 |
|---|---|
| `/trpg-admin stop [group:<id>\|user:<id>]` | 结束指定会话的团 |
| `/trpg-admin reset [group:<id>\|user:<id>]` | 清空指定会话的剧情历史 |
| `/trpg-admin prompt <set <设定>\|show\|reset> [目标]` | 管理指定会话的专属设定 |
| `/trpg-admin help` | 帮助 |

不指定目标时作用于当前会话。权限：全局管理员或本群群主/群管理。

### 4.4 全局管理员管理（仅 root / 全局管理员）

| 命令 | 说明 |
|---|---|
| `/trpgrbac grant <qq>` | 授予全局管理员（支持 @ 成员或纯数字） |
| `/trpgrbac revoke <qq>` | 撤销全局管理员 |
| `/trpgrbac list` | 查看全局管理员列表 |
| `/trpgrbac help` | 帮助 |

> 群主/群管理**不能**使用 `/trpgrbac`（防止越权提权）。

---

## 五、权限说明

1. **全局管理员**：`config.yaml` 的 `root`（机器人 owner）自动获得；其余由 `/trpgrbac grant` 授予。
2. **本群群主/群管理**：自动放行本群管理命令（可用 `EnableGroupOwnerAutoAuth: false` 关闭）。
3. **默认拒绝**：RBAC 服务不可用时一律拒绝（fail-closed）。

管理命令：`/trpg start|prompt|reset|stop`（群内）、`/trpg-admin`。`/trpgrbac` 仅全局管理员。

---

## 六、常见问题

- **开团提示「AI 主持尚未配置」**：`config.yaml` 中 `IsConfigured` 未置 `true`，或
  `ApiKey`/`Model`/`BaseUrl` 未填。
- **群里 @机器人没反应**：该群尚未 `/trpg start` 开团；或 `MustAtBot=true` 但未真正 @ 到机器人。
- **`.r` 没反应**：局内关键词仅在**团激活后**生效，且关键词需在消息开头；可用
  `/trpg roll` 代替。
- **主持人说话不像预期**：通过 `PromptFile` 导入全局主持词，或用
  `/trpg prompt set <设定>` 为单个团追加设定。
- **报错「连续…」/ 模型报错**：检查 `BaseUrl`/`ApiKey`/`Model` 是否正确、网络是否可达，
  查看 `logs/` 日志中的 `ncatbot_trpg` 记录。
- **和 OpenAI 聊天插件同时 @机器人 会重复回复吗？**：不会。跑团进行中的会话由 NcatBotTRPG
  接管，OpenAIChatPlugin 会自动让出该消息；跑团未开团/已结束时由 OpenAI 插件正常回复。
- **能用推理模型（DeepSeek-R1 等）吗？**：可以。插件默认开启 `StripReasoning`，会自动剥离
  `<think>…</think>`、`<reasoning>…</reasoning>` 等思维链以及 DeepSeek 全角分隔符，只发送最终回答；
  开标签被上游剥离、只剩结尾 `</think>` 的孤儿闭标签也会连同其前思维链一并移除。
  提取到的思维链会以 `AI思维链: …` 记录到日志（控制台）便于调试，但绝不会发送给用户。
- **掷骰后主持人好像没看到结果？**：团进行中时先掷骰、再提交行动（`@机器人` 或 `/trpg act`）；
  掷骰结果会随该行动的 system prompt 交给主持人。只掷骰不行动时，结果会保留到你下一次行动。
- **掷骰提示「未结算」？**：每位玩家在结果被行动结算前只能掷一次骰，防止反复重掷。提交行动后即可继续掷骰。

---

## 七、开发与测试

```powershell
# 在插件目录内运行
python -m pytest tests -v -o "addopts="
```

测试用 `ncatbot.testing.PluginTestHarness` + MockAdapter 离线驱动，隔离真实
`config.yaml`/`data/`；AI 链路用假 LLM 客户端，不发起真实网络请求。
更多约定见 [AGENTS.md](./AGENTS.md)。

---

## 许可证

AGPL-3.0，见 [LICENSE](./LICENSE)。设计参考与第三方声明见 [NOTICE](./NOTICE)。
