import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import CliBase, run  # noqa: E402

from workbench import profile  # noqa: E402


class Detection(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patcher = mock.patch.object(profile, "_contributors", return_value=1)
        self.contributors = patcher.start()
        self.addCleanup(patcher.stop)

    def _touch(self, relative: str, content: str = "") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_empty_repo_is_a_prototype(self) -> None:
        self.assertEqual("prototype", profile.detect(self.root).preset)

    def test_solo_repo_with_billing_is_solo_saas(self) -> None:
        self._touch("package.json", json.dumps({"dependencies": {"stripe": "^14.0.0"}}))
        self.assertEqual("solo-saas", profile.detect(self.root).preset)

    def test_codeowners_means_enterprise_regardless_of_size(self) -> None:
        self._touch("CODEOWNERS", "* @team")
        self.assertEqual("enterprise", profile.detect(self.root).preset)

    def test_a_team_with_migrations_is_a_scaleup(self) -> None:
        self.contributors.return_value = 5
        (self.root / "migrations").mkdir()
        self.assertEqual("scaleup", profile.detect(self.root).preset)

    def test_a_team_without_migrations_is_a_startup(self) -> None:
        self.contributors.return_value = 5
        self.assertEqual("startup", profile.detect(self.root).preset)

    def test_signals_are_reported_so_the_call_is_checkable(self) -> None:
        self._touch(".github/workflows/ci.yml", "on: push")
        signals = profile.detect(self.root).signals
        self.assertIn("ci", signals)
        self.assertIn("contributors=1", signals)

    def test_conventions_follow_the_repo(self) -> None:
        self._touch("pyproject.toml", "[project]")
        self._touch("uv.lock", "")
        self._touch("pytest.ini", "[pytest]")
        conventions = profile.detect(self.root).conventions
        self.assertEqual("python", conventions["ecosystem"])
        self.assertEqual("uv", conventions["package_manager"])
        self.assertEqual("pytest", conventions["test_runner"])

    def test_a_stdlib_only_repo_still_has_a_runner(self) -> None:
        """Regression: every marker needs a config file, so unittest read as none.

        The effect was not a wrong label but a silent one: doctor's runner
        check reported a clean all-clear on any repo it could not name a
        runner for, which is exactly the repo the check exists to warn.
        """
        self._touch("tests/test_a.py", "")
        self.assertEqual("unittest", profile.detect(self.root).conventions["test_runner"])

    def test_no_manifest_is_needed_to_see_it(self) -> None:
        """A project with no third-party dependencies has no manifest to read,
        and is the project most likely to be running unittest."""
        self._touch("tests/test_a.py", "")
        self.assertNotIn("ecosystem", profile.detect(self.root).conventions)

    def test_an_explicit_runner_outranks_the_inference(self) -> None:
        self._touch("tests/test_a.py", "")
        self._touch("pytest.ini", "[pytest]")
        self.assertEqual("pytest", profile.detect(self.root).conventions["test_runner"])

    def test_a_non_python_repo_is_not_labelled_unittest(self) -> None:
        self._touch("package.json", "{}")
        self._touch("tests/a.spec.js", "")
        self.assertNotIn("test_runner", profile.detect(self.root).conventions)


class Gates(unittest.TestCase):
    def test_floor_applies_to_every_preset(self) -> None:
        for preset in profile.PRESETS:
            gates = profile.Profile(preset=preset, detected=preset).gates()
            self.assertTrue(any("unit test" in gate for gate in gates), preset)
            self.assertTrue(any("regression test" in gate for gate in gates), preset)
            self.assertTrue(any("rollback" in gate for gate in gates), preset)

    def test_prototype_is_not_exempt_from_the_floor(self) -> None:
        gates = profile.Profile(preset="prototype", detected="prototype").gates()
        self.assertTrue(any("unit test" in gate for gate in gates))

    def test_solo_saas_constrains_operational_cost(self) -> None:
        gates = " ".join(profile.Profile(preset="solo-saas", detected="solo-saas").gates())
        self.assertIn("one person", gates)
        self.assertIn("money paths", gates)

    def test_enterprise_requires_backwards_compatibility(self) -> None:
        gates = " ".join(profile.Profile(preset="enterprise", detected="enterprise").gates())
        self.assertIn("backwards compatibility", gates)


class CriticalZones(unittest.TestCase):
    def test_money_and_auth_paths_are_flagged(self) -> None:
        hits = profile.critical_zones(
            ["src/billing/checkout.py", "src/auth/session.py", "src/ui/button.tsx"]
        )
        self.assertIn("billing", hits)
        self.assertIn("auth", hits)
        self.assertNotIn("src/ui/button.tsx", [p for paths in hits.values() for p in paths])

    def test_migrations_are_flagged(self) -> None:
        self.assertIn("migration", profile.critical_zones(["db/migrations/0004_add_plan.sql"]))

    def test_ordinary_paths_are_not_flagged(self) -> None:
        self.assertEqual({}, profile.critical_zones(["README.md", "src/utils/format.ts"]))


class Confidence(unittest.TestCase):
    """Detection was always allowed to be wrong. It was never allowed to be
    wrong silently -- an unreviewed guess that reads like a finding is how a
    repo ends up held to a bar nobody chose."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patcher = mock.patch.object(profile, "_contributors", return_value=1)
        self.contributors = patcher.start()
        self.addCleanup(patcher.stop)

    def _touch(self, relative: str, content: str = "") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_codeowners_is_evidence_not_a_guess(self) -> None:
        self._touch("CODEOWNERS", "* @team")
        self.assertEqual(profile.HIGH, profile.detect(self.root).confidence)

    def test_unknown_contributor_count_is_low_confidence(self) -> None:
        self.contributors.return_value = None
        detected = profile.detect(self.root)
        self.assertEqual(profile.LOW, detected.confidence)
        self.assertIn("contributors=unknown", detected.signals)
        self.assertTrue(detected.alternatives)

    def test_ci_alone_on_a_solo_repo_is_low_confidence(self) -> None:
        """CI says someone was careful; it does not say who the repo is for."""
        self._touch(".github/workflows/ci.yml", "on: push")
        detected = profile.detect(self.root)
        self.assertEqual("startup", detected.preset)
        self.assertEqual(profile.LOW, detected.confidence)

    def test_a_workspace_declaration_makes_it_a_monorepo(self) -> None:
        self._touch("pnpm-workspace.yaml", 'packages:\n  - packages/*')
        detected = profile.detect(self.root)
        self.assertIn("monorepo", detected.signals)
        self.assertEqual(profile.LOW, detected.confidence)

    def test_package_json_workspaces_count_too(self) -> None:
        self._touch("package.json", json.dumps({"workspaces": ["packages/*"]}))
        self.assertIn("monorepo", profile.detect(self.root).signals)

    def test_three_packages_are_a_monorepo_without_a_declaration(self) -> None:
        for name in ("billing", "web", "worker"):
            (self.root / "packages" / name).mkdir(parents=True)
        self.assertIn("monorepo", profile.detect(self.root).signals)

    def test_two_packages_are_not(self) -> None:
        for name in ("billing", "web"):
            (self.root / "packages" / name).mkdir(parents=True)
        self.assertNotIn("monorepo", profile.detect(self.root).signals)

    def test_a_monorepo_is_low_confidence_even_with_strong_signals(self) -> None:
        """One repo, several products, one bar: wrong by construction."""
        self._touch("CODEOWNERS", "* @team")
        self._touch("pnpm-workspace.yaml", 'packages:\n  - packages/*')
        detected = profile.detect(self.root)
        self.assertEqual("enterprise", detected.preset)
        self.assertEqual(profile.LOW, detected.confidence)

    def test_confirmation_settles_a_low_confidence_call(self) -> None:
        self.contributors.return_value = None
        detected = profile.detect(self.root)
        self.assertTrue(detected.needs_confirmation)
        detected.confirmed = True
        self.assertFalse(detected.needs_confirmation)


class PresetPaths(unittest.TestCase):
    """A monorepo has one repo and several bars. Matching by path is the only
    way to hold billing higher than a playground without splitting the repo."""

    MAPPING = {
        "packages/billing/**": "enterprise",
        "packages/playground/**": "prototype",
        "packages/billing/docs/**": "startup",
    }

    def test_a_path_takes_the_preset_of_its_rule(self) -> None:
        preset, _ = profile.resolve_for(["packages/billing/charge.ts"], self.MAPPING, "startup")
        self.assertEqual("enterprise", preset)

    def test_an_unmatched_path_takes_the_repo_preset(self) -> None:
        preset, _ = profile.resolve_for(["src/util.ts"], self.MAPPING, "solo-saas")
        self.assertEqual("solo-saas", preset)

    def test_a_change_spanning_two_presets_is_held_to_the_higher(self) -> None:
        """The alternative is a plan that meets neither bar."""
        preset, hits = profile.resolve_for(
            ["packages/billing/charge.ts", "packages/playground/demo.ts"], self.MAPPING, "startup"
        )
        self.assertEqual("enterprise", preset)
        self.assertEqual({"enterprise", "prototype"}, set(hits))

    def test_the_longest_rule_wins_so_a_nested_override_holds(self) -> None:
        preset, _ = profile.resolve_for(["packages/billing/docs/readme.md"], self.MAPPING, "startup")
        self.assertEqual("startup", preset)

    def test_windows_separators_resolve_the_same_way(self) -> None:
        preset, _ = profile.resolve_for([r"packages\billing\charge.ts"], self.MAPPING, "startup")
        self.assertEqual("enterprise", preset)

    def test_a_bare_prefix_rule_works_without_a_glob(self) -> None:
        preset, _ = profile.resolve_for(["packages/billing/charge.ts"], {"packages/billing": "enterprise"}, "startup")
        self.assertEqual("enterprise", preset)

    def test_two_rules_of_equal_length_tie_to_the_higher_bar(self) -> None:
        """Regression: the tie fell through to comparing the preset name, so
        "prototype" beat "enterprise" alphabetically and lowered the bar."""
        mapping = {"src/pay/**": "enterprise", "src/*/api*": "prototype"}
        preset, _ = profile.resolve_for(["src/pay/api.ts"], mapping, "startup")
        self.assertEqual("enterprise", preset)

    def test_no_mapping_at_all_is_just_the_repo_preset(self) -> None:
        preset, hits = profile.resolve_for(["a.ts", "b.ts"], {}, "scaleup")
        self.assertEqual("scaleup", preset)
        self.assertEqual(["a.ts", "b.ts"], hits["scaleup"])

    def test_no_paths_at_all_still_answers(self) -> None:
        preset, _ = profile.resolve_for([], {}, "startup")
        self.assertEqual("startup", preset)

    def test_highest_ranks_by_bar_not_by_order(self) -> None:
        self.assertEqual("enterprise", profile.highest(["prototype", "enterprise", "startup"]))
        self.assertEqual("startup", profile.highest(["nonsense"]))

    def test_gates_for_adds_the_zones_a_change_touches(self) -> None:
        gates = " ".join(profile.gates_for("prototype", ["src/billing/charge.py"]))
        self.assertIn("critical zone billing", gates)
        self.assertIn("unit test", gates)


class ThroughTheCli(CliBase):
    def _config(self, data: dict) -> None:
        path = self.root / ".workflow" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_a_low_confidence_preset_asks_to_be_confirmed(self) -> None:
        with mock.patch.object(profile, "_contributors", return_value=None):
            code, out, _ = run("repo", "profile")
        self.assertEqual(0, code)
        self.assertIn("LOW confidence", out)
        self.assertIn("wb repo profile --confirm", out)

    def test_confirming_settles_it_and_stops_asking(self) -> None:
        with mock.patch.object(profile, "_contributors", return_value=None):
            run("repo", "profile", "--confirm")
            _, out, _ = run("repo", "profile")
        self.assertNotIn("LOW confidence", out)
        self.assertIn("confirmed", out)

    def test_setting_a_preset_survives_the_context_binding(self) -> None:
        """Regression shape: the config holds flow and provider too."""
        self._config({"provider": "local", "flow": {"source": "main"}})
        run("repo", "profile", "--set", "enterprise")
        data = json.loads((self.root / ".workflow" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual("enterprise", data["preset"])
        self.assertEqual("local", data["provider"])
        self.assertEqual({"source": "main"}, data["flow"])

    def test_sdd_gates_reads_the_override_not_the_detection(self) -> None:
        """These two commands answer the same question and used to disagree:
        sdd gates went straight to detect() and ignored a recorded preset."""
        self._config({"preset": "enterprise", "preset_confirmed": True})
        _, out, _ = run("sdd", "gates")
        self.assertIn("enterprise", out)
        self.assertIn("backwards compatibility", out)

    def test_gates_for_paths_takes_the_highest_bar_a_change_lands_in(self) -> None:
        self._config(
            {
                "preset": "prototype",
                "preset_confirmed": True,
                "preset_paths": {"packages/billing/**": "enterprise"},
            }
        )
        _, out, _ = run("repo", "gates", "packages/billing/charge.ts", "src/ui.ts")
        self.assertIn("enterprise", out)
        self.assertIn("backwards compatibility", out)

    def test_gates_name_the_critical_zone_a_path_falls_in(self) -> None:
        self._config({"preset": "prototype", "preset_confirmed": True})
        _, out, _ = run("repo", "gates", "src/auth/session.py")
        self.assertIn("critical zone auth", out)

    def test_an_audit_fails_a_plan_declared_under_the_bar_its_files_demand(self) -> None:
        self._config({"preset": "startup", "preset_confirmed": True, "preset_paths": {"billing/**": "enterprise"}})
        doc = {"preset": "startup", "files": [{"path": "billing/charge.py", "change": "edit"}]}
        from workbench import audit as audit_lib

        problems = audit_lib._preset_problems(doc, self.root)
        self.assertEqual(1, len(problems))
        self.assertIn("held to enterprise", problems[0])

    def test_an_audit_stays_quiet_where_the_repo_has_said_nothing(self) -> None:
        """Detection is advice. A plan should not fail over a guess nobody made."""
        from workbench import audit as audit_lib

        doc = {"preset": "prototype", "files": [{"path": "billing/charge.py", "change": "edit"}]}
        self.assertEqual([], audit_lib._preset_problems(doc, self.root))


if __name__ == "__main__":
    unittest.main()
