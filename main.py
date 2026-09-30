# -*- coding: utf-8 -*-
"""NcatBotTRPG 插件入口

通用掷骰引擎 + 极简 AI 跑团主持（openai SDK）+ 三层 RBAC 权限体系。
"""
from __future__ import annotations

import re
import traceback
import yaml
from pathlib import Path

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At
from ncatbot.utils import get_config_manager
from ncatbot.utils.logger import get_log

from . import llm
from .command_handler import ADMIN_PERMISSION, NcatBotTRPGCommandMixin
from .player_status_commands import PlayerStatusCommandMixin
from .prompt_commands import PromptCommandMixin
from .room_commands import RoomCommandMixin
from .session import SessionStore, player_label

_log = get_log('ncatbot_trpg')

# 日志中省略的文本长度
OMITTED_TEXT_LENGTH = 100

# 匹配并移除 CQ:at 组件，用于提取纯文本行动内容
_AT_CQ_PATTERN = re.compile(r'\[CQ:at,[^\]]*\]')


class NcatBotTRPGPlugin(RoomCommandMixin, PlayerStatusCommandMixin,
                        PromptCommandMixin, NcatBotTRPGCommandMixin, NcatBotPlugin):
    """NcatBotTRPG 插件（NcatBot 5）

    在 QQ 群/私聊中提供通用掷骰与 AI 跑团主持，具备三层权限与管理体系。
    """

    name = 'NcatBotTRPG'
    version = '0.1.0'
    author = 'Yang-qwq'
    description = 'TRPG 跑团插件：通用掷骰 + 极简 AI 主持 + 三层 RBAC 权限'

    async def on_load(self):
        """插件加载时的初始化。"""
        # ---- 注册配置默认值（仅内存补充缺失键） ----
        self.init_defaults({
            'ApiKey': 'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
            'Model': 'openai/gpt-4o-mini',
            'BaseUrl': 'https://api.openai.com/v1',
            'IsConfigured': False,          # 是否已完成配置（需用户手动设置为 true）
            'MustAtBot': True,              # 群聊中是否必须 @机器人 才触发 AI 主持
            'InsertUserdataAsPrefix': True,  # 群聊行动是否附带 昵称(QQ) 前缀
            'MaxHistoryMessages': 30,       # 每团保留的历史消息条数
            'EnableCriticalDice': True,     # 是否判定大成功/大失败
            'EnableGroupOwnerAutoAuth': True,  # 群主/群管理自动放行本群管理命令
            'EnableInGameDiceKeyword': True,   # 是否启用局内掷骰关键词
            'InGameDiceKeyword': '.r',         # 局内掷骰关键词（h 后缀为暗骰）
            'MaxDiceCount': 100,            # 单次最大骰数
            'MaxDiceSides': 10000,          # 骰子最大面数
            'StripReasoning': True,         # 是否剥离推理模型的思维链（防止暴露）
            'PromptConfigFile': 'prompts.yaml',  # 提示词配置文件路径（相对工作区或绝对路径）
            'ActivePrompt': 'default',      # 当前激活的提示词名称
        })

        # ---- RBAC：注册管理员权限点并自动授予 root（幂等） ----
        self.add_permission(ADMIN_PERMISSION)
        root = get_config_manager().config.root
        if root and self.rbac and not self.check_permission(root, ADMIN_PERMISSION):
            self.rbac.grant('user', root, ADMIN_PERMISSION)
            _log.info(f'已自动授予 root({root}) 插件全局管理员权限')

        # ---- 初始化持久化数据结构 ----
        self.data.setdefault('sessions', {'group': {}, 'user': {}})
        for scope in ('group', 'user'):
            self.data['sessions'].setdefault(scope, {})
        self.session_store = SessionStore(self.data['sessions'])

        # ---- 初始化 LLM 客户端 ----
        self.llm = llm.LLMClient(self)

        # ---- 加载提示词模板（prompts.yaml，失败回退内置默认） ----
        self.reload_prompt()

        if not self.get_config('IsConfigured'):
            _log.warning(
                '插件未配置，请在全局 config.yaml 的 plugin_configs.NcatBotTRPG 中设置 '
                'ApiKey / Model / BaseUrl 并将 IsConfigured 置为 true'
            )
        _log.debug(f'工作区: {self.workspace}')

    def load_prompts_config(self) -> bool:
        """加载提示词配置文件，更新可用提示词列表。

        从插件工作区加载提示词配置文件，失败时使用内置默认提示词。

        :return: 是否成功加载提示词配置
        """
        # 默认配置文件路径：插件工作区下的 prompts.yaml
        config_file = self.get_config('PromptConfigFile', 'prompts.yaml')
        
        # 相对路径相对于插件工作区，绝对路径按原样解析
        candidate = Path(config_file).expanduser()
        config_path = candidate if candidate.is_absolute() else Path(self.workspace) / candidate
        
        _log.debug(f'尝试加载提示词配置: {config_path}')
        
        if not config_path.exists():
            _log.warning(f'提示词配置文件不存在: {config_path}，使用内置默认提示词')
            self.available_prompts = self._get_builtin_prompts()
            self.default_prompt = self.available_prompts.get('default', '')
            return False
            
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            _log.error(f'提示词配置文件格式错误: {config_path} - {e}，使用内置默认提示词')
            self.available_prompts = self._get_builtin_prompts()
            self.default_prompt = self.available_prompts.get('default', '')
            return False
        except Exception as e:
            _log.error(f'读取提示词配置文件失败: {config_path} - {e}，使用内置默认提示词')
            self.available_prompts = self._get_builtin_prompts()
            self.default_prompt = self.available_prompts.get('default', '')
            return False

        if not isinstance(config_data, dict):
            _log.error(f'提示词配置文件格式错误: {config_path}，使用内置默认提示词')
            self.available_prompts = self._get_builtin_prompts()
            self.default_prompt = self.available_prompts.get('default', '')
            return False

        # 解析提示词配置
        self.available_prompts = {}
        for key, prompt_data in config_data.items():
            if isinstance(prompt_data, dict) and 'content' in prompt_data:
                self.available_prompts[key] = prompt_data['content']
                _log.info(f'已加载提示词: {key} - {prompt_data.get("name", key)}')
            else:
                _log.warning(f'跳过无效的提示词配置: {key}')

        # 设置默认提示词
        self.default_prompt = self.available_prompts.get('default', '')

        _log.info(f'已加载 {len(self.available_prompts)} 个提示词配置')
        return True

    def get_available_prompts(self) -> dict:
        """获取可用的提示词列表。

        :return: 提示词名称到内容的映射
        """
        if not hasattr(self, 'available_prompts'):
            self.load_prompts_config()
        return self.available_prompts

    def get_active_prompt(self) -> str:
        """获取当前激活的提示词内容。

        :return: 当前激活的提示词内容
        """
        active_prompt_name = self.get_config('ActivePrompt', 'default')
        available_prompts = self.get_available_prompts()
        
        if active_prompt_name not in available_prompts:
            _log.warning(f'激活的提示词不存在: {active_prompt_name}，回退到默认提示词')
            if 'default' in available_prompts:
                active_prompt_name = 'default'
            else:
                active_prompt_name = next(iter(available_prompts), '')
        if not active_prompt_name:
            return ''
        return available_prompts[active_prompt_name]

    def set_active_prompt(self, prompt_name: str) -> bool:
        """设置当前激活的提示词。

        :param prompt_name: 提示词名称
        :return: 是否成功设置
        """
        available_prompts = self.get_available_prompts()
        
        if prompt_name not in available_prompts:
            _log.error(f'不存在的提示词: {prompt_name}')
            return False
            
        self.set_config('ActivePrompt', prompt_name)
        self.default_prompt = available_prompts[prompt_name]
        _log.info(f'已切换到提示词: {prompt_name}')
        return True

    def _get_builtin_prompts(self) -> dict:
        """获取内置的提示词。

        当配置文件不存在或加载失败时使用。

        :return: 内置提示词字典
        """
        # 内置提示词集合（default 复用 llm 中的单一来源）
        builtin_prompts = {
            'default': llm.DEFAULT_KEEPER_PROMPT,
            'literary': '''你是一名富有文学素养的 TRPG 主持人，擅长用细腻的笔触描绘世界。

你的职责：
1. 用生动、富有感染力的中文描写场景氛围、人物神态与心理活动，让故事如小说般引人入胜。
2. 扮演所有非玩家角色，赋予他们鲜明的个性和生动的对话，让每个 NPC 都栩栩如生。
3. 根据玩家的行动，用细腻的笔触描述故事的发展，注重细节描写和情感表达。
4. 不替玩家做决定，只提供丰富的环境描写和可能的行动方向，让玩家自由选择。
5. 当行动结果需要检定时，用优雅的方式提示玩家进行掷骰，并描述检定的过程和结果。
6. 严格遵守"本团专属设定"，保持故事的连贯性和沉浸感。
7. 每次回复富有文学性，长度适中，为玩家留下想象空间。''',
            'fast_paced': '''你是一名快节奏的 TRPG 主持人，注重剧情推进和游戏体验。

你的职责：
1. 用简洁明了的中文快速推进剧情，避免冗长的描述，让游戏保持活力。
2. 扮演所有非玩家角色，给出直接、符合性格的反应和对话。
3. 快速响应玩家的行动，明确给出结果和下一步可能的行动方向。
4. 不替玩家做决定，提供清晰的选择和即时的反馈。
5. 当行动需要检定时，直接提示玩家掷骰并快速给出结果。
6. 严格遵守"本团专属设定"，保持剧情的连贯性和紧张感。
7. 每次回复简短有力，通常 2~4 句，专注于推进剧情。''',
            'immersive': '''你是一名追求极致沉浸感的 TRPG 主持人，致力于让玩家完全融入游戏世界。

你的职责：
1. 用第一人称和沉浸式的描写，让玩家感觉自己真的身处游戏世界中。
2. 扮演所有非玩家角色，完全代入角色，用符合身份的语言和反应与玩家互动。
3. 根据玩家的行动，用感官描写（视觉、听觉、嗅觉、触觉）营造真实的游戏体验。
4. 不替玩家做决定，而是通过环境和角色的反应引导玩家做出符合世界观的选择。
5. 当行动需要检定时，用符合世界观的方式描述检定的过程，让掷骰成为游戏的一部分。
6. 严格遵守"本团专属设定"，创造一个自洽且引人入胜的游戏世界。
7. 每次回复都充满沉浸感，让玩家忘记自己正在玩游戏。''',
            'debug': '''你是一名 TRPG 主持人，同时负责向玩家解释游戏机制。

你的职责：
1. 用清晰的中文描述场景和剧情，同时提供相关的游戏机制说明。
2. 扮演所有非玩家角色，给出符合角色性格的反应。
3. 在描述剧情时，适时解释相关的规则和机制，帮助玩家理解游戏。
4. 不替玩家做决定，提供明确的选择和相应的规则说明。
5. 当行动需要检定时，详细解释检定的过程、成功标准和可能的结果。
6. 严格遵守"本团专属设定"，保持剧情的连贯性。
7. 每次回复既推进剧情，又提供必要的游戏信息，帮助玩家更好地理解游戏。'''
        }
        
        _log.info(f'已加载 {len(builtin_prompts)} 个内置提示词')
        return builtin_prompts

    def reload_prompt(self) -> bool:
        """重新加载提示词配置。

        重新加载提示词配置文件并更新当前激活的提示词。

        :return: 是否成功加载
        """
        success = self.load_prompts_config()
        if success:
            # 重新设置当前激活的提示词
            active_prompt_name = self.get_config('ActivePrompt', 'default')
            self.default_prompt = self.get_available_prompts().get(active_prompt_name, self.default_prompt)
            _log.info(f'已重新加载提示词配置，当前使用: {active_prompt_name}')
        return success

    # ------------------------------------------------------------------
    # 跨插件协调接口（供其它插件查询，避免 @机器人 时重复回复）
    # ------------------------------------------------------------------

    def is_group_session_active(self, group_id) -> bool:
        """指定群是否存在进行中的团。

        供其它插件（如 OpenAIChatPlugin）在分发 @机器人 消息前查询：
        跑团进行中的群由本插件接管，其它聊天插件应让出处理权。

        :param group_id: 群号
        :return: 是否有进行中的团
        """
        active = self.session_store.is_active('group', str(group_id))
        _log.debug(f'跨插件查询：群({group_id})跑团是否进行中 = {active}')
        return active

    def is_user_session_active(self, user_id) -> bool:
        """指定私聊是否存在进行中的团（供其它插件协调消息分发）。

        :param user_id: 用户号
        :return: 是否有进行中的团
        """
        active = self.session_store.is_active('user', str(user_id))
        _log.debug(f'跨插件查询：用户({user_id})跑团是否进行中 = {active}')
        return active

    # ------------------------------------------------------------------
    # 消息触发：局内关键词掷骰 / @机器人 或私聊行动
    # ------------------------------------------------------------------

    @registrar.qq.on_group_message()
    async def on_group_message(self, event: GroupMessageEvent):
        """处理群消息：团激活时响应局内关键词掷骰与行动。

        :param event: 群消息事件
        :return: None
        """
        raw = event.raw_message.strip()
        if raw.startswith('/'):
            return
        scope, sid = 'group', str(event.group_id)
        if not self.session_store.is_active(scope, sid):
            _log.debug(f'[group {sid}] 群消息忽略：无进行中的团')
            return

        # 局内关键词掷骰（不受 MustAtBot 限制）
        matched = self._match_dice_keyword(raw)
        if matched is not None:
            hidden, expr = matched
            _log.debug(f'[group {sid}] 命中局内关键词（hidden={hidden}, 骰式={expr!r}）')
            if expr:
                await self._do_roll(event, expr, hidden)
            return

        # 行动触发：默认必须 @机器人
        if self.get_config('MustAtBot', True) and not self._is_at_bot(event):
            _log.debug(f'[group {sid}] 群消息忽略：未 @ 机器人且 MustAtBot=True')
            return
        text = self._clean_action_text(event.raw_message).strip()
        if not text:
            _log.debug(f'[group {sid}] 群消息忽略：无有效行动文本')
            return
        await self._handle_action(event, text, scope, sid)

    @registrar.qq.on_private_message()
    async def on_private_message(self, event: PrivateMessageEvent):
        """处理私聊消息：团激活时响应局内关键词掷骰与行动。

        :param event: 私聊消息事件
        :return: None
        """
        raw = event.raw_message.strip()
        if raw.startswith('/'):
            return
        scope, sid = 'user', str(event.user_id)
        if not self.session_store.is_active(scope, sid):
            _log.debug(f'[user {sid}] 私聊消息忽略：无进行中的团')
            return

        matched = self._match_dice_keyword(raw)
        if matched is not None:
            hidden, expr = matched
            _log.debug(f'[user {sid}] 命中局内关键词（hidden={hidden}, 骰式={expr!r}）')
            if expr:
                await self._do_roll(event, expr, hidden)
            return

        await self._handle_action(event, raw, scope, sid)

    # ------------------------------------------------------------------
    # AI 主持
    # ------------------------------------------------------------------

    async def _handle_action(self, event: MessageEvent, text: str, scope: str, sid: str):
        """将玩家行动交给 AI 主持并回复。

        :param event: 消息事件
        :param text: 行动文本（已去除 @ 组件）
        :param scope: 会话作用域
        :param sid: 会话 ID
        """
        session = self.session_store.get(scope, sid)
        if session is None or not session.get('active'):
            _log.debug(f'[{scope} {sid}] 行动忽略：会话未激活')
            return
        if not self.get_config('IsConfigured'):
            _log.warning(f'[{scope} {sid}] 行动被拒：插件未配置')
            await event.reply(
                text='AI 主持尚未配置：请设置 ApiKey / Model / BaseUrl 并将 IsConfigured 置为 true',
                at_sender=False,
            )
            return

        text = text.strip()
        if not text:
            _log.debug(f'[{scope} {sid}] 行动忽略：文本为空')
            return

        # 群聊中附带 昵称(QQ) 前缀，便于主持人区分不同玩家
        if scope == 'group' and self.get_config('InsertUserdataAsPrefix', True):
            name = event.sender.nickname or str(event.user_id)
            content = llm.USER_PREFIX_FORMAT.format(name=name, user_id=event.user_id, text=text)
        else:
            content = text

        limit = self._get_int_config('MaxHistoryMessages', 30)
        self.session_store.append_turn(scope, sid, 'user', content, limit)

        # 把此前缓存的掷骰结果（类型+结果）经 system prompt 交给主持人
        system_content = llm.build_system_prompt(
            session.get('prompt', ''), None, self)

        # 群聊中向 LLM 传递参与人员 / 玩家状态 / AI 托管请求
        if scope == 'group':
            system_content += self._build_group_context(sid)

        pending_rolls = self.session_store.peek_pending_rolls(scope, sid)
        if pending_rolls:
            system_content += llm.build_roll_context(pending_rolls)
            _log.debug(f'[{scope} {sid}] 注入 {len(pending_rolls)} 条掷骰结果到 system prompt')

        messages = [{'role': 'system', 'content': system_content}]
        messages.extend(list(session.get('history', [])))
        _log.debug(f'[{scope} {sid}] 请求 LLM：{len(messages)} 条消息')

        _log.info(f'[{scope} {sid}] 行动: {content[:OMITTED_TEXT_LENGTH]}')
        try:
            reply = await self.llm.chat(messages)
        except Exception:
            _log.error(traceback.format_exc())
            await event.reply(text='抱歉，AI 主持暂时不可用，请稍后再试', at_sender=False)
            return

        if not reply:
            reply = '（主持人似乎陷入了沉默……）'
        await event.reply(reply, at_sender=False)
        _log.info(f'[{scope} {sid}] 主持回复: {reply[:OMITTED_TEXT_LENGTH]}')

        self.session_store.append_turn(scope, sid, 'assistant', reply, limit)
        # 掷骰结果已随本次请求交给主持人，清空缓存避免重复注入
        self.session_store.clear_pending_rolls(scope, sid)
        self._save_data()

    def _build_group_context(self, sid: str) -> str:
        """构建群聊注入 system prompt 的参与人员 / 玩家状态 / AI 托管请求片段。

        :param sid: 群号
        :return: 以空行开头的追加文本；无内容时返回空串
        """
        parts: list[str] = []
        if info := self.session_store.build_participants_info(sid):
            parts.append(f'【参与人员】\n{info}')
        if info := self.session_store.build_player_status_info(sid):
            parts.append(info)
        if requesters := self.session_store.has_requests_for_ai_control(sid):
            lines = ['【AI托管请求】']
            for user_id in requesters:
                status = self.session_store.get_player_status(sid, user_id)
                if status:
                    lines.append(f'- {player_label(user_id)}: {status["reason"]}')
            parts.append('\n'.join(lines))
        if not parts:
            return ''
        return '\n\n' + '\n\n'.join(parts)

    # ------------------------------------------------------------------
    # 触发辅助
    # ------------------------------------------------------------------

    def _is_at_bot(self, event: GroupMessageEvent) -> bool:
        """判断群消息是否 @ 了机器人。

        :param event: 群消息事件
        :return: 是否 @ 了机器人
        """
        return any(
            isinstance(segment, At) and segment.user_id == str(event.self_id)
            for segment in event.message
        )

    @staticmethod
    def _clean_action_text(raw_message: str) -> str:
        """移除消息中的 CQ:at 组件，得到纯行动文本。

        :param raw_message: 原始消息文本
        :return: 去除 @ 组件后的文本
        """
        return _AT_CQ_PATTERN.sub('', raw_message)

    def _match_dice_keyword(self, text: str) -> tuple[bool, str] | None:
        """匹配局内掷骰关键词。

        关键词需位于消息开头，且后接空格或行尾；``关键词h`` 表示暗骰。

        :param text: 消息文本
        :return: ``(是否暗骰, 骰式)``；未匹配返回 None
        """
        if not self.get_config('EnableInGameDiceKeyword', True):
            _log.debug('局内掷骰关键词已关闭，跳过匹配')
            return None
        keyword = (self.get_config('InGameDiceKeyword', '.r') or '.r').strip()
        if not keyword or not text.startswith(keyword):
            return None

        rest = text[len(keyword):]
        # 暗骰：关键词 + h
        if rest.startswith('h') and (len(rest) == 1 or rest[1] == ' '):
            return True, rest[1:].strip()
        # 公开骰：关键词后紧跟空格或行尾
        if rest == '' or rest[0] == ' ':
            return False, rest.strip()
        return None
