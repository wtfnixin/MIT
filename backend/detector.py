import random

def run_detector(features: dict, vote_buffer: list) -> tuple:
    """
    Runs the anomaly detection logic (Mocked).
    Normally runs the loaded Isolation Forest object across the 4-feature vector.
    
    Args:
        features: dict containing the 4 Prometheus features
        vote_buffer: list holding the sliding window of last 5 observations
        
    Returns:
        confidence float (0-100)
        votes list (updated vote_buffer)
        is_anomaly bool
    """
    # MOCK Logic: Randomly determine if this scrape is anomalous to test the dashboard,
    # or trigger if latency happens to cross a fake threshold.
    # We bias towards 0 (normal) but allow spikes for testing.
    vote = 1 if features.get("p95_latency", 0) > 140.0 or random.random() > 0.8 else 0
    
    # Update sliding window (keep last 5)
    vote_buffer.append(vote)
    if len(vote_buffer) > 5:
        vote_buffer.pop(0)
        
    anomaly_votes = sum(vote_buffer)
    is_anomaly = anomaly_votes >= 4  # Persistent anomaly
    
    # Confidence calculation: sum of votes / 5 * 100
    confidence = (anomaly_votes / 5.0) * 100.0
    
    return confidence, vote_buffer, is_anomaly
