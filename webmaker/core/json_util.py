"""
webmaker.core.json_util
=======================
Lenient JSON extraction for LLM responses.

Handles common Claude/GPT breakage: markdown fences, smart quotes, raw newlines
inside strings, unescaped double quotes inside German text, trailing commas,
and truncated closing braces.
"""

from __future__ import annotations

import json
import re
from typing import Any


def loads_lenient(text: str) -> dict[str, Any] | None:
    """Parse *text* into a JSON object, applying progressive repairs."""
    raw = extract_json_object(text)
    if not raw:
        return None

    candidates = [
        raw,
        _normalize_quotes(raw),
        _escape_control_chars_in_strings(_normalize_quotes(raw)),
        _escape_bare_quotes_in_strings(_normalize_quotes(raw)),
        _escape_bare_quotes_in_strings(
            _escape_control_chars_in_strings(_normalize_quotes(raw))
        ),
    ]

    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        data = _try_load(cand)
        if data is not None:
            return data
        closed = _close_truncated(cand)
        if closed and closed not in seen:
            seen.add(closed)
            data = _try_load(closed)
            if data is not None:
                return data

    return None


def salvage_review_payload(text: str) -> dict[str, Any] | None:
    """Best-effort recovery of ``summary`` + ``sections`` from broken review JSON."""
    raw = extract_json_object(text) or (text or "")
    if not raw.strip():
        return None

    summary = _extract_string_field(raw, "summary") or ""
    sections: list[dict[str, Any]] = []

    for start in _find_section_starts(raw):
        blob = _slice_balanced(raw, start)
        if not blob:
            continue
        obj = loads_lenient(blob)
        if isinstance(obj, dict) and (
            "page_slug" in obj or "recommendations" in obj or "section" in obj
        ):
            sections.append(obj)
            continue
        # Last resort: pull fields with regex from this blob
        partial = _regex_section(blob)
        if partial:
            sections.append(partial)

    if not summary and not sections:
        return None
    return {"summary": summary, "sections": sections}


def extract_json_object(text: str) -> str:
    """Strip fences / prose and return the outermost ``{…}`` span."""
    raw = (text or "").strip()
    if not raw:
        return ""
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1]
    return raw if raw.startswith("{") else ""


# ── internal helpers ──────────────────────────────────────────────────────────

def _try_load(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # trailing commas
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _normalize_quotes(raw: str) -> str:
    """Replace curly/smart quotes with ASCII equivalents."""
    return (
        raw.replace("\u201c", '\\"')
        .replace("\u201d", '\\"')
        .replace("\u201e", '\\"')
        .replace("\u201f", '\\"')
        .replace("\u00ab", '\\"')
        .replace("\u00bb", '\\"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def _escape_control_chars_in_strings(raw: str) -> str:
    out: list[str] = []
    in_str = False
    escape = False
    for ch in raw:
        if in_str:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_str = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            if ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
                continue
            out.append(ch)
            continue
        out.append(ch)
        if ch == '"':
            in_str = True
    return "".join(out)


def _escape_bare_quotes_in_strings(raw: str) -> str:
    """Escape double quotes that appear inside JSON strings (common LLM bug).

    A quote is treated as a string terminator only when the next non-space
    character is one of ``, } ] :`` or end-of-input.
    """
    out: list[str] = []
    i = 0
    n = len(raw)
    in_str = False
    while i < n:
        ch = raw[i]
        if not in_str:
            out.append(ch)
            if ch == '"':
                in_str = True
            i += 1
            continue

        # Inside a string
        if ch == "\\":
            out.append(ch)
            if i + 1 < n:
                out.append(raw[i + 1])
                i += 2
            else:
                i += 1
            continue

        if ch == '"':
            j = i + 1
            while j < n and raw[j] in " \t\r\n":
                j += 1
            if j >= n or raw[j] in ",}]:":
                out.append('"')
                in_str = False
            else:
                out.append('\\"')
            i += 1
            continue

        if ch == "\n":
            out.append("\\n")
            i += 1
            continue
        if ch == "\r":
            out.append("\\r")
            i += 1
            continue
        if ch == "\t":
            out.append("\\t")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _close_truncated(raw: str) -> str | None:
    text = raw.rstrip()
    if text.count('"') % 2 == 1:
        text = text.rsplit('"', 1)[0]
        text = re.sub(r",\s*$", "", text)
    text = re.sub(r",\s*$", "", text)

    stack: list[str] = []
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    if not stack:
        return text
    closing = "".join("}" if o == "{" else "]" for o in reversed(stack))
    return text + closing


def _find_section_starts(raw: str) -> list[int]:
    starts: list[int] = []
    for m in re.finditer(r'\{\s*"page_slug"\s*:', raw):
        starts.append(m.start())
    return starts


def _slice_balanced(raw: str, start: int) -> str:
    if start < 0 or start >= len(raw) or raw[start] != "{":
        return ""
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return ""


def _extract_string_field(raw: str, key: str) -> str:
    # Prefer properly escaped JSON string
    pat = rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"'
    m = re.search(pat, raw)
    if m:
        try:
            return json.loads(f'"{m.group(1)}"')
        except json.JSONDecodeError:
            return m.group(1)

    # Broken string: grab until next ,"key" or ,"sections" or end-ish
    m2 = re.search(
        rf'"{re.escape(key)}"\s*:\s*"(.*?)(?="\s*,\s*"(?:sections|page_slug|section|recommendations)")',
        raw,
        re.DOTALL,
    )
    if m2:
        return re.sub(r"\s+", " ", m2.group(1)).strip()
    return ""


def _regex_section(blob: str) -> dict[str, Any] | None:
    slug = _extract_string_field(blob, "page_slug")
    section = _extract_string_field(blob, "section")
    summary = _extract_string_field(blob, "summary")
    recs: list[dict[str, Any]] = []

    # Pull recommendation objects that are still valid JSON
    for m in re.finditer(r'\{\s*"current"\s*:', blob):
        piece = _slice_balanced(blob, m.start())
        if not piece:
            continue
        obj = loads_lenient(piece)
        if isinstance(obj, dict) and (
            obj.get("recommendation") or obj.get("issue") or obj.get("proposed_html")
        ):
            recs.append(obj)

    if not slug and not recs:
        return None
    return {
        "page_slug": slug or "homepage",
        "section": section or "general",
        "summary": summary,
        "recommendations": recs,
    }
