import requests
from requests.auth import HTTPDigestAuth
from typing import List, Dict
from app.schemas import Method


class COSECAdapter:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.auth = HTTPDigestAuth(username, password)
        self.timeout = 5.0

    def _get(self, endpoint: str, params: dict) -> str:
        """Make GET request using Digest Auth"""
        url = f"{self.base_url}/device.cgi/{endpoint}"
        response = requests.get(url, params=params, auth=self.auth, timeout=self.timeout)
        print(f"[COSEC] {endpoint} -> {response.status_code}")
        return response.text

    async def fetch_events(self, last_index: int = 0) -> List[Dict]:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: self._get("events", {
                "action": "getevent",
                "roll-over-count": 0,
                "seq-number": last_index + 1,
                "no-of-events": 10
            }))
            return self._parse_events(raw)
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to fetch events: {e}")
            return []

    async def sync_user_to_device(self, user_id: str, name: str, ref_user_id: int) -> bool:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: self._get("users", {
                "action": "set",
                "user-id": user_id,
                "ref-user-id": ref_user_id,
                "name": name,
                "user-active": 1
            }))
            print(f"[COSEC] Sync user response: {raw}")
            return "Response-Code=0" in raw or "response-code=0" in raw
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to sync user: {e}")
            return False

    async def delete_user_from_device(self, user_id: str) -> bool:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: self._get("users", {
                "action": "delete",
                "user-id": user_id
            }))
            print(f"[COSEC] Delete user response: {raw}")
            return "Response-Code=0" in raw or "response-code=0" in raw
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to delete user: {e}")
            return False

    async def open_door(self, door_id: str = "1") -> bool:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: self._get("commands", {
                "action": "set",
                "cmd": "open-door",
                "doorid": door_id
            }))
            return "Response-Code=0" in raw or "response-code=0" in raw
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to open door: {e}")
            return False

    def _parse_events(self, raw_data: str) -> List[Dict]:
        events = []
        try:
            current = {}
            for token in raw_data.split():
                if '=' in token:
                    key, value = token.split('=', 1)
                    current[key.strip()] = value.strip()
                if 'event-id' in current and 'detail-1' in current and 'seq-No' in current:
                    event_id = int(current.get('event-id', 0))
                    events.append({
                        "index": int(current.get('seq-No', 0)),
                        "device_user_id": current.get('detail-1', ''),
                        "timestamp": f"{current.get('date', '')} {current.get('time', '')}",
                        "method": Method.FACE,
                        "allowed": event_id in [101, 102, 103, 104, 105, 106, 111, 112]
                    })
                    current = {}
        except Exception as e:
            print(f"[COSEC_PARSE_ERROR] {e}")
        return events

    def _map_method(self, cosec_type: str) -> Method:
        mapping = {
            "face": Method.FACE,
            "rfid": Method.RFID,
            "finger": Method.FACE,
        }
        return mapping.get(cosec_type.lower(), Method.RFID)
