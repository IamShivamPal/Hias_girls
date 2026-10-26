import asyncio
import json
import os
from datetime import datetime
from collections import deque
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.adapters.cosec import COSECAdapter

from app.schemas import (
    AccessInput, ControllerEvent, Decision, Reason, Direction, Method, ReviewAction, UserResponse
)
from app.engine import evaluate, seed_db
from app.hardware import open_gate, GPIO_ENABLED
from app.logger import log_event
from app.database import engine, Base, get_db
from app.models import User, Event, Setting
from app.background import CosecWorker, COSEC_CONFIG

# Initialize Database
Base.metadata.create_all(bind=engine)

app = FastAPI(title="HIAS Controller Core", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Global State ---
SSE_CLIENTS: List[asyncio.Queue] = []
DUPLICATE_CACHE = {}  # (user_id, device_id) -> timestamp
DUPLICATE_THRESHOLD = 2.0 # Ignore identical scans within 2 seconds
REVIEW_QUEUE = {}  # trace_id -> ControllerEvent

# Default Settings (will be overridden by DB if exists)
SYSTEM_SETTINGS = {
    "emergency_mode": False,
    "access_window_start": "07:00",
    "access_window_end": "21:00",
    "require_admin_approval": False
}

COSEC_WORKER: Optional[CosecWorker] = None

@app.on_event("startup")
def startup_event():
    with next(get_db()) as db:
        seed_db(db)
        # Load settings from DB if they exist
        db_settings = db.query(Setting).all()
        for s in db_settings:
            # Simple parsing: assume boolean for emergency_mode
            if s.key == "emergency_mode":
                SYSTEM_SETTINGS[s.key] = s.value == "True"
            else:
                SYSTEM_SETTINGS[s.key] = s.value
    
    # Initialize COSEC Background Worker
    global COSEC_WORKER
    COSEC_WORKER = CosecWorker(process_access_request, broadcast_event)
    asyncio.create_task(COSEC_WORKER.start())

@app.on_event("shutdown")
async def shutdown_event():
    if COSEC_WORKER:
        await COSEC_WORKER.stop()

async def broadcast_event(event: ControllerEvent):
    """Push event to all connected SSE clients."""
    data = event.model_dump_json()
    for queue in SSE_CLIENTS:
        await queue.put(data)

async def process_access_request(
    db: Session,
    user_id: str,
    method: Method,
    device_id: str,
    ip_address: str = None,
    bypass_cache: bool = False,
    event_timestamp: Optional[datetime] = None,
) -> Optional[ControllerEvent]:
    """Core logic for processing access requests from any source."""
    # 1. Duplicate Prevention
    if not bypass_cache:
        now = asyncio.get_event_loop().time()
        dup_key = (user_id, device_id)
        if dup_key in DUPLICATE_CACHE:
            if now - DUPLICATE_CACHE[dup_key] < DUPLICATE_THRESHOLD:
                return None
        DUPLICATE_CACHE[dup_key] = now

    # 2. Enrichment & Decision
    decision, reason, name = evaluate(db, user_id, SYSTEM_SETTINGS)

    # 3. Infer Direction from device_id
    direction = Direction.IN if "in" in device_id.lower() else Direction.OUT

    # 4. Build Canonical Event
    event = ControllerEvent(
        user_id=user_id,
        name=name,
        direction=direction,
        method=method,
        decision=decision,
        reason=reason,
        device_id=device_id,
        ip_address=ip_address
    )

    # 4. Handle Review Queue for Unknowns
    if decision == Decision.DENY and reason == Reason.UNKNOWN:
        # Only add to queue if the 'admin_review' setting is enabled
        if SYSTEM_SETTINGS.get("admin_review", True):
            REVIEW_QUEUE[event.trace_id] = event
            print(f"[REVIEW_REQUIRED] Unknown face (ID: {user_id}) at {device_id}. Trace: {event.trace_id}")
        else:
            print(f"[ACCESS_DENIED] Auto-denying stranger (Review Queue Disabled)")

    # 5. Trigger Hardware feedback
    if decision == Decision.ALLOW:
        open_gate(event.trace_id, event.user_id, device_id)
    elif decision == Decision.DENY and reason == Reason.UNKNOWN:
        # Trigger a clear audio alert on the hardware
        from app.background import COSEC_CONFIG
        # Use the IP address of the device that triggered the event, or fallback to master
        target_url = ip_address or COSEC_CONFIG["devices"][0]["url"]
        hw_adapter = COSECAdapter(
            target_url,
            COSEC_CONFIG["username"],
            COSEC_CONFIG["password"]
        )
        asyncio.create_task(hw_adapter.trigger_buzzer())

    # 6. Save to DB
    # We use the current server's UTC time as requested to bypass incorrect/drifted hardware clocks
    record_timestamp = datetime.utcnow()
    event.timestamp = record_timestamp.isoformat()

    db_event = Event(
        trace_id=event.trace_id,
        user_id=event.user_id,
        name=event.name,
        direction=event.direction,
        method=event.method,
        decision=event.decision,
        reason=event.reason,
        device_id=event.device_id,
        timestamp=record_timestamp
    )
    db.add(db_event)
    db.commit()

    # 7. Save to Shadow Logger (JSON File - Only store latest)
    try:
        import json
        log_entry = {
            "timestamp": event.timestamp,
            "name": event.name,
            "decision": event.decision,
            "Door": f"{event.device_id} ({event.ip_address or 'Unknown'})",
            "method": event.method
        }
        # Overwrite file with ONLY the latest entry
        with open("history.json", "w") as f:
            json.dump([log_entry], f, indent=4)
    except Exception as e:
        print(f"[SHADOW_LOG_ERROR] {e}")

    await broadcast_event(event)
    return event

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "hias-controller"}

