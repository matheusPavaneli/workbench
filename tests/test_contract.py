"""The `--json` surface, held still.

Exit codes have been a contract since the beginning; the payloads were whatever
dict the command happened to build. A field renamed by an edit that looked local
would reach every consumer at runtime -- and a skill inside this package can be
fixed in the same commit, while one in somebody else's cannot.

These tests are the version bump made unavoidable. Adding a key is compatible
and passes. Removing or renaming one fails until `contract.VERSIONS` is raised,
which is exactly the moment somebody should be thinking about it.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import wb  # noqa: E402
from workbench import contract  # noqa: E402


class Declared(unittest.TestCase):
    def test_every_recorded_key_set_names_a_versioned_payload(self) -> None:
        for command in contract.KEYS:
            self.assertIn(command, contract.VERSIONS, f"{command} has keys recorded but no version")

    def test_every_version_is_a_positive_integer(self) -> None:
        for command, version in contract.VERSIONS.items():
            self.assertIsInstance(version, int, command)
            self.assertGreaterEqual(version, 1, command)

    def test_emitting_an_undeclared_command_is_refused(self) -> None:
        """Better a loud failure at development time than an unversioned
        payload reaching a consumer."""
        with self.assertRaises(KeyError):
            contract.emit("not.a.command", {})

    def test_the_stamp_leads_the_payload(self) -> None:
        emitted = json.loads(contract.emit("next", {"key": "ABC-1"}))
        self.assertEqual(contract.VERSIONS["next"], emitted["schema"])
        self.assertEqual("schema", next(iter(emitted)))


class RealOutput(unittest.TestCase):
    """Recorded keys checked against what the commands actually print, so the
    record cannot drift from the truth it describes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(lambda: os.chdir(self._cwd))

        for name, value in (("WORKBENCH_HOME", str(self.root / "home")), ("WORKBENCH_NO_EVENTS", "1")):
            previous = os.environ.get(name)
            os.environ[name] = value
            self.addCleanup(self._restore, name, previous)

        patcher = mock.patch("workbench.gitctx.repo_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

        directory = self.root / ".workflow" / "ABC-1"
        directory.mkdir(parents=True)
        (directory / "triage.json").write_text(json.dumps({"title": "a thing"}), encoding="utf-8")

    def _restore(self, name: str, previous) -> None:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous

    def _payload(self, *argv: str) -> dict:
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("workbench.gitctx.branch", return_value=None):
            wb.main(list(argv))
        return json.loads(out.getvalue())

    def _check(self, command: str, *argv: str) -> None:
        payload = self._payload(*argv)
        expected = contract.KEYS[command]
        missing = expected - set(payload)
        self.assertEqual(
            set(),
            missing,
            f"wb {' '.join(argv)} no longer emits {sorted(missing)}; "
            f"raise contract.VERSIONS[{command!r}] if that is deliberate",
        )
        self.assertEqual(contract.VERSIONS[command], payload["schema"])

    def test_next_keeps_its_keys(self) -> None:
        self._check("next", "next", "--json")

    def test_route_keeps_its_keys(self) -> None:
        self._check("route", "route", "ABC-1", "--files", "src/a.py", "--json")

    def test_repo_profile_keeps_its_keys(self) -> None:
        self._check("repo.profile", "repo", "profile", "--json")

    def test_repo_gates_keeps_its_keys(self) -> None:
        self._check("repo.gates", "repo", "gates", "src/a.py", "--json")

    def test_flow_show_keeps_its_keys(self) -> None:
        self._check("flow.show", "flow", "show", "--json")

    def test_every_json_command_stamps_a_schema(self) -> None:
        """The property that makes the rest of this possible: a consumer can
        always tell which shape it is looking at."""
        for argv in (
            ("next", "--json"),
            ("route", "ABC-1", "--files", "src/a.py", "--json"),
            ("repo", "profile", "--json"),
            ("repo", "gates", "src/a.py", "--json"),
            ("flow", "show", "--json"),
        ):
            with self.subTest(command=" ".join(argv)):
                self.assertIn("schema", self._payload(*argv))

    def test_adding_a_key_stays_compatible(self) -> None:
        """The record is a floor, not an exact match: a new field must not fail
        a build, or every addition would need a version bump it does not owe."""
        payload = self._payload("next", "--json")
        payload["something_new"] = True
        self.assertEqual(set(), contract.KEYS["next"] - set(payload))


if __name__ == "__main__":
    unittest.main()
