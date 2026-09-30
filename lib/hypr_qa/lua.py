"""TOML value -> Lua table literal, for `hyprctl eval 'hl.config({...})'`.

Hyprland 0.56 reads Lua only and rejects `hyprctl keyword`, so `[guest] hyprland`
is converted to a literal and passed to hl.config().
"""
import math
import re

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESERVED = frozenset(
    "and break do else elseif end false for function goto if in local nil not or repeat return then true "
    "until while".split())
_ESC = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


class LuaError(ValueError):
    pass


def lua_string(s):
    out = []
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7f:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def lua_key(k):
    if not isinstance(k, str):
        raise LuaError(f"table key {k!r} is not a string")
    return k if _IDENT.match(k) and k not in RESERVED else f"[{lua_string(k)}]"


def to_lua(v, path="hyprland"):
    """Convert a TOML value (dict/list/str/int/float/bool) to a Lua literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise LuaError(f"{path}: {v} has no Lua literal")
        return repr(v)
    if isinstance(v, str):
        return lua_string(v)
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{lua_key(k)} = {to_lua(x, f'{path}.{k}')}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        if not v:
            return "{}"
        return "{ " + ", ".join(to_lua(x, f"{path}[{i}]") for i, x in enumerate(v, 1)) + " }"
    raise LuaError(f"{path}: a {type(v).__name__} ({v!r}) has no Lua literal")


def hl_config(table):
    """The expression for `hyprctl eval`: hl.config({...})."""
    if not isinstance(table, dict):
        raise LuaError("hl.config() takes a table")
    return f"hl.config({to_lua(table)})"
