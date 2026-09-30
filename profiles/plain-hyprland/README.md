# plain-hyprland

Stock Arch Linux + stock Hyprland, logged in automatically, with only the
changes QA needs. It is the default guest for hypr-qa scenarios. The VM runs on
the KVM host (mason-desktop), never on the desktop you're working at.

## What's in the guest

| | |
|---|---|
| Base | newest Arch Linux cloud image (`Arch-Linux-x86_64-cloudimg-<build>.qcow2`), sha256-verified, cached read-only under `~/vms/hypr-qa/base/`, fully upgraded (`pacman -Syu`) at build time |
| Packages | `hyprland` (0.56.2 at the first build), `mesa` (llvmpipe; virtio-vga has no 3D), `foot`, `ttf-dejavu` (so OCR has a real font), `adwaita-cursors` (a real pointer, see Pointer theme below), `greetd` |
| Terminal | foot, `~qa/.config/foot/foot.ini` sets `DejaVu Sans Mono:size=14`: at foot's default size 10, tesseract misreads its light-on-dark text at 1280x800 |
| User | `qa`, password `qa`, passwordless sudo, the host's `~/.ssh/id_ed25519.pub` in `authorized_keys` (ssh password login off) |
| Login | greetd `[initial_session]` runs `/usr/bin/start-hyprland` as `qa` on every boot; the shipped `default_session` (agreety on tty1) is left as it was |
| Display | virtio-vga, output `Virtual-1`, pinned to `1280x800@60`, scale 1 (the virtio default mode, set explicitly as well) |
| Hyprland config | `~qa/.config/hypr/hyprland.lua` = the installed `/usr/share/hypr/hyprland.lua` with `terminal = "foot"`, plus [`files/hyprland-qa.lua`](files/hyprland-qa.lua): `XCURSOR_THEME=Adwaita`, `XCURSOR_SIZE=24` (`hl.env`), `ecosystem { no_update_news, no_donation_nag }`, `misc { disable_hyprland_logo, disable_splash_rendering, force_default_wallpaper = 0 }`, `animations { enabled = false }` |
| Snapshot | `session-ready`: an internal snapshot in the overlay, taken with the session up, no windows open, the screen settled |
| USB power | `/etc/udev/rules.d/99-hypr-qa-usb-no-autosuspend.rules` sets `power/control=on` for every USB device. Otherwise QEMU's usb-kbd autosuspends after 2 s idle, and the next key press takes ~130 ms instead of ~36 ms (measured by the recording spike). The build checks that the keyboard is still `active` after 6 s idle |
| Pointer theme | Adwaita at 24 px (`adwaita-cursors` in `/usr/share/icons`, `XCURSOR_THEME`/`XCURSOR_SIZE` set with `hl.env`, which runs before Hyprland creates its cursor manager). Without a theme, Hyprland draws its built-in 32x32 fallback pointer (a 25x31 droplet on screen). `default-cursors` already has `/usr/share/icons/default` inherit Adwaita. `cursor:enable_hyprcursor` stays at its default: no hyprcursor theme is installed, so the log's `Hyprcursor failed loading theme ..., falling back to XCursor` is expected |
| Cursor | stock Hyprland (`cursor:no_hardware_cursors` = 2, auto) leaves the virtio-gpu cursor plane unused, so the pointer is composited into the framebuffer and appears in `screendump` frames. Forcing `no_hardware_cursors` to `true` or `false` with `hyprctl eval` makes no difference: the plane stays unused. The build asserts this |
| Helpers | `/usr/local/bin/qa-session CMD...` runs CMD in the Hyprland session (it resolves `HYPRLAND_INSTANCE_SIGNATURE`, `WAYLAND_DISPLAY` and the DBus bus from `/run/user/$UID`). `/usr/local/bin/qa-cursor-plane` reports whether the DRM cursor plane is in use (see below) |

Hyprland 0.56 reads **Lua only** (`hyprland.lua`). `hyprctl keyword` fails with
"keyword can't work with non-legacy parsers. Use eval." So apply runtime
options with `eval`:

```sh
vm session hyprctl eval 'hl.config({ cursor = { no_hardware_cursors = true } })'
vm session hyprctl eval 'hl.config({ animations = { enabled = true } })'
```

### Is the pointer in the frames?

QMP `screendump` captures only the primary plane. If Hyprland puts the pointer
on the hardware cursor plane, recordings don't show it. Check before a run
that relies on the pointer:

```sh
vm ssh qa-cursor-plane    # "cursor-plane 36 crtc=(null) fb=0 unused", exit 0
```

