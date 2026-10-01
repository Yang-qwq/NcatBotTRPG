# -*- coding: utf-8 -*-
"""NcatBotTRPG 世界状态权威层

设计参考（仅借鉴思路，未复制代码）：
- diceframe（AGPL-3.0）：紧凑权威状态 + 原子批次提交 + revision + 只读投影
- TRPG-AI-DM（MIT）：可见性/玩家视角、受控状态变更
- ``D:\\Temp\\clipboard.txt``：权威层紧凑、注入层短可读、展示层懒渲染、工具层按需可读

四层模型：
1. 权威层：``session['world']`` 中的紧凑状态（角色/场景/事件 + revision）
2. 字典层：``entities.yaml``（ID -> 展示名/描述）
3. 投影层：:meth:`StateStore.project` 生成短人类可读文本，注入 system prompt
4. 展示层：:meth:`StateStore.project` 的 ``detail='full'`` 供命令懒渲染

状态写入统一走 :meth:`StateStore.apply_ops`：先在草稿上整体校验，全部通过才提交并
递增 ``revision``；任一步失败则抛 :class:`StateError`，原状态不变（fail-closed）。
"""
from __future__ import annotations

import copy
import re
from typing import Dict, List, Optional

from ncatbot.utils.logger import get_log

_log = get_log('ncatbot_trpg')

# 规范化实体键：仅 ASCII、无空白、无展示名
KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$')

# 事件账本默认上限
DEFAULT_MAX_EVENTS = 200

# 单条物品数量上限
MAX_ITEM_QTY = 99
# 场景字段值长度上限
MAX_SCENE_VALUE_LEN = 100
# 属性变更绝对值上限（防越权/乱填）
MAX_ATTR_VALUE = 9999

# 内置字典（文件缺失时的兜底）
BUILTIN_DICTIONARY: Dict[str, dict] = {
    'attributes': {
        'hp': {'label': '生命'},
        'san': {'label': '理智'},
        'mp': {'label': '魔力'},
        'str': {'label': '力量'},
        'dex': {'label': '敏捷'},
        'con': {'label': '体质'},
        'int': {'label': '智力'},
        'wis': {'label': '感知'},
        'cha': {'label': '魅力'},
        'luck': {'label': '幸运'},
    },
    'items': {},
    'statuses': {'normal': {'label': '正常'}},
}

# 默认角色初始值
DEFAULT_HP = 10
DEFAULT_SAN = 50
# 治疗单次上限比例（不超过最大值的 1/2）
HEAL_CAP_RATIO = 2

# 场景允许字段
SCENE_FIELDS = ('location', 'time', 'weather', 'atmosphere')


class StateError(ValueError):
    """世界状态操作非法（会被整体回滚）。"""


def default_character(label: str = '') -> dict:
    """构造默认角色状态。

    :param label: 展示名（可为空）
    :return: 角色状态字典
    """
    return {
        'label': label or '',
        'attrs': {
            'hp': {'cur': DEFAULT_HP, 'max': DEFAULT_HP},
            'san': {'cur': DEFAULT_SAN, 'max': DEFAULT_SAN},
        },
        'inventory': [],
        'conditions': [],
        'location': '',
    }


def new_world() -> dict:
    """构造空的世界状态容器。"""
    return {'characters': {}, 'scene': {}, 'events': [], 'rev': 0}


def _entry_label(entry, default: str) -> str:
    """从字典条目中取展示名（兼容字符串与 ``{label,...}`` 结构）。"""
    if isinstance(entry, dict):
        return str(entry.get('label') or default)
    if entry:
        return str(entry)
    return default


