# SPDX-License-Identifier: LGPL-2.1-or-later
"""Import one human-registered STEP asset as native project geometry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from typing import Any

from VibeCADStepValidator import (
    StepValidationError,
    ValidatedStepCandidate,
    compare_shape_evidence,
    shape_evidence,
    validate_registered_step,
)
from VibeCADTools import tool_failure, unchanged_state
from VibeCADTransactions import run_freecad_transaction


TOOL_NAME = "project.import_step"
RUNNER_HANDLED = True
PROVENANCE_SCHEMA = "vibecad-step-import-provenance-v2"
PROVENANCE_VERSION = 2
_ASSET_ID_PATTERN = "^[0-9a-f]{32}$"

TOOL_SPEC = {
    "name": TOOL_NAME,
    "description": (
        "Import one human-registered STEP solid by its opaque asset id. The file "
        "is validated in an isolated FreeCAD process before native geometry changes."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartWorkbench",
    "requires_document": True,
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "asset_id": {
                "type": "string",
                "pattern": _ASSET_ID_PATTERN,
                "description": (
                    "Opaque id of one STEP file that a human registered in this project."
                ),
            }
        },
        "required": ["asset_id"],
        "additionalProperties": False,
    },
}

FaultInjector = Callable[[str], None]


def _fault(callback: FaultInjector | None, stage: str) -> None:
    if callback is not None:
        callback(stage)


def _enforce_boundary(service: Any) -> None:
    """Check the managed policy and RBAC before asset I/O or CAD mutation."""

    from VibeCADManagedPolicy import load_managed_policy, validate_policy

    validate_policy(load_managed_policy())
    authorizer = getattr(service, "authorize", None)
    if not callable(authorizer):
        raise PermissionError("The STEP import boundary has no permission service.")
    authorizer("design.modify")


def capture_import_step(service: Any, asset_id: str) -> dict[str, Any]:
    """Capture document and project identity without reading the import file."""

    _enforce_boundary(service)
    clean_id = str(asset_id or "").strip().lower()
    if len(clean_id) != 32 or any(char not in "0123456789abcdef" for char in clean_id):
        raise ValueError("The STEP import asset id must be 32 lowercase hex characters.")
    document = service._active_document()
    if document is None:
        raise RuntimeError("No active CAD document is available.")
    scope = service.project_scope_snapshot()
    root = str(scope.get("root") or "").strip()
    project_id = str(scope.get("project_id") or "").strip()
    if not root or not project_id:
        raise RuntimeError("The active CAD document has no durable VibeCAD project.")
    return {
        "asset_id": clean_id,
        "project_root": root,
        "project_id": project_id,
        "document_name": str(getattr(document, "Name", "") or ""),
        "document_uid": str(getattr(document, "Uid", "") or ""),
    }


def validate_captured_step(
    captured: Mapping[str, Any],
    *,
    cancellation_check: Callable[[], bool] | None = None,
    validator: Callable[..., Any] = validate_registered_step,
) -> Any:
    """Run the isolated validation phase outside the document thread."""

    candidate = validator(
        str(captured["project_root"]),
        str(captured["project_id"]),
        str(captured["asset_id"]),
        cancellation_check=cancellation_check,
    )
    if type(candidate) is ValidatedStepCandidate:
        ValidatedStepCandidate.prepare_detached_shape(
            candidate,
            cancellation_check=cancellation_check,
        )
    return candidate


def _candidate_evidence(candidate: Any) -> dict[str, Any] | None:
    """Return path-free evidence only for an owned validated candidate."""

    if type(candidate) is not ValidatedStepCandidate:
        return None
    try:
        evidence = ValidatedStepCandidate.revalidated_evidence(candidate)
    except (StepValidationError, TypeError, ValueError):
        return None
    if not isinstance(evidence, Mapping):
        return None
    return dict(evidence)


def _cleanup_candidate(candidate: Any, *, strict: bool = False) -> None:
    if type(candidate) is ValidatedStepCandidate:
        cleaner = lambda: ValidatedStepCandidate.cleanup(candidate)
    elif isinstance(candidate, ValidatedStepCandidate):
        # Never invoke methods supplied by a forged candidate subclass.
        cleaner = None
    else:
        cleaner = getattr(candidate, "cleanup", None)
    if callable(cleaner):
        try:
            cleaner()
        except Exception:
            if strict:
                raise RuntimeError(
                    "The private STEP validation artifacts could not be removed."
                )


def _active_identity(service: Any) -> tuple[str, str, str, str]:
    document = service._active_document()
    if document is None:
        raise RuntimeError("No active CAD document is available.")
    scope = service.project_scope_snapshot()
    return (
        str(getattr(document, "Name", "") or ""),
        str(getattr(document, "Uid", "") or ""),
        str(scope.get("root") or ""),
        str(scope.get("project_id") or ""),
    )


def _native_brep_identity(shape: Any) -> dict[str, Any]:
    """Return an exact in-memory identity for one native BREP shape."""

    raw = shape.exportBrepToString()
    serialized = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    if not serialized:
        raise RuntimeError("The native BREP serializer returned no data.")
    return {
        "sha256": hashlib.sha256(serialized).hexdigest(),
        "size_bytes": len(serialized),
    }


def _canonical_native_brep_identity(
    document: Any, shape: Any, *, probe_name: Callable[[str | None], None]
) -> dict[str, Any]:
    """Normalize one loaded BREP through an independent native shape property."""

    probe = document.addObject("Part::Feature", "VibeCADImportIdentityProbe")
    probe_name(str(probe.Name))
    try:
        probe.Shape = shape.copy()
        document.recompute([probe])
        return _native_brep_identity(probe.Shape)
    finally:
        if document.getObject(probe.Name) is not None:
            document.removeObject(probe.Name)
        probe_name(None)


def _add_provenance_properties(
    feature: Any,
    *,
    asset: Mapping[str, Any],
    validation: Mapping[str, Any],
    native_identity: Mapping[str, Any],
) -> dict[str, Any]:
    properties = {
        "VibeCADImportSchema": ("App::PropertyString", PROVENANCE_SCHEMA),
        "VibeCADImportVersion": ("App::PropertyInteger", PROVENANCE_VERSION),
        "VibeCADImportAssetId": ("App::PropertyString", asset["asset_id"]),
        "VibeCADImportAssetSHA256": ("App::PropertyString", asset["sha256"]),
        "VibeCADImportAssetFormat": ("App::PropertyString", "step"),
        "VibeCADImportAssetByteSize": ("App::PropertyInteger", asset["size_bytes"]),
        "VibeCADImportProjectId": ("App::PropertyString", asset["project_id"]),
        "VibeCADImportValidationSHA256": (
            "App::PropertyString",
            validation["evidence_sha256"],
        ),
        "VibeCADImportBREPSHA256": (
            "App::PropertyString",
            validation["brep_sha256"],
        ),
        "VibeCADImportBREPByteSize": (
            "App::PropertyInteger",
            validation["brep_size_bytes"],
        ),
        "VibeCADImportNativeShapeSHA256": (
            "App::PropertyString",
            native_identity["sha256"],
        ),
        "VibeCADImportNativeShapeByteSize": (
            "App::PropertyInteger",
            native_identity["size_bytes"],
        ),
        "VibeCADImportValidation": (
            "App::PropertyString",
            json.dumps(validation, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        ),
    }
    for name, (property_type, value) in properties.items():
        feature.addProperty(property_type, name, "VibeCAD Import")
        setattr(feature, name, value)
        try:
            feature.setEditorMode(name, 1)
        except Exception:
            pass
    return {
        "schema": PROVENANCE_SCHEMA,
        "version": PROVENANCE_VERSION,
        "asset_id": asset["asset_id"],
        "asset_sha256": asset["sha256"],
        "format": "step",
        "size_bytes": asset["size_bytes"],
        "project_id": asset["project_id"],
        "validation_sha256": validation["evidence_sha256"],
        "brep_sha256": validation["brep_sha256"],
        "brep_size_bytes": validation["brep_size_bytes"],
        "native_shape_sha256": native_identity["sha256"],
        "native_shape_size_bytes": native_identity["size_bytes"],
    }


def publish_validated_step(
    service: Any,
    captured: Mapping[str, Any],
    validation: ValidatedStepCandidate,
    *,
    fault: FaultInjector | None = None,
) -> dict[str, Any]:
    """Publish only the exact BREP that the isolated worker validated."""

    evidence = _candidate_evidence(validation)
    if evidence is None:
        _cleanup_candidate(validation)
        return tool_failure(
            TOOL_NAME,
            "STEP_IMPORT_CANDIDATE_INVALID",
            "precondition",
            "The validated STEP candidate is missing its private BREP binding.",
            requested={"asset_id": captured.get("asset_id")},
            state_change=unchanged_state(),
        )

    try:
        _enforce_boundary(service)
        expected_identity = (
            str(captured.get("document_name") or ""),
            str(captured.get("document_uid") or ""),
            str(captured.get("project_root") or ""),
            str(captured.get("project_id") or ""),
        )
        if _active_identity(service) != expected_identity:
            return tool_failure(
                TOOL_NAME,
                "STEP_IMPORT_SCOPE_CHANGED",
                "precondition",
                "The active document or project changed after STEP validation.",
                requested={"asset_id": captured.get("asset_id")},
                state_change=unchanged_state(),
            )
        if (
            evidence.get("ok") is not True
            or evidence.get("asset_id") != captured.get("asset_id")
            or evidence.get("project_id") != captured.get("project_id")
            or evidence.get("format") != "step"
            or not isinstance(evidence.get("asset_sha256"), str)
            or not isinstance(evidence.get("size_bytes"), int)
            or not isinstance(evidence.get("brep_sha256"), str)
            or not isinstance(evidence.get("brep_size_bytes"), int)
        ):
            return tool_failure(
                TOOL_NAME,
                "STEP_IMPORT_VALIDATION_MISMATCH",
                "postcondition",
                "The STEP validation evidence does not match the registered asset.",
                requested={"asset_id": captured.get("asset_id")},
                state_change=unchanged_state(),
            )
        asset = {
            "asset_id": evidence["asset_id"],
            "stored_name": f"{evidence['asset_id']}.step",
            "format": "step",
            "size_bytes": evidence["size_bytes"],
            "sha256": evidence["asset_sha256"],
            "project_id": evidence["project_id"],
        }
        publication_document = service._active_document()
        if publication_document is None:
            return tool_failure(
                TOOL_NAME,
                "STEP_IMPORT_SCOPE_CHANGED",
                "precondition",
                "The active document changed after STEP validation.",
                requested={"asset_id": captured.get("asset_id")},
                state_change=unchanged_state(),
            )

        created_feature_name: str | None = None
        transient_probe_name: str | None = None
        expected_native_brep: dict[str, Any] | None = None
        detached_native_brep: dict[str, Any] | None = None

        def remember_probe_name(value: str | None) -> None:
            nonlocal transient_probe_name
            transient_probe_name = value

        def create() -> dict[str, Any]:
            nonlocal created_feature_name, detached_native_brep
            nonlocal expected_native_brep, transient_probe_name
            import FreeCAD as App

            document = App.ActiveDocument
            if (
                document is None
                or document is not publication_document
                or document is not service._active_document()
            ):
                raise RuntimeError(
                    "The active CAD document changed before STEP publication."
                )
            try:
                shape, detached_native_brep = (
                    ValidatedStepCandidate.consume_prepared_shape(
                    validation
                    )
                )
            except Exception as exc:
                raise RuntimeError(
                    "The validated private BREP could not be published."
                ) from exc
            _fault(fault, "after_live_shape_read")
            observed = shape_evidence(shape)
            comparison = compare_shape_evidence(evidence["shape"], observed)
            if not comparison["ok"]:
                raise RuntimeError(
                    "The private BREP geometry differs from isolated validation."
                )
            if _native_brep_identity(shape) != detached_native_brep:
                raise RuntimeError(
                    "The detached native shape changed before publication."
                )
            expected_native_brep = _canonical_native_brep_identity(
                document,
                shape,
                probe_name=remember_probe_name,
            )
            feature = document.addObject("Part::Feature", "ImportedSTEP")
            created_feature_name = str(feature.Name)
            feature.Label = f"Imported STEP {asset['asset_id'][:8]}"
            feature.Shape = shape.copy()
            document.recompute([feature])
            if _native_brep_identity(feature.Shape) != expected_native_brep:
                raise RuntimeError(
                    "The assigned native shape differs from the validated BREP."
                )
            _fault(fault, "after_feature_create")
            provenance = _add_provenance_properties(
                feature,
                asset=asset,
                validation=evidence,
                native_identity=expected_native_brep,
            )
            _fault(fault, "after_provenance_write")
            return {
                "feature": feature.Name,
                "feature_label": feature.Label,
                "feature_type": feature.TypeId,
                "asset": {
                    "asset_id": asset["asset_id"],
                    "stored_name": asset["stored_name"],
                    "format": asset["format"],
                    "size_bytes": asset["size_bytes"],
                    "sha256": asset["sha256"],
                    "project_id": asset["project_id"],
                },
                "provenance": provenance,
                "shape": observed,
                "isolated_validation": dict(evidence),
                "worker_live_comparison": comparison,
                "native_brep_identity": dict(expected_native_brep),
            }

        def verify(result: Mapping[str, Any]) -> dict[str, Any]:
            _fault(fault, "before_verifier_success")
            scope_unchanged = bool(
                service._active_document() is publication_document
                and _active_identity(service) == expected_identity
            )
            feature = publication_document.getObject(str(result.get("feature") or ""))
            current = shape_evidence(feature.Shape) if feature is not None else None
            current_native_brep = (
                _native_brep_identity(feature.Shape) if feature is not None else None
            )
            comparison = (
                compare_shape_evidence(evidence["shape"], current)
                if current is not None
                else {"ok": False, "checks": []}
            )
            properties = list(getattr(feature, "PropertiesList", []) or [])
            property_checks = feature is not None and all(
                name in properties
                for name in (
                    "VibeCADImportAssetId",
                    "VibeCADImportAssetSHA256",
                    "VibeCADImportProjectId",
                    "VibeCADImportValidationSHA256",
                    "VibeCADImportBREPSHA256",
                    "VibeCADImportBREPByteSize",
                    "VibeCADImportNativeShapeSHA256",
                    "VibeCADImportNativeShapeByteSize",
                )
            )
            exact_binding = bool(
                feature is not None
                and getattr(feature, "VibeCADImportBREPSHA256", "")
                == evidence["brep_sha256"]
                and getattr(feature, "VibeCADImportBREPByteSize", -1)
                == evidence["brep_size_bytes"]
            )
            exact_native_shape = bool(
                expected_native_brep is not None
                and current_native_brep == expected_native_brep
                and feature is not None
                and getattr(feature, "VibeCADImportNativeShapeSHA256", "")
                == expected_native_brep["sha256"]
                and getattr(feature, "VibeCADImportNativeShapeByteSize", -1)
                == expected_native_brep["size_bytes"]
            )
            checks = [
                {
                    "name": "document_scope_is_unchanged",
                    "ok": scope_unchanged,
                    "actual": {"unchanged": scope_unchanged},
                },
                {
                    "name": "isolated_and_published_geometry_match",
                    "ok": bool(comparison["ok"]),
                    "actual": comparison,
                },
                {
                    "name": "source_provenance_is_complete",
                    "ok": bool(property_checks),
                    "actual": result.get("provenance"),
                },
                {
                    "name": "published_brep_matches_worker_binding",
                    "ok": exact_binding,
                    "actual": {
                        "brep_sha256": evidence["brep_sha256"],
                        "brep_size_bytes": evidence["brep_size_bytes"],
                    },
                },
                {
                    "name": "native_shape_matches_published_brep_exactly",
                    "ok": exact_native_shape,
                    "actual": current_native_brep,
                },
            ]
            return {"ok": all(item["ok"] for item in checks), "checks": checks}

        def rollback() -> None:
            if created_feature_name and publication_document.getObject(
                created_feature_name
            ) is not None:
                publication_document.removeObject(created_feature_name)
            if transient_probe_name and publication_document.getObject(
                transient_probe_name
            ) is not None:
                publication_document.removeObject(transient_probe_name)
            publication_document.recompute()

        transaction = run_freecad_transaction(
            f"Import registered STEP {asset['asset_id'][:8]}",
            create,
            verifier=verify,
            rollback_handler=rollback,
        )
        result = dict(transaction.get("result") or {})
        response = {
            "ok": bool(transaction.get("ok")),
            "operation": "import_step",
            "mutation": result,
            "validation": dict(evidence),
            "verification": transaction.get("verification") or {},
            "document_delta": transaction.get("document_delta") or {},
            "candidate_document_delta": transaction.get("candidate_document_delta") or {},
            "native_diagnostics": transaction.get("native_diagnostics") or {},
            "state_change": transaction.get("state_change") or unchanged_state(),
            "rollback_attempted": bool(transaction.get("rollback_attempted")),
            "rollback_succeeded": bool(transaction.get("rollback_succeeded")),
        }
        if not response["ok"]:
            response.update(
                failure_code=transaction.get("failure_code") or "STEP_IMPORT_FAILED",
                failure_stage=transaction.get("failure_stage") or "native_call",
                error=(
                    "The native STEP import failed and the prior document state "
                    "was restored."
                    if response["rollback_succeeded"]
                    else "The native STEP import failed."
                ),
            )
        return response
    finally:
        _cleanup_candidate(validation)


def run(
    service: Any,
    asset_id: str,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    validator: Callable[..., Any] = validate_registered_step,
    fault: FaultInjector | None = None,
) -> dict[str, Any]:
    """Reject direct mutation outside the session acceptance boundary."""

    del service, cancellation_check, validator, fault
    return tool_failure(
        TOOL_NAME,
        "STEP_IMPORT_SESSION_REQUIRED",
        "precondition",
        "STEP import must run through the provider session acceptance boundary.",
        requested={"asset_id": str(asset_id)},
        state_change=unchanged_state(),
    )


__all__ = [
    "TOOL_SPEC",
    "capture_import_step",
    "publish_validated_step",
    "run",
    "validate_captured_step",
]
