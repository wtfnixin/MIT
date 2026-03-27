import time
import uuid

def restart_pod(service_name: str) -> tuple:
    """
    Executes exactly one bounded recovery action via the mock Kubernetes logic.
    Normally uses kubernetes-client to delete exactly 1 pod.
    
    Args:
        service_name: the logical name of the service
        
    Returns:
        pod_name string that was deleted
        timestamp float
    """
    # MOCK Logic: Since we don't have access to the actual Kubernetes cluster
    # or the friend's kubeconfig, we simulate a successful pod identification and termination.
    
    pod_id = str(uuid.uuid4())[:8]
    pod_name = f"{service_name}-{pod_id}"
    timestamp = time.time()
    
    print(f"[RECOVERY] Mock: Sending delete request for pod {pod_name} in namespace boutique.")
    
    return pod_name, timestamp
