#!/bin/bash
# First-boot provisioning for desktop-boot (root, from hypr-qa-provision.service,
# installed by the cloud-init seed). Writes ok|fail to /var/lib/hypr-qa/status;
# the host's `build` polls it.
set -euo pipefail

STATE=/var/lib/hypr-qa
SHARE=/usr/local/share/hypr-qa
mkdir -p "$STATE"
exec > >(tee -a /var/log/hypr-qa-provision.log) 2>&1
echo running >"$STATE/status"
trap 'rc=$?; [ $rc -eq 0 ] || { echo "provision failed (rc=$rc, line $LINENO)"; echo fail >"$STATE/status"; }' EXIT

# shellcheck source=/dev/null
HOME="${HOME:-/root}"   # profile.env expands $HOME for host paths
. "$SHARE/profile.env"
echo "== $(date -u +%FT%TZ) provisioning"

# The image's keyring can be older than current package signatures; refresh it
# first, then one full upgrade together with the install (no partial upgrades).
# desktop-boot's requirements (its README), the vigil build and run deps, and
# the desktop pieces the session needs (waybar is a layer the login splash waits for).
pacman -Sy --noconfirm archlinux-keyring
pacman -Syu --noconfirm --needed \
    hyprland uwsm mesa foot waybar ttf-dejavu adwaita-cursors \
    greetd seatd plymouth grub efibootmgr \
    python-pillow python-gobject librsvg gtk4-layer-shell inter-font ttf-jetbrains-mono \
    base-devel rust clang git rsync libinput libxkbcommon libdrm pam fontconfig hwdata
echo "== installed: $(pacman -Q hyprland uwsm greetd plymouth grub mesa | tr '\n' ' ')"

QA_USER=qa
home=$(getent passwd "$QA_USER" | cut -d: -f6)

# ---- swap: the vigil build (slint) needs more than 4 GB with parallel rustc ----------
if [ "$(swapon --noheadings --bytes --show=SIZE | awk '{s+=$1} END {print s+0}')" -lt 4000000000 ]; then   # the image ships 512 MB
    btrfs filesystem mkswapfile --size 6g /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap defaults 0 0' >>/etc/fstab
fi

# ---- Hyprland config: stock example (terminal foot) + QA overrides + the monitors
install -d -o "$QA_USER" -g "$QA_USER" "$home/.config" "$home/.config/hypr" "$home/.config/foot"
cfg="$home/.config/hypr/hyprland.lua"
sed -E 's/^(local terminal[[:space:]]*=[[:space:]]*)"kitty"/\1"foot"/' /usr/share/hypr/hyprland.lua >"$cfg"
mon=""
for i in "${!HEAD_MODES[@]}"; do
    mon+=$(printf 'hl.monitor({ output = "Virtual-%d", mode = "%s@60", position = "%s", scale = 1, transform = %s })\n' \
        $((i + 1)) "${HEAD_MODES[$i]}" "${HEAD_POS[$i]}" "${HEAD_TRANSFORM[$i]}")$'\n'
done
python3 - "$SHARE/hyprland-qa.lua.tmpl" "$mon" "$DEBUG_STAGE_TAGS" >>"$cfg" <<'PY'
import sys
t = open(sys.argv[1]).read()
sys.stdout.write(t.replace("@MONITORS@", sys.argv[2].rstrip("\n")).replace("@DEBUG_STAGE_TAGS@", sys.argv[3]))
PY
chown "$QA_USER:$QA_USER" "$cfg"
install -m 0644 -o "$QA_USER" -g "$QA_USER" "$SHARE/foot.ini" "$home/.config/foot/foot.ini"

