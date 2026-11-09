"""TOON 4.4 encoder (deterministic; no decoder). Spec: https://toonformat.dev"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# TOON 4.4 encoder (deterministic; no decoder). Spec: https://toonformat.dev
# Spec §9.1 empty arrays are `key: []` / `[]` (NOT legacy `key[0]:`).
# Spec §7.1 newlines in strings are quoted `\n` (TOON has no YAML block scalars).
# ---------------------------------------------------------------------------

_TOON_NUMERIC_LIKE = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$", re.I)
_TOON_UNQUOTED_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_TOON_INDENT = 2


def _toon_escape(s: str) -> str:
    out: List[str] = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20:
            out.append(f"\\u{o:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _toon_needs_quote(s: str) -> bool:
    if s == "" or s in ("true", "false", "null"):
        return True
    if s[0] in "-#" or s[0] in " \t" or s[-1] in " \t":
        return True
    if _TOON_NUMERIC_LIKE.match(s):
        return True
    for ch in s:
        if ch in ':"\\[]{},' or ord(ch) < 0x20:
            return True
    return False


def _toon_key(k: str) -> str:
    return k if _TOON_UNQUOTED_KEY.match(k) else '"' + _toon_escape(k) + '"'


def _toon_prim(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "null"
        if v == int(v) and abs(v) < 1e21:
            return str(int(v))
        return format(v, ".15g")
    s = str(v)
    if _toon_needs_quote(s):
        return '"' + _toon_escape(s) + '"'
    return s


def _toon_is_prim(v: Any) -> bool:
    return not isinstance(v, (dict, list))


def _toon_column_uniform(vals: List[Any]) -> bool:
    """§9.3: uniform-primitive or nested-uniform column."""
    if all(_toon_is_prim(v) for v in vals):
        return True
    if all(isinstance(v, dict) and v for v in vals):
        keys0 = list(vals[0].keys())
        if not keys0:
            return False
        if not all(set(v.keys()) == set(keys0) for v in vals):
            return False
        for k in keys0:
            if not _toon_column_uniform([v[k] for v in vals]):
                return False
        return True
    return False


def _toon_uniform_objects(arr: List[Any]) -> bool:
    """§9.3 tabular detection for an array of objects (supports nested-uniform)."""
    if not arr or not all(isinstance(x, dict) and x for x in arr):
        return False
    keys0 = list(arr[0].keys())
    if not all(set(o.keys()) == set(keys0) for o in arr):
        return False
    for k in keys0:
        if not _toon_column_uniform([o[k] for o in arr]):
            return False
    return True


def _toon_keyed_tabular_ok(obj: Dict[str, Any]) -> bool:
    """§9.5: ≥2 entries, every value a non-empty object, columns uniform."""
    if len(obj) < 2:
        return False
    vals = list(obj.values())
    if not all(isinstance(v, dict) and v for v in vals):
        return False
    return _toon_uniform_objects(vals)


def _toon_field_entry(key: str, sample: Any) -> str:
    """One field list entry, with nested field group when sample is nested-uniform object."""
    ks = _toon_key(str(key))
    if isinstance(sample, dict) and sample:
        inner = ",".join(_toon_field_entry(str(sk), sample[sk]) for sk in sample.keys())
        return f"{ks}{{{inner}}}"
    return ks


def _toon_fields_seg(sample_obj: Dict[str, Any]) -> str:
    return "{" + ",".join(_toon_field_entry(str(k), sample_obj[k]) for k in sample_obj.keys()) + "}"


def _toon_leaf_cells(obj: Dict[str, Any], sample: Dict[str, Any]) -> List[str]:
    """Depth-first leaf primitives matching header field order from sample."""
    cells: List[str] = []
    for k in sample.keys():
        sv = sample[k]
        v = obj[k]
        if isinstance(sv, dict) and sv:
            cells.extend(_toon_leaf_cells(v, sv))
        else:
            cells.append(_toon_prim(v))
    return cells


def _toon_encode_keyed(key: Optional[str], obj: Dict[str, Any], indent: int) -> List[str]:
    """Keyed tabular form §9.5. key=None for root."""
    pad = " " * (_TOON_INDENT * indent)
    vals = list(obj.values())
    sample = vals[0]
    n = len(obj)
    head = (f"{pad}{_toon_key(key)}[{n}:]" if key else f"{pad}[{n}:]") + _toon_fields_seg(sample) + ":"
    lines = [head]
    ipad = " " * (_TOON_INDENT * (indent + 1))
    for ek, ev in obj.items():
        cells = ",".join(_toon_leaf_cells(ev, sample))
        lines.append(f"{ipad}{_toon_key(str(ek))}: {cells}")
    return lines


def _toon_encode_list(key: str, arr: List[Any], indent: int) -> List[str]:
    """Encode an array. key="" for root/keyless; indent = header line depth."""
    pad = " " * (_TOON_INDENT * indent)
    n = len(arr)
    # §9.1 empty arrays: key: [] / root []
    if n == 0:
        if key:
            return [f"{pad}{_toon_key(key)}: []"]
        return [f"{pad}[]"] if indent else ["[]"]
    head = f"{pad}{_toon_key(key)}[{n}]" if key else f"{pad}[{n}]"
    # inline primitive array
    if all(_toon_is_prim(x) for x in arr):
        return [head + ": " + ",".join(_toon_prim(x) for x in arr)]
    # tabular (§9.3) including nested-uniform columns
    if _toon_uniform_objects(arr):
        sample = arr[0]
        lines = [head + _toon_fields_seg(sample) + ":"]
        ipad = " " * (_TOON_INDENT * (indent + 1))
        for obj in arr:
            lines.append(ipad + ",".join(_toon_leaf_cells(obj, sample)))
        return lines
    # list form (§9.2 / §9.4)
    lines = [head + ":"]
    item_depth = indent + 1
    ipad = " " * (_TOON_INDENT * item_depth)
    for item in arr:
        if isinstance(item, dict):
            lines.extend(_toon_encode_list_item_object(item, item_depth))
        elif isinstance(item, list):
            # keyless header as list item (§9.2 / §9.4)
            sub = _toon_encode_list("", item, item_depth)
            # merge onto "- " prefix: header stands at item_depth
            lines.append(ipad + "- " + sub[0][len(ipad):])
            lines.extend(sub[1:])
        else:
            lines.append(ipad + "- " + _toon_prim(item))
    return lines


def _toon_encode_list_item_object(obj: Dict[str, Any], item_depth: int) -> List[str]:
    """§10: first field on hyphen line; siblings at item_depth+1; nested content at +2 from hyphen."""
    ipad = " " * (_TOON_INDENT * item_depth)
    if not obj:
        return [ipad + "-"]
    lines: List[str] = []
    first = True
    for k, v in obj.items():
        ks = _toon_key(str(k))
        if first:
            first = False
            # First field stands at depth item_depth+1 for scope; content at item_depth+2.
            if isinstance(v, dict):
                if _toon_keyed_tabular_ok(v):
                    sub = _toon_encode_keyed(str(k), v, item_depth + 1)
                    hdr_pad = " " * (_TOON_INDENT * (item_depth + 1))
                    lines.append(ipad + "- " + sub[0][len(hdr_pad):])
                    lines.extend(sub[1:])
                elif not v:
                    lines.append(ipad + f"- {ks}:")
                else:
                    lines.append(ipad + f"- {ks}:")
                    lines.extend(_toon_encode(v, item_depth + 2))
            elif isinstance(v, list):
                # Array header on hyphen; logical header depth = item_depth+1; rows at item_depth+2
                sub = _toon_encode_list(str(k), v, item_depth + 1)
                hdr_pad = " " * (_TOON_INDENT * (item_depth + 1))
                lines.append(ipad + "- " + sub[0][len(hdr_pad):])
                lines.extend(sub[1:])
            else:
                lines.append(ipad + f"- {ks}: {_toon_prim(v)}")
        else:
            # Sibling fields at depth item_depth+1
            field_depth = item_depth + 1
            fpad = " " * (_TOON_INDENT * field_depth)
            if isinstance(v, dict):
                if _toon_keyed_tabular_ok(v):
                    lines.extend(_toon_encode_keyed(str(k), v, field_depth))
                elif not v:
                    lines.append(f"{fpad}{ks}:")
                else:
                    lines.append(f"{fpad}{ks}:")
                    lines.extend(_toon_encode(v, field_depth + 1))
            elif isinstance(v, list):
                lines.extend(_toon_encode_list(str(k), v, field_depth))
            else:
                lines.append(f"{fpad}{ks}: {_toon_prim(v)}")
    return lines


def _toon_encode(value: Any, indent: int = 0) -> List[str]:
    pad = " " * (_TOON_INDENT * indent)
    if isinstance(value, dict):
        if not value:
            return []  # empty root/nested object body (§8); caller emits `key:` for fields
        # Root object that itself is keyed-tabular (§9.5)
        if indent == 0 and _toon_keyed_tabular_ok(value):
            return _toon_encode_keyed(None, value, 0)
        lines: List[str] = []
        for k, v in value.items():
            key = _toon_key(str(k))
            if isinstance(v, dict):
                if _toon_keyed_tabular_ok(v):
                    lines.extend(_toon_encode_keyed(str(k), v, indent))
                elif not v:
                    lines.append(f"{pad}{key}:")
                else:
                    lines.append(f"{pad}{key}:")
                    lines.extend(_toon_encode(v, indent + 1))
            elif isinstance(v, list):
                lines.extend(_toon_encode_list(str(k), v, indent))
            else:
                lines.append(f"{pad}{key}: {_toon_prim(v)}")
        return lines
    if isinstance(value, list):
        return _toon_encode_list("", value, indent)
    return [pad + _toon_prim(value)]


def encode_toon(value: Any) -> str:
    """Encode JSON-compatible value as compact TOON 4.4 (no trailing newline)."""
    lines = _toon_encode(value, 0)
    return "\n".join(lines)
