# -*- coding: utf-8 -*-
"""NcatBotTRPG 命令处理 Mixin（含三层 RBAC 权限体系）

命令通过 ``@registrar.qq.on_command`` 独立注册（群+私聊均可触发，方法内用
``isinstance(event, GroupMessageEvent)`` 判群）。管理员命令首行做权限校验。
"""
from __future__ import annotations

import re
import shlex
import time

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent, MessageEvent
from ncatbot.utils import get_config_manager
from ncatbot.utils.logger import get_log

from . import dice
from .session import ROOM_STATUS_TEXT

_log = get_log('ncatbot_trpg')

# 管理员权限点（RBAC，root 在 on_load 时自动授权）
ADMIN_PERMISSION = 'NcatBotTRPG.admin'
# 群角色缓存 TTL（秒），防止刷群成员列表 API
GROUP_ROLE_CACHE_TTL = 300

# 匹配 At 组件 CQ 码（如 [CQ:at,qq=888888]），用于解析 /trpgrbac 目标
_AT_CQ_PATTERN = re.compile(r'^\[CQ:at,qq=(\d+)\]$', re.IGNORECASE)

ROLL_HELP_TEXT = '''掷骰用法：
/trpg roll <骰式> [判定]
  /trpg roll 1d100 <= 50     判定成功率
  /trpg roll d20+5 >= 15     判定 DC
  /trpg roll d20 adv         优势（dis 为劣势）
  /trpg roll 2d6+3           普通投掷
  /trpg roll d100 bonus1     d100 奖励骰（penalty 为惩罚骰）
暗骰：/trpg rh <骰式>；团激活后亦可用 .r / .rh
注意：不支持重复投掷（N#），一次行动只能掷一次'''

USER_HELP_TEXT = '''NcatBotTRPG 用户命令帮助：

# 🏠 房间管理（群聊专用，每群默认有一个房间，无需创建）
/trpg room join [密码] - 加入当前群房间
/trpg room leave - 离开当前群房间  
/trpg room status - 查看当前群房间状态
/trpg room participants - 查看房间参与者列表

# 🎭 玩家状态管理（群聊专用）
/trpg away [原因] - 暂时离开游戏
/trpg offline [原因] - 长时间离线
/trpg back - 重新加入游戏
/trpg ai-control [原因] - 请求AI托管角色
/trpg status - 查看当前团状态和玩家状态

# 🎭 跑团控制
/trpg start [设定] - 开启本会话的 AI 跑团（群内需管理员；创建房间可管理参与者）
/trpg act <行动> - 向 AI 主持提交一次行动
/trpg prompt [set <设定>|show|reset] - 查看/设置本团专属设定（群内需管理员）
/trpg prompt list - 查看可用的提示词模板
/trpg prompt switch <提示词名> - 切换当前使用的提示词
/trpg prompt show [提示词名] - 显示提示词内容
/trpg reset - 清空剧情历史，保留设定（群内需管理员）
/trpg stop - 结束本会话的团（群内需管理员）
/trpg help - 显示此帮助

# 🎲 掷骰系统
/trpg roll <骰式> [判定] - 公开掷骰/检定
/trpg rh <骰式> [判定] - 暗骰（结果私聊发送）

局内关键词：团激活后可用 .r / .rh 快速掷骰/暗骰

💡 提示：使用房间系统可以让主持人知道所有参与者，提供更好的游戏体验！'''

ADMIN_HELP_TEXT = '''NcatBotTRPG 管理员命令帮助：

/trpg-admin stop [group:<id>|user:<id>] - 结束指定会话的团
/trpg-admin reset [group:<id>|user:<id>] - 清空指定会话的剧情历史
/trpg-admin prompt <set <设定>|show|reset> [group:<id>|user:<id>] - 管理专属设定
/trpg-admin room-admin <set-description|set-password|set-max|delete> [参数] - 房间管理
/trpg-admin help - 显示此帮助

不指定目标时作用于当前会话；仅全局管理员或本群群主/群管理可用。'''

