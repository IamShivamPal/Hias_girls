import requests
from requests.auth import HTTPDigestAuth
import sys
sys.path.insert(0, '.')
from app.database import SessionLocal, engine, Base
from app.models import User

Base.metadata.create_all(bind=engine)

DEVICE_URL = "http://192.168.1.101"
AUTH = HTTPDigestAuth("admin", "12345")

db = SessionLocal()

for ref_id in range(1, 26):
    resp = requests.get(
        f"{DEVICE_URL}/device.cgi/users",
        params={"action": "get", "ref-user-id": ref_id},
        auth=AUTH
    )
    text = resp.text
    print(f"ref_id={ref_id}: {text.strip()[:80]}")

    # Parse response - handle multiline format
    data = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if '=' in line:
            k, v = line.split('=', 1)
            data[k.strip()] = v.strip()

    user_id = data.get('user-id')
    name = data.get('name', '')

    if not user_id:
        print(f"  Skipping ref_id={ref_id} - no user-id found")
        continue

    # Check if already exists
    existing = db.query(User).filter(User.user_id == user_id).first()
    if existing:
        # Update device_ref_id if missing
        if not existing.device_ref_id:
            existing.device_ref_id = ref_id
            db.commit()
            print(f"  Updated ref_id for existing: {user_id}")
        else:
            print(f"  Already exists: {user_id}")
        continue

    # Add to DB
    db.add(User(
        user_id=user_id,
        name=name,
        role="Resident",
        device_ref_id=ref_id
    ))
    db.commit()
    print(f"  Added: {user_id} - {name} (ref_id={ref_id})")

db.close()
print("Done!")