@app.post("/access/event", response_model=ControllerEvent)
async def access_event(payload: AccessInput, request: Request, db: Session = Depends(get_db)):
    event = await process_access_request(db, payload.user_id, payload.method, payload.device_id, ip_address=request.client.host)
    if not event:
        raise HTTPException(status_code=429, detail="Duplicate scan ignored")
    await broadcast_event(event)
    return event

@app.post("/events/clear")
async def clear_events(db: Session = Depends(get_db)):
    db.query(Event).delete()
    db.commit()
    return {"status": "success"}

@app.get("/events")
async def get_events(limit: int = 10, db: Session = Depends(get_db)):
    db_events = db.query(Event).order_by(Event.timestamp.desc()).limit(limit).all()
    return [
        ControllerEvent(
            trace_id=e.trace_id,
            user_id=e.user_id,
            name=e.name,
            direction=e.direction,
            method=e.method,
            decision=e.decision,
            reason=e.reason,
            device_id=e.device_id,
            timestamp=e.timestamp.isoformat()
        ) for e in db_events
    ]

@app.get("/events/stream")
async def event_stream(request: Request):
    queue = asyncio.Queue()
    SSE_CLIENTS.append(queue)
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            SSE_CLIENTS.remove(queue)
    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.post("/manual/override", response_model=ControllerEvent)
async def manual_override(payload: dict, db: Session = Depends(get_db)):
    reason_text = payload.get("reason", "No reason provided")
    
    # Force an override event with the new MANUAL reason
    event = await process_access_request(db, "S100", Method.MANUAL, "cosec_manual_in", ip_address="127.0.0.1", bypass_cache=True)
    if event:
        # Override the decision and name
        event.decision = Decision.ALLOW
        event.reason = Reason.MANUAL # Use the enum value to avoid crashes
        
        # Update DB with the custom reason text in the name field for visibility
        db_event = db.query(Event).filter(Event.trace_id == event.trace_id).first()
        if db_event:
            db_event.decision = Decision.ALLOW
            db_event.reason = Reason.MANUAL
            db_event.name = f"MANUAL: {reason_text}"
            db.commit()
        
        # Note: process_access_request ALREADY called open_gate, 
        # so we don't call it again here to avoid double-pulses.
        
        await broadcast_event(event)
    return event

@app.get("/settings")
async def get_settings():
    return SYSTEM_SETTINGS

@app.post("/settings")
async def update_settings(new_settings: dict, db: Session = Depends(get_db)):
    global SYSTEM_SETTINGS
    SYSTEM_SETTINGS.update(new_settings)
    for key, value in new_settings.items():
        db_setting = db.query(Setting).filter(Setting.key == key).first()
        if db_setting:
            db_setting.value = str(value)
        else:
            db.add(Setting(key=key, value=str(value)))
    db.commit()
    return SYSTEM_SETTINGS

