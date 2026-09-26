"""The CI workflow's lint job, held to what the README tells a contributor.

A lint tool installed without a pin drifts: a new ruff release adds a rule and
CI fails on a commit that changed nothing. A pin the README does not repeat
drifts the other way -- a local run passes and CI does not. Read as text: the
workflow file is YAML, and the suite depends on nothing outside the stdlib.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
README = ROOT / "README.md"

_INSTALL = re.compile(r"pip install (.+)$", re.MULTILINE)


def _installs(text: str) -> list[str]:
    return [spec for line in _INSTALL.findall(text) for spec in line.split()]


class LintJob(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_every_tool_the_workflow_installs_is_pinned_exactly(self) -> None:
        specs = _installs(self.workflow)
        self.assertTrue(specs, "the workflow installs nothing; the lint job is gone")
        loose = [spec for spec in specs if not re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9.]*", spec)]
        self.assertEqual(loose, [], "pin each tool with ==, or CI changes under an unchanged commit")

    def test_the_workflow_runs_ruff_and_mypy(self) -> None:
        steps = {line.strip() for line in self.workflow.splitlines()}
        self.assertIn("run: ruff check lib", steps)
        self.assertIn("run: mypy", steps)

    def test_the_readme_documents_the_versions_ci_installs(self) -> None:
        readme = README.read_text(encoding="utf-8")
        for spec in _installs(self.workflow):
            self.assertIn(spec, readme, f"README does not document {spec}, which CI installs")


if __name__ == "__main__":
    unittest.main()
