# -*- coding: utf-8 -*-
"""掷骰命令测试：经 MockAdapter 注入事件，断言回复文本与暗骰私聊行为。

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness
from ncatbot.testing.factories.qq import group_message, private_message

pytestmark = pytest.mark.asyncio(mode="strict")

PLUGINS_DIR = Path(__file__).resolve().parents[2]
PLUGIN_NAME = "NcatBotTRPG"
ROOT_QQ = "123456"
GROUP_ID = "200200"


def _harness() -> PluginTestHarness:
    return PluginTestHarness(plugin_names=[PLUGIN_NAME], plugins_dir=PLUGINS_DIR)


async def test_roll_command_private(tmp_path, monkeypatch):
    """私聊 /trpg roll 返回掷骰结果文本"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        await h.inject(private_message('/trpg roll 1d20+3', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').called().with_text('🎲', 'd20+3')


async def test_roll_command_missing_expr(tmp_path, monkeypatch):
    """无骰式时返回帮助文本"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        await h.inject(private_message('/trpg roll', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('掷骰用法')


async def test_roll_command_invalid_expr(tmp_path, monkeypatch):
    """非法骰式返回错误提示"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        await h.inject(private_message('/trpg roll 999d6', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('掷骰错误')


async def test_repeat_roll_command_rejected(tmp_path, monkeypatch):
    """不支持重复投掷（N#），返回明确错误且不产生骰子"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        await h.inject(private_message('/trpg roll 3#2d6', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').called().with_text('不支持重复投掷')


async def test_hidden_roll_in_group_sends_private(tmp_path, monkeypatch):
    """群内暗骰：结果私聊发起者，群内仅提示"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        await h.inject(group_message('/trpg rh 1d20', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').called().with_params(user_id=ROOT_QQ).with_text('🎲')
        h.assert_api('send_group_msg').called().with_text('暗骰结果已私聊发送')
