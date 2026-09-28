# -*- coding: utf-8 -*-
"""NcatBotTRPG 掷骰引擎（自研）

纯函数实现，不依赖 NcatBot 框架，便于单元测试与未来替换。

支持语法（命令参数以空格连接后整体解析）：
- ``NdM`` / ``dM``：掷 N 个 M 面骰（N 缺省为 1），如 ``1d100``、``d20``、``2d6``
- ``NdM±K``：在总和上加减修正，如 ``1d20+3``、``3d8-2``
- ``adv`` / ``dis``（或 advantage / disadvantage）：仅单个 d20，掷两次取高/取低，同时出现则抵消
- ``bonusN`` / ``penaltyN``：仅单个 d100，克苏鲁式奖励/惩罚骰（N 缺省为 1）
- 判定：``<=`` / ``>=`` / ``<`` / ``>`` / ``==`` 后接目标值，如 ``1d100 <= 50``

不支持重复投掷（``N#expr`` 语法）：一次行动只能掷一次，避免玩家刷骰。

设计参考（仅借鉴思路，未复制任何代码）：
- diceframe（AGPL-3.0）的骰式解析分层与防刷上限思路
- CoC 7e 奖励/惩罚骰的“十位骰取大/取小”判定方式

本文件为原始实现，插件整体遵循 AGPL-3.0（见仓库 NOTICE）。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import List, Optional

from ncatbot.utils.logger import get_log

_log = get_log('ncatbot_trpg')

# 防刷上限（默认值，可由插件配置覆盖）
DEFAULT_MAX_DICE_COUNT = 100
DEFAULT_MAX_DICE_SIDES = 10000
DEFAULT_MAX_MODIFIER = 100
DEFAULT_MAX_BONUS_DICE = 10

# 合法的比较运算符
_OPERATORS = ('<=', '>=', '==', '<', '>')

# 末尾判定：<= / >= / == / < / > 后接目标值
_VERDICT_RE = re.compile(r'(<=|>=|==|<|>)\s*(-?\d+)\s*$')
# 优势 / 劣势关键词（词边界，避免误伤 advantage/disadvantage 内部）
_ADV_RE = re.compile(r'(?<![a-zA-Z])(?:advantage|adv)(?![a-zA-Z])', re.I)
_DIS_RE = re.compile(r'(?<![a-zA-Z])(?:disadvantage|dis)(?![a-zA-Z])', re.I)
# 奖励 / 惩罚骰：bonusN / penaltyN（N 可省略，缺省 1）
_BONUS_RE = re.compile(r'(?<![a-zA-Z])(bonus|penalty)\s*(\d*)(?![a-zA-Z])', re.I)
# 骰式：可选数量 + d + 面数 + 可选修正
_DICE_RE = re.compile(r'^(\d*)[dD](\d+)([+-]\d+)?$')


class DiceError(ValueError):
    """骰式解析或参数校验错误。"""


def _fail(message: str):
    """记录骰式解析失败并抛出 :class:`DiceError`。

    :param message: 失败原因
    :raises DiceError: 始终抛出
    """
    _log.debug(f'骰式解析失败: {message}')
    raise DiceError(message)


@dataclass
class ParsedRoll:
    """解析后的骰式描述。"""

    raw: str
    count: int = 1
    sides: int = 20
    modifier: int = 0
    advantage: bool = False
    disadvantage: bool = False
    bonus: int = 0
    penalty: int = 0
    op: Optional[str] = None
    target: Optional[int] = None


@dataclass
class SingleRoll:
    """一次投掷结果。"""

    dice: List[int] = field(default_factory=list)
    used: List[int] = field(default_factory=list)
    modifier: int = 0
    total: int = 0
    note: str = ''


@dataclass
class RollOutcome:
    """一次指令的完整结果（单组投掷）。"""

    expression: str
    parsed: ParsedRoll
    rolls: List[SingleRoll] = field(default_factory=list)
    verdict: Optional[str] = None
    critical: Optional[str] = None


def parse(
    expr_text: str,
    *,
    max_count: int = DEFAULT_MAX_DICE_COUNT,
    max_sides: int = DEFAULT_MAX_DICE_SIDES,
    max_modifier: int = DEFAULT_MAX_MODIFIER,
    max_bonus: int = DEFAULT_MAX_BONUS_DICE,
) -> ParsedRoll:
    """解析骰式字符串为 :class:`ParsedRoll`。

    :param expr_text: 骰式文本，如 ``1d100 <= 50``、``d20 adv +5``
    :param max_count: 单次最大骰数
    :param max_sides: 骰子最大面数
    :param max_modifier: 修正绝对值上限
    :param max_bonus: 奖励/惩罚骰最大数量
    :return: 解析结果
    :raises DiceError: 骰式非法、含重复投掷或超出上限
    """
    text = (expr_text or '').strip()
    if not text:
        _fail('请提供骰式，例如 1d100 或 d20+3')

    # 禁止重复投掷（N#expr），防止玩家一次命令刷多次骰
    if '#' in text:
        _fail('不支持重复投掷（N#），每位玩家一次行动只能掷一次')

    # 末尾判定运算符与目标值
    op: Optional[str] = None
    target: Optional[int] = None
    m = _VERDICT_RE.search(text)
    if m:
        op = m.group(1)
        target = int(m.group(2))
        text = text[:m.start()].strip()

    # 优势 / 劣势
    advantage = bool(_ADV_RE.search(text))
    disadvantage = bool(_DIS_RE.search(text))
    text = _ADV_RE.sub(' ', text)
    text = _DIS_RE.sub(' ', text)

    # 奖励 / 惩罚骰
    bonus = 0
    penalty = 0
    m = _BONUS_RE.search(text)
    if m:
        value = int(m.group(2)) if m.group(2) else 1
        if m.group(1).lower() == 'bonus':
            bonus = value
        else:
            penalty = value
        text = text[:m.start()] + text[m.end():]

    # 去除空白后匹配骰式
    text = re.sub(r'\s+', '', text)
    m = _DICE_RE.match(text)
    if not m:
        _fail(f'无法解析骰式：{expr_text}')

    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    modifier = int(m.group(3)) if m.group(3) else 0

    # ---- 参数校验 ----
    if count < 1 or count > max_count:
        _fail(f'骰子数量需在 1~{max_count} 之间')
    if sides < 2 or sides > max_sides:
        _fail(f'骰子面数需在 2~{max_sides} 之间')
    if abs(modifier) > max_modifier:
        _fail(f'修正值需在 ±{max_modifier} 之内')
    if max(bonus, penalty) > max_bonus:
        _fail(f'奖励/惩罚骰数量需在 0~{max_bonus} 之间')

    # 优势/劣势仅支持单个 d20；同时出现则抵消
    if (advantage or disadvantage) and (count != 1 or sides != 20):
        _fail('优势/劣势仅支持单个 d20，例如 d20 adv')
    if advantage and disadvantage:
        advantage = disadvantage = False

    # 奖励/惩罚骰仅支持单个 d100，且不能与优势/劣势同用
    if (bonus or penalty) and (count != 1 or sides != 100):
        _fail('奖励/惩罚骰仅支持单个 d100，例如 d100 bonus1')
    if (bonus or penalty) and (advantage or disadvantage):
        _fail('奖励/惩罚骰不能与优势/劣势同时使用')

    parsed = ParsedRoll(
        raw=expr_text.strip(),
        count=count,
        sides=sides,
        modifier=modifier,
        advantage=advantage,
        disadvantage=disadvantage,
        bonus=bonus,
        penalty=penalty,
        op=op,
        target=target,
    )
    _log.debug(f'骰式解析成功: {expr_text!r} -> {_dice_label(parsed)}')
    return parsed


def roll(parsed: ParsedRoll, rng: Optional[random.Random] = None,
         enable_critical: bool = True) -> RollOutcome:
    """按解析结果执行投掷。

    :param parsed: :func:`parse` 的返回值
    :param rng: 随机源（默认使用 ``random`` 模块，便于测试注入种子）
    :param enable_critical: 是否判定大成功/大失败
    :return: 投掷结果
    """
    rng = rng or random
    if parsed.bonus or parsed.penalty:
        single = _roll_d100_bonus(parsed, rng)
    elif parsed.advantage or parsed.disadvantage:
        single = _roll_d20_advantage(parsed, rng)
    else:
        single = _roll_plain(parsed, rng)

    outcome = RollOutcome(
        expression=_dice_label(parsed), parsed=parsed, rolls=[single])
    _log.debug(f'投掷 {outcome.expression}: total={single.total}')

    if parsed.op is not None and parsed.target is not None:
        outcome.verdict = _verdict(single.total, parsed.op, parsed.target)

    if enable_critical:
        outcome.critical = _critical(parsed, single)

    return outcome


def roll_expression(
    expr_text: str,
    rng: Optional[random.Random] = None,
    enable_critical: bool = True,
    **parse_kwargs,
) -> RollOutcome:
    """:func:`parse` + :func:`roll` 的便捷封装。"""
    return roll(parse(expr_text, **parse_kwargs), rng=rng, enable_critical=enable_critical)


def format_outcome(outcome: RollOutcome) -> str:
    """将结果格式化为可直接发送的中文文本。

    :param outcome: 投掷结果
    :return: 展示文本
    """
    lines: List[str] = [
        f'🎲 {outcome.expression} = {_group_detail(outcome.rolls[0])}'
    ]

    if outcome.verdict:
        suffix = f'（{outcome.critical}）' if outcome.critical else ''
        lines.append(
            f'判定 {outcome.parsed.op} {outcome.parsed.target}：{outcome.verdict}{suffix}'
        )
    elif outcome.critical:
        lines.append(outcome.critical)

    return '\n'.join(lines)


# ----------------------------------------------------------------------
# 内部实现
# ----------------------------------------------------------------------


def _roll_plain(parsed: ParsedRoll, rng: random.Random) -> SingleRoll:
    """普通 NdM±K 投掷。"""
    dice = [rng.randint(1, parsed.sides) for _ in range(parsed.count)]
    return SingleRoll(
        dice=dice,
        used=list(dice),
        modifier=parsed.modifier,
        total=sum(dice) + parsed.modifier,
    )


def _roll_d20_advantage(parsed: ParsedRoll, rng: random.Random) -> SingleRoll:
    """单个 d20 的优势/劣势：掷两次取高/取低。"""
    first = rng.randint(1, 20)
    second = rng.randint(1, 20)
    if parsed.advantage:
        chosen = max(first, second)
        note = f'优势 [{first}, {second}] 取{chosen}'
    else:
        chosen = min(first, second)
        note = f'劣势 [{first}, {second}] 取{chosen}'
    return SingleRoll(
        dice=[first, second],
        used=[chosen],
        modifier=parsed.modifier,
        total=chosen + parsed.modifier,
        note=note,
    )


def _roll_d100_bonus(parsed: ParsedRoll, rng: random.Random) -> SingleRoll:
    """单个 d100 的克苏鲁式奖励/惩罚骰。

    掷 (N+1) 个十位骰与 1 个个位骰：奖励取最小的十位，惩罚取最大的十位；
    十位与个位同为 0 时结果为 100。
    """
    extra = parsed.bonus or parsed.penalty
    tens_dice = [rng.randint(0, 9) for _ in range(extra + 1)]
    units = rng.randint(0, 9)
    if parsed.bonus:
        chosen = min(tens_dice)
        kind = f'奖励{parsed.bonus}'
    else:
        chosen = max(tens_dice)
        kind = f'惩罚{parsed.penalty}'
    base = 100 if (chosen == 0 and units == 0) else chosen * 10 + units
    note = f'{kind} 十位[{" ".join(map(str, tens_dice))}] 个位{units}'
    return SingleRoll(
        dice=tens_dice,
        used=[chosen],
        modifier=parsed.modifier,
        total=base + parsed.modifier,
        note=note,
    )


def _verdict(total: int, op: str, target: int) -> str:
    """根据比较运算符给出判定结果。"""
    passed = {
        '<=': total <= target,
        '>=': total >= target,
        '<': total < target,
        '>': total > target,
        '==': total == target,
    }[op]
    return '成功' if passed else '失败'


def _critical(parsed: ParsedRoll, single: SingleRoll) -> Optional[str]:
    """大成功/大失败判定（仅 d20 与 d100 单骰）。"""
    if parsed.bonus or parsed.penalty:
        return None
    if parsed.sides == 20 and parsed.count == 1:
        if 20 in single.used:
            return '大成功'
        if 1 in single.used:
            return '大失败'
    if parsed.sides == 100 and parsed.count == 1:
        if single.total == 1:
            return '大成功'
        if single.total == 100:
            return '大失败'
        # 克苏鲁规则：目标成功率低于 50 时，96~99 亦为大失败
        if parsed.target is not None and parsed.target < 50 and single.total >= 96:
            return '大失败'
    return None


def _dice_label(parsed: ParsedRoll) -> str:
    """生成用于展示的骰式标签（不含判定部分）。"""
    label = f'{parsed.count}d{parsed.sides}' if parsed.count != 1 else f'd{parsed.sides}'
    if parsed.advantage:
        label += ' 优势'
    if parsed.disadvantage:
        label += ' 劣势'
    if parsed.bonus:
        label += f' 奖励{parsed.bonus}'
    if parsed.penalty:
        label += f' 惩罚{parsed.penalty}'
    if parsed.modifier:
        label += f'{parsed.modifier:+d}'
    return label


def _group_detail(single: SingleRoll) -> str:
    """生成单组投掷的明细文本（含总和）。"""
    if single.note:
        core = single.note
        if single.modifier:
            core += f' {single.modifier:+d}'
        return f'{core} = {single.total}'
    if len(single.dice) == 1 and not single.modifier:
        return str(single.total)
    if len(single.dice) > 1:
        core = '[' + ', '.join(map(str, single.dice)) + ']'
    else:
        core = str(single.dice[0])
    if single.modifier:
        core += f' {single.modifier:+d}'
    return f'{core} = {single.total}'
