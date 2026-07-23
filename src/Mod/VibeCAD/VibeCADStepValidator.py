# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bounded FreeCADCmd validation for one registered STEP import asset."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any
import uuid

from VibeCADImportAssets import copy_registered_import_asset, resolve_import_asset
from VibeCADScriptedProcess import run_process
from VibeCADStepSandbox import (
    StepWorkerSandboxUnavailable,
    prepare_step_worker_sandbox,
)


STEP_VALIDATION_REQUEST_SCHEMA = "vibecad-step-validation-request-v2"
STEP_VALIDATION_RESULT_SCHEMA = "vibecad-step-validation-result-v2"
STEP_VALIDATION_VERSION = 2
DEFAULT_STEP_VALIDATION_TIMEOUT_SECONDS = 30.0
DEFAULT_STEP_VALIDATION_MEMORY_BYTES = 1024 * 1024 * 1024
MAX_STEP_VALIDATION_TIMEOUT_SECONDS = 300.0
MAX_STEP_VALIDATION_MEMORY_BYTES = 16 * 1024 * 1024 * 1024
MAX_STEP_VALIDATION_RESULT_BYTES = 256 * 1024
MAX_VALIDATED_BREP_BYTES = 512 * 1024 * 1024

_TOPOLOGY_FIELDS = ("solids", "shells", "faces", "edges", "vertices")
_BOUND_FIELDS = (
    "min_x",
    "min_y",
    "min_z",
    "max_x",
    "max_y",
    "max_z",
    "size_x",
    "size_y",
    "size_z",
)
_CANDIDATE_CONSTRUCTOR_SEAL = object()


