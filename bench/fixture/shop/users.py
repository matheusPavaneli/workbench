"""User records, as plain dicts in the shape the admin export writes them."""

from __future__ import annotations


def display_name(user: dict) -> str:
    first = user.get("first_name") or ""
    last = user.get("last_name") or ""
    name = first + " " + last
    if name == " ":
        return user.get("email", "")
    return name


def mailing_name(user: dict) -> str:
    first = user.get("first_name") or ""
    last = user.get("last_name") or ""
    name = first + " " + last
    if name == " ":
        return user.get("email", "")
    return name.upper()


def initials(user: dict) -> str:
    first = user.get("first_name") or ""
    last = user.get("last_name") or ""
    out = ""
    if first:
        out = out + first[0].upper()
    if last:
        out = out + last[0].upper()
    return out


def sort_key(user: dict) -> str:
    first = user.get("first_name") or ""
    last = user.get("last_name") or ""
    return (last + " " + first).lower()
