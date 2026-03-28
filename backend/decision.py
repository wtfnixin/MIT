"""
decision.py — KubeResilience Decision Engine
Translates detection output (service, confidence, triggered, metrics,
vote_buffer) into a structured DecisionResult with a full audit trail.

Shared decision state is stored in a small SQLite database so multiple
FastAPI/Uvicorn workers can observe the same cooldown and blast-radius view.
Baseline statistics are loaded from ``models/baseline_stats.json`` once at
import time and cached in the module-level ``_baseline_stats`` dict.

Python 3.11+  |  FastAPI-compatible (no event-loop assumptions)
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from service_catalog import (
    AUTO_REMEDIABLE_SCENARIOS,
    BASELINE_STATS_PATH,
    CRITICAL_SERVICES,
)

# Logging
logger = logging.getLogger(__name__)

# Constants

#: Seconds after which a degraded-service entry expires from blast-radius state.
BLAST_RADIUS_TTL_SECONDS: int = 90

#: Minimum number of simultaneously degraded services to flag a cluster-wide event.
BLAST_RADIUS_THRESHOLD: int = 2

#: Minimum confidence (0–100) required before any automated action is taken.
CONFIDENCE_THRESHOLD: float = 80.0

#: Path to baseline statistics JSON relative to this file's location.
_BASELINE_STATS_PATH: Path = BASELINE_STATS_PATH

#: Shared SQLite state backing cooldown and blast-radius tracking.
_STATE_DB_PATH: Path = Path(
    os.environ.get(
        "KUBERESILIENCE_STATE_DB_PATH",
        str(Path(__file__).resolve().parent.parent / "data" / "decision_state.sqlite3"),
    )
)


#: Weights used in severity scoring — must sum to 1.0.
_SEVERITY_WEIGHTS: dict[str, float] = {
    "p95_latency_ms": 0.40,
    "error_rate_pct": 0.35,
    "cpu_cores": 0.15,
    "memory_mb": 0.10,
}

#: Base cooldown durations (seconds) keyed by severity label.
_BASE_COOLDOWNS: dict[str, int] = {
    "critical": 300,
    "high": 180,
    "moderate": 120,
    "low": 60,
}

_REQUIRED_METRIC_KEYS: tuple[str, ...] = (
    "p95_latency_ms",
    "error_rate_pct",
    "cpu_cores",
    "memory_mb",
)

# Type aliases & TypedDicts

MetricsDict = dict[str, float]
"""Expected keys: p95_latency_ms, error_rate_pct, cpu_cores, memory_mb"""

ActionLiteral = Literal["WAIT", "RECOVER", "ESCALATE"]
SeverityLiteral = Literal["low", "moderate", "high", "critical"]
VerificationStatus = Literal["HEALED", "FAILED"]


class ServiceBaseline(TypedDict):
    """Per-service baseline statistics loaded from baseline_stats.json."""

    p95_latency_ms_mean: float
    p95_latency_ms_std: float
    error_rate_pct_mean: float
    error_rate_pct_std: float
    cpu_cores_mean: float
    cpu_cores_std: float
    memory_mb_mean: float
    memory_mb_std: float


class CooldownEntry(TypedDict):
    """Entry stored in :data:`cooldown_tracker` for a given service."""

    timestamp: float          # UNIX epoch from time.monotonic() equivalent — we use time.time()
    severity_label: SeverityLiteral
    was_healed: bool


class VerificationResult(TypedDict):
    """Minimal contract expected from the recovery verifier."""

    status: VerificationStatus
    detail: str


# Shared state and baseline stats


def _load_baseline_stats() -> dict[str, ServiceBaseline]:
    """Load and return baseline statistics from ``models/baseline_stats.json``.

    Returns
    -------
    dict[str, ServiceBaseline]
        Mapping of service name → baseline dict with mean/std for every feature.

    Raises
    ------
    FileNotFoundError
        If the JSON file cannot be found at the expected path.
    ValueError
        If the JSON is malformed or missing required keys.
    """
    if not _BASELINE_STATS_PATH.exists():
        raise FileNotFoundError(
            f"Baseline stats not found at '{_BASELINE_STATS_PATH}'. "
            "Ensure models/baseline_stats.json is present before starting."
        )

    with _BASELINE_STATS_PATH.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = json.load(fh)

    required_keys = set(
        f"{feat}_{stat}"
        for feat in ("p95_latency_ms", "error_rate_pct", "cpu_cores", "memory_mb")
        for stat in ("mean", "std")
    )

    for service, stats in raw.items():
        missing = required_keys - stats.keys()
        if missing:
            raise ValueError(
                f"baseline_stats.json entry for '{service}' is missing keys: {missing}"
            )

    logger.info(
        "Loaded baseline stats for %d service(s) from '%s'.",
        len(raw),
        _BASELINE_STATS_PATH,
    )
    return raw  # type: ignore[return-value]


def _get_state_connection() -> sqlite3.Connection:
    _STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_STATE_DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_state_db() -> None:
    with _get_state_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cooldown_tracker (
                service TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                severity_label TEXT NOT NULL,
                was_healed INTEGER NOT NULL CHECK (was_healed IN (0, 1))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS degraded_services (
                service TEXT PRIMARY KEY,
                timestamp REAL NOT NULL
            )
            """
        )


