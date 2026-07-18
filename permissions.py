from enum import IntEnum

from config import (
    ADMIN_PERMISSION,
    GM_LEADER_ROLE_IDS,
    OFFICER_ADMIN_ROLE_IDS,
    REVIEWER_ROLE_IDS,
)


class AccessLevel(IntEnum):
    GENERAL = 0
    OFFICER_ADMIN = 1
    GM_LEADER = 2


def has_any_role(member, role_ids):
    if not member or not getattr(member, "roles", None):
        return False
    return any(str(role.id) in role_ids for role in member.roles)


def is_admin(interaction):
    return is_admin_member(interaction.user)


def is_admin_member(member):
    if ADMIN_PERMISSION and getattr(getattr(member, "guild_permissions", None), "administrator", False):
        return True
    return has_any_role(member, OFFICER_ADMIN_ROLE_IDS | GM_LEADER_ROLE_IDS)


def can_review_evidence(interaction):
    return can_review_member(interaction.user)


def can_review_member(member):
    return is_admin_member(member) or has_any_role(member, REVIEWER_ROLE_IDS)


def is_gm_member(member):
    return has_any_role(member, GM_LEADER_ROLE_IDS)


def get_access_level(member) -> AccessLevel:
    if is_gm_member(member):
        return AccessLevel.GM_LEADER
    if is_admin_member(member) or can_review_member(member):
        return AccessLevel.OFFICER_ADMIN
    return AccessLevel.GENERAL


def access_level_label(level: AccessLevel | int) -> str:
    labels = {
        AccessLevel.GENERAL: "General",
        AccessLevel.OFFICER_ADMIN: "Officer / Admin",
        AccessLevel.GM_LEADER: "GM / Lider",
    }
    return labels[AccessLevel(level)]
