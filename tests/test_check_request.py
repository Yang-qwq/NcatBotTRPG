# -*- coding: utf-8 -*-
"""检定下发链路测试：request_check -> 玩家掷骰结算 -> 注入主持人。

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness
from ncatbot.testing import extract_text
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


def _request_reply(content, target, expression, difficulty, reason, hidden=False):
    return {
        'content': content,
        'tool_calls': [{
            'id': 'c1', 'name': 'request_check',
            'arguments': {'target': target, 'expression': expression,
                          'difficulty': difficulty, 'reason': reason, 'hidden': hidden},
        }],
    }


async def test_group_check_request_then_roll_resolves(tmp_path, monkeypatch):
    """群内下发检定 -> 玩家掷骰匹配 -> resolved 并注入下一次行动"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, [
            _request_reply('你需要仔细搜索。', ROOT_QQ, '1d100', '<=50', '侦查检定'),
            '你发现了一道暗门。',
        ])

        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('/trpg act 我搜索房间', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        texts = [extract_text(call) for call in h.assert_api('send_group_msg').calls]
        assert any('检定请求' in t and '侦查检定' in t for t in texts)
        assert any('.r 1d100 <=50' in t for t in texts)

        checks = plugin.data['sessions']['group'][GROUP_ID]['pending_checks']
        assert len(checks) == 1
        assert checks[0]['status'] == 'awaiting'
        assert checks[0]['roll_key'] == '1d100'

        # 玩家按提示掷骰 → 结算，且玩家端可见系统裁定的成败
        h.reset_api()
        await h.inject(group_message('/trpg roll 1d100', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        assert checks[0]['status'] == 'resolved'
        assert checks[0]['verdict'] in ('成功', '失败')
        roll_text = ' '.join(extract_text(c) for c in h.assert_api('send_group_msg').calls)
        assert '侦查检定' in roll_text
        assert ('成功' in roll_text or '失败' in roll_text)

        # 下一次行动：system 注入检定结果
        await h.inject(group_message('/trpg act 我推开暗门', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        system = client.chat.completions.calls[1]['messages'][0]['content']
        assert '系统检定结果' in system
        assert '侦查检定' in system

        # 已消费
        assert plugin.data['sessions']['group'][GROUP_ID]['resolved_checks'] == []


async def test_terminal_request_without_content_does_not_fall_back(tmp_path, monkeypatch):
    """下发检定且模型无正文时，不补“沉默”文案"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, [
            _request_reply('', ROOT_QQ, '1d20', '>=12', '力量检定'),
        ])
        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('/trpg act 我推门', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        texts = [extract_text(call) for call in h.assert_api('send_group_msg').calls]
        assert any('检定请求' in t for t in texts)
        assert all('沉默' not in t for t in texts)


async def test_request_check_requires_difficulty(tmp_path, monkeypatch):
    """缺少 difficulty 时工具返回错误并回灌，模型补齐后才下发（避免无法裁定）。"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, [
            {'content': '', 'tool_calls': [{'id': 'c1', 'name': 'request_check',
                'arguments': {'target': ROOT_QQ, 'expression': 'd20', 'reason': '力量检定'}}]},
            {'content': '', 'tool_calls': [{'id': 'c2', 'name': 'request_check',
                'arguments': {'target': ROOT_QQ, 'expression': 'd20',
                              'difficulty': '>=12', 'reason': '力量检定'}}]},
        ])
        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('/trpg act 我推门', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        checks = plugin.data['sessions']['group'][GROUP_ID]['pending_checks']
        assert len(checks) == 1
        assert checks[0]['op'] == '>=' and checks[0]['target'] == 12

        # 第一轮的错误以 role:tool 回灌，模型据此补齐 difficulty
        second_round = client.chat.completions.calls[1]['messages']
        assert any(m['role'] == 'tool' and 'difficulty' in m['content'] for m in second_round)


async def test_hidden_check_delivered_privately(tmp_path, monkeypatch):
    """暗骰检定走私聊下发"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, [
            _request_reply('你悄悄潜入。', ROOT_QQ, '1d100', '<=40', '潜行检定', hidden=True),
        ])
        await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(private_message('我潜行过去', user_id=ROOT_QQ))
        await h.settle()

        texts = [extract_text(call) for call in h.assert_api('send_private_msg').calls]
        assert any('检定请求' in t and '潜行检定' in t for t in texts)


async def test_npc_check_delegated_to_participant(tmp_path, monkeypatch):
    """NPC 检定随机指定在场玩家代掷"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, [
            _request_reply('哥布林发起攻击。', '哥布林', '1d20', '>=12', '哥布林攻击'),
        ])
        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('/trpg act 我迎战', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        texts = [extract_text(call) for call in h.assert_api('send_group_msg').calls]
        assert any('检定请求' in t and '代' in t for t in texts)
        checks = plugin.data['sessions']['group'][GROUP_ID]['pending_checks']
        assert checks and checks[0]['is_npc'] is True
        assert checks[0]['subject'] == '哥布林'


def _segments(call):
    """取出群消息 APICall 的消息段列表（兼容 message list 与 MessageArray）。"""
    msg = call.params.get('message')
    if isinstance(msg, list):
        return msg
    msg = call.params.get('msg')
    if hasattr(msg, 'to_list'):
        return msg.to_list()
    return []


async def test_check_request_mention_precedes_text(tmp_path, monkeypatch):
    """检定下发时 @ 段应位于文本之前，末尾不得再冒出多余 At。"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, [
            _request_reply('你需要仔细搜索。', ROOT_QQ, '1d100', '<=50', '侦查检定'),
        ])
        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('/trpg act 我搜索房间', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        at_calls = [c for c in h.assert_api('send_group_msg').calls
                    if any(seg.get('type') == 'at' for seg in _segments(c))]
        assert at_calls, '检定请求应包含 @ 段落'

        segs = _segments(at_calls[0])
        types = [seg.get('type') for seg in segs]
        assert types.count('at') == 1
        at_index = types.index('at')
        assert str(segs[at_index]['data']['qq']) == ROOT_QQ
        text_indices = [i for i, t in enumerate(types) if t == 'text']
        assert text_indices and at_index < min(text_indices), f'@ 应位于文本之前: {types}'
        assert 'at' not in types[at_index + 1:]


async def test_unmatched_roll_does_not_resolve_check(tmp_path, monkeypatch):
    """骰式不匹配时检定保持 awaiting"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, [
            _request_reply('请掷 d100。', ROOT_QQ, '1d100', '<=50', '侦查检定'),
        ])
        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('/trpg act 我搜索', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        await h.inject(group_message('/trpg roll 1d20', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        checks = plugin.data['sessions']['group'][GROUP_ID]['pending_checks']
        assert checks[0]['status'] == 'awaiting'


async def test_check_target_nickname_with_qq_resolved_as_player(tmp_path, monkeypatch):
    """target 写成「昵称(QQ)」也能解析为玩家本人，不误判为 NPC。"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, [
            _request_reply('开始环境观察。', f'阳({ROOT_QQ})', '1d100', '<=60', '环境观察检定'),
        ])
        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('/trpg act 继续', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        checks = plugin.data['sessions']['group'][GROUP_ID]['pending_checks']
        assert checks[0]['is_npc'] is False
        assert checks[0]['roller_id'] == ROOT_QQ
        assert checks[0]['subject'] == f'玩家{ROOT_QQ}'

        texts = [extract_text(c) for c in h.assert_api('send_group_msg').calls]
        request_texts = [t for t in texts if '检定请求' in t]
        assert request_texts and all('代' not in t for t in request_texts)
        # 玩家本人应被 @
        assert any(any(seg.get('type') == 'at' for seg in _segments(c))
                   for c in h.assert_api('send_group_msg').calls)
