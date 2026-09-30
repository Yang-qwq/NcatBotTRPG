# -*- coding: utf-8 -*-
"""提示词命令 Mixin。

``/trpg prompt`` 分两部分：
- 本团专属设定：``set <设定>`` / ``reset`` / （无参或 show）查看（群内需管理员）；
- 全局提示词模板：``list`` / ``switch <名>`` / ``show [名]``（需全局管理员）。
"""
from __future__ import annotations

from ncatbot.event.qq import MessageEvent
from ncatbot.utils.logger import get_log

_log = get_log('ncatbot_trpg')


class PromptCommandMixin:
    """``/trpg prompt *`` 的处理器。"""

    async def _cmd_prompt(self, event: MessageEvent, args: list[str]):
        """查看/设置本团专属主持人设定或管理提示词模板。"""
        scope, sid = self._scope_sid(event)
        if scope == 'group' and not await self._check_admin(event):
            return

        # 提示词模板管理命令（需要全局管理员权限）
        if args and args[0].lower() in ('list', 'switch', 'show'):
            if not await self._check_global_admin(event):
                await event.reply(text='❌ 提示词管理需要全局管理员权限', at_sender=False)
                return
            await self._handle_prompt_management(event, args)
            return

        # 本团专属设定管理
        reply_text = self._apply_prompt(scope, sid, args)
        self._save_data()
        _log.info(f'[{scope} {sid}] /trpg prompt -> {reply_text}')
        await event.reply(text=reply_text, at_sender=False)

    async def _handle_prompt_management(self, event: MessageEvent, args: list[str]):
        """处理提示词模板管理命令（list / switch / show）。"""
        sub = args[0].lower()
        if sub == 'list':
            await self._cmd_prompt_list(event)
        elif sub == 'switch':
            if len(args) < 2:
                await event.reply(text='用法：/trpg prompt switch <提示词名称>', at_sender=False)
                return
            await self._cmd_prompt_switch(event, args[1:])
        elif sub == 'show':
            prompt_name = args[1] if len(args) > 1 else None
            await self._cmd_prompt_show(event, prompt_name)
        else:
            await event.reply(
                text='未知的提示词管理命令，可用：list, switch, show',
                at_sender=False,
            )

    async def _cmd_prompt_list(self, event: MessageEvent):
        """列出可用的提示词模板。"""
        available_prompts = self.get_available_prompts()
        active_prompt = self.get_config('ActivePrompt', 'default')

        lines = ['📋 可用的提示词模板：']
        for name in available_prompts:
            status = '✅ 当前使用' if name == active_prompt else '  '
            lines.append(f'{status} {name}')

        lines.append('\n使用方法：')
        lines.append('/trpg prompt switch <提示词名称> - 切换提示词')
        lines.append('/trpg prompt show [提示词名称] - 查看提示词内容')

        await event.reply(text='\n'.join(lines), at_sender=False)

    async def _cmd_prompt_switch(self, event: MessageEvent, args: list[str]):
        """切换当前激活的提示词模板。"""
        prompt_name = args[0] if args else None
        if not prompt_name:
            await event.reply(text='请指定要切换的提示词名称', at_sender=False)
            return

        if self.set_active_prompt(prompt_name):
            await event.reply(text=f'✅ 已切换到提示词：{prompt_name}', at_sender=False)
        else:
            available_prompts = list(self.get_available_prompts().keys())
            await event.reply(
                text=f'❌ 提示词不存在。可用提示词：{", ".join(available_prompts)}',
                at_sender=False,
            )

    async def _cmd_prompt_show(self, event: MessageEvent, prompt_name: str | None = None):
        """显示提示词模板内容。"""
        available_prompts = self.get_available_prompts()

        if prompt_name:
            if prompt_name not in available_prompts:
                await event.reply(text=f'❌ 提示词不存在：{prompt_name}', at_sender=False)
                return
            lines = [f'📝 提示词：{prompt_name}', '', available_prompts[prompt_name]]
        else:
            active_prompt = self.get_config('ActivePrompt', 'default')
            lines = [
                f'📝 当前激活的提示词：{active_prompt}',
                '',
                available_prompts.get(active_prompt, '默认提示词'),
            ]

        await event.reply(text='\n'.join(lines), at_sender=False)
