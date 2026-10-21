# steam-two-head

[`plain-hyprland`](../plain-hyprland/README.md) with **two virtio-gpu heads at two scales**, and
Steam installed and bootstrapped, for checking how a client behaves on mixed-scale outputs.

| | |
|---|---|
| `Virtual-1` | 1536x960, scale 1.5 (logical 1024x640), at `0x0` |
| `Virtual-2` | 1024x768, scale 1.0, at `1024x0` |
| Guest | 3 GiB RAM (Steam's webhelper fits, with the guest's page cache doing the rest), 24G disk, ssh `127.0.0.1:2233`, VNC `:33`, its own overlay, so it can sit next to a built `plain-hyprland` |
| Packages | everything in `plain-hyprland`, plus `steam` (multilib), `lib32-mesa`, Vulkan software drivers, `xorg-xwayland`, `xorg-xwininfo`, `xorg-xprop`, `xdotool`, `gamescope` |
| Snapshot | `session-ready`: session up, Steam already bootstrapped (its client download is in the image, so a scenario starts Steam in seconds, not minutes) and not running |

Small on purpose. The scenarios measure the scale, not the size, and a 4K head would cost the guest
more RAM than it has.

## How a second head works

QEMU's virtio-gpu reports only head 0 as connected until a display frontend sets a size for the others.
With no GUI attached the profile does it through the head's own VNC socket: `vm up` calls
`vmkit head-size 1 1024x768` (`HEAD_SIZES` in `profile.env`) right after boot, and the guest then
sees `Virtual-2` as hot-plugged. Both heads' preferred mode is `xres x yres` from `GPU_OPTS`; head 1
is resized to its own.

## Heads in scenarios

- `head = N` at the top of a scenario picks the head the recording and frame checks read (default 0).
  One head per run: two heads are two runs, or `vmkit = ["--head", "1", "shot", NAME]` for a still.
- Pointer actions (`vmkit move/click/tap`) take coordinates in the layout, 0-2047 by 0-767 here
  (`POINTER_AREA`), because the guest maps its one pointer over both heads.
- `[guest] hyprland` options apply as for any profile; `xwayland = { force_zero_scaling = true }`
  restarts XWayland, so give it a few seconds before starting an X11 client.

## Build and use

```sh
profiles/steam-two-head/build     # about 4 min, plus Steam's first-run download (a few hundred MB)
profiles/steam-two-head/vm restore && profiles/steam-two-head/vm session hyprctl monitors
bin/hypr-qa run examples/steam-fractional-scale.toml
```

`build` and `vm` here are `plain-hyprland`'s, run with this profile's `profile.env`
(`HQA_PROFILE_ENV`). Everything a derived profile adds lives in `extra/` (see `HQA_EXTRA_DIR` in
`plain-hyprland/profile.env`): `pre-install.sh` enables multilib, `packages.txt`,
`hyprland-extra.lua` sets the two monitors, `post-session.sh` starts Steam once so its bootstrap is
in the snapshot.

Steam needs no account here: the sign-in window is drawn by the same webhelper as everything
else, which is enough to see its scale.
