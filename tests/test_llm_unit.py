# -*- coding: utf-8 -*-
"""LLM 消息规整纯函数测试（无框架/网络）。

运行：python -m pytest tests -v -o "addopts="
"""
import sys
from pathlib import Path

PLUGINS_DIR = Path(__file__).resolve().parents[2]
if str(PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGINS_DIR))

from NcatBotTRPG.llm import normalize_messages, repair_tool_message_pairs  # noqa: E402


def test_normalize_merges_system_into_single_leading():
    """多条 system（含历史中插入的）合并为唯一置顶 system。"""
    messages = [
        {'role': 'system', 'content': '主持提示词'},
        {'role': 'user', 'content': '行动'},
        {'role': 'assistant', 'content': '回应'},
        {'role': 'system', 'content': 'cheat 注入'},
    ]
    result = normalize_messages(messages)
    assert result[0]['role'] == 'system'
    assert result[0]['content'] == '主持提示词\n\ncheat 注入'
    assert [m['role'] for m in result] == ['system', 'user', 'assistant']


def test_normalize_without_system_is_unchanged():
    """无 system 时保持原样（新列表）。"""
    messages = [{'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b'}]
    result = normalize_messages(messages)
    assert result == messages
    assert result is not messages


def test_normalize_drops_empty_system():
    """空 system 内容被丢弃。"""
    messages = [{'role': 'system', 'content': '  '}, {'role': 'user', 'content': 'a'}]
    result = normalize_messages(messages)
    assert [m['role'] for m in result] == ['user']


def test_repair_fills_missing_tool_result():
    """assistant.tool_calls 缺失对应 tool 结果时补占位，保持协议顺序。"""
    messages = [
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'c1', 'type': 'function', 'function': {'name': 'f', 'arguments': '{}'}}]},
    ]
    result = repair_tool_message_pairs(messages)
    assert result[1]['role'] == 'tool'
    assert result[1]['tool_call_id'] == 'c1'
