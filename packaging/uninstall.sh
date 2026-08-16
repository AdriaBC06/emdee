#!/usr/bin/env bash
# Emdee — remove everything install.sh created.
# Copyright (C) 2026 Adrià Bonnin Catalán
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail

APP_ID="emdee"

DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${DATA_HOME}/applications"
ICON_DIR="${DATA_HOME}/icons/hicolor"

say() { printf '  %s\n' "$*"; }

printf '\nRemoving %s from the desktop environment\n\n' "${APP_ID}"

removed=0
for icon in \
    "${ICON_DIR}"/*/apps/"${APP_ID}".png \
    "${ICON_DIR}"/scalable/apps/"${APP_ID}".svg \
    "${ICON_DIR}"/symbolic/apps/"${APP_ID}"-symbolic.svg
do
    if [[ -e "${icon}" ]]; then
        rm -f "${icon}"
        removed=$((removed + 1))
    fi
done
say "removed ${removed} icon files"

if [[ -e "${DESKTOP_DIR}/${APP_ID}.desktop" ]]; then
    rm -f "${DESKTOP_DIR}/${APP_ID}.desktop"
    say "removed the desktop entry"
fi

if [[ -e "${BIN_DIR}/${APP_ID}" ]]; then
    rm -f "${BIN_DIR}/${APP_ID}"
    say "removed the launcher"
fi

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 \
    && gtk-update-icon-cache --force --quiet "${ICON_DIR}" >/dev/null 2>&1 || true

printf '\nDone. Your documents and preferences were left untouched.\n'
printf 'Preferences live in ~/.config/Emdee/ if you want them gone too.\n\n'
