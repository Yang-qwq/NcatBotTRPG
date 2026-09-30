# NcatBotTRPG

NcatBot 5 的 TRPG 跑团插件：**通用掷骰** + **极简 AI 主持人** + **三层权限管理**。

- 🎲 通用掷骰：`NdM±K`、优势/劣势、d100 奖励/惩罚骰、成功率/DC 判定、公开/暗骰（不支持重复投掷）
- 🎭 AI 主持：兼容 OpenAI 接口，玩家行动由 AI 以主持人身份叙事推进剧情
- 🏠 房间系统：群与房间绑定，每群至多一个房间，用户自由加入退出，/trpg start后向LLM传递参与人员信息
- 🛡 权限：全局管理员（RBAC）+ 本群群主/群管理自动放行 + 默认拒绝
- 📝 提示词：内置多种主持人风格模板，支持全局切换和自定义配置

> 版本 v0.1.0（第一版）。角色卡、世界书、多 NPC、剧本导入、完整存档等将在后续版本提供，
> 设计细节见 [DESIGN.md](./DESIGN.md)。

---

## 一、快速开始

### 1. 放置插件

本插件是工作区 `plugins/NcatBotTRPG` 下的 git 子模块，克隆工作区后即存在。若手动部署，
把整个 `NcatBotTRPG/` 目录放入工作区的 `plugins/` 下即可。

### 2. 准备提示词配置

插件需要提示词配置文件才能使用自定义主持人风格。请复制插件目录中的 `prompts_template.yaml` 
到插件工作区：

```powershell
# 复制提示词模板到插件工作区
Copy-Item "plugins\NcatBotTRPG\prompts_template.yaml" "data\NcatBotTRPG\prompts.yaml"
```

插件工作区通常位于 `data/NcatBotTRPG/` 目录下。如果该目录不存在，机器人会自动创建。

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
      # === 基础配置 ===
      ApiKey: "sk-xxxxxxxxxxxxxxxx"        # 你的 OpenAI API Key
      Model: "openai/gpt-4o-mini"          # 模型名（兼容 OpenAI 的任意服务）
      BaseUrl: "https://api.openai.com/v1"  # API 地址
      IsConfigured: true                    # 必须置 true，否则无法开团
      
      # === 行为配置 ===
      MustAtBot: true                      # 群聊中是否必须 @机器人 才触发 AI 主持
      InsertUserdataAsPrefix: true        # 群聊行动是否附带 昵称(QQ) 前缀
      MaxHistoryMessages: 30               # 每团保留的最近消息条数
      EnableCriticalDice: true             # 是否判定大成功/大失败
      EnableGroupOwnerAutoAuth: true       # 群主/群管理自动放行本群管理命令
      EnableInGameDiceKeyword: true       # 是否启用局内掷骰关键词
      InGameDiceKeyword: ".r"              # 局内掷骰关键词（h 后缀为暗骰）
      MaxDiceCount: 100                    # 单次最大骰数（防刷）
      MaxDiceSides: 10000                  # 骰子最大面数（防刷）
      StripReasoning: true                 # 是否剥离推理模型的思维链（防止暴露）
      
      # === 提示词配置 ===
      PromptConfigFile: "prompts.yaml"     # 提示词配置文件路径（相对于插件工作区）
      ActivePrompt: "default"              # 当前激活的提示词名称
```

> 只使用掷骰功能时 **无需配置** API Key，但 `IsConfigured` 仍需设置为 `true`。

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
| `PromptConfigFile` | str | `prompts.yaml` | 提示词配置文件路径（相对于插件工作区） |
| `ActivePrompt` | str | `default` | 当前激活的提示词名称 |
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

## 三、主持人提示词配置

### 3.1 新的提示词配置系统

插件支持灵活的提示词配置系统，包含多种预设风格和自定义选项：

#### 预设提示词模板

插件内置多种主持人风格，通过 `prompts.yaml` 配置管理：

- **default**：默认主持人（平衡叙事与规则）
- **literary**：文学风格（注重描写细节和情感）
- **fast_paced**：快节奏（简洁明快，注重剧情推进）
- **immersive**：沉浸式（完全代入角色）
- **debug**：调试模式（提供详细的游戏信息和规则解释）

### 3.2 配置方法

#### 方法一：使用预设提示词（推荐）

1. **查看可用提示词**：
   ```
   /trpg prompt list
   ```

2. **切换提示词**：
   ```
   /trpg prompt switch literary
   ```

3. **查看当前提示词**：
   ```
   /trpg prompt show
   ```

#### 方法二：自定义提示词配置

编辑插件工作区中的 `prompts.yaml` 文件（通常位于 `data/NcatBotTRPG/prompts.yaml`），
添加或修改提示词：

```yaml
custom_style:
  name: "自定义风格"
  description: "自定义的主持人风格"
  content: |
    你是自定义的 TRPG 主持人。
    - 根据你的需求编写提示词内容
    - 支持多行文本
    - 使用 Markdown 格式
```

#### 提示词文件位置说明

- **插件工作区**：通常位于 `data/NcatBotTRPG/` 目录下
- **配置文件路径**：相对路径相对于插件工作区解析
- **自动创建**：如果插件工作区不存在，机器人启动时会自动创建

### 3.3 提示词优先级

最终发给模型的 system 提示词按以下顺序组装：

```
[ 配置的提示词 ] + [ 本团专属设定 ]
    ↑
    ├─ PromptConfigFile 加载的提示词（按 ActivePrompt 选用）
    └─ 否则内置默认主持词
