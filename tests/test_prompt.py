# -*- coding: utf-8 -*-
"""主持人提示词模板机制测试（prompts.yaml）。

验证：
1. 配置 PromptConfigFile 指向 prompts.yaml 后，激活的提示词作为 system 消息发给 LLM
2. 文件缺失时回退内置默认提示词，不阻塞
3. ActivePrompt 切换后 system 消息随之变化
4. 本团专属设定追加在提示词之后

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
FIXTURE_PROMPTS = PLUGIN_DIR / "tests" / "fixtures" / "prompts.yaml"
MARKER = "TEST-ONLY-KEEPER-PROMPT"
LITERARY_MARKER = "TEST-ONLY-LITERARY-PROMPT"


def _harness() -> PluginTestHarness:
    return PluginTestHarness(plugin_names=[PLUGIN_NAME], plugins_dir=PLUGINS_DIR)


def _no_persist(plugin):
    """提示词切换会调用 set_config 持久化，测试中改为仅内存，避免写回真实配置。"""
    plugin.set_config = lambda key, value: plugin.config.__setitem__(key, value)


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


async def test_prompts_config_imported_and_used(tmp_path, monkeypatch):
    """配置 PromptConfigFile 后，导入的提示词进入 system 消息"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _no_persist(plugin)
        plugin.config['PromptConfigFile'] = str(FIXTURE_PROMPTS)
        plugin.config['ActivePrompt'] = 'default'
        assert plugin.reload_prompt() is True

        system_content = await _run_one_action(h, plugin, ['测试主持人在线'])
        assert MARKER in system_content


async def test_prompts_config_missing_falls_back_to_builtin(tmp_path, monkeypatch):
    """提示词配置缺失时回退内置默认提示词"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _no_persist(plugin)
        plugin.config['PromptConfigFile'] = str(tmp_path / 'not_exist.yaml')
        plugin.config['ActivePrompt'] = 'default'
        assert plugin.reload_prompt() is False

        system_content = await _run_one_action(h, plugin, ['好的'])
        assert '你是一名专业的 TRPG' in system_content
        assert MARKER not in system_content


async def test_active_prompt_switch(tmp_path, monkeypatch):
    """切换 ActivePrompt 后 system 消息使用新提示词"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _no_persist(plugin)
        plugin.config['PromptConfigFile'] = str(FIXTURE_PROMPTS)
        plugin.config['ActivePrompt'] = 'default'
        plugin.reload_prompt()

        assert plugin.set_active_prompt('literary') is True
        system_content = await _run_one_action(h, plugin, ['好的'])
        assert LITERARY_MARKER in system_content
        assert MARKER not in system_content

        # 还原共享测试配置，避免污染其它用例
        plugin.set_active_prompt('default')


async def test_imported_prompt_with_custom_session_setting(tmp_path, monkeypatch):
    """导入的默认提示词之后追加本团专属设定"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        _no_persist(plugin)
        plugin.config['PromptConfigFile'] = str(FIXTURE_PROMPTS)
        plugin.config['ActivePrompt'] = 'default'
        plugin.reload_prompt()

        system_content = await _run_one_action(
            h, plugin, ['好的'], start_args='迷雾笼罩的港口小镇'
        )
        assert MARKER in system_content
        assert '【本团专属设定】' in system_content
        assert '迷雾笼罩的港口小镇' in system_content