#: Cached baseline stats — populated at import time.
_baseline_stats: dict[str, ServiceBaseline] = _load_baseline_stats()
_AUTO_REMEDIABLE_SCENARIOS: frozenset[str] = AUTO_REMEDIABLE_SCENARIOS

_init_state_db()



# Data contract — DecisionResult


@dataclass(slots=True)
class DecisionResult:
    """Structured output returned by :func:`make_decision`.

    Attributes
    ----------
    action:
        The recommended action: ``WAIT``, ``RECOVER``, or ``ESCALATE``.
    reason:
        Human-readable explanation surfaced in the CLI and dashboard.
    scenario_type:
        Inferred fault type (e.g. ``pod_kill``, ``cpu_stress``).
    severity_label:
        Categorical severity: ``low``, ``moderate``, ``high``, ``critical``.
    severity_score:
        Normalised severity 0.0–1.0 (magnitude, not confidence).
    confidence:
        Detection confidence 0.0–100.0 passed in from the detector.
    cooldown_remaining:
        Seconds remaining in cooldown, or 0 if not cooling down.
    timestamp:
        ISO-8601 UTC timestamp of when this decision was made.
    audit_log:
        Ordered list of strings describing each evaluation step taken.
    """

    action: ActionLiteral
    reason: str
    scenario_type: str = "unknown"
    severity_label: SeverityLiteral = "low"
    severity_score: float = 0.0
    confidence: float = 0.0
    cooldown_remaining: int = 0
    timestamp: str = field(default_factory=lambda: _utc_iso_now())
    audit_log: list[str] = field(default_factory=list)


# Helpers


def _utc_iso_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _now() -> float:
    """Return the current UNIX timestamp (wall clock, not monotonic).

    Using ``time.time()`` so that timestamps stored in module-level dicts can
    be compared across calls without drift concerns for our TTL windows.
    """
    return time.time()


def _get_baseline(service: str) -> ServiceBaseline:
    """Retrieve baseline stats for *service*, raising a clear error on miss.

    Parameters
    ----------
    service:
        Service name to look up (must match a key in baseline_stats.json).

    Raises
    ------
    KeyError
        If the service is not present in the loaded baseline stats.
    """
    if service not in _baseline_stats:
        raise KeyError(
            f"No baseline stats for service '{service}'. "
            f"Available services: {sorted(_baseline_stats.keys())}"
        )
    return _baseline_stats[service]


def _sanitize_metrics(metrics: dict[str, Any]) -> tuple[MetricsDict, list[str]]:
    """Coerce metric values to finite floats and report any missing fields."""
    sanitized: MetricsDict = {}
    missing_fields: list[str] = []

    for key in _REQUIRED_METRIC_KEYS:
        raw_value = metrics.get(key)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            missing_fields.append(key)
            continue

        if math.isnan(value) or math.isinf(value):
            missing_fields.append(key)
            continue

        sanitized[key] = value

    return sanitized, missing_fields


