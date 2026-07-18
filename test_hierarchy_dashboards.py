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

    def test_entry_dashboards_are_compact_and_separated_by_purpose(self):
        ranking = [item.label for item in main.RankingDashboardView().children]
        counting = [item.label for item in main.CountingDashboardView().children]
        officer_admin = [
            item.label
            for item in main.AdminDashboardView(AccessLevel.OFFICER_ADMIN).children
        ]
        gm_leader = [
            item.label
            for item in main.AdminDashboardView(AccessLevel.GM_LEADER).children
        ]

        self.assertEqual(ranking, ["Perfil", "Ranking", "Prio"])
        self.assertEqual(
            counting,
            [
                "Kill Scout",
                "Kill Pelea",
                "Limpieza Aspecto",
                "Scouteo",
                "Mapeo",
                "Pendientes",
            ],
        )
        self.assertEqual(
            officer_admin,
            ["Scout", "Ajustes", "Padrón", "Publicar", "Historial"],
        )
        self.assertEqual(
            gm_leader,
            [
                "Scout",
                "Ajustes",
                "Padrón",
                "Publicar",
                "Historial",
                "Prio",
                "Valores",
                "Exportar",
                "AFK",
                "Cierre",
                "Sistema",
            ],
        )
        self.assertLessEqual(len(counting), 6)
        self.assertLessEqual(len(gm_leader), 11)

    def test_grouped_dashboards_keep_export_tools(self):
        exports = [item.label for item in main.RankingExportView().children]

        self.assertEqual(
            exports,
            ["Actual XLSX", "Actual CSV", "Cierre XLSX", "Cierre CSV"],
        )

    def test_only_summary_commands_are_registered(self):
        self.assertEqual(
            sorted(command.name for command in main.tree.get_commands()),
            ["admin", "conteo", "ranking"],
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
