"""Verify that the backend process in this project actually exposes Revenue routes.

Run from backend/:
    python verify_revenue_route.py

This is intentionally independent of MongoDB/authentication. It imports the
FastAPI app and checks route registration only, which makes it useful for
catching a stale/wrong backend before opening the Admin Revenue page.
"""
from server import app

EXPECTED = (
    "/api/revenue",
    "/api/revenue/employees",
    "/api/revenue/agents",
    "/api/revenue/customers",
    "/api/revenue/transactions",
    "/api/admin/dashboard/revenue-summary",
    "/api/admin/dashboard/employee-revenue",
    "/api/admin/dashboard/customer-revenue",
    "/api/admin/dashboard/agent-revenue",
    "/api/admin/kanban/{board}/lists",       # serves /api/admin/kanban/revenue/lists
    "/api/admin/kanban/{board}/lists/{list_id}",
    "/api/admin/kanban/{board}/move",
    "/api/admin/kanban/revenue/cards",
)

paths = {getattr(route, "path", "") for route in app.routes}
missing = [path for path in EXPECTED if path not in paths]

if missing:
    print("REVENUE ROUTES: FAIL")
    for path in missing:
        print(f"  MISSING: {path}")
    raise SystemExit(1)

print("REVENUE ROUTES: OK")
for path in EXPECTED:
    print(f"  OK: {path}")
