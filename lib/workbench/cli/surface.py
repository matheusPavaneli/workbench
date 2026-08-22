"""``wb surface`` -- every group, action and flag, read off the live parser.

A session composing an unfamiliar call has otherwise to infer the flags from
prose in a SKILL.md, and prose is where a flag that never existed comes from:
``wb pr check --key ABC-1`` was written that way, and argparse refused it.

The alternative shape is the one MCP uses -- publish a typed schema for every
tool and pay for it in the system prompt of every session, used or not. This
plugin already refuses that trade for its own commands: ``wb route`` exists as a
command rather than an eleventh skill for exactly this reason. So the schema is
here, complete, and costs nothing until something asks for it.

Walked from ``wb.build_parser()`` rather than kept by hand, because a
hand-kept list of flags is a second description of the CLI, and a second
description is the thing that drifts.
"""

from __future__ import annotations

import argparse

from .. import contract

ACTIONS: list[str] = []


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("surface", help="every group, action and flag this CLI accepts")
    # Not ``group``: the top-level parser already owns that dest, and a
    # positional of the same name overwrote it -- `wb surface task` dispatched
    # to `wb task` and failed there.
    parser.add_argument("of", nargs="?", metavar="GROUP", help="one group; omit for all of them")
    parser.add_argument("--json", action="store_true", help="machine-readable, for a session composing a call")


def run(args: argparse.Namespace) -> int:
    groups = _walk()
    if args.of:
        groups = [entry for entry in groups if entry["group"] == args.of]
        if not groups:
            from ..errors import UsageError

            known = ", ".join(entry["group"] for entry in _walk())
            raise UsageError(f"no such group: {args.of}", fix=[f"groups: {known}"])

    if args.json:
        print(contract.emit("surface", {"groups": groups}))
        return 0

    for entry in groups:
        for line in _render(entry):
            print(line)
    return 0


def _walk() -> list[dict]:
    """Every group, its actions, and the arguments each one accepts.

    Imported here rather than at module scope: ``wb`` imports this module to
    build the parser, so importing it back at load time would not resolve.
    """
    import wb

    parser = wb.build_parser()
    groups = []
    for name, sub in _choices(parser).items():
        actions = _choices(sub)
        if actions:
            entries = [
                {"action": action, "help": _help(sub, action), "arguments": _arguments(body)}
                for action, body in actions.items()
            ]
        else:
            entries = [{"action": "", "help": sub.description or "", "arguments": _arguments(sub)}]
        groups.append({"group": name, "actions": entries})
    return groups


def _choices(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _help(parent: argparse.ArgumentParser, name: str) -> str:
    """The one-line help argparse recorded for a subcommand."""
    for action in parent._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for choice in action._get_subactions():
            if choice.dest == name:
                return choice.help or ""
    return ""


def _arguments(parser: argparse.ArgumentParser) -> list[dict]:
    arguments = []
    for action in parser._actions:
        if isinstance(action, (argparse._SubParsersAction, argparse._HelpAction)):
            continue
        entry: dict = {
            "name": action.option_strings[0] if action.option_strings else action.dest,
            "positional": not action.option_strings,
            "required": bool(action.required),
            "help": action.help or "",
        }
        if len(action.option_strings) > 1:
            entry["aliases"] = action.option_strings[1:]
        if action.choices:
            entry["choices"] = [str(choice) for choice in action.choices]
        if action.nargs is not None:
            entry["nargs"] = str(action.nargs)
        # A flag that takes no value: worth saying, because the commonest
        # invented call passes one to a switch.
        entry["takes_value"] = not isinstance(
            action, (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._CountAction)
        )
        arguments.append(entry)
    return arguments


def _render(entry: dict) -> list[str]:
    lines = []
    for action in entry["actions"]:
        name = f"wb {entry['group']} {action['action']}".rstrip()
        lines.append(f"{name}{'   ' + action['help'] if action['help'] else ''}")
        for argument in action["arguments"]:
            shape = "" if argument["positional"] or not argument["takes_value"] else " <value>"
            marks = []
            if argument["positional"]:
                marks.append("positional")
            if argument["required"]:
                marks.append("required")
            if argument.get("choices"):
                marks.append("one of: " + ", ".join(argument["choices"]))
            suffix = f"   [{'; '.join(marks)}]" if marks else ""
            lines.append(f"  {argument['name']}{shape}{suffix}")
    return lines
