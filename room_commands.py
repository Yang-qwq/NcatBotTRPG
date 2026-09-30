# -*- coding: utf-8 -*-
"""房间命令 Mixin。

房间与会话绑定：每群默认有且只有一个房间（惰性创建、默认 0 人），
无需创建命令。包含用户命令 ``/trpg room *`` 与管理员命令
``/trpg-admin room-admin *`` 的实现。
"""
from __future__ import annotations

from ncatbot.event.qq import GroupMessageEvent, MessageEvent

from .session import ROOM_STATUS_TEXT


class RoomCommandMixin:
    """``/trpg room *`` 与 ``/trpg-admin room-admin *`` 的处理器。"""

    # ------------------------------------------------------------------
    # 用户命令 /trpg room *
    # ------------------------------------------------------------------

    async def _cmd_room(self, event: MessageEvent, args: list[str]):
        """处理房间命令。"""
        if not isinstance(event, GroupMessageEvent):
            await event.reply(text='房间命令仅可在群聊中使用', at_sender=False)
            return

        if not args:
            await event.reply(
                text='请指定房间操作：join, leave, status, participants',
                at_sender=False,
            )
            return

        sub = args[0].lower()
        sub_args = args[1:]

        if sub == 'join':
            await self._cmd_room_join(event, sub_args)
        elif sub == 'leave':
            await self._cmd_room_leave(event)
        elif sub == 'status':
            await self._cmd_room_status(event)
        elif sub == 'participants':
            await self._cmd_room_participants(event)
        else:
            await event.reply(
                text='未知的房间操作，可用：join, leave, status, participants',
                at_sender=False,
            )

    async def _cmd_room_join(self, event: MessageEvent, args: list[str]):
        """加入房间。"""
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        # 房间默认存在（惰性创建），直接取用
        room = self.session_store.get_or_create_room(group_id)
        if room['status'] not in ('preparing', 'finished'):
            await event.reply(text='❌ 房间不是准备状态，无法加入', at_sender=False)
            return

        if self.session_store.is_participant(group_id, user_id):
            await event.reply(text='✅ 你已经在房间中了', at_sender=False)
            return

        password = ' '.join(args).strip() if args else ''
        if room['password'] and not password:
            await event.reply(text='❌ 此房间需要密码，请使用 /trpg room join <密码>', at_sender=False)
            return

        if self.session_store.join_room(group_id, user_id, password):
            self._save_data()
            participant_count = self.session_store.get_participant_count(group_id)
            await event.reply(
                text=f'✅ 成功加入房间！\n'
                     f'当前参与者：{participant_count}人\n'
                     f'状态：准备中\n'
                     f'等待管理员开始跑团',
                at_sender=False,
            )
        else:
            await event.reply(text='❌ 加入房间失败，请检查密码或房间状态', at_sender=False)

    async def _cmd_room_leave(self, event: MessageEvent):
        """离开房间。"""
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        if not self.session_store.is_participant(group_id, user_id):
            await event.reply(text='❌ 你不在这个房间中', at_sender=False)
            return

        if self.session_store.leave_room(group_id, user_id):
            self._save_data()
            participant_count = self.session_store.get_participant_count(group_id)
            await event.reply(
                text=f'✅ 已离开房间\n'
                     f'房间剩余参与者：{participant_count}人',
                at_sender=False,
            )
        else:
            await event.reply(text='❌ 离开房间失败', at_sender=False)

    async def _cmd_room_status(self, event: MessageEvent):
        """查看房间状态。"""
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        room = self.session_store.room_view(group_id)
        is_in_room = self.session_store.is_participant(group_id, user_id)

        lines = [
            f'🏠 房间信息：{room["name"]}',
            f'📊 状态：{ROOM_STATUS_TEXT.get(room["status"], "未知")}',
            f'👥 参与者：{len(room["participants"])}人',
            '🟢 你在房间中' if is_in_room else '⚪ 你不在房间中',
            f'🕒 创建时间：{room["created_at"]}',
        ]
        if room['status'] == 'running':
            lines.append(f'🚀 开始时间：{room.get("started_at", "未知")}')
        elif room['status'] == 'finished':
            lines.append(f'⏹️ 结束时间：{room.get("finished_at", "未知")}')
        if room['description']:
            lines.append(f'📝 描述：{room["description"]}')

        await event.reply(text='\n'.join(lines), at_sender=False)

    async def _cmd_room_participants(self, event: MessageEvent):
        """查看房间参与者列表。"""
        group_id = str(event.group_id)

        names = self.session_store.participant_names(group_id)
        if not names:
            await event.reply(text='📋 房间暂无参与者', at_sender=False)
            return

        lines = ['👥 房间参与者：']
        lines.extend(f'{i}. {name}' for i, name in enumerate(names, 1))
        await event.reply(text='\n'.join(lines), at_sender=False)

    # ------------------------------------------------------------------
    # 管理员命令 /trpg-admin room-admin *
    # （权限已由 on_trpg_admin 统一校验）
    # ------------------------------------------------------------------

    async def _cmd_room_admin(self, event: MessageEvent, group_id: str, args: list[str]):
        """处理房间管理命令。"""
        if not args:
            await event.reply(
                text='请指定房间管理操作：set-description, set-password, set-max, delete',
                at_sender=False,
            )
            return

        sub = args[0].lower()
        sub_args = args[1:]

        if sub == 'set-description':
            await self._cmd_room_set_description(event, group_id, sub_args)
        elif sub == 'set-password':
            await self._cmd_room_set_password(event, group_id, sub_args)
        elif sub == 'set-max':
            await self._cmd_room_set_max(event, group_id, sub_args)
        elif sub == 'delete':
            await self._cmd_room_delete(event, group_id)
        else:
            await event.reply(
                text='未知的房间管理操作，可用：set-description, set-password, set-max, delete',
                at_sender=False,
            )

    async def _cmd_room_set_description(self, event: MessageEvent, group_id: str, args: list[str]):
        """设置房间描述。"""
        description = ' '.join(args).strip()
        if not description:
            await event.reply(text='请提供房间描述', at_sender=False)
            return

        if self.session_store.set_room_description(group_id, description):
            self._save_data()
            await event.reply(text=f'✅ 房间描述已设置：{description}', at_sender=False)
        else:
            await event.reply(text='❌ 设置房间描述失败', at_sender=False)

    async def _cmd_room_set_password(self, event: MessageEvent, group_id: str, args: list[str]):
        """设置房间密码。"""
        password = ' '.join(args).strip()
        if self.session_store.set_room_password(group_id, password):
            self._save_data()
            if password:
                await event.reply(text='✅ 房间密码已设置，玩家需要密码才能加入', at_sender=False)
            else:
                await event.reply(text='✅ 房间密码已清除，玩家可直接加入', at_sender=False)
        else:
            await event.reply(text='❌ 设置房间密码失败', at_sender=False)

    async def _cmd_room_set_max(self, event: MessageEvent, group_id: str, args: list[str]):
        """设置房间最大参与者数量。"""
        if not args:
            await event.reply(text='请设置最大参与者数量（0表示无限制）', at_sender=False)
            return

        try:
            max_participants = int(args[0])
            if max_participants < 0:
                await event.reply(text='最大参与者数量不能为负数', at_sender=False)
                return
        except ValueError:
            await event.reply(text='请输入有效的数字', at_sender=False)
            return

        if self.session_store.set_room_max_participants(group_id, max_participants):
            self._save_data()
            if max_participants > 0:
                await event.reply(text=f'✅ 房间最大参与者数量已设置为 {max_participants}', at_sender=False)
            else:
                await event.reply(text='✅ 房间最大参与者数量已设置为无限制', at_sender=False)
        else:
            await event.reply(text='❌ 设置最大参与者数量失败', at_sender=False)

    async def _cmd_room_delete(self, event: MessageEvent, group_id: str):
        """删除房间（管理员）。"""
        if not self.session_store.get_room(group_id):
            await event.reply(text='❌ 本群还没有房间', at_sender=False)
            return

        self.session_store.delete_room(group_id)
        self._save_data()
        await event.reply(text='✅ 房间已删除，所有玩家已离开房间', at_sender=False)
