# -*- coding: utf-8 -*-
"""房间系统测试：默认房间只读视图、加入/离开、开团自动加入。

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness
from ncatbot.testing.factories.qq import group_message

from fake_llm import FakeLLMClient

pytestmark = pytest.mark.asyncio(mode="strict")

PLUGINS_DIR = Path(__file__).resolve().parents[2]
PLUGIN_NAME = "NcatBotTRPG"
ROOT_QQ = "123456"
GROUP_ID = "200200"


def _harness() -> PluginTestHarness:
    return PluginTestHarness(plugin_names=[PLUGIN_NAME], plugins_dir=PLUGINS_DIR)


def _configure(plugin, replies=None):
    plugin.config["IsConfigured"] = True
    client = FakeLLMClient(replies or [])
    plugin.llm._client = client
    return client


def _rooms(plugin):
    return plugin.data['sessions']['rooms']


async def test_group_status_shows_default_room_without_persist(tmp_path, monkeypatch):
    """`/trpg status` 展示默认 0 人房间（含房间信息），但只读不落库"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin)
        assert GROUP_ID not in _rooms(plugin)

        await h.inject(group_message('/trpg status', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        h.assert_api('send_group_msg').with_text('房间信息', '准备中', '0人')
        assert GROUP_ID not in _rooms(plugin)


async def test_room_join_and_leave_keeps_room(tmp_path, monkeypatch):
    """加入会持久化房间；离开后清空但房间仍保留"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin)

        await h.inject(group_message('/trpg join', group_id=GROUP_ID, user_id='111'))
        await h.settle()
        assert _rooms(plugin)[GROUP_ID]['participants'] == ['111']

        h.reset_api()
        await h.inject(group_message('/trpg leave', group_id=GROUP_ID, user_id='111'))
        await h.settle()
        assert _rooms(plugin)[GROUP_ID]['participants'] == []


async def test_group_start_auto_joins_starter(tmp_path, monkeypatch):
    """群内开团时发起人自动加入默认房间并置为进行中"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, ["叙事"])

        await h.inject(group_message('/trpg start 设定', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        room = _rooms(plugin)[GROUP_ID]
        assert ROOT_QQ in room['participants']
        assert room['status'] == 'running'
        assert plugin.data['sessions']['group'][GROUP_ID]['active'] is True
