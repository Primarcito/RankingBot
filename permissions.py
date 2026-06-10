from config import ADMIN_PERMISSION, ADMIN_ROLE_IDS, REVIEWER_ROLE_IDS


def has_any_role(member, role_ids):
    if not member or not getattr(member, "roles", None):
        return False
    return any(str(role.id) in role_ids for role in member.roles)


def is_admin(interaction):
    return is_admin_member(interaction.user)


def is_admin_member(member):
    if ADMIN_PERMISSION and getattr(getattr(member, "guild_permissions", None), "administrator", False):
        return True
    return has_any_role(member, ADMIN_ROLE_IDS)


def can_review_evidence(interaction):
    return can_review_member(interaction.user)


def can_review_member(member):
    return is_admin_member(member) or has_any_role(member, REVIEWER_ROLE_IDS)
