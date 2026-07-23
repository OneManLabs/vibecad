# SPDX-License-Identifier: LGPL-2.1-or-later
"""Isolated save-close-reopen validation for one candidate FCStd document."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from VibeCADAssemblyExplodedView import validate_assembly_configurations
from tool_impl.service.mesh_analyze import analyze_mesh


RESULT_SCHEMA = "vibecad-document-validation-v1"


def validate_open_document(document: Any) -> dict[str, Any]:
    """Validate native shapes and persisted TechDraw dependencies."""
    errors: list[str] = []
    techdraw_checks = 0
    assembly_checks = 0
    exploded_view_checks = 0
    spreadsheet_checks = 0
    material_checks = 0
    mesh_checks = 0
    surface_checks = 0
    cam_checks = 0
    fem_checks = 0
    sheets: dict[str, dict[str, Any]] = {}
    sheets_by_label: dict[str, dict[str, Any]] = {}
    for candidate in list(document.Objects):
        if str(getattr(candidate, "TypeId", "")) != "Spreadsheet::Sheet":
            continue
        aliases: set[str] = set()
        try:
            used_cells = list(candidate.getUsedCells())
        except Exception as exc:
            errors.append(f"{candidate.Name}: spreadsheet cells failed: {exc}")
            used_cells = []
        for cell in used_cells:
            try:
                alias = str(candidate.getAlias(cell) or "")
                candidate.get(cell)
            except Exception as exc:
                errors.append(f"{candidate.Name}.{cell}: spreadsheet cell failed: {exc}")
                continue
            if alias:
                aliases.add(alias)
        entry = {"object": candidate, "aliases": aliases}
        sheets[candidate.Name] = entry
        sheets_by_label[str(candidate.Label)] = entry
        spreadsheet_checks += 1
    for obj in list(document.Objects):
        mesh = getattr(obj, "Mesh", None)
        if mesh is not None:
            mesh_checks += 1
            try:
                mesh_analysis = analyze_mesh(mesh)
            except Exception as exc:
                errors.append(f"{obj.Name}: mesh analysis failed: {exc}")
            else:
                if mesh_analysis.get("nonempty") is not True:
                    errors.append(f"{obj.Name}: mesh is empty")
                if mesh_analysis.get("complete") is not True:
                    unknown = ", ".join(mesh_analysis.get("unknown_checks") or [])
                    errors.append(
                        f"{obj.Name}: mesh defect analysis is incomplete: {unknown}"
                    )
                elif mesh_analysis.get("verdict") != "ready":
                    defects = ", ".join(mesh_analysis.get("known_defects") or [])
                    errors.append(
                        f"{obj.Name}: mesh is not ready for accepted use: {defects}"
                    )
        shape = getattr(obj, "Shape", None)
        if shape is not None and hasattr(shape, "isNull") and not shape.isNull():
            if hasattr(shape, "isValid") and not shape.isValid():
                errors.append(f"{obj.Name}: invalid shape")
        material = getattr(obj, "ShapeMaterial", None)
        material_uuid = str(getattr(material, "UUID", "") or "").strip()
        if material_uuid:
            material_checks += 1
            if not str(getattr(material, "Name", "") or "").strip():
                errors.append(f"{obj.Name}: assigned material has no name")
        type_id = str(getattr(obj, "TypeId", "") or "")
        if all(hasattr(obj, name) for name in ("Model", "Stock", "Tools", "Operations")):
            cam_checks += 1
            _validate_cam_job(obj, errors)
        if type_id == "Fem::FemAnalysis":
            fem_checks += 1
            _validate_fem_analysis(obj, errors)
        if type_id.startswith("TechDraw::DrawViewPart"):
            techdraw_checks += 1
            sources = list(getattr(obj, "Source", []) or [])
            if not sources:
                errors.append(f"{obj.Name}: drawing view has no source")
                continue
            try:
                projection = obj.getProjectedElementDescriptors()
            except Exception as exc:
                errors.append(f"{obj.Name}: drawing projection failed: {exc}")
            else:
                if not list(projection.get("edges") or []):
                    errors.append(f"{obj.Name}: drawing projection has no edges")
        elif type_id == "TechDraw::DrawViewDimension":
            techdraw_checks += 1
            if not list(getattr(obj, "References2D", []) or []):
                errors.append(f"{obj.Name}: drawing dimension has no references")
            try:
                value = float(obj.getRawValue())
            except Exception as exc:
                errors.append(f"{obj.Name}: drawing dimension failed: {exc}")
            else:
                if not math.isfinite(value):
                    errors.append(f"{obj.Name}: drawing dimension is not finite")
        elif type_id == "TechDraw::DrawViewAnnotation":
            techdraw_checks += 1
            if not any(str(line).strip() for line in list(getattr(obj, "Text", []) or [])):
                errors.append(f"{obj.Name}: drawing annotation is empty")
        elif type_id == "Surface::Filling":
            surface_checks += 1
            if not list(getattr(obj, "BoundaryEdges", []) or []):
                errors.append(f"{obj.Name}: surface fill has no boundary links")
            if shape is None or shape.isNull() or not list(shape.Faces):
                errors.append(f"{obj.Name}: surface fill has no face")
        elif type_id == "Surface::Sections":
            surface_checks += 1
            if len(list(getattr(obj, "NSections", []) or [])) < 2:
                errors.append(f"{obj.Name}: surface loft has fewer than two sections")
            if shape is None or shape.isNull() or not list(shape.Faces):
                errors.append(f"{obj.Name}: surface loft has no face")
        elif type_id == "Part::Offset" and str(getattr(obj, "Mode", "")) == "Skin":
            surface_checks += 1
            if getattr(obj, "Source", None) is None:
                errors.append(f"{obj.Name}: thickened surface has no source")
            if not bool(getattr(obj, "Fill", False)):
                errors.append(f"{obj.Name}: thickened surface is not filled")
            if shape is None or shape.isNull() or len(list(shape.Solids)) != 1:
                errors.append(f"{obj.Name}: thickened surface is not one solid")
        elif type_id in {"App::Link", "Assembly::AssemblyLink"}:
            assembly_checks += 1
            if getattr(obj, "LinkedObject", None) is None:
                errors.append(f"{obj.Name}: assembly component has no linked source")
        elif type_id == "Assembly::AssemblyObject":
            assembly_checks += 1
            exploded_view_checks += _validate_assembly(obj, errors)
        for property_path, expression in list(
            getattr(obj, "ExpressionEngine", []) or []
        ):
            direct = _direct_spreadsheet_reference(str(expression or ""))
            if direct is None:
                continue
            spreadsheet_checks += 1
            sheet_key, alias, by_label = direct
            sheet_entry = (
                sheets_by_label.get(sheet_key) if by_label else sheets.get(sheet_key)
            )
            # A direct Object.Property expression is not necessarily a
            # spreadsheet link. CAM and other native modules use the same
            # syntax for internal property dependencies. Validate only a
            # reference that resolves to an actual Spreadsheet::Sheet.
            if sheet_entry is None:
                continue
            if alias not in sheet_entry["aliases"]:
                errors.append(
                    f"{obj.Name}.{property_path}: spreadsheet alias {alias} is missing"
                )
    diagnostics = list(
        getattr(document, "getRecomputeDiagnostics", lambda: [])() or []
    )
    errors.extend(str(item) for item in diagnostics if "error" in str(item).lower())
    return {
        "ok": not errors,
        "errors": errors,
        "techdraw_checks": techdraw_checks,
        "assembly_checks": assembly_checks,
        "exploded_view_checks": exploded_view_checks,
        "spreadsheet_checks": spreadsheet_checks,
        "material_checks": material_checks,
        "mesh_checks": mesh_checks,
        "surface_checks": surface_checks,
        "cam_checks": cam_checks,
        "fem_checks": fem_checks,
    }


def _direct_spreadsheet_reference(expression: str) -> tuple[str, str, bool] | None:
    match = re.fullmatch(
        r"(?:(?P<name>[A-Za-z_][A-Za-z0-9_]*)|<<(?P<label>[^<>]+)>>)\."
        r"(?P<alias>[A-Za-z_][A-Za-z0-9_]*)",
        expression.strip(),
    )
    if match is None:
        return None
    label = match.group("label")
    return (label or match.group("name"), match.group("alias"), label is not None)


def _validate_fem_analysis(analysis: Any, errors: list[str]) -> None:
    """Validate one accepted native FEM analysis after reopen and recompute."""
    members = list(getattr(analysis, "Group", []) or [])
    solvers = [
        member for member in members
        if "Solver" in str(getattr(member, "TypeId", ""))
    ]
    meshes = [member for member in members if hasattr(member, "FemMesh")]
    materials = [
        member for member in members
        if isinstance(getattr(member, "Material", None), dict)
    ]
    fixed = [
        member for member in members
        if _fem_kind(member) == "Fem::ConstraintFixed"
    ]
    loads = [
        member for member in members
        if _fem_kind(member) in {
            "Fem::ConstraintForce", "Fem::ConstraintPressure",
            "Fem::ConstraintSelfWeight",
        }
    ]
    if len(solvers) != 1:
        errors.append(f"{analysis.Name}: FEM analysis must have one solver")
        return
    solver = solvers[0]
    analysis_type = str(getattr(solver, "AnalysisType", "") or "")
    if not analysis_type:
        errors.append(f"{analysis.Name}: FEM solver has no analysis type")
    if len(meshes) != 1:
        errors.append(f"{analysis.Name}: FEM analysis must have one mesh")
    else:
        fem_mesh = meshes[0].FemMesh
        if int(getattr(fem_mesh, "NodeCount", 0) or 0) <= 0:
            errors.append(f"{meshes[0].Name}: FEM mesh has no nodes")
        if int(getattr(fem_mesh, "VolumeCount", 0) or 0) <= 0:
            errors.append(f"{meshes[0].Name}: FEM mesh has no volume elements")
        if str(getattr(meshes[0], "VibeCADOperationKind", "") or "") == "gmsh":
            if str(getattr(meshes[0], "VibeCADOperationState", "")) != "completed":
                errors.append(f"{meshes[0].Name}: Gmsh operation is not complete")
            if not bool(getattr(meshes[0], "VibeCADOperationFinalized", False)):
                errors.append(f"{meshes[0].Name}: Gmsh operation is not finalized")
    if not materials:
        errors.append(f"{analysis.Name}: FEM analysis has no material")
    else:
        for material in materials:
            properties = dict(material.Material)
            for name in ("YoungsModulus", "PoissonRatio"):
                if not str(properties.get(name, "")).strip():
                    errors.append(f"{material.Name}: FEM material lacks {name}")
    if analysis_type in {"static", "frequency", "thermomech", "buckling"} and not fixed:
        errors.append(f"{analysis.Name}: FEM analysis has no fixed support")
    if analysis_type in {"static", "buckling"} and not loads:
        errors.append(f"{analysis.Name}: FEM analysis has no mechanical load")
    for constraint in fixed + loads:
        if _fem_kind(constraint) == "Fem::ConstraintSelfWeight":
            continue
        references = list(getattr(constraint, "References", []) or [])
        if not references or any(
            not entry or entry[0] is None or not list(entry[1] or [])
            for entry in references
        ):
            errors.append(f"{constraint.Name}: FEM constraint has broken references")
    if str(getattr(solver, "VibeCADOperationKind", "") or "") == "calculix":
        if str(getattr(solver, "VibeCADOperationState", "")) != "completed":
            errors.append(f"{solver.Name}: CalculiX operation is not complete")
        if not bool(getattr(solver, "VibeCADOperationFinalized", False)):
            errors.append(f"{solver.Name}: CalculiX operation is not finalized")
        results = _fem_result_objects(analysis, solver)
        if not results and analysis_type != "check":
            errors.append(f"{analysis.Name}: completed FEM solve has no result")
        else:
            from tool_impl.service.fem_solve import _result_summary

            for result in results:
                try:
                    summary = _result_summary(result)
                except Exception as exc:
                    errors.append(f"{result.Name}: FEM result failed: {exc}")
                    continue
                if summary.get("status") != "ok":
                    errors.append(f"{result.Name}: FEM result is invalid")
                if str(getattr(result, "TypeId", "")) == "App::TextDocument":
                    if int((summary.get("text_output") or {}).get("character_count", 0)) <= 0:
                        errors.append(f"{result.Name}: FEM solver output is empty")
                    continue
                numeric = [
                    value for name, value in summary.items()
                    if name in {"vonMises", "DisplacementLengths", "Temperature"}
                ]
                fields = summary.get("fields") or {}
                if not numeric and not fields and analysis_type != "check":
                    errors.append(f"{result.Name}: FEM result has no numeric fields")
                for name, field in fields.items():
                    if int(field.get("tuple_count", 0)) <= 0 or (
                        int(field.get("finite_count", 0))
                        != int(field.get("value_count", 0))
                    ):
                        errors.append(
                            f"{result.Name}: FEM field {name} has non-finite values"
                        )
                for value in numeric:
                    if int(value.get("count", 0)) <= 0 or (
                        int(value.get("finite_count", 0)) != int(value.get("count", 0))
                    ):
                        errors.append(f"{result.Name}: FEM result has non-finite values")


def _fem_kind(obj: Any) -> str:
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    return proxy_type or str(getattr(obj, "TypeId", "") or "")


def _fem_result_objects(analysis: Any, solver: Any) -> list[Any]:
    results: dict[str, Any] = {}
    for obj in list(getattr(analysis, "Group", []) or []) + list(
        getattr(solver, "Results", []) or []
    ):
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id.startswith("Fem::FemPost") or type_id.startswith("Fem::FemResult"):
            results[obj.Name] = obj
        elif type_id == "App::TextDocument":
            results[obj.Name] = obj
    return [results[name] for name in sorted(results)]


def _validate_assembly(assembly: Any, errors: list[str]) -> int:
    group = list(getattr(assembly, "Group", []) or [])
    components = [
        child
        for child in group
        if str(getattr(child, "TypeId", ""))
        in {"App::Link", "Assembly::AssemblyLink"}
    ]
    component_names = {component.Name for component in components}
    joint_groups = [
        child
        for child in group
        if str(getattr(child, "TypeId", "")) == "Assembly::JointGroup"
    ]
    exploded = validate_assembly_configurations(assembly)
    errors.extend(exploded["errors"])
    if not components:
        errors.append(f"{assembly.Name}: assembly has no components")
    if len(joint_groups) != 1:
        errors.append(f"{assembly.Name}: assembly must have one joint group")
        return int(exploded["view_count"])
    joints = list(getattr(joint_groups[0], "Group", []) or [])
    grounded = 0
    constrained = 0
    for joint in joints:
        ground_target = getattr(joint, "ObjectToGround", None)
        if ground_target is not None:
            grounded += 1
            if getattr(ground_target, "Name", None) not in component_names:
                errors.append(f"{joint.Name}: grounded target is not an assembly component")
            continue
        constrained += 1
        for index in (1, 2):
            reference = getattr(joint, f"Reference{index}", None)
            try:
                target = reference[0]
            except (TypeError, IndexError):
                target = None
            if getattr(target, "Name", None) not in component_names:
                errors.append(f"{joint.Name}: Reference{index} is not an assembly component")
    if constrained and not grounded:
        errors.append(f"{assembly.Name}: constrained assembly has no grounded component")
    if constrained and grounded:
        try:
            solver_code = int(assembly.solve(False))
        except Exception as exc:
            errors.append(f"{assembly.Name}: assembly solver failed: {exc}")
        else:
            if solver_code != 0:
                errors.append(
                    f"{assembly.Name}: assembly solver returned {solver_code}"
                )
    return int(exploded["view_count"])


def _validate_cam_job(job: Any, errors: list[str]) -> None:
    models = list(getattr(getattr(job, "Model", None), "Group", []) or [])
    tools = list(getattr(getattr(job, "Tools", None), "Group", []) or [])
    operations = list(getattr(getattr(job, "Operations", None), "Group", []) or [])
    if not models:
        errors.append(f"{job.Name}: CAM job has no model clones")
    stock = getattr(job, "Stock", None)
    stock_shape = getattr(stock, "Shape", None)
    if (
        stock is None or stock_shape is None or stock_shape.isNull()
        or not stock_shape.isValid() or len(list(stock_shape.Solids)) != 1
    ):
        errors.append(f"{job.Name}: CAM job stock is not one valid solid")
    if not tools:
        errors.append(f"{job.Name}: CAM job has no tool controllers")
    for controller in tools:
        if getattr(controller, "Tool", None) is None:
            errors.append(f"{controller.Name}: CAM controller has no tool")
    if not operations:
        errors.append(f"{job.Name}: CAM job has no operations")
    for operation in operations:
        controller = getattr(operation, "ToolController", None)
        if controller not in tools:
            errors.append(f"{operation.Name}: CAM operation controller is outside the job")
        commands = list(
            getattr(getattr(operation, "Path", None), "Commands", []) or []
        )
        if not commands:
            errors.append(f"{operation.Name}: CAM operation path is empty")


def _freecadcmd() -> Path:
    override = os.environ.get("VIBECAD_FREECADCMD", "").strip()
    candidates: list[Path] = [Path(override)] if override else []
    try:
        import FreeCAD as App
        home = Path(App.getHomePath())
        candidates.extend((home / "bin" / "FreeCADCmd", home / "MacOS" / "FreeCADCmd"))
    except Exception:
        pass
    candidates.extend((Path(sys.executable).with_name("FreeCADCmd"), Path(sys.executable)))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("The isolated FreeCAD document validator is not available.")


def validate_saved_document(path: str | Path, *, timeout: float = 120) -> dict[str, Any]:
    candidate = Path(path).resolve()
    if not candidate.is_file():
        raise RuntimeError("The saved candidate document is missing.")
    descriptor, result_name = tempfile.mkstemp(prefix="vibecad-validation-", suffix=".json")
    os.close(descriptor)
    result_path = Path(result_name)
    result_path.unlink(missing_ok=True)
    command = [
        str(_freecadcmd()), "-c",
        "from VibeCADDocumentValidator import worker_main; worker_main()",
        "--pass", str(candidate), str(result_path),
    ]
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False,
        )
        if completed.returncode != 0 or not result_path.is_file():
            output = (completed.stdout or "")[-2000:]
            raise RuntimeError(f"The isolated document validator failed: {output}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("The isolated document validator timed out.") from exc
    finally:
        result_path.unlink(missing_ok=True)
    if not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA:
        raise RuntimeError("The isolated document validation result is invalid.")
    return result


def worker_main() -> None:
    import FreeCAD as App

    source, destination = Path(sys.argv[-2]), Path(sys.argv[-1])
    errors: list[str] = []
    opened = None
    preferences = None
    global_update = None
    page_settings: list[tuple[Any, bool]] = []
    try:
        opened = App.openDocument(str(source))
        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/TechDraw/General"
        )
        global_update = bool(preferences.GetBool("GlobalUpdateDrawings", True))
        preferences.SetBool("GlobalUpdateDrawings", True)
        for obj in list(opened.Objects):
            if str(getattr(obj, "TypeId", "")) == "TechDraw::DrawPage":
                page_settings.append((obj, bool(obj.KeepUpdated)))
                obj.KeepUpdated = True
        opened.recompute()
        validation = validate_open_document(opened)
        errors.extend(validation["errors"])
        result = {
            "schema": RESULT_SCHEMA, "ok": not errors, "errors": errors,
            "check": "isolated_save_close_reopen_recompute",
            "object_count": len(opened.Objects),
            "techdraw_checks": validation["techdraw_checks"],
            "assembly_checks": validation["assembly_checks"],
            "exploded_view_checks": validation["exploded_view_checks"],
            "spreadsheet_checks": validation["spreadsheet_checks"],
            "material_checks": validation["material_checks"],
            "mesh_checks": validation["mesh_checks"],
            "surface_checks": validation["surface_checks"],
            "cam_checks": validation["cam_checks"],
            "fem_checks": validation["fem_checks"],
        }
    except Exception as exc:
        result = {
            "schema": RESULT_SCHEMA, "ok": False,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "check": "isolated_save_close_reopen_recompute", "object_count": 0,
        }
    finally:
        for page, keep_updated in page_settings:
            page.KeepUpdated = keep_updated
        if preferences is not None and global_update is not None:
            preferences.SetBool("GlobalUpdateDrawings", global_update)
        if opened is not None:
            App.closeDocument(opened.Name)
    destination.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
