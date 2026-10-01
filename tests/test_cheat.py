# -*- coding: utf-8 -*-
"""调试命令 /trpg cheat 测试：仅管理员可插入系统提示词并可选触发回复。

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness, group_message
from ncatbot.testing import extract_text
from ncatbot.testing.factories.qq import private_message
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
    return [GroupMemberInfo(user_id=uid, role=role, group_id=GROUP_ID)
            for uid, role in roles.items()]


async def _start_group(h):
    await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
    await h.settle()
    h.reset_api()


async def test_cheat_requires_admin_group(tmp_path, monkeypatch):
    """普通成员无法使用 cheat，且不会插入历史"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        h.mock_api_for("qq").set_response(
            "get_group_member_list", _member_list({"333": "member"}))

        await h.inject(group_message('/trpg cheat 偷看答案 false',
                                     group_id=GROUP_ID, user_id="333"))
        await h.settle()
        h.assert_api('send_group_msg').with_text('权限不足')
        assert plugin.data['sessions']['group'].get(GROUP_ID) is None


async def test_cheat_requires_admin_private(tmp_path, monkeypatch):
    """私聊非全局管理员被拒"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        await h.inject(private_message('/trpg cheat 偷看答案 false', user_id="999999"))
        await h.settle()
        h.assert_api('send_private_msg').with_text('权限不足')


async def test_cheat_inserts_system_without_reply(tmp_path, monkeypatch):
    """false：仅插入 system 历史，不调用 LLM"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin)
        await _start_group(h)

        await h.inject(group_message('/trpg cheat 隐藏信息：队长是叛徒 false',
                                     group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        h.assert_api('send_group_msg').with_text('已插入系统提示词')
        assert len(client.chat.completions.calls) == 0
        history = plugin.data['sessions']['group'][GROUP_ID]['history']
        assert history[-1]['role'] == 'system'
        assert '队长是叛徒' in history[-1]['content']


async def test_cheat_triggers_reply(tmp_path, monkeypatch):
    """true：插入 system 后立即触发主持人回复"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, ['收到，暗流涌动。'])
        await _start_group(h)

        await h.inject(group_message('/trpg cheat 请以叛徒视角推进剧情 true',
                                     group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        texts = [extract_text(c) for c in h.assert_api('send_group_msg').calls]
        assert any('暗流涌动' in t for t in texts)
        assert len(client.chat.completions.calls) == 1
        sent = client.chat.completions.calls[0]['messages']
        # 发送前已合并为唯一、置顶的 system（兼容 GLM 等仅接受单条 system 的厂商）
        assert [m['role'] for m in sent].count('system') == 1
        assert sent[0]['role'] == 'system'
        assert '叛徒视角' in sent[0]['content']
        history = plugin.data['sessions']['group'][GROUP_ID]['history']
        assert history[-2]['role'] == 'system'
        assert history[-1]['role'] == 'assistant'


async def test_cheat_merge_system_disabled_keeps_separate(tmp_path, monkeypatch):
    """MergeSystemMessages=False 时保持多条 system（缓存友好，要求后端支持）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        plugin.config['MergeSystemMessages'] = False
        client = _configure(plugin, ['收到。'])
        await _start_group(h)

        await h.inject(group_message('/trpg cheat 保持独立 true',
                                     group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        sent = client.chat.completions.calls[0]['messages']
        assert [m['role'] for m in sent].count('system') == 2


async def test_cheat_defaults_to_reply(tmp_path, monkeypatch):
    """省略布尔参数时默认 true（立即触发回复）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, ['默认触发。'])
        await _start_group(h)

        await h.inject(group_message('/trpg cheat 推进剧情', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        assert len(client.chat.completions.calls) == 1
        texts = [extract_text(c) for c in h.assert_api('send_group_msg').calls]
        assert any('默认触发' in t for t in texts)
        history = plugin.data['sessions']['group'][GROUP_ID]['history']
        assert history[-2]['role'] == 'system'
        assert history[-2]['content'] == '推进剧情'
        assert history[-1]['role'] == 'assistant'


async def test_cheat_trailing_non_bool_is_content(tmp_path, monkeypatch):
    """末尾非布尔 token 视为系统提示词内容，默认立即回复"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, ['好的。'])
        await _start_group(h)

        await h.inject(group_message('/trpg cheat 这句话结尾是 maybe',
                                     group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        history = plugin.data['sessions']['group'][GROUP_ID]['history']
        assert history[-2]['role'] == 'system'
        assert history[-2]['content'] == '这句话结尾是 maybe'


async def test_cheat_requires_active_session(tmp_path, monkeypatch):
    """无进行中的团时拒绝"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        _configure(h.get_plugin(PLUGIN_NAME))
        await h.inject(private_message('/trpg cheat 内容 false', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('没有进行中的团')
