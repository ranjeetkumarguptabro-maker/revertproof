# API Reference & Testing Guide

## Dashboard REST API (`src/revertproof/dashboard.py`)

Start locally:
```bash
revertproof dashboard --host 127.0.0.1 --port 8000
```

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Serves the 7-tab RevertProof Web Dashboard |
| `GET` | `/api/health` | Health check + active DB engine (`postgresql` or `sqlite`) |
| `GET` | `/api/overview` | Aggregate real metrics from persisted `reports/` |
| `GET` | `/api/history?limit=50` | Lists persisted verification runs (newest first) |
| `GET` | `/api/reports/{run_id}` | Downloads machine-readable JSON evidence report |
| `GET` | `/api/reports/{run_id}/html` | Renders human-readable HTML evidence report |
| `POST` | `/api/scan` | Runs static SQL Migration Scanner on arbitrary SQL (`{"sql": "...", "migration_name": "..."}`) |
| `POST` | `/api/verify` | Runs full Safety Gate on custom SQL (`{"sql": "...", "domain": "banking", "fixture_count": 1000}`) |
| `POST` | `/api/run-preset` | Runs built-in scenarios (`banking_unsafe`, `banking_safe`, `banking_rollback`, `ecommerce_unsafe`, `ecommerce_safe`, `full_demo`) |

## Testing Guide

Run the full test suite and linter:
```bash
ruff check src tests
pytest -v --cov=revertproof
```

Run against PostgreSQL 16 (Docker or Amazon RDS):
```bash
docker compose up -d db
RP_DATABASE_URL="postgresql://rp_tester:rp_local_dev_only@localhost:54329/revertproof_test" pytest -v
```
