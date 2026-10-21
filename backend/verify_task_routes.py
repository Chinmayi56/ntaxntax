"""Static verification for the production-critical task routes.
Run from backend/: python verify_task_routes.py
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
server = (ROOT / "server.py").read_text(encoding="utf-8")
erp = (ROOT / "erp.py").read_text(encoding="utf-8")

checks = {
    "ERP router uses /api prefix": 'router = APIRouter(prefix="/api")' in erp,
    "Employee tasks route exists": '@router.get("/employee/tasks")' in erp,
    "Employee task update route exists": '@router.put("/employee/tasks/{task_id}")' in erp,
    "Employee comment route exists": '@router.post("/employee/tasks/{task_id}/comment")' in erp,
    "Agent tasks route exists": '@router.get("/agent/tasks")' in erp,
    "ERP router is included by server": 'app.include_router(build_erp_router(db, get_current_user))' in server,
    "Production entrypoint is server:app": 'uvicorn server:app' in (ROOT / "Procfile").read_text(encoding="utf-8"),
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}: {name}")
if failed:
    raise SystemExit("Task route verification failed: " + "; ".join(failed))
print("All critical task-route checks passed.")
