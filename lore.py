# -*- coding: utf-8 -*-
"""世界书 / 知识检索（阶段性预留接口）

设计参考 SillyTavern（AGPL-3.0）的 World Info：关键词激活 + 预算受控 + 递归扩展。

当前版本（v1.1）仅提供接口占位，``search`` 恒返回空列表；阶段 3 将实现
``lore.yaml`` 的加载与关键词/预算激活，并接入 ``tools.search_lore`` 与
``main`` 的 system prompt 装配。此设计确保接口稳定、后续无需改动调用方。
"""
from __future__ import annotations

from typing import List, Optional

from ncatbot.utils.logger import get_log

_log = get_log('ncatbot_trpg')


def load_lore_config(path) -> dict:
    """加载世界书配置（阶段 3 实现，当前返回空配置）。

    :param path: ``lore.yaml`` 路径
    :return: 世界书配置字典
    """
    _log.debug(f'世界书接口预留：忽略配置 {path!r}（阶段 3 实现）')
    return {}


def search(query: str, *, top_k: int = 3, config: Optional[dict] = None) -> List[dict]:
    """按关键词检索世界书（阶段 3 实现，当前返回空）。

    :param query: 检索词
    :param top_k: 返回条数上限
    :param config: 已加载的世界书配置
    :return: 命中条目列表（``{id, label, content}``）
    """
    return []
