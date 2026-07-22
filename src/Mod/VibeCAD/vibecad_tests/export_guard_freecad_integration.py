# SPDX-License-Identifier: LGPL-2.1-or-later
from pathlib import Path
import tempfile

import Part
import VibeCADManagedPolicy as managed_policy
from VibeCADManagedPolicy import default_policy


assert getattr(Part.export, "__vibecad_export_guard__", False)
policy = default_policy()
policy.update(managed=True, export_enabled=False)
original = managed_policy.load_managed_policy
managed_policy.load_managed_policy = lambda: policy
try:
    with tempfile.TemporaryDirectory(prefix="vibecad-export-guard-") as directory:
        target = Path(directory) / "blocked.step"
        try:
            Part.export([], str(target))
        except PermissionError as exc:
            assert "organization policy" in str(exc)
        else:
            raise AssertionError("The direct Part export was not blocked.")
        assert not target.exists()
finally:
    managed_policy.load_managed_policy = original

print("direct FreeCAD export policy integration passed")