# Module 1 — Scenario Classifier


def classify_scenario(metrics: MetricsDict, service: str) -> str:
    """Infer the most likely fault type from the current metric ratios.

    Compares normalised metric values against the per-service baseline to
    identify the dominant failure pattern.

    Parameters
    ----------
    metrics:
        Live metric dict with keys ``p95_latency_ms``, ``error_rate_pct``,
        ``cpu_cores``, ``memory_mb``.
    service:
        Service name used to load the correct baseline from
        :data:`_baseline_stats`.

    Returns
    -------
    str
        One of ``cpu_stress``, ``memory_stress``, ``pod_kill``,
        ``network_latency``, ``packet_loss``, or ``unknown``.
    """
    baseline = _get_baseline(service)

    latency_ratio: float = metrics["p95_latency_ms"] / baseline["p95_latency_ms_mean"]

    # Guard divide-by-zero for error_rate baseline
    error_base: float = baseline["error_rate_pct_mean"] or 1e-6
    error_ratio: float = metrics["error_rate_pct"] / error_base

    cpu_ratio: float = metrics["cpu_cores"] / baseline["cpu_cores_mean"]
    memory_ratio: float = metrics["memory_mb"] / baseline["memory_mb_mean"]

    logger.debug(
        "[%s] Ratios — latency=%.2f  error=%.2f  cpu=%.2f  memory=%.2f",
        service,
        latency_ratio,
        error_ratio,
        cpu_ratio,
        memory_ratio,
    )

    # Decision tree (order matters — most specific rules first)

    if cpu_ratio > 7.0 and latency_ratio > 2.0:
        return "cpu_stress"

    if memory_ratio > 5.0 and latency_ratio > 2.0:
        return "memory_leak"

    if latency_ratio > 7.0 and error_ratio > 3.0 and cpu_ratio < 0.5:
        return "pod_kill"

    if latency_ratio > 6.0 and error_ratio > 3.0 and 0.8 <= cpu_ratio <= 1.2:
        # cpu_ratio NEAR 1.0 → within ±20 % of normal
        return "network_latency"

    if latency_ratio > 3.0 and error_ratio > 5.0 and 1.0 < cpu_ratio <= 2.0:
        # cpu_ratio "slightly elevated" → between 1× and 2× baseline
        return "packet_loss"

    return "unknown"


# Module 2 — Severity Scorer


def compute_severity(
    metrics: MetricsDict, service: str
) -> tuple[SeverityLiteral, float]:
    """Compute severity label and normalised score from metric deviations.

    Severity measures *magnitude* (how far metrics are from normal), whereas
    the detector's confidence measures *persistence* (how consistently the
    anomaly has been seen).

    Parameters
    ----------
    metrics:
        Live metric dict (same format as :func:`classify_scenario`).
    service:
        Service name used to resolve the correct baseline.

    Returns
    -------
    tuple[SeverityLiteral, float]
        ``(severity_label, normalised_score_0_to_1)``
    """
    baseline = _get_baseline(service)

    features: list[tuple[str, float, float, float]] = [
        # (key, metric_value, baseline_mean, baseline_std)
        (
            "p95_latency_ms",
            metrics["p95_latency_ms"],
            baseline["p95_latency_ms_mean"],
            baseline["p95_latency_ms_std"],
        ),
        (
            "error_rate_pct",
            metrics["error_rate_pct"],
            baseline["error_rate_pct_mean"],
            baseline["error_rate_pct_std"],
        ),
        (
            "cpu_cores",
            metrics["cpu_cores"],
            baseline["cpu_cores_mean"],
            baseline["cpu_cores_std"],
        ),
        (
            "memory_mb",
            metrics["memory_mb"],
            baseline["memory_mb_mean"],
            baseline["memory_mb_std"],
        ),
    ]

    weighted_sum: float = 0.0
    for key, value, mean, std in features:
        if std == 0.0:
            std = 1e-6  # guard against zero-std baseline
        deviation = abs(value - mean) / std
        deviation = min(deviation, 10.0)  # cap outlier dominance
        weighted_sum += deviation * _SEVERITY_WEIGHTS[key]
        logger.debug("[%s] %s deviation=%.2f (weight=%.2f)", service, key, deviation, _SEVERITY_WEIGHTS[key])

    # Normalise: max possible weighted_sum is 10.0 (all features capped at 10)
    normalised: float = min(weighted_sum / 10.0, 1.0)

    if normalised >= 0.75:
        label: SeverityLiteral = "critical"
    elif normalised >= 0.45:
        label = "high"
    elif normalised >= 0.20:
        label = "moderate"
    else:
        label = "low"

    logger.debug("[%s] Severity: %s (score=%.3f)", service, label, normalised)
    return label, round(normalised, 4)


