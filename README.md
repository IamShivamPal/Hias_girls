# HIAS - Hostel Intelligent Access System

HIAS is a centralized management system for hostel security, integrating Matrix COSEC biometric hardware with a modern web dashboard for real-time monitoring and access control.

## 🏗️ Project Structure

- **`backend/`**: FastAPI-based server handling logic, database (SQLite), and hardware integration.
- **`frontend/`**: React + Vite dashboard for administrative control and monitoring.
- **`logs_data/`**: Storage for event logs and audit trails.

---

## 🚀 Getting Started

Follow these instructions to get the system running locally.

### 📋 Prerequisites

- **Python**: 3.10 or higher
- **Node.js**: 18.0 or higher
- **NPM**: 9.0 or higher (or Yarn/PNPM)

---

### 🔧 Backend Setup

1. **Navigate to the backend directory**:
   ```bash
   cd backend
   ```

2. **Create and activate a virtual environment** (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   Copy the example file and update the values as needed (especially COSEC device IP and credentials):
   ```bash
   cp .env.example .env
   ```

5. **Start the FastAPI server**:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
   The backend API will be available at `http://localhost:8000`.

---

### 💻 Frontend Setup

1. **Navigate to the frontend directory**:
   ```bash
   cd frontend
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Configure environment variables**:
   Copy the example file and ensure `VITE_API_URL` points to your backend:
   ```bash
   cp .env.example .env
   ```

4. **Start the development server**:
   ```bash
   npm run dev
   ```
   The dashboard will be available at `http://localhost:5173` (or the port shown in your terminal).

---

## 🛠️ Key Commands

### Backend Scripts
- **Hardware Synchronization**: Sync user templates to COSEC devices.
  ```bash
  python sync_users.py
  ```
- **Simulate Access Events**: Useful for testing without hardware.
  ```bash
  python scripts/simulate.py
  ```

---

## 📖 Documentation & Resources

- [Render Deployment Guide](RENDER_DEPLOY_GUIDE.md) - Instructions for cloud deployment.
- [COSEC API Guide](api_guide.txt) - Low-level API documentation for hardware integration.

---

## 🛡️ Access Rules
The system uses a deterministic rule engine located in `backend/app/engine.py`:
- **Active Hours**: 07:00 - 19:00 (default).
- **Validation**: Cross-references biometric scans with the local `hias.db` database.
