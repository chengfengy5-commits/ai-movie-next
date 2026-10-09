"""Source-compatible team management use cases."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, TypeVar

from haoai_backend.authentication.avatar import resolve_avatar_url
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams import (
    DEFAULT_ADMIN_PERMISSIONS,
    InviteCreate,
    JoinRequest,
    PermissionsUpdate,
    RoleUpdate,
    TeamBadRequest,
    TeamCreate,
    TeamForbidden,
    TeamLimitExceeded,
    TeamNotFound,
    TeamUpdate,
    TeamsUnavailable,
    has_team_permission,
    member_permissions,
    permissions_for_update,
)

from .domain import (
    invite_payload,
    member_detail_payload,
    team_list_payload,
    team_payload,
)
from .ports import ManagementRecord, ManagementUnitOfWork, ManagementUnitOfWorkFactory


_Result = TypeVar("_Result")


def _run(
    uow_factory: ManagementUnitOfWorkFactory | None,
    operation: Callable[[ManagementUnitOfWork], _Result],
) -> _Result:
    if uow_factory is None:
        raise TeamsUnavailable()
    uow = uow_factory()
    try:
        uow.ensure_clean()
        return operation(uow)
    except Exception:
        try:
            uow.rollback()
        except Exception:
            pass
        raise
    finally:
        uow.close()


def _require_team(uow: ManagementUnitOfWork, team_id: str) -> ManagementRecord:
    team = uow.load_team_snapshot(team_id)
    if team is None:
        raise TeamNotFound()
    return team


def _require_member(
    uow: ManagementUnitOfWork,
    team_id: str,
    user_id: str,
):
    member = uow.membership(team_id, user_id)
    if member is None:
        raise TeamForbidden()
    return member


def _require_owner(uow: ManagementUnitOfWork, team_id: str, user_id: str):
    member = _require_member(uow, team_id, user_id)
    if member.role != "owner":
        raise TeamForbidden("仅队长可执行此操作")
    return member


def _require_permission(
    uow: ManagementUnitOfWork,
    team_id: str,
    user_id: str,
    permission: str,
):
    member = uow.membership(team_id, user_id)
    if member is None:
        raise TeamForbidden()
    # Preserve the source's separate second membership lookup.
    current = uow.membership(team_id, user_id)
    if current is None or not has_team_permission(
        current.role,
        current.permissions,
        permission,
    ):
        raise TeamForbidden("没有执行该操作的权限")
    return member


def _invite_permissions(member: Mapping[str, Any]) -> list[Any]:
    return member_permissions(member.get("permissions"))


def create_team(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    body: TeamCreate,
) -> dict[str, Any]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, Any]:
        config = uow.load_first_config()
        created_count = uow.count_owned_teams(actor.user_id)
        if created_count >= config.team_created_limit:
            raise TeamLimitExceeded(
                f"已达可创建团队上限 {config.team_created_limit} 个"
            )

        team_id = uow.new_id()
        uow.stage_new_team(
            team_id=team_id,
            name=body.name.strip(),
            owner_id=actor.user_id,
            created_at=uow.now_utc_naive(),
        )
        uow.flush()
        uow.stage_new_member(
            member_id=uow.new_id(),
            team_id=team_id,
            user_id=actor.user_id,
            role="owner",
            permissions=None,
            joined_at=uow.now_utc_naive(),
        )
        uow.commit()
        saved = uow.refresh_team(team_id)
        if saved is None:
            raise RuntimeError("committed team could not be read back")
        return team_payload(saved)

    return _run(uow_factory, execute)


def my_teams(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
) -> list[dict[str, Any]]:
    def execute(uow: ManagementUnitOfWork) -> list[dict[str, Any]]:
        memberships = uow.memberships_for_user(actor.user_id)
        team_ids = [row["team_id"] for row in memberships]
        if not team_ids:
            return []

        result: list[dict[str, Any]] = []
        for team in uow.teams_for_ids(team_ids):
            role = next(
                (
                    row["role"]
                    for row in memberships
                    if row["team_id"] == team["id"]
                ),
                "member",
            )
            result.append(
                team_list_payload(
                    team,
                    member_count=uow.count_team_members(team["id"]),
                    my_role=role,
                )
            )
        return result

    return _run(uow_factory, execute)


def team_detail(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
) -> dict[str, Any]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, Any]:
        team = _require_team(uow, team_id)
        _require_member(uow, team_id, actor.user_id)
        members = uow.memberships_for_team(team_id)
        users = uow.load_users([member["user_id"] for member in members])
        current = next(
            (member for member in members if member["user_id"] == actor.user_id),
            None,
        )
        output_members = []
        for member in members:
            user_record = users.get(member["user_id"])
            user_payload = None
            if user_record is not None:
                user_payload = {
                    "username": user_record.username,
                    "avatar_url": resolve_avatar_url(user_record.email),
                }
            output_members.append(member_detail_payload(member, user_payload))

        return {
            **team_payload(team),
            "members": output_members,
            "my_role": current["role"] if current is not None else None,
            "my_permissions": _invite_permissions(current) if current is not None else [],
        }

    return _run(uow_factory, execute)


def update_team(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    body: TeamUpdate,
) -> dict[str, Any]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, Any]:
        team = _require_team(uow, team_id)
        _require_owner(uow, team_id, actor.user_id)
        if body.name:
            uow.stage_team_name(team, body.name.strip())
        uow.commit()
        saved = uow.refresh_team(team_id)
        if saved is None:
            raise RuntimeError("committed team could not be read back")
        return {"id": saved["id"], "name": saved["name"]}

    return _run(uow_factory, execute)


def delete_team(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
) -> dict[str, str]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, str]:
        _require_team(uow, team_id)
        _require_owner(uow, team_id, actor.user_id)
        uow.detach_team_series(team_id)
        uow.release_team_locks(team_id)
        uow.delete_team_invites(team_id)
        uow.delete_team_members(team_id)
        uow.stage_team_delete(team_id)
        uow.commit()
        return {"message": "团队已解散"}

    return _run(uow_factory, execute)


def leave_team(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
) -> dict[str, str]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, str]:
        _require_team(uow, team_id)
        member = uow.membership(team_id, actor.user_id)
        if member is None:
            raise TeamBadRequest("你不是该团队成员")
        if member.role == "owner":
            raise TeamBadRequest("队长不能退出团队，如需解散请使用解散功能")
        uow.stage_member_delete(member.id)
        uow.release_team_locks(team_id, actor.user_id)
        uow.commit()
        return {"message": "已退出团队"}

    return _run(uow_factory, execute)


def remove_member(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    user_id: str,
) -> dict[str, str]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, str]:
        _require_team(uow, team_id)
        _require_permission(uow, team_id, actor.user_id, "remove_members")
        if user_id == actor.user_id:
            raise TeamBadRequest("不能移除自己，如需离开请解散团队")
        member = uow.member_snapshot(team_id, user_id)
        if member is None:
            raise TeamNotFound("该成员不存在")
        if member["role"] == "owner":
            raise TeamBadRequest("不能移除队长")
        if member["role"] == "admin":
            operator = uow.membership(team_id, actor.user_id)
            if operator is None or operator.role != "owner":
                raise TeamForbidden("仅队长可移除管理员")
        uow.stage_member_delete(member["id"])
        uow.release_team_locks(team_id, user_id)
        uow.commit()
        return {"message": "成员已移除"}

    return _run(uow_factory, execute)


def set_member_role(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    user_id: str,
    body: RoleUpdate,
) -> dict[str, Any]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, Any]:
        _require_team(uow, team_id)
        _require_owner(uow, team_id, actor.user_id)
        if user_id == actor.user_id:
            raise TeamBadRequest("不能修改自己的角色")
        role = body.role.strip().lower()
        if role not in ("admin", "member"):
            raise TeamBadRequest("角色只能是 admin（管理员）或 member（成员）")
        member = uow.member_snapshot(team_id, user_id)
        if member is None:
            raise TeamNotFound("该成员不在团队中")
        if member["role"] == "owner":
            raise TeamBadRequest("不能修改队长的角色")
        if member["role"] == role:
            return {
                "message": "角色未变化",
                "role": member["role"],
                "permissions": _invite_permissions(member),
            }

        permissions = (
            json.dumps(DEFAULT_ADMIN_PERMISSIONS)
            if role == "admin"
            else None
        )
        uow.stage_member_role(member, role, permissions)
        uow.commit()
        saved = uow.refresh_member(member["id"])
        if saved is None:
            raise RuntimeError("committed membership could not be read back")
        return {
            "message": "角色已更新",
            "role": saved["role"],
            "permissions": _invite_permissions(saved),
        }

    return _run(uow_factory, execute)


def update_member_permissions(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    user_id: str,
    body: PermissionsUpdate,
) -> dict[str, Any]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, Any]:
        _require_team(uow, team_id)
        _require_owner(uow, team_id, actor.user_id)
        member = uow.member_snapshot(team_id, user_id)
        if member is None:
            raise TeamNotFound("该成员不在团队中")
        if member["role"] == "owner":
            raise TeamBadRequest("队长拥有全部权限，无需配置")
        if member["role"] != "admin":
            raise TeamBadRequest("只有管理员可以配置权限，请先设为管理员")
        valid = permissions_for_update(body.permissions)
        stored = json.dumps(valid) if valid else None
        uow.stage_member_permissions(member, stored)
        uow.commit()
        return {"message": "权限已更新", "permissions": valid}

    return _run(uow_factory, execute)


def create_invites(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    body: InviteCreate,
) -> dict[str, Any]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, Any]:
        team = _require_team(uow, team_id)
        _require_permission(uow, team_id, actor.user_id, "manage_invites")
        config = uow.load_first_config()
        ttl = timedelta(hours=config.invite_code_ttl_hours)
        now = uow.now_utc_naive()
        created: list[dict[str, Any]] = []

        for _ in range(body.count):
            code = uow.new_invite_code()
            while uow.invite_code_exists(code):
                code = uow.new_invite_code()
            invite_id = uow.new_id()
            expires_at = now + ttl
            uow.stage_new_invite(
                invite_id=invite_id,
                team_id=team["id"],
                code=code,
                created_by=actor.user_id,
                created_at=now,
                expires_at=expires_at,
            )
            created.append(
                {"id": invite_id, "code": code, "expires_at": expires_at}
            )

        uow.commit()
        return {"invites": created, "ttl_hours": config.invite_code_ttl_hours}

    return _run(uow_factory, execute)


def list_invites(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
) -> list[dict[str, Any]]:
    def execute(uow: ManagementUnitOfWork) -> list[dict[str, Any]]:
        _require_team(uow, team_id)
        _require_permission(uow, team_id, actor.user_id, "manage_invites")
        now = uow.now_utc_naive()
        expired = uow.expired_active_invites(team_id, now)
        if expired:
            for invite in expired:
                uow.stage_invite_status(invite, "expired")
            uow.commit()
        return [invite_payload(row) for row in uow.invites_newest_first(team_id)]

    return _run(uow_factory, execute)


def revoke_invite(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    invite_id: str,
) -> dict[str, str]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, str]:
        _require_team(uow, team_id)
        _require_permission(uow, team_id, actor.user_id, "manage_invites")
        invite = uow.invite_snapshot(invite_id, team_id)
        if invite is None:
            raise TeamNotFound("邀请码不存在")
        if invite["status"] == "used":
            raise TeamBadRequest("该邀请码已被使用，无法作废")
        uow.stage_invite_status(invite, "expired")
        uow.commit()
        return {"message": "邀请码已作废"}

    return _run(uow_factory, execute)


def join_team(
    uow_factory: ManagementUnitOfWorkFactory | None,
    actor: TrustedActor,
    body: JoinRequest,
) -> dict[str, Any]:
    def execute(uow: ManagementUnitOfWork) -> dict[str, Any]:
        code = body.code.strip().upper()
        invite = uow.invite_by_code(code)
        if invite is None or invite["status"] != "active":
            raise TeamBadRequest("邀请码无效或已失效")

        now = uow.now_utc_naive()
        if invite["expires_at"] < now:
            uow.stage_invite_status(invite, "expired")
            uow.commit()
            raise TeamBadRequest("邀请码已过期，请联系队长重新生成")

        team = uow.load_team_snapshot(invite["team_id"])
        if team is None:
            raise TeamNotFound()
        if uow.membership(team["id"], actor.user_id) is not None:
            raise TeamBadRequest("你已在该团队中")

        config = uow.load_first_config()
        joined_count = uow.count_joinable_memberships(actor.user_id)
        if joined_count >= config.team_joined_limit:
            raise TeamLimitExceeded(
                f"已达可加入团队上限 {config.team_joined_limit} 个"
            )

        member_count = uow.count_team_members(team["id"])
        if member_count >= config.team_member_limit:
            raise TeamLimitExceeded(
                f"该团队人数已达上限 {config.team_member_limit} 人"
            )

        uow.stage_new_member(
            member_id=uow.new_id(),
            team_id=team["id"],
            user_id=actor.user_id,
            role="member",
            permissions=None,
            joined_at=uow.now_utc_naive(),
        )
        uow.stage_invite_used(invite, actor.user_id, uow.now_utc_naive())
        uow.commit()
        saved_team = uow.refresh_team(team["id"])
        if saved_team is None:
            raise RuntimeError("committed team could not be read back")
        return {
            "message": f"已成功加入团队「{saved_team['name']}」",
            "team_id": saved_team["id"],
            "team_name": saved_team["name"],
        }

    return _run(uow_factory, execute)
