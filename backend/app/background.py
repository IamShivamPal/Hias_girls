
import asyncio
import json
import os
from datetime import datetime
from typing import List, Dict, Optional
from dotenv import load_dotenv

from app.database import get_db
from app.models import User, Event
from app.schemas import Direction, Method, Decision, Reason
from app.adapters.cosec import COSECAdapter

load_dotenv()

# Multi-Device Configuration from .env
device_urls = os.getenv("COSEC_DEVICE_URLS", "http://192.168.50.2").split(",")
device_names = ["Entry 1", "Entry 2", "Exit 1", "Exit 2"]

COSEC_CONFIG = {
    "devices": [
        {"id": f"gate_{i+1}", "name": device_names[i] if i < len(device_names) else f"Gate {i+1}", "url": url.strip()}
        for i, url in enumerate(device_urls)
    ],
    "username": os.getenv("COSEC_USERNAME", "admin"),
    "password": os.getenv("COSEC_PASSWORD", "12345"),
    "poll_interval": float(os.getenv("COSEC_POLL_INTERVAL", "2.0"))
}

class CosecWorker:
    def __init__(self, process_request_fn, broadcast_fn):
        self.adapters = {
            dev["id"]: COSECAdapter(dev["url"], COSEC_CONFIG["username"], COSEC_CONFIG["password"])
            for dev in COSEC_CONFIG["devices"]
        }
        self.process_request = process_request_fn
        self.broadcast = broadcast_fn
        self.running = False
        self.last_indices = {dev["id"]: 0 for dev in COSEC_CONFIG["devices"]}

    async def start(self):
        self.running = True
        # Sync with all devices
        for dev_id, adapter in self.adapters.items():
            try:
                self.last_indices[dev_id] = await adapter.get_current_event_index()
                print(f"[COSEC_WORKER] Synced {dev_id} to latest index: {self.last_indices[dev_id]}")
            except Exception as e:
                print(f"[COSEC_WORKER] Failed to sync {dev_id}: {e}")
        
        asyncio.create_task(self._run())
        print(f"[COSEC_WORKER] Background polling started for {len(self.adapters)} devices.")

    async def stop(self):
        self.running = False
        print("[COSEC_WORKER] Background polling stopped.")

    async def _run(self):
        while self.running:
            # Poll all devices in parallel
            tasks = [self._loop_once(dev_id) for dev_id in self.adapters.keys()]
            await asyncio.gather(*tasks)
            await asyncio.sleep(COSEC_CONFIG["poll_interval"])

    async def _loop_once(self, dev_id):
        """Single iteration for a specific device."""
        adapter = self.adapters[dev_id]
        try:
            events = await adapter.fetch_events(self.last_indices[dev_id])
            
            if events:
                # Sort by index to process in order
                events.sort(key=lambda x: x["index"])
                
                for raw_event in events:
                    # Skip if already processed
                    if raw_event["index"] <= self.last_indices[dev_id]:
                        continue
                    
                    # 1. Identify User by Reference ID
                    user_id = None
                    ref_id = raw_event.get("ref_id")
                    with next(get_db()) as db:
                        if ref_id == 0:
                            user_id = "UNKNOWN"
                        elif ref_id:
                            user = db.query(User).filter(User.device_ref_id == ref_id).first()
                            if user:
                                user_id = user.user_id
                            else:
                                # Fallback: Still process as unknown but log the ID
                                user_id = f"GHOST_{ref_id}"
                                print(f"[COSEC_WORKER] Warning: Ghost User {ref_id} on {dev_id}")
                    
                    if not user_id:
                        continue

                    # 2. Process via core logic (Decision Engine)
                    with next(get_db()) as db:
                        # Extract the IP from the URL
                        dev_ip = adapter.base_url.replace("http://", "").split(":")[0]
                        # Use the friendly name for the device
                        dev_name = next((d["name"] for d in COSEC_CONFIG["devices"] if d["id"] == dev_id), dev_id)
                        
                        await self.process_request(
                            db, 
                            user_id, 
                            raw_event["method"], 
                            dev_name,
                            ip_address=dev_ip,
                            bypass_cache=False # Enable duplicate prevention
                        )
                        
                    # 3. Update progress for THIS device
                    self.last_indices[dev_id] = raw_event["index"]

        except Exception as e:
            # Silencing connection errors to keep logs clean for multiple devices
            pass
