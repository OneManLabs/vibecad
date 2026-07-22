# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scale and estimate contracts for reference images."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


CALIBRATION_SCHEMA = "vibecad-image-calibration-v1"
ESTIMATE_SCHEMA = "vibecad-image-dimension-estimate-v1"
CALIBRATION_VERSION = 1
_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


def _number(value: Any, name: str, *, minimum: float = 0.0) -> float:
    try:
        clean = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not math.isfinite(clean) or clean <= minimum:
        raise ValueError(f"{name} must be greater than {minimum:g}.")
    return clean


def _content(calibration: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": CALIBRATION_SCHEMA,
        "version": CALIBRATION_VERSION,
        "known_length": float(calibration["known_length"]),
        "known_unit": str(calibration["known_unit"]),
        "known_length_mm": float(calibration["known_length_mm"]),
        "pixel_distance": float(calibration["pixel_distance"]),
        "millimetres_per_pixel": float(calibration["millimetres_per_pixel"]),
        "basis": "user_supplied_scale_reference",
    }


def calibration_id(calibration: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _content(calibration), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_calibration(
    *, known_length: Any, known_unit: str, pixel_distance: Any
) -> dict[str, Any]:
    length = _number(known_length, "Known length")
    pixels = _number(pixel_distance, "Pixel distance", minimum=0.999999)
    unit = str(known_unit or "").strip().lower()
    if unit not in _UNIT_TO_MM:
        raise ValueError("Known unit must be mm, cm, m, or in.")
    millimetres = length * _UNIT_TO_MM[unit]
    calibration = {
        "schema": CALIBRATION_SCHEMA,
        "version": CALIBRATION_VERSION,
        "known_length": length,
        "known_unit": unit,
        "known_length_mm": millimetres,
        "pixel_distance": pixels,
        "millimetres_per_pixel": millimetres / pixels,
        "basis": "user_supplied_scale_reference",
    }
    calibration["calibration_id"] = calibration_id(calibration)
    return calibration


def validate_calibration(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RuntimeError("Image scale calibration is not an object.")
    if raw.get("schema") != CALIBRATION_SCHEMA or raw.get("version") != CALIBRATION_VERSION:
        raise RuntimeError("Image scale calibration has an unsupported schema.")
    clean = create_calibration(
        known_length=raw.get("known_length"),
        known_unit=str(raw.get("known_unit") or ""),
        pixel_distance=raw.get("pixel_distance"),
    )
    if str(raw.get("calibration_id") or "") != clean["calibration_id"]:
        raise RuntimeError("Image scale calibration does not match its content.")
    return clean


def estimate_dimension(pixel_distance: Any, calibration: Any | None) -> dict[str, Any]:
    pixels = _number(pixel_distance, "Pixel distance", minimum=0.0)
    if calibration is None:
        return {
            "schema": ESTIMATE_SCHEMA,
            "version": CALIBRATION_VERSION,
            "pixel_distance": pixels,
            "value_mm": None,
            "is_estimate": True,
            "needs_scale_reference": True,
            "warning": "Add one known dimension before VibeCAD uses an image measurement for CAD geometry.",
        }
    clean = validate_calibration(calibration)
    return {
        "schema": ESTIMATE_SCHEMA,
        "version": CALIBRATION_VERSION,
        "pixel_distance": pixels,
        "value_mm": pixels * clean["millimetres_per_pixel"],
        "is_estimate": True,
        "needs_scale_reference": False,
        "calibration_id": clean["calibration_id"],
        "warning": "This dimension is an image estimate. Perspective and lens distortion can change it.",
    }
