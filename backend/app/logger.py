# Event logger — trace_id-aware audit trail
# Vijay Dhawan (Logs/Trace module) consumes these outputs

import json
import os
import requests
from datetime import datetime

LOGS_SERVICE_URL = "http://127.0.0.1:8001/events"

def log_event(event: dict) -> None:
    """
    Forward a canonical controller event to the Truth Layer (Logging Service).
    """
    try:
        requests.post(LOGS_SERVICE_URL, json=event, timeout=2)
    except Exception:
        pass
    
    print(f"[EVENT_LOG] trace_id={event.get('trace_id')} | "
          f"decision={event.get('decision')} | "
          f"user_id={event.get('user_id')}")


def log_relay(trace_id: str, user_id: str) -> None:
    """
    Forward a relay trigger event to the Truth Layer.
    """
    relay_entry = {
        "type": "RELAY_TRIGGER",
        "trace_id": trace_id,
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        requests.post(LOGS_SERVICE_URL, json=relay_entry, timeout=2)
    except Exception:
        # Silently fail or log a tiny message if Truth Layer is down
        print(f"[LOG] Truth Layer offline (Port 8001)")

