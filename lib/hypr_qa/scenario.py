"""Load and validate a scenario TOML file (docs/scenario-schema.md).

Validation is strict: an unknown key anywhere is an error, so a typo such as
`setle_ms` fails loudly instead of being ignored. Every error names where it
is (`step[2] "open-foot" expect[1]: ...`).
"""
import os
import re
import tomllib

from . import lua

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# ---- `at` ---------------------------------------------------------------------
# A strict subset of vmkit's timeline grammar (lib/vmkit/timeline.py), so anything
# accepted here is accepted there: a signed offset with a unit, or a window a..b.
_OFFSET = re.compile(r"^([+-])(\d+(?:\.\d+)?)(ms|s)$")
_UNIT_NS = {"ms": 1_000_000, "s": 1_000_000_000}


class ScenarioError(Exception):
    pass


def parse_offset(s):
    """'+300ms' | '-100ms' | '+1.5s' -> signed ns."""
    m = _OFFSET.match(s.strip()) if isinstance(s, str) else None
    if not m:
        raise ScenarioError(f"bad offset {s!r}: want a sign, a number and ms or s, e.g. +300ms, -100ms, +1.5s")
    sign, num, unit = m.groups()
    v = round(float(num) * _UNIT_NS[unit])
    return -v if sign == "-" else v


def parse_at(spec):
    """'+300ms' -> ('point', ns); '+50ms..+300ms' -> ('window', a_ns, b_ns)."""
    if not isinstance(spec, str):
        raise ScenarioError(f"at must be a string like \"+300ms\" or \"+50ms..+300ms\", got {spec!r}")
    if ".." in spec:
        parts = spec.split("..")
        if len(parts) != 2:
            raise ScenarioError(f"bad window {spec!r}: want exactly one '..', e.g. +50ms..+300ms")
        try:
            a, b = parse_offset(parts[0]), parse_offset(parts[1])
        except ScenarioError as e:
            raise ScenarioError(f"bad window {spec!r}: {e}") from None
        if b < a:
            raise ScenarioError(f"window {spec!r} ends before it starts")
        return ("window", a, b)
    return ("point", parse_offset(spec))


def at_bounds(spec):
    """(earliest, latest) offset in ns that `at` can select."""
    p = parse_at(spec)
    return (p[1], p[1]) if p[0] == "point" else (p[1], p[2])


# ---- helpers ------------------------------------------------------------------

def _where(*parts):
    return " ".join(p for p in parts if p)


def _check_keys(obj, allowed, where, required=()):
    if not isinstance(obj, dict):
        raise ScenarioError(f"{where}: expected a table, got {type(obj).__name__}")
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise ScenarioError(f"{where}: unknown key{'s' if len(unknown) > 1 else ''} "
                            f"{', '.join(repr(k) for k in unknown)} (allowed: {', '.join(sorted(allowed))})")
    for k in required:
        if k not in obj:
            raise ScenarioError(f"{where}: missing required key {k!r}")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (isinstance(v, (int, float))) and not isinstance(v, bool)


def _str(v, where, key):
    if not isinstance(v, str) or not v:
        raise ScenarioError(f"{where}: {key} must be a non-empty string, got {v!r}")
    return v


def _argv(v, where, key):
    if not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v):
        raise ScenarioError(f"{where}: {key} must be a non-empty list of strings, got {v!r}")
    return v


def _nonneg_int(v, where, key):
    if not _is_int(v) or v < 0:
        raise ScenarioError(f"{where}: {key} must be an integer >= 0, got {v!r}")
    return v


def _region(v, where):
    if not isinstance(v, list) or len(v) != 4 or not all(_is_int(x) for x in v):
        raise ScenarioError(f"{where}: region must be 4 integers [x, y, w, h], got {v!r}")
    if v[2] < 0 or v[3] < 0:
        raise ScenarioError(f"{where}: region w and h must be >= 0 (0 = to the edge), got {v!r}")


# ---- expectations -------------------------------------------------------------

