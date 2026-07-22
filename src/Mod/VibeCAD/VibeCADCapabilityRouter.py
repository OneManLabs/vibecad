# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deterministic beginner-first selection of one valid CAD modeling strategy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


ROUTING_REQUEST_SCHEMA = "vibecad-capability-routing-request-v1"
ROUTING_REQUEST_VERSION = 1
ROUTER_SCHEMA = "vibecad-capability-route-v2"
ROUTER_VERSION = 2
LEGACY_ROUTER_SCHEMA = "vibecad-capability-route-v1"
RELIABILITY_POLICY = "deterministic-native-safe-v1"
KNOWN_ENGINES = frozenset({"native", "build123d", "openscad", "vibescript"})

SCRIPTED_PART_HINTS = frozenset({
    "enclosure", "bracket", "mount", "mounting", "adapter", "clamp", "cover", "tray",
    "housing", "lid", "fixture", "coupling",
})

_CATEGORY_HINTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("drawing", frozenset({"drawing", "techdraw", "title block", "section view"})),
    ("assembly", frozenset({"assembly", "joint", "interference", "bill of materials", "bom"})),
    ("fem", frozenset({"fem", "finite element", "stress analysis", "structural analysis"})),
    ("cam", frozenset({"cam", "cnc", "toolpath", "g-code", "gcode"})),
    ("bim", frozenset({"bim", "building information", "architectural wall"})),
    ("surface", frozenset({"surface", "nurbs", "blend surface", "filled surface"})),
    ("spreadsheet", frozenset({"spreadsheet", "size variants", "parameter table"})),
    ("mesh", frozenset({"mesh repair", "mesh analysis", "convert mesh", "remesh"})),
    ("sketch", frozenset({"fully constrained sketch", "sketch constraint", "constrain sketch"})),
)

NATIVE_CAPABILITY_WORKBENCHES: dict[str, str] = {
    "drawing": "TechDrawWorkbench",
    "assembly": "AssemblyWorkbench",
    "fem": "FemWorkbench",
    "cam": "CAMWorkbench",
    "bim": "BIMWorkbench",
    "surface": "SurfaceWorkbench",
    "spreadsheet": "SpreadsheetWorkbench",
    "mesh": "MeshWorkbench",
    "sketch": "SketcherWorkbench",
}

COMPATIBLE_FOLLOW_UP_CATEGORIES = frozenset({
    "part_edit", "part_design", "functional_part", "generic_edit",
})
CAPABILITY_CATEGORIES = frozenset({
    *NATIVE_CAPABILITY_WORKBENCHES,
    *COMPATIBLE_FOLLOW_UP_CATEGORIES,
})


