"""Deterministic Effect-argument constraints grounded in trusted task text."""
from __future__ import annotations

from datetime import datetime
import re


_ORDINAL = re.compile(r"(?<=\d)(?:st|nd|rd|th)\b", re.I)
_FORMATS = (
    "%Y-%m-%d",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d",
    "%b %d",
    "%d %B",
    "%d %b",
)


def _task_date(value: str) -> tuple[int | None, int, int] | None:
    text = _ORDINAL.sub("", str(value)).replace(",", " ")
    text = re.sub(r"\s+of\s+", " ", text, flags=re.I)
    text = " ".join(text.split())
    for fmt in _FORMATS:
        parse_text = text
        parse_fmt = fmt
        if "%Y" not in fmt:
            parse_text += " 2000"
            parse_fmt += " %Y"
        try:
            parsed = datetime.strptime(parse_text, parse_fmt)
        except ValueError:
            continue
        year = parsed.year if "%Y" in fmt else None
        return year, parsed.month, parsed.day
    return None


def _datetime(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def validate_constraint_spec(spec, trusted_task: str,
                             argument_names=()) -> str:
    """Return an error for an invalid Contract constraint, else ``""``."""
    if not isinstance(spec, dict) or set(spec) != {"constraint"}:
        return "constraint argument must contain exactly constraint"
    constraint = spec.get("constraint")
    if not isinstance(constraint, dict):
        return "constraint must be an object"
    operator = constraint.get("operator")
    if operator == "datetime_on_task_date":
        if set(constraint) != {"operator", "task_text"}:
            return "datetime_on_task_date fields mismatch"
        task_text = constraint.get("task_text")
        if (not isinstance(task_text, str) or not task_text.strip() or
                task_text.casefold() not in str(trusted_task).casefold()):
            return "constraint date is not an exact trusted-task span"
        if _task_date(task_text) is None:
            return "constraint task date is not parseable"
        return ""
    if operator == "datetime_after_argument":
        if set(constraint) != {"operator", "argument", "max_seconds"}:
            return "datetime_after_argument fields mismatch"
        argument = constraint.get("argument")
        if (not isinstance(argument, str) or
                argument not in set(map(str, argument_names))):
            return "constraint references an unknown argument"
        limit = constraint.get("max_seconds")
        if (not isinstance(limit, int) or isinstance(limit, bool) or
                not 0 < limit <= 86400):
            return "constraint max_seconds must be in 1..86400"
        return ""
    return "unknown constraint operator"


def constraint_matches(spec, value, arguments: dict) -> bool:
    """Replay one validated Effect-argument constraint without a model."""
    if not isinstance(spec, dict) or set(spec) != {"constraint"}:
        return False
    constraint = spec.get("constraint")
    if not isinstance(constraint, dict):
        return False
    proposed = _datetime(value)
    if proposed is None:
        return False
    operator = constraint.get("operator")
    if operator == "datetime_on_task_date":
        target = _task_date(constraint.get("task_text", ""))
        if target is None:
            return False
        year, month, day = target
        return ((year is None or proposed.year == year) and
                proposed.month == month and proposed.day == day)
    if operator == "datetime_after_argument":
        start = _datetime(dict(arguments or {}).get(
            str(constraint.get("argument", ""))))
        if start is None:
            return False
        try:
            seconds = (proposed - start).total_seconds()
        except TypeError:
            return False
        limit = constraint.get("max_seconds")
        return (isinstance(limit, int) and not isinstance(limit, bool) and
                0 < seconds <= limit)
    return False
