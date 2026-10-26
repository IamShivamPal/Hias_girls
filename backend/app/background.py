
import asyncio
import os
from datetime import datetime
from typing import Dict, Optional
from dotenv import load_dotenv

from app.database import get_db
from app.models import User
from app.schemas import Method
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

        # Per-device cache: dev_id -> {ref_id (int) -> user_id (str)}
        # Avoids redundant device queries once a ref_id has been resolved.
        self._ref_cache: Dict[str, Dict[int, str]] = {
            dev["id"]: {} for dev in COSEC_CONFIG["devices"]
        }

    def clear_cache(self):
        """Clears the in-memory device reference ID cache."""
        for dev_id in self._ref_cache:
            self._ref_cache[dev_id].clear()
        print("[COSEC_WORKER] Cleared in-memory reference cache.")

    async def start(self):
        self.running = True
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
            tasks = [self._loop_once(dev_id) for dev_id in self.adapters.keys()]
            await asyncio.gather(*tasks)
            await asyncio.sleep(COSEC_CONFIG["poll_interval"])

    async def _resolve_user_id(self, dev_id: str, ref_id: int) -> Optional[str]:
        """
        Resolve a hardware ref-user-id to an alphanumeric user_id.

        Strategy (in order):
        1. Per-device in-memory cache (fastest).
        2. DB lookup by device_ref_id (works when all devices share the same ref_id for a user).
        3. Live query to the SOURCE device for ref-user-id=N, then DB lookup by user_id.
           This correctly handles the case where each COSEC device assigns its own numeric
           ref-user-id independently, so the same person may have different ref_ids on
           different devices.
        """
        # 1. In-memory cache
        if ref_id in self._ref_cache[dev_id]:
            return self._ref_cache[dev_id][ref_id]

        # 2. Local DB lookup by device_ref_id
        try:
            with next(get_db()) as db:
                user = db.query(User).filter(User.device_ref_id == ref_id).first()
                if user:
                    self._ref_cache[dev_id][ref_id] = user.user_id
                    return user.user_id
        except Exception:
            pass

        # 3. Query the source device for the alphanumeric user-id
        adapter = self.adapters[dev_id]
        hw_user_id = await adapter.get_user_id_by_ref(ref_id)

        if hw_user_id:
            # Look up in DB by the canonical alphanumeric user_id
            try:
                with next(get_db()) as db:
                    user = db.query(User).filter(User.user_id == hw_user_id).first()
                    if user:
                        self._ref_cache[dev_id][ref_id] = user.user_id
                        print(f"[COSEC_WORKER] Resolved ref_id={ref_id} on {dev_id} "
                              f"-> user_id='{user.user_id}' via device query")
                        return user.user_id
            except Exception:
                pass

            print(f"[COSEC_WORKER] Warning: Device {dev_id} reports user '{hw_user_id}' "
                  f"(ref_id={ref_id}) but this user is not in the local DB.")
            ghost_id = f"GHOST_{hw_user_id}"
            self._ref_cache[dev_id][ref_id] = ghost_id
            return ghost_id

        # Could not resolve at all
        ghost_id = f"GHOST_{ref_id}"
        print(f"[COSEC_WORKER] Warning: Could not resolve ref_id={ref_id} on {dev_id} "
              f"(user may have been deleted from device).")
        self._ref_cache[dev_id][ref_id] = ghost_id
        return ghost_id

    async def _loop_once(self, dev_id):
        """Single polling iteration for one device."""
        adapter = self.adapters[dev_id]
        try:
            events = await adapter.fetch_events(self.last_indices[dev_id])

            if events:
                events.sort(key=lambda x: x["index"])

                for raw_event in events:
                    if raw_event["index"] <= self.last_indices[dev_id]:
                        continue

                    # 1. Identify User by Reference ID
                    ref_id = raw_event.get("ref_id")
                    if ref_id == 0:
                        user_id = "UNKNOWN"
                    elif ref_id:
                        user_id = await self._resolve_user_id(dev_id, ref_id)
                    else:
                        continue  # Missing ref_id — skip

                    if not user_id:
                        continue

                    # 2. Process via core logic (Decision Engine)
                    try:
                        with next(get_db()) as db:
                            dev_ip = adapter.base_url.replace("http://", "").split(":")[0]
                            dev_name = next(
                                (d["name"] for d in COSEC_CONFIG["devices"] if d["id"] == dev_id),
                                dev_id
                            )
                            # hw_timestamp is a UTC datetime parsed from the hardware clock (IST -> UTC).
                            # Passing it to process_request ensures the event is logged with the
                            # actual scan time rather than the server's receipt time.
                            hw_ts = raw_event.get("hw_timestamp")

                            await self.process_request(
                                db,
                                user_id,
                                raw_event["method"],
                                dev_name,
                                ip_address=dev_ip,
                                bypass_cache=False,
                                event_timestamp=hw_ts,
                            )
                    except Exception as e:
                        print(f"[COSEC_WORKER] Error processing event on {dev_id}: {e}")

                    # 3. Advance the index even if processing failed, to avoid infinite retries
                    self.last_indices[dev_id] = raw_event["index"]

        except Exception:
            # Silence connection errors — devices may be temporarily unreachable
            pass
