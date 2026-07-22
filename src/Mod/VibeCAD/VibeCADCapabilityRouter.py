# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deterministic beginner-first selection of one valid CAD modeling strategy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence


ROUTER_SCHEMA = "vibecad-capability-route-v1"
ROUTER_VERSION = 1
SCRIPTED_PART_HINTS = frozenset({
    "enclosure", "bracket", "mount", "adapter", "clamp", "cover", "tray",
    "housing", "lid", "fixture", "coupling",
})
PROFESSIONAL_NATIVE_HINTS = frozenset({
    "assembly", "drawing", "techdraw", "fem", "cam", "cnc", "bim", "surface",
    "sheet metal", "spreadsheet", "constraint", "sketch",
})


@dataclass(frozen=True)
class CapabilityRoute:
    schema: str
    version: int
    route_id: str
    engine: str
    workbench: str
    reason_code: str
    explanation: str
    preserved_existing_structure: bool
    automatic: bool

    def summary(self) -> dict[str, Any]:
        return asdict(self)


def _result(
    *, engine: str, workbench: str, reason: str, explanation: str,
    preserved: bool, automatic: bool = True,
) -> CapabilityRoute:
    content = {
        "schema": ROUTER_SCHEMA, "version": ROUTER_VERSION, "engine": engine,
        "workbench": workbench, "reason_code": reason, "explanation": explanation,
        "preserved_existing_structure": preserved, "automatic": automatic,
    }
    route_id = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return CapabilityRoute(route_id=route_id, **content)


def route_capability(
    request: str, *, workbench: str | None, current_engine: str,
    available_engines: Sequence[str], has_existing_geometry: bool,
    strategy_lock: str | None = None,
) -> CapabilityRoute:
    """Choose one route without provider calls or hidden fallback behavior."""
    active = str(workbench or "").strip()
    current = str(current_engine or "").strip().lower()
    available = tuple(dict.fromkeys(str(item).strip().lower() for item in available_engines))
    if not active or not available:
        raise RuntimeError("No CAD authoring strategy is available for this document.")
    if strategy_lock:
        locked = str(strategy_lock).strip().lower()
        if locked not in available:
            raise RuntimeError("The locked modeling strategy is not available.")
        return _result(
            engine=locked, workbench=active, reason="advanced_lock",
            explanation="The project uses the advanced modeling-strategy lock.",
            preserved=has_existing_geometry, automatic=False,
        )
    if has_existing_geometry and current in available:
        return _result(
            engine=current, workbench=active, reason="preserve_document_structure",
            explanation="The route keeps the document's established editable structure.",
            preserved=True,
        )
    words = str(request or "").casefold()
    if any(hint in words for hint in PROFESSIONAL_NATIVE_HINTS) and "native" in available:
        return _result(
            engine="native", workbench=active, reason="professional_native_capability",
            explanation="The request needs native professional CAD features.", preserved=False,
        )
    if (
        active == "PartDesignWorkbench" and "build123d" in available and
        any(hint in words for hint in SCRIPTED_PART_HINTS)
    ):
        return _result(
            engine="build123d", workbench=active, reason="new_functional_part",
            explanation="A validated parametric scripted part is the most reliable route for this new object.",
            preserved=False,
        )
    if "native" in available:
        return _result(
            engine="native", workbench=active, reason="native_editability_default",
            explanation="Native parametric CAD gives the most editable default result.", preserved=False,
        )
    if current in available:
        selected = current
    else:
        selected = available[0]
    return _result(
        engine=selected, workbench=active, reason="available_surface",
        explanation="The route uses the available CAD surface for this workbench.", preserved=False,
    )
