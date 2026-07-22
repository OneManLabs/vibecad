#!/bin/bash
# SPDX-License-Identifier: LGPL-2.1-or-later
set -euo pipefail

usage() {
    echo "Usage: $0 --app APP --output PKG --version VERSION [--identifier ID] [--sign ID]" >&2
}

app=""
output=""
version=""
identifier="com.vibecad.desktop"
signing_identity=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app) app="$2"; shift 2 ;;
        --output) output="$2"; shift 2 ;;
        --version) version="$2"; shift 2 ;;
        --identifier) identifier="$2"; shift 2 ;;
        --sign) signing_identity="$2"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[[ -d "$app" && -n "$output" && -n "$version" ]] || { usage; exit 2; }
mkdir -p "$(dirname "$output")"
args=(--component "$app" --install-location /Applications --identifier "$identifier" --version "$version")
if [[ -n "$signing_identity" ]]; then
    args+=(--sign "$signing_identity" --timestamp)
fi
/usr/bin/pkgbuild "${args[@]}" "$output"
/usr/sbin/pkgutil --check-signature "$output" || [[ -z "$signing_identity" ]]