COMMON_EXPECT = {"at", "anchor", "check", "mode", "not"}
CHECK_FIELDS = {
    "pixel": {"region", "color", "tolerance", "min_fraction"},
    "ocr": {"region", "text", "regex"},
    "template": {"image", "region", "near", "radius", "threshold"},
    "changed": {"region", "min_px", "fuzz"},
}
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_expect(e, where):
    if not isinstance(e, dict):
        raise ScenarioError(f"{where}: expected a table")
    kind = e.get("check")
    if kind not in CHECK_FIELDS:
        raise ScenarioError(f"{where}: check must be one of {', '.join(CHECK_FIELDS)}, got {kind!r}")
    _check_keys(e, COMMON_EXPECT | CHECK_FIELDS[kind], f"{where} ({kind})", required=("at",))
    try:
        parse_at(e["at"])
    except ScenarioError as err:
        raise ScenarioError(f"{where}: at: {err}") from None
    if e.get("anchor", "send") not in ("send", "ack"):
        raise ScenarioError(f"{where}: anchor must be \"send\" or \"ack\", got {e['anchor']!r}")
    if e.get("mode", "any") not in ("any", "all"):
        raise ScenarioError(f"{where}: mode must be \"any\" or \"all\", got {e['mode']!r}")
    if "not" in e and not isinstance(e["not"], bool):
        raise ScenarioError(f"{where}: not must be true or false, got {e['not']!r}")
    if "region" in e:
        _region(e["region"], where)
    if kind == "pixel":
        for k in ("region", "color"):
            if k not in e:
                raise ScenarioError(f"{where}: pixel needs {k}")
        if not isinstance(e["color"], str) or not COLOR_RE.match(e["color"]):
            raise ScenarioError(f"{where}: color must be \"#rrggbb\", got {e['color']!r}")
        if "tolerance" in e and (not _is_int(e["tolerance"]) or not 0 <= e["tolerance"] <= 255):
            raise ScenarioError(f"{where}: tolerance must be an integer 0-255, got {e['tolerance']!r}")
        if "min_fraction" in e and (not _is_num(e["min_fraction"]) or not 0 <= e["min_fraction"] <= 1):
            raise ScenarioError(f"{where}: min_fraction must be a number 0-1, got {e['min_fraction']!r}")
    elif kind == "ocr":
        if ("text" in e) == ("regex" in e):
            raise ScenarioError(f"{where}: ocr needs exactly one of text or regex")
        key = "text" if "text" in e else "regex"
        _str(e[key], where, key)
        if key == "regex":
            try:
                re.compile(e["regex"])
            except re.error as err:
                raise ScenarioError(f"{where}: regex does not compile: {err}") from None
    elif kind == "template":
        _str(e.get("image"), where, "image")
        has_near = "near" in e or "radius" in e
        if has_near == ("region" in e):
            raise ScenarioError(f"{where}: template needs region, or near = [x, y] with radius (not both)")
        if has_near:
            n = e.get("near")
            if not isinstance(n, list) or len(n) != 2 or not all(_is_int(x) for x in n):
                raise ScenarioError(f"{where}: near must be [x, y] integers, got {n!r}")
            if not _is_int(e.get("radius")) or e["radius"] <= 0:
                raise ScenarioError(f"{where}: radius must be an integer > 0, got {e.get('radius')!r}")
        if "threshold" in e and (not _is_num(e["threshold"]) or not 0 <= e["threshold"] <= 1):
            raise ScenarioError(f"{where}: threshold must be a number 0-1, got {e['threshold']!r}")
    elif kind == "changed":
        if "region" not in e:
            raise ScenarioError(f"{where}: changed needs region")
        if "min_px" in e:
            _nonneg_int(e["min_px"], where, "min_px")
        if "fuzz" in e and (not _is_num(e["fuzz"]) or not 0 <= e["fuzz"] <= 100):
            raise ScenarioError(f"{where}: fuzz must be a percentage 0-100, got {e['fuzz']!r}")


# ---- steps, setup, top level --------------------------------------------------

ACTIONS = ("hyprhands", "vmkit", "session", "hyprctl", "wait_ms")
SETUP = ("push", "session")
TOP = {"name", "profile", "snapshot", "stop_on_fail", "guest", "setup", "hyprhands", "step"}


def action_kind(do):
    return next(iter(do))


def validate_do(do, where):
    if not isinstance(do, dict) or len(do) != 1:
        raise ScenarioError(f"{where}: do must have exactly one of {', '.join(ACTIONS)}, got {do!r}")
    kind, v = next(iter(do.items()))
    if kind not in ACTIONS:
        raise ScenarioError(f"{where}: unknown action {kind!r} (want one of {', '.join(ACTIONS)})")
    w = f"{where} do.{kind}"
    if kind == "hyprhands":
        if not isinstance(v, dict) or not isinstance(v.get("op"), str) or not v["op"]:
            raise ScenarioError(f"{w}: must be a request table with a string op, e.g. {{ op = \"click\", x = 1, y = 2 }}")
    elif kind in ("vmkit", "hyprctl"):
        _argv(v, where, f"do.{kind}")
    elif kind == "session":
        _str(v, where, "do.session")
    elif kind == "wait_ms":
        _nonneg_int(v, where, "do.wait_ms")
    return kind


