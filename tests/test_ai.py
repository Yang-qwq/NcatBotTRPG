# -*- coding: utf-8 -*-
"""AI 主持链路测试：配置门控、私聊/群聊行动、局内关键词。

用假 LLM 客户端替换 ``plugin.llm._client``，禁止真实网络请求。
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
SELF_ID = "10001"  # 与 ncatbot_test_config.yaml 的 bot_uin 一致


def _harness() -> PluginTestHarness:
    return PluginTestHarness(plugin_names=[PLUGIN_NAME], plugins_dir=PLUGINS_DIR)


def _configure(plugin, replies):
    """开启配置并注入假 LLM 客户端（不持久化）。"""
    plugin.config["IsConfigured"] = True
    client = FakeLLMClient(replies)
    plugin.llm._client = client
    return client


async def test_private_start_and_action(tmp_path, monkeypatch):
    """私聊开团后可对话：system + 历史随请求发送，并写入会话历史"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, ["欢迎来到无名小镇。"])

        await h.inject(private_message('/trpg start 克苏鲁小镇', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').with_text('已开启跑团')

        h.reset_api()
        await h.inject(private_message('我推开酒馆的木门', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').called().with_text('欢迎来到无名小镇')

        session = plugin.data['sessions']['user'][ROOT_QQ]
        assert [m['role'] for m in session['history']] == ['user', 'assistant']

        sent = client.chat.completions.calls[0]['messages']
        assert sent[0]['role'] == 'system'
        assert any(
            m['role'] == 'user' and '我推开酒馆的木门' in m['content'] for m in sent
        )


async def test_group_action_requires_at_bot(tmp_path, monkeypatch):
    """群聊 MustAtBot=True：未 @机器人 不触发，@ 后触发"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, ["你看到昏暗的房间。"])

        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        # 未 @机器人：不触发
        await h.inject(group_message('我观察四周', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        assert len(client.chat.completions.calls) == 0
        h.assert_api('send_group_msg').not_called()

        # @机器人：触发并回复
        await h.inject(group_message(
            '我观察四周',
            group_id=GROUP_ID,
            user_id=ROOT_QQ,
            raw_message=f'[CQ:at,qq={SELF_ID}] 我观察四周',
            message=[
                {'type': 'at', 'data': {'qq': SELF_ID}},
                {'type': 'text', 'data': {'text': ' 我观察四周'}},
            ],
        ))
        await h.settle()
        assert len(client.chat.completions.calls) == 1
        h.assert_api('send_group_msg').called().with_text('你看到昏暗的房间')


async def test_start_rejected_when_unconfigured(tmp_path, monkeypatch):
    """IsConfigured=False 时开团被拒绝"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
        await h.settle()
        h.assert_api('send_private_msg').called().with_text('尚未配置')


async def test_keyword_dice_does_not_call_llm(tmp_path, monkeypatch):
    """团激活后 `.r` 关键词触发掷骰，不进入 AI 主持"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, [])

        await h.inject(group_message('/trpg start', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(group_message('.r 1d20', group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()

        h.assert_api('send_group_msg').called().with_text('🎲')
        assert len(client.chat.completions.calls) == 0


async def test_command_prefix_skips_action(tmp_path, monkeypatch):
    """以 / 开头的消息走命令处理，不进入 AI 主持"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        client = _configure(plugin, [])

        await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(private_message('/trpg status', user_id=ROOT_QQ))
        await h.settle()

        h.assert_api('send_private_msg').called().with_text('跑团状态')
        assert len(client.chat.completions.calls) == 0


async def test_reasoning_stripped_from_reply_and_history(tmp_path, monkeypatch):
    """推理模型的思维链不会发送给用户，也不会写入会话历史"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, ['<thinking>先想一下怎么描述</thinking>你看到一间昏暗的酒馆。'])

        await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(private_message('我观察四周', user_id=ROOT_QQ))
        await h.settle()

        texts = [extract_text(call) for call in h.assert_api('send_private_msg').calls]
        assert any('你看到一间昏暗的酒馆' in t for t in texts)
        assert all('先想一下怎么描述' not in t for t in texts)

        history = plugin.data['sessions']['user'][ROOT_QQ]['history']
        assert history[-1]['role'] == 'assistant'
        assert history[-1]['content'] == '你看到一间昏暗的酒馆。'


async def test_reasoning_only_reply_falls_back(tmp_path, monkeypatch):
    """模型仅返回思维链时，回复兜底文案而非暴露思维链"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, ['<thinking>这里没有正文</thinking>'])

        await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(private_message('我观察四周', user_id=ROOT_QQ))
        await h.settle()

        h.assert_api('send_private_msg').called().with_text('主持人似乎陷入了沉默')
        texts = [extract_text(call) for call in h.assert_api('send_private_msg').calls]
        assert all('这里没有正文' not in t for t in texts)


async def test_orphan_reasoning_close_stripped(tmp_path, monkeypatch):
    """开标签被剥离只剩 </think> 时，其前的思维链不发送给用户"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _configure(plugin, ['我先推理场景再作答</think>你看到一间昏暗的酒馆。'])

        await h.inject(private_message('/trpg start', user_id=ROOT_QQ))
        await h.settle()
        h.reset_api()

        await h.inject(private_message('我观察四周', user_id=ROOT_QQ))
        await h.settle()

        texts = [extract_text(call) for call in h.assert_api('send_private_msg').calls]
        assert any('你看到一间昏暗的酒馆' in t for t in texts)
        assert all('我先推理场景再作答' not in t for t in texts)
