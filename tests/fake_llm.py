# -*- coding: utf-8 -*-
"""LLM 客户端 stub：隔离真实网络，驱动 AI 主持链路测试。"""


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """记录调用参数并按脚本依次返回响应。"""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._replies.pop(0) if self._replies else ""
        return _FakeResponse(content)


class _FakeChat:
    def __init__(self, replies):
        self.completions = _FakeCompletions(replies)


class FakeLLMClient:
    """替换 ``plugin.llm._client`` 的假客户端。"""

    def __init__(self, replies=None):
        self.chat = _FakeChat(replies or [])
