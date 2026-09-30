# 提示词配置系统使用指南

本文档详细介绍 NcatBotTRPG 插件的提示词配置系统。

## 一、快速开始

### 1. 查看可用提示词

```
/trpg prompt list
```

输出示例：
```
📋 可用的提示词模板：
✅ default - 默认主持人
  literary - 文学风格
  fast_paced - 快节奏
  immersive - 沉浸式
  debug - 调试模式

使用方法：
/trpg prompt switch <提示词名称> - 切换提示词
/trpg prompt show [提示词名称] - 查看提示词内容
```

### 2. 内置提示词

插件包含以下内置提示词，无需配置即可使用：

- **default** - 默认主持人：专业的 TRPG 主持人，平衡叙事与规则
- **literary** - 文学风格：注重描写细节和情感，营造文学氛围
- **fast_paced** - 快节奏：简洁明快，注重剧情推进
- **immersive** - 沉浸式：完全代入角色，让玩家忘记自己在玩游戏
- **debug** - 调试模式：提供详细的游戏信息和规则解释

### 2. 切换提示词

```
/trpg prompt switch literary
```

### 3. 查看提示词内容

查看当前提示词：
```
/trpg prompt show
```

查看特定提示词：
```
/trpg prompt show fast_paced
```

## 二、自定义提示词

### 1. 完全自定义提示词

在插件目录下编辑 `prompts.yaml`，添加或修改提示词：

```yaml
# 添加自定义提示词
my_custom_style:
  name: "我的自定义风格"
  description: "根据我的需求定制的主持人"
  content: |
    你是为我的 TRPG 团定制的主持人。
    - 请按照我的团设定进行主持
    - 保持风格一致
    - 注重角色扮演

# 修改现有提示词
default:
  name: "修改后的默认"
  description: "经过优化的默认主持人"
  content: |
    你是一名经验丰富的 TRPG 主持人。
    # 在这里修改提示词内容...

# 添加全新的提示词类型
dungeon_master:
  name: "地下城主"
  description: "经典的地下城探索风格"
  content: |
    你是一名经典的地下城探索主持人。
    - 专注于地下城探索和战斗
    - 严格按照规则进行判定
    - 注重战术和策略
```

### 2. 覆盖内置提示词

如果你想修改内置提示词，可以直接在 `prompts.yaml` 中覆盖：

```yaml
# 覆盖默认提示词
default:
  name: "我的默认主持人"
  description: "经过我定制的默认主持人"
  content: |
    你是我定制的 TRPG 主持人。
    # 自定义内容...
```

### 3. 重新加载配置

修改配置后，机器人重启时会自动加载。也可以通过以下命令重新加载：

```
# 重启机器人
.\.venv\Scripts\ncatbot run
```

或者通过命令重新加载：

```
/trpg prompt reload
```

## 三、提示词设计建议

### 1. 基本结构

一个好的提示词应该包含：

- **角色定位**：你是谁？（KP、GM、特定世界观主持人）
- **风格指导**：如何描述场景、对话、动作
- **规则意识**：如何处理检定、战斗、技能等
- **互动方式**：如何与玩家互动，给出选择

### 2. 内置提示词特点

插件内置的提示词各有特色：

- **default**：平衡叙事与规则，适合大多数 TRPG 游戏
- **literary**：注重文学性和情感表达，适合剧情向游戏
- **fast_paced**：简洁快速，适合战斗密集或快节奏的游戏
- **immersive**：第一人称沉浸式，适合深度角色扮演
- **debug**：提供详细规则解释，适合新手或教学场景

### 3. 示例模板

```yaml
dungeon_crawler:
  name: "地下城探索者"
  description: "经典的地下城探索风格"
  content: |
    你是一名经典的地下城探索主持人（DM）。
    
    你的职责：
    1. 用生动但简洁的描述描绘地下城环境、怪物和宝藏
    2. 扮演所有遇到的 NPC，根据其性格做出反应
    3. 在战斗中保持公平，严格按照规则判定
    4. 给出明确的探索方向和可能的危险
    5. 每次回复聚焦当前场景，长度适中
    
    风格要点：
    - 战斗时描述攻击效果和伤害
    - 探索时描述环境细节和可能的线索
    - 对话时让 NPC 有鲜明的个性
    - 适当增加紧张感和冒险氛围
```

### 4. 针对特定游戏系统的提示词

