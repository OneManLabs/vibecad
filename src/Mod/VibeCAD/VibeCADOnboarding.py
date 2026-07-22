# SPDX-License-Identifier: LGPL-2.1-or-later
"""Durable, engine-neutral first-launch choices for the beginner workspace."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from VibeCADProject import vibecad_data_dir


ONBOARDING_SCHEMA = "vibecad-onboarding-v1"
ONBOARDING_VERSION = 1


@dataclass(frozen=True)
class StartChoice:
    choice_id: str
    title: str
    description: str
    prompt: str


START_CHOICES = (
    StartChoice("new-part", "Create a new part", "Describe the object and its important dimensions.", "Create a new editable part for: "),
    StartChoice("modify-file", "Modify an existing CAD file", "Open a design, select what matters, and describe the change.", "Modify the open design to: "),
    StartChoice("reference-image", "Design from a reference image", "Attach a picture or drawing. Add one known dimension when scale matters.", "Create an editable design from the attached reference image. The known scale is: "),
    StartChoice("enclosure", "Create an enclosure", "Describe the contents, envelope, walls, openings, and lid.", "Create an editable enclosure for: "),
    StartChoice("bracket", "Create a bracket or mount", "Describe what it supports, its interfaces, dimensions, and loads.", "Create an editable bracket or mount for: "),
    StartChoice("assembly", "Create an assembly", "Describe the separate parts, their connections, and required motion.", "Create an editable assembly for: "),
    StartChoice("learn", "Learn by building", "Build a small editable object with short explanations.", "Help me build a simple editable object. Explain only the decisions I need to make. Start with: "),
)

_CHOICES_BY_ID = {choice.choice_id: choice for choice in START_CHOICES}


def onboarding_path() -> Path:
    return vibecad_data_dir() / "onboarding.json"


def default_state() -> dict[str, Any]:
    return {
        "schema": ONBOARDING_SCHEMA,
        "version": ONBOARDING_VERSION,
        "completed": False,
        "last_choice": None,
    }


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Onboarding state must be a JSON object.")
    if value.get("schema") != ONBOARDING_SCHEMA or value.get("version") != ONBOARDING_VERSION:
        raise ValueError("Onboarding state uses an unsupported schema.")
    if not isinstance(value.get("completed"), bool):
        raise ValueError("Onboarding completion state must be true or false.")
    choice = value.get("last_choice")
    if choice is not None and choice not in _CHOICES_BY_ID:
        raise ValueError("Onboarding state has an unknown start choice.")
    return {
        "schema": ONBOARDING_SCHEMA,
        "version": ONBOARDING_VERSION,
        "completed": value["completed"],
        "last_choice": choice,
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or onboarding_path()
    if not target.exists():
        return default_state()
    try:
        return _validate_state(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Could not read onboarding state: {exc}") from exc


def save_state(state: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    validated = _validate_state(state)
    target = path or onboarding_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(validated, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return validated


def choose_start(choice_id: str, path: Path | None = None) -> StartChoice:
    choice = start_choice(choice_id)
    save_state(
        {
            "schema": ONBOARDING_SCHEMA,
            "version": ONBOARDING_VERSION,
            "completed": True,
            "last_choice": choice.choice_id,
        },
        path,
    )
    return choice


def start_choice(choice_id: str) -> StartChoice:
    """Resolve one choice without changing durable completion state."""

    try:
        return _CHOICES_BY_ID[choice_id]
    except KeyError as exc:
        raise ValueError(f"Unknown start choice: {choice_id}") from exc


def reset_onboarding(path: Path | None = None) -> dict[str, Any]:
    return save_state(default_state(), path)
