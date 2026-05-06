
import requests
from typing import List, Dict, Optional
from app.schemas import Method

class COSECAdapter:
    def __init__(self, ip: str, username: str = "hias", password: str = "hias"):
        # Handle both raw IP "192.168.50.2" and full URL "http://192.168.50.2"
        if ip.startswith("http"):
            self.base_url = ip.rstrip('/')
        else:
            self.base_url = f"http://{ip}".rstrip('/')
            
        self.username = username
        self.password = password
        from requests.auth import HTTPDigestAuth
        self.auth = HTTPDigestAuth(username, password)

    def _get(self, endpoint: str, params: Dict) -> str:
        # CONFIRMED: This firmware requires standard & ampersand separators.
        # Semicolons trigger 'Incomplete Command' on every request.
        # Proven by probe_auth.py: users?action=get&ref-user-id=1 works,
        # users?action=get;ref-user-id=1 fails with the same generic error.
        from urllib.parse import urlencode
        query_string = urlencode(params)
        url = f"{self.base_url}/device.cgi/{endpoint}?{query_string}"
        
        print(f"[COSEC] {endpoint} -> {params.get('action', 'get')}")
        response = requests.get(
            url, 
            auth=self.auth, 
            timeout=10
        )
        print(f"[COSEC] {endpoint} -> {response.status_code}")
        return response.text

    def _post(self, endpoint: str, data: Dict) -> str:
        # Hybrid POST: Identity params must be in the URL for the firmware 
        # to identify the target, while the payload (like name) goes in the body.
        url_params = {k: v for k, v in data.items() if k in ["action", "user-id", "ref-user-id"]}
        body_data = {k: v for k, v in data.items() if k not in ["action", "user-id", "ref-user-id"]}
        
        from urllib.parse import urlencode
        query_string = urlencode(url_params)
        url = f"{self.base_url}/device.cgi/{endpoint}?{query_string}"
        
        print(f"[COSEC_POST] {endpoint} -> {url_params.get('action', 'set')}")
        response = requests.post(
            url, 
            data=body_data,
            auth=self.auth, 
            timeout=15
        )
        print(f"[COSEC_POST] {endpoint} -> {response.status_code}")
        return response.text

    async def sync_user_to_device(self, user_id: str, name: str, ref_user_id: int) -> bool:
        try:
            import asyncio
            # Truncate name to 15 chars and remove non-alphanumeric chars for hardware compatibility
            clean_name = "".join(filter(str.isalnum, name))[:15]
            
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: self._get("users", {
                "action": "set",
                "user-id": user_id,
                "ref-user-id": ref_user_id,
                "name": clean_name,
                "user-active": 1,
                "validity-enable": 0
            }))
            print(f"[COSEC] Sync user response: {raw}")
            success = "Response-Code=0" in raw or "response-code=0" in raw
            if not success:
                print(f"[COSEC_ERROR] Device rejected user sync. Response: {raw}")
            return success
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

    async def get_current_event_index(self) -> int:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: self._get("command", {
                "action": "geteventcount"
            }))
            # Response format: "Roll-Over-Count=0 Seq-Number=231"
            print(f"[DEBUG] Raw Event Count Response: {raw}")
            for token in raw.split():
                if ("event-count" in token.lower() or "seq-number" in token.lower()) and "=" in token:
                    return int(token.split('=')[1].strip())
            return 0
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to get event index: {e}")
            return 0

    async def fetch_events(self, last_index: int) -> List[Dict]:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # Hardware requires 'getevent' and 'seq-number' and 'roll-over-count'
            raw = await loop.run_in_executor(None, lambda: self._get("events", {
                "action": "getevent",
                "seq-number": last_index + 1,
                "roll-over-count": "0", # We'll start with 0
                "no-of-events": "10"
            }))
            return self._parse_events(raw)
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to fetch events: {e}")
            return []

    async def enroll_user_face(self, user_id: str) -> bool:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: self._get("enrolluser", {
                "action": "enroll",
                "type": "7",
                "user-id": user_id
            }))
            print(f"[COSEC] Enroll face response: {raw}")
            return "Response-Code=0" in raw or "response-code=0" in raw
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to trigger face enrollment: {e}")
            return False

    async def fetch_all_users(self) -> List[Dict]:
        try:
            import asyncio
            loop = asyncio.get_event_loop()

            # Step 1: Get count
            count_raw = await loop.run_in_executor(None, lambda: self._get("command", {
                "action": "getusercount"
            }))

            user_count = 0
            for line in count_raw.splitlines():
                line = line.strip()
                if '=' in line:
                    key, value = line.split('=', 1)
                    if key.strip().lower() == "enrolled-user-count":
                        try:
                            user_count = int(value.strip())
                        except ValueError: pass

            print(f"[COSEC] Device reported {user_count} enrolled users")

            users = []
            # Step 2: Iterate by ref-user-id (1..N)
            for i in range(1, user_count + 1):
                user_raw = await loop.run_in_executor(None, lambda i=i: self._get("users", {
                    "action": "get",
                    "ref-user-id": i
                }))

                current = {}
                for line in user_raw.splitlines():
                    line = line.strip()
                    if '=' in line:
                        key, value = line.split('=', 1)
                        current[key.strip()] = value.strip()

                uid = current.get("user-id", "")
                name = current.get("name", "")
                ref_id = current.get("ref-user-id", str(i))

                if uid and name:
                    users.append({
                        "user_id": uid,
                        "name": name,
                        "ref_id": int(ref_id) if ref_id.isdigit() else i
                    })

            print(f"[COSEC] Fetched {len(users)} users from device")
            return users
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to fetch all users: {e}")
            return []

    async def trigger_buzzer(self):
        """Triggers a physical alarm pulse on the device."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # This pulses the 'Alarm' output which is much more reliable
            await loop.run_in_executor(None, lambda: self._get("door", {
                "action": "pulse",
                "alarm": "1"
            }))
        except:
            pass

    async def show_denial_message(self):
        """Displays 'ACCESS DENIED' on the device screen."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            params = {
                "action": "set", "mode": "1", "message-id": "1", "enable": "1", "user": "0",
                "message0": "ACCESS DENIED", "message1": "UNKNOWN USER",
                "from-date-dd": "01", "from-date-mm": "01", "from-date-yyyy": "2024",
                "to-date-dd": "31", "to-date-mm": "12", "to-date-yyyy": "2037"
            }
            for i in range(7): params[f"week-day{i}"] = "1"
            await loop.run_in_executor(None, lambda: self._get("device-display", params))
        except: pass

    def _parse_events(self, raw_data: str) -> List[Dict]:
        events = []
        try:
            # The hardware sends events either as XML or space-separated key=value pairs.
            # We normalize everything by removing tags and splitting into tokens.
            clean_data = raw_data.replace('<', ' ').replace('>', ' ').replace('/', ' ')
            
            # We'll group tokens by their sequence number
            event_buckets = {}
            current_seq = None
            
            for token in clean_data.split():
                if '=' in token:
                    key, value = token.split('=', 1)
                    k = key.strip().lower()
                    v = value.strip()
                    
                    if k in ['seq-no', 'seq-number']:
                        current_seq = v
                        if current_seq not in event_buckets:
                            event_buckets[current_seq] = {}
                    
                    if current_seq:
                        event_buckets[current_seq][k] = v

            # Now process the completed buckets
            for seq, data in event_buckets.items():
                eid = data.get('event-id')
                ref_id = data.get('detail-1')
                if eid and ref_id:
                    event_id = int(eid)
                    events.append({
                        "index": int(seq),
                        "ref_id": int(ref_id),
                        "timestamp": f"{data.get('date', '')} {data.get('time', '')}",
                        "method": Method.FACE,
                        # 100-series: Allowed, Others: Denied
                        "allowed": 100 <= event_id <= 199
                    })
        except Exception as e:
            print(f"[COSEC_PARSE_ERROR] {e}")
        return events

    async def open_door(self):
        """Remotely triggers the door relay to unlock."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # Pulses the lock relay to unlock the door
            await loop.run_in_executor(None, lambda: self._get("door", {
                "action": "pulse",
                "lock": "1"
            }))
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to open door: {e}")
    async def upload_template(self, user_id: str, ref_user_id: int, template: str) -> bool:
        """Uploads a face template using standard & ampersand separators.
        
        Uses the confirmed working ampersand format. Template data is sent 
        in the POST body to avoid URL length constraints.
        """
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # Standard ampersand format
            url = f"{self.base_url}/device.cgi/biometric?action=settemplate&template-type=7&template-index=0&user-id={ref_user_id}"
            
            print(f"[COSEC_BIO] upload -> {ref_user_id}")
            # Send template-data in the POST body
            response = requests.post(
                url,
                data={"template-data": template},
                auth=self.auth,
                timeout=15
            )
            result = "Response-Code=0" in response.text or "response-code=0" in response.text
            if not result:
                print(f"[COSEC_BIO] Upload failed for {user_id}: {response.text[:120]}")
            return result
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to upload template for {user_id}: {e}")
            return False

    async def download_template(self, user_id: str, ref_user_id: int) -> str:
        """Downloads a face template using standard & ampersand separators.

        CONFIRMED FIX (probe_auth.py): The firmware requires & separators, NOT
        semicolons. Semicolons trigger 'Incomplete Command' universally.
        Proven: users?action=get&ref-user-id=1  =>  returns user data  ✅
                users?action=get;ref-user-id=1  =>  Incomplete Command  ❌
        """
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # Standard & separators — confirmed working by probe_auth.py
            url = (f"{self.base_url}/device.cgi/biometric"
                   f"?action=gettemplate&template-type=7&user-id={ref_user_id}")

            print(f"[COSEC_BIO] download -> {ref_user_id}")
            response = await loop.run_in_executor(
                None, lambda: requests.get(url, auth=self.auth, timeout=10)
            )
            raw = response.text
            print(f"[COSEC_BIO] Download response for {user_id}: {raw[:150]}")

            for key in ["template-data=", "template="]:
                if key in raw:
                    parts = raw.split(key)
                    if len(parts) > 1:
                        tmpl = parts[1].split("\r")[0].split("\n")[0].strip()
                        if tmpl and len(tmpl) > 10:
                            return tmpl
            return None
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to download template for {user_id}: {e}")
            return None

    async def delete_user(self, user_id: str):
        """Deletes a user profile from the device."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # CORRECT COSEC API: users/delete
            await loop.run_in_executor(None, lambda: self._get("users", {
                "action": "delete",
                "user-id": str(user_id)
            }))
            return True
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to delete user {user_id}: {e}")
            return False

    async def delete_user_from_device(self, user_id: str):
        """Legacy alias for delete_user to maintain compatibility."""
        # Note: This assumes ref_id is same as user_id for legacy calls
        # or we might need to look it up. For now, let's just route it.
        return await self.delete_user(user_id)

    async def upsert_user(self, user_id: str, name: str, ref_user_id: int):
        """Creates or updates a user profile on the device."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # Prepare the profile data
            payload = {
                "action": "set",
                "user-id": str(user_id),
                "ref-user-id": str(ref_user_id),
                "name": name,
                "user-active": "1",
                "enable-fr": "1",
                "vip": "1",
            }
            
            # Use GET to ensure all params are in the URL (firmware ignores POST body for users)
            raw = await loop.run_in_executor(None, lambda: self._get("users", payload))
            print(f"[COSEC] Profile update response for {name} ({user_id}): {raw}")
            return "Response-Code=0" in raw or "response-code=0" in raw
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to upsert user {name}: {e}")
            return False
