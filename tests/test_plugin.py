"""Packaging invariants.

These are cheap to check and expensive to get wrong: a skill whose description
does not say what it produces will not chain, and a SKILL.md that has quietly
grown to two hundred lines costs tokens in every session that triggers it.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

# A SKILL.md is a route sheet, not a manual. Anything longer is reference
# material that belongs in references/ or docs/, loaded only if needed.
MAX_SKILL_LINES = 80
# Descriptions are in the system prompt of every session, whether or not the
# skill is used. This is the always-on cost of the whole plugin.
MAX_DESCRIPTION_CHARS = 260
# Ten skills at ~200 chars each. Roughly 500 tokens in every session, paid
# whether or not any of them is used. Raising this is a decision about what the
# whole plugin costs; drifting past it is not.
MAX_TOTAL_DESCRIPTION_CHARS = 2100

FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _skills():
    return sorted(path for path in SKILLS.glob("*/SKILL.md"))


def _actions(module) -> list:
    """The subcommands a CLI group registers, read off the parser it builds."""
    import argparse

    parser = argparse.ArgumentParser()
    module.register(parser.add_subparsers())
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for group in action.choices.values():
            for nested in group._actions:
                if isinstance(nested, argparse._SubParsersAction):
                    return list(nested.choices)
    return []


def _frontmatter(path: Path) -> dict:
    match = FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            name, _, value = line.partition(":")
            fields[name.strip()] = value.strip()
    return fields


class Skills(unittest.TestCase):
    def test_every_skill_has_frontmatter(self) -> None:
        for path in _skills():
            with self.subTest(skill=path.parent.name):
                fields = _frontmatter(path)
                self.assertIn("name", fields)
                self.assertIn("description", fields)

    def test_name_matches_the_directory(self) -> None:
        for path in _skills():
            with self.subTest(skill=path.parent.name):
                self.assertEqual(path.parent.name, _frontmatter(path)["name"])

    def test_description_states_an_output(self) -> None:
        """Without a stated output, nothing downstream knows to run next."""
        stems = ("writ", "produc", "report", "turn", "draft", "record", "execut", "trace")
        for path in _skills():
            with self.subTest(skill=path.parent.name):
                description = _frontmatter(path)["description"].lower()
                self.assertTrue(
                    any(stem in description for stem in stems),
                    f"{path.parent.name}: description does not state an output",
                )

    def test_description_states_when_to_use_it(self) -> None:
        trigger = re.compile(r"\buse (when|to|for|before|after)\b")
        for path in _skills():
            with self.subTest(skill=path.parent.name):
                description = _frontmatter(path)["description"].lower()
                self.assertRegex(description, trigger)

    def test_descriptions_stay_within_the_always_on_budget(self) -> None:
        for path in _skills():
            with self.subTest(skill=path.parent.name):
                self.assertLessEqual(len(_frontmatter(path)["description"]), MAX_DESCRIPTION_CHARS)

    def test_total_always_on_cost_is_bounded(self) -> None:
        """The one number that is paid in every session, used or not.

        It creeps one helpful clause at a time, so it is asserted rather than
        watched. Raising the ceiling is a decision; drifting past it is not.
        """
        total = sum(len(_frontmatter(p)["description"]) + len(p.parent.name) for p in _skills())
        self.assertLessEqual(total, MAX_TOTAL_DESCRIPTION_CHARS, f"always-on cost is {total} chars")

    def test_skill_bodies_stay_short(self) -> None:
        for path in _skills():
            with self.subTest(skill=path.parent.name):
                lines = len(path.read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(lines, MAX_SKILL_LINES, f"{path.parent.name} is {lines} lines")

    def test_skills_invoke_the_cli_through_the_plugin_root(self) -> None:
        """A hardcoded path works on the author's machine and nowhere else."""
        for path in _skills():
            with self.subTest(skill=path.parent.name):
                body = path.read_text(encoding="utf-8")
                if "wb.py" in body:
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}", body)

    def test_every_command_a_skill_names_exists(self) -> None:
        """A SKILL.md is an instruction an agent follows, not a document a
        person may or may not read, so a command that has been renamed or
        removed does not merely mislead -- it sends the session somewhere.

        Three skills drifted from the CLI unnoticed because nothing compared
        them.
        """
        import sys

        sys.path.insert(0, str(ROOT / "lib"))
        import wb

        groups = {name: set(_actions(module)) for name, module in wb.GROUPS.items()}
        quoted = re.compile(r"`(?:wb )?(" + "|".join(groups) + r")(?: ([a-z-]+))?[^`]*`")

        for path in _skills():
            body = path.read_text(encoding="utf-8")
            for group, action in quoted.findall(body):
                with self.subTest(skill=path.parent.name, command=f"{group} {action}".strip()):
                    if not action or not groups[group]:
                        continue  # a group with no subcommands, or named on its own
                    self.assertIn(
                        action, groups[group], f"{path.parent.name} names '{group} {action}', which does not exist"
                    )

    def test_every_command_is_reachable_from_a_skill_or_a_doc(self) -> None:
        """The other direction of the same seam.

        The test above stops a skill naming a command that does not exist. This
        one stops a command existing that nothing names: `wb next` and
        `wb repo gates` were both written before anything told a session they
        were there, which is the same defect from the other end -- a capability
        nobody can reach was not really delivered.
        """
        import sys

        sys.path.insert(0, str(ROOT / "lib"))
        import wb

        written = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [*_skills(), *(ROOT / "docs").glob("*.md"), ROOT / "README.md"]
        )

        unreachable = []
        for group, module in wb.GROUPS.items():
            actions = _actions(module)
            if not actions:
                if f"wb {group}" not in written:
                    unreachable.append(f"wb {group}")
                continue
            unreachable.extend(
                f"wb {group} {action}" for action in actions if f"{group} {action}" not in written
            )

        self.assertEqual([], unreachable, "named nowhere a session will read: " + ", ".join(unreachable))

    def test_no_skill_composes_a_tracker_query(self) -> None:
        """The whole point of the closed CLI surface."""
        for path in _skills():
            body = path.read_text(encoding="utf-8").lower()
            for forbidden in ("--jql", "--wiql", "--fields=", "jql=", "wiql="):
                with self.subTest(skill=path.parent.name, flag=forbidden):
                    self.assertNotIn(forbidden, body)


