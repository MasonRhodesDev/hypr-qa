# desktop-boot

A guest for testing the whole boot chain on three screens: UEFI (OVMF), GRUB,
Plymouth, greetd with the vigil greeter, then a Hyprland session started through
uwsm. It exists so the stages of the desktop-boot project can be proved end to
end in a VM, never on a live seat.

Unlike `plain-hyprland` it has no snapshot: QEMU cannot `savevm` OVMF's writable
pflash, so every run is a **cold boot**, which is the point.

## Commands

All paths relative to the repo root. Run heavy things niced on the KVM host.

```sh
# 1. build the guest (about 2 minutes; cached Arch cloud image, fully upgraded)
nice -n 19 ionice -c3 profiles/desktop-boot/build

# 2. put a desktop-boot branch and a vigil build into it (guest must be up)
V=profiles/desktop-boot
$V/vm up && $V/vm wait ssh 120
nice -n 19 $V/install-desktop-boot --desktop-boot /path/to/desktop-boot --ref scene-v2 \
                                   --vigil /path/to/vigil            # add --vigil-ref BRANCH for a commit instead of the tree
$V/vm down

# 3. a full boot recording, every head, power-on to desktop to reboot to greeter
nice -n 19 ionice -c3 examples/desktop-boot/full-boot --out /path/to/run
#    -> RUN/report.html, RUN/rec/head0..2/frames/%06d.png, RUN/marks.json, RUN/results.json

# 4. find frames: per head, numbered, with host-clock times
ls RUN/rec/head1/frames | head                    # 000000.png ...
head -3 RUN/rec/head1/frames.jsonl                # {"n","t_req","t","inactive"}  (t = CLOCK_MONOTONIC ns)
cat RUN/marks.json                                # the same clock: power-on, greeter, login-sent, desktop, reboot-sent, greeter-2
```

By hand: `vm up`, `vm record start --fps 10 --heads all --out RUN`, ..., `vm record stop`,
`vm shot --heads now` (files `now-head<N>.png` in the vmkit dir), `vm heads`,
`vm find --head 2 'text'` (OCR one head), `vm reset` (hard reset), `vm down`.
Head index n is the connector `Virtual-<n+1>`.

## The guest

| | |
|---|---|
| Base | newest Arch cloud image, sha256-verified, cached under `~/vms/hypr-qa/base/` (shared with plain-hyprland), full upgrade at build |
| Boot | OVMF (`vmkit up --uefi`), GRUB installed to the ESP mounted at `/efi` with a real NVRAM entry (`GRUB`), kernel and initramfs on the root btrfs |
| User | `qa`, password `qa` (known, test VM only; it lives in this profile), passwordless sudo, the host's ssh key |
| Packages | hyprland, uwsm, greetd, seatd, plymouth, grub, waybar, foot, mesa (llvmpipe), desktop-boot's dependencies, the vigil build dependencies (rust, clang, base-devel) |
| Greeter | greetd runs `/usr/local/bin/qa-greeter`: `vigil` when installed, otherwise `agreety` (so a fresh build still logs in). `/etc/greetd/vigil.toml`: kiosk user `qa` (password stage only), session list limited to `/usr/local/share/wayland-sessions` |
| Session | `Hyprland (desktop-boot)`: `desktop-boot-session uwsm start -e -D Hyprland hyprland.desktop`. `hyprland.lua` is the stock config plus the three monitors, a flat `background_color`, `desktop-boot-login-splash` and `waybar` on `hyprland.start` (no wallpaper daemon; the splash waits for the `waybar` layer only) |
| Debug tags | `DEBUG_STAGE_TAGS=1` (profile.env): set for `install.sh` (GRUB and Plymouth images), and as an environment variable of the greeter, the session and the login splash |

## The three outputs

One virtio GPU (`virtio-vga`, id `gpu0`, `max_outputs=3`): connectors
`Virtual-1..3`, vmkit heads 0 to 2.

| head | connector | mode | Hyprland position | transform | role |
|---|---|---|---|---|---|
| 0 | Virtual-1 | 3440x1440 | 1920x0 | 0 | centre ultrawide; the only head OVMF and GRUB light |
| 1 | Virtual-2 | 1920x1080 | 0x0 | 0 | left |
| 2 | Virtual-3 | 3840x2160 | 5360x0 | 3 (portrait, 2160x3840) | right |

The order is the physical machine's, not the order in which the task listed the sizes:
firmware and GRUB draw on head 0 only, and that must be the ultrawide for GRUB's theme
(laid out for one pixel mode) to match. Edit `HEAD_MODES`, `HEAD_POS`, `HEAD_TRANSFORM` in
`profile.env` to change it; `vm layout` prints the compositor-space rectangles vmkit's
pointer mapping needs, and `vm` passes them on every call.

### Pinning a mode per output (what works)

- `xres`/`yres` on the device apply to **every** output's EDID and default mode; there is
  no per-output size (`outputs` only sets names). The profile sets them to head 0's mode.
- Kernel `video=Virtual-N:WxH@60` creates the mode on that connector, with or without the
  EDID listing it. Provisioning writes one per connector into `GRUB_CMDLINE_LINUX_DEFAULT`
  (desktop-boot's installer de-duplicates options by key, so only the first `video=` would
  survive if they were in its config).
- The Hyprland `hl.monitor` rule then selects the same mode and sets position and transform.
- `PANEL_ORIENTATION="Virtual-3:right_side_up"` goes through desktop-boot's own option.
  Whether the virtio driver honours it is a property of the driver, see the findings below.
- OVMF: see the findings below for what firmware and GRUB resolve to.

## Host side

State is under `~/vms/hypr-qa/desktop-boot/` (override `HQA_VMS`, `PROFILE_DIR`): the overlay,
`seed.iso`, and `.vmkit/` (sockets, `serial.log`, `efivars.fd` = the OVMF NVRAM, shots).
ssh is `127.0.0.1:2233`, VNC `127.0.0.1:5933` (head 0). Both ports are checked before boot.

## Install hook details

`install-desktop-boot` streams the tree into the guest (`git archive REF`, or the working
tree without ignored files), copies the profile's `config.env` (GRUB mode, ESP path, panel
orientation) and runs desktop-boot's `install.sh`. vigil is built **in the guest**, as an Arch
package from the checkout's own `packaging/PKGBUILD` (source swapped for the tree, tests
skipped, cargo's target dir kept in `~/.cache/vigil-target` so rebuilds are incremental) and
installed with `pacman -U`, so its post-install hooks run. A host build would depend on the host
matching the guest's library versions; `--vigil-pkg FILE` installs a package built elsewhere.
Nothing runs on the host system.