# Module 3 — Blast Radius Guard


def _prune_degraded_services(conn: sqlite3.Connection, now: float) -> None:
    conn.execute(
        "DELETE FROM degraded_services WHERE timestamp < ?",
        (now - BLAST_RADIUS_TTL_SECONDS,),
    )


def get_degraded_services() -> list[str]:
    """Return the currently degraded services after expiring stale entries."""
    now = _now()
    with _get_state_connection() as conn:
        _prune_degraded_services(conn, now)
        rows = conn.execute(
            "SELECT service FROM degraded_services ORDER BY service"
        ).fetchall()
    return [str(row[0]) for row in rows]


def update_blast_radius(service: str, triggered: bool) -> list[str]:
    """Update shared blast-radius state and return the current degraded services.

    Parameters
    ----------
    service:
        The service being evaluated in this detection cycle.
    triggered:
        Whether the detector considers this service currently anomalous.
    """
    now = _now()
    with _get_state_connection() as conn:
        _prune_degraded_services(conn, now)

        if triggered:
            existing = conn.execute(
                "SELECT 1 FROM degraded_services WHERE service = ?",
                (service,),
            ).fetchone()
            if existing is None:
                logger.info("[BlastRadius] Marking '%s' as degraded (new entry).", service)
            conn.execute(
                "INSERT OR REPLACE INTO degraded_services(service, timestamp) VALUES(?, ?)",
                (service, now),
            )
        else:
            conn.execute(
                "DELETE FROM degraded_services WHERE service = ?",
                (service,),
            )

        rows = conn.execute(
            "SELECT service FROM degraded_services ORDER BY service"
        ).fetchall()
    return [str(row[0]) for row in rows]


def is_blast_radius_exceeded() -> tuple[bool, str]:
    """Check whether the number of simultaneously degraded services is too high.

    If two or more distinct services are concurrently degraded the likely cause
    is a cluster-wide event and automated recovery should not run on any of them.

    Returns
    -------
    tuple[bool, str]
        ``(exceeded, reason)`` — ``reason`` is ``"ok"`` when not exceeded.
    """
    degraded = get_degraded_services()
    count = len(degraded)
    if count >= BLAST_RADIUS_THRESHOLD:
        reason = "cluster_wide_event_suspected"
        logger.warning(
            "[BlastRadius] %d services degraded simultaneously → %s: %s",
            count,
            reason,
            degraded,
        )
        return True, reason
    return False, "ok"


# Module 4 — Adaptive Cooldown


def get_cooldown_duration(
    severity_label: SeverityLiteral,
    last_verification_status: VerificationStatus | None,
) -> float:
    """Calculate the effective cooldown for the next action on a service.

    Verified heals shorten the cooldown (system proved it can self-heal);
    failed recoveries lengthen it (be cautious before trying again).

    Parameters
    ----------
    severity_label:
        Severity of the *last* event that triggered a recovery attempt.
    last_verification_status:
        Result of the last recovery attempt: ``"HEALED"``, ``"FAILED"``, or
        ``None`` if there is no recorded verification yet.

    Returns
    -------
    float
        Effective cooldown in seconds (float to preserve fractional seconds
        from the ±% adjustments).
    """
    base: float = float(_BASE_COOLDOWNS.get(severity_label, 120))

    if last_verification_status == "HEALED":
        adjusted = base * 0.75  # 25 % reduction — system proved recovery works
    elif last_verification_status == "FAILED":
        adjusted = base * 1.50  # 50 % increase — last attempt didn't work
    else:
        adjusted = base

    logger.debug(
        "[Cooldown] severity=%s  verification=%s  base=%.0fs  adjusted=%.0fs",
        severity_label,
        last_verification_status,
        base,
        adjusted,
    )
    return adjusted


