**KubeResilience**

Backend Product Requirements Document

For Antigravity \| Hackathon MVP Scope

**1. Overview**

This document defines the backend requirements for KubeResilience --- an
autonomous chaos engineering and self-healing platform for Kubernetes
microservices. The backend is built with Python 3.11 and FastAPI. It
connects to a Prometheus instance (on a teammate\'s machine), imports a
trained Isolation Forest model, makes remediation decisions, restarts
pods via the Kubernetes Python client, verifies recovery, and exposes
REST API endpoints for the React dashboard to poll.

**2. Tech Stack**

  ---------------------- ------------------------------- ----------------
  **Component**          **Technology**                  **Version**

  Language               Python                          3.11

  Web framework          FastAPI                         latest

  Server                 Uvicorn                         latest

  Prometheus client      requests                        latest

  ML model               scikit-learn (Isolation Forest) latest

  Data handling          pandas, numpy                   latest

  Kubernetes client      kubernetes (Python)             latest
  ---------------------- ------------------------------- ----------------

**3. Project Structure**

The backend is a single flat Python project with one file per
responsibility:

> kuberesilience-backend/
>
> main.py \# FastAPI app + all route definitions
>
> prometheus_client.py \# fetches metrics from teammate\'s Prometheus
>
> detector.py \# Isolation Forest voting logic + z-score fallback
>
> decision.py \# confidence gate + criticality + cooldown check
>
> recovery.py \# restarts exactly 1 pod via Kubernetes API
>
> verifier.py \# checks pod ready + p95 latency below 1.5x baseline
>
> requirements.txt \# pip dependencies
>
> kubeconfig.yaml \# teammate\'s kubeconfig (do not commit)

**4. API Routes**

All routes are prefixed with /api. The React dashboard polls these
endpoints every 2-3 seconds.

  ------------ -------------------- -------------------------------------------
  **Method**   **Route**            **Description**

  GET          /api/health          Returns {status: ok}. Used to confirm
                                    backend is running.

  POST         /api/warmup/start    Begins 10-minute warm-up. Collects baseline
                                    metrics. No chaos during this period.

  GET          /api/warmup/status   Returns {done: true/false}. Dashboard polls
                                    until warm-up is complete.

  POST         /api/detect/run      Fetches latest metrics, runs detector,
                                    updates vote buffer, returns confidence and
                                    votes.

  POST         /api/recover         Triggers decision engine. If gates pass,
                                    restarts 1 pod. Returns incident object.

  GET          /api/incidents       Returns list of all incident objects from
                                    in-memory state.

  GET          /api/latest          Returns the most recent incident object.
                                    Returns no incidents yet if none exist.
  ------------ -------------------- -------------------------------------------

**5. Module Specifications**

**5.1 prometheus_client.py**

Responsible for fetching the 4 input features from the teammate\'s
Prometheus instance over HTTP.

-   Base URL: configurable via PROMETHEUS_URL environment variable,
    default http://localhost:9090

-   Scrape interval: every 15 seconds

-   Queries: p95 latency, error rate, CPU usage, memory usage

-   Returns: a dict with keys p95_latency, error_rate, cpu, memory as
    float values

-   If a metric is unavailable: log a warning and return 0.0 for that
    feature

-   Metric names must be verified against the live Prometheus instance
    before hardcoding

**5.2 detector.py**

Runs the anomaly detection logic. Accepts the trained Isolation Forest
model from the teammate as an importable Python object.

-   Input: 4-feature vector \[p95_latency, error_rate, cpu, memory\]

-   Primary detector: Isolation Forest binary prediction. Output -1 =
    anomaly vote of 1, output 1 = vote of 0

-   Fallback detector: z-score on p95_latency and error_rate if model is
    unavailable or unstable

-   Vote buffer: sliding window of last 5 observations

-   Persistent anomaly: 4 or more anomalous votes out of 5

-   Confidence = (sum of votes / 5) x 100

-   Returns: confidence float (0-100), votes list, is_anomaly bool

**5.3 decision.py**

The safety gate. Acts only when all 4 conditions are met simultaneously.

  -------------------- ------------------------------ --------------------
  **Gate**             **Condition**                  **Value**

  Anomaly gate         Persistent anomaly detected    4 of 5 votes
                                                      anomalous

  Confidence gate      Confidence score threshold     \>= 80%

  Criticality gate     Service must be non-critical   Not frontend or
                                                      checkoutservice

  Cooldown gate        No recent action on same       No action in last
                       service                        120 seconds
  -------------------- ------------------------------ --------------------

-   Critical services (never auto-remediate): frontend, checkoutservice

-   Non-critical services (safe to remediate): cartservice,
    recommendationservice, adservice

-   Cooldown timer: per-service, resets after each automated action

-   Returns: should_act bool, reason string

**5.4 recovery.py**

Executes exactly one bounded recovery action. Uses the Kubernetes Python
client with the teammate\'s kubeconfig.

-   Loads kubeconfig from kubeconfig.yaml in the project root

-   Lists pods in the boutique namespace filtered by app label matching
    the service name

-   Deletes exactly 1 pod --- the first pod returned by the API

-   Records the timestamp of the action for cooldown tracking

-   Returns: pod name that was deleted, timestamp

-   Hard limit: maximum 1 pod affected per automated action --- no
    loops, no retries

**5.5 verifier.py**

Confirms whether recovery actually worked. Called automatically after
recovery.py completes.

-   Wait: 20-30 seconds after pod deletion before checking anything

-   Check 1 --- pod readiness: pod must be in Running phase with Ready
    condition True

-   Check 2 --- latency recovery: p95 latency must be below 1.5x the
    baseline average captured during warm-up

-   Both checks must pass for status = HEALED

-   If either check fails: status = FAILED, automation freezes, manual
    mode flag set to True

-   No blind retries after FAILED --- escalate to manual

**6. In-Memory State**

No database. All state is held in a Python dict in main.py for the
duration of the demo.

> state = {
>
> \'warmup_done\': False, \# bool
>
> \'baseline_avg\': None, \# float - avg p95 latency from warm-up
>
> \'votes\': \[\], \# list of 5 ints (0 or 1)
>
> \'confidence\': 0.0, \# float 0-100
>
> \'incidents\': \[\], \# list of incident dicts
>
> \'latest\': None, \# most recent incident dict
>
> \'manual_mode\': False, \# bool - set True after FAILED verification
>
> \'cooldowns\': {} \# dict: service_name -\> timestamp of last action
>
> }

**7. Incident Object Schema**

Every incident created by /api/recover follows this structure:

> {
>
> \'id\': 1, \# int, auto-incremented
>
> \'service\': \'cartservice\', \# string
>
> \'confidence\': 82.0, \# float
>
> \'votes\': \[1,1,0,1,1\], \# list of 5 ints
>
> \'action\': \'restarted 1 pod\', \# string
>
> \'pod_name\': \'cartservice-xyz\', \# string
>
> \'status\': \'HEALED\', \# HEALED \| FAILED \| PENDING
>
> \'timestamp\': 1234567890.0 \# float unix timestamp
>
> }

**8. Environment & Configuration**

  ------------------------ ----------------------- -------------------------
  **Variable**             **Default**             **Description**

  PROMETHEUS_URL           http://localhost:9090   URL of teammate\'s
                                                   Prometheus instance

  KUBE_CONFIG_PATH         ./kubeconfig.yaml       Path to teammate\'s
                                                   kubeconfig file

  BOUTIQUE_NAMESPACE       boutique                Kubernetes namespace for
                                                   Online Boutique

  WARMUP_DURATION          600                     Warm-up duration in
                                                   seconds (10 minutes)

  COOLDOWN_SECONDS         120                     Per-service cooldown
                                                   after an automated action

  CONFIDENCE_THRESHOLD     80                      Minimum confidence %
                                                   required to act

  VOTE_WINDOW              5                       Number of observations in
                                                   the sliding vote window

  ANOMALY_VOTES_REQUIRED   4                       Votes needed out of
                                                   VOTE_WINDOW to trigger
  ------------------------ ----------------------- -------------------------

**9. Failure Modes & Fallbacks**

  -------------------------- --------------------------------------------
  **Failure**                **Fallback Behaviour**

  Prometheus unreachable     Return 0.0 for all metrics, log warning,
                             skip detection cycle

  p95 latency metric missing Use request duration average or error rate
                             as primary signal

  Isolation Forest unstable  Switch to z-score fallback detector, keep
                             same voting logic

  Warm-up data too noisy     Extend warm-up, revalidate variance before
                             fitting

  Recovery verification      Set status FAILED, set manual_mode True,
  fails                      freeze all automation

  Dashboard unreachable      Backend CLI logs must show full loop: Detect
                             \> Decide \> Act \> Verify

  kubeconfig missing         Log error on startup, disable recovery.py,
                             continue detection only
  -------------------------- --------------------------------------------

**10. Critical Constraints**

-   Maximum 1 pod restarted per automated action --- hard limit, no
    exceptions

-   Never auto-remediate frontend or checkoutservice

-   Do not verify latency immediately after restart --- wait 20-30
    seconds first

-   Do not retry after FAILED verification --- freeze and escalate

-   Do not add Loki, LSTM, multi-service root-cause analysis, or
    multi-pod recovery

-   Do not claim production-grade ML accuracy in any output

-   All metric names must be verified in Prometheus before hardcoding in
    prometheus_client.py

**11. Running the Backend**

Install dependencies:

> pip install fastapi uvicorn requests kubernetes scikit-learn pandas
> numpy

Start the server:

> uvicorn main:app \--reload \--host 0.0.0.0 \--port 8000

Verify it is running:

> curl http://localhost:8000/api/health
>
> \# Expected: {\"status\": \"ok\"}

**12. Integration Checklist**

Before connecting the full system, verify each integration point:

-   Friend 2 has run port-forward on 0.0.0.0:9090 and shared their IP

-   prometheus_client.py returns real non-zero values for at least
    p95_latency and error_rate

-   Friend 1 has shared the trained model as an importable Python object

-   detector.py returns a confidence value and 5-vote buffer correctly

-   Friend 2 has shared kubeconfig.yaml and recovery.py can list pods in
    boutique namespace

-   POST /api/recover creates an incident and updates /api/latest

-   React dashboard polls /api/latest and shows the incident status

*End of Backend PRD*