def validate(doc):
    """Validate a parsed scenario dict in place; returns it."""
    _check_keys(doc, TOP, "scenario", required=("name", "profile", "step"))
    for k in ("name", "profile"):
        if not isinstance(doc[k], str) or not NAME_RE.match(doc[k]):
            raise ScenarioError(f"scenario: {k} must match {NAME_RE.pattern} (it names a directory), got {doc[k]!r}")
    if "snapshot" in doc:
        _str(doc["snapshot"], "scenario", "snapshot")
    if "stop_on_fail" in doc and not isinstance(doc["stop_on_fail"], bool):
        raise ScenarioError(f"scenario: stop_on_fail must be true or false, got {doc['stop_on_fail']!r}")
    if "guest" in doc:
        _check_keys(doc["guest"], {"hyprland"}, "[guest]")
        if "hyprland" in doc["guest"] and not isinstance(doc["guest"]["hyprland"], dict):
            raise ScenarioError("[guest]: hyprland must be a table (it is passed to hl.config())")
        if "hyprland" in doc["guest"]:
            try:
                lua.hl_config(doc["guest"]["hyprland"])
            except lua.LuaError as e:
                raise ScenarioError(f"[guest] hyprland: {e}") from None
    for i, s in enumerate(doc.get("setup", []), 1):
        where = f"setup[{i}]"
        if not isinstance(s, dict) or len(s) != 1 or next(iter(s)) not in SETUP:
            raise ScenarioError(f"{where}: must have exactly one of {', '.join(SETUP)}, got {s!r}")
        if "push" in s:
            _check_keys(s["push"], {"src", "dst", "mode"}, f"{where} push", required=("src", "dst"))
            _str(s["push"]["src"], where, "push.src")
            _str(s["push"]["dst"], where, "push.dst")
            if "mode" in s["push"] and not re.match(r"^[0-7]{3,4}$", str(s["push"]["mode"])):
                raise ScenarioError(f"{where}: push.mode must be an octal string like \"0755\", got {s['push']['mode']!r}")
        else:
            _str(s["session"], where, "session")
    if "hyprhands" in doc:
        _check_keys(doc["hyprhands"], {"argv"}, "[hyprhands]", required=("argv",))
        _argv(doc["hyprhands"]["argv"], "[hyprhands]", "argv")
    steps = doc["step"]
    if not isinstance(steps, list) or not steps:
        raise ScenarioError("scenario: needs at least one [[step]]")
    seen = set()
    for i, st in enumerate(steps, 1):
        sid = st.get("id") if isinstance(st, dict) else None
        where = f"step[{i}]" + (f" {sid!r}" if isinstance(sid, str) else "")
        _check_keys(st, {"id", "do", "settle_ms", "expect"}, where, required=("id", "do"))
        if not isinstance(sid, str) or not NAME_RE.match(sid):
            raise ScenarioError(f"{where}: id must match {NAME_RE.pattern} (it names frames), got {sid!r}")
        if sid in seen:
            raise ScenarioError(f"{where}: duplicate step id {sid!r}")
        seen.add(sid)
        kind = validate_do(st["do"], where)
        if kind == "hyprhands" and "hyprhands" not in doc:
            raise ScenarioError(f"{where}: a hyprhands action needs a [hyprhands] table (argv of the serve)")
        if "settle_ms" in st:
            _nonneg_int(st["settle_ms"], where, "settle_ms")
        exps = st.get("expect", [])
        if not isinstance(exps, list):
            raise ScenarioError(f"{where}: expect must be an array of tables ([[step.expect]])")
        for k, e in enumerate(exps, 1):
            validate_expect(e, f"{where} expect[{k}]")
    return doc


def load(path):
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ScenarioError(f"{path}: TOML: {e}") from None
    except OSError as e:
        raise ScenarioError(f"{path}: {e.strerror}") from None
    try:
        return validate(doc)
    except ScenarioError as e:
        raise ScenarioError(f"{path}: {e}") from None


def template_images(doc):
    """Every template image path the scenario references (as written)."""
    return [e["image"] for st in doc["step"] for e in st.get("expect", []) if e["check"] == "template"]


def asset_name(rel):
    """Where a scenario-relative asset is stored inside a run dir (assets/...)."""
    parts = [p if p not in ("..", ".") else "_up" if p == ".." else "" for p in os.path.normpath(rel).split(os.sep)]
    return os.path.join("assets", *[p for p in parts if p])
