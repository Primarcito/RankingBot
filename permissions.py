from config import ADMIN_PERMISSION, ADMIN_ROLE_IDS, REVIEWER_ROLE_IDS


def has_any_role(member, role_ids):
    if not member or not getattr(member, "roles", None):
        return False
    return any(str(role.id) in role_ids for role in member.roles)


def is_admin(interaction):
    if ADMIN_PERMISSION and interaction.user.guild_permissions.administrator:
        return True
    return has_any_role(interaction.user, ADMIN_ROLE_IDS)


def can_review_evidence(interaction):
    return is_admin(interaction) or has_any_role(interaction.user, REVIEWER_ROLE_IDS)
