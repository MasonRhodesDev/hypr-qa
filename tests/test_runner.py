"""The runner end to end against a fake profile `vm` (tests/fake_profiles/fake/vm)
and the fake hyprhands serve: no QEMU. Checks and the report are the real vmkit's,
found at $VMKIT or ~/repos/vmkit/bin/vmkit; skipped without it."""
import json
import os
import shutil
import sys
import tempfile
import unittest

from hypr_qa import runner

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

        # check RUN_DIR: same verdict from the saved copy; a tuned scenario flips it
        self.assertEqual(runner.check_run(run, profiles_dir=os.path.join(HERE, "fake_profiles")), 0)
        tuned = os.path.join(self.tmp, "tuned.toml")
        with open(tuned, "w") as f:
            f.write(SCENARIO.replace('color = "#ffffff"\n\n[[step]]\nid = "hh-capture"',
                                     'color = "#ff0000"\n\n[[step]]\nid = "hh-capture"'))
        self.assertEqual(runner.check_run(run, tuned, profiles_dir=os.path.join(HERE, "fake_profiles")), 1)
        res = json.load(open(os.path.join(run, "results.json")))
        self.assertEqual(res["counts"]["fail"], 1)

    def test_precheck_failure_is_an_error_without_recording(self):
        os.environ["FAKE_VM_FAIL"] = "qa-cursor-plane"
        code, run = self.run_it()
        self.assertEqual(code, 2)
        res = json.load(open(os.path.join(run, "results.json")))
        self.assertTrue(any("qa-cursor-plane" in e for e in res["errors"]), res["errors"])
        self.assertFalse(os.path.exists(os.path.join(run, "frames.jsonl")))

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
