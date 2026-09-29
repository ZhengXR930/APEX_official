"""Deterministic operator algebra for Contract Conditional clauses.

This module is the only registry for Conditional operand types and replay
semantics. Operators are pure, fail closed, and never invoke a model.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from apex.defender.state import UNRESOLVED, stable


OPERATOR_ARITY = {
    "identity": 1, "singleton": 1, "count": 1, "map_count": 1,
    "union": 1, "flatten": 1, "keys": 1, "frequency": 1,
    "difference": 2, "argmin": 2, "argmax": 2, "coalesce": 2,
    "aligned_lookup": 3, "object_set": 3, "sort_by": 3,
    "basename": 1, "path_join": 2, "gt": 3, "lt": 3,
    "field": 2, "project": 2, "select_eq": 3,
    "add": 2, "multiply": 2, "percent_of": 2,
    "datetime_combine": 2, "add_duration": 2, "interval_free": 3,
}

CLOSING_OPERATORS = frozenset({
    "argmin", "argmax", "coalesce", "gt", "interval_free", "lt",
    "select_eq",
})


def operator_operand_type(operator: str, index: int) -> str:
    """Return the closed operator's structural requirement for one operand.

    This is part of the operator algebra, not application policy.  ``any``
    means the operator imposes no useful precondition at that position.
    """
    if operator in {"add", "multiply", "percent_of"}:
        return "number"
    if operator in {"gt", "lt"}:
        return "number" if index in {1, 2} else "any"
    if operator in {"argmax", "argmin"}:
        return "number-list" if index == 1 else "collection"
    if operator in {"count", "frequency"}:
        return "collection"
    if operator in {"map_count", "flatten"}:
        return "collection-list"
    if operator == "union":
        return "collection-list"
    if operator == "difference":
        return "collection"
    if operator == "keys":
        return "object"
    if operator == "field":
        return "string" if index == 1 else "object"
    if operator == "project":
        return "string" if index == 1 else "collection"
    if operator == "object_set":
        return "object" if index == 0 else "string" if index == 1 else "any"
    if operator == "sort_by":
        return "collection"
    if operator == "select_eq":
        return ("collection" if index == 0 else
                "string" if index == 1 else "any")
    if operator == "interval_free":
        return "collection" if index == 0 else "datetime"
    if operator in {"normalize_date", "datetime_combine", "add_duration", "basename",
                    "path_join"}:
        return "string"
    if operator == "aligned_lookup":
        return "collection" if index in {0, 1} else "any"
    return "any"


def _is_number(value) -> bool:
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        return False
    try:
        Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return True


def operator_value_matches(kind: str, value) -> bool:
    """Validate a materialized value against an operator operand type."""
    if kind == "any":
        return True
    if kind == "number":
        return _is_number(value)
    if kind == "number-list":
        return (isinstance(value, (list, tuple)) and bool(value) and
                all(_is_number(item) for item in value))
    if kind == "collection":
        return isinstance(value, (list, tuple, dict))
    if kind == "collection-list":
        return (isinstance(value, (list, tuple)) and
                all(isinstance(item, (list, tuple, dict)) for item in value))
    if kind == "object":
        return isinstance(value, dict)
    if kind == "string":
        return isinstance(value, str)
    if kind == "datetime":
        if not isinstance(value, str):
            return False
        try:
            datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return False
        return True
    raise ValueError(f"unknown operator operand type: {kind}")


def replay_operator(operator: str, operands: list):
    """Replay one closed Conditional operator; raise on an unknown operator."""
    if operator == "identity":
        return operands[0]
    if operator == "coalesce":
        return next((value for value in operands if value is not UNRESOLVED),
                    UNRESOLVED)
    if operator == "singleton":
        return [operands[0]]
    if operator == "count":
        return len(operands[0])
    if operator == "map_count":
        groups = operands[0]
        if not isinstance(groups, (list, tuple)):
            raise ValueError("map_count needs a collection of collections")
        if any(not isinstance(group, (list, tuple, dict))
               for group in groups):
            raise ValueError("map_count items must be collections")
        return [len(group) for group in groups]
    if operator == "flatten":
        groups = operands[0]
        if not isinstance(groups, (list, tuple)) or any(
                not isinstance(group, (list, tuple)) for group in groups):
            raise ValueError("flatten needs a collection of collections")
        return [item for group in groups for item in group]
    if operator == "union":
        groups = operands[0]
        if not isinstance(groups, (list, tuple)) or any(
                not isinstance(group, (list, tuple)) for group in groups):
            raise ValueError("union needs a collection of collections")
        merged: list = []
        for group in groups:
            for item in group:
                if item not in merged:
                    merged.append(item)
        return merged
    if operator == "difference":
        removed = operands[1]
        return [item for item in operands[0] if item not in removed]
    if operator == "field":
        value, field = operands
        if not isinstance(field, str):
            raise ValueError("field name must be a string")
        if isinstance(value, dict) and field in value:
            return value[field]
        if hasattr(value, field):
            return getattr(value, field)
        raise ValueError("field is absent")
    if operator == "keys":
        value = operands[0]
        if not isinstance(value, dict):
            raise ValueError("keys needs an object")
        return list(value)
    if operator == "project":
        items, field = operands
        if not isinstance(items, (list, tuple)) or not isinstance(field, str):
            raise ValueError("project needs a collection and field")
        projected = []
        for item in items:
            if isinstance(item, dict) and field in item:
                projected.append(item[field])
            elif hasattr(item, field):
                projected.append(getattr(item, field))
            else:
                raise ValueError("project field is absent")
        return projected
    if operator == "object_set":
        value, field, child = operands
        if not isinstance(value, dict) or not isinstance(field, str):
            raise ValueError("object_set needs an object and field")
        return {**value, field: child}
    if operator == "frequency":
        items = operands[0]
        if not isinstance(items, (list, tuple)):
            raise ValueError("frequency needs a collection")
        records, positions = [], {}
        for item in items:
            key = stable(item)
            if key not in positions:
                positions[key] = len(records)
                records.append({"value": item, "count": 0})
            records[positions[key]]["count"] += 1
        return records
    if operator == "sort_by":
        items, fields, directions = operands
        if (not isinstance(items, (list, tuple)) or
                not isinstance(fields, (list, tuple)) or not fields or
                not isinstance(directions, (list, tuple)) or
                len(fields) != len(directions) or
                any(direction not in {"asc", "desc"}
                    for direction in directions)):
            raise ValueError("sort_by needs aligned fields and directions")
        result = list(items)
        for field, direction in reversed(list(zip(fields, directions))):
            if not isinstance(field, str):
                raise ValueError("sort_by fields must be strings")
            try:
                result.sort(
                    key=lambda item: (item[field] if isinstance(item, dict)
                                      else getattr(item, field)),
                    reverse=direction == "desc")
            except (KeyError, AttributeError, TypeError):
                raise ValueError("sort_by field is absent or incomparable")
        return result
    if operator == "select_eq":
        items, field, expected = operands
        if not isinstance(items, (list, tuple)) or not isinstance(field, str):
            raise ValueError("select_eq needs a collection and field")

        def equal(left, right):
            if isinstance(left, str) and isinstance(right, str):
                return left.casefold() == right.casefold()
            if (_is_number(left) and _is_number(right)):
                return Decimal(str(left)) == Decimal(str(right))
            return type(left) is type(right) and left == right

        matches = []
        for item in items:
            actual = (item.get(field, UNRESOLVED)
                      if isinstance(item, dict)
                      else getattr(item, field, UNRESOLVED))
            if actual is not UNRESOLVED and equal(actual, expected):
                matches.append(item)
        if len(matches) != 1:
            raise ValueError("select_eq requires one unique match")
        return matches[0]
    if operator == "add":
        if any(isinstance(value, bool) for value in operands):
            raise ValueError("add operands must be numeric")
        try:
            total = sum(Decimal(str(value)) for value in operands)
        except (InvalidOperation, ValueError):
            raise ValueError("add operands must be numeric")
        return int(total) if total == total.to_integral() else float(total)
    if operator in {"multiply", "percent_of"}:
        if any(isinstance(value, bool) for value in operands):
            raise ValueError(f"{operator} operands must be numeric")
        try:
            left, right = (Decimal(str(value)) for value in operands)
            result = left * right
            if operator == "percent_of":
                result /= Decimal(100)
        except (InvalidOperation, ValueError):
            raise ValueError(f"{operator} operands must be numeric")
        return (int(result) if result == result.to_integral()
                else float(result))
    if operator == "normalize_date":
        if len(operands) != 1 or not isinstance(operands[0], str):
            raise ValueError("normalize_date needs one string")
        raw = re.sub(r"(?i)(?<=\d)(?:st|nd|rd|th)\b", "", operands[0])
        raw = re.sub(r"\s+", " ", raw.strip().replace(",", ""))
        formats = (
            "%Y-%m-%d", "%Y/%m/%d",
            "%B %d %Y", "%b %d %Y",
            "%d %B %Y", "%d %b %Y",
        )
        for fmt in formats:
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise ValueError("unsupported date")
    if operator == "datetime_combine":
        date, time = map(str, operands)
        return datetime.fromisoformat(date + "T" + time).strftime(
            "%Y-%m-%d %H:%M")
    if operator == "add_duration":
        start, duration = operands
        match = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)?\s*(?:-\s*)?"
            r"(minute|minutes|hour|hours)\s*",
            str(duration), re.I)
        if not match:
            words = {"one": 1, "two": 2, "three": 3, "four": 4}
            match = re.fullmatch(
                r"\s*(one|two|three|four)\s*(?:-\s*)?"
                r"(minute|minutes|hour|hours)\s*",
                str(duration), re.I)
            if not match:
                raise ValueError("unsupported duration")
            amount = Decimal(words[match.group(1).casefold()])
        else:
            amount = Decimal(match.group(1) or "1")
        unit = match.group(2).casefold()
        minutes = float(amount * (60 if unit.startswith("hour") else 1))
        value = datetime.fromisoformat(str(start).replace(" ", "T"))
        return (value + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    if operator == "interval_free":
        events, start, end = operands
        if not isinstance(events, (list, tuple)):
            raise ValueError("interval_free events must be a collection")
        lower = datetime.fromisoformat(str(start).replace(" ", "T"))
        upper = datetime.fromisoformat(str(end).replace(" ", "T"))
        if lower >= upper:
            raise ValueError("interval_free requires a positive interval")
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("interval_free event must be an object")
            event_start = datetime.fromisoformat(
                str(event["start_time"]).replace(" ", "T"))
            event_end = datetime.fromisoformat(
                str(event["end_time"]).replace(" ", "T"))
            if lower < event_end and event_start < upper:
                return UNRESOLVED
        return start
    if operator in ("gt", "lt"):
        candidate, score, threshold = operands
        if isinstance(score, bool) or isinstance(threshold, bool):
            raise ValueError("gt/lt operands must be numeric")
        try:
            left, right = Decimal(str(score)), Decimal(str(threshold))
        except (InvalidOperation, ValueError):
            raise ValueError("gt/lt operands must be numeric")
        passed = left > right if operator == "gt" else left < right
        return candidate if passed else UNRESOLVED
    if operator in ("argmax", "argmin"):
        items, scores = operands[0], operands[1]
        if len(items) != len(scores) or not items:
            raise ValueError("argmax/argmin need aligned non-empty operands")
        pick = max if operator == "argmax" else min
        index = pick(range(len(scores)), key=lambda i: scores[i])
        return items[index]
    if operator == "aligned_lookup":
        keys, values, selected = operands
        if (not isinstance(keys, (list, tuple)) or
                not isinstance(values, (list, tuple)) or
                len(keys) != len(values)):
            raise ValueError("aligned_lookup needs aligned collections")
        matches = [index for index, key in enumerate(keys)
                   if key == selected and type(key) is type(selected)]
        if len(matches) != 1:
            raise ValueError("aligned_lookup requires one unique key")
        return values[matches[0]]
    if operator == "basename":
        return str(operands[0]).rstrip("/").rsplit("/", 1)[-1]
    if operator == "path_join":
        return str(operands[0]).rstrip("/") + "/" + str(operands[1])
    raise ValueError(f"unknown Conditional operator: {operator}")
