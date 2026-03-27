from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
import time
import asyncio

# Local Imports
import database
import models
import prometheus_client
import detector
import decision
import recovery
import verifier

# Initialize database
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="KubeResilience", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-Memory State
state = {
    "warmup_done": False,
    "baseline_avg": 100.0, # default mock float
    "votes": [], # list of up to 5 ints
    "confidence": 0.0,
    "manual_mode": False,
    "cooldowns": {} # dict: service_name -> timestamp of last action
}

# Dependency
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/api/health")
def read_health():
    return {"status": "ok"}


async def perform_warmup():
    print("[WARMUP] Starting 10-minute warm-up phase (Mocked to 10 seconds for demo).")
    await asyncio.sleep(10) # Faking the warmup duration
    
    # Calculate baseline
    baselines = []
    for _ in range(5):
        features = prometheus_client.fetch_metrics()
        baselines.append(features.get("p95_latency", 0))
        await asyncio.sleep(1)
        
    state["baseline_avg"] = sum(baselines) / len(baselines) if baselines else 100.0
    state["warmup_done"] = True
    print(f"[WARMUP] Completed. Baseline latency avg: {state['baseline_avg']} ms")

@app.post("/api/warmup/start")
def start_warmup(background_tasks: BackgroundTasks):
    if state["warmup_done"]:
        return {"message": "Warmup already completed"}
    background_tasks.add_task(perform_warmup)
    return {"message": "Warm-up started"}

@app.get("/api/warmup/status")
def warmup_status():
    return {"done": state["warmup_done"], "baseline_avg": state["baseline_avg"]}

@app.post("/api/detect/run")
def run_detect():
    if not state["warmup_done"]:
        return {"error": "Wait until warmup is completed"}
    if state["manual_mode"]:
        return {"error": "System frozen in manual mode"}
        
    features = prometheus_client.fetch_metrics()
    confidence, new_votes, is_anomaly = detector.run_detector(features, state["votes"])
    
    state["votes"] = new_votes
    state["confidence"] = confidence
    
    return {
        "confidence": confidence,
        "votes": new_votes,
        "is_anomaly": is_anomaly,
        "features": features
    }

class RecoverRequest(str):
    pass

@app.post("/api/recover")
def recover_service(service_name: str, db: Session = Depends(get_db)):
    if state["manual_mode"]:
        raise HTTPException(status_code=400, detail="Automation frozen in manual mode")
        
    should_act, reason = decision.evaluate_decision(state["confidence"], state["votes"], service_name, state["cooldowns"])
    
    if not should_act:
        return {"status": "skipped", "reason": reason}
        
    # Recovery Process
    pod_deleted, timestamp = recovery.restart_pod(service_name)
    state["cooldowns"][service_name] = timestamp
    
    # Verifier Process
    status = verifier.verify_recovery(pod_deleted, state["baseline_avg"])
    
    # Handle critical failure
    if status == "FAILED":
        state["manual_mode"] = True
    
    # Save Incident to PostgreSQL via SQLAlchemy
    new_incident = models.Incident(
        service=service_name,
        confidence=state["confidence"],
        votes=state["votes"],
        action=f"restarted 1 pod ({pod_deleted})",
        pod_name=pod_deleted,
        status=status,
        timestamp=timestamp
    )
    
    db.add(new_incident)
    db.commit()
    db.refresh(new_incident)
    
    return new_incident

@app.get("/api/incidents")
def get_incidents(db: Session = Depends(get_db)):
    incidents = db.query(models.Incident).order_by(models.Incident.timestamp.desc()).all()
    return incidents

@app.get("/api/latest")
def get_latest_incident(db: Session = Depends(get_db)):
    incident = db.query(models.Incident).order_by(models.Incident.timestamp.desc()).first()
    if not incident:
        return {"message": "No incidents yet"}
    return incident
