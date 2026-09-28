# -*- coding: utf-8 -*-
"""AI 主持人 LLM 客户端封装（自研）

基于 openai SDK 的 OpenAI 兼容接口，作为可替换单元，未来可切换为
NcatBot 内置 AI 适配器（``api.ai``）或 diceframe 后端而不改动命令层。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import List, Optional

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


# 内置默认主持人提示词（自研，未复制任何第三方 prompt）
DEFAULT_KEEPER_PROMPT = '''你是一名专业的 TRPG（桌上角色扮演游戏）主持人（GM/KP）。

你的职责：
1. 用生动、简洁的中文描述场景、NPC 与剧情进展，营造沉浸感。
2. 扮演所有非玩家角色（NPC），根据其性格与动机做出合理反应。
3. 根据玩家的行动推进故事，给出明确的环境反馈与可行动的线索。
4. 不替玩家做决定、不代玩家发言，只描述玩家行动带来的结果。
5. 当行动结果存在不确定性时，提示玩家进行检定（例如：请掷 1d100 进行侦查检定），但绝不自行编造骰子结果。
6. 保持剧情连贯，遵守“本团专属设定”，不与玩家进行游戏外的元讨论。
7. 每次回复聚焦当前场景，长度适中（通常 2~6 句），必要时给出 2~3 个可选行动方向。'''

# 群聊中区分不同玩家发言时使用的消息前缀格式
USER_PREFIX_FORMAT = '{name}({user_id})：{text}'


def resolve_prompt_path(plugin, path: str) -> Optional[Path]:
    """将配置中的提示词路径解析为绝对路径。

    相对路径基于插件工作区（``plugin.workspace``）解析，便于随插件数据目录分发。

    :param plugin: 插件实例（提供 workspace）
    :param path: 配置中的路径（空字符串表示未配置）
    :return: 绝对路径；未配置返回 None
    """
    raw = (str(path) if path is not None else '').strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = Path(plugin.workspace) / candidate
    return candidate


def load_prompt_file(plugin, path: str) -> Optional[str]:
    """从文件导入主持人提示词（极简 prompt 导入机制）。

    仅支持 UTF-8 纯文本 / Markdown；读取失败或内容为空时返回 None，
    由调用方回退到内置默认提示词，不阻塞插件加载。

    :param plugin: 插件实例（提供 workspace）
    :param path: 配置的文件路径（相对工作区或绝对路径）
    :return: 提示词文本；未配置或读取失败返回 None
    """
    candidate = resolve_prompt_path(plugin, path)
    if candidate is None:
        _log.debug('未配置 PromptFile，使用内置默认主持人提示词')
        return None
    try:
        text = candidate.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        _log.warning(f'PromptFile 不存在，回退内置默认提示词: {candidate}')
        return None
    except OSError as e:
        _log.error(f'读取 PromptFile 失败，回退内置默认提示词: {candidate} - {e}')
        return None
    if not text:
        _log.warning(f'PromptFile 内容为空，回退内置默认提示词: {candidate}')
        return None
    _log.info(f'已导入主持人提示词: {candidate}（{len(text)} 字）')
    return text


def build_system_prompt(custom_prompt: str = '',
                        default_prompt: Optional[str] = None) -> str:
    """拼接主持人 system 提示词。

    优先级：``default_prompt``（来自 PromptFile）> 内置 ``DEFAULT_KEEPER_PROMPT``；
    其后追加可选的“本团专属设定”。

    :param custom_prompt: 本团专属设定文本（可为空）
    :param default_prompt: 通过配置导入的默认提示词（可为空）
    :return: 完整 system 提示词
    """
    base = (default_prompt or '').strip() or DEFAULT_KEEPER_PROMPT
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
    ) -> str:
        """发起一次 Chat Completion，返回助手文本。

        :param messages: OpenAI 格式的消息列表
        :param model: 覆盖默认模型
        :param temperature: 采样温度
        :param max_tokens: 最大生成 token 数
        :return: 助手回复文本（已去除思维链并 strip）
        """
        kwargs = {
            'model': model or self._plugin.get_config('Model'),
            'messages': messages,
        }
        if temperature is not None:
            kwargs['temperature'] = temperature
        if max_tokens is not None:
            kwargs['max_tokens'] = max_tokens

        # openai SDK 为同步阻塞调用，放入线程避免阻塞事件循环
        _log.debug(
            f'调用 LLM：model={kwargs["model"]}, messages={len(messages)}, '
            f'temperature={temperature}, max_tokens={max_tokens}'
        )
        create = self.client.chat.completions.create
        response = await asyncio.to_thread(create, **kwargs)
        message = response.choices[0].message
        content = (message.content or '').strip()

        # 提取并打印思维链（便于调试），参考其它插件的日志风格；不会发送给用户
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
        if not content:
            if reasoning:
                _log.warning('模型仅返回思维链而无正文，已忽略以避免暴露思维链')
            else:
                _log.warning('模型未返回任何正文内容')

        # 推理模型常把思维链以标签等形式混入正文，统一剥离后再返回
        if self._plugin.get_config('StripReasoning', True):
            content = strip_reasoning(content)

        _log.debug(f'LLM 返回 {len(content)} 字')
        return content
