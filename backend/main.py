from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
import time
import asyncio

import database
import models
import prometheus_client
import detector
import decision
import recovery
import verifier
from config import DEMO_MODE, KUBE_NAMESPACE
from chaos.chaos_engine import inject_chaos_safe, cleanup_all
from service_catalog import (
    get_non_critical_services,
    get_supported_chaos_scenarios,
    get_supported_services,
)

models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="KubeResilience", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVICES = get_supported_services()
CHAOS_SERVICES = get_non_critical_services()
CHAOS_SCENARIOS = get_supported_chaos_scenarios()

# Independent States!
state = {
    "warmup_done": True, # True by default as baselines are pre-loaded by detector
    "manual_mode": False,
    "cooldowns": {},
    "services": {
        svc: {
            "votes": [],
            "confidence": 0.0,
            "is_anomaly": False,
            "features": {"p95_latency_ms": 0.0, "error_rate_pct": 0.0, "cpu_cores": 0.0, "memory_mb": 0.0}
        } for svc in SERVICES
    }
}

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/api/health")
def read_health():
    return {"status": "ok"}

@app.get("/api/services")
def list_services():
    """Returns the exact list of tracked services for the frontend."""
    return {"services": SERVICES}


@app.get("/api/config")
def get_runtime_config():
    return {
        "services": SERVICES,
        "chaos_services": CHAOS_SERVICES,
        "chaos_scenarios": CHAOS_SCENARIOS,
        "demo_mode": DEMO_MODE,
        "namespace": KUBE_NAMESPACE,
    }


@app.post("/api/warmup/start")
def start_warmup():
    state["warmup_done"] = True
    return {"message": "Warm-up skipped (using pre-trained baseline stats)"}

@app.get("/api/warmup/status")
def warmup_status():
    return {"done": state["warmup_done"]}

@app.post("/api/detect/run")
def run_detect():
    """
    Called by the dashboard loop. Overrides global polling and individually returns metrics.
    """
    if not state["warmup_done"]:
        return {"error": "Wait until warmup is completed"}
    if state["manual_mode"]:
        return {"error": "System frozen in manual mode"}
        
    for svc in SERVICES:
        svc_state = state["services"][svc]
        features = prometheus_client.fetch_metrics(svc)
        
        det_result = detector.run_detection(features, svc_state["votes"])
        
        # vote buffer is modified in place, but we can assign confidence and anomaly state
        svc_state["confidence"] = det_result["confidence"]
        svc_state["is_anomaly"] = det_result["triggered"]
        svc_state["features"] = features
    
    return state["services"]

@app.post("/api/recover")
def recover_service(service_name: str, db: Session = Depends(get_db)):
    if state["manual_mode"]:
        raise HTTPException(status_code=400, detail="Automation frozen in manual mode")
        
    if service_name not in SERVICES:
        raise HTTPException(status_code=404, detail="Service not tracked")
        
    svc_state = state["services"][service_name]
    
    # Delegate to external Decision Engine
    res = decision.make_decision(
        service=service_name,
        confidence=svc_state["confidence"],
        triggered=svc_state["is_anomaly"],
        metrics=svc_state["features"],
        vote_buffer=svc_state["votes"]
    )
    
    if res.action != "RECOVER":
        return {"status": "skipped", "reason": res.reason}
        
    pod_deleted, timestamp = recovery.restart_pod(service_name)
    
    # Immediately wipe chaos so the system can heal during the 20-second verification wait!
    cleanup_all()
    prometheus_client.clear_demo_chaos(service_name)
    
    baseline = detector.get_baseline(service_name)
    baseline_avg = baseline["p95_latency_ms_mean"]
    
    status = verifier.verify_recovery(pod_deleted, baseline_avg)
    
    # Record action in decision state DB for cooldown logic
    try:
        decision.record_action(
            service=service_name,
            severity_label=res.severity_label,
            verification_status=status
        )
    except Exception as e:
        print(f"Warning: Failed to log adaptive cooldown record: {e}")
    
    incident_votes = list(svc_state["votes"])

    if status == "FAILED":
        state["manual_mode"] = True
    
    new_incident = models.Incident(
        service=service_name,
        confidence=svc_state["confidence"],
        votes=incident_votes,
        action=f"restarted 1 pod ({pod_deleted})",
        pod_name=pod_deleted,
        status=status,
        timestamp=timestamp
    )
    
    db.add(new_incident)
    db.commit()
    db.refresh(new_incident)

    if status == "HEALED":
        prometheus_client.clear_demo_chaos(service_name)
        svc_state["votes"].clear()
        svc_state["confidence"] = 0.0
        svc_state["is_anomaly"] = False
    
    return new_incident

@app.get("/api/incidents")
def get_incidents(db: Session = Depends(get_db)):
    return db.query(models.Incident).order_by(models.Incident.timestamp.desc()).all()

@app.get("/api/latest")
def get_latest_incident(db: Session = Depends(get_db)):
    incident = db.query(models.Incident).order_by(models.Incident.timestamp.desc()).first()
    if not incident:
        return {"message": "No incidents yet"}
    return incident

@app.post("/api/chaos/inject")
def trigger_chaos(service: str, scenario: str):
    if service not in SERVICES:
        raise HTTPException(status_code=404, detail=f"Service {service} not tracked")
    
    result = inject_chaos_safe(service, scenario)
    if not result["success"]:
        detail = result.get("error") or result.get("message") or "Chaos injection failed"
        raise HTTPException(status_code=400, detail=detail)
        
    if service in state["services"]:
        state["services"][service]["is_anomaly"] = True
        state["services"][service]["confidence"] = 99.0
        state["services"][service]["votes"] = [1, 1, 1, 1, 1]
        prometheus_client.set_demo_chaos(service, scenario)
    
    return result

@app.post("/api/chaos/cleanup")
def chaos_cleanup():
    cleanup_all()
    prometheus_client.clear_demo_chaos()
    state["manual_mode"] = False
    for svc_state in state["services"].values():
        svc_state["votes"].clear()
        svc_state["confidence"] = 0.0
        svc_state["is_anomaly"] = False
    return {"message": "All chaos experiments cleaned up"}
