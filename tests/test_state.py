# -*- coding: utf-8 -*-
"""世界状态权威层测试（纯函数，无框架/网络）。

运行：python -m pytest tests -v -o "addopts="
"""
import sys
from pathlib import Path

import pytest

PLUGINS_DIR = Path(__file__).resolve().parents[2]
if str(PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGINS_DIR))

from NcatBotTRPG.state import StateError, StateStore  # noqa: E402

DICTIONARY = {
    'items': {'potion_heal': {'label': '治疗药水', 'desc': '恢复1d8生命'}},
    'statuses': {'poisoned': {'label': '中毒'}},
}


def _store(session=None):
    session = session if session is not None else {}
    return StateStore(session, DICTIONARY)


def test_hp_damage_and_heal_clamp():
    """伤害不超过当前值，治疗不超过上限的一半。"""
    st = _store()
    st.ensure_character('1', '甲')
    st.apply_ops([{'kind': 'hp', 'user_id': '1', 'delta': -4}])
    assert st.get_character('1')['attrs']['hp']['cur'] == 6
    # 治疗上限 = max//2 = 5
    st.apply_ops([{'kind': 'hp', 'user_id': '1', 'delta': 3}])
    assert st.get_character('1')['attrs']['hp']['cur'] == 9
    st.apply_ops([{'kind': 'hp', 'user_id': '1', 'delta': 99}])
    assert st.get_character('1')['attrs']['hp']['cur'] == 10
    # 过量伤害被钳制到 0
    st.apply_ops([{'kind': 'hp', 'user_id': '1', 'delta': -999}])
    assert st.get_character('1')['attrs']['hp']['cur'] == 0


def test_apply_ops_is_atomic_and_fail_closed():
    """任一操作非法则整批回滚，revision 不变。"""
    st = _store()
    st.ensure_character('1')
    rev = st.rev
    with pytest.raises(StateError):
        st.apply_ops([
            {'kind': 'hp', 'user_id': '1', 'delta': 3},
            {'kind': 'hp', 'user_id': '不存在', 'delta': 1},
        ])
    assert st.rev == rev
    assert st.get_character('1')['attrs']['hp']['cur'] == 10


def test_attribute_whitelist_and_hp_rejected():
    """属性须登记；hp 必须走 change_hp。"""
    st = _store()
    st.ensure_character('1')
    st.apply_ops([{'kind': 'attr', 'user_id': '1', 'field': 'san', 'op': 'delta', 'value': -10}])
    assert st.get_character('1')['attrs']['san']['cur'] == 40
    with pytest.raises(StateError):
        st.apply_ops([{'kind': 'attr', 'user_id': '1', 'field': 'hp', 'op': 'set', 'value': 1}])
    with pytest.raises(StateError):
        st.apply_ops([{'kind': 'attr', 'user_id': '1', 'field': '不存在', 'op': 'set', 'value': 1}])


def test_inventory_requires_registered_item():
    """物品必须登记；不足时拒绝。"""
    st = _store()
    st.ensure_character('1')
    with pytest.raises(StateError):
        st.apply_ops([{'kind': 'inventory', 'user_id': '1', 'action': 'add', 'item': 'unknown'}])
    st.apply_ops([{'kind': 'inventory', 'user_id': '1', 'action': 'add', 'item': 'potion_heal', 'qty': 2}])
    inv = st.get_character('1')['inventory']
    assert inv[0]['qty'] == 2
    st.apply_ops([{'kind': 'inventory', 'user_id': '1', 'action': 'use', 'item': 'potion_heal', 'qty': 1}])
    assert st.get_character('1')['inventory'][0]['qty'] == 1
    with pytest.raises(StateError):
        st.apply_ops([{'kind': 'inventory', 'user_id': '1', 'action': 'remove', 'item': 'potion_heal', 'qty': 5}])


def test_scene_and_event_ledger():
    """场景字段白名单；事件账本追加并绑定 revision。"""
    st = _store()
    st.apply_ops([{'kind': 'scene', 'fields': {'location': '雾港码头', 'weather': '小雨'}}])
    assert '雾港码头' in st.project_scene()
    with pytest.raises(StateError):
        st.apply_ops([{'kind': 'scene', 'fields': {'bad': 'x'}}])
    st.apply_ops([{'kind': 'event', 'event': {'t': 'note', 'text': '抵达码头'}}])
    events = st._session['world']['events']
    assert events and events[0]['text'] == '抵达码头'


def test_projection_short_readable():
    """投影为短人类可读标签。"""
    st = _store()
    st.ensure_character('1', '甲')
    st.get_character('1')['inventory'].append({'id': 'potion_heal', 'qty': 1})
    st.get_character('1')['location'] = '雾港码头'
    text = st.project()
    assert '甲(1)' in text
    assert '生命10/10' in text
    assert '理智50/50' in text
    assert '治疗药水×1' in text
    assert '位置雾港码头' in text


def test_projection_budget_truncates():
    """投影遵守字符预算。"""
    st = _store()
    st.ensure_character('1', '甲')
    assert len(st.project(budget=5)) <= 5
