# -*- coding: utf-8 -*-
"""团会话管理测试：开团/状态/重置/结束、群内权限门控、关键词开关。

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness
from ncatbot.testing.factories.qq import group_message, private_message
from ncatbot.types.napcat.group import GroupMemberInfo

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


def _member_list(roles):
    return [
        GroupMemberInfo(user_id=uid, role=role, group_id=GROUP_ID)
        for uid, role in roles.items()
    ]


async def test_status_reset_stop_flow_private(tmp_path, monkeypatch):
    """私聊：开团 → 状态 → 对话累积 → 重置 → 结束"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, ["叙事一", "叙事二"])

        await h.inject(private_message('/trpg start 设定内容', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('已开启跑团')
        assert plugin.data['sessions']['user'][ROOT_QQ]['prompt'] == '设定内容'

        # 状态
        h.reset_api()
        await h.inject(private_message('/trpg status', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('跑团状态', '已记录剧情消息')

        # 两次行动累积历史
        await h.inject(private_message('行动一', user_id=ROOT_QQ))
        await h.inject(private_message('行动二', user_id=ROOT_QQ))
        await h.settle()
        assert len(plugin.data['sessions']['user'][ROOT_QQ]['history']) == 4

        # 重置：清空历史，保留设定
        h.reset_api()
        await h.inject(private_message('/trpg reset', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('已清空剧情历史')
        session = plugin.data['sessions']['user'][ROOT_QQ]
        assert session['history'] == []
        assert session['prompt'] == '设定内容'

        # 结束
        h.reset_api()
        await h.inject(private_message('/trpg stop', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('已结束本会话的团')
        assert plugin.data['sessions']['user'][ROOT_QQ]['active'] is False


async def test_group_start_requires_admin(tmp_path, monkeypatch):
    """群内开团需管理员：普通成员被拒且不建团"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin)
        h.mock_api_for('qq').set_response(
            'get_group_member_list', _member_list({'333': 'member'})
        )

        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id='333'))
        await h.settle()
        h.assert_api('send_group_msg').with_text('权限不足')
        session = plugin.data['sessions']['group'].get(GROUP_ID)
        assert not session or not session.get('active')


async def test_group_owner_can_start(tmp_path, monkeypatch):
    """本群群主自动放行开团"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin)
        h.mock_api_for('qq').set_response(
            'get_group_member_list', _member_list({'111': 'owner'})
        )

        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id='111'))
        await h.settle()
        h.assert_api('get_group_member_list').called().with_params(group_id=GROUP_ID)
        h.assert_api('send_group_msg').with_text('已开启跑团')
        assert plugin.data['sessions']['group'][GROUP_ID]['active'] is True


async def test_start_twice_rejected(tmp_path, monkeypatch):
    """重复开团被拒绝"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin)

        await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()
        await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('已在进行中的团')


async def test_keyword_disabled_ignores_short_dice(tmp_path, monkeypatch):
    """EnableInGameDiceKeyword 关闭后，`.r` 不再触发掷骰"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin)

        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        plugin.config['EnableInGameDiceKeyword'] = False
        h.reset_api()

        await h.inject(group_message('.r 1d20', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_group_msg').not_called()


async def test_cross_plugin_session_query(tmp_path, monkeypatch):
    """跨插件协调接口（is_group/user_session_active）反映团激活状态"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin)

        assert plugin.is_group_session_active(GROUP_ID) is False
        assert plugin.is_user_session_active(ROOT_QQ) is False

        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        assert plugin.is_group_session_active(GROUP_ID) is True
        assert plugin.is_user_session_active(ROOT_QQ) is False

        await h.inject(group_message('/trpg stop', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        assert plugin.is_group_session_active(GROUP_ID) is False

