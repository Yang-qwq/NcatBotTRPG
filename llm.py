# -*- coding: utf-8 -*-
"""AI 主持人 LLM 客户端封装

基于 openai SDK 的 OpenAI 兼容接口，作为可替换单元，未来可切换为
NcatBot 内置 AI 适配器（``api.ai``）或 diceframe 后端而不改动命令层。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Callable, List, Optional

from ncatbot.utils.logger import get_log
from openai import OpenAI

_log = get_log('ncatbot_trpg')

# 日志中省略的文本长度（与其它插件保持一致）
OMITTED_TEXT_LENGTH = 100

# 推理模型思维链片段的正则（避免将思维链暴露给用户）
# 1) 常见成对标签：<think>…</think>、<thinking>…</thinking>、<reasoning>…</reasoning> 等
_REASONING_TAGS = ('think', 'thinking', 'reasoning', 'thought', 'analysis')
_REASONING_BLOCK_RES = [
    re.compile(rf'<{tag}\b[^>]*>.*?</{tag}\s*>', re.IGNORECASE | re.DOTALL)
    for tag in _REASONING_TAGS
]
# 2) 未闭合的思维链标签：连同其后的全部内容一并移除，宁缺毋滥
_REASONING_OPEN_RES = [
    re.compile(rf'<{tag}\b[^>]*>.*$', re.IGNORECASE | re.DOTALL)
    for tag in _REASONING_TAGS
]
# 3) DeepSeek-R1 风格全角分隔符：<｜begin▁of▁thinking｜>…<｜end▁of▁thinking｜>
_REASONING_SPECIAL_RE = re.compile(
    r'<[|｜][^>]*begin[^>]*thinking[^>]*>.*?<[|｜][^>]*end[^>]*thinking[^>]*>',
    re.IGNORECASE | re.DOTALL,
)
_REASONING_SPECIAL_OPEN_RE = re.compile(
    r'<[|｜][^>]*begin[^>]*thinking[^>]*>.*$',
    re.IGNORECASE | re.DOTALL,
)
# 4) 孤儿闭标签（缺少开标签）：provider 可能已剥离开标签，只剩 </think> / <｜end▁of▁thinking｜>
_ORPHAN_CLOSE_RE = re.compile(
    r'</(?:think(?:ing)?|reasoning|thought|analysis)\s*>', re.IGNORECASE
)
_ORPHAN_CLOSE_DS_RE = re.compile(
    r'<[|｜][^>]*end[^>]*thinking[^>]*>', re.IGNORECASE
)
# 去除思维链标签，仅保留思考内容（用于日志提取）
_TAG_STRIP_RE = re.compile(r'<[^>]*>')


def _apply_collect(text: str, pattern, chunks: list) -> str:
    """移除匹配片段，并把被移除的内容收集到 ``chunks`` 中。

    :param text: 待处理文本
    :param pattern: 待移除的正则
    :param chunks: 收集被移除片段的列表
    :return: 移除后的文本
    """

    def _repl(match):
        chunks.append(match.group(0))
        return ''

    return pattern.sub(_repl, text)


def _reasoning_passes(text: str):
    """执行一次思维链剥离，返回 ``(剥离后文本, 思维链片段列表)``。

    剥离逻辑与 :func:`strip_reasoning` 完全一致，同时收集被移除的片段，
    供 :func:`extract_reasoning` 记录日志使用。

    :param text: 模型返回的原始文本
    :return: (剥离后文本, 思维链片段列表)
    """
    if not text:
        return text, []
    chunks: list = []
    for pattern in _REASONING_BLOCK_RES:
        text = _apply_collect(text, pattern, chunks)
    text = _apply_collect(text, _REASONING_SPECIAL_RE, chunks)
    for pattern in _REASONING_OPEN_RES:
        text = _apply_collect(text, pattern, chunks)
    text = _apply_collect(text, _REASONING_SPECIAL_OPEN_RE, chunks)
    # 孤儿闭标签：之后有正文时，其前内容视为思维链一并移除；否则仅移除标签
    for pattern in (_ORPHAN_CLOSE_RE, _ORPHAN_CLOSE_DS_RE):
        while True:
            match = pattern.search(text)
            if match is None:
                break
            after = text[match.end():]
            if after.strip():
                chunks.append(text[:match.end()])
                text = after
            else:
                chunks.append(match.group(0))
                text = pattern.sub('', text)
                break
    return text, chunks


def strip_reasoning(text: str) -> str:
    """移除推理模型的思维链片段，仅保留对用户可见的回答。

    支持常见形式：``<think>…</think>``、``<thinking>…</thinking>``、
    ``<reasoning>…</reasoning>``（及 thought / analysis），以及 DeepSeek-R1 的
    全角分隔符 ``<｜begin▁of▁thinking｜>…<｜end▁of▁thinking｜>``。
    未闭合的开标签会连同其后内容一并移除；缺少开标签的孤儿闭标签（如 provider
    仅返回 ``</think>``）会连同其前的思维链残留一并移除。

    :param text: 模型返回的原始文本
    :return: 移除思维链后的文本
    """
    if not text:
        return text
    result, _ = _reasoning_passes(text)
    result = result.strip()
    if result != text:
        _log.debug(f'已移除思维链片段（{len(text) - len(result)} 字）')
    return result


def extract_reasoning(text: str) -> str:
    """提取推理模型返回文本中的思维链内容（用于日志记录，不发送给用户）。

    :param text: 模型返回的原始文本
    :return: 去除标签后的思维链文本（无思维链时为空串）
    """
    if not text:
        return ''
    _, chunks = _reasoning_passes(text)
    cleaned = []
    for chunk in chunks:
        # 去掉 <think> / </think> / DeepSeek 全角分隔符等标签，仅保留思考内容
        inner = _TAG_STRIP_RE.sub(' ', chunk).strip()
        if inner:
            cleaned.append(inner)
    return '\n'.join(cleaned)




# 群聊中区分不同玩家发言时使用的消息前缀格式
USER_PREFIX_FORMAT = '{name}({user_id})：{text}'

# 启用工具调用时追加到 system 的硬约束（数值权威：系统裁定、LLM 只叙事）
TOOL_GUIDANCE = '''

【工具使用规范（必须遵守）】
1. 任何需要随机结果的行动，必须调用 request_check 向玩家下发检定，由玩家掷骰；
   严禁自行编造骰值、成功或失败。
   下发检定时**必须给出 difficulty 目标值**（如 '<=50' / '>=15'），否则系统无法裁定成败。
2. 需要修改角色数值、物品或场景时，必须调用对应的写工具；不得只在叙事中声称。
3. request_check 下发后本回合立即结束，等待玩家掷骰后再继续。
4. 掷骰结果与检定成败由系统给出，只能据其结果叙事，不得重掷或改判。
5. 工具返回 error 时按提示修正后重试，不要向玩家暴露工具细节。'''

# 内置默认主持人提示词
DEFAULT_KEEPER_PROMPT = '''你是一名专业的 TRPG（桌上角色扮演游戏）主持人（GM/KP）。

你的职责：
1. 用生动、简洁的中文描述场景、NPC 与剧情进展，营造沉浸感。
2. 扮演所有非玩家角色（NPC），根据其性格与动机做出合理反应。
3. 根据玩家的行动推进故事，给出明确的环境反馈与可行动的线索。
4. 不替玩家做决定、不代玩家发言，只描述玩家行动带来的结果。
5. 当行动结果存在不确定性时，提示玩家进行检定（例如：请掷 1d100 进行侦查检定），但绝不自行编造骰子结果。
6. 保持剧情连贯，遵守“本团专属设定”，不与玩家进行游戏外的元讨论。
7. 每次回复聚焦当前场景，长度适中（通常 2~6 句），必要时给出 2~3 个可选行动方向。'''


def build_system_prompt(custom_prompt: str = '',
                        default_prompt: Optional[str] = None,
                        plugin=None) -> str:
    """拼接主持人 system 提示词。

    优先级：通过插件获取当前激活的提示词 > ``default_prompt``；
    其后追加可选的"本团专属设定"。

    :param custom_prompt: 本团专属设定文本（可为空）
    :param default_prompt: 通过配置导入的默认提示词（可为空）
    :param plugin: 插件实例（用于获取当前激活的提示词）
    :return: 完整 system 提示词
    """
    # 优先取插件当前激活的提示词；失败/缺失时回退 default_prompt，再回退内置默认
    base = ''
    if plugin:
        try:
            base = plugin.get_active_prompt()
        except Exception:
            base = ''

    base = (base or default_prompt or '').strip() or DEFAULT_KEEPER_PROMPT
    custom_prompt = (custom_prompt or '').strip()
    if custom_prompt:
        return f'{base}\n\n【本团专属设定】\n{custom_prompt}'
    return base


def build_roll_context(rolls: List[str]) -> str:
    """将待传递的掷骰结果拼装为 system 提示词片段。

    在玩家提交行动时，把此前产生的掷骰类型与结果通过 system prompt 交给
    主持人，使其能据此推进剧情，避免 LLM 因看不到骰值而无法叙事。

    :param rolls: 掷骰结果摘要列表（由 ``SessionStore.peek_pending_rolls`` 提供）
    :return: 追加到 system 提示词末尾的文本；无内容时返回空串
    """
    items = [str(r).strip() for r in (rolls or []) if str(r).strip()]
    if not items:
        return ''
    lines = '\n'.join(f'- {item}' for item in items)
    return (
        '\n\n【最近掷骰结果（由系统产生，请据此推进剧情与判定，'
        '不要自行编造或修改骰值；暗骰结果不得向其他玩家透露具体数值）】\n'
        f'{lines}'
    )


class LLMClient:
    """OpenAI 兼容的 LLM 客户端封装。"""

    def __init__(self, plugin):
        """初始化并依据插件配置构造底层客户端。

        :param plugin: 插件实例（提供 get_config）
        """
        self._plugin = plugin
        self._client: Optional[OpenAI] = None
        self.rebuild()

    def rebuild(self) -> None:
        """按当前配置重建底层 openai 客户端（配置变更后可调用）。"""
        self._client = OpenAI(
            api_key=self._plugin.get_config('ApiKey'),
            base_url=self._plugin.get_config('BaseUrl'),
        )
        _log.debug('已重建 LLM 客户端')

    @property
    def client(self) -> OpenAI:
        """获取底层客户端（懒加载）。"""
        if self._client is None:
            self.rebuild()
        return self._client

    async def chat(
        self,
        messages: List[dict],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[dict]] = None,
        tool_executor: Optional[Callable] = None,
        max_tool_rounds: int = 5,
        max_tool_calls_per_round: int = 4,
    ) -> str:
        """发起 Chat Completion，返回助手文本；可选的工具调用循环。

        :param messages: OpenAI 格式的消息列表
        :param model: 覆盖默认模型
        :param temperature: 采样温度
        :param max_tokens: 最大生成 token 数
        :param tools: 暴露给模型的工具 schema；为空则单次调用
        :param tool_executor: ``async (name, args) -> (result_str, terminal)``
        :param max_tool_rounds: 工具循环最大轮数
        :param max_tool_calls_per_round: 单轮最多执行的工具数
        :return: 助手回复文本（已去除思维链并 strip）
        """
        if not tools or tool_executor is None:
            return await self._chat_once(messages, model=model,
                                         temperature=temperature, max_tokens=max_tokens)
        return await self._chat_with_tools(
            messages, tools=tools, tool_executor=tool_executor,
            model=model, temperature=temperature, max_tokens=max_tokens,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls_per_round=max_tool_calls_per_round,
        )

    def _normalize(self, messages: List[dict]) -> List[dict]:
        """按 ``MergeSystemMessages`` 配置规整消息。

        - ``True``（默认）：把所有 system 合并为唯一置顶 system，兼容仅接受单条
          system 的厂商（如智谱 GLM）。代价是改变了 system 前缀，会破坏 prompt 缓存。
        - ``False``：保持 system 消息原样（缓存友好），要求后端允许多条/非置顶 system。
        """
        if self._plugin.get_config('MergeSystemMessages', True):
            return normalize_messages(list(messages))
        return list(messages)

    def _build_kwargs(self, messages: List[dict], *, model, temperature,
                      max_tokens, tools=None) -> dict:
        """组装底层 create 参数。"""
        kwargs = {
            'model': model or self._plugin.get_config('Model'),
            'messages': messages,
        }
        if temperature is not None:
            kwargs['temperature'] = temperature
        if max_tokens is not None:
            kwargs['max_tokens'] = max_tokens
        if tools:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = 'auto'
        return kwargs

    async def _call(self, kwargs: dict):
        """执行一次底层请求（同步 SDK 放入线程）。"""
        _log.debug(
            f'调用 LLM：model={kwargs["model"]}, messages={len(kwargs["messages"])}, '
            f'tools={len(kwargs.get("tools") or [])}'
        )
        create = self.client.chat.completions.create
        return await asyncio.to_thread(create, **kwargs)

    def _log_reasoning(self, message, content: str) -> None:
        """提取并打印思维链（不发送给用户）。"""
        reasoning = extract_reasoning(content)
        field_reasoning = getattr(message, 'reasoning_content', None)
        if field_reasoning:
            field_reasoning = str(field_reasoning).strip()
            reasoning = f'{reasoning}\n{field_reasoning}' if reasoning else field_reasoning
        if reasoning:
            _log.info(
                f'AI思维链: {reasoning[:OMITTED_TEXT_LENGTH]}'
                f'{"..." if len(reasoning) > OMITTED_TEXT_LENGTH else ""}'
            )
        if not content and not getattr(message, 'tool_calls', None):
            if reasoning:
                _log.warning('模型仅返回思维链而无正文，已忽略以避免暴露思维链')
            else:
                _log.warning('模型未返回任何正文内容')

    def _clean(self, content: str) -> str:
        """按配置剥离思维链并 strip。"""
        content = (content or '').strip()
        if self._plugin.get_config('StripReasoning', True):
            content = strip_reasoning(content)
        return content

    async def _chat_once(self, messages, *, model, temperature, max_tokens) -> str:
        """单次调用（无工具）。"""
        messages = self._normalize(messages)
        kwargs = self._build_kwargs(messages, model=model,
                                    temperature=temperature, max_tokens=max_tokens)
        response = await self._call(kwargs)
        message = response.choices[0].message
        content = (message.content or '').strip()
        self._log_reasoning(message, content)
        content = self._clean(content)
        _log.debug(f'LLM 返回 {len(content)} 字')
        return content

    async def _chat_with_tools(self, messages, *, tools, tool_executor, model,
                               temperature, max_tokens, max_tool_rounds,
                               max_tool_calls_per_round) -> str:
        """工具调用循环（ReAct）。

        每轮把 assistant(含 tool_calls) 与对应的 role:tool 结果追加到临时消息列表，
        直到模型不再请求工具、命中终止工具或达到轮数上限。中间消息不落库。
        """
        working = self._normalize(messages)
        seen: dict = {}  # 去重：name+sorted(args) -> result
        rounds = 0
        while True:
            working = repair_tool_message_pairs(working)
            kwargs = self._build_kwargs(working, model=model,
                                        temperature=temperature, max_tokens=max_tokens,
                                        tools=tools)
            response = await self._call(kwargs)
            choice = response.choices[0]
            message = choice.message
            content = (message.content or '').strip()
            tool_calls = getattr(message, 'tool_calls', None)
            finish_reason = getattr(choice, 'finish_reason', None)
            self._log_reasoning(message, content)

            if not tool_calls or finish_reason == 'stop':
                final = self._clean(content)
                _log.debug(f'LLM 返回 {len(final)} 字（工具轮 {rounds} 次）')
                return final

            rounds += 1
            if rounds > max_tool_rounds:
                _log.warning(f'工具调用达到上限 {max_tool_rounds}，结束本回合')
                return self._clean(content) or '（主持人思考了很久，请继续你的行动）'

            # 先追加 assistant（含 tool_calls），再追加工具结果（协议顺序）
            working.append(assistant_message_to_dict(message))
            terminal_hit = False
            for tool_call in list(tool_calls)[:max_tool_calls_per_round]:
                name = getattr(getattr(tool_call, 'function', None), 'name', '') or ''
                raw_args = getattr(getattr(tool_call, 'function', None), 'arguments', None) or '{}'
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except (json.JSONDecodeError, TypeError, ValueError):
                    result, terminal = _payload_error(f'参数 JSON 解析失败: {raw_args!r}'), False
                else:
                    key = name + '|' + json.dumps(args, sort_keys=True, ensure_ascii=False)
                    if key in seen:
                        result, terminal = '[重复调用已跳过] ' + seen[key], False
                    else:
                        try:
                            result, terminal = await tool_executor(name, args)
                        except Exception as e:  # noqa: BLE001 - 回灌给模型
                            _log.error(f'工具 {name} 执行异常: {e}')
                            result, terminal = _payload_error(f'工具执行失败: {e}'), False
                        seen[key] = result
                working.append({
                    'role': 'tool',
                    'tool_call_id': getattr(tool_call, 'id', None),
                    'name': name,
                    'content': result,
                })
                if terminal:
                    terminal_hit = True
            if terminal_hit:
                final = self._clean(content)
                _log.debug(f'命中终止工具，结束本回合（正文 {len(final)} 字）')
                return final


def _payload_error(message: str) -> str:
    """llm 内部使用的简单错误 payload（与 tools._payload 结构一致）。"""
    return json.dumps({'status': 'error', 'message': message}, ensure_ascii=False)


def assistant_message_to_dict(message) -> dict:
    """把 SDK 的 assistant 消息（含 tool_calls）转为可回传的 dict。"""
    entry = {'role': 'assistant', 'content': getattr(message, 'content', None) or ''}
    tool_calls = getattr(message, 'tool_calls', None)
    if tool_calls:
        entry['tool_calls'] = [
            {
                'id': getattr(tc, 'id', None),
                'type': getattr(tc, 'type', None) or 'function',
                'function': {
                    'name': getattr(getattr(tc, 'function', None), 'name', '') or '',
                    'arguments': getattr(getattr(tc, 'function', None), 'arguments', None) or '{}',
                },
            }
            for tc in tool_calls
        ]
    return entry


def normalize_messages(messages: List[dict]) -> List[dict]:
    """规整消息列表：把所有 ``system`` 消息合并为**唯一**的置顶 system。

    部分厂商（如智谱 GLM，错误码 1214「messages 参数非法」）只接受单条、位于
    开头的 system 消息。``cheat`` 命令会向历史插入 system 消息，故发送前统一
    合并，避免出现多条 system 或非置顶 system。

    :param messages: 原始消息列表
    :return: 规整后的新列表（无非 system 变化时原样返回拷贝）
    """
    system_parts: List[str] = []
    rest: List[dict] = []
    saw_system = False
    for message in messages:
        if message.get('role') == 'system':
            saw_system = True
            text = (message.get('content') or '')
            text = text.strip() if isinstance(text, str) else str(text).strip()
            if text:
                system_parts.append(text)
        else:
            rest.append(message)
    if not saw_system:
        return list(messages)
    if not system_parts:
        return rest
    return [{'role': 'system', 'content': '\n\n'.join(system_parts)}] + rest


def repair_tool_message_pairs(messages: List[dict]) -> List[dict]:
    """修复工具消息协议：确保每个 ``assistant.tool_calls`` 后紧跟等量 ``role:tool``。

    对 DeepSeek / OpenAI 兼容网关是硬性要求：缺失的结果补 ``[缺少工具结果]``，
    并保持顺序稳定。

    :param messages: 待发送的消息列表
    :return: 修复后的新列表
    """
    repaired: List[dict] = []
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        repaired.append(message)
        tool_calls = message.get('tool_calls') if message.get('role') == 'assistant' else None
        if not tool_calls:
            index += 1
            continue
        ids = [tc.get('id') for tc in tool_calls]
        index += 1
        results = {}
        while index < total and messages[index].get('role') == 'tool':
            results[messages[index].get('tool_call_id')] = messages[index]
            index += 1
        for tool_id in ids:
            repaired.append(results.get(tool_id) or {
                'role': 'tool', 'tool_call_id': tool_id, 'content': '[缺少工具结果]',
            })
    return repaired
