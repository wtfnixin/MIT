import time

def verify_recovery(pod_name: str, baseline_avg: float) -> str:
    """
    Confirms whether recovery actually worked post-restart.
    Normally checks readiness states and compares p95 threshold latency levels.
    
    Args:
        pod_name: name of the deleted pod
        baseline_avg: the p95 baseline captured during warmup
        
    Returns:
        status string: 'HEALED' or 'FAILED'
    """
    print(f"[VERIFIER] Mock: Waiting 20 seconds to assess recovery for pod {pod_name}...")
    # In a real system we'd `time.sleep(20)` but for the hackathon endpoint we'll 
    # either fake the delay or have it pass instantly to avoid blocking FastAPI workers.
    # For now, we mock an instant check returning success to ensure demo flows easily.
    
    print(f"[VERIFIER] Mock: Assessing latency compared to baseline {baseline_avg} ms")
    
    # Normally we do logic:
    # if current_latency < baseline_avg * 1.5:
    #     return "HEALED"
    # else: return "FAILED"
    
    # We assume success in our mock
    return "HEALED"
