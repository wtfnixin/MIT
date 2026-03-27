import time
import requests

BASE_URL = "http://localhost:8000/api"

def print_res(name, res):
    print(f"\n--- {name} ---")
    try:
        print(f"Status: {res.status_code}")
        print(res.json())
    except Exception as e:
        print(f"Failed: {e}")

try:
    print_res("1. HEALTH CHECK", requests.get(f"{BASE_URL}/health"))

    print_res("2. LATEST INCIDENT (Empty)", requests.get(f"{BASE_URL}/latest"))

    print_res("3. POST WARMUP START", requests.post(f"{BASE_URL}/warmup/start"))
    
    print("\n[Sleeping 12s to wait for warmup to finish...]")
    time.sleep(12)

    print_res("4. GET WARMUP STATUS", requests.get(f"{BASE_URL}/warmup/status"))

    print("\n[Running Detection Loop to trigger anomaly]")
    for i in range(5):
        print_res(f"5.{i} DETECT RUN", requests.post(f"{BASE_URL}/detect/run"))
        time.sleep(0.5)

    print_res("6. POST RECOVER (Adservice)", requests.post(f"{BASE_URL}/recover?service_name=adservice"))

    print_res("7. GET INCIDENTS (Should have 1)", requests.get(f"{BASE_URL}/incidents"))

except Exception as e:
    print(f"Test script failed: {e}")
