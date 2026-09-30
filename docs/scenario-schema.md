# Scenario schema (v0 draft)

Status: **draft, published early so consumers can write against it.** The shape is
stable; the timing numbers (frame rate, how close an offset can be resolved) are
pending the recording spike and will be filled in here. Breaking changes before v1
will be called out in this file's changelog.

A scenario is one TOML file. `hypr-qa run SCENARIO.toml` boots (or restores) a guest,
records its screen, performs each step, stamps every action on the host clock, then
checks the recorded frames against each step's expectations and writes a report.
`hypr-qa check RUN_DIR` re-evaluates the checks against an existing recording without
rerunning the VM, so checks can be tuned cheaply.

QA always runs in a VM on mason-desktop. Nothing here ever touches a live desktop.

## Clock

Every timestamp is the **host's `CLOCK_MONOTONIC`, in nanoseconds**, taken by the
runner on mason-desktop. The recorder stamps each frame on the same clock. Nothing is
stamped by the guest.

Each action records:

| field    | meaning |
|----------|---------|
| `t_send` | just before the action leaves the host (QMP write, ssh write) |
| `t_ack`  | when the action's reply came back (QMP return, hyprhands `{"ok": ...}`, command exit) |

Expectations are offset from `t_send` by default, or from `t_ack` with `anchor = "ack"`.
For hyprhands actions `t_ack` is usually the better anchor: it is after the guest has
processed the request.

## Coordinates

Guest framebuffer pixels, origin top-left, the same as `vmkit shot`. A region is
`[x, y, w, h]`. A negative `x` or `y` counts from the right or bottom edge, so the
bottom 60 px of the screen is `[0, -60, 0, 60]`. A `w` or `h` of `0` means "to the edge".

## File layout

```toml
name    = "hyprhands-overlay"     # required; used for the run directory
profile = "plain-hyprland"        # guest profile (profiles/<name>/)
snapshot = "session-ready"        # restore this snapshot before the run (default: profile's)

[guest]
hyprland = { "cursor:no_hardware_cursors" = true }  # hyprctl keywords applied before step 1

[[setup]]                          # runs before recording starts; not checked
push = { src = "target/release/hyprhands", dst = "/usr/local/bin/hyprhands", mode = "0755" }

[[setup]]
session = "hyprhands --version"    # a command in the guest's Hyprland session

[hyprhands]                        # optional: start one `hyprhands serve` for hyprhands actions
argv = ["hyprhands", "serve", "--monitor", "Virtual-1"]

[[step]]
id = "click-middle"                # required, unique; names frames and report rows
do = { hyprhands = { op = "click", x = 640, y = 400 } }
settle_ms = 500                    # wait after the action before the next step (default 0)

  [[step.expect]]
  at = "+50ms..+300ms"             # a window; see "Timing"
  anchor = "ack"
  check = "pixel"
  region = [608, 368, 64, 64]
  color = "#00b4ff"
  tolerance = 40                   # per-channel distance allowed (alpha-blended overlays need slack)
  min_fraction = 0.05              # share of region pixels that must match
```

### Actions (`do`)

Exactly one key per step:

| key         | value | performed by |
|-------------|-------|--------------|
| `hyprhands` | a request object as in hyprhands `src/proto.rs` (`{op = "...", ...args}`) | the runner's `hyprhands serve` session over ssh |
| `vmkit`     | argv for a vmkit input command, e.g. `["click", "640", "400"]`, `["keys", "super-ret"]`, `["type", "hello"]` | QMP, host side |
| `session`   | a shell command run in the guest's Hyprland session (Wayland/Hyprland/DBus env imported) | ssh |
| `hyprctl`   | argv after `hyprctl`, e.g. `["dispatch", "workspace", "2"]` | ssh, in session |
| `wait_ms`   | an integer; no action, only a timestamped marker | host |

A failed action (error reply, nonzero exit) fails the step and records the error; later
steps still run unless `stop_on_fail = true` at the top level.

### Timing (`at`)

- `at = "+300ms"`: the first frame at or after anchor + 300 ms.
- `at = "+50ms..+300ms"`: every frame in the window. With `mode = "any"` (the default for
  windows) it passes if any frame passes; with `mode = "all"`, only if all do.
- `at = "-100ms"`: frames before the action are allowed, for a baseline ("was not there yet").

An offset window with no recorded frame in it is an **error**, not a pass. It means the
recording couldn't resolve that window; the report says so.

### Checks (`check`)

All checks are model-free. Any check can be inverted with `not = true` (e.g. "the ring
is gone by +600ms").

| check      | fields | passes when |
|------------|--------|-------------|
| `pixel`    | `region`, `color` (`#rrggbb`), `tolerance` (0-255, default 16), `min_fraction` (default 1.0) | at least `min_fraction` of the region's pixels are within `tolerance` of `color` on every channel |
| `ocr`      | `region` (optional), `text` (substring, case-insensitive) or `regex` | tesseract finds it in the region |
| `template` | `image` (PNG path relative to the scenario), `region` or `near = [x, y]` + `radius`, `threshold` (0-1, default 0.9) | the template matches inside the search area |
| `changed`  | `region`, `min_px` (default 50), `fuzz` (percent, default 5) | the region differs from the last frame before the anchor by at least `min_px` pixels |

`changed` is the cheap "did anything happen here at all" check, useful before writing
exact-color checks.

### The cursor

The pointer is drawn into recorded frames only if Hyprland uses a software cursor.
With a hardware cursor plane, the screen capture omits it. Scenarios that check the cursor must set
`[guest] hyprland = { "cursor:no_hardware_cursors" = true }`. *(Pending the spike:
whether this is required and sufficient.)*

## Output

`runs/<name>/<UTC timestamp>/`:

| file | content |
|------|---------|
| `timeline.jsonl` | one line per action: `{step, kind, detail, t_send, t_ack, ok, error?}` |
| `frames.jsonl`   | one line per recorded frame: `{n, t}` |
| `video.mkv`      | the recording (frame timestamps match `frames.jsonl`) |
| `frames/`        | PNGs of every frame a check used, named `<step>-<expect#>-<ms>.png` |
| `results.json`   | per expectation: pass/fail/error, frames used, measured values |
| `report.html`    | a contact sheet per step (frames around the action with the check region outlined), pass/fail summary, video |

Exit code: `0` all passed, `1` a check failed, `2` an error (boot, action, or unresolvable window).

## Changelog

- v0 (2026-09-30): first draft.
