"""Run a scenario: boot, precheck, setup, record, act, check, report.

Everything VM-side goes through the profile's `vm` wrapper (profiles/<name>/vm),
which passes the profile's flags to vmkit. Recording, frame selection, checks
and the report are vmkit's (`vm record`, `vm check batch`, `vm report`); this
module only orders them, performs the steps and names things.

Run dir (runs/<name>/<UTC>/), in addition to vmkit's recording files:
  scenario.toml   the scenario as run (what `hypr-qa check` re-reads)
  assets/         template images the scenario references
  run.json        the runner's record: steps performed, runner errors
  timeline.jsonl  one line per step: {step, kind, detail, t_send, t_ack, ok, error?}
  expect.json     the check-batch input; checks.json its output (vmkit's list)
  results.json    timing (from summary.json) first, then status, steps, expectations
  runner.log      every vm command the runner ran, with its output
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import lua, proto
from . import scenario as S
from . import translate as X

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXIT_PASS, EXIT_FAIL, EXIT_ERROR = 0, 1, 2
VIDEO_NOTE = ("video.mkv is for watching only: it plays at the nominal rate, so stalls don't show in it. "
              "Timing comes from frames.jsonl (and the frame PNGs).")
FRAME_FILE = re.compile(r"^\d{6}\.png$")


def utc_stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def wall():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def say(msg):
    print(f"hypr-qa: {msg}", file=sys.stderr, flush=True)


def load_jsonl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def append_jsonl(path, rec):
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


class RunError(Exception):
    pass


class VM:
    """The profile's `vm` wrapper. Every call is logged to runner.log."""

    def __init__(self, profile, log_path=None, profiles_dir=None):
        self.dir = os.path.join(profiles_dir or os.path.join(REPO, "profiles"), profile)
        self.path = os.path.join(self.dir, "vm")
        if not os.access(self.path, os.X_OK):
            raise RunError(f"profile {profile!r}: no executable {self.path}")
        self.log_path = log_path

    def env(self, step=None):
        env = {k: v for k, v in os.environ.items() if k != "VMKIT_STEP"}
        if step:
            env["VMKIT_STEP"] = step
        return env

    def argv(self, *args):
        return [self.path, *args]

    def log(self, text):
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(text if text.endswith("\n") else text + "\n")

    def run(self, *args, step=None, timeout=120):
        """(rc, stdout, stderr); rc None on timeout."""
        t0 = time.monotonic()
        try:
            p = subprocess.run(self.argv(*args), capture_output=True, text=True, env=self.env(step),
                               timeout=timeout, stdin=subprocess.DEVNULL)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            rc = None
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = (e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")) \
                + f"\n(timed out after {timeout} s)"
        dt = time.monotonic() - t0
        self.log(f"$ vm {' '.join(args)}" + (f"   [VMKIT_STEP={step}]" if step else "")
                 + f"\n  rc={rc} {dt:.2f}s\n" + "".join(f"  | {line}\n" for line in (out + err).splitlines()))
        return rc, out, err


def _tail(out, err, n=300):
    txt = (err.strip() or out.strip()).splitlines()
    return (txt[-1] if txt else "")[:n]


# ---- assets -------------------------------------------------------------------

def make_resolver(scenario_dir, run_dir):
    """Template path resolver: absolute as-is; relative against the scenario's
    directory, else the copy saved in the run dir (assets/...)."""
    def resolve(rel):
        if os.path.isabs(rel):
            return rel
        p = os.path.join(scenario_dir, rel)
        if os.path.exists(p):
            return os.path.abspath(p)
        return os.path.abspath(os.path.join(run_dir, S.asset_name(rel)))
    return resolve


def save_scenario(path, doc, run_dir):
    shutil.copyfile(path, os.path.join(run_dir, "scenario.toml"))
    sdir = os.path.dirname(os.path.abspath(path))
    missing = []
    for rel in S.template_images(doc):
        src = rel if os.path.isabs(rel) else os.path.join(sdir, rel)
        dst = os.path.join(run_dir, S.asset_name(rel))
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        else:
            missing.append(rel)
    return missing


# ---- the run ------------------------------------------------------------------

class Runner:
    def __init__(self, scenario_path, runs_dir=None, profiles_dir=None, down=False, serve_timeout=30.0):
        self.path = os.path.abspath(scenario_path)
        self.doc = S.load(self.path)
        self.sdir = os.path.dirname(self.path)
        base = runs_dir or os.environ.get("HYPR_QA_RUNS") or os.path.join(REPO, "runs")
        self.run_dir = os.path.abspath(os.path.join(base, self.doc["name"], utc_stamp()))
        self.vm = VM(self.doc["profile"], os.path.join(self.run_dir, "runner.log"), profiles_dir)
        os.makedirs(self.run_dir)
        os.makedirs(os.path.join(self.run_dir, "frames"), exist_ok=True)
        self.down, self.serve_timeout = down, serve_timeout
        self.errors = []          # runner-level errors (boot, setup, actions, ...)
        self.steps = []           # timeline rows
        self.skipped = []
        self.serve = None
        self.recording = False
        self.actions_path = os.path.join(self.run_dir, "actions.jsonl")

    # -- bookkeeping
    def error(self, msg):
        self.errors.append(msg)
        say(f"ERROR: {msg}")

    def save_state(self):
        write_json(os.path.join(self.run_dir, "run.json"), {
            "scenario": self.doc["name"], "profile": self.doc["profile"],
            "snapshot": self.doc.get("snapshot"), "scenario_file": self.path, "run_dir": self.run_dir,
            "recorded": self.recording or os.path.exists(os.path.join(self.run_dir, "frames.jsonl")),
            "errors": self.errors, "steps": self.steps, "skipped": self.skipped,
        })

    # -- phases
    def boot(self):
        snap = self.doc.get("snapshot")
        say(f"restore {snap or '(profile default snapshot)'}")
        rc, out, err = self.vm.run("restore", *([snap] if snap else []), timeout=600)
        if rc != 0:
            raise RunError(f"boot/restore failed: {_tail(out, err)}")
        rc, out, err = self.vm.run("wait", "ssh", "60", timeout=90)
        if rc != 0:
            raise RunError(f"guest ssh not reachable after restore: {_tail(out, err)}")
        rc, out, err = self.vm.run("ssh", "qa-cursor-plane", timeout=60)
        say(f"precheck qa-cursor-plane: {out.strip() or err.strip()} (exit {rc})")
        if rc != 0:
            raise RunError(f"precheck qa-cursor-plane exited {rc}: {_tail(out, err)} "
                           "(the pointer would be missing from recorded frames)")

    def apply_guest(self):
        table = self.doc.get("guest", {}).get("hyprland")
        if not table:
            return
        expr = lua.hl_config(table)
        say(f"hyprctl eval '{expr}'")
        rc, out, err = self.vm.run("session", "hyprctl", "eval", expr)
        if rc != 0:
            raise RunError(f"[guest] hyprland: hyprctl eval failed (exit {rc}): {_tail(out, err)}")

    def setup(self):
        for i, s in enumerate(self.doc.get("setup", []), 1):
            if "push" in s:
                p = s["push"]
                src = p["src"] if os.path.isabs(p["src"]) else os.path.join(self.sdir, p["src"])
                say(f"setup[{i}] push {src} -> {p['dst']}")
                if not os.path.isfile(src):
                    raise RunError(f"setup[{i}] push: no such file {src}")
                rc, out, err = self.vm.run("push", src, p["dst"])
                if rc == 0 and "mode" in p:
                    rc, out, err = self.vm.run("ssh", "sudo", "chmod", str(p["mode"]), p["dst"])
            else:
                say(f"setup[{i}] session {s['session']}")
                rc, out, err = self.vm.run("session", "sh", "-c", s["session"], timeout=300)
            if rc != 0:
                raise RunError(f"setup[{i}] failed (exit {rc}): {_tail(out, err)}")

    def start_serve(self):
        hh = self.doc.get("hyprhands")
        if not hh:
            return
        argv = self.vm.argv("session", *hh["argv"])
        say(f"hyprhands: {' '.join(hh['argv'])}")
        self.serve = proto.Serve(argv, os.path.join(self.run_dir, "hyprhands-serve.log"),
                                 env=self.vm.env(), timeout=self.serve_timeout)

    def lead_in_s(self):
        """Record this long before step 1, so its most negative `at` has frames."""
        lo = min((S.at_bounds(e["at"])[0] for st in self.doc["step"] for e in st.get("expect", [])), default=0)
        return max(0.3, -lo / 1e9 + 0.1)

    def record_start(self):
        rc, out, err = self.vm.run("record", "start", "--out", self.run_dir, timeout=60)
        if rc != 0:
            raise RunError(f"record start failed: {_tail(out, err)}")
        self.recording = True
        say(f"recording -> {self.run_dir}")

    def record_stop(self):
        if not self.recording:
            return
        self.recording = False
        rc, out, err = self.vm.run("record", "stop", timeout=180)
        line = out.strip().splitlines()[-1] if out.strip() else ""
        say(f"record stop: {line}")
        if rc != 0:
            self.error(f"record stop failed: {_tail(out, err)}")

    # -- steps
    def _last_action(self, step_id):
        hits = [a for a in load_jsonl(self.actions_path) if a.get("step") == step_id]
        return hits[-1] if hits else None

    def _vm_action(self, st, args, detail, kind):
        t0 = time.monotonic_ns()
        rc, out, err = self.vm.run(*args, step=st["id"], timeout=300)
        t1 = time.monotonic_ns()
        a = self._last_action(st["id"])
        if a is None:
            # The vmkit command does not stamp itself (e.g. `shot`): stamp it here, so the
            # step still has an anchor, from the subprocess's start and exit.
            a = {"t": wall(), "cmd": kind, "args": detail, "t_send": t0, "t_ack": t1, "ok": rc == 0, "step": st["id"]}
            if rc != 0:
                a["error"] = f"exit {rc}: {_tail(out, err)}"
            append_jsonl(self.actions_path, a)
        row = {"t_send": a["t_send"], "t_ack": a["t_ack"], "ok": bool(a.get("ok", True)) and rc == 0}
        if not row["ok"]:
            row["error"] = a.get("error") or f"exit {rc}: {_tail(out, err)}"
        if out.strip():
            row["stdout"] = out.strip()[-2000:]
        return row

    def do_step(self, st):
        kind = S.action_kind(st["do"])
        v = st["do"][kind]
        if kind == "vmkit":
            detail = " ".join(v)
            row = self._vm_action(st, v, detail, kind)
        elif kind == "session":
            detail = v
            row = self._vm_action(st, ["session", "sh", "-c", v], detail, kind)
        elif kind == "hyprctl":
            detail = "hyprctl " + " ".join(v)
            row = self._vm_action(st, ["session", "hyprctl", *v], detail, kind)
        elif kind == "wait_ms":
            detail = f"{v}ms"
            t = time.monotonic_ns()
            append_jsonl(self.actions_path, {"t": wall(), "cmd": "wait", "args": detail, "t_send": t, "t_ack": t,
                                             "ok": True, "step": st["id"]})
            time.sleep(v / 1000)
            row = {"t_send": t, "t_ack": t, "ok": True}
        elif kind == "hyprhands":
            detail = json.dumps(v, separators=(",", ":"))
            r = self.serve.request(v)
            a = {"t": wall(), "cmd": "hyprhands", "args": detail, "t_send": r["t_send"], "t_ack": r["t_ack"],
                 "ok": r["ok"], "step": st["id"]}
            if not r["ok"]:
                a["error"] = r["error"]
            if isinstance(v.get("x"), int) and isinstance(v.get("y"), int):
                a.update(x=v["x"], y=v["y"])
            append_jsonl(self.actions_path, a)
            row = {"t_send": r["t_send"], "t_ack": r["t_ack"], "ok": r["ok"]}
            if not r["ok"]:
                row["error"] = r["error"]
            if r["reply"] is not None:
                row["reply"] = r["reply"]
            if r["blob"] is not None:
                bdir = os.path.join(self.run_dir, "hyprhands")
                os.makedirs(bdir, exist_ok=True)
                bpath = os.path.join(bdir, f"{st['id']}.blob")
                with open(bpath, "wb") as f:
                    f.write(r["blob"])
                row["blob"] = os.path.relpath(bpath, self.run_dir)
        row = {"step": st["id"], "kind": kind, "detail": detail, **row}
        append_jsonl(os.path.join(self.run_dir, "timeline.jsonl"), row)
        self.steps.append(row)
        ack_ms = (row["t_ack"] - row["t_send"]) / 1e6
        say(f"step {st['id']}: {kind} {detail}  {'ok' if row['ok'] else 'FAILED: ' + row.get('error', '')}"
            f"  (ack {ack_ms:.1f} ms)")
        if not row["ok"]:
            self.error(f"step {st['id']}: {kind} failed: {row.get('error')}")
        if st.get("settle_ms"):
            time.sleep(st["settle_ms"] / 1000)
        return row["ok"]

    def run_steps(self):
        steps = self.doc["step"]
        for i, st in enumerate(steps):
            ok = self.do_step(st)
            if not ok and self.doc.get("stop_on_fail"):
                self.skipped = [s["id"] for s in steps[i + 1:]]
                if self.skipped:
                    say(f"stop_on_fail: skipping {', '.join(self.skipped)}")
                break

    def cover_tail(self):
        """Keep recording until every expectation's window has frames after it."""
        need = 0
        by_id = {r["step"]: r for r in self.steps}
        for st in self.doc["step"]:
            r = by_id.get(st["id"])
            if not r:
                continue
            for e in st.get("expect", []):
                anchor = r["t_ack"] if e.get("anchor", "send") == "ack" else r["t_send"]
                need = max(need, anchor + S.at_bounds(e["at"])[1])
        wait = (need - time.monotonic_ns()) / 1e9 + 0.15
        if wait > 0:
            time.sleep(wait)

    def main(self):
        say(f"run {self.doc['name']} -> {self.run_dir}")
        missing = save_scenario(self.path, self.doc, self.run_dir)
        for m in missing:
            self.error(f"template image not found: {m}")
        try:
            if missing:
                raise RunError("missing template images")
            self.boot()
            self.apply_guest()
            self.setup()
            self.start_serve()
            self.record_start()
            time.sleep(self.lead_in_s())
            self.run_steps()
            self.cover_tail()
        except RunError as e:
            self.error(str(e))
        except KeyboardInterrupt:
            self.error("interrupted")
        finally:
            self.record_stop()
            if self.serve:
                rc = self.serve.close()
                if rc not in (0, None):
                    say(f"hyprhands serve exited {rc} (see hyprhands-serve.log)")
            self.save_state()
        code = evaluate(self.run_dir, self.doc, self.sdir, self.vm)
        if self.down:
            self.vm.run("down", timeout=120)
            say("vm down")
        return code


# ---- checks, frames, results, report (shared by run and check) ----------------

def name_frames(run_dir, checks):
    """Hardlink every frame a check used to frames/<step>-<k>-<ms>.png."""
    fdir = os.path.join(run_dir, "frames")
    for f in os.listdir(fdir) if os.path.isdir(fdir) else []:
        if f.endswith(".png") and not FRAME_FILE.match(f):
            os.unlink(os.path.join(fdir, f))
    named = 0
    for r in checks:
        if not r.get("id") or "#" not in r["id"]:
            continue
        step, k = X.split_id(r["id"])
        rows = [(fr["n"], fr.get("rel_ms")) for fr in r.get("frames", [])]
        b = r.get("baseline")
        if b and r.get("anchor_t") is not None:
            rows.append((b["n"], (b["t"] - r["anchor_t"]) / 1e6))
        for n, rel in rows:
            if rel is None:
                continue
            src = os.path.join(fdir, f"{n:06d}.png")
            dst = os.path.join(fdir, f"{step}-{k}-{round(rel)}.png")
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copyfile(src, dst)
                named += 1
    return named


def timing_block(run_dir):
    try:
        with open(os.path.join(run_dir, "summary.json")) as f:
            sm = json.load(f)
    except (OSError, ValueError):
        return {"degraded": None, "error": "no summary.json (the recording did not stop cleanly)"}
    keys = ("degraded", "fps", "target_fps", "frames", "interval_ms", "degraded_threshold_ms",
            "gaps_over_threshold", "inactive_frames", "screendump_call_ms", "error")
    out = {k: sm.get(k) for k in keys}
    out["note"] = VIDEO_NOTE
    return out


def evaluate(run_dir, doc, scenario_dir, vm):
    """Translate expectations, run `vm check batch`, name frames, write results.json
    and report.html. Returns the exit code."""
    state = {}
    try:
        with open(os.path.join(run_dir, "run.json")) as f:
            state = json.load(f)
    except (OSError, ValueError):
        pass
    errors = list(state.get("errors", []))
    timeline = state.get("steps", [])
    performed = {r["step"] for r in timeline}
    recorded = os.path.exists(os.path.join(run_dir, "frames.jsonl"))
    timing = timing_block(run_dir)

    checks = []
    if recorded:
        batch = X.translate(doc, performed, make_resolver(scenario_dir, run_dir))
        exp_path = os.path.join(run_dir, "expect.json")
        out_path = os.path.join(run_dir, "checks.json")
        write_json(exp_path, batch)
        if batch:
            say(f"checking {len(batch)} expectation(s)")
            rc, out, err = vm.run("check", "batch", exp_path, "--run", run_dir, "-o", out_path, timeout=3600)
            try:
                with open(out_path) as f:
                    checks = json.load(f)
            except (OSError, ValueError):
                errors.append(f"vmkit check batch wrote no results (exit {rc}): {_tail(out, err)}")
                checks = []
            if rc not in (0, 1, 2):
                errors.append(f"vmkit check batch exited {rc}: {_tail(out, err)}")
    # Expectations of steps that never ran: error rows (not sent to vmkit).
    for st in doc["step"]:
        if st["id"] in performed:
            continue
        why = "step skipped (stop_on_fail)" if st["id"] in state.get("skipped", []) else "step was not run"
        for k, e in enumerate(st.get("expect", []), 1):
            checks.append({"id": X.expect_id(st["id"], k), "check": e["check"], "at": e["at"],
                           "anchor": e.get("anchor", "send"), "not": bool(e.get("not")), "action": None,
                           "frames": [], "status": "error", "error": why})
    if recorded:
        write_json(os.path.join(run_dir, "checks.json"), checks)
        named = name_frames(run_dir, checks)
        rc, out, err = vm.run("report", run_dir, "--results", os.path.join(run_dir, "checks.json"), timeout=600)
        if rc != 0:
            errors.append(f"vmkit report failed (exit {rc}): {_tail(out, err)}")
        else:
            say(f"report: {out.strip()}")
    else:
        named = 0

    statuses = [c.get("status") for c in checks]
    if errors or "error" in statuses:
        code, status = EXIT_ERROR, "error"
    elif "fail" in statuses:
        code, status = EXIT_FAIL, "fail"
    else:
        code, status = EXIT_PASS, "pass"
    counts = {s: statuses.count(s) for s in ("pass", "fail", "error")}
    results = {
        "timing": timing,
        "status": status, "exit": code, "counts": counts,
        "scenario": doc["name"], "profile": doc["profile"], "run_dir": run_dir,
        "errors": errors,
        "steps": timeline,
        "expectations": checks,
        "named_frames": named,
    }
    write_json(os.path.join(run_dir, "results.json"), results)

    if recorded:
        deg = timing.get("degraded")
        print(f"timing: degraded={str(deg).lower()} fps={timing.get('fps')} "
              f"interval max={(timing.get('interval_ms') or {}).get('max')} ms (timing from frames.jsonl; "
              f"video.mkv is for watching only)")
    for c in checks:
        extra = f"  ({c['error']})" if c.get("error") else ""
        print(f"{c['status']:5}  {c['id']}  {c['check']} {c['at']}{' not' if c.get('not') else ''}{extra}")
    for e in errors:
        print(f"error  {e}")
    print(f"{status}  {counts['pass']} pass, {counts['fail']} fail, {counts['error']} error  -> {run_dir}")
    return code


def check_run(run_dir, scenario_path=None, profiles_dir=None):
    """`hypr-qa check RUN_DIR`: re-run only the checks against the recording."""
    run_dir = os.path.abspath(run_dir)
    path = os.path.abspath(scenario_path or os.path.join(run_dir, "scenario.toml"))
    doc = S.load(path)
    if not os.path.exists(os.path.join(run_dir, "frames.jsonl")):
        raise RunError(f"{run_dir}: no recording (frames.jsonl) to check")
    vm = VM(doc["profile"], os.path.join(run_dir, "runner.log"), profiles_dir)
    vm.log(f"--- hypr-qa check {wall()} scenario={path}")
    sdir = os.path.dirname(path)
    return evaluate(run_dir, doc, sdir, vm)
