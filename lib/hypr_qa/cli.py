"""hypr-qa command line.

  hypr-qa run SCENARIO.toml [--runs-dir DIR] [--down]
  hypr-qa check RUN_DIR [--scenario FILE]
  hypr-qa validate SCENARIO.toml...

Exit codes: 0 all passed, 1 a check failed, 2 an error (bad scenario, boot,
precheck, setup, a failed action, an unresolvable window).
"""
import argparse
import sys
import traceback

from . import runner
from .lua import LuaError
from .scenario import ScenarioError, load


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hypr-qa", description="Hyprland QA scenarios in a VM (see docs/scenario-schema.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="boot/restore, record, perform the steps, check, report")
    r.add_argument("scenario")
    r.add_argument("--runs-dir", help="where runs/<name>/<UTC>/ go (default: $HYPR_QA_RUNS or <repo>/runs)")
    r.add_argument("--profiles-dir", help="profiles directory (default: <repo>/profiles)")
    r.add_argument("--down", action="store_true", help="power the VM off after the run")
    r.add_argument("--serve-timeout", type=float, default=30.0, help="seconds to wait for a hyprhands reply")
    c = sub.add_parser("check", help="re-run only the checks against a run's recording")
    c.add_argument("run_dir")
    c.add_argument("--scenario", help="use this scenario instead of the run's saved copy (to tune checks)")
    c.add_argument("--profiles-dir")
    v = sub.add_parser("validate", help="validate scenario files only")
    v.add_argument("scenario", nargs="+")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "run":
            return runner.Runner(a.scenario, a.runs_dir, a.profiles_dir, a.down, a.serve_timeout).main()
        if a.cmd == "check":
            return runner.check_run(a.run_dir, a.scenario, a.profiles_dir)
        for p in a.scenario:
            doc = load(p)
            print(f"ok  {p}  ({len(doc['step'])} steps, "
                  f"{sum(len(s.get('expect', [])) for s in doc['step'])} expectations)")
        return 0
    except (ScenarioError, LuaError, runner.RunError) as e:
        print(f"hypr-qa: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"hypr-qa: {e}", file=sys.stderr)
        return 2
    except runner.Terminated:
        print("hypr-qa: terminated (SIGTERM)", file=sys.stderr)
        return 2
    except Exception:   # a bug, never "check failed" (1) or a pass
        traceback.print_exc()
        print("hypr-qa: unexpected error (exit 2)", file=sys.stderr)
        return 2
