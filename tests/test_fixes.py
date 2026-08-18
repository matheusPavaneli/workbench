"""Every error carries a fix. These check the fix is worth following.

`WbError.fix` is an ordered list of concrete steps, and an agent follows them
literally -- which makes a wrong step worse than no step. The `clean-tree`
refusal said "commit or stash them first" while counting untracked files, which
`git stash` does not touch: following the advice left the refusal exactly where
it was, and burned the credibility of every other fix in the package.

Three properties, each testable without guessing at prose:

1. a fix names something that exists -- a real command, a real flag, a real file
2. a fix is actionable: it tells you what to do, not what went wrong
3. the fix for a refusal actually clears the refusal
"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"

sys.path.insert(0, str(LIB))

import wb  # noqa: E402
from workbench import gitrun  # noqa: E402

# A fix that starts with one of these is describing, not instructing.
DESCRIBING = ("this ", "that ", "it ", "there ", "the error", "something")

# Words that make a step unfollowable: the reader cannot act on a placeholder
# nobody defined.
VAGUE = ("somehow", "as appropriate", "if needed", "correctly", "properly")


def _fix_strings() -> list[tuple[Path, int, str]]:
    """Every literal string passed as a `fix=` argument, with where it came from."""
    found: list[tuple[Path, int, str]] = []
    for path in sorted(LIB.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "fix" or not isinstance(keyword.value, ast.List):
                    continue
                for element in keyword.value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        found.append((path, element.lineno, element.value))
                    elif isinstance(element, ast.JoinedStr):
                        # An f-string: keep the literal parts, which is where a
                        # command name would be.
                        literal = "".join(
                            part.value for part in element.values
                            if isinstance(part, ast.Constant) and isinstance(part.value, str)
                        )
                        if literal.strip():
                            found.append((path, element.lineno, literal))
    return found


class Actionable(unittest.TestCase):
    def setUp(self) -> None:
        self.fixes = _fix_strings()

    def test_there_are_fixes_to_check(self) -> None:
        """If the extraction breaks, every test below passes vacuously."""
        self.assertGreater(len(self.fixes), 40, "the fix extraction found almost nothing; it is probably broken")

    def test_no_fix_merely_restates_the_problem(self) -> None:
        offences = [
            f"{path.relative_to(ROOT)}:{line}  {text!r}"
            for path, line, text in self.fixes
            if text.lower().startswith(DESCRIBING)
        ]
        self.assertEqual([], offences, "\n" + "\n".join(offences))

    def test_no_fix_is_vague(self) -> None:
        offences = [
            f"{path.relative_to(ROOT)}:{line}  {text!r}"
            for path, line, text in self.fixes
            if any(word in text.lower() for word in VAGUE)
        ]
        self.assertEqual([], offences, "\n" + "\n".join(offences))

    def test_every_wb_command_named_in_a_fix_exists(self) -> None:
        """A fix pointing at a renamed command sends the reader nowhere."""
        groups = {name: _actions(module) for name, module in wb.GROUPS.items()}
        pattern = re.compile(r"\bwb ([a-z]+)(?: ([a-z-]+))?")
        offences = []

        for path, line, text in self.fixes:
            for group, action in pattern.findall(text):
                where = f"{path.relative_to(ROOT)}:{line}"
                if group not in groups:
                    offences.append(f"{where} names 'wb {group}', which is not a group")
                elif action and groups[group] and action not in groups[group]:
                    offences.append(f"{where} names 'wb {group} {action}', which is not an action")

        self.assertEqual([], offences, "\n" + "\n".join(offences))

    def test_every_flag_named_in_a_fix_exists_on_that_command(self) -> None:
        parser = wb.build_parser()
        known = set(_flags(parser))
        pattern = re.compile(r"(--[a-z][a-z-]+)")
        offences = []

        for path, line, text in self.fixes:
            for flag in pattern.findall(text):
                if flag not in known:
                    offences.append(f"{path.relative_to(ROOT)}:{line} names {flag}, which no command accepts")

        self.assertEqual([], offences, "\n" + "\n".join(offences))


class ClearsTheRefusal(unittest.TestCase):
    """The strongest form: do what the message says, and check it worked."""

    def test_stashing_clears_the_clean_tree_refusal(self) -> None:
        """Regression: the message said "commit or stash", but the check counted
        untracked files, which stash leaves alone -- so following it changed
        nothing. The check now reads tracked changes, which stash does clear."""
        from unittest import mock

        action = gitrun.Action(["switch", "-c", "x", "origin/main"], precondition=gitrun.CLEAN_TREE)
        root = Path(".")

        with mock.patch("workbench.gitctx.tracked_changes", return_value=["src/a.py"]):
            refusal = gitrun.precondition(action, root, [])
        self.assertIsNotNone(refusal)
        self.assertIn("stash", refusal or "")

        # What `git stash` does: tracked changes go away, untracked stay.
        with mock.patch("workbench.gitctx.tracked_changes", return_value=[]), mock.patch(
            "workbench.gitctx.changed_files", return_value=["scratch.md"]
        ):
            self.assertIsNone(gitrun.precondition(action, root, []))

    def test_starting_a_working_branch_clears_the_protected_refusal(self) -> None:
        from unittest import mock

        action = gitrun.Action(["commit", "-F", "m.txt"], precondition=gitrun.NOT_PROTECTED)
        root = Path(".")

        with mock.patch("workbench.gitctx.branch", return_value="main"):
            refusal = gitrun.precondition(action, root, ["main"])
        self.assertIsNotNone(refusal)
        self.assertIn("working branch", refusal or "")

        with mock.patch("workbench.gitctx.branch", return_value="feature/ABC-1"):
            self.assertIsNone(gitrun.precondition(action, root, ["main"]))

    def test_clearing_the_switch_re_enables_execution(self) -> None:
        import os

        previous = os.environ.get("WB_NO_EXECUTE")
        try:
            os.environ["WB_NO_EXECUTE"] = "1"
            self.assertIsNotNone(gitrun.disabled_reason(Path(".")))
            os.environ.pop("WB_NO_EXECUTE")
            self.assertIsNone(gitrun.disabled_reason(Path(".")))
        finally:
            if previous is not None:
                os.environ["WB_NO_EXECUTE"] = previous
            else:
                os.environ.pop("WB_NO_EXECUTE", None)


def _actions(module) -> list[str]:
    return list(getattr(module, "ACTIONS", []) or [])


def _flags(parser) -> list[str]:
    """Every long flag the parser knows, at any depth."""
    found: list[str] = []
    for action in parser._actions:  # noqa: SLF001 - argparse offers no public walk
        found.extend(option for option in action.option_strings if option.startswith("--"))
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            for sub in action.choices.values():
                found.extend(_flags(sub))
    return found


if __name__ == "__main__":
    unittest.main()
