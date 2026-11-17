from .scenario import CHECK_FIELDS


def expect_id(step_id, k):
    return f"{step_id}#{k}"


def split_id(eid):
    step, _, k = eid.rpartition("#")
    return step, int(k)


def default_mode(e):
    if "mode" in e:
        return e["mode"]
    return "all" if e.get("not") else "any"


def translate_expect(step_id, k, e, resolve_asset=None):
    out = {"id": expect_id(step_id, k), "action": step_id, "anchor": e.get("anchor", "send"),
           "at": e["at"], "check": e["check"]}
    out["mode"] = default_mode(e)
    if e.get("not"):
        out["not"] = True
    for f in sorted(CHECK_FIELDS[e["check"]]):
        if f in e:
            out[f] = e[f]
    if e["check"] == "template" and resolve_asset:
        out["image"] = resolve_asset(e["image"])
    return out


def translate(doc, performed=None, resolve_asset=None):
    batch = []
    for st in doc["step"]:
        if performed is not None and st["id"] not in performed:
            continue
        for k, e in enumerate(st.get("expect", []), 1):
            batch.append(translate_expect(st["id"], k, e, resolve_asset))
    return batch
