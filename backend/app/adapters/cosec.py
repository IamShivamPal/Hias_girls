
import requests
from typing import List, Dict, Optional
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:
    _IST = None
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
        
        from requests import Session
        from requests.auth import HTTPDigestAuth
        self.session = Session()
        self.auth = HTTPDigestAuth(username, password)
        self.session.auth = self.auth

    def _get(self, endpoint: str, params: Dict) -> str:
        # CONFIRMED: This firmware requires standard & ampersand separators.
        # Semicolons trigger 'Incomplete Command' on every request.
        # Proven by probe_auth.py: users?action=get&ref-user-id=1 works,
        # users?action=get;ref-user-id=1 fails with the same generic error.
        from urllib.parse import urlencode
        query_string = urlencode(params)
        url = f"{self.base_url}/device.cgi/{endpoint}?{query_string}"
        
        print(f"[COSEC] {endpoint} -> {params.get('action', 'get')}")
        response = self.session.get(
            url, 
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

    @staticmethod
    def _is_user_record(raw: str) -> bool:
        """A successful users/get returns the profile fields (user-id=...), not Response-Code=0."""
        if "Response-Code=0" in raw or "response-code=0" in raw:
            return True
        for line in raw.splitlines():
            k, sep, v = line.strip().partition('=')
            if sep and k.strip().lower() == "user-id" and v.strip():
                return True
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
            found_count = 0
            # Probe a reasonable range to find used IDs. 
            # We stop once we've found all users reported by getusercount, 
            # or hit a sensible safety margin (user_count + 100).
            safety_limit = user_count + 100
            for i in range(1, safety_limit + 1):
                if found_count >= user_count:
                    break
                    
                # Rate limiting to prevent hardware from dropping requests
                await asyncio.sleep(0.05)
                
                user_raw = await loop.run_in_executor(None, lambda i=i: self._get("users", {
                    "action": "get",
                    "ref-user-id": i
                }))

                if self._is_user_record(user_raw):
                    current = {"ref_id": i}
                    for line in user_raw.splitlines():
                        line = line.strip()
                        if '=' in line:
                            key, value = line.split('=', 1)
                            if key.strip().lower() == "user-id":
                                current["user_id"] = value.strip()
                            elif key.strip().lower() == "name":
                                current["name"] = value.strip()
                    users.append(current)
                    found_count += 1
            
            if found_count < user_count:
                print(f"[WARNING] Only found {found_count}/{user_count} users on device. Some IDs might be outside probe range.")
                
            return users
        except Exception as e:
            print(f"[COSEC_ERROR] Error fetching all users: {e}")
            return []

    async def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        """Check if a user exists on hardware and return their ref_id if found."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            user_raw = await loop.run_in_executor(None, lambda: self._get("users", {
                "action": "get",
                "user-id": user_id
            }))
            if self._is_user_record(user_raw):
                user_data = {}
                for line in user_raw.splitlines():
                    line = line.strip()
                    if '=' in line:
                        k, v = line.split('=', 1)
                        if k.strip().lower() == "user-id":
                            user_data["user_id"] = v.strip()
                        elif k.strip().lower() == "ref-user-id":
                            try:
                                user_data["ref_id"] = int(v.strip())
                            except ValueError: pass
                        elif k.strip().lower() == "name":
                            user_data["name"] = v.strip()
                return user_data
        except Exception:
            pass
        return None

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

    def _parse_hw_timestamp(self, date_str: str, time_str: str) -> datetime:
        """Parse hardware date (D/M/YYYY) and time (H:M:S) as IST, return UTC datetime."""
        try:
            # Hardware format: date=10/5/2026 time=17:9:31 (DD/MM/YYYY H:M:S, IST clock)
            dt_naive = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%d/%m/%Y %H:%M:%S")
            if _IST:
                # Attach IST timezone and convert to UTC
                dt_ist = dt_naive.replace(tzinfo=_IST)
                return dt_ist.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                # Fallback: subtract 5h30m manually
                from datetime import timedelta
                return dt_naive - timedelta(hours=5, minutes=30)
        except Exception:
            # If parsing fails, fall back to current UTC time
            return datetime.utcnow()

    def _parse_events(self, raw_data: str) -> List[Dict]:
        events = []
        try:
            # The hardware sends events as newline-separated key=value pairs,
            # grouped by sequence number (one blank line between events).
            # We parse line-by-line to avoid issues with '/' in date values.
            event_buckets = {}
            current_seq = None

            for line in raw_data.splitlines():
                line = line.strip()
                if not line:
                    continue
                if '=' not in line:
                    continue
                key, _, value = line.partition('=')
                k = key.strip().lower()
                v = value.strip()

                if k in ('seq-no', 'seq-number'):
                    current_seq = v
                    if current_seq not in event_buckets:
                        event_buckets[current_seq] = {}

                if current_seq is not None:
                    event_buckets[current_seq][k] = v

            # Now process the completed buckets
            for seq, data in event_buckets.items():
                eid = data.get('event-id')
                ref_id = data.get('detail-1')
                if eid and ref_id:
                    event_id = int(eid)
                    # Parse the hardware's IST timestamp into a UTC datetime object
                    hw_ts = self._parse_hw_timestamp(
                        data.get('date', ''),
                        data.get('time', '')
                    )
                    events.append({
                        "index": int(seq),
                        "ref_id": int(ref_id),
                        "hw_timestamp": hw_ts,   # UTC datetime from hardware clock
                        "method": Method.FACE,
                        # 100-series: Allowed, Others: Denied
                        "allowed": 100 <= event_id <= 199
                    })
        except Exception as e:
            print(f"[COSEC_PARSE_ERROR] {e}")
        return events

    async def get_user_id_by_ref(self, ref_id: int) -> Optional[str]:
        """Resolve a numeric ref-user-id from THIS device to its alphanumeric user-id.
        
        The hardware reports ref-user-id in events (detail-1). Each device may assign
        different ref-user-ids to the same physical user, so we query the source device
        directly to get the canonical alphanumeric user-id for DB lookup.
        """
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: self._get("users", {
                "action": "get",
                "ref-user-id": ref_id
            }))
            for line in raw.splitlines():
                line = line.strip()
                if '=' in line:
                    k, _, v = line.partition('=')
                    if k.strip().lower() == "user-id":
                        uid = v.strip()
                        if uid:
                            return uid
        except Exception as e:
            print(f"[COSEC_ERROR] get_user_id_by_ref({ref_id}): {e}")
        return None

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
        return await self.delete_user(user_id)

    async def delete_all_credentials(self):
        """
        Force delete ALL biometric credentials from the device.
        Type 0 = All (Finger, Palm, Face).
        """
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, lambda: self._get("command", {
                "action": "deletecredential",
                "type": "0"
            }))
            print(f"[COSEC] Global credential wipe on {self.base_url}: {raw}")
            return "Response-Code=0" in raw or "response-code=0" in raw
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to wipe credentials on {self.base_url}: {e}")
            return False

    async def upsert_user(self, user_id: str, name: str, ref_user_id: int):
        """Creates or updates a user profile on the device."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # Prepare the profile data
            # Sanitize name: remove non-alphanumeric (keep spaces) and truncate to 15 chars
            clean_name = "".join(c for c in name if c.isalnum() or c.isspace())[:15].strip()
            
            payload = {
                "action": "set",
                "user-id": str(user_id),
                "ref-user-id": str(ref_user_id),
                "name": clean_name,
                "user-active": "1",
                "enable-fr": "1",
                "vip": "1",
            }
            
            # Use GET to ensure all params are in the URL (firmware ignores POST body for users)
            raw = await loop.run_in_executor(None, lambda: self._get("users", payload))
            print(f"[COSEC] Profile update response for {name} ({user_id}): {raw}")
            
            # Handle non-zero response codes
            if "Response-Code=0" in raw or "response-code=0" in raw:
                return True
                
            if "Response-Code=21" in raw or "response-code=21" in raw:
                existing = await self.get_user_by_id(user_id)
                if existing:
                    if existing["ref_id"] != ref_user_id:
                        print(f"[SYNC_REPAIR] User {user_id} exists with ref_id={existing['ref_id']} on {self.base_url}. Deleting and re-syncing to global ref_id={ref_user_id}")
                        await self.delete_user_from_device(user_id)
                        # Wait a moment for hardware to process deletion
                        await asyncio.sleep(0.5)
                        # Retry the set operation
                        raw = await loop.run_in_executor(None, lambda: self._get("users", payload))
                        print(f"[COSEC] Re-sync response for {user_id}: {raw}")
                        return "Response-Code=0" in raw or "response-code=0" in raw
                    else:
                        # Same user, same ID, hardware just being pedantic?
                        print(f"[SYNC] User {user_id} already exists with correct ref_id={ref_user_id} on {self.base_url}")
                        return True
                else:
                    # Code 21 = "Reference User ID exists": the ref-user-id slot belongs to
                    # a DIFFERENT user. Deleting user_id can't free it, so fail loudly.
                    print(f"[SYNC_ERROR] ref-user-id={ref_user_id} on {self.base_url} is already assigned to another user; not overwriting.")
                    return False

            print(f"[COSEC_ERROR] Upsert failed on {self.base_url} with raw response: {raw}")
            return False
        except Exception as e:
            print(f"[COSEC_ERROR] Failed to upsert user {name}: {e}")
            return False