@app.post("/users/sync-all")
async def sync_all_devices(db: Session = Depends(get_db)):
    """
    Phase 1 — Profile Sync Only:
      Push every user's profile (name, ID, ref-ID, face-enabled flag) to all
      secondary gates. This ensures the gates know the users exist and 
      enables 'enable-fr=1'.
      
      Enrollment is now handled manually per-gate via the enrollment page.
    """
    from app.background import COSEC_CONFIG

    # Auto-assign Reference IDs to users who don't have them
    unassigned = db.query(User).filter(User.device_ref_id == None).all()
    if unassigned:
        # Find the current max ref id
        max_ref = db.query(func.max(User.device_ref_id)).scalar() or 100
        for i, user in enumerate(unassigned):
            user.device_ref_id = max_ref + i + 1
        db.commit()
        
    users = db.query(User).filter(
        User.user_id.isnot(None),
        User.device_ref_id.isnot(None)
    ).all()

    all_devs = COSEC_CONFIG["devices"]

    results = {
        "total_users": len(users),
        "profiles_synced": 0,
        "errors": [],
    }

    for user in users:
        profile_ok = True

        for idx, dev_cfg in enumerate(all_devs):
            adapter = COSECAdapter(
                dev_cfg["url"],
                COSEC_CONFIG["username"],
                COSEC_CONFIG["password"]
            )
            dev_name = dev_cfg["name"]

            try:
                # ── Phase 1: push user profile ──────────────────────────────
                # Check if user already exists to avoid overwriting/wiping templates
                loop = asyncio.get_event_loop()
                exists_check = await loop.run_in_executor(None, lambda: adapter._get("users", {"action": "get", "user-id": user.user_id}))
                if "Response-Code=0" in exists_check or "response-code=0" in exists_check:
                    print(f"[SYNC] Skipping Gate {idx+1} for {user.user_id} - Profile already exists.")
                    continue

                # Only push if they don't exist
                ok = await adapter.upsert_user(
                    user.user_id, user.name, user.device_ref_id
                )
                if not ok:
                    results["errors"].append(
                        f"{dev_name}: profile push failed for {user.name}"
                    )
                    profile_ok = False
                    continue

                print(f"[SYNC] Profile pushed: {user.name} → {dev_name}")

            except Exception as e:
                results["errors"].append(f"{dev_name}/{user.name}: {e}")
                profile_ok = False

        if profile_ok:
            results["profiles_synced"] += 1

    return results

@app.get("/system/time")
async def get_system_time():
    from datetime import datetime
    return {"iso": datetime.now().isoformat()}

@app.get("/system/status")
async def get_system_status():
    """Provides a simplified health status for the sidebar widget."""
    import time
    global COSEC_WORKER
    
    cosec_online = COSEC_WORKER and COSEC_WORKER.running
    
    return {
        "controller": "Online",
        "face_devices": "Online" if cosec_online else "Offline",
        "rfid_readers": "Healthy",
        "relays": "Healthy" if GPIO_ENABLED else "Simulated",
        "network": "Online",
        "power": "Normal"
    }

@app.get("/system/devices")
async def get_system_devices():
    from app.background import COSEC_CONFIG
    global COSEC_WORKER
    
    cosec_online = COSEC_WORKER and COSEC_WORKER.running
    cosec_latency = "42ms" if cosec_online else "0ms"

    devices = [
        {"name": "HIAS Controller Core", "status": "Online", "uptime": "99.98%", "latency": "1ms", "type": "software", "url": "http://localhost:8000"},
        {"name": "SQLite Database", "status": "Online", "uptime": "100%", "latency": "0ms", "type": "software", "url": "sqlite:///hias.db"},
    ]

    # Add the actual hardware gates from config
    for dev in COSEC_CONFIG["devices"]:
        devices.append({
            "name": dev["name"],
            "status": "Online" if cosec_online else "Offline",
            "uptime": "99.95%",
            "latency": cosec_latency,
            "type": "hardware",
            "url": dev["url"]
        })

    return devices

