# -*- coding: utf-8 -*-
"""NcatBotTRPG 插件入口

自研纯插件：通用掷骰引擎 + 极简 AI 跑团主持（openai SDK）+ 三层 RBAC 权限体系。
"""
from __future__ import annotations

import re
import traceback

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At
from ncatbot.utils import get_config_manager
from ncatbot.utils.logger import get_log

from . import llm
from .command_handler import ADMIN_PERMISSION, NcatBotTRPGCommandMixin
from .session import SessionStore

_log = get_log('ncatbot_trpg')

# 日志中省略的文本长度
OMITTED_TEXT_LENGTH = 100

# 匹配并移除 CQ:at 组件，用于提取纯文本行动内容
_AT_CQ_PATTERN = re.compile(r'\[CQ:at,[^\]]*\]')


class NcatBotTRPGPlugin(NcatBotTRPGCommandMixin, NcatBotPlugin):
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
            'IsConfigured': False,          # 是否已完成配置
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
            'PromptFile': '',               # 主持人提示词文件（相对工作区或绝对路径，空=内置默认）
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

        # ---- 导入可选的默认主持人提示词（PromptFile，失败回退内置默认） ----
        self.reload_prompt()

        if not self.get_config('IsConfigured'):
            _log.warning(
                '插件未配置，请在全局 config.yaml 的 plugin_configs.NcatBotTRPG 中设置 '
                'ApiKey / Model / BaseUrl 并将 IsConfigured 置为 true'
            )
        _log.debug(f'工作区: {self.workspace}')

    def reload_prompt(self) -> bool:
        """按配置（PromptFile）重新导入默认主持人提示词。

        可在修改配置后调用；导入失败时回退为内置默认提示词。

        :return: 是否成功从文件导入
        """
        self.default_prompt = llm.load_prompt_file(self, self.get_config('PromptFile', '')) or ''
        if self.default_prompt:
            _log.info('已加载配置指定的主持人提示词')
        else:
            _log.debug('使用内置默认主持人提示词')
        return bool(self.default_prompt)

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
            session.get('prompt', ''), self.default_prompt)
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
