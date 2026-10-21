#!/bin/bash
# Steam is in [multilib], which the cloud image leaves off.
set -euo pipefail
grep -q '^\[multilib\]' /etc/pacman.conf || printf '\n[multilib]\nInclude = /etc/pacman.d/mirrorlist\n' >>/etc/pacman.conf
pacman -Sy --noconfirm
