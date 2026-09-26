from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from workbench import audit, cite, events
from workbench.errors import EXIT_AUDIT, EXIT_USAGE

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import wb  # noqa: E402

LINE = "charge = create_charge(total)"


class Extract(unittest.TestCase):
    def test_a_quoted_citation_is_found_with_its_document_line(self) -> None:
        (found,) = cite.extract(f"intro\n- the charge runs first `src/checkout.py:2` — `{LINE}`\n")
        self.assertEqual((2, "src/checkout.py", 2, LINE), (found.at, found.path, found.line, found.quote))

    def test_other_separators_and_none_are_accepted(self) -> None:
        for separator in (" - ", ": ", " ", " – "):
            with self.subTest(separator=separator):
                (found,) = cite.extract(f"`src/a.py:3`{separator}`{LINE}`")
                self.assertEqual(LINE, found.quote)

    def test_a_reference_with_no_quote_is_found_as_unquoted(self) -> None:
        (found,) = cite.extract("see `src/checkout.py:2` for the charge")
        self.assertIsNone(found.quote)

    def test_fenced_blocks_are_examples_not_claims(self) -> None:
        text = f"```\n`src/checkout.py:2` — `{LINE}`\n```\n~~~\n`src/b.py:1`\n~~~\n"
        self.assertEqual([], cite.extract(text))

    def test_a_span_without_a_file_path_is_not_a_citation(self) -> None:
        self.assertEqual([], cite.extract("the job runs at `12:30` and `ratio:3`"))

    def test_several_citations_on_one_line(self) -> None:
        found = cite.extract(f"`a/x.py:1` — `{LINE}` and `b/y.py:2` — `{LINE}`")
        self.assertEqual(["a/x.py", "b/y.py"], [reference.path for reference in found])


class Checkout(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.git("init", "-q", ".")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.write(".gitignore", "scratch/\n")
        self.write("src/checkout.py", f"def pay(total):\n    {LINE}\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False)

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class Check(Checkout):
    def verdicts(self, text: str, **kwargs) -> list[str]:
        return [result.verdict for result in cite.check(text, self.root, **kwargs)]

    def test_a_true_citation_passes(self) -> None:
        self.assertEqual([audit.OK], self.verdicts(f"`src/checkout.py:2` — `{LINE}`"))

    def test_an_invented_quote_fails_and_names_the_document_line(self) -> None:
        (result,) = cite.check("x\n`src/checkout.py:2` — `charge = stripe.pay(total)`", self.root)
        self.assertEqual(audit.MISMATCH, result.verdict)
        self.assertEqual(2, result.reference.at)

    def test_an_unquoted_reference_fails(self) -> None:
        self.assertEqual([cite.UNQUOTED], self.verdicts("see `src/checkout.py:2`"))

    def test_by_default_an_uncommitted_line_fails(self) -> None:
        self.write("src/checkout.py", f"def pay(total):\n    {LINE}\n    refund_everything(total)\n")
        self.assertEqual([audit.UNCOMMITTED], self.verdicts("`src/checkout.py:3` — `refund_everything(total)`"))

    def test_the_worktree_mode_reads_the_disk(self) -> None:
        self.write("src/checkout.py", f"def pay(total):\n    {LINE}\n    refund_everything(total)\n")
        self.write("src/new.py", "def added_in_this_diff():\n")
        text = "`src/checkout.py:3` — `refund_everything(total)`\n`src/new.py:1` — `def added_in_this_diff():`"
        self.assertEqual([audit.OK, audit.OK], self.verdicts(text, worktree=True))

    def test_the_worktree_mode_still_refuses_artifacts_and_ignored_files(self) -> None:
        self.write(".workflow/ABC-1/notes.md", "the charge runs before validation\n")
        self.write("scratch/notes.md", "the charge runs before validation\n")
        text = (
            "`.workflow/ABC-1/notes.md:1` — `the charge runs before validation`\n"
            "`scratch/notes.md:1` — `the charge runs before validation`"
        )
        self.assertEqual([audit.ARTIFACT, audit.UNCOMMITTED], self.verdicts(text, worktree=True))


class Cli(Checkout):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(lambda: os.chdir(self._cwd))
        previous = os.environ.get("WORKBENCH_NO_EVENTS")
        os.environ["WORKBENCH_NO_EVENTS"] = "1"
        self.addCleanup(
            lambda: os.environ.pop("WORKBENCH_NO_EVENTS", None)
            if previous is None
            else os.environ.__setitem__("WORKBENCH_NO_EVENTS", previous)
        )

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = wb.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_clean_artifact_passes(self) -> None:
        self.write(".workflow/ABC-1/review.md", f"`src/checkout.py:2` — `{LINE}`\n")
        code, out, _ = self.run_cli("cite", "check", ".workflow/ABC-1/review.md")
        self.assertEqual(0, code)
        self.assertIn("1 citation(s) verified", out)

    def test_an_artifact_with_no_citations_passes_and_says_so(self) -> None:
        self.write("notes.md", "No findings.\n")
        code, out, _ = self.run_cli("cite", "check", "notes.md")
        self.assertEqual(0, code)
        self.assertIn("no citations found", out)

    def test_a_checked_artifact_logs_its_counts_per_verdict(self) -> None:
        os.environ.pop("WORKBENCH_NO_EVENTS", None)
        previous = os.environ.get("WORKBENCH_HOME")
        os.environ["WORKBENCH_HOME"] = str(self.root / "home")
        self.addCleanup(
            lambda: os.environ.pop("WORKBENCH_HOME", None)
            if previous is None
            else os.environ.__setitem__("WORKBENCH_HOME", previous)
        )
        self.write("review.md", "`src/checkout.py:2` — `charge = stripe.pay(total)`\n")
        code, _, _ = self.run_cli("cite", "check", "review.md")
        self.assertEqual(EXIT_AUDIT, code)
        (entry,) = events.read()
        self.assertEqual(("cite", "check"), (entry["group"], entry["action"]))
        self.assertEqual({"mismatch": 1}, entry["verdicts"])

    def test_a_failed_citation_exits_7_with_its_line(self) -> None:
        self.write("review.md", "finding\n`src/checkout.py:2` — `charge = stripe.pay(total)`\n")
        code, _, err = self.run_cli("cite", "check", "review.md")
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("line 2", err)
        self.assertIn("mismatch", err)

    def test_a_file_outside_the_checkout_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere) / "notes.md"
            outside.write_text("x\n", encoding="utf-8")
            self.assertEqual(EXIT_USAGE, self.run_cli("cite", "check", str(outside))[0])


if __name__ == "__main__":
    unittest.main()
