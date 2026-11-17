#!/bin/bash
set -euo pipefail

STATE=/var/lib/hypr-qa
SHARE=/usr/local/share/hypr-qa
QA_USER=qa
mkdir -p "$STATE"
exec > >(tee -a /var/log/hypr-qa-provision.log) 2>&1
echo running >"$STATE/status"
trap 'rc=$?; [ $rc -eq 0 ] || { echo "provision failed (rc=$rc, line $LINENO)"; echo fail >"$STATE/status"; }' EXIT

echo "== $(date -u +%FT%TZ) provisioning"

pacman -Sy --noconfirm archlinux-keyring
bash "$SHARE/pre-install.sh"
extra=$(grep -v '^[[:space:]]*#' "$SHARE/packages.txt" | tr '\n' ' ')
# shellcheck disable=SC2086
pacman -Syu --noconfirm --needed $extra \
    hyprland mesa \
    foot \
    ttf-dejavu \
    adwaita-cursors \
    greetd \
    accountsservice polkit-gnome

echo "== installed: $(pacman -Q hyprland foot greetd mesa adwaita-cursors accountsservice polkit polkit-gnome | tr '\n' ' ')"

home=$(getent passwd "$QA_USER" | cut -d: -f6)
install -d -o "$QA_USER" -g "$QA_USER" "$home/.config" "$home/.config/hypr"
cfg="$home/.config/hypr/hyprland.lua"
sed -E 's/^(local terminal[[:space:]]*=[[:space:]]*)"kitty"/\1"foot"/' /usr/share/hypr/hyprland.lua >"$cfg"
grep -qE '^local terminal[[:space:]]*=[[:space:]]*"foot"' "$cfg" \
    || echo "WARN: stock config has no kitty terminal line; terminal not switched to foot"
cat "$SHARE/hyprland-qa.lua" "$SHARE/hyprland-extra.lua" >>"$cfg"
chown "$QA_USER:$QA_USER" "$cfg"

install -d -o "$QA_USER" -g "$QA_USER" "$home/.config/foot"
install -m 0644 -o "$QA_USER" -g "$QA_USER" "$SHARE/foot.ini" "$home/.config/foot/foot.ini"

[ -f /etc/greetd/config.toml.orig ] || cp /etc/greetd/config.toml /etc/greetd/config.toml.orig
cat /etc/greetd/config.toml.orig "$SHARE/greetd-autologin.toml" >/etc/greetd/config.toml
systemctl enable greetd.service

systemctl mask systemd-time-wait-sync.service

bash "$SHARE/post-install.sh"

echo "== $(date -u +%FT%TZ) provisioning done"
touch "$STATE/provisioned"
echo ok >"$STATE/status"