class Manifests(unittest.TestCase):
    def test_plugin_manifest_is_valid(self) -> None:
        data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("workbench", data["name"])
        for field in ("version", "description", "license"):
            self.assertIn(field, data)

    def test_marketplace_points_at_a_real_plugin(self) -> None:
        data = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertTrue(data["plugins"])
        for entry in data["plugins"]:
            source = (ROOT / entry["source"]).resolve()
            self.assertTrue((source / ".claude-plugin" / "plugin.json").is_file())

    def test_the_listing_names_every_skill_that_ships(self) -> None:
        """The listing is the only text most people read before installing.

        It claimed nine skills and named nine while the package shipped ten,
        and nothing noticed, because nothing was looking. A count in prose
        drifts the moment a skill is added; a check against the directory tree
        does not.
        """
        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        described = " ".join(entry["description"] for entry in marketplace["plugins"])
        for path in _skills():
            with self.subTest(skill=path.parent.name):
                self.assertIn(path.parent.name, described, f"{path.parent.name} is not in the marketplace listing")

    def test_every_declared_version_agrees_with_the_package(self) -> None:
        """A version copied by hand is a version nobody remembers to copy.

        There were four: the package, both manifests and the outbound user
        agent. The user agent had already fallen two releases behind without
        anything noticing, so the package is now the source and the rest are
        checked against it.
        """
        from workbench import __version__

        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(__version__, plugin["version"])
        self.assertEqual(__version__, marketplace["metadata"]["version"])

    def test_the_user_agent_carries_the_package_version(self) -> None:
        from workbench import __version__, http

        self.assertIn(__version__, http.USER_AGENT)

    def test_the_version_is_a_real_release_number(self) -> None:
        from workbench import __version__

        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_shared_references_exist_where_skills_point(self) -> None:
        pattern = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+\.md)")
        for path in _skills():
            for reference in pattern.findall(path.read_text(encoding="utf-8")):
                with self.subTest(skill=path.parent.name, reference=reference):
                    self.assertTrue((ROOT / reference).is_file(), f"missing {reference}")


if __name__ == "__main__":
    unittest.main()