@app.get("/review/queue")
async def get_review_queue():
    return list(REVIEW_QUEUE.values())

@app.post("/review/action")
async def process_review_action(data: ReviewAction, db: Session = Depends(get_db)):
    if data.trace_id not in REVIEW_QUEUE:
        raise HTTPException(status_code=404, detail="Event not found in queue")
    event = REVIEW_QUEUE.pop(data.trace_id)
    if data.action == "confirm":
        event.decision = Decision.ALLOW
        open_gate(event.trace_id, event.user_id, event.device_id)
    else:
        event.decision = Decision.DENY
    # Update existing event in DB
    db_event = db.query(Event).filter(Event.trace_id == event.trace_id).first()
    if db_event:
        db_event.decision = event.decision
        db_event.reason = event.reason
        db.commit()
    else:
        # Fallback if not found (shouldn't happen)
        db_event = Event(
            trace_id=event.trace_id,
            user_id=event.user_id,
            name=event.name,
            direction=event.direction,
            method=event.method,
            decision=event.decision,
            reason=event.reason,
            device_id=event.device_id,
            timestamp=datetime.fromisoformat(event.timestamp)
        )
        db.add(db_event)
        db.commit()
    
    # Broadcast the decision to the live feed
    await broadcast_event(event)
    
    return {"status": "success", "decision": event.decision}

@app.post("/simulate/review")
async def simulate_review_event(db: Session = Depends(get_db)):
    event = process_access_request(db, "S002", Method.FACE, "cam_01")
    if event:
        event.decision = Decision.REVIEW
        event.reason = Reason.UNKNOWN
        REVIEW_QUEUE[event.trace_id] = event
    return event

@app.get("/reports/stats")
async def get_report_stats(db: Session = Depends(get_db)):
    from collections import Counter
    db_events = db.query(Event).all()
    hours = [e.timestamp.hour for e in db_events]
    hour_counts = Counter(hours)
    decisions = Counter([e.decision for e in db_events])
    methods = Counter([e.method for e in db_events])
    return {
        "hourly_distribution": [hour_counts.get(i, 0) for i in range(24)],
        "decision_split": {
            "allowed": decisions.get(Decision.ALLOW, 0),
            "denied": decisions.get(Decision.DENY, 0)
        },
        "method_split": {
            "rfid": methods.get(Method.RFID, 0),
            "face": methods.get(Method.FACE, 0),
            "manual": methods.get(Method.MANUAL, 0)
        }
    }

@app.get("/reports/export")
async def export_logs_csv(db: Session = Depends(get_db)):
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "User ID", "Name", "Method", "Decision", "Reason", "Device ID"])
    db_events = db.query(Event).order_by(Event.timestamp.desc()).all()
    for e in db_events:
        writer.writerow([e.timestamp, e.user_id, e.name, e.method, e.decision, e.reason, e.device_id])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=hias_access_logs.csv"})

@app.get("/alerts")
async def get_alerts(db: Session = Depends(get_db)):
    alerts = []
    db_events = db.query(Event).order_by(Event.timestamp.desc()).limit(20).all()
    for event in db_events:
        if event.decision == Decision.DENY:
            alerts.append({
                "id": event.trace_id,
                "type": "Security",
                "message": f"Unauthorized Access Attempt: {event.name} ({event.user_id})",
                "severity": "High",
                "timestamp": event.timestamp.isoformat()
            })
        elif event.method == Method.MANUAL:
             alerts.append({
                "id": event.trace_id,
                "type": "System",
                "message": f"Manual Override Triggered at {event.device_id}",
                "severity": "Medium",
                "timestamp": event.timestamp.isoformat()
            })
    return sorted(alerts, key=lambda x: x["timestamp"], reverse=True)

@app.post("/alerts/clear")
async def clear_alerts(db: Session = Depends(get_db)):
    """Clears all events from the database (effectively clearing alerts)."""
    db.query(Event).delete()
    db.commit()
    return {"status": "success"}

@app.get("/users", response_model=List[UserResponse])
async def get_all_users(db: Session = Depends(get_db)):
    return db.query(User).all()

