#!/usr/bin/env python3
"""Check that the VibeCAD build entry point is portable."""

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "build_vibecad.sh"


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        environment = dict(os.environ)
        environment.update({"PATH": "/usr/bin:/bin", "VIBECAD_BUILD_JOBS": "2"})
        result = subprocess.run(
            ["/bin/bash", str(SCRIPT), "--help"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    assert "Usage: tools/build_vibecad.sh" in result.stdout
    assert "VIBECAD_BUILD_JOBS" in result.stdout
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'provider_python="${VIBECAD_PROVIDER_PYTHON:-}"' in source
    assert '"${provider_python}" -m pip install' in source
    print("build_vibecad portability self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
