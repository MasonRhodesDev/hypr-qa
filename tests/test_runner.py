"""The runner end to end against a fake profile `vm` (tests/fake_profiles/fake/vm)
and the fake hyprhands serve: no QEMU. Checks and the report are the real vmkit's,
found at $VMKIT or ~/repos/vmkit/bin/vmkit; skipped without it."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from hypr_qa import cli, runner

HERE = os.path.dirname(os.path.abspath(__file__))
VMKIT = os.environ.get("VMKIT") or os.path.expanduser("~/repos/vmkit/bin/vmkit")
FAKE_SERVE = os.path.join(HERE, "fake_hyprhands_serve.py")

SCENARIO = f"""
name = "fake-e2e"
profile = "fake"

[guest]
hyprland = {{ cursor = {{ no_hardware_cursors = true }} }}

[[setup]]
session = "true"

[hyprhands]
argv = ["{sys.executable}", "{FAKE_SERVE}"]

[[step]]
id = "baseline"
do = {{ wait_ms = 150 }}

  [[step.expect]]
  at = "+0ms..+100ms"
  check = "pixel"
  region = [0, 0, 0, 0]
  color = "#ffffff"
  not = true

[[step]]
id = "hh-click"
do = {{ hyprhands = {{ op = "click", x = 3, y = 4 }} }}

  [[step.expect]]
  at = "+0ms"
  anchor = "ack"
  check = "pixel"
  region = [0, 0, 0, 0]
  color = "#000000"

[[step]]
id = "press"
do = {{ vmkit = ["keys", "meta_l", "q"] }}
settle_ms = 200

  [[step.expect]]
  at = "+100ms..+200ms"
  check = "changed"
  region = [0, 0, 0, 0]
  min_px = 10

  [[step.expect]]
  at = "+100ms..+200ms"
  check = "pixel"
  region = [0, 0, 4, 4]
  color = "#ffffff"