TRPGRBAC_HELP_TEXT = '''NcatBotTRPG 全局管理员管理命令帮助：

/trpgrbac grant <qq> - 授予全局管理员权限（支持 @ 成员或纯数字）
/trpgrbac revoke <qq> - 撤销全局管理员权限
/trpgrbac list - 查看全局管理员列表
/trpgrbac help - 显示此帮助

注意：仅限全局管理员（含机器人 owner）使用，群主/群管理身份不可越权调用'''


def _parse_command(event: MessageEvent) -> list | None:
    """解析消息为命令参数列表（shlex），失败时返回 None。

    :param event: 消息事件
    :return: 参数列表；解析失败返回 None
    """
    replaced_message = event.raw_message.replace('\\n', '\n')
    try:
        return shlex.split(replaced_message)
    except ValueError:
        _log.warning(f'命令解析失败（引号不匹配等）：{event.raw_message[:100]}')
        return None


class NcatBotTRPGCommandMixin:
    """命令处理逻辑 Mixin，供 NcatBotTRPGPlugin 继承使用。"""

    # ------------------------------------------------------------------
    # 权限体系（三层：RBAC 全局管理员 > 本群群主/群管理自动放行 > 默认拒绝）
    # ------------------------------------------------------------------

    async def _check_admin(self, event: MessageEvent) -> bool:
        """校验管理员权限：RBAC 全局管理员 或 本群群主/群管理（受开关控制）。

        :param event: 消息事件
        :return: 是否拥有权限
        """
        if self.check_permission(str(event.user_id), ADMIN_PERMISSION):
            _log.debug(f'[{event.user_id}] 管理员校验通过（全局 RBAC）')
            return True
        if await self._is_group_privileged(event):
            _log.debug(f'[{event.user_id}] 管理员校验通过（本群群主/群管理）')
            return True
        _log.warning(f'[{event.user_id}] 管理员校验失败，已拒绝命令')
        await event.reply(
            text='权限不足：该命令需要全局管理员权限（/trpgrbac 授权）或本群群主/群管理身份',
            at_sender=False,
        )
        return False

    async def _check_global_admin(self, event: MessageEvent) -> bool:
        """校验全局管理员权限（仅 RBAC，跨群生效）。

        :param event: 消息事件
        :return: 是否拥有权限
        """
        if self.check_permission(str(event.user_id), ADMIN_PERMISSION):
            return True
        _log.warning(f'[{event.user_id}] 全局管理员校验失败，已拒绝命令')
        await event.reply(
            text='权限不足：该命令需要全局管理员权限（机器人 owner 或 /trpgrbac 授权）',
            at_sender=False,
        )
        return False

    async def _is_group_privileged(self, event: MessageEvent) -> bool:
        """校验是否为本群群主或群管理员（受 EnableGroupOwnerAutoAuth 开关控制）。

        群角色列表带 TTL 缓存，避免每条命令都调用群成员列表 API。

        :param event: 消息事件
        :return: 是否拥有本群管理权限
        """
        if not isinstance(event, GroupMessageEvent):
            return False
        if not self.get_config('EnableGroupOwnerAutoAuth', True):
            _log.debug('群主/群管理自动放行已关闭，跳过群角色校验')
            return False
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        cache = getattr(self, '_group_role_cache', None)
        if cache is None:
            cache = self._group_role_cache = {}
        entry = cache.get(group_id)

        now = time.time()
        if not entry or now > entry['expire']:
            try:
                members = await self.api.qq.query.get_group_member_list(event.group_id)
            except Exception as e:
                _log.warning(f'获取群成员列表失败: {e}')
                return False
            roles = {}
            for member in members:
                uid = getattr(member, 'user_id', None)
                role = getattr(member, 'role', None)
                if uid is not None:
                    roles[str(uid)] = role
            entry = {'roles': roles, 'expire': now + GROUP_ROLE_CACHE_TTL}
            cache[group_id] = entry
            _log.debug(f'已刷新群({group_id})角色缓存，共 {len(roles)} 人')

        role = entry['roles'].get(user_id)
        _log.debug(f'群({group_id})用户({user_id})角色: {role!r}')
        return role in ('owner', 'admin')

    @staticmethod
    def _resolve_target_qq(token: str) -> str | None:
        """解析 /trpgrbac 目标 QQ，兼容纯数字与 At 组件（CQ 码）。

        :param token: 命令参数中的目标 QQ（如 "888888" 或 "[CQ:at,qq=888888]"）
        :return: 归一化 QQ 号；无法解析时返回 None
        """
        token = token.strip()
        if token.isdigit():
            return token
        match = _AT_CQ_PATTERN.match(token)
        if match:
            return match.group(1)
        return None

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _scope_sid(event: MessageEvent) -> tuple[str, str]:
        """返回当前事件的会话作用域与 ID。

        :param event: 消息事件
        :return: (``group``/``user``, 会话 ID 字符串)
        """
        if isinstance(event, GroupMessageEvent):
            return 'group', str(event.group_id)
        return 'user', str(event.user_id)

    @staticmethod
    def _split_target(args: list[str]) -> tuple[str | None, list[str]]:
        """从参数列表中提取 group:<id> / user:<id> 目标。

        :param args: 命令参数
        :return: (目标 token 或 None, 去掉目标后的其余参数)
        """
        target = None
        rest = []
        for item in args:
            if target is None and item.startswith(('group:', 'user:')):
                target = item
            else:
                rest.append(item)
        if target is not None:
            _log.debug(f'管理命令目标: {target}')
        return target, rest

    def _resolve_scope_target(self, event: MessageEvent,
                              target: str | None) -> tuple[str | None, str | None]:
        """解析管理命令的目标会话；target 为空时使用当前会话。

        :param event: 消息事件
        :param target: ``group:<id>`` / ``user:<id>`` 或 None
        :return: (scope, sid)；非法目标返回 (None, None)
        """
        if target is None:
            return self._scope_sid(event)
        scope, _, raw = target.partition(':')
        if scope in ('group', 'user') and raw.isdigit():
            return scope, raw
        _log.warning(f'非法的管理命令目标: {target!r}')
        return None, None

    def _dice_keyword_tip(self) -> str:
        """返回局内掷骰关键词提示文本。"""
        if self.get_config('EnableInGameDiceKeyword', True):
            keyword = self.get_config('InGameDiceKeyword', '.r') or '.r'
            return f'局内关键词 {keyword} / {keyword}h'
        return '/trpg roll'

    def _get_int_config(self, key: str, default: int) -> int:
        """读取整型配置，兼容字符串（含 `|` 复合值）与非法值。

        :param key: 配置键
        :param default: 缺省值（解析失败时返回）
        :return: 整型配置值
        """
        value = self.get_config(key, default)
        if isinstance(value, str):
            value = value.split('|')[-1]
        try:
            return int(value)
        except (TypeError, ValueError):
            _log.warning(f'配置 {key}={value!r} 无法解析为整数，使用默认值 {default}')
            return default

    # ------------------------------------------------------------------
    # /trpg 用户与团管理命令
    # ------------------------------------------------------------------

    @registrar.qq.on_command('/trpg')
    async def on_trpg(self, event: MessageEvent):
        """处理 /trpg 命令。

        :param event: 消息事件
        :return: None
        """
        command = _parse_command(event)
        if command is None:
            await event.reply(text='命令格式错误，请检查引号是否匹配', at_sender=False)
            return
        if len(command) == 1:
            await event.reply(text=USER_HELP_TEXT, at_sender=False)
            return

        sub = command[1].lower()
        args = command[2:]
        _log.debug(f'[{event.user_id}] /trpg {sub}（参数: {args}）')

        if sub == 'roll':
            await self._cmd_roll(event, args, hidden=False)
        elif sub == 'rh':
            await self._cmd_roll(event, args, hidden=True)
        elif sub == 'start':
            await self._cmd_start(event, args)
        elif sub == 'act':
            await self._cmd_act(event, args)
        elif sub == 'status':
            await self._cmd_status(event)
        elif sub == 'prompt':
            await self._cmd_prompt(event, args)
        elif sub == 'reset':
            await self._cmd_reset(event)
        elif sub == 'stop':
            await self._cmd_stop(event)
        elif sub == 'room':
            await self._cmd_room(event, args)
        elif sub == 'away':
            await self._cmd_away(event, args)
        elif sub == 'offline':
            await self._cmd_offline(event, args)
        elif sub == 'back':
            await self._cmd_back(event)
        elif sub == 'ai-control':
            await self._cmd_ai_control(event, args)
        elif sub == 'help':
            await event.reply(text=USER_HELP_TEXT, at_sender=False)
        else:
            _log.warning(f'[{event.user_id}] 未知 /trpg 子命令: {sub!r}')
            await event.reply(text='未知命令，请使用 /trpg help 查看帮助', at_sender=False)

    async def _cmd_roll(self, event: MessageEvent, args: list[str], hidden: bool):
        """执行掷骰命令。

        :param event: 消息事件
        :param args: 骰式参数
        :param hidden: 是否暗骰
        """
        expr_text = ' '.join(args).strip()
        if not expr_text:
            _log.debug(f'[{event.user_id}] /trpg {"rh" if hidden else "roll"} 缺少骰式，返回帮助')
            await event.reply(text=ROLL_HELP_TEXT, at_sender=False)
            return
        await self._do_roll(event, expr_text, hidden)

    async def _do_roll(self, event: MessageEvent, expr_text: str, hidden: bool):
        """解析并执行掷骰，发送结果（供命令与局内关键词复用）。

        :param event: 消息事件
        :param expr_text: 骰式文本
        :param hidden: 是否暗骰
        """
        # 团激活时限制：同一位玩家在结果被消费前不能重复投掷
        scope, sid = self._scope_sid(event)
        session_active = self.session_store.is_active(scope, sid)
        if session_active and self.session_store.has_pending_roll(scope, sid, event.user_id):
            _log.info(f'[{scope} {sid}] 用户({event.user_id})已有未结算掷骰，拒绝重复投掷')
            await event.reply(
                text='⏳ 你还有一次未结算的掷骰结果，请先提交行动'
                     '（@我 或 /trpg act <行动>）后再掷骰。',
                at_sender=False,
            )
            return

        try:
            parsed = dice.parse(
                expr_text,
                max_count=self._get_int_config('MaxDiceCount', dice.DEFAULT_MAX_DICE_COUNT),
                max_sides=self._get_int_config('MaxDiceSides', dice.DEFAULT_MAX_DICE_SIDES),
            )
        except (dice.DiceError, ValueError) as e:
            _log.warning(f'[{event.user_id}] 掷骰解析失败: {expr_text!r} - {e}')
            await event.reply(text=f'掷骰错误：{e}', at_sender=False)
            return

        outcome = dice.roll(parsed, enable_critical=self.get_config('EnableCriticalDice', True))
        message = dice.format_outcome(outcome)
        _log.info(f'[{event.user_id}] 掷骰 {expr_text} -> {message.splitlines()[0]}')

        # 团激活时缓存掷骰结果，待玩家下次行动经 system prompt 交给主持人
        if session_active:
            summary = ' '.join(line.strip() for line in message.splitlines() if line.strip())
            name = event.sender.nickname or str(event.user_id)
            kind = '暗骰' if hidden else '掷骰'
            note = '（暗骰，请勿向其他玩家透露具体数值）' if hidden else ''
            self.session_store.add_pending_roll(
                scope, sid, event.user_id, f'{name}({event.user_id}) {kind}：{summary}{note}')
            self._save_data()

        hint = '\n（主持人将在你的下一次行动中参考此掷骰结果）' if session_active else ''

        if not hidden:
            await event.reply(text=message + hint, at_sender=False)
            return

        # 暗骰：群聊中私聊发起者，私聊中直接回复
        if isinstance(event, GroupMessageEvent):
            try:
                await self.api.qq.post_private_msg(event.user_id, text=message + hint)
                _log.debug(f'[{event.user_id}] 暗骰结果已私聊发送')
                await event.reply(text='🤫 暗骰结果已私聊发送', at_sender=False)
            except Exception as e:
                _log.error(f'暗骰私聊发送失败: {e}')
                await event.reply(text='暗骰私聊发送失败（可能未添加好友）', at_sender=False)
        else:
            await event.reply(text=message + hint, at_sender=False)

    async def _cmd_start(self, event: MessageEvent, args: list[str]):
        """开启本会话的 AI 跑团。"""
        scope, sid = self._scope_sid(event)
        if scope == 'group' and not await self._check_admin(event):
            return
            
        # 群聊：房间与会话绑定，每群默认有一个房间（无需创建、0 人起步）
        room = self.session_store.get_or_create_room(sid) if scope == 'group' else None
        if room is not None:
            if room['status'] not in ('preparing', 'finished'):
                await event.reply(
                    text='❌ 房间不是准备状态，无法开始跑团',
                    at_sender=False,
                )
                return

            # 发起人自动加入房间（无密码房间；有密码需玩家自行 /trpg room join）
            if not room['password']:
                self.session_store.join_room(sid, str(event.user_id))

            # 开始房间
            if not self.session_store.start_room(sid):
                await event.reply(
                    text='❌ 开始房间失败',
                    at_sender=False,
                )
                return
                
        if not self.get_config('IsConfigured'):
            _log.warning(f'[{scope} {sid}] 开团被拒：插件未配置')
            await event.reply(
                text='AI 主持尚未配置：请在 config.yaml 的 plugin.plugin_configs.NcatBotTRPG '
                     '中设置 ApiKey / Model / BaseUrl，并将 IsConfigured 置为 true',
                at_sender=False,
            )
            return
        existing = self.session_store.get(scope, sid)
        if existing and existing.get('active'):
            _log.debug(f'[{scope} {sid}] 开团被拒：已有进行中的团')
            await event.reply(
                text='本会话已在进行中的团；如需重开请先 /trpg reset 或 /trpg stop',
                at_sender=False,
            )
            return

        prompt = ' '.join(args).strip()

        self.session_store.create(scope, sid, prompt=prompt)
        self._save_data()
        _log.info(f'[{scope} {sid}] 已开启跑团')
        
        # 群聊中若存在房间则显示参与者信息
        if room is not None:
            participant_names = self.session_store.participant_names(sid)
            await event.reply(
                text=f'✅ 已开启跑团！\n'
                     f'👥 参与者：{", ".join(participant_names)}\n'
                     f'用 @我 或 /trpg act <行动> 开始行动；'
                     f'掷骰用 /trpg roll 或 {self._dice_keyword_tip()}',
                at_sender=False,
            )
        else:
            await event.reply(
                text=f'✅ 已开启跑团。用 @我 或 /trpg act <行动> 开始行动；'
                     f'掷骰用 /trpg roll 或 {self._dice_keyword_tip()}',
                at_sender=False,
            )

    async def _cmd_act(self, event: MessageEvent, args: list[str]):
        """向 AI 主持提交一次行动。"""
        scope, sid = self._scope_sid(event)
        text = ' '.join(args).strip()
        if not self.session_store.is_active(scope, sid):
            _log.debug(f'[{scope} {sid}] /trpg act 被拒：无进行中的团')
            await event.reply(text='当前没有进行中的团，请先 /trpg start [设定]', at_sender=False)
            return
        if not text:
            _log.debug(f'[{scope} {sid}] /trpg act 缺少行动文本')
            await event.reply(text='请提供行动，例如 /trpg act 我推开大门', at_sender=False)
            return
        await self._handle_action(event, text, scope, sid)

    async def _cmd_status(self, event: MessageEvent):
        """查看当前团状态（群聊同时展示房间与玩家状态）。"""
        scope, sid = self._scope_sid(event)
        lines: list[str] = []

        # 群聊：房间信息与玩家状态
        if scope == 'group':
            room = self.session_store.room_view(sid)
            lines.append(f'🏠 房间信息：{room["name"]}')
            lines.append(f'📊 状态：{ROOM_STATUS_TEXT.get(room["status"], "未知")}')
            lines.append(f'👥 总参与者：{len(room["participants"])}人')

            if room['status'] == 'running':
                active_players = self.session_store.get_active_players(sid)
                lines.append(f'🎮 活跃玩家：{len(active_players)}人')
                player_status_info = self.session_store.build_player_status_info(sid)
                if player_status_info:
                    lines.append(player_status_info)
                requesting_players = self.session_store.has_requests_for_ai_control(sid)
                if requesting_players:
                    lines.append(f'⚠️ AI托管请求：{len(requesting_players)}人')

        # 团会话状态
        session = self.session_store.get(scope, sid)
        if session is None:
            _log.debug(f'[{scope} {sid}] /trpg status：无跑团记录')
            lines.append('📖 本会话暂无跑团记录，使用 /trpg start 开启')
        else:
            _log.debug(f'[{scope} {sid}] /trpg status：active={session.get("active")}')
            state = '进行中' if session.get('active') else '已结束'
            prompt = (session.get('prompt') or '').strip()
            prompt_desc = (prompt[:30] + '…') if len(prompt) > 30 else (prompt or '（默认主持人设定）')
            lines.append(f'📖 本会话跑团状态：{state}')
            lines.append(f'开始时间：{session.get("started_at", "未知")}')
            lines.append(f'专属设定：{prompt_desc}')
            lines.append(f'已记录剧情消息：{len(session.get("history", []))} 条')

        await event.reply(text='\n'.join(lines), at_sender=False)

    async def _cmd_reset(self, event: MessageEvent):
        """清空当前会话的剧情历史（保留设定）。"""
        scope, sid = self._scope_sid(event)
        if scope == 'group' and not await self._check_admin(event):
            return
        reply_text = self._apply_reset(scope, sid)
        self._save_data()
        _log.info(f'[{scope} {sid}] /trpg reset -> {reply_text}')
        await event.reply(text=reply_text, at_sender=False)

    async def _cmd_stop(self, event: MessageEvent):
        """结束当前会话的团。"""
        scope, sid = self._scope_sid(event)
        if scope == 'group' and not await self._check_admin(event):
            return
        reply_text = self._apply_stop(scope, sid)
        self._save_data()
        _log.info(f'[{scope} {sid}] /trpg stop -> {reply_text}')
        await event.reply(text=reply_text, at_sender=False)

    # 可复用的内部操作（返回回复文本，供 /trpg 与 /trpg-admin 共用）

    def _apply_prompt(self, scope: str, sid: str, args: list[str]) -> str:
        """应用 prompt 子命令并返回回复文本。"""
        session = self.session_store.get(scope, sid)
        if session is None:
            return '本会话暂无跑团记录，请先 /trpg start'
        sub = args[0].lower() if args else 'show'
        _log.debug(f'[{scope} {sid}] prompt 子命令: {sub}')
        if sub == 'set':
            text = ' '.join(args[1:]).strip()
            if not text:
                return '用法：/trpg prompt set <本团专属设定>'
            self.session_store.update(scope, sid, prompt=text)
            _log.info(f'[{scope} {sid}] 已更新专属设定（{len(text)} 字）')
            return '✅ 已更新本团专属设定（不影响已有剧情历史）'
        if sub == 'reset':
            self.session_store.update(scope, sid, prompt='')
            _log.info(f'[{scope} {sid}] 已重置专属设定')
            return '✅ 已恢复为默认主持人设定'
        current = (session.get('prompt') or '').strip()
        if current:
            return f'📝 本团专属设定：\n{current}'
        return '本团使用默认主持人设定。用法：/trpg prompt set <设定>'

    def _apply_reset(self, scope: str, sid: str) -> str:
        """清空会话历史并返回回复文本。"""
        if self.session_store.get(scope, sid) is None:
            return '本会话暂无跑团记录'
        self.session_store.clear_history(scope, sid)
        self.session_store.clear_pending_rolls(scope, sid)
        _log.info(f'[{scope} {sid}] 已清空剧情历史')
        return '✅ 已清空剧情历史，保留本团设定与激活状态'

    def _apply_stop(self, scope: str, sid: str) -> str:
        """结束会话的团并返回回复文本。"""
        session = self.session_store.get(scope, sid)
        if session is None or not session.get('active'):
            return '当前没有进行中的团'
        self.session_store.update(scope, sid, active=False)
        if scope == 'group':
            self.session_store.finish_room(sid)
        _log.info(f'[{scope} {sid}] 已结束团会话')
        return '🛑 已结束本会话的团（剧情记录保留，可用 /trpg reset 后重新 start）'

    # ------------------------------------------------------------------
    # /trpg-admin 跨会话管理命令
    # ------------------------------------------------------------------

    @registrar.qq.on_command('/trpg-admin')
    async def on_trpg_admin(self, event: MessageEvent):
        """处理 /trpg-admin 管理员命令（首行权限校验）。"""
        if not await self._check_admin(event):
            return
        command = _parse_command(event)
        if command is None:
            await event.reply(text='命令格式错误，请检查引号是否匹配', at_sender=False)
            return
        if len(command) < 2 or command[1].lower() == 'help':
            await event.reply(text=ADMIN_HELP_TEXT, at_sender=False)
            return

        sub = command[1].lower()
        target, rest = self._split_target(command[2:])
        _log.debug(f'[{event.user_id}] /trpg-admin {sub}（target={target!r}）')
        scope, sid = self._resolve_scope_target(event, target)
        if scope is None:
            await event.reply(text='目标格式错误，请使用 group:<id> 或 user:<id>', at_sender=False)
            return

        if sub == 'stop':
            reply_text = self._apply_stop(scope, sid)
        elif sub == 'reset':
            reply_text = self._apply_reset(scope, sid)
        elif sub == 'prompt':
            reply_text = self._apply_prompt(scope, sid, rest)
        elif sub == 'room-admin':
            if scope != 'group':
                await event.reply(
                    text='房间管理需在群内或使用 group:<id> 指定目标',
                    at_sender=False,
                )
                return
            await self._cmd_room_admin(event, sid, rest)
            return
        else:
            _log.warning(f'[{event.user_id}] 未知 /trpg-admin 子命令: {sub!r}')
            await event.reply(text='未知管理员命令，请使用 /trpg-admin help 查看帮助', at_sender=False)
            return

        self._save_data()
        _log.info(f'[{event.user_id}] /trpg-admin {sub} {scope}:{sid} -> {reply_text}')
        await event.reply(text=reply_text, at_sender=False)

    # ------------------------------------------------------------------
    # /trpgrbac 全局管理员管理命令（严格 RBAC，防群主/群管理提权）
    # ------------------------------------------------------------------

    @registrar.qq.on_command('/trpgrbac')
    async def on_trpgrbac(self, event: MessageEvent):
        """处理 /trpgrbac 命令，管理全局管理员权限（仅全局管理员/root 可用）。"""
        if not await self._check_global_admin(event):
            return

        command = _parse_command(event)
        if command is None:
            await event.reply(text='命令格式错误，请检查引号是否匹配', at_sender=False)
            return
        params = command[1:] if len(command) > 1 else []
        if not params or params[0] == 'help':
            await event.reply(text=TRPGRBAC_HELP_TEXT, at_sender=False)
            return

        subcommand = params[0]
        _log.debug(f'[{event.user_id}] /trpgrbac {subcommand}')

        if subcommand == 'grant':
            if len(params) != 2:
                await event.reply(text='用法：/trpgrbac grant <qq>（支持 @ 成员或纯数字）',
                                  at_sender=False)
                return
            target = self._resolve_target_qq(params[1])
            if target is None:
                _log.warning(f'[{event.user_id}] /trpgrbac grant 非法目标: {params[1]!r}')
                await event.reply(text='QQ 号必须是纯数字或 @ 成员', at_sender=False)
                return
            if not self.rbac:
                _log.error('RBAC 服务不可用，无法授权')
                await event.reply(text='RBAC 服务不可用，无法授权', at_sender=False)
                return
            if self.check_permission(target, ADMIN_PERMISSION):
                _log.debug(f'[{target}] 已是全局管理员，跳过重复授予')
                await event.reply(text=f'用户 {target} 已是全局管理员', at_sender=False)
                return
            try:
                self.rbac.add_user(target, exist_ok=True)
                self.rbac.grant('user', target, ADMIN_PERMISSION)
            except Exception as e:
                _log.error(f'授予全局管理员权限失败: {e}')
                await event.reply(text=f'授予全局管理员权限失败：{e}', at_sender=False)
                return
            _log.info(f'{event.user_id} 通过 /trpgrbac 授予 {target} 全局管理员权限')
            await event.reply(text=f'✅ 已授予 {target} 全局管理员权限', at_sender=False)

        elif subcommand == 'revoke':
            if len(params) != 2:
                await event.reply(text='用法：/trpgrbac revoke <qq>（支持 @ 成员或纯数字）',
                                  at_sender=False)
                return
            target = self._resolve_target_qq(params[1])
            if target is None:
                _log.warning(f'[{event.user_id}] /trpgrbac revoke 非法目标: {params[1]!r}')
                await event.reply(text='QQ 号必须是纯数字或 @ 成员', at_sender=False)
                return
            if not self.rbac:
                _log.error('RBAC 服务不可用，无法撤销')
                await event.reply(text='RBAC 服务不可用，无法撤销', at_sender=False)
                return
            if not self.check_permission(target, ADMIN_PERMISSION):
                _log.debug(f'[{target}] 不是全局管理员，跳过撤销')
                await event.reply(text=f'用户 {target} 不是全局管理员', at_sender=False)
                return
            try:
                self.rbac.revoke('user', target, ADMIN_PERMISSION)
            except Exception as e:
                _log.error(f'撤销全局管理员权限失败: {e}')
                await event.reply(text=f'撤销全局管理员权限失败：{e}', at_sender=False)
                return
            _log.info(f'{event.user_id} 通过 /trpgrbac 撤销 {target} 全局管理员权限')
            await event.reply(text=f'✅ 已撤销 {target} 的全局管理员权限', at_sender=False)

        elif subcommand == 'list':
            if not self.rbac:
                _log.error('RBAC 服务不可用，无法列出管理员')
                await event.reply(text='RBAC 服务不可用', at_sender=False)
                return
            root = get_config_manager().config.root
            admins = [
                uid for uid in self.rbac.users
                if self.check_permission(uid, ADMIN_PERMISSION)
            ]
            _log.info(f'列出全局管理员 {len(admins)} 人')
            if not admins:
                await event.reply(
                    text='当前暂无全局管理员（root 将在下次启动时自动恢复授权）',
                    at_sender=False,
                )
                return
            lines = [f'🔐 全局管理员列表（共 {len(admins)} 人）：', '']
            for index, uid in enumerate(admins, 1):
                root_mark = '（root）' if uid == root else ''
                lines.append(f'  {index}. {uid}{root_mark}')
            await event.reply(text='\n'.join(lines), at_sender=False)

        else:
            _log.warning(f'[{event.user_id}] 未知 /trpgrbac 子命令: {subcommand!r}')
            await event.reply(text=TRPGRBAC_HELP_TEXT, at_sender=False)
