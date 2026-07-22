#!/bin/zsh
# SPDX-License-Identifier: LGPL-2.1-or-later
set -euo pipefail

artifact=""
profile="VibeCAD"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --artifact) artifact="$2"; shift 2 ;;
    --keychain-profile) profile="$2"; shift 2 ;;
    *) print -r -- "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -f "$artifact" ]] || { print -r -- "Artifact does not exist: $artifact" >&2; exit 2; }
result="$(xcrun notarytool submit "$artifact" --keychain-profile "$profile" --wait --output-format json)"
status="$(print -r -- "$result" | /usr/bin/python3 -c 'import json,sys; print((json.load(sys.stdin).get("status") or "").lower())')"
[[ "$status" == "accepted" ]] || { print -r -- "$result" >&2; exit 1; }
xcrun stapler staple "$artifact"
xcrun stapler validate "$artifact"
