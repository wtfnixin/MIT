import time

def evaluate_decision(confidence: float, votes: list, service_name: str, cooldowns: dict) -> tuple:
    """
    Evaluates the safety gates before authorizing a recovery action.
    Normally triggers based on the anomaly score, but also checks criticality and cooldown limits.
    
    Args:
        confidence: float representing anomaly confidence (0-100)
        votes: the 5-observation vote buffer
        service_name: name of the service evaluating remediation
        cooldowns: dict mapping service names to their last recovery timestamp
        
    Returns:
        should_act bool
        reason string
    """
    # Gate 1: Anomaly gate
    if sum(votes) < 4:
        return False, "Not enough anomalous votes (need 4/5)"
        
    # Gate 2: Confidence gate
    if confidence < 80.0:
        return False, f"Confidence {confidence}% is below 80% threshold"
        
    # Gate 3: Criticality gate
    if service_name in ["frontend", "checkoutservice"]:
        return False, f"Service {service_name} is critical; halting auto-remediation"
        
    # Gate 4: Cooldown gate
    last_action_time = cooldowns.get(service_name, 0.0)
    time_since_last_action = time.time() - last_action_time
    # Hardcoded 120 seconds default as per PRD
    if time_since_last_action < 120.0:
        return False, f"Service {service_name} in cooldown for another {int(120 - time_since_last_action)}s"
        
    return True, "All gates passed; initiating recovery"
