from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone

import requests

from config import DEMO_MODE, KUBE_NAMESPACE, PROMETHEUS_TIMEOUT_SECONDS, PROMETHEUS_URL
from service_catalog import BASELINE_STATS_PATH, get_supported_services

HEADERS = {"ngrok-skip-browser-warning": "true"}
TIMEOUT = PROMETHEUS_TIMEOUT_SECONDS
NAMESPACE = KUBE_NAMESPACE
SERVICES = get_supported_services()
_CONNECTIVITY_TTL_SECONDS = 15.0
_last_connectivity_check = 0.0
_last_connectivity_ok = False
_demo_chaos_state: dict[str, str] = {}


def _load_demo_baselines() -> dict[str, dict]:
    try:
        with BASELINE_STATS_PATH.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            return {str(service): dict(stats) for service, stats in raw.items()}
    except Exception:
        pass
    return {}


_demo_baselines = _load_demo_baselines()


def _missing_metrics_payload(service: str) -> dict:
    return {
        "service": service,
        "p95_latency_ms": None,
        "error_rate_pct": None,
        "cpu_cores": None,
        "memory_mb": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "all_available": False,
        "missing_fields": [
            "p95_latency_ms",
            "error_rate_pct",
            "cpu_cores",
            "memory_mb",
        ],
    }


def _demo_metric_value(service: str, key: str, fallback: float) -> float:
    stats = _demo_baselines.get(service, {})
    return float(stats.get(key, fallback))


def _demo_metrics(service: str) -> dict:
    latency = _demo_metric_value(service, "p95_latency_ms_mean", 10.0)
    error_rate = _demo_metric_value(service, "error_rate_pct_mean", 0.0)
    cpu = _demo_metric_value(service, "cpu_cores_mean", 0.02)
    memory = _demo_metric_value(service, "memory_mb_mean", 64.0)
    scenario = _demo_chaos_state.get(service)

    if scenario == "cpu_stress":
        latency *= 2.8
        error_rate = max(error_rate, 3.5)
        cpu = max(cpu * 8.0, cpu + 0.05)
        memory *= 1.3
    elif scenario == "memory_leak":
        latency *= 2.6
        error_rate = max(error_rate, 2.5)
        cpu *= 1.2
        memory = max(memory * 6.0, memory + 128.0)
    elif scenario == "network_latency":
        latency *= 6.5
        error_rate = max(error_rate, 4.0)
        cpu *= 1.0
    elif scenario == "packet_loss":
        latency *= 4.0
        error_rate = max(error_rate, 6.0)
        cpu *= 1.5
    elif scenario == "pod_kill":
        latency *= 8.0
        error_rate = max(error_rate, 8.0)
        cpu *= 0.3
        memory *= 0.9

    return {
        "service": service,
        "p95_latency_ms": round(latency, 4),
        "error_rate_pct": round(error_rate, 4),
        "cpu_cores": round(cpu, 6),
        "memory_mb": round(memory, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "all_available": True,
        "missing_fields": [],
    }


def set_demo_chaos(service: str, scenario: str) -> None:
    _demo_chaos_state[service] = scenario


def clear_demo_chaos(service: str | None = None) -> None:
    if service is None:
        _demo_chaos_state.clear()
        return
    _demo_chaos_state.pop(service, None)


def _probe_prometheus(force: bool = False) -> bool:
    global _last_connectivity_check, _last_connectivity_ok

    if not PROMETHEUS_URL:
        return False

    now = time.monotonic()
    if not force and (now - _last_connectivity_check) < _CONNECTIVITY_TTL_SECONDS:
        return _last_connectivity_ok

    _last_connectivity_check = now

    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": "up"},
            headers=HEADERS,
            timeout=min(TIMEOUT, 0.75),
        )
        response.raise_for_status()
        data = response.json()
        _last_connectivity_ok = data.get("status") == "success"
    except Exception:
        _last_connectivity_ok = False

    return _last_connectivity_ok


def _query(promql: str) -> float | None:
    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": promql},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        results = data["data"]["result"]
        if not results:
            return None
        val = float(results[0]["value"][1])
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    except Exception as e:
        print(f"[PROM WARN] query failed: {promql[:60]} — {e}")
        return None


