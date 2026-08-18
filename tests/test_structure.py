"""Rules about the shape of the source, enforced on the source itself.

The consistency tests hold two callers to one answer. These stop a third caller
from appearing that never goes through the resolver at all -- which is how the
divergence gets reintroduced: not by changing a resolved value, but by calling
the raw detector directly because it is one line shorter.

Read with `ast`, not with a regex: a comment or a docstring mentioning
`profile.detect` is not a call, and a test that cannot tell the difference is a
test people learn to work around.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"

sys.path.insert(0, str(LIB))

# Raw readers, and the resolver each one belongs behind. Calling the raw form
# outside its resolver skips whatever the repo has recorded -- which is the
# entire difference between "what this repo looks like" and "what this repo has
# decided", and the source of three separate bugs.
BEHIND_A_RESOLVER = {
    ("profile", "detect"): "profile.resolve",
    ("flow", "load"): "flow.resolve",
}

# Where the raw form is legitimate: inside the resolver itself, and in the tests
# that exercise detection on purpose.
ALLOWED_IN = {
    "profile.py": {("profile", "detect")},
    "flow.py": {("flow", "load")},
}


def _modules():
    return sorted(path for path in LIB.rglob("*.py") if "__pycache__" not in path.parts)


def _calls(tree: ast.AST):
    """Every call in the file, as ``(receiver, attribute)`` where it has one."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            yield func.value.id, func.attr, node.lineno


class Resolvers(unittest.TestCase):
    def test_no_module_reads_a_raw_detector_behind_the_resolver_s_back(self) -> None:
        offences = []
        for path in _modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            allowed = ALLOWED_IN.get(path.name, set())

            for receiver, attribute, line in _calls(tree):
                # The alias matters as much as the name: `profile as profile_lib`
                # is how every CLI module imports it.
                module = receiver.replace("_lib", "")
                key = (module, attribute)
                if key in BEHIND_A_RESOLVER and key not in allowed:
                    offences.append(
                        f"{path.relative_to(ROOT)}:{line} calls {receiver}.{attribute}(); "
                        f"use {BEHIND_A_RESOLVER[key]}() so the repo's own config is not skipped"
                    )

        self.assertEqual([], offences, "\n" + "\n".join(offences))

    def test_the_resolvers_this_guards_still_exist(self) -> None:
        """A guard naming a function that was renamed protects nothing."""
        from workbench import flow, profile

        self.assertTrue(callable(profile.resolve))
        self.assertTrue(callable(profile.detect))
        self.assertTrue(callable(flow.resolve))
        self.assertTrue(callable(flow.load))


class ExecutionSurface(unittest.TestCase):
    """One module writes to a repository. Keeping it that way is a property of
    the source, not a habit."""

    WRITERS = {"gitrun.py", "verify.py"}

    # Everything else that shells out reads: git plumbing, the OS keychain, and
    # `gh auth token`. Each is here by decision, and adding a name to this set
    # is the decision -- which is the point of asserting it.
    READERS = {"gitctx.py", "profile.py", "secrets.py", "github.py"}

    def test_only_the_execution_modules_run_a_subprocess(self) -> None:
        allowed = {*self.WRITERS, *self.READERS}
        offences = []

        for path in _modules():
            if path.name in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for receiver, attribute, line in _calls(tree):
                if receiver == "subprocess":
                    offences.append(f"{path.relative_to(ROOT)}:{line} calls subprocess.{attribute}()")

        self.assertEqual([], offences, "\n" + "\n".join(offences))

    def test_nothing_runs_a_command_through_a_shell(self) -> None:
        """shell=True anywhere in this package would make every allowlist in it
        decorative."""
        offences = []
        for path in _modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "shell" and not (
                        isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                    ):
                        offences.append(f"{path.relative_to(ROOT)}:{node.lineno} passes shell=")
        self.assertEqual([], offences, "\n" + "\n".join(offences))

    def test_every_subprocess_call_carries_a_timeout(self) -> None:
        """A git command that hangs hangs the session that called it."""
        offences = []
        for path in _modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "subprocess"):
                    continue
                if func.attr != "run":
                    continue
                if not any(keyword.arg == "timeout" for keyword in node.keywords):
                    offences.append(f"{path.relative_to(ROOT)}:{node.lineno} runs without a timeout")
        self.assertEqual([], offences, "\n" + "\n".join(offences))


if __name__ == "__main__":
    unittest.main()
