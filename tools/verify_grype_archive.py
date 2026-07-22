#!/usr/bin/env python3
"""Verify the repository-pinned Grype release archive before execution."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any


PIN_SCHEMA = "vibecad-grype-release-pin-v1"
PIN_VERSION = 1
GRYPE_VERSION = "0.116.0"
ARCHIVES = {
    "arm64": {
        "grype_arch": "arm64",
        "sha256": "9425c225d0d63d2b384baf2177d3aba713a2bfb800235848ce70169e78c9c5fa",
    },
    "x86_64": {
        "grype_arch": "amd64",
        "sha256": "92dc64f7f1c71f92f610b250d801837c75a3c7336cb44656e59c4f1a07939163",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_pin(architecture: str, version: str) -> dict[str, Any]:
    if version != GRYPE_VERSION:
        raise ValueError(f"Grype {version} is not the repository-pinned release.")
    pin = ARCHIVES.get(architecture)
    if pin is None:
        raise ValueError(f"Grype architecture {architecture} is not supported.")
    filename = f"grype_{version}_darwin_{pin['grype_arch']}.tar.gz"
    return {
        "schema": PIN_SCHEMA,
        "version": PIN_VERSION,
        "filename": filename,
        "sha256": pin["sha256"],
        "url": f"https://github.com/anchore/grype/releases/download/v{version}/{filename}",
    }


def verify_archive(path: Path, architecture: str, version: str) -> dict[str, Any]:
    pin = release_pin(architecture, version)
    if path.name != pin["filename"]:
        raise ValueError(
            f"The Grype archive name does not match the repository pin: {path.name}."
        )
    actual = _sha256(path)
    if actual != pin["sha256"]:
        raise ValueError(
            "The Grype archive SHA-256 does not match the repository pin: "
            f"expected {pin['sha256']}, received {actual}."
        )
    return pin


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--architecture", choices=sorted(ARCHIVES), required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        pin = verify_archive(args.archive, args.architecture, args.version)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(
        f"Verified Grype {args.version} for {args.architecture}: "
        f"{pin['sha256']}  {pin['filename']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