[[step]]
id = "hh-capture"
do = {{ hyprhands = {{ op = "capture", size = 64 }} }}
"""


class CliUnexpected(unittest.TestCase):
    def test_unexpected_exception_exits_2(self):
        with mock.patch.object(runner, "check_run", side_effect=KeyError("x")), \
                mock.patch("sys.stderr"):
            self.assertEqual(cli.main(["check", "/nonexistent"]), 2)


class Kills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hqa-test-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_vm_timeout_kills_the_whole_process_group(self):
        pidf = os.path.join(self.tmp, "pid")
        os.makedirs(os.path.join(self.tmp, "p", "hang"))
        vm = os.path.join(self.tmp, "p", "hang", "vm")
        with open(vm, "w") as f:
            f.write(f"#!/bin/bash\nbash -c '{sys.executable} -c \"import os,time; open(\\\"{pidf}\\\", \\\"w\\\")"
                    f".write(str(os.getpid())); time.sleep(5)\"; true'; true\n")
        os.chmod(vm, 0o755)
        t0 = time.monotonic()
        rc, out, err = runner.VM("hang", profiles_dir=os.path.join(self.tmp, "p")).run("x", timeout=1)
        self.assertIsNone(rc)
        self.assertLess(time.monotonic() - t0, 4, "run() waited for the orphaned grandchild")
        pid = int(open(pidf).read())
        time.sleep(0.2)
        with open(f"/proc/{pid}/stat") if os.path.exists(f"/proc/{pid}") else open(os.devnull) as f:
            st = f.read()
        self.assertTrue(not st or st.rsplit(")", 1)[1].split()[0] == "Z", "grandchild survived the timeout")

    def test_forced_serve_close_cleans_up_the_guest_side_by_exact_name(self):
        state = os.path.join(self.tmp, "state")
        os.makedirs(state)
        scn = os.path.join(self.tmp, "s.toml")
        with open(scn, "w") as f:
            f.write('name="k"\nprofile="fake"\n[hyprhands]\nargv=["/usr/local/bin/fake-hyprhands", "serve"]\n'
                    '[[step]]\nid="a"\ndo={wait_ms=1}\n')
        with mock.patch.dict(os.environ, {"FAKE_VM_STATE": state, "VMKIT": VMKIT}):
            r = runner.Runner(scn, runs_dir=os.path.join(self.tmp, "runs"),
                              profiles_dir=os.path.join(HERE, "fake_profiles"))
            r.serve = mock.Mock(killed=True, **{"close.return_value": -9})
            r.stop_serve()
        self.assertIn("session pkill -x fake-hyprhands", open(os.path.join(state, "calls.log")).read())


class LeadIn(unittest.TestCase):
    def test_covers_the_most_negative_offset(self):
        tmp = tempfile.mkdtemp(prefix="hqa-test-")
        self.addCleanup(shutil.rmtree, tmp)
        scn = os.path.join(tmp, "s.toml")
        with open(scn, "w") as f:
            f.write('name="l"\nprofile="fake"\n[[step]]\nid="a"\ndo={wait_ms=1}\n'
                    '[[step.expect]]\nat="-7s..+0ms"\ncheck="changed"\nregion=[0,0,0,0]\n')
        r = runner.Runner(scn, runs_dir=os.path.join(tmp, "runs"), profiles_dir=os.path.join(HERE, "fake_profiles"))
        self.assertGreaterEqual(r.lead_in_s(), 7.1)


@unittest.skipUnless(os.access(VMKIT, os.X_OK), f"needs vmkit at {VMKIT}")
class RunnerE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hqa-test-")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        self.env = {"FAKE_VM_STATE": self.state, "VMKIT": VMKIT, "HYPR_QA_RUNS": os.path.join(self.tmp, "runs")}
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.scn = os.path.join(self.tmp, "s.toml")
        with open(self.scn, "w") as f:
            f.write(SCENARIO)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ.pop("FAKE_VM_FAIL", None)
        shutil.rmtree(self.tmp)

    def run_it(self, text=None):
        if text:
            with open(self.scn, "w") as f:
                f.write(text)
        r = runner.Runner(self.scn, profiles_dir=os.path.join(HERE, "fake_profiles"))
        return r.main(), r.run_dir

    def test_pass_end_to_end(self):
        code, run = self.run_it()
        res = json.load(open(os.path.join(run, "results.json")))
        self.assertEqual(list(res)[0], "timing")
        self.assertIs(res["timing"]["degraded"], False)
        self.assertEqual(res["status"], "pass", res)
        self.assertEqual(code, 0)
        self.assertEqual(res["counts"], {"pass": 4, "fail": 0, "error": 0})
        # hyprhands requests land in actions.jsonl in vmkit's shape, with the step label
        acts = runner.load_jsonl(os.path.join(run, "actions.jsonl"))
        hh = [a for a in acts if a["cmd"] == "hyprhands"]
        self.assertEqual([a["step"] for a in hh], ["hh-click", "hh-capture"])
        for a in hh:
            self.assertTrue(set(a) >= {"t", "cmd", "args", "t_send", "t_ack", "ok", "step"})
            self.assertLessEqual(a["t_send"], a["t_ack"])
        self.assertEqual((hh[0]["x"], hh[0]["y"]), (3, 4))
        self.assertEqual(os.path.getsize(os.path.join(run, "hyprhands", "hh-capture.blob")), 64)
        # timeline, named frames, report, the hl.config call
        tl = runner.load_jsonl(os.path.join(run, "timeline.jsonl"))
        self.assertEqual([r["step"] for r in tl], ["baseline", "hh-click", "press", "hh-capture"])
        named = [f for f in os.listdir(os.path.join(run, "frames")) if not runner.FRAME_FILE.match(f)]
        self.assertTrue(any(f.startswith("press-1-") for f in named), named)
        self.assertTrue(any(f.startswith("hh-click-1-") for f in named), named)
        self.assertTrue(any(f.startswith("press-1--") for f in named), "changed baseline frame is named")
        self.assertTrue(os.path.exists(os.path.join(run, "report.html")))
        calls = open(os.path.join(self.state, "calls.log")).read()
        self.assertIn("session hyprctl eval hl.config({ cursor = { no_hardware_cursors = true } })", calls)
        self.assertIn("keys meta_l q  [step=press]", calls)
        # the cursor-plane precheck runs after [guest] and [[setup]], right before recording
        order = [ln.split(" --out")[0] for ln in calls.splitlines()]
        self.assertLess(order.index("session sh -c true"), order.index("ssh qa-cursor-plane"))
        self.assertLess(order.index("ssh qa-cursor-plane"), order.index("record start"))

        # check RUN_DIR: same verdict from the saved copy; a tuned scenario flips it
        self.assertEqual(runner.check_run(run, profiles_dir=os.path.join(HERE, "fake_profiles")), 0)
        tuned = os.path.join(self.tmp, "tuned.toml")
        with open(tuned, "w") as f:
            f.write(SCENARIO.replace('color = "#ffffff"\n\n[[step]]\nid = "hh-capture"',
                                     'color = "#ff0000"\n\n[[step]]\nid = "hh-capture"'))
        self.assertEqual(runner.check_run(run, tuned, profiles_dir=os.path.join(HERE, "fake_profiles")), 1)
        res = json.load(open(os.path.join(run, "results.json")))
        self.assertEqual(res["counts"]["fail"], 1)

    def test_check_never_reports_stale_or_missing_results(self):
        code, run = self.run_it()
        self.assertEqual(code, 0)
        fake = os.path.join(HERE, "fake_profiles")
        os.environ["FAKE_VM_FAIL"] = "batch"          # vmkit check batch fails without writing output
        self.assertEqual(runner.check_run(run, profiles_dir=fake), 2)
        res = json.load(open(os.path.join(run, "results.json")))
        self.assertTrue(any("check batch wrote no results" in e for e in res["errors"]), res["errors"])
        del os.environ["FAKE_VM_FAIL"]
        os.environ["FAKE_VM_CHECK_DROP"] = "press#2"  # vmkit answers, but without one expectation
        try:
            self.assertEqual(runner.check_run(run, profiles_dir=fake), 2)
        finally:
            del os.environ["FAKE_VM_CHECK_DROP"]
        res = json.load(open(os.path.join(run, "results.json")))
        st = {e["id"]: e for e in res["expectations"]}
        self.assertEqual(st["press#2"]["status"], "error")
        self.assertIn("no result", st["press#2"]["error"])
        self.assertEqual(st["press#1"]["status"], "pass")

    def test_precheck_failure_is_an_error_without_recording(self):
        os.environ["FAKE_VM_FAIL"] = "qa-cursor-plane"
        code, run = self.run_it()
        self.assertEqual(code, 2)
        res = json.load(open(os.path.join(run, "results.json")))
        self.assertTrue(any("qa-cursor-plane" in e for e in res["errors"]), res["errors"])
        self.assertFalse(os.path.exists(os.path.join(run, "frames.jsonl")))

    def test_non_utf8_output_is_kept_not_fatal(self):
        code, run = self.run_it('name="u"\nprofile="fake"\n[[step]]\nid="bin"\n'
                                'do={session="printf \'x\\\\377\\\\376\'"}\n')
        self.assertEqual(code, 0)
        res = json.load(open(os.path.join(run, "results.json")))
        self.assertEqual(res["steps"][0]["stdout"], "x\ufffd\ufffd")

    def test_unexpected_exception_is_an_error_with_results(self):
        with mock.patch.object(runner.Runner, "run_steps", side_effect=ValueError("boom")):
            code, run = self.run_it()
        self.assertEqual(code, 2)
        res = json.load(open(os.path.join(run, "results.json")))
        self.assertTrue(any("ValueError: boom" in e for e in res["errors"]), res["errors"])
        state = json.load(open(os.path.join(run, "run.json")))
        self.assertIn("Traceback", state["traceback"])
        self.assertIn("ValueError: boom", open(os.path.join(run, "runner.log")).read())
        self.assertIn("record stop", open(os.path.join(self.state, "calls.log")).read())

    def test_record_stop_after_a_failed_start(self):
        os.environ["FAKE_VM_FAIL"] = "after-start"
        code, run = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("record stop", open(os.path.join(self.state, "calls.log")).read())
        self.assertFalse(os.path.exists(os.path.join(self.state, "record.run")), "recorder left running")

    def test_sigterm_takes_the_cleanup_path(self):
        with open(self.scn, "w") as f:
            f.write('name="t"\nprofile="fake"\n[[step]]\nid="long"\ndo={wait_ms=20000}\n')
        p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(HERE), "bin", "hypr-qa"), "run", self.scn,
                              "--profiles-dir", os.path.join(HERE, "fake_profiles")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        deadline = time.monotonic() + 20
        while not os.path.exists(os.path.join(self.state, "record.run")) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(30), 2)
        self.assertIn("record stop", open(os.path.join(self.state, "calls.log")).read())
        runs = os.path.join(self.tmp, "runs", "t")
        res = json.load(open(os.path.join(runs, os.listdir(runs)[0], "results.json")))
        self.assertTrue(any("terminated" in e for e in res["errors"]), res["errors"])

    def test_hyprctl_ok_reply_commands_need_ok(self):
        code, run = self.run_it('name="h"\nprofile="fake"\n[[step]]\nid="kw"\n'
                                'do={hyprctl=["keyword", "general:gaps_in", "0"]}\n'
                                '[[step]]\nid="ver"\ndo={hyprctl=["version"]}\n')
        self.assertEqual(code, 2)
        res = json.load(open(os.path.join(run, "results.json")))
        st = {r["step"]: r for r in res["steps"]}
        self.assertFalse(st["kw"]["ok"])
        self.assertIn("keyword can't work", st["kw"]["error"])
        self.assertTrue(st["ver"]["ok"])

    def test_failed_action_and_stop_on_fail(self):
        os.environ["FAKE_VM_FAIL"] = "meta_l"
        code, run = self.run_it("stop_on_fail = true\n" + SCENARIO)
        self.assertEqual(code, 2)
        res = json.load(open(os.path.join(run, "results.json")))
        st = {e["id"]: e for e in res["expectations"]}
        self.assertEqual(st["press#1"]["status"], "error")
        self.assertEqual(st["baseline#1"]["status"], "pass")
        tl = [r["step"] for r in res["steps"]]
        self.assertNotIn("hh-capture", tl)


if __name__ == "__main__":
    unittest.main()
