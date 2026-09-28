# -*- coding: utf-8 -*-
"""掷骰引擎纯函数测试（不加载框架，无事件注入）。

运行：python -m pytest tests -v -o "addopts="
"""
import sys
from pathlib import Path

import pytest

# 插件根目录（dice.py 所在目录）加入 sys.path，便于直接导入纯函数模块
PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import dice


class SeqRng:
    """按预设序列返回掷骰结果，用于结果确定性断言。"""

    def __init__(self, values):
        self._values = list(values)

    def randint(self, a, b):
        value = self._values.pop(0)
        assert a <= value <= b, f"固定随机值 {value} 不在 [{a}, {b}] 区间"
        return value


def test_parse_basic():
    parsed = dice.parse('1d20+3')
    assert (parsed.count, parsed.sides, parsed.modifier) == (1, 20, 3)
    assert parsed.op is None

    parsed = dice.parse('d100')
    assert (parsed.count, parsed.sides, parsed.modifier) == (1, 100, 0)


def test_parse_verdict():
    parsed = dice.parse('1d100 <= 50')
    assert parsed.op == '<=' and parsed.target == 50
    assert (parsed.count, parsed.sides) == (1, 100)


def test_roll_plain_with_modifier():
    outcome = dice.roll(dice.parse('1d20+3'), rng=SeqRng([15]))
    assert outcome.rolls[0].total == 18
    assert outcome.expression == 'd20+3'
    assert '= 18' in dice.format_outcome(outcome)


def test_roll_multi_dice():
    outcome = dice.roll(dice.parse('2d6'), rng=SeqRng([4, 2]))
    assert outcome.rolls[0].total == 6
    assert outcome.rolls[0].dice == [4, 2]
    assert '[4, 2]' in dice.format_outcome(outcome)


def test_verdict_success_and_failure():
    outcome = dice.roll(dice.parse('1d100 <= 50'), rng=SeqRng([37]))
    assert outcome.verdict == '成功'
    outcome = dice.roll(dice.parse('1d100 <= 50'), rng=SeqRng([77]))
    assert outcome.verdict == '失败'
    outcome = dice.roll(dice.parse('1d20+5 >= 15'), rng=SeqRng([12]))
    assert outcome.verdict == '成功'


def test_advantage_and_disadvantage():
    outcome = dice.roll(dice.parse('d20 adv'), rng=SeqRng([7, 18]))
    assert outcome.rolls[0].total == 18
    assert '优势' in dice.format_outcome(outcome)

    outcome = dice.roll(dice.parse('d20 dis'), rng=SeqRng([7, 18]))
    assert outcome.rolls[0].total == 7
    assert '劣势' in dice.format_outcome(outcome)

    # 优势与劣势同时出现则抵消
    parsed = dice.parse('d20 adv dis')
    assert not parsed.advantage and not parsed.disadvantage


def test_critical_d20_and_d100():
    assert dice.roll(dice.parse('d20'), rng=SeqRng([20])).critical == '大成功'
    assert dice.roll(dice.parse('d20'), rng=SeqRng([1])).critical == '大失败'
    assert dice.roll(dice.parse('1d100'), rng=SeqRng([1])).critical == '大成功'
    assert dice.roll(dice.parse('1d100'), rng=SeqRng([100])).critical == '大失败'
    # 目标成功率 < 50 时 96~99 亦为大失败
    outcome = dice.roll(dice.parse('1d100 <= 30'), rng=SeqRng([97]))
    assert outcome.critical == '大失败' and outcome.verdict == '失败'


def test_repeat_rolls_rejected():
    # 不支持重复投掷（N#expr），防止玩家一次命令刷多次骰
    for expr in ('3#2d6', '2#1d20', '1#1d100 <= 50', '10#d20'):
        with pytest.raises(dice.DiceError):
            dice.parse(expr)


def test_d100_bonus_and_penalty():
    # 奖励：十位取小；十位 3 + 个位 5 = 35
    outcome = dice.roll(dice.parse('d100 bonus2'), rng=SeqRng([3, 7, 9, 5]))
    assert outcome.rolls[0].total == 35
    assert '奖励2' in dice.format_outcome(outcome)

    # 惩罚：十位取大；十位 9 + 个位 5 = 95
    outcome = dice.roll(dice.parse('d100 penalty2'), rng=SeqRng([3, 7, 9, 5]))
    assert outcome.rolls[0].total == 95

    # 十位与个位同为 0 记为 100
    outcome = dice.roll(dice.parse('d100 bonus1'), rng=SeqRng([0, 0, 0]))
    assert outcome.rolls[0].total == 100


@pytest.mark.parametrize('expr', [
    '',            # 空
    'abc',         # 非法
    '999d6',       # 骰数超限
    '1d99999',     # 面数超限
    '1d20+999',    # 修正超限
    '2d20 adv',    # 优势仅支持单个 d20
    '1d6 bonus1',  # 奖励骰仅支持 d100
    '3#1d20 <= 5',  # 不支持重复投掷（N#）
    '3#2d6',        # 不支持重复投掷（N#）
    '1d100 bonus1 adv',  # 奖励骰与优势互斥
])
def test_parse_errors(expr):
    with pytest.raises(dice.DiceError):
        dice.parse(expr)
