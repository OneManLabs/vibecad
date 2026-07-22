# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import pytest

from VibeCADCore import VibeCADService
from VibeCADImageCalibration import (
    CALIBRATION_SCHEMA,
    ESTIMATE_SCHEMA,
    create_calibration,
    estimate_dimension,
    validate_calibration,
)


def test_calibration_converts_supported_units_to_millimetres():
    calibration = create_calibration(
        known_length=2, known_unit="in", pixel_distance=200
    )

    assert calibration["schema"] == CALIBRATION_SCHEMA
    assert calibration["known_length_mm"] == pytest.approx(50.8)
    assert calibration["millimetres_per_pixel"] == pytest.approx(0.254)
    assert validate_calibration(calibration) == calibration


@pytest.mark.parametrize(
    "values",
    [
        {"known_length": 0, "known_unit": "mm", "pixel_distance": 10},
        {"known_length": 10, "known_unit": "feet", "pixel_distance": 10},
        {"known_length": 10, "known_unit": "mm", "pixel_distance": 0.5},
    ],
)
def test_calibration_rejects_invalid_scale(values):
    with pytest.raises(ValueError):
        create_calibration(**values)


def test_uncalibrated_image_never_returns_a_numeric_dimension():
    estimate = estimate_dimension(250, None)

    assert estimate["schema"] == ESTIMATE_SCHEMA
    assert estimate["value_mm"] is None
    assert estimate["needs_scale_reference"] is True
    assert estimate["is_estimate"] is True


def test_calibrated_dimension_remains_an_explicit_estimate():
    calibration = create_calibration(
        known_length=100, known_unit="mm", pixel_distance=500
    )

    estimate = estimate_dimension(125, calibration)

    assert estimate["value_mm"] == pytest.approx(25)
    assert estimate["needs_scale_reference"] is False
    assert estimate["is_estimate"] is True
    assert "Perspective" in estimate["warning"]


def test_tampered_calibration_is_rejected():
    calibration = create_calibration(
        known_length=100, known_unit="mm", pixel_distance=500
    )
    calibration["known_length"] = 90

    with pytest.raises(RuntimeError, match="does not match"):
        validate_calibration(calibration)


def test_service_calibration_is_persisted_and_available_for_estimates(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("VIBECAD_HOME", str(tmp_path))
    service = VibeCADService()
    service._reference_images = [
        {"id": "image-1", "name": "drawing.png", "path": "/drawing.png"}
    ]
    monkeypatch.setattr(service, "_load_reference_images_for_active_project", lambda: None)
    writes = []
    monkeypatch.setattr(service, "_write_reference_images", lambda: writes.append(True))

    calibrated = service.calibrate_reference_image(
        "image-1", known_length=80, known_unit="mm", pixel_distance=400
    )
    estimated = service.estimate_reference_dimension(
        "image-1", pixel_distance=100
    )

    assert calibrated["ok"] is True
    assert writes == [True]
    assert estimated["estimate"]["value_mm"] == pytest.approx(20)
    assert estimated["estimate"]["is_estimate"] is True


def test_service_calibration_write_failure_restores_prior_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBECAD_HOME", str(tmp_path))
    service = VibeCADService()
    service._reference_images = [
        {"id": "image-1", "name": "drawing.png", "path": "/drawing.png"}
    ]
    monkeypatch.setattr(service, "_load_reference_images_for_active_project", lambda: None)
    monkeypatch.setattr(
        service,
        "_write_reference_images",
        lambda: (_ for _ in ()).throw(OSError("injected write failure")),
    )

    result = service.calibrate_reference_image(
        "image-1", known_length=80, known_unit="mm", pixel_distance=400
    )

    assert result["ok"] is False
    assert "scale_calibration" not in service._reference_images[0]


def test_clean_reference_drops_tampered_scale_and_records_error():
    calibration = create_calibration(
        known_length=100, known_unit="mm", pixel_distance=500
    )
    calibration["pixel_distance"] = 50

    cleaned = VibeCADService._clean_reference_entry(
        {
            "id": "image-1",
            "name": "drawing.png",
            "path": "/drawing.png",
            "scale_calibration": calibration,
        }
    )

    assert "scale_calibration" not in cleaned
    assert cleaned["scale_error"].startswith("Stored image scale is invalid")


def test_reference_panel_exposes_an_accessible_scale_control():
    source = (Path(__file__).resolve().parents[1] / "VibeCADGui.py").read_text(
        encoding="utf-8"
    )

    assert "VibeReferenceScale_" in source
    assert "Set scale for {name}" in source
    assert "Pixel-derived dimensions will remain labeled as estimates" in source


def test_provider_contract_forbids_exact_uncalibrated_image_dimensions():
    source = (Path(__file__).resolve().parents[1] / "VibeCADProvider.py").read_text(
        encoding="utf-8"
    )

    assert "Never infer an exact CAD dimension from an image." in source
    assert "uncalibrated-no-exact-dimensions" in source
    assert "pixel-dimensions-are-estimates" in source
