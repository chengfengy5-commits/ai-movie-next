"""Pure parsing, formatting and matching helpers for storyboard assets."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any

from haoai_backend.asset_data import naming


def parse_json_array_lenient(raw: str) -> tuple[list[Any] | None, bool]:
    """Parse a JSON array, tolerating fences, prose, numeric-key objects and truncation."""
    text = (raw or "").strip()
    if not text:
        return None, False
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    else:
        text = re.sub(r"^\s*```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        value = json.loads(text)
        decoded_json = True
    except (json.JSONDecodeError, TypeError):
        value = None
        decoded_json = False
    if isinstance(value, list):
        return value, True
    if isinstance(value, dict):
        numeric_rows = _numeric_key_rows(value)
        return (numeric_rows, True) if numeric_rows is not None else (None, False)
    if decoded_json:
        return None, False
    array_source = text
    if text.startswith("{"):
        object_end = _matching_delimiter(text, 0, "{", "}")
        if object_end is None:
            return None, False
        try:
            object_value = json.loads(text[:object_end + 1])
        except json.JSONDecodeError:
            return None, False
        if isinstance(object_value, dict):
            numeric_rows = _numeric_key_rows(object_value)
            if numeric_rows is not None:
                return numeric_rows, True
        array_source = text[object_end + 1:]

    for start in _array_starts(array_source):
        candidate, complete = _first_array(array_source, start)
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return parsed, complete
        except json.JSONDecodeError:
            salvaged = _salvage_array_items(candidate)
            if salvaged:
                return salvaged, False
    return None, False


def _numeric_key_rows(value: Mapping[str, Any]) -> list[Any] | None:
    if not value:
        return None
    rows: list[tuple[int, Any]] = []
    for key, item in value.items():
        try:
            index = int(str(key).strip())
        except (TypeError, ValueError):
            return None
        rows.append((index, item))
    rows.sort(key=lambda pair: pair[0])
    return [item for _, item in rows]


def _array_starts(text: str) -> list[int]:
    starts: list[int] = []
    object_depth = 0
    array_depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            object_depth += 1
        elif char == "}":
            object_depth = max(0, object_depth - 1)
        elif char == "[":
            if object_depth == 0 and array_depth == 0:
                next_char = text[index + 1:index + 2]
                if not next_char or next_char in " \t\r\n{[\"]0123456789tfn-":
                    starts.append(index)
                    if len(starts) == 20:
                        break
            array_depth += 1
        elif char == "]":
            array_depth = max(0, array_depth - 1)
    return starts


def _matching_delimiter(text: str, start: int, opening: str, closing: str) -> int | None:
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _first_array(text: str, start: int) -> tuple[str | None, bool]:
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start:index + 1], True
    candidate = text[start:]
    return candidate, False


def _salvage_array_items(candidate: str) -> list[Any]:
    """Keep only syntactically complete top-level items from a truncated array."""
    body = candidate[1:]
    items: list[Any] = []
    start = 0
    stack: list[str] = []
    quoted = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for index, char in enumerate(body):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if stack and stack[-1] == pairs[char]:
                stack.pop()
            elif char == "]" and not stack:
                break
        elif char == "," and not stack:
            _append_json_item(items, body[start:index])
            start = index + 1
    if not quoted and not stack and body[start:].strip():
        _append_json_item(items, body[start:])
    return items


def _append_json_item(items: list[Any], source: str) -> None:
    value = source.strip().rstrip(",").strip()
    if not value:
        return
    try:
        items.append(json.loads(value))
    except json.JSONDecodeError:
        return


def parse_extracted(kind: str, raw: str) -> list[Any]:
    rows, _complete = parse_json_array_lenient(raw)
    if not rows:
        return []
    if kind == "character":
        return rows
    result: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, str):
            name = row.strip()
            if name:
                result.append({"title" if kind == "scene" else "name": name, "description": ""})
            continue
        if not isinstance(row, Mapping):
            continue
        if kind == "scene":
            result.append({
                "title": row.get("title") or row.get("name", ""),
                "description": row.get("description") or row.get("visualPrompt", ""),
            })
        else:
            result.append({
                "name": row.get("name", ""),
                "description": row.get("description") or row.get("visualPrompt", ""),
            })
    return result


def parse_appearances(kind: str, raw: str) -> tuple[list[Any], bool]:
    rows, complete = parse_json_array_lenient(raw)
    if rows is None:
        return [], False
    parsed: list[Any] = []
    valid_shape = True
    for row in rows:
        if kind == "scene":
            raw_items = row if isinstance(row, list) else ([row] if row is not None else [])
            if not isinstance(row, (list, Mapping, str)) and row is not None:
                valid_shape = False
            title = ""
            for item in raw_items:
                if isinstance(item, str):
                    title = item.strip()
                elif isinstance(item, Mapping):
                    title = _as_text(item.get("title") or item.get("name") or "")
                else:
                    title = ""
                if title:
                    break
            parsed.append(title)
            continue
        raw_items = row if isinstance(row, list) else ([row] if row is not None else [])
        if not isinstance(row, (list, Mapping, str)) and row is not None:
            valid_shape = False
        names: list[str] = []
        for item in raw_items:
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, Mapping):
                field = "character" if kind == "character" else "prop"
                name = _as_text(item.get("name") or item.get(field) or "")
            else:
                name = ""
            if name:
                names.append(name)
        parsed.append(names)
    return parsed, complete and valid_shape


def _appearance_name(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("title") or ""
    return _as_text(value)


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else str(value or "").strip()


def format_storyboard_texts(texts: Sequence[str], frame_numbers: Sequence[int] | None) -> str:
    total = len(texts)
    labels = list(frame_numbers) if frame_numbers and len(frame_numbers) == total else list(range(1, total + 1))
    header = (
        f"【本次共 {total} 个分镜】\n"
        f"必须严格按下面给出的顺序输出 {total} 项（**外层数组的长度必须等于 {total}**），每一项对应一个分镜；\n"
        f"即使某个分镜没有任何内容，也必须为它保留一项、写成空项（角色/道具写 []，场景写空字符串 \"\"），\n"
        f"也就是说：外层数组任何时候都必须有 {total} 项，**绝对不要只输出一个空数组 []**；\n"
        "不得省略、不得合并、不得多写、不得调换顺序。\n\n"
    )
    body = "\n\n".join(f"【分镜 {label}】\n{text}" for label, text in zip(labels, texts, strict=True))
    return header + body


def pad_appearances(
    rows: list[Any],
    count: int,
    frame_numbers: Sequence[int] | None,
    *,
    parsed_ok: bool,
) -> tuple[list[Any], list[int]]:
    if count <= 0 or len(rows) >= count:
        return rows, []
    if count == 1 and not rows and parsed_ok:
        return [[]], []
    labels = list(frame_numbers) if frame_numbers and len(frame_numbers) == count else list(range(1, count + 1))
    missing = labels[len(rows):count]
    return rows + [None] * (count - len(rows)), missing


def match_asset_name(
    name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    name_field: str,
) -> Mapping[str, Any] | None:
    normalized = naming.normalize_name(name)
    if not normalized:
        return None
    for row in rows:
        if naming.normalize_name(row.get(name_field)) == normalized:
            return row

    key = naming.make_key(kind, name)
    if key:
        for row in rows:
            existing_key = row.get("canonical_key") or naming.make_key(kind, row.get(name_field))
            if existing_key and existing_key == key:
                return row
    for row in rows:
        if naming.alias_contains(row.get("aliases"), name):
            return row

    if kind == "character":
        best: Mapping[str, Any] | None = None
        best_ratio = 0.0
        for row in rows:
            existing_name = str(row.get(name_field) or "")
            if not existing_name:
                continue
            ratio = SequenceMatcher(None, name, existing_name).ratio()
            if name in existing_name or existing_name in name:
                ratio = max(ratio, 0.85)
            if ratio > best_ratio:
                best, best_ratio = row, ratio
        return best if best_ratio >= 0.7 else None

    best = None
    best_ratio = 0.0
    for row in rows:
        existing_name = naming.normalize_name(row.get(name_field))
        if not existing_name:
            continue
        ratio = SequenceMatcher(None, normalized, existing_name).ratio()
        if ratio > best_ratio:
            best, best_ratio = row, ratio
    return best if best_ratio >= 0.9 else None
