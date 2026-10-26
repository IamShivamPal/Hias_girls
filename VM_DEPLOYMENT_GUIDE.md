# HIAS VM Deployment Guide

This guide describes deploying the HIAS React/Vite frontend and FastAPI backend to a Linux VM, using PostgreSQL for persistent data. It also lists the backend API routes and the configuration the deployer needs.

## 1. Deployment Layout

Recommended production layout:

- `https://hias.example.com` serves the built frontend from `frontend/dist` using Nginx.
- `https://api.example.com` is served by Nginx and reverse-proxied to Uvicorn at `127.0.0.1:8000`.
- PostgreSQL runs on the VM or a private database host. It is not exposed to the browser or public internet.
- The VM must be able to reach the COSEC devices over the site LAN or a VPN.

Replace `example.com` with the actual domain. A same-origin deployment is also possible, but this guide uses separate frontend and API subdomains for simpler proxy configuration.

## 2. VM Requirements

Suggested starting size: Ubuntu 22.04/24.04, 2 vCPU, 4 GB RAM, and 20 GB disk. Increase resources based on event volume, concurrent users, and database retention needs.

Install:

- Python 3.10 or newer and `python3-venv`
- Node.js 20.19+ or 22.12+ and npm for Vite 8 builds
- PostgreSQL 15+ (or a reachable private PostgreSQL service)
- Nginx
- Git

The backend requirements include GPIO packages. GPIO relay control requires compatible physical hardware; a generic cloud VM may not provide GPIO support. Validate dependency installation on the target VM. Do not rely on GPIO for door control unless the VM is physically connected to the required relay hardware.

## 3. Network and Security

- Allow inbound `80/tcp` and `443/tcp` for web traffic.
- Allow SSH (`22/tcp`) only from approved administrator IPs.
- Keep Uvicorn (`8000/tcp`) bound to localhost and do not expose it publicly.
- Keep PostgreSQL (`5432/tcp`) private; permit access only from the backend host.
- Permit the VM to connect to all configured COSEC device addresses and ports. Confirm the correct routes/firewall rules with the network administrator.
- Configure TLS certificates for both hostnames.
- The backend currently has permissive CORS (`allow_origins=["*"]`) and no authentication was found in the inspected application routes. Restrict access to a trusted network/VPN or add authentication and authorization before exposing the API publicly. CORS alone is not access control.
- Protect credentials in a root-owned environment file. Do not commit secrets or include them in a public handoff document.

## 4. Database Setup

The backend uses SQLAlchemy and supports SQLite and PostgreSQL. For a production VM, PostgreSQL is recommended. Create a database and dedicated account, and ensure the account has access only to the HIAS database.

Example connection string (replace placeholders and use a strong secret):

```text
postgresql://hias_app:REPLACE_WITH_SECRET@127.0.0.1:5432/hias
```

The Python dependency `psycopg2-binary` is in `backend/requirements.txt`. On startup, the app calls `Base.metadata.create_all`; this creates missing tables but is not a database migration system. Back up the database before upgrades and use explicit migrations if schema changes require them.

### Migrate existing Render PostgreSQL data

Run these commands from a trusted environment that can reach both databases. Keep the connection strings private and avoid putting them in shell history where possible.

```bash
pg_dump "$SOURCE_DATABASE_URL" > hias.sql
psql "$VM_DATABASE_URL" < hias.sql
```

Here `SOURCE_DATABASE_URL` is the Render database URL and `VM_DATABASE_URL` is the VM PostgreSQL URL. Verify the restored users and events before switching the frontend to the VM.

If using SQLite instead, set `DATABASE_URL=sqlite:////absolute/path/to/hias.db`, ensure the backend service account can write to the directory, and back up that file regularly. SQLite is best kept to a single backend instance.

## 5. Environment Variables

Create a protected backend environment file such as `/etc/hias/backend.env` and restrict it to the service administrator (for example, owner `root`, mode `600`). Use actual values only on the VM.

```env
# Backend
DATABASE_URL=postgresql://hias_app:REPLACE_WITH_SECRET@127.0.0.1:5432/hias
COSEC_DEVICE_URLS=http://DEVICE_IP_1,http://DEVICE_IP_2
COSEC_USERNAME=REPLACE_WITH_DEVICE_USERNAME
COSEC_PASSWORD=REPLACE_WITH_DEVICE_PASSWORD
COSEC_POLL_INTERVAL=2.0
PORT=8000
```

`COSEC_DEVICE_URLS` is a comma-separated list of device base URLs. The running code reads this variable in `backend/app/background.py`. The current `backend/.env.example` uses `COSEC_BASE_URL`, which the multi-device worker does not read; use `COSEC_DEVICE_URLS` unless the code is changed.

The frontend variable is separate and is read at **build time**:

