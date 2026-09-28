# -*- coding: utf-8 -*-
"""团会话状态存取（自研）

每个会话（群聊按 group_id、私聊按 user_id）对应一个“团”，保存激活状态、
专属主持人设定与剧情历史。存储直接引用 ``DataMixin`` 的 ``plugin.data['sessions']``，
由插件在变更后调用 ``_save_data()`` 持久化。
"""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from ncatbot.utils.logger import get_log

_log = get_log('ncatbot_trpg')

# 会话作用域：group（群聊） / user（私聊）
SCOPES = ('group', 'user')


class SessionStore:
    """团会话状态管理器。"""

    def __init__(self, sessions: Dict[str, Dict[str, dict]]):
        """初始化并补齐作用域结构。

        :param sessions: 持久化字典（``plugin.data['sessions']`` 的引用）
        """
        self._sessions = sessions
        for scope in SCOPES:
            self._sessions.setdefault(scope, {})

    def get(self, scope: str, session_id) -> Optional[dict]:
        """获取会话；不存在返回 None。"""
        return self._sessions[scope].get(str(session_id))

    def is_active(self, scope: str, session_id) -> bool:
        """会话是否处于跑团进行中状态。"""
        session = self.get(scope, session_id)
        return bool(session and session.get('active'))

    def create(self, scope: str, session_id, prompt: str = '') -> dict:
        """创建（激活）一个新团。

        :param scope: ``group`` 或 ``user``
        :param session_id: 群号或用户号
        :param prompt: 本团专属主持人设定（可为空）
        :return: 新建的会话字典
        """
        session = {
            'active': True,
            'prompt': prompt or '',
            'history': [],
            'pending_rolls': [],
            'started_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        self._sessions[scope][str(session_id)] = session
        _log.debug(f'[{scope} {session_id}] 创建会话（专属设定 {len(session["prompt"])} 字）')
        return session

    def update(self, scope: str, session_id, **fields) -> Optional[dict]:
        """更新会话字段；会话不存在返回 None。"""
        session = self.get(scope, session_id)
        if session is None:
            _log.debug(f'[{scope} {session_id}] 更新会话失败：不存在')
            return None
        session.update(fields)
        _log.debug(f'[{scope} {session_id}] 更新会话字段: {list(fields)}')
        return session

    def clear_history(self, scope: str, session_id) -> bool:
        """清空剧情历史（保留设定与激活状态）；会话不存在返回 False。"""
        session = self.get(scope, session_id)
        if session is None:
            return False
        session['history'] = []
        _log.debug(f'[{scope} {session_id}] 已清空聊天历史')
        return True

    def append_turn(self, scope: str, session_id, role: str, content: str,
                    limit: int = 30) -> None:
        """追加一条历史消息，并按 ``limit`` 截断最早的记录。

        :param scope: ``group`` 或 ``user``
        :param session_id: 群号或用户号
        :param role: ``user`` 或 ``assistant``
        :param content: 消息内容
        :param limit: 保留的最大消息数（<=0 表示不限制）
        """
        session = self.get(scope, session_id)
        if session is None:
            _log.debug(f'[{scope} {session_id}] 追加历史失败：会话不存在')
            return
        history: List[dict] = session.setdefault('history', [])
        history.append({'role': role, 'content': content})
        if limit and limit > 0 and len(history) > limit:
            session['history'] = history[-limit:]
            _log.debug(f'[{scope} {session_id}] 历史超限，已截断至最近 {limit} 条')
        _log.debug(f'[{scope} {session_id}] 追加历史 {role}（共 {len(session["history"])} 条）')

    # ------------------------------------------------------------------
    # 待传递掷骰结果（在玩家下一次行动时注入 system prompt 供主持人参考）
    # 同一位玩家在结果被消费前不能重复投掷，避免刷骰
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_rolls(rolls: List) -> List[dict]:
        """把历史遗留的纯文本条目归一化为 ``{'user_id', 'text'}`` 结构。"""
        normalized = []
        for item in rolls or []:
            if isinstance(item, dict):
                normalized.append(item)
            else:
                normalized.append({'user_id': None, 'text': str(item)})
        return normalized

    def has_pending_roll(self, scope: str, session_id, user_id) -> bool:
        """指定玩家是否已有未结算的掷骰结果。

        :param scope: ``group`` 或 ``user``
        :param session_id: 群号或用户号
        :param user_id: 玩家 QQ
        :return: 是否存在未结算掷骰
        """
        session = self.get(scope, session_id)
        if session is None:
            return False
        uid = str(user_id)
        return any(
            entry.get('user_id') == uid
            for entry in self._normalize_rolls(session.get('pending_rolls'))
        )

    def add_pending_roll(self, scope: str, session_id, user_id, text: str,
                         limit: int = 20) -> bool:
        """记录一条掷骰结果，等待随玩家下一次行动注入 system prompt。

        同一位玩家若已有未结算掷骰，则拒绝新增（防止重复投掷）。

        :param scope: ``group`` 或 ``user``
        :param session_id: 群号或用户号
        :param user_id: 玩家 QQ
        :param text: 掷骰结果摘要（含掷骰人、骰式与结果）
        :param limit: 最多保留的条目数（<=0 表示不限制）
        :return: 是否成功记录（False 表示该玩家已有未结算掷骰）
        """
        session = self.get(scope, session_id)
        if session is None:
            _log.debug(f'[{scope} {session_id}] 记录掷骰失败：会话不存在')
            return False
        uid = str(user_id)
        rolls = self._normalize_rolls(session.get('pending_rolls'))
        if any(entry.get('user_id') == uid for entry in rolls):
            _log.info(f'[{scope} {session_id}] 用户({uid})已有未结算掷骰，拒绝重复投掷')
            return False
        rolls.append({'user_id': uid, 'text': text})
        if limit and limit > 0 and len(rolls) > limit:
            rolls = rolls[-limit:]
        session['pending_rolls'] = rolls
        _log.debug(f'[{scope} {session_id}] 已记录待传递掷骰（共 {len(rolls)} 条）')
        return True

    def peek_pending_rolls(self, scope: str, session_id) -> List[str]:
        """查看待传递掷骰结果文本（不清除）。

        :param scope: ``group`` 或 ``user``
        :param session_id: 群号或用户号
        :return: 掷骰结果摘要列表
        """
        session = self.get(scope, session_id)
        if session is None:
            return []
        result = []
        for entry in self._normalize_rolls(session.get('pending_rolls')):
            text = entry.get('text')
            if text:
                result.append(text)
        return result

    def clear_pending_rolls(self, scope: str, session_id) -> None:
        """清空待传递掷骰结果（在成功注入并收到主持人回复后调用）。

        :param scope: ``group`` 或 ``user``
        :param session_id: 群号或用户号
        """
        session = self.get(scope, session_id)
        if session is None:
            return
        if session.get('pending_rolls'):
            session['pending_rolls'] = []
            _log.debug(f'[{scope} {session_id}] 已清空待传递掷骰结果')
