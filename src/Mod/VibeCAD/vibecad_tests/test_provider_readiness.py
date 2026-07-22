# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from tools.probe_provider_readiness import run_probe


def test_timeout_before_result_fails_closed_without_data_claim(tmp_path) -> None:
    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    result = run_probe(Path("FreeCADCmd"), Path("child.py"), tmp_path / "result.json", 1, runner=runner)
    assert result["ready_for_live_benchmark"] is False
    assert result["process_timed_out"] is True
    assert result["prompt_sent"] is False
    assert result["document_data_sent"] is False


def test_fresh_completed_result_can_authorize_live_benchmark(tmp_path) -> None:
    output = tmp_path / "result.json"

    def runner(command, **kwargs):
        output.write_text(json.dumps({
            "schema": "vibecad-provider-readiness-v1", "version": 1,
            "can_call_provider": True, "prompt_sent": False,
            "document_data_sent": False, "provider": "openai", "model": "model",
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    result = run_probe(Path("FreeCADCmd"), Path("child.py"), output, 1, runner=runner)
    assert result["ready_for_live_benchmark"] is True
    assert result["process_timed_out"] is False


def test_stale_result_is_deleted_before_failed_probe(tmp_path) -> None:
    output = tmp_path / "result.json"
    output.write_text('{"can_call_provider":true}', encoding="utf-8")

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=9)

    result = run_probe(Path("FreeCADCmd"), Path("child.py"), output, 1, runner=runner)
    assert result["ready_for_live_benchmark"] is False
    assert result["process_exit_code"] == 9