```env
VITE_API_URL=https://api.example.com
```

Use the API origin only, without an endpoint path or trailing slash. Rebuild the frontend after changing it.

## 6. Build and Run

From the repository root on the VM:

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Run Uvicorn for a local smoke test (load the protected environment file in the service configuration before production use):

```bash
cd backend
. .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For production, run the same Uvicorn command under `systemd`, configure it to load `/etc/hias/backend.env`, start it at boot, and restart it on failure. Run it as a dedicated unprivileged service account with access to the project and any required persistent directories.

Build the frontend with the correct API URL:

```bash
cd frontend
VITE_API_URL=https://api.example.com npm ci
VITE_API_URL=https://api.example.com npm run build
```

Configure Nginx to serve `frontend/dist` for `hias.example.com` and proxy requests for `api.example.com` to `http://127.0.0.1:8000`. For `/events/stream`, disable proxy buffering and allow long-lived responses so Server-Sent Events remain live. Enable TLS for both hosts.

## 7. Frontend Deployment Note

The API base URL is defined in `frontend/src/api/config.js`. Set `VITE_API_URL` explicitly for production; its fallback uses plain HTTP on port `8000`.

Before deploying, update these two direct localhost requests to use the configured API base URL:

- `frontend/src/App.jsx` requests `/system/time` from `http://127.0.0.1:8000`.
- `frontend/src/components/StatusPanel.jsx` requests `/system/status` from `http://127.0.0.1:8000`.

In a visitor's browser, `127.0.0.1` means the visitor's own computer, not the VM. Without this change those status requests will fail after deployment.

## 8. Backend API Reference

All paths below are relative to the backend API base URL, for example `https://api.example.com`. FastAPI also serves interactive documentation at `/docs`, ReDoc at `/redoc`, and the OpenAPI schema at `/openapi.json` unless those routes are disabled in code.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Backend health check |
| `POST` | `/access/event` | Submit an access event |
| `GET` | `/events?limit=10` | List recent events; `limit` is optional |
| `GET` | `/events/stream` | Live event stream using Server-Sent Events (SSE) |
| `POST` | `/events/clear` | Delete all access events |
| `POST` | `/manual/override` | Trigger a manual override |
| `GET` | `/settings` | Read system settings |
| `POST` | `/settings` | Update system settings |
| `GET` | `/system/time` | Read backend server time |
| `GET` | `/system/status` | Read controller and hardware status |
| `GET` | `/system/devices` | Read configured device status |
| `POST` | `/system/force-delete-hw` | Delete a user from configured hardware |
| `POST` | `/system/master-reset` | **Destructive:** wipe users/events and hardware credentials |
| `GET` | `/review/queue` | List events awaiting review |
| `POST` | `/review/action` | Approve or reject a review event |
| `POST` | `/simulate/review` | Create a simulated review event |
| `GET` | `/reports/stats` | Read report statistics |
| `GET` | `/reports/export` | Download access events as CSV |
| `GET` | `/alerts` | List alerts derived from recent events |
| `POST` | `/alerts/clear` | Clear events that generate alerts |
| `GET` | `/users` | List users |
| `POST` | `/users` | Add/update a user and sync with devices |
| `GET` | `/users/search?q=...` | Search by user ID or name |
| `POST` | `/users/sync-all` | Sync user profiles to configured devices |
| `POST` | `/users/sync-from-device` | Import users from the first configured device |
| `DELETE` | `/users/{user_id}` | Delete a user from database and hardware |
| `POST` | `/users/{user_id}/enroll-face?gate_idx=0` | Start face enrollment on a device |
| `POST` | `/users/{user_id}/enroll-complete` | Mark face enrollment complete |
| `GET` | `/stats/summary` | Read access totals and overrides |

The user, event, and settings routes connect to the configured database. There is no separate browser-facing database API. Treat endpoints that clear data, delete users, trigger overrides, or reset hardware as privileged operations.

## 9. Verification Checklist

- `https://api.example.com/health` returns a healthy status.
- `https://api.example.com/docs` loads from the expected network.
- The frontend loads over HTTPS and API requests target `https://api.example.com`, not localhost or port `8000` from the browser.
- `/events/stream` remains connected and new events appear in the dashboard.
- Users and events persist after restarting the backend.
- The VM can reach each COSEC device; confirm polling and a controlled test event before enabling real access decisions.
- A database backup can be restored successfully.
- Public access is restricted appropriately; credentials and database ports are not exposed.

## 10. Useful Project References

- [Backend configuration](backend/app/database.py)
- [COSEC worker configuration](backend/app/background.py)
- [Backend API implementation](backend/app/main.py)
- [Frontend API configuration](frontend/src/api/config.js)
- [Render deployment guide](RENDER_DEPLOY_GUIDE.md)