class StepValidationError(RuntimeError):
    """A stable, provider-safe isolated validation failure."""

    def __init__(self, code: str, message: str, evidence: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = str(code)
        self.evidence = dict(evidence or {})


class ValidatedStepCandidate(Mapping[str, Any]):
    """Own one provider-safe result and its private worker BREP."""

    def __init__(
        self,
        evidence: Mapping[str, Any],
        temporary: tempfile.TemporaryDirectory[str],
        artifact_path: Path,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _CANDIDATE_CONSTRUCTOR_SEAL:
            raise TypeError("Validated STEP candidates are created by the validator only.")
        self._evidence_json = _canonical_json(dict(evidence))
        self._temporary = temporary
        self._artifact_path = artifact_path
        self._cleaned = False
        self._temporary_cleaned = False
        self._sealed_descriptor: int | None = None
        self._sealed_metadata: os.stat_result | None = None
        self._descriptor_consumed = False
        self._detached_shape: Any | None = None
        self._detached_native_identity: dict[str, Any] | None = None
        self._shape_consumed = False

    def __getitem__(self, key: str) -> Any:
        return ValidatedStepCandidate.provider_evidence(self)[key]

    def __iter__(self) -> Iterator[str]:
        return iter(ValidatedStepCandidate.provider_evidence(self))

    def __len__(self) -> int:
        return len(ValidatedStepCandidate.provider_evidence(self))

    def __repr__(self) -> str:
        return (
            "ValidatedStepCandidate("
            f"asset_id={ValidatedStepCandidate.provider_evidence(self).get('asset_id')!r}, "
            f"cleaned={self._cleaned!r})"
        )

    def provider_evidence(self) -> dict[str, Any]:
        """Return the path-free validation record."""

        return json.loads(self._evidence_json.decode("utf-8"))

    def revalidated_evidence(self) -> dict[str, Any]:
        """Revalidate the complete content-bound evidence before publication."""

        raw = ValidatedStepCandidate.provider_evidence(self)
        asset_id = raw.get("asset_id")
        asset_sha256 = raw.get("asset_sha256")
        size_bytes = raw.get("size_bytes")
        project_id = raw.get("project_id")
        if (
            not isinstance(asset_id, str)
            or not isinstance(asset_sha256, str)
            or isinstance(size_bytes, bool)
            or type(size_bytes) is not int
            or not isinstance(project_id, str)
        ):
            raise StepValidationError(
                "STEP_VALIDATION_RESULT_INVALID",
                "The validated STEP candidate identity is invalid.",
            )
        return _validate_result(
            raw,
            project_id=project_id,
            asset={
                "asset_id": asset_id,
                "sha256": asset_sha256,
                "size_bytes": size_bytes,
            },
        )

    def artifact_available(self) -> bool:
        """Return whether the private worker artifact is available to seal."""

        return bool(
            not self._cleaned
            and self._sealed_descriptor is None
            and not self._descriptor_consumed
            and self._artifact_path.is_file()
        )

    def seal_for_publication(self) -> None:
        """Authenticate one BREP handle for immediate detached parsing."""

        if self._sealed_descriptor is not None or self._detached_shape is not None:
            return
        if self._cleaned or self._descriptor_consumed:
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_CLEANED",
                "The private STEP validation artifact is no longer available.",
            )
        evidence = ValidatedStepCandidate.provider_evidence(self)
        descriptor = -1
        try:
            descriptor, metadata = _open_verified_private_artifact(
                self._artifact_path,
                expected_sha256=str(evidence["brep_sha256"]),
                expected_size=int(evidence["brep_size_bytes"]),
            )
        except StepValidationError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except Exception as exc:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                if not self._temporary_cleaned:
                    self._temporary.cleanup()
                    self._temporary_cleaned = True
            except Exception:
                pass
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_TAMPERED",
                "The private STEP validation artifact failed identity checks.",
            ) from exc
        self._sealed_descriptor = descriptor
        self._sealed_metadata = metadata

    @contextmanager
    def consume_verified_brep_descriptor(
        self,
    ) -> Iterator[tuple[int, os.stat_result]]:
        """Yield the authenticated private BREP handle once, then close it."""

        if self._descriptor_consumed:
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_CONSUMED",
                "The private STEP validation artifact was already consumed.",
            )
        if self._sealed_descriptor is None:
            ValidatedStepCandidate.seal_for_publication(self)
        descriptor = self._sealed_descriptor
        metadata = self._sealed_metadata
        if descriptor is None or metadata is None or self._descriptor_consumed:
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_CONSUMED",
                "The private STEP validation artifact was already consumed.",
            )
        self._sealed_descriptor = None
        self._sealed_metadata = None
        self._descriptor_consumed = True
        try:
            yield descriptor, metadata
        finally:
            os.close(descriptor)

    def prepare_detached_shape(
        self,
        *,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> None:
        """Parse and verify a detached native shape outside the document thread."""

        if self._detached_shape is not None:
            return
        if self._shape_consumed or self._cleaned:
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_CONSUMED",
                "The validated STEP geometry was already consumed.",
            )
        if cancellation_check is not None and cancellation_check():
            ValidatedStepCandidate.cleanup(self)
            raise StepValidationError(
                "STEP_VALIDATION_CANCELLED", "The STEP validation was cancelled."
            )
        evidence = ValidatedStepCandidate.revalidated_evidence(self)
        try:
            with ValidatedStepCandidate.consume_verified_brep_descriptor(
                self
            ) as (descriptor, metadata):
                _verify_parser_input_binding(
                    self._artifact_path,
                    descriptor,
                    metadata,
                    expected_sha256=str(evidence["brep_sha256"]),
                    expected_size=int(evidence["brep_size_bytes"]),
                )
                _verify_open_private_descriptor(
                    descriptor,
                    metadata,
                    expected_sha256=str(evidence["brep_sha256"]),
                    expected_size=int(evidence["brep_size_bytes"]),
                )
                import Part

                shape = Part.Shape()
                shape.importBrep(str(self._artifact_path))
                _verify_parser_input_binding(
                    self._artifact_path,
                    descriptor,
                    metadata,
                    expected_sha256=str(evidence["brep_sha256"]),
                    expected_size=int(evidence["brep_size_bytes"]),
                )
                _verify_open_private_descriptor(
                    descriptor,
                    metadata,
                    expected_sha256=str(evidence["brep_sha256"]),
                    expected_size=int(evidence["brep_size_bytes"]),
                )
                observed = shape_evidence(shape)
                comparison = compare_shape_evidence(evidence["shape"], observed)
                if not comparison["ok"]:
                    raise ValueError(
                        "The detached BREP geometry differs from isolated validation."
                    )
                native_identity = _canonical_native_shape_identity(shape)
                self._artifact_path.unlink()
                self._temporary.cleanup()
                self._temporary_cleaned = True
                unlinked_metadata = os.fstat(descriptor)
                _verify_open_private_descriptor(
                    descriptor,
                    unlinked_metadata,
                    expected_sha256=str(evidence["brep_sha256"]),
                    expected_size=int(evidence["brep_size_bytes"]),
                    unlinked=True,
                )
        except StepValidationError:
            ValidatedStepCandidate.cleanup(self)
            raise
        except Exception as exc:
            ValidatedStepCandidate.cleanup(self)
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_TAMPERED",
                "The private STEP validation artifact could not be prepared.",
            ) from exc
        if cancellation_check is not None and cancellation_check():
            ValidatedStepCandidate.cleanup(self)
            raise StepValidationError(
                "STEP_VALIDATION_CANCELLED", "The STEP validation was cancelled."
            )
        self._detached_shape = shape
        self._detached_native_identity = native_identity

    def consume_prepared_shape(self) -> tuple[Any, dict[str, Any]]:
        """Return one detached verified native shape exactly once."""

        if (
            self._detached_shape is None
            or self._detached_native_identity is None
            or self._shape_consumed
        ):
            raise StepValidationError(
                "STEP_VALIDATION_SHAPE_NOT_PREPARED",
                "The validated STEP geometry is not ready for publication.",
            )
        shape = self._detached_shape
        identity = dict(self._detached_native_identity)
        self._detached_shape = None
        self._detached_native_identity = None
        self._shape_consumed = True
        return shape, identity

    @contextmanager
    def verified_brep_copy(self) -> Iterator[Path]:
        """Yield a new exact private copy of the worker-generated BREP."""

        if self._cleaned:
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_CLEANED",
                "The private STEP validation artifact is no longer available.",
            )
        try:
            evidence = ValidatedStepCandidate.provider_evidence(self)
            _verify_private_artifact(
                self._artifact_path,
                expected_sha256=str(evidence["brep_sha256"]),
                expected_size=int(evidence["brep_size_bytes"]),
            )
            with tempfile.TemporaryDirectory(prefix="vibecad-brep-publish-") as name:
                copy = Path(name) / "validated.brep"
                _copy_verified_source(
                    self._artifact_path,
                    copy,
                    expected_sha256=str(evidence["brep_sha256"]),
                    expected_size=int(evidence["brep_size_bytes"]),
                )
                yield copy
        except StepValidationError:
            raise
        except Exception as exc:
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_TAMPERED",
                "The private STEP validation artifact failed identity checks.",
            ) from exc

    def cleanup(self) -> None:
        """Remove the request, result, STEP copy, and generated BREP."""

        descriptor = self._sealed_descriptor
        self._sealed_descriptor = None
        self._sealed_metadata = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._detached_shape = None
        self._detached_native_identity = None
        if self._cleaned:
            return
        try:
            if not self._temporary_cleaned:
                self._temporary.cleanup()
                self._temporary_cleaned = True
        except Exception as exc:
            raise StepValidationError(
                "STEP_VALIDATION_CLEANUP_FAILED",
                "The private STEP validation artifacts could not be removed.",
            ) from exc
        self._cleaned = True

    def __enter__(self) -> "ValidatedStepCandidate":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        ValidatedStepCandidate.cleanup(self)

    def __del__(self) -> None:
        try:
            ValidatedStepCandidate.cleanup(self)
        except Exception:
            pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _content_digest(value: Mapping[str, Any], digest_field: str) -> str:
    content = {key: item for key, item in value.items() if key != digest_field}
    return hashlib.sha256(_canonical_json(content)).hexdigest()


