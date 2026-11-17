#!/bin/bash
set -euo pipefail
grep -q '^\[multilib\]' /etc/pacman.conf || printf '\n[multilib]\nInclude = /etc/pacman.d/mirrorlist\n' >>/etc/pacman.conf
pacman -Sy --noconfirm