It finds the cursor plane id (libdrm's `modetest -p`, type Cursor) and reads
its `crtc`/`fb` from `/sys/kernel/debug/dri/0/state` with sudo, mounting
debugfs if needed. The exit status is 0 when the plane is unused (the pointer
is composited into frames), 1 when it's in use, and 2 when it can't tell. To
check by hand:

```sh
vm ssh 'sudo mount -t debugfs none /sys/kernel/debug 2>/dev/null; sudo cat /sys/kernel/debug/dri/0/state' | grep -A2 '^plane'
```

## Host side

| | |
|---|---|
| Overlay | `~/vms/hypr-qa/plain-hyprland/plain-hyprland.qcow2` (20G virtual, backed by the cached base) |
| Seed | `~/vms/hypr-qa/plain-hyprland/seed.iso` (NoCloud, `xorriso -as mkisofs -V cidata -J -r`); the sources are in `seed/` next to it |
| vmkit dir | `~/vms/hypr-qa/plain-hyprland/.vmkit/` (qmp.sock, serial.log, qemu.pid, screenshots) |
| SSH | `127.0.0.1:2232` -> guest `:22`, user `qa`, key `~/.ssh/id_ed25519` |
| VNC | `127.0.0.1:5932` (display `:32`), a shared view of the same VM |
| QMP | always on: `.vmkit/qmp.sock` |

Settings are in [`profile.env`](profile.env); each one can be overridden from
the environment (`SSH_PORT=... vm up`).

## Build

```sh
profiles/plain-hyprland/build
```

It caches the newest cloud image, creates a fresh overlay and seed, boots
through `vmkit up`, and waits for the provisioning service
(`/var/lib/hypr-qa/status`, log at `/var/log/hypr-qa-provision.log`). Then it
reboots into the autologin session, waits for `hyprctl monitors` to answer,
checks 1280x800 and an empty `hyprctl configerrors`, and checks the pointer
(below). Then it waits for a stable frame. Last, it saves `session-ready` and shuts the VM down. A build replaces
the previous overlay and its snapshots.

The pointer check fails the build when the pointer on screen is not the
session theme's. It reads `XCURSOR_THEME`/`XCURSOR_SIZE` from a process
Hyprland spawns (they must be `CURSOR_THEME`/`CURSOR_SIZE` from `profile.env`),
copies `/usr/share/icons/$CURSOR_THEME/cursors/left_ptr` out of the guest,
moves the pointer to (640, 400) and compares a screendump with that image
composited over the background, allowing a 1 px offset.
[`check-pointer`](check-pointer) does the comparison (stdlib Python, no
model); it names Hyprland's built-in fallback pointer (a 25x31 shape at the
hotspot minus (3, 2)) when that is what's on screen. By hand:

```sh
check-pointer SHOT.png X Y LEFT_PTR_XCURSOR_FILE SIZE   # exit 0 match, 1 mismatch, 2 bad input
```

Timings on mason-desktop (2026-09-30, base image cached): about 105 s in all.
Of that, 50 s is the first boot to ssh (the cloud image's sshd waits for
the first NTP sync, see below), 27 s provisioning, 15 s the reboot into the
session, 5 s checks, 3 s `savevm` (the snapshot is 2.2 GiB) and 4 s shutdown.
Downloading the 560 MB base image the first time took about 5 s.

The cloud image orders sshd after `systemd-time-wait-sync`, and the first
NTP sync through QEMU user networking can take over a minute. Provisioning
masks that unit, so later boots don't wait: session and ssh are up about
15 s after power-on.

Everything runs under `nice -n 19 ionice -c3`. vmkit adds `nice` to qemu, and
`vm up` adds `ionice`.

## Use

`vm` passes this profile's flags to vmkit, so a command can't boot the overlay
with the wrong ports or devices:

```sh
V=profiles/plain-hyprland/vm

$V restore                 # boot + load session-ready (the usual start of a run)
$V session hyprctl version # a command in the Hyprland session
$V xexec hyprctl version   # the same via vmkit xexec (works: Hyprland exports its env to systemd --user)
$V shot now                # -> .vmkit/now.png
$V find 'some text'        # OCR
$V keys meta_l q           # SUPER+Q (stock bind): opens foot. One chord = qcodes as separate args
$V ssh                     # shell as qa
$V snapshots               # list snapshots
$V down                    # power off
```

`$V up` boots the snapshot's disk state without loading RAM. You get a normal
boot that autologs in, so it's slower than `restore` and not pixel-identical.

### Restore

`vm restore [NAME]` boots the VM if it's down, loads the snapshot with HMP
`loadvm`, then syncs the guest clock from the RTC (`hwclock --hctosys`). A
snapshot's clock is otherwise frozen at save time. A restore takes about 2.5 s from a stopped VM
(qemu start plus `loadvm`) and about 2.2 s on a running one, and `hyprctl`
answers right after. The restored screen is the empty session: no windows,
the plain dark Hyprland background. Always boot this overlay
through `vm`: `loadvm` needs the same block devices as `savevm`, including
the seed ISO.

vmkit's own `snapshot`/`restore` send `savevm`/`loadvm` as QMP commands. QEMU
has no such QMP commands, so they fail with `CommandNotFound`. `vm` wraps both
in `human-monitor-command` and checks the HMP reply text, because HMP reports
errors as text in `return`.

### Reset

- **Back to a clean session**: `vm restore`. It discards everything since
  the snapshot: disk, RAM and windows.
- **Different snapshot**: `vm snapshot my-state` / `vm restore my-state`.
  Snapshots live in the overlay until the next build.
- **From scratch**: `build`. It keeps the cached base image. To pick up a
  newer Arch image, run build again: it always uses the newest one. Old bases
  in `~/vms/hypr-qa/base/` can be deleted once no overlay uses them
  (`qemu-img info --backing-chain`).
