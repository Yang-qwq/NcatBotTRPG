# -*- coding: utf-8 -*-
"""工具层测试：schema 组合、受控写工具校验、伤害门控（离线，无网络）。

运行：python -m pytest tests -v -o "addopts="
"""
import asyncio
import json
import sys
from pathlib import Path

PLUGINS_DIR = Path(__file__).resolve().parents[2]
if str(PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGINS_DIR))

from NcatBotTRPG import tools  # noqa: E402
from NcatBotTRPG.session import SessionStore  # noqa: E402
from NcatBotTRPG.state import StateStore  # noqa: E402

DICTIONARY = {'items': {'potion_heal': {'label': '治疗药水', 'desc': '恢复1d8生命'}}}


class _StubSender:
    nickname = '甲'


class _StubEvent:
    def __init__(self, user_id='123'):
        self.user_id = user_id
        self.sender = _StubSender()


class _StubPlugin:
    def __init__(self, config=None):
        self.config = config or {}
        self.workspace = Path('.')

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def _get_int_config(self, key, default):
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default


def _ctx(scope='user', sid='123', user_id='123'):
    sessions = {'group': {}, 'user': {}}
    store = SessionStore(sessions)
    session = store.create(scope, sid)
    state = StateStore(session, DICTIONARY)
    state.ensure_character(user_id, '甲')
    event = _StubEvent(user_id)
    return tools.ToolContext(plugin=_StubPlugin(), event=event, scope=scope,
                             sid=sid, state=state, session_store=store), store


def test_build_tools_includes_request_check_always():
    """request_check 始终可用；写工具受门控。"""
    read_only = {t['function']['name'] for t in tools.build_tools(False)}
    full = {t['function']['name'] for t in tools.build_tools(True)}
    assert 'request_check' in read_only
    assert 'change_hp' not in read_only
    assert {'change_hp', 'change_attribute', 'manage_inventory',
            'update_scene', 'record_event'} <= full


def test_change_hp_damage_requires_resolved_check():
    """伤害必须有已结算检定背书；治疗后正常写入。"""
    ctx, store = _ctx()
    # 无背书 → 拒绝
    result, terminal = asyncio.run(
        tools.execute('change_hp', {'target': '123', 'delta': -3, 'reason': '被击中'}, ctx))
    assert json.loads(result)['status'] == 'error'
    assert terminal is False
    assert ctx.state.get_character('123')['attrs']['hp']['cur'] == 10

    # 添加并结算一条检定 → 允许伤害
    store.add_check('user', '123', {
        'roller_id': '123', 'subject': '甲', 'expr': '1d100',
        'roll_key': '1d100', 'op': '<=', 'target': 50, 'reason': '侦查'})
    store.resolve_checks_for_roll('user', '123', '123', '1d100', 30)
    assert store.has_resolved_check('user', '123', '123')
    result, _ = asyncio.run(
        tools.execute('change_hp', {'target': '123', 'delta': -3, 'reason': '被击中'}, ctx))
    assert json.loads(result)['status'] == 'ok'
    assert ctx.state.get_character('123')['attrs']['hp']['cur'] == 7


def test_unknown_tool_returns_error_without_raise():
    """未知工具返回 error payload，不抛异常。"""
    ctx, _ = _ctx()
    result, terminal = asyncio.run(tools.execute('nope', {}, ctx))
    assert json.loads(result)['status'] == 'error'
    assert terminal is False


def test_manage_inventory_unregistered_item_rejected():
    """未登记物品被拒绝。"""
    ctx, _ = _ctx()
    result, _ = asyncio.run(
        tools.execute('manage_inventory',
                      {'target': '123', 'action': 'add', 'item': 'unknown', 'reason': '拾取'}, ctx))
    assert json.loads(result)['status'] == 'error'


def test_group_write_target_is_self_only():
    """群聊中只能修改自己的角色状态。"""
    ctx, store = _ctx(scope='group', sid='200', user_id='123')
    # 在群房间加入另一名玩家
    store.join_room('200', '123')
    store.join_room('200', '456')
    result, _ = asyncio.run(
        tools.execute('change_attribute',
                      {'target': '456', 'field': 'san', 'op': 'delta',
                       'value': -5, 'reason': '恐惧'}, ctx))
    assert json.loads(result)['status'] == 'error'
