# hypr-qa

QA for Hyprland apps, always in a VM. Built on
[vmkit](https://github.com/MasonRhodesDev/vmkit), which boots and drives the QEMU guest and records
its screen with every action on the same host clock. hypr-qa adds only the
Hyprland parts:

- **Guest profiles** (`profiles/`): cloud image + cloud-init that boot straight
  into a Hyprland session, always with QMP. First profile: `plain-hyprland`.
- **Session helpers**: push a binary, run commands inside the guest's Hyprland
  session, apply Hyprland options (`hyprctl eval 'hl.config({...})'`).
- **Scenarios** (`docs/scenario-schema.md`): TOML files of steps, where each step is an action
  plus checks on the frames recorded at set offsets after it. The run writes a
  report with a contact sheet per step, a pass/fail summary and the video.

Status: early. The scenario schema is a v0 draft; the runner works end to end on
`plain-hyprland`.

## Running scenarios

```sh
bin/hypr-qa run examples/foot-smoke.toml    # -> runs/foot-smoke/<UTC>/, exit 0 pass / 1 fail / 2 error
bin/hypr-qa check runs/foot-smoke/<UTC>     # re-run only the checks on that recording
bin/hypr-qa check RUN --scenario tuned.toml # ... with edited checks
bin/hypr-qa validate SCENARIO.toml          # schema errors only, no VM
```

`run` restores the profile's snapshot through `profiles/<name>/vm`, applies
`[guest] hyprland`, runs `[[setup]]`, starts `[hyprhands]` if set, requires the
`qa-cursor-plane` precheck to exit 0, records, performs the steps, and then has vmkit check
the frames and write `report.html`. `results.json` starts with the recorder's
timing (`degraded`). Options: `--runs-dir` (or `$HYPR_QA_RUNS`), `--down` to power
the VM off afterwards. vmkit is found through the profile (`$VMKIT` overrides it).
Run it niced (`nice -n 19 ionice -c3`) on the KVM host.

The code is stdlib Python 3.11+ in `lib/hypr_qa/`; tests: `python3 -m unittest`
(the end-to-end runner test uses a fake profile and needs vmkit at `$VMKIT` or
`~/repos/vmkit`). `tests/guest_fake_hyprhands.toml` exercises the hyprhands
protocol path in a real guest with a fake serve.

VMs need KVM, so runs happen on a KVM host, never on the desktop you're using.

## License

MIT © Mason Rhodes