@app.post("/users")
async def enroll_user(user: dict, db: Session = Depends(get_db)):
    user_id = user.get("user_id")
    name = user.get("name")
    if not user_id or not name:
        raise HTTPException(status_code=400, detail="User ID and Name are required")

    db_user = db.query(User).filter(User.user_id == user_id).first()

    # Allocate ref_id from DB only — no hardware queries on the critical path
    if db_user:
        ref_id = db_user.device_ref_id
        db_user.name = name
    else:
        db_max = db.query(func.max(User.device_ref_id)).scalar() or 0
        ref_id = max(db_max + 1, 26)
        db_user = User(user_id=user_id, name=name, device_ref_id=ref_id)
        db.add(db_user)

    db.commit()
    print(f"[ENROLL] User {user_id} saved to DB with ref_id={ref_id}. Scheduling background hardware sync.")

    # Fire-and-forget hardware sync — does NOT block the response
    async def _sync_to_hardware(uid: str, uname: str, rid: int):
        from app.background import COSEC_CONFIG
        all_devs = COSEC_CONFIG["devices"]
        for dev_cfg in all_devs:
            try:
                adapter = COSECAdapter(dev_cfg["url"], COSEC_CONFIG["username"], COSEC_CONFIG["password"])
                success = await adapter.upsert_user(uid, uname, rid)
                print(f"[HW_SYNC] {dev_cfg['name']} -> {'OK' if success else 'FAILED'} for {uid}")
            except Exception as e:
                print(f"[HW_SYNC_ERROR] {dev_cfg['name']}: {e}")
            await asyncio.sleep(0.2)
        if COSEC_WORKER:
            COSEC_WORKER.clear_cache()

    asyncio.create_task(_sync_to_hardware(user_id, name, ref_id))

    return {
        "status": "success",
        "user_id": user_id,
        "device_ref_id": ref_id,
        "device_synced": False,
        "message": "User enrolled in DB. Hardware sync running in background."
    }

@app.post("/users/sync-from-device")
async def sync_from_device(db: Session = Depends(get_db)):
    from app.background import COSEC_CONFIG
    master_dev = COSEC_CONFIG["devices"][0]
    adapter = COSECAdapter(
        master_dev["url"],
        COSEC_CONFIG["username"],
        COSEC_CONFIG["password"]
    )
    device_users = await adapter.fetch_all_users()
    
    synced_count = 0
    for u in device_users:
        existing = db.query(User).filter(User.user_id == u["user_id"]).first()
        if not existing:
            new_user = User(user_id=u["user_id"], name=u["name"], device_ref_id=u.get("ref_id", 0))
            db.add(new_user)
            synced_count += 1
        else:
            existing.name = u["name"]
            existing.device_ref_id = u.get("ref_id", existing.device_ref_id)
            
    db.commit()

    if COSEC_WORKER:
        COSEC_WORKER.clear_cache()

    return {"status": "success", "synced": synced_count}


