"""`wb init` and `wb route`: getting to the first useful step without a manual.

Both exist for the same reason and are tested for the same property. `init` is
the answer to "I have a clone, now what", and `route` to "this is a one-line fix,
do I really do all ten of these". Neither may invent anything: init proposes and
never writes without being told, route computes the same tier the audit will
compute rather than negotiating a lower one.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import CliBase, run  # noqa: E402

from workbench.errors import EXIT_USAGE  # noqa: E402


class Init(CliBase):
    def _config(self) -> dict:
        path = self.root / ".workflow" / "config.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def test_it_writes_nothing_without_being_told(self) -> None:
        code, out, _ = run("init")
        self.assertEqual(0, code)
        self.assertIn("nothing written", out)
        self.assertEqual({}, self._config())

    def test_it_shows_the_file_before_writing_it(self) -> None:
        _, out, _ = run("init")
        self.assertIn('"provider"', out)
        self.assertIn('"preset"', out)

    def test_writing_produces_a_config_doctor_can_read(self) -> None:
        code, _, _ = run("init", "--write")
        self.assertEqual(0, code)
        config = self._config()
        self.assertIn("provider", config)
        self.assertIn("preset", config)

    def test_it_refuses_to_replace_a_config_by_accident(self) -> None:
        run("init", "--write")
        code, _, err = run("init", "--write")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("--force", err)

    def test_force_replaces_it(self) -> None:
        run("init", "--write")
        code, _, _ = run("init", "--write", "--force", "--preset", "enterprise")
        self.assertEqual(0, code)
        self.assertEqual("enterprise", self._config()["preset"])

    def test_it_never_drops_a_decision_already_recorded(self) -> None:
        """Re-running it must not silently undo a context binding or a flow."""
        path = self.root / ".workflow" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"context": "work-acme", "execute": False}), encoding="utf-8")

        run("init", "--write", "--force")
        config = self._config()
        self.assertEqual("work-acme", config["context"])
        self.assertIs(False, config["execute"])

    def test_a_named_preset_counts_as_confirmed(self) -> None:
        run("init", "--write", "--preset", "scaleup")
        self.assertTrue(self._config()["preset_confirmed"])

    def test_a_detected_preset_does_not(self) -> None:
        """Detection proposing is not the same as somebody agreeing."""
        run("init", "--write")
        self.assertFalse(self._config()["preset_confirmed"])

    def test_it_says_a_credential_is_still_needed(self) -> None:
        with mock.patch("workbench.gitctx.origin", return_value=_remote("github.com", "acme")):
            _, out, _ = run("init")
        self.assertIn("credential", out)
        self.assertIn("wb ctx add", out)

    def test_a_repo_with_no_tracker_remote_is_local_not_broken(self) -> None:
        """Nine of the ten skills never touch a tracker: no tracker is a
        supported setup, not an unfinished one."""
        with mock.patch("workbench.gitctx.origin", return_value=None):
            _, out, _ = run("init")
        self.assertIn('"provider": "local"', out)


class Route(CliBase):
    def _plan(self, key: str, paths: list[str]) -> None:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "sdd.json").write_text(
            json.dumps({"key": key, "files": [{"path": p} for p in paths], "steps": [], "verify": []}),
            encoding="utf-8",
        )

    def _triage(self, key: str, kind: str) -> None:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "triage.json").write_text(json.dumps({"type": kind}), encoding="utf-8")

    def test_a_small_change_gets_the_short_route(self) -> None:
        _, out, _ = run("route", "ABC-1", "--files", "src/util.py")
        self.assertIn("light route", out)
        self.assertNotIn("frame-product", out)

    def test_a_critical_zone_gets_the_full_one(self) -> None:
        _, out, _ = run("route", "ABC-1", "--files", "src/billing/charge.py")
        self.assertIn("standard route", out)
        self.assertIn("billing", out)

    def test_too_many_files_gets_the_full_one(self) -> None:
        _, out, _ = run("route", "ABC-1", "--files", "a.py", "b.py", "c.py")
        self.assertIn("standard route", out)

    def test_a_bug_ticket_always_owes_a_handover(self) -> None:
        """Owed by the ticket type, never by the size of the diff."""
        self._triage("ABC-1", "bug")
        _, out, _ = run("route", "ABC-1", "--files", "src/util.py")
        self.assertIn("write-handover", out)

    def test_an_existing_plan_decides_its_own_tier(self) -> None:
        """The route must agree with the audit, which reads the plan."""
        self._plan("ABC-1", ["src/a.py", "src/b.py", "src/c.py"])
        _, out, _ = run("route", "ABC-1")
        self.assertIn("standard route", out)

    def test_the_floor_is_never_described_as_waived(self) -> None:
        _, out, _ = run("route", "ABC-1", "--files", "src/util.py")
        self.assertIn("the floor is not waived", out)
        self.assertIn("citations", out)

    def test_json_names_the_skill_for_each_step(self) -> None:
        _, out, _ = run("route", "ABC-1", "--files", "src/util.py", "--json")
        steps = json.loads(out)["steps"]
        self.assertTrue(all(step["skill"] for step in steps))
        self.assertEqual("plan-change", next(s["skill"] for s in steps if s["step"] == "plan"))

    def test_with_no_key_and_nothing_in_flight_it_says_so(self) -> None:
        with mock.patch("workbench.gitctx.branch", return_value=None):
            code, _, err = run("route")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("wb task list", err)


def _remote(host: str, org: str):
    from workbench.gitctx import Remote

    return Remote(url=f"https://{host}/{org}/repo.git", host=host, org=org, repo="repo")


if __name__ == "__main__":
    unittest.main()