def is_cooldown_active(service: str) -> tuple[bool, int]:
    """Check whether *service* is still within its adaptive cooldown window.

    Parameters
    ----------
    service:
        Service name to check.

    Returns
    -------
    tuple[bool, int]
        ``(active, remaining_seconds)`` — ``remaining_seconds`` is 0 when not
        in cooldown.
    """
    with _get_state_connection() as conn:
        row = conn.execute(
            """
            SELECT timestamp, severity_label, was_healed
            FROM cooldown_tracker
            WHERE service = ?
            """,
            (service,),
        ).fetchone()

        if row is None:
            return False, 0

        entry = CooldownEntry(
            timestamp=float(row[0]),
            severity_label=str(row[1]),
            was_healed=bool(row[2]),
        )
        verification_status: VerificationStatus | None = (
            "HEALED" if entry["was_healed"] else "FAILED"
        )
        required = get_cooldown_duration(entry["severity_label"], verification_status)
        elapsed = _now() - entry["timestamp"]

        if elapsed < required:
            remaining = int(required - elapsed)
            logger.debug(
                "[Cooldown] '%s' is in cooldown — %.0fs remaining.",
                service,
                remaining,
            )
            return True, remaining

        conn.execute("DELETE FROM cooldown_tracker WHERE service = ?", (service,))
        return False, 0


def record_action(
    service: str,
    severity_label: SeverityLiteral,
    verification_status: VerificationStatus,
) -> None:
    """Persist the outcome of a recovery attempt for adaptive cooldown tracking.

    Parameters
    ----------
    service:
        Service that was recovered.
    severity_label:
        Severity label from the event that triggered the recovery.
    verification_status:
        Outcome of the post-recovery health check (``HEALED`` or ``FAILED``).
    """
    with _get_state_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO cooldown_tracker(service, timestamp, severity_label, was_healed)
            VALUES(?, ?, ?, ?)
            """,
            (
                service,
                _now(),
                severity_label,
                1 if verification_status == "HEALED" else 0,
            ),
        )
    logger.info(
        "[Cooldown] Recorded action for '%s': severity=%s, healed=%s.",
        service,
        severity_label,
        verification_status == "HEALED",
    )


def _force_set_cooldown(service: str, duration: float, reason: str) -> None:
    """Force a specific cooldown duration for *service* regardless of severity.

    This is used internally when a recovery attempt fails and the service must
    be frozen to prevent automated thrashing.

    Parameters
    ----------
    service:
        Service to freeze.
    duration:
        Cooldown length in seconds (written as a ``critical`` entry that
        naturally produces >= 300 s — we override via direct timestamp shift).
    reason:
        Human-readable reason for the forced cooldown (logged only).
    """
    # We model forced cooldowns as a "critical / FAILED" entry whose timestamp
    # is rolled back far enough that the adaptive formula produces *at least*
    # `duration` seconds of remaining cooldown.
    #
    # base for (critical, FAILED) = 300 * 1.5 = 450 s
    # We want remaining = duration. remaining = required - elapsed.
    # duration = 450 - (_now() - timestamp) => timestamp = _now() - (450 - duration)
    forced_timestamp = _now() - (450.0 - duration)
    with _get_state_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO cooldown_tracker(service, timestamp, severity_label, was_healed)
            VALUES(?, ?, ?, ?)
            """,
            (service, forced_timestamp, "critical", 0),
        )
    logger.warning(
        "[Cooldown] FORCED cooldown for '%s' (%.0fs, reason=%s).",
        service,
        duration,
        reason,
    )


# Module 5 — Main Decision Function