```

### 3.4 为单个团单独设定

除了全局提示词配置，还可以为每个团单独设定：

```
/trpg prompt set "这是一个恐怖风格的团"
```

这个设定会追加在全局提示词之后，仅影响当前团。

> 提示词配置文件路径解析：绝对路径直接使用；相对路径相对**插件工作区**（`data/NcatBotTRPG/`）解析。
> 文件缺失或为空时自动回退内置默认提示词，并在日志中告警。
> 
> **注意**：提示词配置文件应放在插件工作区中，而不是插件目录中。插件目录中的 `prompts_template.yaml` 是模板文件，需要复制到工作区使用。

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

### 4.2 房间系统（群聊专用）

房间与会话绑定：**每个群默认有且只有一个房间**（初始 0 人、无需创建），用户可在开团前自由加入与退出。

#### 房间管理命令

| 命令 | 权限 | 说明 |
|---|---|---|
| `/trpg room join [密码]` | 所有人 | 加入当前群房间 |
| `/trpg room leave` | 所有人 | 离开当前群房间 |
| `/trpg room status` | 所有人 | 查看房间状态 |
| `/trpg room participants` | 所有人 | 查看参与者列表 |

> `/trpg start` 会自动把发起人加入房间；无需先执行任何创建命令。

#### 房间状态

- **准备中（preparing）**：可以加入玩家，可以开始跑团
- **进行中（running）**：跑团进行中，不能加入新玩家
- **已结束（finished）**：跑团已结束

### 4.3 玩家状态管理（群聊专用）

玩家状态管理允许玩家在跑团过程中管理自己的参与状态，包括中途退出、请求托管和重新加入。

#### 玩家状态命令

| 命令 | 权限 | 说明 |
|---|---|---|
| `/trpg away [原因]` | 所有人 | 暂时离开游戏 |
| `/trpg offline [原因]` | 所有人 | 长时间离线 |
| `/trpg back` | 所有人 | 重新加入游戏 |
| `/trpg ai-control [原因]` | 所有人 | 请求AI托管角色 |
| `/trpg status` | 所有人 | 查看当前团状态和玩家状态 |

#### 玩家状态类型

- **活跃（active）**：正常参与游戏
- **离开（away）**：暂时离开，可能很快回来
- **离线（offline）**：长时间离开
- **请求AI托管（requested_ai_control）**：请求AI控制角色

#### 状态管理流程

1. **暂时离开**：
   ```
   /trpg away 需要处理一些事情
   ```

2. **重新加入**：
   ```
   /trpg back
   ```

3. **长时间离线**：
   ```
   /trpg offline 今天有事，明天再回来
   ```

4. **请求AI托管**：
   ```
   /trpg ai-control 需要暂时离开，请帮我控制角色
   ```

#### LLM状态信息

在每次行动时，LLM会接收以下信息：
- 参与者列表
- 每个玩家的当前状态
- AI托管请求

这使主持人能够根据玩家状态调整剧情和角色行为。

#### 房间使用流程

1. **玩家加入房间**（可选，`start` 会自动加入发起人）：
   ```
   /trpg room join
   ```

2. **查看房间状态**：
   ```
   /trpg room status
   ```

3. **开始跑团**：
   ```
   /trpg start 这是一个奇幻风格的跑团
   ```

### 4.3 开团与主持

| 命令 | 权限 | 说明 |
|---|---|---|
| `/trpg start [设定]` | 群内：管理员 | 开启本会话的 AI 跑团（自动加入房间） |
| `/trpg act <行动>` | 所有人 | 向 AI 主持提交一次行动 |
| `/trpg away [原因]` | 所有人 | 暂时离开游戏 |
| `/trpg offline [原因]` | 所有人 | 长时间离线 |
| `/trpg back` | 所有人 | 重新加入游戏 |
| `/trpg ai-control [原因]` | 所有人 | 请求AI托管角色 |
| `/trpg status` | 所有人 | 查看团状态（包含玩家状态） |
| `/trpg prompt [set <设定>\|show\|reset]` | 群内：管理员；私聊：本人 | 查看/设置本团专属设定 |
| `/trpg prompt list` | 全局管理员 | 查看可用的提示词模板 |
| `/trpg prompt switch <提示词名>` | 全局管理员 | 切换当前使用的提示词 |
| `/trpg prompt show [提示词名]` | 全局管理员 | 显示提示词内容 |
| `/trpg reset` | 群内：管理员；私聊：本人 | 清空剧情历史（保留设定） |
| `/trpg stop` | 群内：管理员；私聊：本人 | 结束本会话的团（保留记录） |
| `/trpg help` | 所有人 | 帮助 |

**群聊开团后如何行动**：发送 `@机器人 <你的行动>`（`MustAtBot=true` 时），
或使用 `/trpg act <行动>`。

**房间系统优势**：使用房间系统后，在 `/trpg start` 时会向LLM传递所有参与者信息，
让主持人知道当前有哪些玩家在游戏中，提供更好的游戏体验。

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
| `/trpg-admin room-admin <set-description\|set-password\|set-max\|delete>` | 群内：管理员 | 房间管理 |
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
- **主持人说话不像预期**：使用 `/trpg prompt list` 查看可用提示词，`/trpg prompt switch` 切换风格；
  或编辑 `prompts.yaml`，或用 `/trpg prompt set <设定>` 为单个团追加设定。
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
