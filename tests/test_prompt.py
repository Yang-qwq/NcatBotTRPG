# -*- coding: utf-8 -*-
"""主持人提示词导入机制测试（PromptFile）。

验证：
1. 配置文件指定 PromptFile 后，导入的提示词作为 system 消息发给 LLM
2. 文件缺失 / 为空时回退内置默认提示词，不阻塞
3. 本团专属设定追加在导入的默认提示词之后

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness
from ncatbot.testing.factories.qq import private_message

from fake_llm import FakeLLMClient

pytestmark = pytest.mark.asyncio(mode="strict")

PLUGINS_DIR = Path(__file__).resolve().parents[2]
PLUGIN_DIR = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "NcatBotTRPG"
ROOT_QQ = "123456"
FIXTURE_PROMPT = PLUGIN_DIR / "tests" / "fixtures" / "example_prompt.md"
MARKER = "TEST-ONLY-KEEPER-PROMPT"


def _harness() -> PluginTestHarness:
    return PluginTestHarness(plugin_names=[PLUGIN_NAME], plugins_dir=PLUGINS_DIR)


def _configure(plugin, replies):
    plugin.config["IsConfigured"] = True
    client = FakeLLMClient(replies)
    plugin.llm._client = client
    return client


async def _run_one_action(h, plugin, replies, start_args=''):
    """开团 → 一次行动，返回假 LLM 收到的 system 消息内容。"""
    client = _configure(plugin, replies)
    await h.inject(private_message(f'/trpg start {start_args}'.strip(), user_id=ROOT_QQ))
    await h.settle()
    await h.inject(private_message('我向前走了一步', user_id=ROOT_QQ))
    await h.settle()
    return client.chat.completions.calls[0]['messages'][0]['content']


async def test_prompt_file_imported_and_used(tmp_path, monkeypatch):
    """配置 PromptFile 后，导入的提示词进入 system 消息"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        plugin.config['PromptFile'] = str(FIXTURE_PROMPT)
        assert plugin.reload_prompt() is True

        system_content = await _run_one_action(h, plugin, ['测试主持人在线'])
        assert MARKER in system_content


async def test_prompt_file_missing_falls_back_to_builtin(tmp_path, monkeypatch):
    """PromptFile 缺失时回退内置默认提示词"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        plugin.config['PromptFile'] = str(tmp_path / 'not_exist.md')
        assert plugin.reload_prompt() is False

        system_content = await _run_one_action(h, plugin, ['好的'])
        assert '你是一名专业的 TRPG' in system_content
        assert MARKER not in system_content


async def test_imported_prompt_with_custom_session_setting(tmp_path, monkeypatch):
    """导入的默认提示词之后追加本团专属设定"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        plugin.config['PromptFile'] = str(FIXTURE_PROMPT)
        plugin.reload_prompt()

        system_content = await _run_one_action(
            h, plugin, ['好的'], start_args='迷雾笼罩的港口小镇'
        )
        assert MARKER in system_content
        assert '【本团专属设定】' in system_content
        assert '迷雾笼罩的港口小镇' in system_content