def _open_regular_read_only(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(
            "The registered STEP source is missing or unsafe."
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("The registered STEP source is not a regular file.")
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor, _metadata = _open_regular_read_only(path)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _descriptor_sha256(descriptor: int) -> tuple[str, int]:
    """Hash one already-open file without changing its final stream position."""

    digest = hashlib.sha256()
    copied = 0
    position = os.lseek(descriptor, 0, os.SEEK_CUR)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            copied += len(block)
    finally:
        os.lseek(descriptor, position, os.SEEK_SET)
    return digest.hexdigest(), copied


def _same_file_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return bool(
        stat.S_ISREG(second.st_mode)
        and second.st_dev == first.st_dev
        and second.st_ino == first.st_ino
        and second.st_size == first.st_size
        and second.st_mtime_ns == first.st_mtime_ns
        and second.st_ctime_ns == first.st_ctime_ns
    )


def _verify_parser_input_binding(
    path: Path,
    descriptor: int,
    baseline: os.stat_result,
    *,
    expected_sha256: str,
    expected_size: int,
) -> None:
    """Verify that a parser path still names one unchanged open regular file."""

    handle_metadata = os.fstat(descriptor)
    try:
        path_metadata = os.lstat(path)
    except OSError as exc:
        raise ValueError("The isolated STEP parser input changed.") from exc
    if (
        stat.S_ISLNK(path_metadata.st_mode)
        or not _same_file_identity(baseline, handle_metadata)
        or not _same_file_identity(baseline, path_metadata)
    ):
        raise ValueError("The isolated STEP parser input changed.")
    digest, size = _descriptor_sha256(descriptor)
    after_hash = os.fstat(descriptor)
    if (
        not _same_file_identity(baseline, after_hash)
        or size != expected_size
        or digest != expected_sha256
    ):
        raise ValueError("The isolated STEP parser input changed.")


def _canonical_native_shape_identity(shape: Any) -> dict[str, Any]:
    """Return a bounded digest of the native BREP serialization for one shape."""

    serialized = shape.exportBrepToString()
    if isinstance(serialized, str):
        content = serialized.encode("utf-8")
    elif isinstance(serialized, bytes):
        content = serialized
    else:
        raise ValueError("The native shape serializer returned invalid content.")
    if not 1 <= len(content) <= MAX_VALIDATED_BREP_BYTES:
        raise ValueError("The native shape serialization size is invalid.")
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _copy_verified_source(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> str:
    """Copy exact registered bytes without following a source or target link."""

    source_descriptor, source_metadata = _open_regular_read_only(source)
    target_descriptor = -1
    target_created = False
    digest = hashlib.sha256()
    copied = 0
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        if source_metadata.st_size != expected_size:
            raise ValueError("The registered STEP byte size does not match the request.")
        target_descriptor = os.open(destination, flags, 0o600)
        target_created = True
        with (
            os.fdopen(source_descriptor, "rb", closefd=False) as source_stream,
            os.fdopen(target_descriptor, "wb", closefd=False) as target_stream,
        ):
            for block in iter(lambda: source_stream.read(1024 * 1024), b""):
                digest.update(block)
                copied += len(block)
                target_stream.write(block)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        observed = digest.hexdigest()
        if copied != expected_size or observed != expected_sha256:
            raise ValueError("The registered STEP bytes do not match the request.")
        return observed
    finally:
        os.close(source_descriptor)
        if target_descriptor >= 0:
            os.close(target_descriptor)
        if target_created and (
            copied != expected_size or digest.hexdigest() != expected_sha256
        ):
            destination.unlink(missing_ok=True)


def _verify_private_artifact(
    path: Path, *, expected_sha256: str, expected_size: int
) -> None:
    """Verify one private regular file without following its final component."""

    _read_private_artifact(
        path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    )


def _verify_open_private_descriptor(
    descriptor: int,
    baseline: os.stat_result,
    *,
    expected_sha256: str,
    expected_size: int,
    unlinked: bool = False,
) -> None:
    """Verify one unchanged private BREP through its stable open handle."""

    def identity_matches(observed: os.stat_result) -> bool:
        if unlinked:
            return bool(
                stat.S_ISREG(observed.st_mode)
                and observed.st_dev == baseline.st_dev
                and observed.st_ino == baseline.st_ino
                and observed.st_size == baseline.st_size
                and observed.st_nlink == 0
            )
        return _same_file_identity(baseline, observed)

    before = os.fstat(descriptor)
    if (
        not identity_matches(before)
        or before.st_size != expected_size
        or (os.name != "nt" and before.st_mode & 0o077)
    ):
        raise ValueError("The private validation artifact identity is invalid.")
    digest, size = _descriptor_sha256(descriptor)
    after = os.fstat(descriptor)
    if (
        not identity_matches(after)
        or size != expected_size
        or digest != expected_sha256
    ):
        raise ValueError("The private validation artifact identity is invalid.")


def _open_verified_private_artifact(
    path: Path, *, expected_sha256: str, expected_size: int
) -> tuple[int, os.stat_result]:
    """Open and authenticate one private BREP without following its pathname."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("The private validation artifact cannot be opened safely.")
    descriptor, metadata = _open_regular_read_only(path)
    try:
        _verify_open_private_descriptor(
            descriptor,
            metadata,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _read_private_artifact(
    path: Path, *, expected_sha256: str, expected_size: int
) -> bytes:
    """Read one exact private artifact through one stable no-follow handle."""

    descriptor, metadata = _open_regular_read_only(path)
    digest = hashlib.sha256()
    blocks: list[bytes] = []
    copied = 0
    try:
        if metadata.st_size != expected_size:
            raise ValueError("The private validation artifact has the wrong size.")
        if os.name != "nt" and metadata.st_mode & 0o077:
            raise ValueError("The private validation artifact permissions are unsafe.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                copied += len(block)
                digest.update(block)
                blocks.append(block)
        after = os.fstat(descriptor)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or after.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise ValueError("The private validation artifact changed while read.")
    finally:
        os.close(descriptor)
    if copied != expected_size or digest.hexdigest() != expected_sha256:
        raise ValueError("The private validation artifact identity is invalid.")
    return b"".join(blocks)


def _normalized_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("The STEP validation timeout must be a finite number.")
    timeout = float(value)
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or timeout > MAX_STEP_VALIDATION_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "The STEP validation timeout is outside the supported range."
        )
    return timeout


def _normalized_memory_limit(value: Any) -> int:
    if (
        isinstance(value, bool)
        or type(value) is not int
        or value <= 0
        or value > MAX_STEP_VALIDATION_MEMORY_BYTES
    ):
        raise ValueError(
            "The STEP validation memory limit must be a supported positive integer."
        )
    return value


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} is not numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} is not finite.")
    return result


def shape_evidence(shape: Any) -> dict[str, Any]:
    """Return deterministic validity, topology, bounds, and volume evidence."""

    null = bool(shape.isNull())
    valid = bool(not null and shape.isValid())
    topology = {
        "solids": len(list(shape.Solids)) if not null else 0,
        "shells": len(list(shape.Shells)) if not null else 0,
        "faces": len(list(shape.Faces)) if not null else 0,
        "edges": len(list(shape.Edges)) if not null else 0,
        "vertices": len(list(shape.Vertexes)) if not null else 0,
    }
    bounds: dict[str, float] | None = None
    volume = 0.0
    if not null:
        box = shape.BoundBox
        bounds = {
            "min_x": float(box.XMin),
            "min_y": float(box.YMin),
            "min_z": float(box.ZMin),
            "max_x": float(box.XMax),
            "max_y": float(box.YMax),
            "max_z": float(box.ZMax),
            "size_x": float(box.XLength),
            "size_y": float(box.YLength),
            "size_z": float(box.ZLength),
        }
        volume = float(shape.Volume)
    return {
        "shape_type": str(getattr(shape, "ShapeType", "") or ""),
        "null": null,
        "valid": valid,
        "topology": topology,
        "bounds_mm": bounds,
        "volume_mm3": volume,
    }


def _validate_shape_evidence(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("shape evidence is not an object.")
    expected = {
        "shape_type",
        "null",
        "valid",
        "topology",
        "bounds_mm",
        "volume_mm3",
    }
    if set(raw) != expected:
        raise ValueError("shape evidence has invalid fields.")
    if raw.get("shape_type") != "Solid":
        raise ValueError("shape type is not Solid.")
    if raw.get("null") is not False or raw.get("valid") is not True:
        raise ValueError("the imported STEP shape is null or invalid.")
    topology = raw.get("topology")
    if not isinstance(topology, Mapping) or set(topology) != set(_TOPOLOGY_FIELDS):
        raise ValueError("topology evidence has invalid fields.")
    clean_topology: dict[str, int] = {}
    for field in _TOPOLOGY_FIELDS:
        value = topology.get(field)
        if isinstance(value, bool) or type(value) is not int or value < 0:
            raise ValueError(f"topology field {field} is invalid.")
        clean_topology[field] = value
    if clean_topology["solids"] != 1:
        raise ValueError("the STEP asset must contain exactly one solid.")
    bounds = raw.get("bounds_mm")
    if not isinstance(bounds, Mapping) or set(bounds) != set(_BOUND_FIELDS):
        raise ValueError("bounding evidence has invalid fields.")
    clean_bounds = {field: _finite(bounds[field], field) for field in _BOUND_FIELDS}
    if any(clean_bounds[field] <= 0 for field in ("size_x", "size_y", "size_z")):
        raise ValueError("the STEP solid has an empty bounding dimension.")
    volume = _finite(raw.get("volume_mm3"), "volume_mm3")
    if volume <= 0:
        raise ValueError("the STEP solid has no positive volume.")
    return {
        "shape_type": raw["shape_type"],
        "null": False,
        "valid": True,
        "topology": clean_topology,
        "bounds_mm": clean_bounds,
        "volume_mm3": volume,
    }


def compare_shape_evidence(
    expected: Mapping[str, Any], observed: Mapping[str, Any], *, tolerance: float = 1e-7
) -> dict[str, Any]:
    """Compare worker and live-parser evidence with a fixed metric tolerance."""

    first = _validate_shape_evidence(expected)
    second = _validate_shape_evidence(observed)
    checks: list[dict[str, Any]] = []
    for field in ("shape_type", "null", "valid", "topology"):
        checks.append(
            {
                "name": field,
                "ok": first[field] == second[field],
                "expected": first[field],
                "observed": second[field],
            }
        )
    for field in _BOUND_FIELDS:
        expected_value = first["bounds_mm"][field]
        observed_value = second["bounds_mm"][field]
        checks.append(
            {
                "name": f"bounds_mm.{field}",
                "ok": math.isclose(
                    expected_value, observed_value, rel_tol=tolerance, abs_tol=tolerance
                ),
                "expected": expected_value,
                "observed": observed_value,
            }
        )
    checks.append(
        {
            "name": "volume_mm3",
            "ok": math.isclose(
                first["volume_mm3"],
                second["volume_mm3"],
                rel_tol=tolerance,
                abs_tol=tolerance,
            ),
            "expected": first["volume_mm3"],
            "observed": second["volume_mm3"],
        }
    )
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def _freecadcmd() -> Path:
    override = os.environ.get("VIBECAD_FREECADCMD", "").strip()
    candidates: list[Path] = [Path(override)] if override else []
    try:
        import FreeCAD as App

        home = Path(App.getHomePath())
        names = (
            ("FreeCADCmd.exe", "freecadcmd.exe")
            if sys.platform == "win32"
            else ("FreeCADCmd", "freecadcmd")
        )
        candidates.extend(home / "bin" / name for name in names)
        candidates.extend(home / "MacOS" / name for name in names)
    except Exception:
        pass
    candidates.extend(
        (Path(sys.executable).with_name("FreeCADCmd"), Path(sys.executable))
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise StepValidationError(
        "STEP_VALIDATOR_UNAVAILABLE",
        "The isolated STEP validator is not available.",
    )


def _worker_environment(
    staging: Path, request: Path, result: Path, brep: Path
) -> dict[str, str]:
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "SYSTEMROOT",
        "WINDIR",
    }
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in allowed
    }
    environment.update(
        {
            "HOME": str(staging),
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "QT_QPA_PLATFORM": "offscreen",
            "TEMP": str(staging),
            "TMP": str(staging),
            "TMPDIR": str(staging),
            "VIBECAD_STEP_VALIDATION_REQUEST": str(request),
            "VIBECAD_STEP_VALIDATION_RESULT": str(result),
            "VIBECAD_STEP_VALIDATION_BREP": str(brep),
        }
    )
    if sys.platform == "win32":
        environment["USERPROFILE"] = str(staging)
    return environment


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(json.dumps(payload, sort_keys=True).encode("ascii"))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _request_payload(
    asset: Mapping[str, Any], project_id: str, internal_path: Path
) -> dict[str, Any]:
    content = {
        "schema": STEP_VALIDATION_REQUEST_SCHEMA,
        "version": STEP_VALIDATION_VERSION,
        "project_id": project_id,
        "asset_id": asset["asset_id"],
        "asset_sha256": asset["sha256"],
        "size_bytes": asset["size_bytes"],
        "format": "step",
        "internal_path": str(internal_path),
    }
    return {**content, "request_sha256": _content_digest(content, "request_sha256")}


def _validate_result(
    raw: Any, *, project_id: str, asset: Mapping[str, Any]
) -> dict[str, Any]:
    expected_fields = {
        "schema",
        "version",
        "ok",
        "project_id",
        "asset_id",
        "asset_sha256",
        "size_bytes",
        "format",
        "shape",
        "brep_sha256",
        "brep_size_bytes",
        "errors",
        "evidence_sha256",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected_fields:
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "The isolated STEP validation result has invalid fields.",
        )
    if (
        raw.get("schema") != STEP_VALIDATION_RESULT_SCHEMA
        or raw.get("version") != STEP_VALIDATION_VERSION
    ):
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "The isolated STEP validation result has an unsupported schema.",
        )
    if raw.get("evidence_sha256") != _content_digest(raw, "evidence_sha256"):
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_TAMPERED",
            "The isolated STEP validation evidence hash is invalid.",
        )
    identity_ok = (
        raw.get("project_id") == project_id
        and raw.get("asset_id") == asset["asset_id"]
        and raw.get("asset_sha256") == asset["sha256"]
        and raw.get("size_bytes") == asset["size_bytes"]
        and raw.get("format") == "step"
    )
    if not identity_ok:
        raise StepValidationError(
            "STEP_VALIDATION_IDENTITY_MISMATCH",
            "The isolated STEP validation result does not match the registered asset.",
        )
    errors = raw.get("errors")
    if not isinstance(errors, list) or any(not isinstance(item, str) for item in errors):
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "The isolated STEP validation errors are invalid.",
        )
    if raw.get("ok") is not True:
        raise StepValidationError(
            "STEP_CONTENT_INVALID",
            "The registered STEP asset did not pass isolated geometry validation.",
            {"errors": errors[:8]},
        )
    brep_sha256 = raw.get("brep_sha256")
    brep_size = raw.get("brep_size_bytes")
    if (
        not isinstance(brep_sha256, str)
        or len(brep_sha256) != 64
        or any(char not in "0123456789abcdef" for char in brep_sha256)
        or isinstance(brep_size, bool)
        or type(brep_size) is not int
        or not 1 <= brep_size <= MAX_VALIDATED_BREP_BYTES
    ):
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "The isolated STEP validation BREP identity is invalid.",
        )
    try:
        shape = _validate_shape_evidence(raw.get("shape"))
    except ValueError as exc:
        raise StepValidationError(
            "STEP_CONTENT_INVALID",
            f"The registered STEP asset is not one valid solid: {exc}",
        ) from exc
    if errors:
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "Successful isolated STEP validation contains errors.",
        )
    return {
        "schema": STEP_VALIDATION_RESULT_SCHEMA,
        "version": STEP_VALIDATION_VERSION,
        "ok": True,
        "project_id": project_id,
        "asset_id": asset["asset_id"],
        "asset_sha256": asset["sha256"],
        "size_bytes": asset["size_bytes"],
        "format": "step",
        "shape": shape,
        "brep_sha256": brep_sha256,
        "brep_size_bytes": brep_size,
        "errors": [],
        "evidence_sha256": raw["evidence_sha256"],
    }


def _read_worker_result(path: Path) -> Any:
    """Read one bounded worker result through one stable no-follow handle."""

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if not hasattr(os, "O_NOFOLLOW"):
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "This platform cannot prove the STEP validation result identity.",
        )
    flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_MISSING",
            "The isolated STEP validator did not create a result.",
        ) from exc
    except OSError as exc:
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "The isolated STEP validation result is unsafe.",
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= MAX_STEP_VALIDATION_RESULT_BYTES
        ):
            raise StepValidationError(
                "STEP_VALIDATION_RESULT_INVALID",
                "The isolated STEP validation result has an invalid byte size.",
            )
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            encoded = stream.read(MAX_STEP_VALIDATION_RESULT_BYTES + 1)
        after = os.fstat(descriptor)
        if (
            len(encoded) != before.st_size
            or len(encoded) > MAX_STEP_VALIDATION_RESULT_BYTES
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise StepValidationError(
                "STEP_VALIDATION_RESULT_INVALID",
                "The isolated STEP validation result changed while it was read.",
            )
    finally:
        os.close(descriptor)
    try:
        return json.loads(encoded.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise StepValidationError(
            "STEP_VALIDATION_RESULT_INVALID",
            "The isolated STEP validation result cannot be decoded.",
        ) from exc


def validate_registered_step(
    project_root: str | Path,
    project_id: str,
    asset_id: str,
    *,
    freecadcmd: str | Path | None = None,
    timeout_seconds: float = DEFAULT_STEP_VALIDATION_TIMEOUT_SECONDS,
    memory_limit_bytes: int = DEFAULT_STEP_VALIDATION_MEMORY_BYTES,
    cancellation_check: Callable[[], bool] | None = None,
    process_runner: Callable[..., Mapping[str, Any]] = run_process,
) -> ValidatedStepCandidate:
    """Validate one registered asset in a bounded, windowless FreeCAD process."""

    clean_timeout = _normalized_timeout(timeout_seconds)
    clean_memory_limit = _normalized_memory_limit(memory_limit_bytes)
    executable = Path(freecadcmd) if freecadcmd is not None else _freecadcmd()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise StepValidationError(
            "STEP_VALIDATOR_UNAVAILABLE",
            "The isolated STEP validator executable is unavailable.",
        )
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-step-validation-")
    try:
        staging = Path(temporary.name)
        request_path = staging / "request.json"
        result_path = staging / "result.json"
        source_path = staging / "registered.step"
        artifact_path = staging / "validated.brep"
        asset = copy_registered_import_asset(
            project_root,
            project_id,
            asset_id,
            source_path,
        )
        _write_json_atomic(
            request_path,
            _request_payload(asset, str(project_id), source_path),
        )
        module_root = str(Path(__file__).resolve().parent)
        worker_code = (
            "import sys;"
            f"sys.path.insert(0,{module_root!r});"
            "from VibeCADStepValidator import worker_main;worker_main()"
        )
        command = [
            str(executable),
            "--safe-mode",
            "-c",
            worker_code,
        ]
        worker_environment = _worker_environment(
            staging, request_path, result_path, artifact_path
        )
        try:
            sandbox_plan = prepare_step_worker_sandbox(
                command,
                staging=staging,
                environment=worker_environment,
                module_roots=(module_root,),
            )
        except StepWorkerSandboxUnavailable as exc:
            raise StepValidationError(
                exc.code,
                str(exc),
                exc.provider_evidence(),
            ) from exc
        except (OSError, ValueError) as exc:
            raise StepValidationError(
                "STEP_VALIDATOR_SANDBOX_INVALID",
                "The STEP validator sandbox plan is invalid.",
            ) from exc
        process = dict(
            process_runner(
                list(sandbox_plan.command),
                cwd=staging,
                environment=dict(sandbox_plan.environment),
                cancellation_check=cancellation_check,
                timeout_seconds=clean_timeout,
                memory_limit_bytes=clean_memory_limit,
            )
        )
        if not process.get("started"):
            raise StepValidationError(
                "STEP_VALIDATOR_START_FAILED",
                "The isolated STEP validator could not start.",
            )
        if process.get("cancelled"):
            raise StepValidationError(
                "STEP_VALIDATION_CANCELLED", "The STEP validation was cancelled."
            )
        if process.get("timed_out"):
            raise StepValidationError(
                "STEP_VALIDATION_TIMEOUT", "The isolated STEP validation timed out."
            )
        if process.get("memory_exceeded"):
            raise StepValidationError(
                "STEP_VALIDATION_MEMORY_LIMIT",
                "The isolated STEP validation exceeded its memory limit.",
            )
        if process.get("output_exceeded"):
            raise StepValidationError(
                "STEP_VALIDATION_OUTPUT_LIMIT",
                "The isolated STEP validation exceeded its output limit.",
            )
        if process.get("returncode") != 0:
            raise StepValidationError(
                "STEP_VALIDATOR_CRASHED",
                "The isolated STEP validator exited without a valid result.",
                {"returncode": process.get("returncode")},
            )
        raw = _read_worker_result(result_path)
        result = _validate_result(raw, project_id=str(project_id), asset=asset)
        if not artifact_path.is_file() or artifact_path.is_symlink():
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_MISSING",
                "The isolated STEP validator did not create a BREP artifact.",
            )
        try:
            _verify_private_artifact(
                artifact_path,
                expected_sha256=result["brep_sha256"],
                expected_size=result["brep_size_bytes"],
            )
        except (OSError, ValueError) as exc:
            raise StepValidationError(
                "STEP_VALIDATION_ARTIFACT_TAMPERED",
                "The isolated STEP validation BREP failed identity checks.",
            ) from exc
        try:
            verified = resolve_import_asset(project_root, project_id, asset_id)
        except (ValueError, RuntimeError) as exc:
            raise StepValidationError(
                "STEP_ASSET_TAMPERED",
                "The registered STEP asset changed after isolated validation.",
            ) from exc
        if (
            verified["sha256"] != result["asset_sha256"]
            or verified["size_bytes"] != result["size_bytes"]
        ):
            raise StepValidationError(
                "STEP_ASSET_TAMPERED",
                "The registered STEP asset changed after isolated validation.",
            )
        return ValidatedStepCandidate(
            result,
            temporary,
            artifact_path,
            _seal=_CANDIDATE_CONSTRUCTOR_SEAL,
        )
    except Exception:
        temporary.cleanup()
        raise


def _read_worker_request(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema",
        "version",
        "project_id",
        "asset_id",
        "asset_sha256",
        "size_bytes",
        "format",
        "internal_path",
        "request_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("The STEP validation request has invalid fields.")
    if (
        raw.get("schema") != STEP_VALIDATION_REQUEST_SCHEMA
        or raw.get("version") != STEP_VALIDATION_VERSION
        or raw.get("format") != "step"
        or raw.get("request_sha256") != _content_digest(raw, "request_sha256")
    ):
        raise ValueError("The STEP validation request is invalid.")
    internal_path = Path(str(raw.get("internal_path") or ""))
    if (
        not internal_path.is_absolute()
        or internal_path.is_symlink()
        or not internal_path.is_file()
    ):
        raise ValueError("The STEP validation source is unsafe or missing.")
    asset_id = raw.get("asset_id")
    digest = raw.get("asset_sha256")
    size = raw.get("size_bytes")
    if (
        not isinstance(asset_id, str)
        or len(asset_id) != 32
        or any(char not in "0123456789abcdef" for char in asset_id)
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or isinstance(size, bool)
        or type(size) is not int
        or size <= 0
    ):
        raise ValueError("The STEP validation request identity is invalid.")
    if (
        internal_path.name != "registered.step"
        or internal_path.parent.resolve() != path.parent.resolve()
    ):
        raise ValueError("The STEP validation source name is invalid.")
    return raw


def _write_validated_brep(shape: Any, target: Path) -> tuple[str, int]:
    """Write one worker-generated BREP with private permissions and identity."""

    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shape.exportBrep(str(temporary))
        if not temporary.is_file() or temporary.is_symlink():
            raise ValueError("The BREP serializer did not create a safe artifact.")
        os.chmod(temporary, 0o600)
        size = temporary.stat().st_size
        if not 1 <= size <= MAX_VALIDATED_BREP_BYTES:
            raise ValueError("The generated BREP byte size is invalid.")
        descriptor, _metadata = _open_regular_read_only(temporary)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, target)
        temporary.unlink()
        if os.name != "nt":
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        digest = _file_sha256(target)
        return digest, size
    finally:
        temporary.unlink(missing_ok=True)


def worker_main() -> None:
    """FreeCADCmd entry point. It writes one BREP-bound result."""

    request_path = Path(os.environ["VIBECAD_STEP_VALIDATION_REQUEST"])
    result_path = Path(os.environ["VIBECAD_STEP_VALIDATION_RESULT"])
    brep_path = Path(os.environ["VIBECAD_STEP_VALIDATION_BREP"])
    request: dict[str, Any] = {}
    shape: dict[str, Any] | None = None
    brep_sha256 = ""
    brep_size_bytes = 0
    errors: list[str] = []
    try:
        request = _read_worker_request(request_path)
        source = Path(request["internal_path"])
        if (
            not brep_path.is_absolute()
            or brep_path.name != "validated.brep"
            or brep_path.parent.resolve() != request_path.parent.resolve()
            or brep_path.exists()
            or brep_path.is_symlink()
        ):
            raise ValueError("The BREP validation target is unsafe.")
        isolated_source = request_path.parent / "parser-input.step"
        before_digest = _copy_verified_source(
            source,
            isolated_source,
            expected_sha256=request["asset_sha256"],
            expected_size=request["size_bytes"],
        )
        import Part

        parser_descriptor, parser_metadata = _open_regular_read_only(isolated_source)
        try:
            _verify_parser_input_binding(
                isolated_source,
                parser_descriptor,
                parser_metadata,
                expected_sha256=before_digest,
                expected_size=request["size_bytes"],
            )
            imported = Part.read(str(isolated_source))
            _verify_parser_input_binding(
                isolated_source,
                parser_descriptor,
                parser_metadata,
                expected_sha256=before_digest,
                expected_size=request["size_bytes"],
            )
        finally:
            os.close(parser_descriptor)
        solids = list(imported.Solids)
        if len(solids) != 1:
            raise ValueError("The STEP asset must contain exactly one solid.")
        solid = solids[0]
        shape = shape_evidence(solid)
        _validate_shape_evidence(shape)
        source_native_identity = _canonical_native_shape_identity(solid)
        if _file_sha256(source) != before_digest:
            raise ValueError("The registered STEP bytes changed during validation.")
        brep_sha256, brep_size_bytes = _write_validated_brep(solid, brep_path)
        reopened = Part.read(str(brep_path))
        reopened_evidence = shape_evidence(reopened)
        if not compare_shape_evidence(shape, reopened_evidence)["ok"]:
            raise ValueError("The generated BREP did not preserve the validated solid.")
        if _file_sha256(brep_path) != brep_sha256:
            raise ValueError("The generated BREP changed during validation.")
        if _canonical_native_shape_identity(reopened) != source_native_identity:
            raise ValueError(
                "The generated BREP changed the native shape serialization."
            )
    except Exception as exc:
        brep_path.unlink(missing_ok=True)
        # Do not put the private project path or parser text in provider-visible
        # evidence.  The exception class is enough for developer diagnostics.
        errors.append(f"{type(exc).__name__}: STEP parsing or validation failed.")
    content = {
        "schema": STEP_VALIDATION_RESULT_SCHEMA,
        "version": STEP_VALIDATION_VERSION,
        "ok": not errors,
        "project_id": str(request.get("project_id") or ""),
        "asset_id": str(request.get("asset_id") or ""),
        "asset_sha256": str(request.get("asset_sha256") or ""),
        "size_bytes": request.get("size_bytes") if request else 0,
        "format": str(request.get("format") or ""),
        "shape": shape,
        "brep_sha256": brep_sha256,
        "brep_size_bytes": brep_size_bytes,
        "errors": errors,
    }
    result = {
        **content,
        "evidence_sha256": _content_digest(content, "evidence_sha256"),
    }
    _write_json_atomic(result_path, result)


__all__ = [
    "STEP_VALIDATION_RESULT_SCHEMA",
    "StepValidationError",
    "ValidatedStepCandidate",
    "compare_shape_evidence",
    "shape_evidence",
    "validate_registered_step",
    "worker_main",
]