def make_decision(
    service: str,
    confidence: float,
    triggered: bool,
    metrics: MetricsDict,
    vote_buffer: list[Any],
) -> DecisionResult:
    """Core decision function — called once per detection cycle per service.

    Evaluates all gates in order (fail-fast) and returns a :class:`DecisionResult`
    with a complete audit trail.

    Gate evaluation order:
      1. ``triggered`` flag — anomaly must be persistent, not transient.
      2. Confidence threshold — must be ≥ 80 % to act.
      3. Critical service guard — ``checkoutservice`` / ``frontend`` always escalate.
      4. Blast radius guard — halt automation during cluster-wide events.
      5. Adaptive cooldown — prevent spam recovery after a recent action.
      6. **All gates passed → RECOVER**.

    Parameters
    ----------
    service:
        Service name (e.g. ``"cartservice"``).
    confidence:
        Anomaly confidence from the detector (0.0–100.0).
    triggered:
        Whether the detector's vote buffer considers the anomaly persistent.
    metrics:
        Dict with keys ``p95_latency_ms``, ``error_rate_pct``, ``cpu_cores``,
        ``memory_mb``.
    vote_buffer:
        Raw vote buffer from the detector — stored in the audit log only.

    Returns
    -------
    DecisionResult
        Fully populated result including ``action``, ``reason``, ``severity``,
        ``scenario_type``, and ``audit_log``.
    """
    audit: list[str] = []
    audit.append(
        f"[0] make_decision called | service={service} "
        f"confidence={confidence:.1f} triggered={triggered} "
        f"vote_buffer_len={len(vote_buffer)}"
    )
    scenario_type = "unknown"
    severity_label: SeverityLiteral = "low"
    severity_score = 0.0

    sanitized_metrics, missing_metrics = _sanitize_metrics(metrics)
    audit.append(
        f"[1] Metrics sanitised | missing_fields={missing_metrics or ['none']}"
    )

    degraded_snapshot = update_blast_radius(service, triggered)
    audit.append(
        f"[2] BlastRadius updated | degraded_services={degraded_snapshot}"
    )

    if not triggered:
        audit.append("[3] GATE FAILED - anomaly not persistent (triggered=False)")
        return DecisionResult(
            action="WAIT",
            reason="anomaly_not_persistent",
            scenario_type=scenario_type,
            severity_label=severity_label,
            severity_score=severity_score,
            confidence=confidence,
            audit_log=audit,
        )
    audit.append("[3] Gate passed - anomaly is triggered")

    if confidence < CONFIDENCE_THRESHOLD:
        audit.append(
            f"[4] GATE FAILED - confidence {confidence:.1f} < {CONFIDENCE_THRESHOLD}"
        )
        return DecisionResult(
            action="WAIT",
            reason="confidence_below_threshold",
            scenario_type=scenario_type,
            severity_label=severity_label,
            severity_score=severity_score,
            confidence=confidence,
            audit_log=audit,
        )
    audit.append(f"[4] Gate passed - confidence {confidence:.1f} >= {CONFIDENCE_THRESHOLD}")

    if service in CRITICAL_SERVICES:
        audit.append(f"[5] GATE FAILED - '{service}' is a critical service -> ESCALATE")
        return DecisionResult(
            action="ESCALATE",
            reason="critical_service_manual_only",
            scenario_type=scenario_type,
            severity_label=severity_label,
            severity_score=severity_score,
            confidence=confidence,
            audit_log=audit,
        )
    audit.append(f"[5] Gate passed - '{service}' is not in CRITICAL_SERVICES")

    if service not in _baseline_stats:
        audit.append(f"[6] GATE FAILED - no baseline stats for '{service}' -> ESCALATE")
        return DecisionResult(
            action="ESCALATE",
            reason="service_not_modeled",
            scenario_type=scenario_type,
            severity_label=severity_label,
            severity_score=severity_score,
            confidence=confidence,
            audit_log=audit,
        )
    audit.append(f"[6] Gate passed - '{service}' has baseline stats")

    if missing_metrics:
        audit.append(
            f"[7] GATE FAILED - missing required metrics for decision: {missing_metrics}"
        )
        return DecisionResult(
            action="ESCALATE",
            reason="missing_required_metrics",
            scenario_type=scenario_type,
            severity_label=severity_label,
            severity_score=severity_score,
            confidence=confidence,
            audit_log=audit,
        )
    audit.append("[7] Gate passed - required metrics are complete")

    scenario_type = classify_scenario(sanitized_metrics, service)
    severity_label, severity_score = compute_severity(sanitized_metrics, service)
    audit.append(
        f"[8] Classification | scenario={scenario_type} "
        f"severity={severity_label} score={severity_score:.4f}"
    )

    if scenario_type not in _AUTO_REMEDIABLE_SCENARIOS:
        audit.append(
            f"[9] GATE FAILED - scenario '{scenario_type}' is not auto-remediable -> ESCALATE"
        )
        return DecisionResult(
            action="ESCALATE",
            reason="scenario_not_auto_remediable",
            scenario_type=scenario_type,
            severity_label=severity_label,
            severity_score=severity_score,
            confidence=confidence,
            audit_log=audit,
        )
    audit.append(f"[9] Gate passed - scenario '{scenario_type}' is auto-remediable")

    blast_exceeded, blast_reason = is_blast_radius_exceeded()
    if blast_exceeded:
        audit.append(
            f"[10] GATE FAILED - blast radius exceeded: {blast_reason} "
            f"degraded={get_degraded_services()}"
        )
        return DecisionResult(
            action="ESCALATE",
            reason=blast_reason,
            scenario_type=scenario_type,
            severity_label=severity_label,
            severity_score=severity_score,
            confidence=confidence,
            audit_log=audit,
        )
    audit.append("[10] Gate passed - blast radius within safe limits")

    cooldown_active, cooldown_remaining = is_cooldown_active(service)
    if cooldown_active:
        audit.append(
            f"[11] GATE FAILED - cooldown active for '{service}', "
            f"{cooldown_remaining}s remaining"
        )
        return DecisionResult(
            action="WAIT",
            reason="cooldown_active",
            scenario_type=scenario_type,
            severity_label=severity_label,
            severity_score=severity_score,
            confidence=confidence,
            cooldown_remaining=cooldown_remaining,
            audit_log=audit,
        )
    audit.append("[11] Gate passed - no active cooldown")

    audit.append("[12] ALL GATES PASSED -> action=RECOVER")
    return DecisionResult(
        action="RECOVER",
        reason="all_gates_passed",
        scenario_type=scenario_type,
        severity_label=severity_label,
        severity_score=severity_score,
        confidence=confidence,
        cooldown_remaining=0,
        audit_log=audit,
    )


