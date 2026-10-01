# -*- coding: utf-8 -*-
"""LLM 客户端 stub：隔离真实网络，驱动 AI 主持与工具调用链路测试。

每个脚本项可以是：
- ``str``：助手正文，无工具调用（finish_reason='stop'）
- ``dict``：``{'content': str, 'tool_calls': [...], 'finish_reason': 'tool_calls'}``
  其中 ``tool_calls`` 每项为 ``{'id', 'name', 'arguments'}``（arguments 可为 dict 或 JSON 串）
"""


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = 'function'
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content, tool_calls=None, reasoning_content=None):
        self.content = content
        self.tool_calls = tool_calls or None
        self.reasoning_content = reasoning_content


class _FakeChoice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, message, finish_reason):
        self.choices = [_FakeChoice(message, finish_reason)]


def _build_tool_calls(items):
    calls = []
    for index, item in enumerate(items or [], 1):
        arguments = item.get('arguments', {})
        if not isinstance(arguments, str):
            import json
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append(_FakeToolCall(item.get('id') or f'call_{index}',
                                   item.get('name', ''), arguments))
    return calls


def _build_response(reply):
    """把脚本项转为一个假响应。"""
    if isinstance(reply, str):
        return _FakeResponse(_FakeMessage(reply), 'stop')
    if isinstance(reply, dict):
        tool_calls = _build_tool_calls(reply.get('tool_calls'))
        finish = reply.get('finish_reason') or ('tool_calls' if tool_calls else 'stop')
        message = _FakeMessage(reply.get('content'), tool_calls,
                               reply.get('reasoning_content'))
        return _FakeResponse(message, finish)
    return _FakeResponse(_FakeMessage(str(reply)), 'stop')


class _FakeCompletions:
    """记录调用参数并按脚本依次返回响应。"""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self._replies.pop(0) if self._replies else ''
        return _build_response(reply)


class _FakeChat:
    def __init__(self, replies):
        self.completions = _FakeCompletions(replies)


class FakeLLMClient:
    """替换 ``plugin.llm._client`` 的假客户端。"""

    def __init__(self, replies=None):
        self.chat = _FakeChat(replies or [])
