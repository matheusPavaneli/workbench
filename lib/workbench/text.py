"""Turning provider rich text into plain text.

Jira Cloud v3 returns descriptions and comments as ADF (a nested JSON document).
Azure DevOps returns HTML. Both have to become plain text before anything can
cap them by character count, and both are attacker-influenced input, so both
walks are depth- and size-bounded.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

MAX_DEPTH = 20
MAX_CHARS = 20_000

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"}
_DROP_TAGS = {"script", "style"}


def adf_to_text(node: Any, depth: int = 0) -> str:
    """Flatten an Atlassian Document Format node.

    Unknown node types are traversed rather than dropped: ADF gains node types
    over time, and silently losing a paragraph is worse than a rough rendering.
    """
    if depth > MAX_DEPTH or node is None:
        return ""

    if isinstance(node, list):
        return "".join(adf_to_text(child, depth + 1) for child in node)

    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type")

    if node_type == "text":
        return str(node.get("text", ""))
    if node_type == "hardBreak":
        return "\n"
    if node_type == "mention":
        attrs = node.get("attrs") or {}
        return f"@{attrs.get('text', 'unknown').lstrip('@')}"
    if node_type == "emoji":
        attrs = node.get("attrs") or {}
        return str(attrs.get("shortName", ""))
    if node_type in {"inlineCard", "blockCard"}:
        attrs = node.get("attrs") or {}
        return str(attrs.get("url", ""))
    if node_type == "rule":
        return "\n---\n"
    if node_type == "mediaSingle" or node_type == "media":
        return "[attachment]"

    inner = "".join(adf_to_text(child, depth + 1) for child in node.get("content", []))

    if node_type in {"paragraph", "heading", "blockquote"}:
        return inner + "\n\n"
    if node_type == "listItem":
        return "- " + inner.strip() + "\n"
    if node_type == "codeBlock":
        attrs = node.get("attrs") or {}
        language = attrs.get("language", "")
        return f"\n```{language}\n{inner.strip()}\n```\n"
    if node_type in {"tableRow"}:
        return inner.strip() + "\n"
    if node_type in {"tableCell", "tableHeader"}:
        return inner.strip() + " | "

    return inner


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _DROP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    stripper = _Stripper()
    try:
        stripper.feed(raw[:MAX_CHARS])
        stripper.close()
    except Exception:  # noqa: BLE001 - malformed markup must degrade, not crash
        return normalise(re.sub(r"<[^>]+>", " ", html.unescape(raw[:MAX_CHARS])))
    return normalise("".join(stripper.parts))


def normalise(text: str) -> str:
    """Collapse runs of blank lines and trailing whitespace."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cut on a word boundary when one is close enough to the limit."""
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.8:
        cut = cut[:space]
    return cut.rstrip() + "…", True