# Module 6 — Post-Recovery Hook


def on_recovery_complete(
    service: str,
    verification_result: VerificationResult,
    severity_label: SeverityLiteral,
) -> None:
    """Called by the recovery controller after pod restarts and verifier runs.

    Updates the cooldown tracker with the real-world outcome so that future
    decisions benefit from accurate recovery history.

    If the last recovery *failed*, automation for this service is frozen for
    10 minutes to prevent thrashing and a loud CLI warning is emitted.

    Parameters
    ----------
    service:
        Service that was recovered.
    verification_result:
        Dict with at least ``status`` (``"HEALED"`` or ``"FAILED"``) and
        ``detail`` (human-readable message from the verifier).
    severity_label:
        Severity label from the triggering decision — stored for adaptive
        cooldown calculation on the next cycle.
    """
    status: VerificationStatus = verification_result["status"]
    detail: str = verification_result.get("detail", "")

    record_action(service, severity_label, status)
    logger.info(
        "[Recovery] '%s' verification=%s detail='%s'.", service, status, detail
    )

    if status == "FAILED":
        _force_set_cooldown(
            service,
            duration=600.0,
            reason="recovery_failed",
        )
        # Loud CLI-visible warning — also propagated via logger
        warning_msg = (
            f"\n{'='*60}\n"
            f"  ⚠  AUTOMATION FROZEN for '{service}'\n"
            f"  Manual intervention required.\n"
            f"  Detail: {detail}\n"
            f"{'='*60}\n"
        )
        logger.warning(warning_msg)
        print(warning_msg, flush=True)  # ensure visibility in non-logging CLIs