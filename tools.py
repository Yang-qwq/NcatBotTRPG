# -*- coding: utf-8 -*-
"""NcatBotTRPG 工具层（Function Calling）

设计参考（仅借鉴思路，未复制代码）：
- diceframe（AGPL-3.0）：工具 schema 只描述意图、系统裁定数值、参数钳制
- TRPG-AI-DM（MIT）：工具注册表 + 分发、ReAct 错误回灌、可见性
- SillyTavern（AGPL-3.0）：运行时可见性与按需工具

工具分三类：
- 检定：``request_check`` —— 把「骰式 + 目标值」下发给玩家，玩家自行掷骰
- 只读：``get_state`` / ``search_lore``
- 受控写：``change_hp`` / ``change_attribute`` / ``manage_inventory`` /
  ``update_scene`` / ``record_event``（受 ``EnableStateMutationTools`` 门控）

所有执行器返回 ``(payload_json, terminal)``：``terminal=True`` 表示调用后应结束
本回合（如已下发检定）。未知工具/非法参数返回 error payload 供模型自纠，不抛异常。
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ncatbot.types import MessageArray
from ncatbot.utils.logger import get_log

from . import dice, lore
from .session import player_label
from .state import StateError, StateStore

_log = get_log('ncatbot_trpg')

# 难度表达式：<= / >= / < / > / == 后接整数
_DIFFICULTY_RE = re.compile(r'^(<=|>=|<|>|==)\s*(-?\d+)$')

# 场景字段 -> 工具参数名
_SCENE_PARAMS = ('location', 'time', 'weather', 'atmosphere')


@dataclass
class ToolContext:
    """一次工具调用所处的上下文。"""

    plugin: Any
    event: Any
    scope: str
    sid: str
    state: StateStore
    session_store: Any

    @property
    def user_id(self) -> str:
        """当前消息发送者 QQ。"""
        return str(self.event.user_id)

    @property
    def is_group(self) -> bool:
        """是否群聊上下文。"""
        return self.scope == 'group'


def _payload(status: str, message: str = '', data: Any = None) -> str:
    """构造统一的工具返回 JSON。"""
    result = {'status': status, 'message': message}
    if data is not None:
        result['data'] = data
    return json.dumps(result, ensure_ascii=False)


def _status(payload: str) -> str:
    """读取 payload 的 status（解析失败返回空串）。"""
    try:
        return str(json.loads(payload).get('status', ''))
    except (json.JSONDecodeError, TypeError, ValueError):
        return ''


def _schema(name: str, description: str, properties: dict,
            required: Optional[List[str]] = None) -> dict:
    """构造 OpenAI function 工具 schema。"""
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': description,
            'parameters': {
                'type': 'object',
                'additionalProperties': False,
                'properties': properties,
                'required': required or [],
            },
        },
    }


# 只读工具（始终可用）
_READ_TOOLS = [
    _schema(
        'request_check',
        '当某玩家（或 NPC）的行动结果不确定且有意义时，向其下发一次检定：'
        '你给出骰式与目标值（判定线），玩家会自行掷骰并给出结果。'
        '**必须提供 difficulty 目标值**，否则系统无法裁定成败。'
        '严禁自行编造骰值或成败。调用后本回合立即结束，等待玩家掷骰。',
        {
            'target': {'type': 'string', 'description': '受检者：玩家号/展示名，或 NPC/实体名'},
            'expression': {'type': 'string', 'description': '骰式，如 1d100 / d20 / d20+3'},
            'difficulty': {'type': 'string',
                           'description': "判定目标值，必填，如 '<=50' / '>=15' / '<=13'"},
            'reason': {'type': 'string', 'description': "检定名称，如 '侦查检定'"},
            'hidden': {'type': 'boolean', 'description': '是否暗骰（仅该玩家可见）', 'default': False},
        },
        required=['target', 'expression', 'difficulty', 'reason'],
    ),
    _schema(
        'get_state',
        '按需读取角色/队伍/场景状态明细（短可读）。用于你已忘记或需要精确数值时。',
        {
            'target': {'type': 'string', 'description': "self / party / scene / 具体玩家号"},
            'fields': {'type': 'array', 'items': {'type': 'string'},
                       'description': '可选：仅关注这些字段'},
        },
    ),
    _schema(
        'search_lore',
        '检索世界书/设定资料（当前版本预留，可能返回空）。',
        {
            'query': {'type': 'string', 'description': '检索词'},
            'top_k': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'description': '返回条数'},
        },
        required=['query'],
    ),
]

# 受控写工具（受 EnableStateMutationTools 门控）
_WRITE_TOOLS = [
    _schema(
        'change_hp',
        '修改某角色的生命值。伤害必须由系统已结算的检定背书（先 request_check、'
        '玩家掷骰成功结算后才能造成伤害）；治疗单次不超过上限的一半。',
        {
            'target': {'type': 'string', 'description': '目标玩家号'},
            'delta': {'type': 'integer', 'minimum': -9999, 'maximum': 9999,
                      'description': '变化量：负数伤害，正数治疗'},
            'reason': {'type': 'string', 'description': '变更原因'},
        },
        required=['target', 'delta', 'reason'],
    ),
    _schema(
        'change_attribute',
        '修改某角色（非生命）属性，如理智/魔力/力量等。字段须为已登记属性。',
        {
            'target': {'type': 'string', 'description': '目标玩家号'},
            'field': {'type': 'string', 'description': '属性 ID，如 san / mp / str'},
            'op': {'type': 'string', 'enum': ['delta', 'set'], 'description': '增减或设定'},
            'value': {'type': 'integer', 'description': '数值'},
            'reason': {'type': 'string', 'description': '变更原因'},
        },
        required=['target', 'field', 'op', 'value', 'reason'],
    ),
    _schema(
        'manage_inventory',
        '增删/使用角色物品。物品须已在实体字典中登记。',
        {
            'target': {'type': 'string', 'description': '目标玩家号'},
            'action': {'type': 'string', 'enum': ['add', 'remove', 'use'], 'description': '操作'},
            'item': {'type': 'string', 'description': '物品 ID，如 potion_heal'},
            'qty': {'type': 'integer', 'minimum': 1, 'maximum': 99, 'description': '数量'},
            'reason': {'type': 'string', 'description': '操作原因'},
        },
        required=['target', 'action', 'item', 'reason'],
    ),
    _schema(
        'update_scene',
        '更新当前场景（位置/时间/天气/氛围）。只提供需要变更的字段。',
        {
            'location': {'type': 'string', 'description': '位置'},
            'time': {'type': 'string', 'description': '时间'},
            'weather': {'type': 'string', 'description': '天气'},
            'atmosphere': {'type': 'string', 'description': '氛围'},
        },
    ),
    _schema(
        'record_event',
        '记录一条剧情事件到事件账本（供审计与回顾）。visibility=gm 表示仅主持人可见。',
        {
            'text': {'type': 'string', 'description': '事件描述'},
            'visibility': {'type': 'string', 'enum': ['public', 'gm'], 'description': '可见性'},
        },
        required=['text'],
    ),
]


def build_tools(enable_mutation: bool) -> List[dict]:
    """构造暴露给模型的工具列表。

    :param enable_mutation: 是否包含受控写工具
    :return: OpenAI 工具 schema 列表
    """
    tools = list(_READ_TOOLS)
    if enable_mutation:
        tools.extend(_WRITE_TOOLS)
    return tools


async def execute(name: str, args: dict, ctx: ToolContext) -> Tuple[str, bool]:
    """执行一次工具调用。

    :param name: 工具名
    :param args: 参数
    :param ctx: 调用上下文
    :return: ``(payload_json, terminal)``
    """
    try:
        if name == 'request_check':
            # 仅下发成功才终止本回合；缺目标值等错误需回灌给模型重试
            result = await _request_check(args, ctx)
            return result, _status(result) == 'ok'
        if name == 'get_state':
            return _get_state(args, ctx), False
        if name == 'search_lore':
            return _search_lore(args, ctx), False
        if name == 'change_hp':
            return _change_hp(args, ctx), False
        if name == 'change_attribute':
            return _change_attribute(args, ctx), False
        if name == 'manage_inventory':
            return _manage_inventory(args, ctx), False
        if name == 'update_scene':
            return _update_scene(args, ctx), False
        if name == 'record_event':
            return _record_event(args, ctx), False
    except StateError as e:
        _log.warning(f'工具 {name} 状态操作失败: {e}')
        return _payload('error', str(e)), False
    except Exception as e:  # noqa: BLE001 - 工具错误回灌给模型自纠
        _log.error(f'工具 {name} 执行异常: {e}')
        return _payload('error', f'工具执行失败: {e}'), False
    _log.warning(f'未知工具调用: {name}')
    return _payload('error', f'未知工具: {name}'), False


# ----------------------------------------------------------------------
# 目标解析
# ----------------------------------------------------------------------


def _participants(ctx: ToolContext) -> List[str]:
    """群聊返回房间参与者列表；私聊返回当前用户。"""
    if ctx.is_group:
        return [str(uid) for uid in ctx.session_store.get_participants(ctx.sid)]
    return [ctx.user_id]


def _sender_nickname(ctx: ToolContext) -> str:
    """当前消息发送者昵称（可能为空）。"""
    sender = getattr(ctx.event, 'sender', None)
    return (getattr(sender, 'nickname', '') or '').strip()


def _match_participant(target: str, participants: List[str]) -> Optional[str]:
    """从 target 文本中解析出参与者 QQ。

    兼容模型常见的多种写法：纯 QQ、``玩家123456``、``昵称(123456)``、``@123456`` 等，
    只要文本中出现某参与者的 QQ 即视为命中（避免把玩家误判为 NPC）。

    :param target: 工具参数 target
    :param participants: 在场参与者 QQ 列表
    :return: 命中的 QQ；未命中返回 None
    """
    target = (target or '').strip()
    if not target:
        return None
    if target in participants:
        return target
    for qq in re.findall(r'\d{5,12}', target):
        if qq in participants:
            return qq
    return None


def _resolve_player(ctx: ToolContext, target: str) -> Tuple[Optional[str], Optional[str]]:
    """把工具参数 target 解析为玩家 QQ。

    :return: ``(user_id, error_message)``；解析失败时 user_id 为 None
    """
    target = (target or '').strip()
    if not ctx.is_group:
        return ctx.user_id, None
    participants = _participants(ctx)
    if not participants:
        return None, '当前房间没有参与者'
    is_self = (not target or target in ('self', '自己', ctx.user_id)
               or target == _sender_nickname(ctx))
    if is_self:
        return (ctx.user_id if ctx.user_id in participants else None,
                None if ctx.user_id in participants else '你不在房间参与者中')
    matched = _match_participant(target, participants)
    if matched:
        return matched, None
    # 展示名匹配（玩家QQ 形式）
    for uid in participants:
        if player_label(uid) == target or uid == target:
            return uid, None
    return None, f'无法解析目标玩家: {target!r}'


def _roll_command(ctx: ToolContext, roll_key: str, op: Optional[str],
                  target: Optional[int], hidden: bool) -> str:
    """生成给玩家的一步可复制掷骰命令。"""
    suffix = f' {op}{target}' if op and target is not None else ''
    if ctx.plugin.get_config('EnableInGameDiceKeyword', True):
        keyword = (ctx.plugin.get_config('InGameDiceKeyword', '.r') or '.r').strip()
        return f'{keyword}{"h" if hidden else ""} {roll_key}{suffix}'
    return f'/trpg {"rh" if hidden else "roll"} {roll_key}{suffix}'


# ----------------------------------------------------------------------
# 检定下发
# ----------------------------------------------------------------------


async def _request_check(args: dict, ctx: ToolContext) -> str:
    """下发检定请求：校验后通知玩家自行掷骰，写入 pending_checks。"""
    expression = str(args.get('expression') or '').strip()
    reason = str(args.get('reason') or '').strip()
    hidden = bool(args.get('hidden', False))
    if not expression:
        return _payload('error', '缺少骰式 expression')
    if not reason:
        return _payload('error', '缺少检定名 reason')

    # 难度解析（必填：否则无法裁定成败）
    difficulty = str(args.get('difficulty') or '').strip()
    if not difficulty:
        return _payload('error', '缺少 difficulty（判定目标值），请提供如 "<=50" / ">=15" 后再下发')
    match = _DIFFICULTY_RE.match(difficulty.replace(' ', ''))
    if not match:
        return _payload('error', f'非法的 difficulty: {difficulty!r}（形如 <=50 / >=15）')
    op, target_value = match.group(1), int(match.group(2))
    if abs(target_value) > 1000:
        return _payload('error', f'目标值超出范围: {target_value}（|目标值| <= 1000）')

    max_count = ctx.plugin._get_int_config('MaxDiceCount', dice.DEFAULT_MAX_DICE_COUNT)
    max_sides = ctx.plugin._get_int_config('MaxDiceSides', dice.DEFAULT_MAX_DICE_SIDES)
    try:
        parsed = dice.parse(f'{expression} {op}{target_value}' if op else expression,
                            max_count=max_count, max_sides=max_sides)
    except (dice.DiceError, ValueError) as e:
        return _payload('error', f'骰式非法: {e}')
    roll_key = dice.roll_key(parsed)

    # 目标与掷骰人
    raw_target = str(args.get('target') or '').strip()
    participants = _participants(ctx)
    is_npc = False
    subject = raw_target or '玩家'
    roller: Optional[str] = None
    if ctx.is_group:
        matched = _match_participant(raw_target, participants)
        is_self = (not raw_target or raw_target in ('self', '自己', ctx.user_id)
                   or raw_target == _sender_nickname(ctx))
        if matched:
            # 玩家本人（兼容 昵称(QQ) / @QQ / 纯 QQ 等写法）
            roller = matched
        elif is_self:
            roller = ctx.user_id if ctx.user_id in participants else None
        else:
            # 非参与者：视为 NPC/实体，随机指定一名在场玩家代掷
            is_npc = True
            if not participants:
                return _payload('error', '当前房间没有可用玩家代掷')
            roller = random.choice(participants)
    else:
        roller = ctx.user_id
        if raw_target and not raw_target.isdigit():
            subject = raw_target
    if roller is None:
        return _payload('error', '无法确定掷骰玩家')
    if ctx.is_group and not is_npc:
        subject = player_label(roller)

    # 确保角色存在
    label = ''
    if roller == ctx.user_id:
        label = getattr(getattr(ctx.event, 'sender', None), 'nickname', '') or ''
    ctx.state.ensure_character(roller, label)

    # 写入待完成检定
    check = {
        'subject': subject,
        'subject_id': roller if not is_npc else raw_target,
        'roller_id': str(roller),
        'expr': expression,
        'roll_key': roll_key,
        'op': op,
        'target': target_value,
        'reason': reason,
        'hidden': hidden,
        'is_npc': is_npc,
    }
    record = ctx.session_store.add_check(ctx.scope, ctx.sid, check)
    if record is None:
        return _payload('error', '会话不存在，无法下发检定')

    # 下发指令
    command = _roll_command(ctx, roll_key, op, target_value, hidden)
    judge_text = f'（目标 {op}{target_value}）' if op else ''
    lines = [f'检定请求：{subject} 请进行「{reason}」']
    if is_npc and ctx.is_group:
        lines.append(f'（由 {player_label(roller)} 代 {subject} 掷骰）')
    lines.append(f'骰式：{roll_key}{judge_text}')
    lines.append(f'发送命令：{command}')
    text = '\n'.join(lines)
    if not await _deliver(ctx, roller, hidden, text, is_npc):
        # 下发失败则回滚该检定，避免玩家无法完成
        ctx.session_store.clear_checks(ctx.scope, ctx.sid)
        return _payload('error', '检定请求下发失败')

    _log.info(f'[{ctx.scope} {ctx.sid}] 已下发检定 {record["id"]} -> {roller}（{reason}）')
    return _payload('ok', f'已向 {subject} 下发「{reason}」，等待玩家掷骰。',
                    {'check_id': record['id'], 'roller': str(roller),
                     'command': command})


async def _deliver(ctx: ToolContext, roller: str, hidden: bool, text: str,
                   is_npc: bool = False) -> bool:
    """把检定请求送达玩家（隐藏检定走私聊）。"""
    try:
        if hidden:
            await ctx.plugin.api.qq.post_private_msg(str(roller), text=text)
            if ctx.is_group:
                await ctx.event.reply(text='已向目标玩家私下发送检定请求', at_sender=False)
            return True
        if ctx.is_group and ctx.plugin.get_config('CheckRequestAtPlayer', True) and not is_npc:
            # 手动组装消息，让 @ 出现在文本之前（reply 的 at= 会追加到末尾）
            message = MessageArray()
            message.add_at(str(roller))
            message.add_text(' ')
            message.add_text(text)
            await ctx.event.reply(rtf=message, at_sender=False)
        else:
            await ctx.event.reply(text=text, at_sender=False)
        return True
    except Exception as e:  # noqa: BLE001
        _log.error(f'检定请求下发失败: {e}')
        return False


# ----------------------------------------------------------------------
# 只读工具
# ----------------------------------------------------------------------


def _get_state(args: dict, ctx: ToolContext) -> str:
    """读取状态投影或明细。"""
    target = str(args.get('target') or 'party').strip()
    if target in ('self', '自己'):
        target = ctx.user_id
    detail = 'short'
    if target == 'scene':
        text = ctx.state.project_scene(budget=1200)
    else:
        text = ctx.state.project(target=target, detail=detail, budget=1200)
    if not text:
        return _payload('ok', '暂无状态记录', {'text': ''})
    return _payload('ok', text, {'text': text})


def _search_lore(args: dict, ctx: ToolContext) -> str:
    """检索世界书（阶段 3 前为空）。"""
    query = str(args.get('query') or '').strip()
    top_k = int(args.get('top_k') or 3)
    hits = lore.search(query, top_k=top_k)
    if not hits:
        return _payload('ok', '未检索到相关设定', {'hits': []})
    return _payload('ok', '、'.join(h.get('label', '') for h in hits), {'hits': hits})


# ----------------------------------------------------------------------
# 受控写工具
# ----------------------------------------------------------------------


def _require_writer(ctx: ToolContext, target: str) -> Tuple[Optional[str], Optional[str]]:
    """写工具的玩家解析（群内仅允许改自己，防跨玩家篡改）。"""
    uid, err = _resolve_player(ctx, target)
    if err:
        return None, err
    if ctx.is_group and uid != ctx.user_id:
        # 群内不允许替他人改写状态（NPC 由 GM 随机代掷检定，但状态写入仅限本人）
        return None, '群聊中只能修改你自己的角色状态'
    return uid, None


def _change_hp(args: dict, ctx: ToolContext) -> str:
    """生命值变更（伤害需已结算检定背书）。"""
    target = str(args.get('target') or '').strip()
    uid, err = _require_writer(ctx, target)
    if err:
        return _payload('error', err)
    try:
        delta = int(args.get('delta'))
    except (TypeError, ValueError):
        return _payload('error', 'delta 必须为整数')
    if delta < 0 and not ctx.session_store.has_resolved_check(ctx.scope, ctx.sid, uid):
        return _payload('error', '伤害需要系统已结算的检定背书；请先 request_check 并由玩家掷骰')
    ctx.state.ensure_character(uid)
    receipts = ctx.state.apply_ops(
        [{'kind': 'hp', 'user_id': uid, 'delta': delta}],
        max_events=ctx.plugin._get_int_config('MaxEvents', 200),
    )
    return _payload('ok', f'生命值变更 {delta:+d}', {'receipts': receipts})


def _change_attribute(args: dict, ctx: ToolContext) -> str:
    """属性变更。"""
    target = str(args.get('target') or '').strip()
    uid, err = _require_writer(ctx, target)
    if err:
        return _payload('error', err)
    field = str(args.get('field') or '').strip()
    op = str(args.get('op') or 'set').strip()
    try:
        value = int(args.get('value'))
    except (TypeError, ValueError):
        return _payload('error', 'value 必须为整数')
    ctx.state.ensure_character(uid)
    receipts = ctx.state.apply_ops(
        [{'kind': 'attr', 'user_id': uid, 'field': field, 'op': op, 'value': value}],
        max_events=ctx.plugin._get_int_config('MaxEvents', 200),
    )
    return _payload('ok', f'属性 {field} 已更新', {'receipts': receipts})


def _manage_inventory(args: dict, ctx: ToolContext) -> str:
    """物品增删/使用。"""
    target = str(args.get('target') or '').strip()
    uid, err = _require_writer(ctx, target)
    if err:
        return _payload('error', err)
    action = str(args.get('action') or '').strip()
    item = str(args.get('item') or '').strip()
    try:
        qty = int(args.get('qty', 1))
    except (TypeError, ValueError):
        return _payload('error', 'qty 必须为整数')
    ctx.state.ensure_character(uid)
    receipts = ctx.state.apply_ops(
        [{'kind': 'inventory', 'user_id': uid, 'action': action, 'item': item, 'qty': qty}],
        max_events=ctx.plugin._get_int_config('MaxEvents', 200),
    )
    return _payload('ok', f'物品 {item} 操作 {action} 完成', {'receipts': receipts})


def _update_scene(args: dict, ctx: ToolContext) -> str:
    """场景字段更新。"""
    fields = {key: args[key] for key in _SCENE_PARAMS if str(args.get(key) or '').strip()}
    if not fields:
        return _payload('error', '未提供任何场景字段')
    receipts = ctx.state.apply_ops(
        [{'kind': 'scene', 'fields': fields}],
        max_events=ctx.plugin._get_int_config('MaxEvents', 200),
    )
    return _payload('ok', '场景已更新', {'receipts': receipts})


def _record_event(args: dict, ctx: ToolContext) -> str:
    """记录剧情事件。"""
    text = str(args.get('text') or '').strip()
    if not text:
        return _payload('error', '事件内容不能为空')
    visibility = str(args.get('visibility') or 'public')
    receipts = ctx.state.apply_ops(
        [{'kind': 'event', 'event': {'t': 'note', 'text': text,
                                     'visibility': visibility, 'actor': ctx.user_id}}],
        max_events=ctx.plugin._get_int_config('MaxEvents', 200),
    )
    return _payload('ok', '事件已记录', {'receipts': receipts})
