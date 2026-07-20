import inspect
import unittest
from unittest import mock

import main
import permissions
import views
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

        self.assertEqual(ranking, ["Mi Perfil", "Ver Ranking", "Requisito Prio"])
        self.assertEqual(
            counting,
            [
                "Kill Scout",
                "Kill Pelea",
                "Limpieza Aspecto",
                "Scouteo",
                "Mapeo",
                "Ver Pendientes",
            ],
        )
        self.assertEqual(
            officer_admin,
            [
                "Editar Scout",
                "Ajustar Puntos",
                "Gestionar Padrón",
                "Publicar Ranking",
                "Ver Historial",
            ],
        )
        self.assertEqual(
            gm_leader,
            [
                "Editar Scout",
                "Ajustar Puntos",
                "Gestionar Padrón",
                "Publicar Ranking",
                "Ver Historial",
                "Gestionar Prio",
                "Configurar Puntos",
                "Exportar Ranking",
                "Revisar AFK",
                "Cerrar Semana",
                "Herramientas GM",
            ],
        )
        self.assertLessEqual(len(counting), 6)
        self.assertLessEqual(len(gm_leader), 11)
        self.assertEqual(
            [item.row for item in main.AdminDashboardView(AccessLevel.GM_LEADER).children],
            [0, 0, 0, 1, 1, 2, 2, 2, 3, 3, 3],
        )

    def test_grouped_dashboards_keep_export_tools(self):
        exports = [item.label for item in main.RankingExportView().children]

        self.assertEqual(
            exports,
            ["Actual XLSX", "Actual CSV", "Cierre XLSX", "Cierre CSV"],
        )

    def test_prio_source_selector_keeps_closure_read_only(self):
        self.assertEqual(
            [item.label for item in main.PrioritySourceSelectView().children],
            ["Prio actual", "Ultimo cierre"],
        )
        self.assertIn(
            "Aplicar",
            [item.label for item in main.PrioDashboardView(source="actual").children],
        )
        closure_labels = [
            item.label
            for item in main.PrioDashboardView(source="ultimo_cierre").children
        ]
        self.assertNotIn("Aplicar", closure_labels)
        self.assertNotIn("Corte", closure_labels)

    def test_general_ranking_entry_keeps_pagination_controls(self):
        source = inspect.getsource(views.DashboardView.ranking)
        self.assertIn("RankingPaginationView", source)
        self.assertIn("per_page=15", source)

    def test_pending_alert_uses_relative_time_and_clickable_review(self):
        content = main.build_pending_evidence_alert_content(
            "<@&20>",
            "Kill Pelea",
            "2026-07-18T10:00:00",
            "https://discord.com/channels/1/2/3",
        )
        self.assertIn("<t:1784368800:R>", content)
        self.assertIn(
            "[Abrir revisión](https://discord.com/channels/1/2/3)",
            content,
        )
        self.assertNotIn("5 horas", content)
        self.assertNotIn("ID de evidencia", content)

    def test_scouteo_review_defers_before_slow_resolution(self):
        source = inspect.getsource(main.ScouteoCountView.send_review)
        defer_position = source.index("interaction.response.defer")
        create_position = source.index("create_scouteo_count_review")
        self.assertLess(defer_position, create_position)
        self.assertIn("interaction.followup.send", source)

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