@app.delete("/users/{user_id}")
async def delete_user(user_id: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 1. Remove from ALL Hardware Gates
    from app.background import COSEC_CONFIG
    hw_errors = 0
    for device_cfg in COSEC_CONFIG["devices"]:
        try:
            adapter = COSECAdapter(
                device_cfg["url"],
                COSEC_CONFIG["username"],
                COSEC_CONFIG["password"]
            )
            await adapter.delete_user(user.user_id)
        except Exception as e:
            print(f"[WARNING] Failed to delete user {user_id} from {device_cfg['name']}: {e}")
            hw_errors += 1
    
    hw_success = (hw_errors == 0)
    
    if not hw_success:
        print(f"[WARNING] Hardware deletion failed for {user_id}. Proceeding with DB cleanup.")

    # 2. Remove from local DB (Always do this to prevent getting stuck)
    db.delete(user)
    db.commit()

    if COSEC_WORKER:
        COSEC_WORKER.clear_cache()

    return {
        "status": "success", 
        "user_id": user_id, 
        "hardware_synced": hw_success
    }

@app.post("/users/{user_id}/enroll-face")
async def enroll_user_face(user_id: str, gate_idx: int = 0, db: Session = Depends(get_db)):
    """Triggers face enrollment mode on a specific gate."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    from app.background import COSEC_CONFIG
    if gate_idx < 0 or gate_idx >= len(COSEC_CONFIG["devices"]):
        raise HTTPException(status_code=400, detail="Invalid gate index")
        
    target_dev = COSEC_CONFIG["devices"][gate_idx]
    adapter = COSECAdapter(
        target_dev["url"],
        COSEC_CONFIG["username"],
        COSEC_CONFIG["password"]
    )
    
    print(f"[ENROLL] Triggering gate {target_dev['name']} for user {user_id}")
    success = await adapter.enroll_user_face(user_id)
    
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to trigger enrollment on {target_dev['name']}")

    return {"status": "success", "message": f"Enrollment started for {user_id} on {target_dev['name']}"}

@app.post("/users/{user_id}/enroll-complete")
async def complete_user_enrollment(user_id: str, db: Session = Depends(get_db)):
    """Manually marks a user as fully enrolled to remove them from the queue."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.face_enrolled = True
    db.commit()
    return {"status": "success", "message": f"User {user_id} marked as fully enrolled"}

@app.get("/users/search")
async def search_users(q: str = "", db: Session = Depends(get_db)):
    q = f"%{q}%"
    db_users = db.query(User).filter((User.user_id.ilike(q)) | (User.name.ilike(q))).limit(10).all()
    return [{"user_id": u.user_id, "name": u.name, "role": u.role} for u in db_users]

@app.post("/system/force-delete-hw")
async def force_delete_hw(payload: dict):
    """Manual trigger to delete a user from all devices even if not in DB"""
    id_val = payload.get("user_id")
    if not id_val:
        return {"status": "error", "message": "Missing user_id"}
    
    all_devs = COSEC_CONFIG["devices"]
    results = []
    for d in all_devs:
        try:
            adapter = COSECAdapter(d["url"], COSEC_CONFIG["username"], COSEC_CONFIG["password"])
            await adapter.delete_user(id_val)
            results.append(d["name"])
        except:
            pass
    return {"status": "success", "cleared_from": results}

@app.post("/system/master-reset")
async def master_reset(db: Session = Depends(get_db)):
    """
    CRITICAL: Wipes ALL user data and events from DB and ALL hardware.
    """
    print("[MASTER_RESET] Starting full system wipe...")
    
    all_devs = COSEC_CONFIG["devices"]
    
    # 1. Fetch all users before deleting from DB
    users = db.query(User).all()
    user_ids = [u.user_id for u in users]
    
    # 2. Hardware Wipe (Per User)
    for user_id in user_ids:
        print(f"[MASTER_RESET] Deleting user {user_id} from all devices...")
        for dev in all_devs:
            try:
                adapter = COSECAdapter(dev["url"], COSEC_CONFIG["username"], COSEC_CONFIG["password"])
                await adapter.delete_user(user_id)
            except Exception as e:
                print(f"[MASTER_RESET_ERROR] Failed to delete user {user_id} from {dev['name']}: {e}")
        await asyncio.sleep(0.1)

    # 3. Global Hardware Purge (Face/Finger/Palm)
    print("[MASTER_RESET] Triggering global credential wipe on all devices...")
    for dev in all_devs:
        try:
            adapter = COSECAdapter(dev["url"], COSEC_CONFIG["username"], COSEC_CONFIG["password"])
            await adapter.delete_all_credentials()
        except Exception as e:
            print(f"[MASTER_RESET_ERROR] Global wipe failed on {dev['name']}: {e}")

    # 4. Database Wipe
    print("[MASTER_RESET] Clearing local database tables...")
    try:
        db.query(Event).delete()
        db.query(User).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[MASTER_RESET_ERROR] Database wipe failed: {e}")
        return {"status": "error", "message": f"DB wipe failed: {str(e)}"}

    print("[MASTER_RESET] Full system wipe completed successfully.")
    return {"status": "success", "message": "All data wiped from DB and Hardware."}

@app.get("/stats/summary")
async def get_stats_summary(db: Session = Depends(get_db)):
    total = db.query(Event).count()
    granted = db.query(Event).filter(Event.decision == Decision.ALLOW).count()
    denied = total - granted
    overrides = db.query(Event).filter(Event.method == Method.MANUAL).count()
    return {"total": total, "granted": granted, "denied": denied, "overrides": overrides}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