# ---- the greeter and the session ------------------------------------------------
# greetd runs qa-greeter: vigil when it is installed (install-desktop-boot puts it
# there), otherwise agreety, so a fresh build still reaches a desktop.
install -d /etc/greetd /usr/local/share/wayland-sessions
cat >/usr/local/bin/qa-greeter <<'GREETER'
#!/bin/sh
if [ -x /usr/bin/vigil ]; then exec /usr/bin/env DEBUG_STAGE_TAGS=@DEBUG_STAGE_TAGS@ /usr/bin/vigil; fi
exec /usr/bin/agreety --cmd "/usr/bin/env DEBUG_STAGE_TAGS=@DEBUG_STAGE_TAGS@ /usr/local/bin/desktop-boot-session uwsm start -e -D Hyprland hyprland.desktop"
GREETER
sed -i "s/@DEBUG_STAGE_TAGS@/$DEBUG_STAGE_TAGS/g" /usr/local/bin/qa-greeter
chmod 0755 /usr/local/bin/qa-greeter
# desktop-boot-session is installed by desktop-boot's install.sh; until then the
# fallback agreety session would not start, so ship a pass-through stand-in.
[ -e /usr/local/bin/desktop-boot-session ] || printf '#!/bin/sh\nexec "$@"\n' >/usr/local/bin/desktop-boot-session
chmod 0755 /usr/local/bin/desktop-boot-session
sed "s|@DEBUG_STAGE_TAGS@|$DEBUG_STAGE_TAGS|g" "$SHARE/desktop-boot.session" >/usr/local/share/wayland-sessions/desktop-boot.desktop
sed 's|^command = .*|command = "/usr/local/bin/qa-greeter"|' "$SHARE/greetd.toml" >/etc/greetd/config.toml
install -Dm644 "$SHARE/vigil.toml" /etc/greetd/vigil.toml
systemctl enable greetd.service
systemctl set-default graphical.target

# ---- bootloader: a real GRUB entry in the NVRAM, ESP mounted at /efi --------------
# The cloud image boots through the removable path with the ESP unmounted.
# desktop-boot's installer wants the ESP mounted and a named loader.
esp=$(lsblk -nrpo PATH,PARTTYPE | awk 'tolower($2)=="c12a7328-f81f-11d2-ba4b-00a0c93ec93b"{print $1; exit}')
[ -n "$esp" ] || { echo "no EFI system partition found"; exit 1; }
mkdir -p /efi
grep -q ' /efi ' /etc/fstab || echo "UUID=$(blkid -s UUID -o value "$esp") /efi vfat umask=0077 0 2" >>/etc/fstab
mountpoint -q /efi || mount /efi
grub-install --target=x86_64-efi --efi-directory=/efi --bootloader-id=GRUB
# Kernel video= options: one per connector, so the framebuffer (Plymouth) and the
# greeter see the intended mode on every head. (desktop-boot's installer de-duplicates
# options by key, so more than one video= has to be here, not in its config.)
vopts=""
for i in "${!HEAD_MODES[@]}"; do vopts+=" video=Virtual-$((i + 1)):${HEAD_MODES[$i]}@60"; done
sed -i -E "s|^(GRUB_CMDLINE_LINUX_DEFAULT=\")(.*)\"$|\1\2$vopts\"|" /etc/default/grub
grub-mkconfig -o /boot/grub/grub.cfg

# desktop-boot's config.env for the guest (the host hook copies it into the checkout).
cat >"$SHARE/desktop-boot-config.env" <<CFG
# Written by hypr-qa's desktop-boot profile.
GRUB_MODE="${HEAD_MODES[0]}"
GRUB_EFI_DIR="/efi"
GRUB_BOOTLOADER_ID="GRUB"
PANEL_ORIENTATION="${PANEL_ORIENTATION}"
CFG

# The cloud image orders sshd after time-sync; NTP through QEMU user networking
# can take over a minute. The guest clock comes from the host RTC.
systemctl mask systemd-time-wait-sync.service

echo "== $(date -u +%FT%TZ) provisioning done"
touch "$STATE/provisioned"
echo ok >"$STATE/status"
