"""Companions: files a planned change carries through the scope guard unlisted.

Every rule is tested with its negative case, because the negative case is the
guard: a companion rule that released too much would be a hole with a
docstring.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import wb  # noqa: E402
from workbench import audit, companions  # noqa: E402
from workbench.errors import EXIT_AUDIT  # noqa: E402


def tie(path: str, *planned: str, generated: list[str] | None = None) -> str | None:
    return companions.reason(path, set(planned), generated)


class Tests(unittest.TestCase):
    def test_python_test_prefix(self) -> None:
        self.assertEqual("test of src/util.py", tie("tests/test_util.py", "src/util.py"))

    def test_python_test_suffix(self) -> None:
        self.assertEqual("test of src/util.py", tie("src/util_test.py", "src/util.py"))

    def test_go_test(self) -> None:
        self.assertEqual("test of pkg/util.go", tie("pkg/util_test.go", "pkg/util.go"))

    def test_js_test_and_spec(self) -> None:
        self.assertEqual("test of src/util.ts", tie("src/util.test.ts", "src/util.ts"))
        self.assertEqual("test of src/util.ts", tie("test/util.spec.tsx", "src/util.ts"))

    def test_a_file_under_tests_dir_marker(self) -> None:
        self.assertEqual("test of src/util.js", tie("src/__tests__/util.js", "src/util.js"))

    def test_a_test_whose_stem_matches_no_planned_source_is_not_released(self) -> None:
        self.assertIsNone(tie("tests/test_other.py", "src/util.py"))

    def test_a_test_in_another_language_is_not_released(self) -> None:
        self.assertIsNone(tie("pkg/util_test.go", "src/util.py"))

    def test_a_planned_test_does_not_release_another_test(self) -> None:
        self.assertIsNone(tie("tests/test_util.py", "tests/util_test.py"))

    def test_a_source_file_with_the_same_stem_is_not_a_test(self) -> None:
        self.assertIsNone(tie("lib/util.py", "src/util.py"))


class Declarations(unittest.TestCase):
    def test_a_declaration_beside_a_planned_ts_or_js(self) -> None:
        self.assertEqual("declaration of src/util.ts", tie("src/util.d.ts", "src/util.ts"))
        self.assertEqual("declaration of src/util.js", tie("src/util.d.ts", "src/util.js"))

    def test_a_declaration_in_another_directory_is_not_released(self) -> None:
        self.assertIsNone(tie("types/util.d.ts", "src/util.ts"))


class Lockfiles(unittest.TestCase):
    def test_a_lockfile_follows_its_planned_manifest(self) -> None:
        self.assertEqual("lockfile of package.json", tie("package-lock.json", "package.json"))
        self.assertEqual("lockfile of web/package.json", tie("web/pnpm-lock.yaml", "web/package.json"))
        self.assertEqual("lockfile of pyproject.toml", tie("uv.lock", "pyproject.toml"))
        self.assertEqual("lockfile of go.mod", tie("go.sum", "go.mod"))
        self.assertEqual("lockfile of Cargo.toml", tie("Cargo.lock", "Cargo.toml"))

    def test_a_lockfile_without_its_manifest_stays_a_deviation(self) -> None:
        """Unplanned dependency drift is exactly what the guard is for."""
        self.assertIsNone(tie("package-lock.json", "src/index.ts"))

    def test_a_manifest_in_another_directory_does_not_release_it(self) -> None:
        self.assertIsNone(tie("package-lock.json", "web/package.json"))


class Generated(unittest.TestCase):
    def test_a_configured_glob_releases_a_match(self) -> None:
        self.assertEqual(
            "generated, matches src/gen/**", tie("src/gen/api/client.py", "src/util.py", generated=["src/gen/**"])
        )
        self.assertIsNotNone(tie("src/api_pb2.py", "src/util.py", generated=["*_pb2.py", "src/*_pb2.py"]))

    def test_no_glob_no_release(self) -> None:
        self.assertIsNone(tie("src/gen/api/client.py", "src/util.py"))
        self.assertIsNone(tie("src/gen/api/client.py", "src/util.py", generated=[]))

    def test_a_path_the_glob_does_not_match_is_not_released(self) -> None:
        self.assertIsNone(tie("src/api/client.py", "src/util.py", generated=["src/gen/**"]))

    def test_globs_come_from_config_and_only_as_a_list_of_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".workflow").mkdir()
            config = root / ".workflow" / "config.json"
            config.write_text(json.dumps({"generated": ["src/gen/**", 3, ""]}), encoding="utf-8")
            self.assertEqual(["src/gen/**"], companions.generated_globs(root))
            config.write_text(json.dumps({"generated": "src/gen/**"}), encoding="utf-8")
            self.assertEqual([], companions.generated_globs(root))


class CriticalZones(unittest.TestCase):
    def test_a_zone_is_never_a_companion(self) -> None:
        self.assertIsNone(tie("tests/test_billing.py", "src/billing.py"))
        self.assertIsNone(tie("src/gen/auth/client.py", "src/util.py", generated=["src/gen/**"]))

    def test_the_planned_path_itself_is_not_a_companion(self) -> None:
        self.assertIsNone(tie("src/util.py", "src/util.py"))


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


class ImplCheck(unittest.TestCase):
    """impl check and the edit hook call the same function; this is impl check's half."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        _git(self.root, "init", "-q", ".")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "T")
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "util.py").write_bytes(b"x = 1\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "init")

        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        previous = os.environ.get("WORKBENCH_NO_EVENTS")
        os.environ["WORKBENCH_NO_EVENTS"] = "1"
        self.addCleanup(self._restore, previous)

        files = [{"path": "src/util.py", "change": "edit", "lines": 500, "why": "w"}]
        directory = self.root / ".workflow" / "ABC-1"
        directory.mkdir(parents=True)
        doc = {"key": "ABC-1", "files": files}
        (directory / "sdd.json").write_text(json.dumps(doc), encoding="utf-8")
        (directory / "audit.json").write_text(
            json.dumps({"verdict": "pass", "plan_sha256": audit.digest(doc)}), encoding="utf-8"
        )

    def _restore(self, previous: str | None) -> None:
        if previous is None:
            os.environ.pop("WORKBENCH_NO_EVENTS", None)
        else:
            os.environ["WORKBENCH_NO_EVENTS"] = previous

    def _check(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = wb.main(["impl", "check", "ABC-1"])
        return code, out.getvalue(), err.getvalue()

    def test_a_companion_is_listed_apart_and_passes(self) -> None:
        (self.root / "src" / "util.py").write_bytes(b"x = 2\n")
        (self.root / "tests" / "test_util.py").write_bytes(b"assert True\n")
        code, out, _ = self._check()
        self.assertEqual(0, code)
        self.assertIn("  companion tests/test_util.py  (test of src/util.py)", out)
        self.assertIn("1 companion(s)", out)
        self.assertNotIn("ok        tests/test_util.py", out)

    def test_a_file_with_no_tie_is_still_a_deviation(self) -> None:
        (self.root / "tests" / "test_other.py").write_bytes(b"assert True\n")
        code, _, err = self._check()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("tests/test_other.py", err)

    def test_a_lockfile_without_its_manifest_is_a_deviation(self) -> None:
        (self.root / "package-lock.json").write_bytes(b"{}\n")
        code, _, err = self._check()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("package-lock.json", err)


if __name__ == "__main__":
    unittest.main()
