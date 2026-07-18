import unittest
from unittest import mock

import main
import permissions
from permissions import AccessLevel


class FakeRole:
    def __init__(self, role_id):
        self.id = role_id


class FakePermissions:
    def __init__(self, administrator=False):
        self.administrator = administrator


class FakeMember:
    def __init__(self, role_ids=(), administrator=False):
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.guild_permissions = FakePermissions(administrator)


class HierarchyDashboardTests(unittest.TestCase):
    def test_access_level_has_only_the_three_agreed_hierarchies(self):
        self.assertEqual(
            list(AccessLevel),
            [
                AccessLevel.GENERAL,
                AccessLevel.OFFICER_ADMIN,
                AccessLevel.GM_LEADER,
            ],
        )

    def test_roles_do_not_mix_officer_admin_with_gm_leader(self):
        with (
            mock.patch.object(permissions, "OFFICER_ADMIN_ROLE_IDS", {"20"}),
            mock.patch.object(permissions, "GM_LEADER_ROLE_IDS", {"30"}),
        ):
            self.assertEqual(permissions.get_access_level(FakeMember()), AccessLevel.GENERAL)
            self.assertEqual(
                permissions.get_access_level(FakeMember(role_ids=("20",))),
                AccessLevel.OFFICER_ADMIN,
            )
            self.assertEqual(
                permissions.get_access_level(FakeMember(role_ids=("30",))),
                AccessLevel.GM_LEADER,
            )
            self.assertEqual(
                permissions.get_access_level(FakeMember(administrator=True)),
                AccessLevel.OFFICER_ADMIN,
            )

    def test_main_dashboard_buttons_are_compact_by_hierarchy(self):
        expected = {
            AccessLevel.GENERAL: ["Perfil", "Ranking", "Prio"],
            AccessLevel.OFFICER_ADMIN: [
                "Perfil",
                "Ranking",
                "Prio",
                "Operaciones",
                "Historial",
            ],
            AccessLevel.GM_LEADER: [
                "Perfil",
                "Ranking",
                "Prio",
                "Operaciones",
                "Admin",
                "Historial",
            ],
        }
        for level, labels in expected.items():
            view = main.RankingHierarchyView(level)
            self.assertEqual([item.label for item in view.children], labels)
            self.assertLessEqual(len(view.children), 6)

    def test_grouped_dashboards_cover_all_administrative_tools(self):
        operations = [item.label for item in main.OperationsDashboardView().children]
        administration = [item.label for item in main.AdministrationDashboardView().children]
        exports = [item.label for item in main.RankingExportView().children]

        self.assertEqual(
            operations,
            ["Evidencias", "Scout", "Puntos", "Padrón", "Publicar"],
        )
        self.assertEqual(
            administration,
            ["Prio", "Valores", "Exportar", "AFK", "Cierre", "Sistema"],
        )
        self.assertEqual(
            exports,
            ["Actual XLSX", "Actual CSV", "Cierre XLSX", "Cierre CSV"],
        )

    def test_only_summary_commands_are_registered(self):
        self.assertEqual(
            sorted(command.name for command in main.tree.get_commands()),
            ["ranking"],
        )
        self.assertEqual(
            sorted(command.name for command in main.admin_group.commands),
            [
                "afks",
                "analizar_mapeo",
                "conteo",
                "dashboard_scouts",
                "export_ranking",
                "info_ranking",
                "modificar_puntos",
                "mover_conteo_cierre",
                "padron",
                "perfil",
                "prio",
                "puntos",
                "reset_analisis",
                "reset_ranking",
            ],
        )


if __name__ == "__main__":
    unittest.main()
