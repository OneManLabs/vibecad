# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deterministic Tier 1 CAD capability baseline inside FreeCADCmd."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import FreeCAD as App
import Mesh
import Part


def _feature(doc, name, shape, **parameters):
    obj = doc.addObject("PartDesign::Feature", name)
    obj.Shape = shape
    for key, value in parameters.items():
        obj.addProperty("App::PropertyLength", key, "Design Parameters")
        setattr(obj, key, float(value))
    return obj


def _build(operation, doc, output):
    if operation == "exact_box":
        obj = doc.addObject("Part::Box", "Box")
        obj.Length, obj.Width, obj.Height = 40, 30, 20
        return obj, lambda item: abs(item.Shape.BoundBox.XLength - 40) < 1e-6
    if operation == "centered_hole":
        plate = Part.makeBox(40, 30, 10)
        hole = Part.makeCylinder(3, 10, App.Vector(20, 15, 0))
        obj = _feature(doc, "PlateWithCenteredHole", plate.cut(hole), HoleDiameter=6)
        return obj, lambda item: item.Shape.isValid() and len(item.Shape.Solids) == 1
    if operation == "round_edges":
        box = Part.makeBox(30, 30, 30)
        obj = _feature(doc, "RoundedCube", box.makeFillet(2, box.Edges), FilletRadius=2)
        return obj, lambda item: item.Shape.isValid() and len(item.Shape.Edges) > 12
    if operation == "hollow_enclosure":
        outer = Part.makeBox(60, 40, 25)
        inner = Part.makeBox(56, 36, 25, App.Vector(2, 2, 2))
        obj = _feature(doc, "HollowEnclosure", outer.cut(inner), WallThickness=2)
        return obj, lambda item: item.Shape.isValid() and abs(item.Shape.BoundBox.ZLength - 25) < 1e-6
    if operation == "change_dimension":
        obj = doc.addObject("Part::Box", "ResizableBox")
        obj.Length, obj.Width, obj.Height = 40, 30, 20
        doc.recompute()
        obj.Length = 55
        return obj, lambda item: abs(item.Shape.BoundBox.XLength - 55) < 1e-6
    if operation == "mirror_feature":
        plate = Part.makeBox(40, 30, 5)
        left = Part.makeCylinder(2.5, 5, App.Vector(10, 15, 0))
        right = Part.makeCylinder(2.5, 5, App.Vector(30, 15, 0))
        obj = _feature(doc, "PlateWithMirroredHoles", plate.cut(left.fuse(right)), HoleDiameter=5)
        return obj, lambda item: item.Shape.isValid() and len(item.Shape.Edges) >= 18
    if operation == "export_stl":
        obj = doc.addObject("Part::Box", "PrintableCube")
        obj.Length = obj.Width = obj.Height = 20
        doc.recompute()
        path = output / "t1_export_stl.stl"
        Mesh.export([obj], str(path))
        return obj, lambda item: path.is_file() and path.stat().st_size > 100
    raise RuntimeError(f"Unknown operation: {operation}")


def run_case(case, output):
    started = time.monotonic()
    doc = App.newDocument(case["id"])
    try:
        obj, validate = _build(case["operation"], doc, output)
        doc.recompute()
        if obj.Shape.isNull() or not obj.Shape.isValid() or not validate(obj):
            raise RuntimeError("The created shape failed its geometry checks.")
        path = output / f"{case['id']}.FCStd"
        doc.saveAs(str(path))
        App.closeDocument(doc.Name)
        reopened = App.openDocument(str(path))
        reopened.recompute()
        restored = reopened.Objects[-1]
        if restored.Shape.isNull() or not restored.Shape.isValid() or not validate(restored):
            raise RuntimeError("The reopened shape failed its geometry checks.")
        return {"id": case["id"], "passed": True, "artifact": str(path), "elapsed_seconds": time.monotonic() - started}
    except Exception as exc:
        return {"id": case["id"], "passed": False, "error": f"{type(exc).__name__}: {exc}", "elapsed_seconds": time.monotonic() - started}
    finally:
        for name in list(App.listDocuments()):
            App.closeDocument(name)


def main():
    if len(sys.argv) < 3:
        raise RuntimeError("The benchmark needs a case file and an output directory.")
    suite = json.loads(Path(sys.argv[-2]).read_text(encoding="utf-8"))
    output = Path(sys.argv[-1])
    output.mkdir(parents=True, exist_ok=True)
    results = [run_case(case, output) for case in suite["cases"]]
    passed = sum(1 for result in results if result["passed"])
    report = {
        "schema": "vibecad-cad-benchmark-result-v1", "version": 1, "tier": 1,
        "executor": "deterministic-offline-capability-baseline",
        "case_count": len(results), "passed": passed, "failed": len(results) - passed,
        "valid_completion_rate": passed / len(results), "results": results,
    }
    report_path = output / "tier1-results.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
