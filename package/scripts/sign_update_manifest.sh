#!/bin/bash
# SPDX-License-Identifier: LGPL-2.1-or-later
set -euo pipefail

manifest="${1:-}"
private_key="${2:-}"
signature="${3:-${manifest}.sig}"
[[ -f "$manifest" && -f "$private_key" ]] || {
    echo "Usage: $0 MANIFEST PRIVATE_KEY [SIGNATURE]" >&2
    exit 2
}
temporary="${signature}.tmp.$$"
trap 'rm -f "$temporary"' EXIT
openssl dgst -sha256 -sign "$private_key" -out "$temporary" "$manifest"
openssl base64 -A -in "$temporary" -out "$signature"
printf '\n' >> "$signature"