def _clean_engine(value: Any) -> str:
    return str(value or "").strip().lower()


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    # Make the request safe to hash and persist. This also rejects live CAD
    # objects and other values that cannot cross the provider boundary.
    try:
        clean = json.loads(json.dumps(dict(value), sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ValueError("Capability-routing context must contain JSON values.") from exc
    if not isinstance(clean, dict):
        raise ValueError("Capability-routing context must be a JSON object.")
    return clean


def _contains_hint(text: str, hint: str) -> bool:
    """Match one normalized token or phrase, not an arbitrary substring."""

    phrase = str(hint or "").strip().casefold()
    if not phrase:
        return False
    pattern = re.escape(phrase).replace(r"\ ", r"\s+")
    return re.search(
        rf"(?<![a-z0-9_]){pattern}(?![a-z0-9_])",
        str(text or "").casefold(),
    ) is not None


def infer_capability_category(
    request: str,
    *,
    selection_context: Mapping[str, Any] | None = None,
    manufacturing_intent: Mapping[str, Any] | str | None = None,
) -> str:
    """Return one stable category without a provider call."""

    words = str(request or "").casefold()
    for category, hints in _CATEGORY_HINTS:
        if any(_contains_hint(words, hint) for hint in hints):
            return category
    manufacturing = (
        json.dumps(manufacturing_intent, sort_keys=True).casefold()
        if isinstance(manufacturing_intent, Mapping)
        else str(manufacturing_intent or "").casefold()
    )
    if any(
        _contains_hint(words, hint) or _contains_hint(manufacturing, hint)
        for hint in ("toolpath", "cnc", "g-code", "gcode")
    ):
        return "cam"
    if any(_contains_hint(words, hint) for hint in SCRIPTED_PART_HINTS):
        return "functional_part"
    selection = selection_context if isinstance(selection_context, Mapping) else {}
    if int(selection.get("selection_count") or 0) > 0:
        return "part_edit"
    edit_hints = ("make", "move", "change", "resize", "thicken", "round", "remove")
    if any(_contains_hint(words, hint) for hint in edit_hints):
        return "part_edit"
    return "part_design"


def target_workbench_for_category(
    category: str,
    source_workbench: str | None,
    *,
    has_existing_structure: bool = False,
) -> str:
    clean = str(category or "part_design").strip().lower()
    if clean in NATIVE_CAPABILITY_WORKBENCHES:
        return NATIVE_CAPABILITY_WORKBENCHES[clean]
    if has_existing_structure and clean in COMPATIBLE_FOLLOW_UP_CATEGORIES:
        return str(source_workbench or "PartDesignWorkbench").strip() or "PartDesignWorkbench"
    if clean in {"part_design", "part_edit", "functional_part", "generic_edit"}:
        return "PartDesignWorkbench"
    return str(source_workbench or "PartDesignWorkbench").strip() or "PartDesignWorkbench"


def _normalize_reliability(
    value: Mapping[str, Any] | None,
    available_engines: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], bool]:
    raw = value if isinstance(value, Mapping) else {}
    normalized: dict[str, dict[str, Any]] = {}
    explicit = False
    for engine in available_engines:
        item = raw.get(engine)
        score: float | None = None
        source = "not_supplied"
        if isinstance(item, Mapping):
            candidate = item.get("score")
            source = str(item.get("source") or "explicit")
            try:
                score = float(candidate)
            except (TypeError, ValueError):
                score = None
        elif item is not None:
            try:
                score = float(item)
                source = "explicit"
            except (TypeError, ValueError):
                score = None
        if score is not None:
            if not 0.0 <= score <= 1.0:
                raise ValueError("Modeling reliability scores must be from 0 through 1.")
            explicit = True
        elif engine == "native":
            score = 1.0
            source = "safe_native_default"
        else:
            score = 0.0
            source = "missing_data"
        normalized[engine] = {"score": score, "source": source}
    return normalized, explicit


@dataclass(frozen=True)
class CapabilityRoutingRequest:
    schema: str
    version: int
    request: str
    capability_category: str
    source_workbench: str
    selection_context: dict[str, Any]
    manufacturing_intent: dict[str, Any]
    existing_document_structure: dict[str, Any]
    available_engines: tuple[str, ...]
    current_engine: str
    strategy_lock: str | None
    reliability: dict[str, dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        result = asdict(self)
        result["available_engines"] = list(self.available_engines)
        return result


@dataclass(frozen=True)
class CapabilityRoute:
    schema: str
    version: int
    route_id: str
    engine: str
    workbench: str
    target_workbench: str
    reason_code: str
    explanation: str
    preserved_existing_structure: bool
    automatic: bool
    request: dict[str, Any]
    evidence: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return asdict(self)


def make_routing_request(
    request: str,
    *,
    workbench: str | None,
    current_engine: str,
    available_engines: Sequence[str],
    has_existing_geometry: bool = False,
    strategy_lock: str | None = None,
    capability_category: str | None = None,
    selection_context: Mapping[str, Any] | None = None,
    manufacturing_intent: Mapping[str, Any] | str | None = None,
    existing_document_structure: Mapping[str, Any] | None = None,
    reliability: Mapping[str, Any] | None = None,
) -> CapabilityRoutingRequest:
    """Create and validate the versioned request used by the route decision."""

    active = str(workbench or "").strip()
    available = tuple(dict.fromkeys(_clean_engine(item) for item in available_engines if _clean_engine(item)))
    if not active or not available:
        raise RuntimeError("No CAD authoring strategy is available for this document.")
    if any(engine not in KNOWN_ENGINES for engine in available):
        raise ValueError("The capability-routing request contains an unknown engine.")
    clean_current = _clean_engine(current_engine)
    if clean_current and clean_current not in KNOWN_ENGINES:
        raise ValueError("The capability-routing request contains an unknown current engine.")
    selection = _json_object(selection_context)
    if isinstance(manufacturing_intent, Mapping):
        manufacturing = _json_object(manufacturing_intent)
    elif str(manufacturing_intent or "").strip():
        manufacturing = {"process": str(manufacturing_intent).strip()}
    else:
        manufacturing = {}
    structure = _json_object(existing_document_structure)
    if has_existing_geometry:
        structure.setdefault("has_geometry", True)
        structure.setdefault("established_engine", _clean_engine(current_engine))
        structure.setdefault(
            "compatible_capabilities", sorted(COMPATIBLE_FOLLOW_UP_CATEGORIES)
        )
    category = str(capability_category or "").strip().lower() or infer_capability_category(
        request,
        selection_context=selection,
        manufacturing_intent=manufacturing,
    )
    if category not in CAPABILITY_CATEGORIES:
        raise ValueError(f"Unknown CAD capability category: {category!r}.")
    normalized_reliability, _ = _normalize_reliability(reliability, available)
    lock = _clean_engine(strategy_lock) or None
    return CapabilityRoutingRequest(
        schema=ROUTING_REQUEST_SCHEMA,
        version=ROUTING_REQUEST_VERSION,
        request=str(request or "").strip(),
        capability_category=category,
        source_workbench=active,
        selection_context=selection,
        manufacturing_intent=manufacturing,
        existing_document_structure=structure,
        available_engines=available,
        current_engine=clean_current,
        strategy_lock=lock,
        reliability=normalized_reliability,
    )


def _result(
    routing_request: CapabilityRoutingRequest,
    *,
    engine: str,
    target_workbench: str,
    reason: str,
    explanation: str,
    preserved: bool,
    automatic: bool = True,
    evidence: Mapping[str, Any] | None = None,
) -> CapabilityRoute:
    request_summary = routing_request.summary()
    route_evidence = {
        "policy": RELIABILITY_POLICY,
        "capability_category": routing_request.capability_category,
        "selection_present": bool(
            int(routing_request.selection_context.get("selection_count") or 0)
        ),
        "manufacturing_intent_present": bool(routing_request.manufacturing_intent),
        "reliability": routing_request.reliability,
        **dict(evidence or {}),
    }
    content = {
        "schema": ROUTER_SCHEMA,
        "version": ROUTER_VERSION,
        "engine": engine,
        "workbench": target_workbench,
        "target_workbench": target_workbench,
        "reason_code": reason,
        "explanation": explanation,
        "preserved_existing_structure": preserved,
        "automatic": automatic,
        "request": request_summary,
        "evidence": route_evidence,
    }
    route_id = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CapabilityRoute(route_id=route_id, **content)


def route_capability(
    request: str | CapabilityRoutingRequest | Mapping[str, Any],
    *,
    workbench: str | None = None,
    current_engine: str = "native",
    available_engines: Sequence[str] = ("native",),
    has_existing_geometry: bool = False,
    strategy_lock: str | None = None,
    capability_category: str | None = None,
    selection_context: Mapping[str, Any] | None = None,
    manufacturing_intent: Mapping[str, Any] | str | None = None,
    existing_document_structure: Mapping[str, Any] | None = None,
    reliability: Mapping[str, Any] | None = None,
) -> CapabilityRoute:
    """Choose one route without provider calls or hidden fallback behavior.

    The keyword arguments keep the version 1 caller API. New callers should
    pass a :class:`CapabilityRoutingRequest`.
    """

    if isinstance(request, CapabilityRoutingRequest):
        routing_request = request
    elif isinstance(request, Mapping):
        if (
            request.get("schema") != ROUTING_REQUEST_SCHEMA
            or int(request.get("version") or 0) != ROUTING_REQUEST_VERSION
        ):
            raise RuntimeError("The capability-routing request schema is invalid.")
        routing_request = make_routing_request(
            str(request.get("request") or ""),
            workbench=str(request.get("source_workbench") or ""),
            current_engine=str(request.get("current_engine") or ""),
            available_engines=list(request.get("available_engines") or []),
            strategy_lock=request.get("strategy_lock"),
            capability_category=str(request.get("capability_category") or ""),
            selection_context=request.get("selection_context"),
            manufacturing_intent=request.get("manufacturing_intent"),
            existing_document_structure=request.get("existing_document_structure"),
            reliability=request.get("reliability"),
        )
    else:
        routing_request = make_routing_request(
            str(request or ""),
            workbench=workbench,
            current_engine=current_engine,
            available_engines=available_engines,
            has_existing_geometry=has_existing_geometry,
            strategy_lock=strategy_lock,
            capability_category=capability_category,
            selection_context=selection_context,
            manufacturing_intent=manufacturing_intent,
            existing_document_structure=existing_document_structure,
            reliability=reliability,
        )

    if (
        routing_request.schema != ROUTING_REQUEST_SCHEMA
        or routing_request.version != ROUTING_REQUEST_VERSION
        or routing_request.capability_category not in CAPABILITY_CATEGORIES
    ):
        raise RuntimeError("The capability-routing request schema is invalid.")

    active = routing_request.source_workbench
    available = routing_request.available_engines
    current = routing_request.current_engine
    category = routing_request.capability_category
    target = target_workbench_for_category(
        category,
        active,
        has_existing_structure=bool(
            routing_request.existing_document_structure.get("has_geometry")
        ),
    )
    lock = routing_request.strategy_lock
    native_required = category in NATIVE_CAPABILITY_WORKBENCHES

    if lock and lock not in available:
        raise RuntimeError("The locked modeling strategy is not available.")
    if native_required:
        if lock and lock != "native":
            raise RuntimeError(
                "The locked modeling strategy cannot provide the required native capability."
            )
        if "native" not in available:
            raise RuntimeError("The requested professional CAD capability needs native tools.")
        return _result(
            routing_request,
            engine="native",
            target_workbench=target,
            reason="professional_native_capability",
            explanation="The request needs native professional CAD features.",
            preserved=False,
            automatic=not bool(lock),
            evidence={"decision_factor": "native_capability_required"},
        )

    if lock:
        return _result(
            routing_request,
            engine=lock,
            target_workbench=target,
            reason="advanced_lock",
            explanation="The project uses the advanced modeling-strategy lock.",
            preserved=bool(routing_request.existing_document_structure.get("has_geometry")),
            automatic=False,
            evidence={"decision_factor": "project_strategy_lock"},
        )

    structure = routing_request.existing_document_structure
    established = _clean_engine(structure.get("established_engine"))
    compatible = {
        str(item).strip().lower()
        for item in list(structure.get("compatible_capabilities") or [])
    }
    if (
        bool(structure.get("has_geometry"))
        and category in COMPATIBLE_FOLLOW_UP_CATEGORIES
        and category in compatible
        and established in available
    ):
        return _result(
            routing_request,
            engine=established,
            target_workbench=target,
            reason="preserve_document_structure",
            explanation="The route keeps the document's established compatible editable structure.",
            preserved=True,
            evidence={"decision_factor": "compatible_established_engine"},
        )

    reliability_input = routing_request.reliability
    native_score = float(reliability_input.get("native", {}).get("score") or 0.0)
    build123d_score = float(reliability_input.get("build123d", {}).get("score") or 0.0)
    build123d_source = str(reliability_input.get("build123d", {}).get("source") or "")
    if (
        category == "functional_part"
        and target == "PartDesignWorkbench"
        and "build123d" in available
        and build123d_source not in {"", "missing_data"}
        and build123d_score > native_score
    ):
        return _result(
            routing_request,
            engine="build123d",
            target_workbench=target,
            reason="reliable_functional_part",
            explanation="Measured reliability selects build123d for this functional part.",
            preserved=False,
            evidence={"decision_factor": "explicit_reliability_advantage"},
        )

    if "native" in available:
        return _result(
            routing_request,
            engine="native",
            target_workbench=target,
            reason="native_editability_default",
            explanation="Native parametric CAD is the safe editable default.",
            preserved=False,
            evidence={"decision_factor": "safe_native_default"},
        )
    if current in available:
        selected = current
    else:
        selected = available[0]
    return _result(
        routing_request,
        engine=selected,
        target_workbench=target,
        reason="available_surface",
        explanation="The route uses the available CAD surface for this workbench.",
        preserved=False,
        evidence={"decision_factor": "only_available_surface"},
    )


def normalize_route_record(route: Mapping[str, Any]) -> dict[str, Any]:
    """Validate version 2 or migrate a persisted version 1 route record."""

    raw = _json_object(route)
    schema = str(raw.get("schema") or "")
    if schema == ROUTER_SCHEMA:
        try:
            version = int(raw.get("version") or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("The capability route version is invalid.") from exc
        if version != ROUTER_VERSION:
            raise RuntimeError("The capability route version is invalid.")
        required = {
            "route_id", "engine", "target_workbench", "reason_code", "request", "evidence"
        }
        if not required.issubset(raw) or not str(raw.get("route_id") or ""):
            raise RuntimeError("The capability route schema is invalid.")
        content = {key: value for key, value in raw.items() if key != "route_id"}
        expected_id = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if str(raw.get("route_id")) != expected_id:
            raise RuntimeError("The capability route content hash is invalid.")
        route_request = raw.get("request")
        evidence = raw.get("evidence")
        if not isinstance(route_request, dict) or not isinstance(evidence, dict):
            raise RuntimeError("The capability route schema is invalid.")
        if (
            route_request.get("schema") != ROUTING_REQUEST_SCHEMA
            or int(route_request.get("version") or 0) != ROUTING_REQUEST_VERSION
            or str(route_request.get("capability_category") or "")
            not in CAPABILITY_CATEGORIES
            or _clean_engine(raw.get("engine")) not in KNOWN_ENGINES
            or not str(raw.get("target_workbench") or "").strip()
            or str(raw.get("workbench") or "")
            != str(raw.get("target_workbench") or "")
        ):
            raise RuntimeError("The capability route schema is invalid.")
        return raw
    if schema != LEGACY_ROUTER_SCHEMA or not str(raw.get("route_id") or ""):
        raise RuntimeError("The capability route schema is invalid.")

    engine = _clean_engine(raw.get("engine")) or "native"
    workbench = str(raw.get("workbench") or "PartDesignWorkbench")
    legacy_request = make_routing_request(
        "",
        workbench=workbench,
        current_engine=engine,
        available_engines=[engine],
        has_existing_geometry=bool(raw.get("preserved_existing_structure")),
        capability_category="part_edit" if raw.get("preserved_existing_structure") else "part_design",
    )
    migrated = _result(
        legacy_request,
        engine=engine,
        target_workbench=workbench,
        reason=str(raw.get("reason_code") or "legacy_route"),
        explanation=str(raw.get("explanation") or "Migrated version 1 capability route."),
        preserved=bool(raw.get("preserved_existing_structure")),
        automatic=bool(raw.get("automatic", True)),
        evidence={
            "decision_factor": "migrated_v1_route",
            "legacy_route_id": str(raw.get("route_id")),
            "legacy_schema": LEGACY_ROUTER_SCHEMA,
        },
    )
    return migrated.summary()
