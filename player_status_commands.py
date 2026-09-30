# -*- coding: utf-8 -*-
"""玩家状态命令 Mixin。

群聊房间内的玩家参与状态管理：``/trpg away``、``/trpg offline``、
``/trpg back``、``/trpg ai-control``。
"""
from __future__ import annotations

from ncatbot.event.qq import MessageEvent


class PlayerStatusCommandMixin:
    """``/trpg away|offline|back|ai-control`` 的处理器。"""

    async def _cmd_away(self, event: MessageEvent, args: list[str]):
        """暂时离开游戏。"""
        scope, sid = self._scope_sid(event)
        if scope != 'group':
            await event.reply(text='❌ 暂时离开功能仅可在群聊房间中使用', at_sender=False)
            return

        user_id = str(event.user_id)
        reason = ' '.join(args).strip() if args else '暂时离开'

        if not self.session_store.is_participant(sid, user_id):
            await event.reply(text='❌ 你不在这个房间中', at_sender=False)
            return

        if self.session_store.set_player_away(sid, user_id, reason):
            self._save_data()
            await event.reply(
                text=f'✅ 你已暂时离开游戏\n'
                     f'原因：{reason}\n'
                     f'使用 /trpg back 重新加入',
                at_sender=False,
            )
        else:
            await event.reply(text='❌ 设置离开状态失败', at_sender=False)

    async def _cmd_offline(self, event: MessageEvent, args: list[str]):
        """长时间离线。"""
        scope, sid = self._scope_sid(event)
        if scope != 'group':
            await event.reply(text='❌ 长时间离线功能仅可在群聊房间中使用', at_sender=False)
            return

        user_id = str(event.user_id)
        reason = ' '.join(args).strip() if args else '长时间离线'

        if not self.session_store.is_participant(sid, user_id):
            await event.reply(text='❌ 你不在这个房间中', at_sender=False)
            return

        if self.session_store.set_player_offline(sid, user_id, reason):
            self._save_data()
            await event.reply(
                text=f'✅ 你已标记为离线\n'
                     f'原因：{reason}\n'
                     f'使用 /trpg back 重新加入',
                at_sender=False,
            )
        else:
            await event.reply(text='❌ 设置离线状态失败', at_sender=False)

    async def _cmd_back(self, event: MessageEvent):
        """重新加入游戏。"""
        scope, sid = self._scope_sid(event)
        if scope != 'group':
            await event.reply(text='❌ 重新加入功能仅可在群聊房间中使用', at_sender=False)
            return

        user_id = str(event.user_id)
        if not self.session_store.is_participant(sid, user_id):
            await event.reply(text='❌ 你不在这个房间中', at_sender=False)
            return

        if self.session_store.reactivate_player(sid, user_id):
            self._save_data()
            await event.reply(text='✅ 欢迎回来！你已重新加入游戏', at_sender=False)
        else:
            await event.reply(text='❌ 重新加入失败', at_sender=False)

    async def _cmd_ai_control(self, event: MessageEvent, args: list[str]):
        """请求 AI 托管。"""
        scope, sid = self._scope_sid(event)
        if scope != 'group':
            await event.reply(text='❌ AI托管功能仅可在群聊房间中使用', at_sender=False)
            return

        user_id = str(event.user_id)
        reason = ' '.join(args).strip() if args else '需要AI托管'

        if not self.session_store.is_participant(sid, user_id):
            await event.reply(text='❌ 你不在这个房间中', at_sender=False)
            return

        if self.session_store.request_ai_control(sid, user_id, reason):
            self._save_data()
            await event.reply(
                text=f'✅ 已请求AI托管\n'
                     f'原因：{reason}\n'
                     f'主持人将接管你的角色',
                at_sender=False,
            )
        else:
            await event.reply(text='❌ 请求AI托管失败', at_sender=False)
