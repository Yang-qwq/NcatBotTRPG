# -*- coding: utf-8 -*-
"""掷骰结果传递测试

团激活后，玩家掷骰的类型与结果会被缓存，并在玩家下一次行动时通过 system prompt
交给主持人（LLM），使其能据此推进剧情；缓存随请求消费后清空。

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness
from ncatbot.testing.factories.qq import group_message, private_message

from fake_llm import FakeLLMClient

pytestmark = pytest.mark.asyncio(mode="strict")

PLUGINS_DIR = Path(__file__).resolve().parents[2]
PLUGIN_NAME = "NcatBotTRPG"
ROOT_QQ = "123456"
GROUP_ID = "200200"


def _harness() -> PluginTestHarness:
    return PluginTestHarness(plugin_names=[PLUGIN_NAME], plugins_dir=PLUGINS_DIR)


def _configure(plugin, replies):
    plugin.config["IsConfigured"] = True
    client = FakeLLMClient(replies)
    plugin.llm._client = client
    return client


async def _start(h):
    await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
    await h.settle()
    h.reset_api()


async def test_roll_buffered_then_injected_into_system_prompt(tmp_path, monkeypatch):
    """团内掷骰被缓存，下一次行动时注入 system prompt，随后清空"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, ["你挥剑砍向哥布林。"])
        await _start(h)

        await h.inject(private_message('/trpg roll 1d20+3', user_id=ROOT_QQ))
        await h.settle()

        # 掷骰本身不调用 LLM，但已缓存结果
        assert len(client.chat.completions.calls) == 0
        assert len(plugin.data['sessions']['user'][ROOT_QQ]['pending_rolls']) == 1

        # 玩家提交行动 → 掷骰结果进入 system prompt
        await h.inject(private_message('我发起攻击', user_id=ROOT_QQ))
        await h.settle()

        assert len(client.chat.completions.calls) == 1
        system = client.chat.completions.calls[0]['messages'][0]['content']
        assert 'd20+3' in system
        assert '掷骰' in system
        # 结果已消费
        assert plugin.data['sessions']['user'][ROOT_QQ]['pending_rolls'] == []


async def test_roll_outside_session_not_buffered(tmp_path, monkeypatch):
    """未开团的会话掷骰不缓存、不影响后续"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        await h.inject(private_message('/trpg roll 1d20', user_id=ROOT_QQ))
        await h.settle()
        assert plugin.data['sessions']['user'].get(ROOT_QQ) is None


async def test_duplicate_roll_rejected_until_action(tmp_path, monkeypatch):
    """同一玩家在结果被消费前不能重复投掷；提交行动后恢复"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, ["主持人回应"])
        await _start(h)

        await h.inject(private_message('/trpg roll 1d20', user_id=ROOT_QQ))
        await h.settle()
        assert len(plugin.data['sessions']['user'][ROOT_QQ]['pending_rolls']) == 1

        # 第二次掷骰被拒绝，未新增缓存
        h.reset_api()
        await h.inject(private_message('/trpg roll 1d20', user_id=ROOT_QQ))
        await h.settle()
        assert len(plugin.data['sessions']['user'][ROOT_QQ]['pending_rolls']) == 1
        h.assert_api('send_private_msg').called().with_text('未结算')

        # 提交行动消费结果后可再次掷骰
        await h.inject(private_message('我发起攻击', user_id=ROOT_QQ))
        await h.settle()
        assert plugin.data['sessions']['user'][ROOT_QQ]['pending_rolls'] == []

        await h.inject(private_message('/trpg roll 1d20', user_id=ROOT_QQ))
        await h.settle()
        assert len(plugin.data['sessions']['user'][ROOT_QQ]['pending_rolls']) == 1


async def test_multiple_players_rolls_accumulate(tmp_path, monkeypatch):
    """不同玩家可各自持有一条未结算掷骰，行动时一并注入 system prompt"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, ["结果如上。"])

        # root 开团（群内需管理员）
        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('/trpg roll 1d20', group_id=GROUP_ID, user_id='111'))
        await h.settle()
        await h.inject(group_message('/trpg roll 1d100 <= 50', group_id=GROUP_ID, user_id='222'))
        await h.settle()
        await h.inject(group_message('/trpg rh 1d20', group_id=GROUP_ID, user_id='333'))
        await h.settle()

        assert len(plugin.data['sessions']['group'][GROUP_ID]['pending_rolls']) == 3

        await h.inject(group_message('/trpg act 我继续前进', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        system = client.chat.completions.calls[0]['messages'][0]['content']
        assert 'd20' in system
        assert 'd100' in system
        assert '暗骰' in system
        assert '请勿向其他玩家透露' in system
        assert plugin.data['sessions']['group'][GROUP_ID]['pending_rolls'] == []


async def test_reset_clears_pending_rolls(tmp_path, monkeypatch):
    """重置清空待传递掷骰结果"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, [])
        await _start(h)

        await h.inject(private_message('/trpg roll 1d20', user_id=ROOT_QQ))
        await h.settle()
        assert len(plugin.data['sessions']['user'][ROOT_QQ]['pending_rolls']) == 1

        await h.inject(private_message('/trpg reset', user_id=ROOT_QQ))
        await h.settle()
        assert plugin.data['sessions']['user'][ROOT_QQ]['pending_rolls'] == []