class StateStore:
    """世界状态权威层管理器（直接引用会话字典）。"""

    def __init__(self, session: dict, dictionary: Optional[dict] = None):
        """初始化并补齐世界状态结构。

        :param session: 会话字典（``plugin.data['sessions'][scope][sid]``）
        :param dictionary: 实体字典（``entities.yaml`` 内容）；缺省用内置字典
        """
        self._session = session
        world = session.setdefault('world', new_world())
        for key, value in new_world().items():
            world.setdefault(key, value)
        self._world = world
        merged = copy.deepcopy(BUILTIN_DICTIONARY)
        for section, entries in (dictionary or {}).items():
            if isinstance(entries, dict):
                merged.setdefault(section, {}).update(entries)
        self._dict = merged

    # ------------------------------------------------------------------
    # revision / 角色
    # ------------------------------------------------------------------

    @property
    def rev(self) -> int:
        """当前权威状态版本号。"""
        return int(self._world.get('rev', 0))

    def ensure_character(self, user_id, label: str = '') -> dict:
        """确保角色存在（惰性创建默认角色）。

        :param user_id: 玩家 QQ
        :param label: 展示名（存在时不覆盖空值）
        :return: 角色状态字典
        """
        uid = str(user_id)
        char = self._world['characters'].get(uid)
        if char is None:
            char = default_character(label)
            self._world['characters'][uid] = char
        elif label and not char.get('label'):
            char['label'] = label
        return char

    def get_character(self, user_id) -> Optional[dict]:
        """获取角色状态；不存在返回 None。"""
        return self._world['characters'].get(str(user_id))

    def all_characters(self) -> dict:
        """获取全部角色状态。"""
        return self._world['characters']

    def subject_label(self, subject) -> str:
        """把检定主体标识渲染为可读名（玩家号 -> 展示名；其它原样）。

        :param subject: 玩家 QQ 或实体名
        :return: 可读名
        """
        char = self.get_character(subject)
        if char is not None:
            return self._display_name(str(subject), char)
        return str(subject)

    # ------------------------------------------------------------------
    # 字典查询（工具按需可读）
    # ------------------------------------------------------------------

    def lookup(self, section: str, entity_id) -> Optional[dict]:
        """查询字典条目。

        :param section: ``attributes`` / ``items`` / ``statuses``
        :param entity_id: 实体 ID
        :return: ``{label, desc}``；未命中返回 None
        """
        entry = self._dict.get(section, {}).get(str(entity_id))
        if entry is None:
            return None
        if isinstance(entry, dict):
            return {'label': str(entry.get('label') or entity_id),
                    'desc': str(entry.get('desc') or '')}
        return {'label': str(entry), 'desc': ''}

    def item_label(self, item_id) -> str:
        """物品展示名（未登记时返回原 ID）。"""
        entry = self.lookup('items', item_id)
        return entry['label'] if entry else str(item_id)

    def known_item(self, item_id) -> bool:
        """物品是否在字典中登记。"""
        return self.lookup('items', item_id) is not None

    def attr_label(self, attr_id) -> str:
        """属性展示名（未登记时返回原 ID）。"""
        entry = self.lookup('attributes', attr_id)
        return entry['label'] if entry else str(attr_id)

    # ------------------------------------------------------------------
    # 原子批次写入
    # ------------------------------------------------------------------

    def apply_ops(self, ops: List[dict], *, max_events: int = DEFAULT_MAX_EVENTS) -> List[dict]:
        """在草稿上整体校验并提交一批状态变更（原子、fail-closed）。

        :param ops: 操作列表，每项形如 ``{'kind': ...}``
        :param max_events: 事件账本上限
        :return: 回执列表
        :raises StateError: 任一操作非法时抛出，原状态不变
        """
        draft = copy.deepcopy(self._world)
        receipts: List[dict] = []
        for op in ops or []:
            if not isinstance(op, dict):
                raise StateError(f'非法操作: {op!r}')
            kind = op.get('kind')
            handler = {
                'hp': self._op_hp,
                'attr': self._op_attr,
                'inventory': self._op_inventory,
                'scene': self._op_scene,
                'event': self._op_event,
            }.get(kind)
            if handler is None:
                raise StateError(f'未知操作类型: {kind!r}')
            handler(draft, op, receipts, max_events)
        draft['rev'] = int(draft.get('rev', 0)) + 1
        self._world = draft
        self._session['world'] = draft
        _log.debug(f'状态提交成功：{len(receipts)} 项，rev={draft["rev"]}')
        return receipts

    def _op_hp(self, world: dict, op: dict, receipts: List[dict], max_events: int) -> None:
        """HP 增减：伤害不超过当前值，治疗不超过上限的一半。"""
        uid = str(op.get('user_id') or '')
        char = world['characters'].get(uid)
        if char is None:
            raise StateError(f'未知角色: {uid}')
        try:
            delta = int(op.get('delta'))
        except (TypeError, ValueError):
            raise StateError('HP 变更量必须为整数')
        hp = char['attrs'].setdefault('hp', {'cur': DEFAULT_HP, 'max': DEFAULT_HP})
        cur = int(hp.get('cur', 0))
        mx = hp.get('max')
        mx = int(mx) if mx is not None else None
        if delta < 0:
            applied = -min(cur, -delta)  # 伤害不超过当前值
        else:
            if mx is not None:
                deficit = max(0, mx - cur)
                heal_cap = max(1, mx // HEAL_CAP_RATIO)
                applied = min(delta, deficit, heal_cap)
            else:
                applied = delta
        hp['cur'] = cur + applied
        receipts.append({'kind': 'hp', 'user_id': uid, 'applied': applied, 'cur': hp['cur']})

    def _op_attr(self, world: dict, op: dict, receipts: List[dict], max_events: int) -> None:
        """属性变更：字段须为已登记属性，数值限定范围。"""
        uid = str(op.get('user_id') or '')
        char = world['characters'].get(uid)
        if char is None:
            raise StateError(f'未知角色: {uid}')
        field = str(op.get('field') or '')
        if self.lookup('attributes', field) is None:
            raise StateError(f'未登记的属性: {field!r}')
        if field in ('hp',):
            raise StateError('生命值请使用 change_hp 工具')
        op_kind = str(op.get('op') or 'set')
        if op_kind not in ('set', 'delta'):
            raise StateError(f'非法属性操作: {op_kind!r}')
        try:
            value = int(op.get('value'))
        except (TypeError, ValueError):
            raise StateError('属性值必须为整数')
        if abs(value) > MAX_ATTR_VALUE:
            raise StateError(f'属性值超出范围（|v| <= {MAX_ATTR_VALUE}）')
        attrs = char['attrs']
        entry = attrs.get(field)
        if isinstance(entry, dict):
            cur = int(entry.get('cur', 0))
            entry['cur'] = value if op_kind == 'set' else cur + value
            new_value = entry['cur']
        else:
            cur = int(entry or 0)
            new_value = value if op_kind == 'set' else cur + value
            attrs[field] = {'cur': new_value, 'max': None}
        receipts.append({'kind': 'attr', 'user_id': uid, 'field': field, 'value': new_value})

    def _op_inventory(self, world: dict, op: dict, receipts: List[dict], max_events: int) -> None:
        """物品增删/使用：物品须在字典登记，数量受限。"""
        uid = str(op.get('user_id') or '')
        char = world['characters'].get(uid)
        if char is None:
            raise StateError(f'未知角色: {uid}')
        action = str(op.get('action') or '')
        item = str(op.get('item') or '')
        if action not in ('add', 'remove', 'use'):
            raise StateError(f'非法物品操作: {action!r}')
        if not item:
            raise StateError('物品 ID 不能为空')
        if not self.known_item(item):
            raise StateError(f'未登记的物品: {item!r}')
        try:
            qty = int(op.get('qty', 1))
        except (TypeError, ValueError):
            raise StateError('物品数量必须为整数')
        if qty <= 0:
            raise StateError('物品数量必须为正整数')
        inventory: List[dict] = char.setdefault('inventory', [])

        def _find(entry_id):
            return next((e for e in inventory if e.get('id') == entry_id), None)

        if action == 'add':
            entry = _find(item)
            if entry is None:
                inventory.append({'id': item, 'qty': min(qty, MAX_ITEM_QTY)})
            else:
                entry['qty'] = min(int(entry.get('qty', 0)) + qty, MAX_ITEM_QTY)
            receipts.append({'kind': 'inventory', 'user_id': uid, 'action': 'add', 'item': item})
            return

        # remove / use
        entry = _find(item)
        if entry is None or int(entry.get('qty', 0)) < qty:
            raise StateError(f'物品不足: {item!r}')
        entry['qty'] = int(entry['qty']) - qty
        if entry['qty'] <= 0:
            inventory.remove(entry)
        receipts.append({'kind': 'inventory', 'user_id': uid, 'action': action, 'item': item})

    def _op_scene(self, world: dict, op: dict, receipts: List[dict], max_events: int) -> None:
        """场景字段更新（白名单 + 长度限制）。"""
        fields = op.get('fields')
        if not isinstance(fields, dict) or not fields:
            raise StateError('场景更新缺少字段')
        scene = world.setdefault('scene', {})
        for key, value in fields.items():
            if key not in SCENE_FIELDS:
                raise StateError(f'非法场景字段: {key!r}')
            text = str(value).strip()
            if len(text) > MAX_SCENE_VALUE_LEN:
                raise StateError(f'场景字段 {key!r} 过长（<= {MAX_SCENE_VALUE_LEN}）')
            scene[key] = text
        receipts.append({'kind': 'scene', 'fields': list(fields)})

    def _op_event(self, world: dict, op: dict, receipts: List[dict], max_events: int) -> None:
        """追加事件账本（append-only + 上限）。"""
        event = op.get('event')
        if not isinstance(event, dict) or not event:
            raise StateError('事件内容不能为空')
        events: List[dict] = world.setdefault('events', [])
        record = copy.deepcopy(event)
        record['rev'] = int(world.get('rev', 0))
        events.append(record)
        if max_events and max_events > 0 and len(events) > max_events:
            del events[:-max_events]
        receipts.append({'kind': 'event', 'record': record})

    def append_event(self, event: dict, *, max_events: int = DEFAULT_MAX_EVENTS) -> None:
        """直接追加一条事件并递增 revision（内部使用）。"""
        self.apply_ops([{'kind': 'event', 'event': event}], max_events=max_events)

    # ------------------------------------------------------------------
    # 投影（短可读 / 完整可读）
    # ------------------------------------------------------------------

    def _display_name(self, uid: str, char: dict) -> str:
        """角色展示名：``标签(QQ)`` 或 ``玩家QQ``。"""
        label = (char.get('label') or '').strip()
        return f'{label}({uid})' if label else f'玩家{uid}'

    def _fmt_attr(self, attr_id: str, value, detail: str) -> str:
        """格式化单个属性为 ``生命12/15`` / ``力量60``。"""
        name = self.attr_label(attr_id)
        if isinstance(value, dict):
            cur = value.get('cur', 0)
            mx = value.get('max')
            if mx:
                return f'{name}{cur}/{mx}'
            return f'{name}{cur}'
        return f'{name}{value}'

    def _fmt_character(self, char: dict, detail: str) -> str:
        """格式化单个角色为短可读文本。"""
        parts: List[str] = []
        for attr_id, value in char.get('attrs', {}).items():
            parts.append(self._fmt_attr(attr_id, value, detail))
        location = (char.get('location') or '').strip()
        if location:
            parts.append(f'位置{location}')
        inventory = char.get('inventory') or []
        if inventory:
            items = []
            for entry in inventory:
                name = self.item_label(entry.get('id'))
                qty = int(entry.get('qty', 1))
                state = entry.get('state')
                suffix = f'({state})' if state else ''
                items.append(f'{name}{suffix}×{qty}')
            parts.append('背包' + ' '.join(items))
        conditions = char.get('conditions') or []
        if conditions:
            labels = [self.lookup('statuses', c)['label'] if self.lookup('statuses', c) else str(c)
                      for c in conditions]
            parts.append('状态' + ' '.join(labels))
        if detail == 'full':
            detail_items = []
            for entry in inventory:
                item_id = entry.get('id')
                info = self.lookup('items', item_id)
                if info and info['desc']:
                    detail_items.append(f'{info["label"]}：{info["desc"]}')
            if detail_items:
                parts.append('（' + '；'.join(detail_items) + '）')
        return ' '.join(parts)

    def project(self, *, target: str = 'party', detail: str = 'short',
                budget: int = 1200) -> str:
        """生成给 LLM 注入的状态投影。

        :param target: ``party`` / ``self``（等价全部）/ 具体玩家号 / ``scene``
        :param detail: ``short``（短标签）或 ``full``（附物品描述，供展示层）
        :param budget: 字符预算上限（超出截断）
        :return: 投影文本；无内容返回空串
        """
        if target == 'scene':
            return self.project_scene(budget=budget)
        if target in ('party', 'self', ''):
            lines = []
            for uid, char in self.all_characters().items():
                text = self._fmt_character(char, detail)
                if text:
                    lines.append(f'{self._display_name(uid, char)}: {text}')
            result = '\n'.join(lines)
        else:
            char = self.get_character(target)
            if char is None:
                return ''
            result = self._fmt_character(char, detail)
        if budget and budget > 0 and len(result) > budget:
            result = result[:budget]
        return result

    def project_scene(self, *, budget: int = 1200) -> str:
        """生成场景投影文本。"""
        scene = self._world.get('scene') or {}
        parts = [f'{self._scene_label(k)}{v}' for k, v in scene.items() if str(v).strip()]
        result = ' '.join(parts)
        if budget and budget > 0 and len(result) > budget:
            result = result[:budget]
        return result

    @staticmethod
    def _scene_label(key: str) -> str:
        """场景字段的可读前缀。"""
        return {'location': '位置', 'time': '时间', 'weather': '天气',
                'atmosphere': '氛围'}.get(key, f'{key}：')