def _get_latency(service: str) -> float | None:
    """
    Try Istio request duration first (requires Istio sidecar injection).
    Falls back to liveness-probe duration as a rough proxy.
    Both return p95 in milliseconds.
    """
    queries = [
        # Istio-native latency — works after: kubectl label namespace default istio-injection=enabled
        (
            f'histogram_quantile(0.95, sum(rate('
            f'istio_request_duration_milliseconds_bucket'
            f'{{reporter="destination", destination_service_namespace="{NAMESPACE}", '
            f'destination_app=~".*{service}.*"}}[1m])) by (le))'
        ),
        # Kubernetes liveness-probe duration (always present, coarser signal)
        (
            f'histogram_quantile(0.95, sum(rate('
            f'prober_probe_duration_seconds_bucket'
            f'{{namespace="{NAMESPACE}", pod=~".*{service}.*", probe_type="Liveness"}}[1m])) by (le))'
            f' * 1000'
        ),
    ]
    for query in queries:
        value = _query(query)
        if value is not None:
            return value
    print(f"[PROM WARN] latency not found for {service} — enable Istio sidecar injection")
    return None


def _get_error_rate(service: str) -> tuple[float | None, bool]:
    """
    Returns (error_rate_pct, was_found).
    Tries Istio error rate first, then liveness-probe failure rate.
    Returns (None, False) if no query succeeds — never masks failures as 0.0.
    """
    queries = [
        # Istio — non-5xx/429 success rate inverted to error %
        (
            f'(1 - sum(rate(istio_requests_total'
            f'{{reporter="destination", destination_service_namespace="{NAMESPACE}", '
            f'destination_app=~".*{service}.*", response_code!~"5..|429"}}[1m]))'
            f' / sum(rate(istio_requests_total'
            f'{{reporter="destination", destination_service_namespace="{NAMESPACE}", '
            f'destination_app=~".*{service}.*"}}[1m]))) * 100'
        ),
        # Liveness-probe failure rate
        (
            f'(1 - (sum(rate(prober_probe_total'
            f'{{namespace="{NAMESPACE}", pod=~".*{service}.*", '
            f'probe_type="Liveness", result="successful"}}[1m]))'
            f' / sum(rate(prober_probe_total'
            f'{{namespace="{NAMESPACE}", pod=~".*{service}.*", '
            f'probe_type="Liveness"}}[1m])))) * 100'
        ),
    ]
    for query in queries:
        value = _query(query)
        if value is not None:
            return value, True
    return None, False


def _get_cpu(service: str) -> float | None:
    """
    Returns CPU usage in cores (summed across all containers of the pod).
    NOTE: Do NOT filter by cpu="total" — that label does not exist in
    container_cpu_usage_seconds_total; it causes zero results.
    """
    query = (
        f'sum(rate(container_cpu_usage_seconds_total'
        f'{{namespace="{NAMESPACE}", pod=~".*{service}.*", container!=""}}[1m]))'
    )
    value = _query(query)
    if value is None:
        print(f"[PROM WARN] cpu not found for {service}")
    return value


def _get_memory(service: str) -> float | None:
    """Returns RSS memory in MiB."""
    query = (
        f'sum(container_memory_working_set_bytes'
        f'{{namespace="{NAMESPACE}", pod=~".*{service}.*", container!=""}}) / 1048576'
    )
    value = _query(query)
    if value is None:
        print(f"[PROM WARN] memory not found for {service}")
    return value


def fetch_metrics(service: str) -> dict:
    if DEMO_MODE or _demo_chaos_state.get(service):
        return _demo_metrics(service)

    if not PROMETHEUS_URL or not _probe_prometheus():
        return _missing_metrics_payload(service)

    try:
        lat = _get_latency(service)
        err, err_found = _get_error_rate(service)
        cpu = _get_cpu(service)
        mem = _get_memory(service)

        # Only treat error_rate as 0.0 when Prometheus explicitly returned 0.0.
        # If no query succeeded (err_found=False), keep it None so callers know
        # the metric is unavailable — not that the service is healthy.
        error_rate_final = err if err_found else None

        all_available = all(v is not None for v in [lat, error_rate_final, cpu, mem])
        missing = [
            key
            for key, value in {
                "p95_latency_ms": lat,
                "error_rate_pct": error_rate_final,
                "cpu_cores": cpu,
                "memory_mb": mem,
            }.items()
            if value is None
        ]

        return {
            "service": service,
            "p95_latency_ms": lat,
            "error_rate_pct": error_rate_final,
            "cpu_cores": cpu,
            "memory_mb": mem,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "all_available": all_available,
            "missing_fields": missing,
        }
    except Exception as e:
        print(f"[PROM ERROR] fetch_metrics({service}) crashed: {e}")
        return _missing_metrics_payload(service)


def validate_connection() -> bool:
    if DEMO_MODE:
        print("Prometheus demo mode enabled")
        return True

    if not PROMETHEUS_URL:
        print("Prometheus URL not configured")
        return False

    try:
        if _probe_prometheus(force=True):
            print(f"Prometheus reachable at {PROMETHEUS_URL}")
            return True
        print(f"Prometheus unreachable at {PROMETHEUS_URL}")
        return False
    except Exception as e:
        print(f"Prometheus unreachable: {e}")
        return False
