# -*- coding: utf-8 -*-
"""团会话状态存取

每个会话（群聊按 group_id、私聊按 user_id）对应一个"团"，保存激活状态、
专属主持人设定与剧情历史。存储直接引用 ``DataMixin`` 的 ``plugin.data['sessions']``，
由插件在变更后调用 ``_save_data()`` 持久化。

房间系统：房间与群会话绑定，每群默认有且只有一个房间（惰性创建、默认 0 人），
/trpg start 后向 LLM 传递参与人员信息。
"""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from ncatbot.utils.logger import get_log

from . import dice

_log = get_log('ncatbot_trpg')

# 会话作用域：group（群聊） / user（私聊）
SCOPES = ('group', 'user')

# 房间状态到中文展示
ROOM_STATUS_TEXT = {'preparing': '准备中', 'running': '进行中', 'finished': '已结束'}


def player_label(user_id: str) -> str:
    """参与者在回复中的展示名（暂用 QQ 号，后续可换昵称）。

    :param user_id: 用户 QQ
    :return: 展示名
    """
    return f'玩家{user_id}'


class SessionStore:
    """团会话状态管理器。"""

    def __init__(self, sessions: Dict[str, Dict[str, dict]]):
        """初始化并补齐作用域结构。

        :param sessions: 持久化字典（``plugin.data['sessions']`` 的引用）
        """
        self._sessions = sessions
        for scope in SCOPES:
            self._sessions.setdefault(scope, {})
        
        # 房间系统数据结构：group_id -> room_info
        self._rooms = sessions.setdefault('rooms', {})

    # ------------------------------------------------------------------
    # 房间系统：群与房间绑定，每群有且只有一个房间（惰性默认 0 人）
    # ------------------------------------------------------------------

    def _default_room(self, group_id: str) -> dict:
        """构造默认房间（不落库）：0 人、准备中。"""
        return {
            'name': f'群 {group_id} 的房间',
            'created_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'participants': [],  # 默认 0 人，玩家 /trpg join 后加入
            'status': 'preparing',  # preparing, running, finished
            'max_participants': 0,  # 0 表示无限制
            'description': '',  # 房间描述
        }

    def get_or_create_room(self, group_id: str) -> dict:
        """获取群房间；不存在时惰性创建并持久化默认房间（0 人）。

        每个群默认有且只有一个房间，与群会话（group_id）绑定，无需手动创建。

        :param group_id: 群号
        :return: 房间信息
        """
        room = self._rooms.get(str(group_id))
        if room is None:
            room = self._default_room(group_id)
            self._rooms[str(group_id)] = room
            _log.info(f'惰性创建默认房间：群({group_id})')
        return room

    def room_view(self, group_id: str) -> dict:
        """只读获取房间视图：不存在时返回临时默认房间，不写库。

        :param group_id: 群号
        :return: 房间信息（可能是未持久化的默认房间）
        """
        return self._rooms.get(str(group_id)) or self._default_room(group_id)

    def get_room(self, group_id: str) -> Optional[dict]:
        """获取已持久化的房间信息；不存在返回 None。

        :param group_id: 群号
        :return: 房间信息或 None
        """
        return self._rooms.get(str(group_id))

    def join_room(self, group_id: str, user_id: str) -> bool:
        """加入房间。

        :param group_id: 群号
        :param user_id: 用户QQ
        :return: 是否成功加入
        """
        room = self.get_or_create_room(group_id)

        # 检查房间状态
        if room['status'] not in ('preparing', 'finished'):
            _log.warning(f'房间({group_id})状态不是准备中，无法加入')
            return False

        # 检查人数限制
        if room['max_participants'] > 0 and len(room['participants']) >= room['max_participants']:
            _log.warning(f'房间({group_id})已满')
            return False
            
        # 检查是否已在房间中
        if str(user_id) in room['participants']:
            _log.info(f'用户({user_id})已在房间({group_id})中')
            return True
            
        # 加入房间
        room['participants'].append(str(user_id))
        _log.info(f'用户({user_id})加入房间({group_id})')
        return True

    def leave_room(self, group_id: str, user_id: str) -> bool:
        """离开房间。

        :param group_id: 群号
        :param user_id: 用户QQ
        :return: 是否成功离开
        """
        room = self.get_room(group_id)
        if room is None:
            _log.warning(f'房间不存在：群({group_id})')
            return False
            
        if str(user_id) not in room['participants']:
            _log.info(f'用户({user_id})不在房间({group_id})中')
            return False
            
        # 离开房间：清除玩家状态，房间保留（清空后仍为默认 0 人房间）
        room['participants'].remove(str(user_id))
        room.get('player_statuses', {}).pop(str(user_id), None)
        _log.info(f'用户({user_id})离开房间({group_id})，剩余 {len(room["participants"])} 人')

        return True

    def start_room(self, group_id: str) -> bool:
        """开始房间（进入跑团状态）。

        :param group_id: 群号
        :return: 是否成功开始
        """
        room = self.get_or_create_room(group_id)

        if room['status'] not in ('preparing', 'finished'):
            _log.warning(f'房间({group_id})状态不是准备中，无法开始')
            return False
            
        if len(room['participants']) < 1:
            _log.warning(f'房间({group_id})没有参与者，无法开始')
            return False
            
        # 更新房间状态
        room['status'] = 'running'
        room['started_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _log.info(f'房间({group_id})开始跑团，参与者：{len(room["participants"])}人')
        return True

    def finish_room(self, group_id: str) -> bool:
        """结束房间（跑团结束）。

        :param group_id: 群号
        :return: 是否成功结束
        """
        room = self.get_room(group_id)
        if room is None:
            return False

        room['status'] = 'finished'
        room['finished_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _log.info(f'房间({group_id})结束跑团')
        return True

    def delete_room(self, group_id: str) -> bool:
        """删除房间（下次访问会重新惰性创建默认房间）。

        :param group_id: 群号
        :return: 是否成功删除
        """
        if self._rooms.pop(str(group_id), None) is None:
            return False
        _log.info(f'房间({group_id})已删除')
        return True

    def update_room(self, group_id: str, **fields) -> bool:
        """更新房间信息。

        :param group_id: 群号
        :param fields: 要更新的字段
        :return: 是否成功更新
        """
        room = self.get_or_create_room(group_id)

        room.update(fields)
        _log.info(f'房间({group_id})更新字段: {list(fields)}')
        return True

    def get_participants(self, group_id: str) -> List[str]:
        """获取房间参与者列表。

        :param group_id: 群号
        :return: 参与者QQ列表
        """
        room = self.get_room(group_id)
        if room is None:
            return []
        return room['participants']

    def is_participant(self, group_id: str, user_id: str) -> bool:
        """检查用户是否是房间参与者。

        :param group_id: 群号
        :param user_id: 用户QQ
        :return: 是否是参与者
        """
        room = self.get_room(group_id)
        if room is None:
            return False
        return str(user_id) in room['participants']

    def get_participant_count(self, group_id: str) -> int:
        """获取房间参与者数量。

        :param group_id: 群号
        :return: 参与者数量
        """
        room = self.get_room(group_id)
        if room is None:
            return 0
        return len(room['participants'])

    def build_participants_info(self, group_id: str) -> str:
        """构建参与者信息，用于传递给LLM。

        :param group_id: 群号
        :return: 参与者信息文本
        """
        participants = self.get_participants(group_id)
        if not participants:
            return ''
            
        participant_info = []
        for i, user_id in enumerate(participants, 1):
            participant_info.append(f'{i}. {player_label(user_id)}')
            
        return '\n'.join(participant_info)

    def participant_names(self, group_id: str) -> List[str]:
        """获取参与者展示名列表（暂用 QQ 号）。

        :param group_id: 群号
        :return: 展示名列表
        """
        return [player_label(uid) for uid in self.get_participants(group_id)]

    def set_room_description(self, group_id: str, description: str) -> bool:
        """设置房间描述。

        :param group_id: 群号
        :param description: 描述文本
        :return: 是否成功设置
        """
        return self.update_room(group_id, description=description)

    def set_room_max_participants(self, group_id: str, max_participants: int) -> bool:
        """设置房间最大参与者数量。

        :param group_id: 群号
        :param max_participants: 最大数量
        :return: 是否成功设置
        """
        return self.update_room(group_id, max_participants=max_participants)

    # ------------------------------------------------------------------
    # 玩家状态管理：中途退出/请求托管/重新加入
    # ------------------------------------------------------------------

    def set_player_status(self, group_id: str, user_id: str, status: str, reason: str = '') -> bool:
        """设置玩家状态。

        :param group_id: 群号
        :param user_id: 用户QQ
        :param status: 玩家状态（active, away, offline, requested_ai_control）
        :param reason: 状态原因
        :return: 是否成功设置
        """
        room = self.get_room(group_id)
        if not room:
            return False
            
        if str(user_id) not in room['participants']:
            return False
            
        # 初始化玩家状态字典（如果不存在）
        if 'player_statuses' not in room:
            room['player_statuses'] = {}
            
        # 更新玩家状态
        room['player_statuses'][str(user_id)] = {
            'status': status,
            'reason': reason,
            'updated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        _log.info(f'玩家({user_id})状态更新为{status}，原因：{reason}')
        return True

    def get_player_status(self, group_id: str, user_id: str) -> Optional[dict]:
        """获取玩家状态。

        :param group_id: 群号
        :param user_id: 用户QQ
        :return: 玩家状态信息，不存在返回None
        """
        room = self.get_room(group_id)
        if not room:
            return None
            
        if 'player_statuses' not in room:
            return None
            
        return room['player_statuses'].get(str(user_id))

    def get_all_player_statuses(self, group_id: str) -> dict:
        """获取所有玩家状态。

        :param group_id: 群号
        :return: 所有玩家状态信息
        """
        room = self.get_room(group_id)
        if not room:
            return {}
            
        return room.get('player_statuses', {})

    def get_active_players(self, group_id: str) -> List[str]:
        """获取活跃玩家列表。

        :param group_id: 群号
        :return: 活跃玩家QQ列表
        """
        room = self.get_room(group_id)
        if not room:
            return []
            
        active_players = []
        statuses = self.get_all_player_statuses(group_id)
        
        for user_id in room['participants']:
            status_info = statuses.get(str(user_id))
            if not status_info or status_info['status'] == 'active':
                active_players.append(user_id)
                
        return active_players

    def request_ai_control(self, group_id: str, user_id: str, reason: str = '') -> bool:
        """请求AI托管。

        :param group_id: 群号
        :param user_id: 用户QQ
        :param reason: 请求原因
        :return: 是否成功请求
        """
        return self.set_player_status(group_id, user_id, 'requested_ai_control', reason)

    def set_player_away(self, group_id: str, user_id: str, reason: str = '') -> bool:
        """设置玩家离开。

        :param group_id: 群号
        :param user_id: 用户QQ
        :param reason: 离开原因
        :return: 是否成功设置
        """
        return self.set_player_status(group_id, user_id, 'away', reason)

    def set_player_offline(self, group_id: str, user_id: str, reason: str = '') -> bool:
        """设置玩家离线。

        :param group_id: 群号
        :param user_id: 用户QQ
        :param reason: 离线原因
        :return: 是否成功设置
        """
        return self.set_player_status(group_id, user_id, 'offline', reason)

    def reactivate_player(self, group_id: str, user_id: str) -> bool:
        """重新激活玩家。

        :param group_id: 群号
        :param user_id: 用户QQ
        :return: 是否成功激活
        """
        return self.set_player_status(group_id, user_id, 'active', '重新加入游戏')

    def build_player_status_info(self, group_id: str) -> str:
        """构建玩家状态信息，用于传递给LLM。

        :param group_id: 群号
        :return: 玩家状态信息文本
        """
        room = self.get_room(group_id)
        if not room:
            return ''
            
        participants = room['participants']
        statuses = self.get_all_player_statuses(group_id)
        
        if not participants:
            return ''
            
        status_lines = ['【玩家状态】']
        
        for i, user_id in enumerate(participants, 1):
            status_info = statuses.get(str(user_id))
            if status_info:
                status_text = status_info['status']
                reason_text = f' ({status_info["reason"]})' if status_info['reason'] else ''
                updated_at = f' @ {status_info["updated_at"]}' if status_info.get('updated_at') else ''
                status_lines.append(f'{i}. {player_label(user_id)}: {status_text}{reason_text}{updated_at}')
            else:
                status_lines.append(f'{i}. {player_label(user_id)}: 活跃')
                
        return '\n'.join(status_lines)

    def has_requests_for_ai_control(self, group_id: str) -> List[str]:
        """检查是否有玩家请求AI托管。

        :param group_id: 群号
        :return: 请求AI托管的玩家列表
        """
        room = self.get_room(group_id)
        if not room:
            return []
            
        requesting_players = []
        statuses = self.get_all_player_statuses(group_id)
        
        for user_id, status_info in statuses.items():
            if status_info['status'] == 'requested_ai_control':
                requesting_players.append(user_id)
                
        return requesting_players

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
            'pending_checks': [],
            'resolved_checks': [],
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
    # 检定请求（主持人经 request_check 工具下发给玩家，玩家自行掷骰）
    # 允许多条并存；玩家掷出匹配骰式后由系统按请求的目标值裁定成败
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_check_fields(session: dict) -> None:
        """补齐会话的检定字段（兼容旧数据）。"""
        session.setdefault('pending_checks', [])
        session.setdefault('resolved_checks', [])

    def add_check(self, scope: str, session_id, check: dict,
                  limit: int = 20) -> Optional[dict]:
        """记录一条待玩家掷骰的检定请求。

        :param scope: ``group`` 或 ``user``
        :param session_id: 群号或用户号
        :param check: 检定信息（至少含 roller_id / subject / roll_key）
        :param limit: 最多保留的检定条数（<=0 表示不限制）
        :return: 写入后的检定字典；会话不存在返回 None
        """
        session = self.get(scope, session_id)
        if session is None:
            _log.debug(f'[{scope} {session_id}] 记录检定失败：会话不存在')
            return None
        self._ensure_check_fields(session)
        record = dict(check)
        record.setdefault('id', f'c{len(session["pending_checks"]) + 1}')
        record.setdefault('status', 'awaiting')
        record.setdefault('verdict', None)
        record.setdefault('created_at', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        session['pending_checks'].append(record)
        if limit and limit > 0 and len(session['pending_checks']) > limit:
            session['pending_checks'] = session['pending_checks'][-limit:]
        _log.info(
            f'[{scope} {session_id}] 新增检定请求 {record["id"]}：'
            f'{record.get("subject")} 掷 {record.get("expr")}（{record.get("reason")}）'
        )
        return record

    def peek_checks(self, scope: str, session_id, *, status: str = 'awaiting',
                    roller_id=None) -> List[dict]:
        """查看检定请求（不带筛选时返回全部待完成项）。

        :param status: 筛选状态（``awaiting`` / ``resolved`` / None 表示不限）
        :param roller_id: 仅返回该玩家需掷的检定
        :return: 检定请求列表
        """
        session = self.get(scope, session_id)
        if session is None:
            return []
        self._ensure_check_fields(session)
        result = []
        for check in session['pending_checks']:
            if status and check.get('status') != status:
                continue
            if roller_id is not None and str(check.get('roller_id')) != str(roller_id):
                continue
            result.append(check)
        return result

    def resolve_checks_for_roll(self, scope: str, session_id, roller_id,
                                roll_key: str, total: int,
                                limit: int = 20) -> List[dict]:
        """玩家掷骰后，结算匹配骰式的待完成检定。

        仅当骰式规范化键（``dice.roll_key``）一致时才算完成；成败由系统按
        请求中记录的目标值裁定（玩家命令中的判定被忽略，保证权威性）。

        :param scope: ``group`` 或 ``user``
        :param session_id: 群号或用户号
        :param roller_id: 掷骰玩家
        :param roll_key: 掷出骰式的规范化键
        :param total: 掷出总值
        :param limit: ``resolved_checks`` 保留上限
        :return: 本次结算的检定列表（可能为空）
        """
        session = self.get(scope, session_id)
        if session is None:
            return []
        self._ensure_check_fields(session)
        uid = str(roller_id)
        resolved: List[dict] = []
        for check in session['pending_checks']:
            if check.get('status') != 'awaiting':
                continue
            if str(check.get('roller_id')) != uid:
                continue
            if check.get('roll_key') != roll_key:
                continue
            verdict = None
            op, target = check.get('op'), check.get('target')
            if op and target is not None:
                verdict = dice.judge(total, op, int(target))
            check['status'] = 'resolved'
            check['verdict'] = verdict
            resolved.append(check)
            session['resolved_checks'].append({
                'id': check.get('id'),
                'user_id': uid,
                'subject': check.get('subject'),
                'reason': check.get('reason'),
                'verdict': verdict,
            })
            if limit and limit > 0 and len(session['resolved_checks']) > limit:
                session['resolved_checks'] = session['resolved_checks'][-limit:]
        if resolved:
            _log.info(
                f'[{scope} {session_id}] 玩家({uid})掷骰结算 {len(resolved)} 条检定：'
                + '、'.join(f'{c.get("reason")}={c.get("verdict")}' for c in resolved)
            )
        return resolved

    def has_resolved_check(self, scope: str, session_id, roller_id) -> bool:
        """指定玩家是否有已结算且尚未消费的检定（用于伤害门控）。"""
        session = self.get(scope, session_id)
        if session is None:
            return False
        uid = str(roller_id)
        return any(str(item.get('user_id')) == uid
                   for item in session.get('resolved_checks', []))

    def clear_resolved_checks(self, scope: str, session_id) -> None:
        """清空已结算检定记录（主主持人回合结束后调用）。"""
        session = self.get(scope, session_id)
        if session is not None:
            session['resolved_checks'] = []

    def clear_checks(self, scope: str, session_id) -> None:
        """清空全部检定请求（reset 时调用）。"""
        session = self.get(scope, session_id)
        if session is not None:
            session['pending_checks'] = []
            session['resolved_checks'] = []

    def build_check_context(self, scope: str, session_id) -> str:
        """把已结算的检定结果拼装为 system 片段（供主持人叙事）。"""
        session = self.get(scope, session_id)
        if session is None:
            return ''
        items = session.get('resolved_checks', [])
        if not items:
            return ''
        lines = []
        for item in items:
            verdict = item.get('verdict') or '（未判定）'
            lines.append(f'- {item.get("subject")} {item.get("reason")}：{verdict}')
        return (
            '\n\n【系统检定结果（由系统裁定，请据此叙事，不得重掷或改判）】\n'
            + '\n'.join(lines)
        )

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
