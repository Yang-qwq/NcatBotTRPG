# -*- coding: utf-8 -*-
"""房间命令 Mixin。

房间与会话绑定：每群默认有且只有一个房间（惰性创建、默认 0 人），
无需创建命令。用户命令为一级命令 ``/trpg join|leave|participants``
（已移除 ``/trpg room`` 路由；房间状态由 ``/trpg status`` 统一展示）；
管理员命令为 ``/trpg-admin room-admin *``。
"""
from __future__ import annotations

from ncatbot.event.qq import GroupMessageEvent, MessageEvent


class RoomCommandMixin:
    """``/trpg join|leave|participants`` 与 ``/trpg-admin room-admin *`` 的处理器。"""

    # ------------------------------------------------------------------
    # 用户命令（一级）
    # ------------------------------------------------------------------

    async def _require_group(self, event: MessageEvent) -> bool:
        """房间命令仅限群聊；非群聊时回复提示并返回 False。"""
        if not isinstance(event, GroupMessageEvent):
            await event.reply(text='房间命令仅可在群聊中使用', at_sender=False)
            return False
        return True

    async def _cmd_room_join(self, event: MessageEvent):
        """加入房间。"""
        if not await self._require_group(event):
            return
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

        if self.session_store.join_room(group_id, user_id):
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
            await event.reply(text='❌ 加入房间失败，请检查房间状态或人数上限', at_sender=False)

    async def _cmd_room_leave(self, event: MessageEvent):
        """离开房间。"""
        if not await self._require_group(event):
            return
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

    async def _cmd_room_participants(self, event: MessageEvent):
        """查看房间参与者列表。"""
        if not await self._require_group(event):
            return
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
                text='请指定房间管理操作：set-description, set-max, delete',
                at_sender=False,
            )
            return

        sub = args[0].lower()
        sub_args = args[1:]

        if sub == 'set-description':
            await self._cmd_room_set_description(event, group_id, sub_args)
        elif sub == 'set-max':
            await self._cmd_room_set_max(event, group_id, sub_args)
        elif sub == 'delete':
            await self._cmd_room_delete(event, group_id)
        else:
            await event.reply(
                text='未知的房间管理操作，可用：set-description, set-max, delete',
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
