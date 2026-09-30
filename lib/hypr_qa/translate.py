"""[[step.expect]] -> `vmkit check batch` input.

Each expectation becomes one object anchored on its step's action, which the
runner labels with VMKIT_STEP (vmkit actions) or writes itself (hyprhands,
wait_ms) as `step` in actions.jsonl:

  {"id": "<step>#<k>", "action": "<step>", "anchor": "send"|"ack", "at": ...,
   "mode": "any"|"all", "check": ..., ...check fields..., "not"?: true}

k is 1-based within the step. Template images are resolved to absolute paths
here: the schema makes them relative to the scenario, vmkit to EXPECT.json.
"""
from .scenario import CHECK_FIELDS


def expect_id(step_id, k):
    return f"{step_id}#{k}"


def split_id(eid):
    step, _, k = eid.rpartition("#")
    return step, int(k)


def default_mode(e):
    """The mode, always explicit (never left to vmkit's default). Schema v0.3: a
    window is `any`, but with `not` it means "in no frame", so `all`. A point
    selects one frame, where any and all agree."""
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
    """The check-batch list for every expectation of every performed step
    (performed: a set of step ids; None = all)."""
    batch = []
    for st in doc["step"]:
        if performed is not None and st["id"] not in performed:
            continue
        for k, e in enumerate(st.get("expect", []), 1):
            batch.append(translate_expect(st["id"], k, e, resolve_asset))
    return batch
