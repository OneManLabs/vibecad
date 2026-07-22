#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read provider readiness in FreeCAD without sending a prompt or document."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

def _write(target: Path, report: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    configured_output = str(os.environ.get("VIBECAD_PROVIDER_READINESS_OUTPUT") or "").strip()
    if not configured_output:
        raise RuntimeError("VIBECAD_PROVIDER_READINESS_OUTPUT is required.")
    target = Path(configured_output).resolve()
    base = {
        "schema": "vibecad-provider-readiness-v1", "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "can_call_provider": False, "prompt_sent": False, "document_data_sent": False,
    }
    _write(target, {**base, "stage": "importing_service"})
    from VibeCADCore import VibeCADService

    _write(target, {**base, "stage": "creating_service"})
    service = VibeCADService()
    _write(target, {**base, "stage": "reading_provider"})
    provider = service.provider_name()
    _write(target, {**base, "stage": "reading_dotenv_path", "provider": provider})
    dotenv_path = service._dotenv_path()
    _write(target, {**base, "stage": "resolving_auth", "provider": provider})
    from VibeCADAuth import resolve_auth_state

    auth = resolve_auth_state(dotenv_path=dotenv_path, provider=provider)
    report = {
        **base, "stage": "complete",
        "provider": provider, "model": service.provider_model(),
        "auth_status": auth.status.value, "auth_source": auth.source,
        "can_call_provider": bool(auth.can_call_provider),
        "online_by_default": bool(service.use_online_provider_by_default()),
    }
    _write(target, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