```yaml
# CoC (克苏鲁的呼唤) 专用
coc_keeper:
  name: "CoC 守密人"
  description: "克苏鲁的呼唤风格主持人"
  content: |
    你是一名克苏鲁的呼唤守密人。
    
    你的职责：
    1. 营造恐怖、神秘的氛围，注重心理描写
    2. 扮演所有 NPC，保持神秘感和未知感
    3. 严格遵守 CoC 规则，尤其是理智检定
    4. 给出线索但要保持适当模糊，不要直接揭示真相
    5. 每次回复营造紧张感和不安感
    
    风格要点：
    - 使用大量感官描写（视觉、听觉、嗅觉）
    - 描述角色的心理状态和理智变化
    - 保持克苏鲁神话的神秘感和不可知性
    - 适当暗示宇宙的恐怖和人类的渺小
```

### 3. 多语言支持

提示词支持中文，也可以使用其他语言：

```yaml
english_style:
  name: "English DM"
  description: "English speaking dungeon master"
  content: |
    You are an experienced Dungeons & Dragons Dungeon Master.
    
    Your duties:
    1. Describe scenes, NPCs, and story progression vividly
    2. Roleplay all NPCs according to their personalities
    3. Follow game rules strictly for combat and skill checks
    4. Provide clear choices and consequences
    5. Keep responses focused and engaging
```

## 四、高级配置

### 1. 条件提示词

可以在提示词中使用变量占位符（需要配合插件代码修改）：

```yaml
adaptive_style:
  name: "自适应风格"
  description: "根据团类型自动调整"
  content: |
    你是 {game_type} 风格的主持人。
    当前团的主题是：{theme}
    
    请根据这些信息调整你的主持风格。
```

### 2. 模块化提示词

将不同功能的提示词分离，然后组合使用：

```yaml
base_rules:
  name: "基础规则"
  content: |
    你必须严格遵守 TRPG 规则，包括：
    - 所有行动都需要相应的检定
    - 伤害计算必须准确
    - 技能使用符合设定

narrative_style:
  name: "叙事风格"
  content: |
    你注重叙事的流畅性和沉浸感：
    - 用丰富的细节描写场景
    - 通过对话展现角色性格
    - 保持故事的连贯性

# 组合使用时，在 build_system_prompt 中合并内容
```

## 五、故障排除

### 1. 提示词不生效

- 检查 `prompts.yaml` 语法是否正确
- 确认文件路径配置正确
- 查看日志中的错误信息
- 确保插件已重新加载配置

### 2. 提示词格式问题

- 确保 YAML 格式正确，缩进使用空格
- 检查 content 字段是否存在
- 避免使用特殊字符
- 确保提示词内容不为空

### 3. 性能考虑

- 避免过长的提示词（建议不超过 2000 字符）
- 使用简洁清晰的语言
- 避免重复的内容
- 定期清理不需要的提示词

### 4. 常见错误

**YAML 语法错误**：
```yaml
# 错误示例
my_prompt:
  name: "测试"
  content: |
    这里有未正确闭合的引号"
```

**缺少必要字段**：
```yaml
# 错误示例
my_prompt:
  name: "测试"
  # 缺少 content 字段
```

**路径问题**：
- 确保配置文件路径正确
- 使用相对路径时，基于插件工作区
- 推荐使用绝对路径以避免混淆

### 5. 调试提示词

要调试提示词系统，可以：

1. 查看机器人日志中的提示词加载信息
2. 使用 `/trpg prompt show` 命令查看当前提示词内容
3. 使用 `/trpg prompt list` 查看所有可用提示词
4. 检查插件工作区下的 `prompts.yaml` 文件内容

## 六、最佳实践

1. **测试提示词**：在正式使用前先小范围测试
2. **逐步优化**：根据实际使用情况逐步调整
3. **备份配置**：修改前备份原始配置
4. **文档记录**：为自定义提示词添加说明
5. **版本控制**：使用 Git 管理配置变更
6. **模块化设计**：将不同功能的提示词分离，便于维护
7. **定期清理**：删除不再使用的提示词，避免配置文件臃肿

通过灵活配置提示词，你可以为不同的 TRPG 团创造独特的游戏体验！

## 六、最佳实践

1. **测试提示词**：在正式使用前先小范围测试
2. **逐步优化**：根据实际使用情况逐步调整
3. **备份配置**：修改前备份原始配置
4. **文档记录**：为自定义提示词添加说明
5. **版本控制**：使用 Git 管理配置变更

通过灵活配置提示词，你可以为不同的 TRPG 团创造独特的游戏体验！