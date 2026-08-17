import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import profile


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


if __name__ == "__main__":
    unittest.main()
