# -*- coding: utf-8 -*-
"""权限系统测试

覆盖三层授权机制：
1. root（config.yaml 中机器人 owner）启动时自动获得全局管理员权限
2. /trpgrbac 命令授予/撤销/查看全局管理员（仅全局管理员/root 可用）
3. 群主/群管理自动放行本群管理命令（受 EnableGroupOwnerAutoAuth 开关控制）

全部用例经框架 MockAdapter 驱动：事件注入走真实分发链路，API 调用通过
`assert_api()` / `mock.call_count()` 做精确断言。

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness, group_message
from ncatbot.testing.factories.qq import private_message
from ncatbot.types.napcat.group import GroupMemberInfo

pytestmark = pytest.mark.asyncio(mode="strict")

PLUGINS_DIR = Path(__file__).resolve().parents[2]
PLUGIN_NAME = "NcatBotTRPG"
ROOT_QQ = "123456"
PERM = "NcatBotTRPG.admin"
GROUP_ID = "200200"


def _harness() -> PluginTestHarness:
    return PluginTestHarness(plugin_names=[PLUGIN_NAME], plugins_dir=PLUGINS_DIR)


def _member_list(roles):
    return [
        GroupMemberInfo(user_id=uid, role=role, group_id=GROUP_ID)
        for uid, role in roles.items()
    ]


async def test_on_load_grants_root_admin(tmp_path, monkeypatch):
    """root 在插件加载后自动获得全局管理员权限，非 root 无权限"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        assert plugin.check_permission(ROOT_QQ, PERM) is True
        assert plugin.check_permission("999999", PERM) is False


async def test_trpgrbac_grant_revoke_list(tmp_path, monkeypatch):
    """root 可通过 /trpgrbac 授予/撤销/查看全局管理员"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)

        await h.inject(
            group_message("/trpgrbac grant 888888", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is True
        h.assert_api("send_group_msg").with_text("已授予 888888")

        # 重复授予提示
        h.reset_api()
        await h.inject(
            group_message("/trpgrbac grant 888888", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        h.assert_api("send_group_msg").with_text("已是全局管理员")

        # list 可见
        h.reset_api()
        await h.inject(group_message("/trpgrbac list", group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle()
        h.assert_api("send_group_msg").with_text("全局管理员列表").with_text(ROOT_QQ, "888888")

        # 撤销
        h.reset_api()
        await h.inject(
            group_message("/trpgrbac revoke 888888", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is False
        h.assert_api("send_group_msg").with_text("已撤销 888888")

        # 严格 RBAC 路径不查询群成员列表
        h.assert_api("get_group_member_list").not_called()


async def test_trpgrbac_grant_with_at_component(tmp_path, monkeypatch):
    """/trpgrbac 支持 @ 成员（CQ:at 组件）形式授权"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)

        await h.inject(
            group_message(
                "/trpgrbac grant",
                group_id=GROUP_ID,
                user_id=ROOT_QQ,
                raw_message="/trpgrbac grant [CQ:at,qq=888888]",
                message=[
                    {"type": "text", "data": {"text": "/trpgrbac grant "}},
                    {"type": "at", "data": {"qq": "888888"}},
                ],
            )
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is True
        h.assert_api("send_group_msg").with_text("已授予 888888")
        h.assert_api("get_group_member_list").not_called()


async def test_trpgrbac_invalid_target_rejected(tmp_path, monkeypatch):
    """@全体成员与非法目标无法被授予"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)

        await h.inject(
            group_message("/trpgrbac grant all", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        h.assert_api("send_group_msg").with_text("纯数字或 @ 成员")
        assert plugin.check_permission("all", PERM) is False

        h.reset_api()
        await h.inject(
            group_message("/trpgrbac grant abc", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        h.assert_api("send_group_msg").with_text("纯数字或 @ 成员")
        h.assert_api("get_group_member_list").not_called()


async def test_group_owner_cannot_grant_global_admin(tmp_path, monkeypatch):
    """群主不能通过 /trpgrbac 授予全局管理员（防止提权）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        h.mock_api_for("qq").set_response(
            "get_group_member_list", _member_list({"444": "owner"})
        )

        await h.inject(
            group_message("/trpgrbac grant 888888", group_id=GROUP_ID, user_id="444")
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is False
        h.assert_api("send_group_msg").with_text("权限不足")
        # 严格 RBAC 路径即便群主也不查询群角色
        h.assert_api("get_group_member_list").not_called()


async def test_group_owner_and_admin_allowed_for_admin_command(tmp_path, monkeypatch):
    """群主/群管理经群角色查询自动放行 /trpg-admin，普通成员被拒；命中角色缓存"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        mock = h.mock_api_for("qq")
        mock.set_response(
            "get_group_member_list",
            _member_list({"111": "owner", "222": "admin", "333": "member"}),
        )

        await h.inject(group_message("/trpg-admin help", group_id=GROUP_ID, user_id="111"))
        await h.settle()
        h.assert_api("get_group_member_list").called().with_params(group_id=GROUP_ID)
        h.assert_api("send_group_msg").with_text("管理员命令帮助")

        # 群管理放行：命中 300s 角色缓存，不再重复请求
        await h.inject(group_message("/trpg-admin help", group_id=GROUP_ID, user_id="222"))
        await h.settle()
        assert mock.call_count("get_group_member_list") == 1
        h.assert_api("send_group_msg").with_text("管理员命令帮助")

        # 普通成员拒绝
        await h.inject(group_message("/trpg-admin help", group_id=GROUP_ID, user_id="333"))
        await h.settle()
        assert mock.call_count("get_group_member_list") == 1
        h.assert_api("send_group_msg").with_text("权限不足")


async def test_group_owner_auto_auth_can_be_disabled(tmp_path, monkeypatch):
    """EnableGroupOwnerAutoAuth 关闭后群主不再自动放行（且不查询群成员列表）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin(PLUGIN_NAME)
        h.mock_api_for("qq").set_response(
            "get_group_member_list", _member_list({"111": "owner"})
        )
        plugin.config["EnableGroupOwnerAutoAuth"] = False

        await h.inject(group_message("/trpg-admin help", group_id=GROUP_ID, user_id="111"))
        await h.settle()
        h.assert_api("send_group_msg").with_text("权限不足")
        h.assert_api("get_group_member_list").not_called()


async def test_non_admin_denied_private(tmp_path, monkeypatch):
    """非管理员私聊使用 /trpg-admin 被拒绝（fail-closed，不查询群角色）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        await h.inject(private_message("/trpg-admin help", user_id="999999"))
        await h.settle()
        h.assert_api("send_private_msg").with_text("权限不足")
        h.assert_api("get_group_member_list").not_called()
