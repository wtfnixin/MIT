import os
import random

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

def fetch_metrics() -> dict:
    """
    Fetches feature metrics from teammate's Prometheus.
    MOCKED implementation since Prometheus is unavailable.
    Returns: dict with p95_latency, error_rate, cpu, memory
    """
    # Simulate realistic non-zero mock metrics.
    # Introduce random small variance to simulate a live environment.
    return {
        "p95_latency": round(random.uniform(50.0, 150.0), 2),  # ms
        "error_rate": round(random.uniform(0.0, 0.05), 4),     # %
        "cpu": round(random.uniform(10.0, 30.0), 2),           # request %
        "memory": round(random.uniform(40.0, 60.0), 2)         # request %
    }
