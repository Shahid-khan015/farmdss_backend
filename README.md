## Backend (FastAPI) - Tractor DSS

### 1) Setup Python env

From `backend/`:

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

### 2) Start PostgreSQL (recommended via Docker)

From repo root:

```powershell
docker compose up -d
```

If you see an error about `dockerDesktopLinuxEngine`, start **Docker Desktop** first (Docker daemon must be running).

### 3) Configure env

Copy `backend/.env.example` to `backend/.env` and adjust if needed:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/tractor_dss
SECRET_KEY=your-secret-key
DEBUG=True
```

### SQLite fallback (dev-only)

If PostgreSQL/Docker isn’t available, in **DEBUG** mode the backend will automatically fall back to a local SQLite file (`backend/tractor_dss.db`) so you can still run the app end-to-end.

### 4) Create DB tables (Alembic)

From `backend/`:

```powershell
alembic revision --autogenerate -m "init"
alembic upgrade head
```

### 5) Run API

From `backend/`:

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Swagger: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

