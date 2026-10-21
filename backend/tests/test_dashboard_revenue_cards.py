"""Admin Dashboard Employee / Customer / Agent Revenue APIs.

Runs the real FastAPI app in-process on mongomock-motor (pip install mongomock-motor httpx pytest).
Run:  cd backend && python -m pytest -c /dev/null --rootdir=. tests/test_dashboard_revenue_cards.py
Data is inserted straight into the existing collections (erp_employees / erp_agents / erp_customers / erp_payments).
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

pytest.importorskip("mongomock_motor")
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "ntaxco_dash_rev_test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ.pop("NTAXCO_ENABLE_DEMO_SEED", None)

import motor.motor_asyncio as _motor  # noqa: E402
_motor.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402

LOOP = asyncio.new_event_loop()
ENDPOINTS = ("employee-revenue", "customer-revenue", "agent-revenue")


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


def _login(client, role):
    email, pw = f"{role}-{uuid.uuid4().hex[:6]}@example.com", "AdminPass#123"
    LOOP.run_until_complete(server.db.users.insert_one({
        "id": f"U-{uuid.uuid4().hex[:6].upper()}", "name": f"Test {role}", "email": email, "role": role,
        "password_hash": server.hash_password(pw), "status": "active", "meta": {}}))
    path = "/api/auth/admin/login" if role == "admin" else "/api/auth/login"
    r = client.post(path, json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture(scope="module")
def admin(client):
    return _login(client, "admin")


@pytest.fixture(scope="module")
def seeded(client):
    n = uuid.uuid4().hex[:6].upper()
    ids = {"emp": f"EMP-{n}", "agent": f"AGT-{n}", "cust": f"CUS-{n}"}
    db = server.db
    LOOP.run_until_complete(db["erp_employees"].insert_one({"id": ids["emp"], "name": f"Emp {n}", "email": f"e{n}@x.com", "mobile": "9000000001", "department": "Tax", "designation": "Consultant", "status": "Active"}))
    LOOP.run_until_complete(db["erp_agents"].insert_one({"id": ids["agent"], "name": f"Agent {n}", "email": f"a{n}@x.com", "mobile": "9000000002", "status": "Active"}))
    LOOP.run_until_complete(db["erp_customers"].insert_one({"id": ids["cust"], "business_name": f"Acme {n}", "owner": f"Owner {n}", "email": f"c{n}@x.com", "mobile": "9000000003", "assigned_employee_id": ids["emp"], "assigned_agent_id": ids["agent"], "status": "Active"}))
    LOOP.run_until_complete(db["erp_payments"].insert_one({"id": f"PAY-{n}", "customer_id": ids["cust"], "employee_id": ids["emp"], "agent_id": ids["agent"], "amount": 4000, "total": 4000, "status": "Paid", "payment_date": "2026-10-01", "term_name": "Term 1"}))
    ids["n"] = n
    return ids


def test_unauthenticated_and_non_admin_are_rejected(client):
    customer = _login(client, "customer")
    for ep in ("revenue-summary",) + ENDPOINTS:
        assert client.get(f"/api/admin/dashboard/{ep}").status_code in (401, 403)
        assert client.get(f"/api/admin/dashboard/{ep}", headers=customer).status_code == 403


def test_summary_matches_existing_revenue_totals(client, admin, seeded):
    s = client.get("/api/admin/dashboard/revenue-summary", headers=admin)
    assert s.status_code == 200, s.text
    d = s.json()["data"]
    old = client.get("/api/revenue", headers=admin).json()["data"]
    for kind in ("employee", "customer", "agent"):
        assert d[kind]["total_revenue"] == old[kind]["total_revenue"]
    assert d["employee"]["total_revenue"] >= 4000


def test_detail_rows_carry_real_values(client, admin, seeded):
    emp = client.get("/api/admin/dashboard/employee-revenue", headers=admin, params={"search": seeded["emp"]}).json()["data"]["rows"]
    assert len(emp) == 1 and emp[0]["revenue"] == 4000 and emp[0]["customers_count"] == 1 and emp[0]["department"] == "Tax" and emp[0]["role"] == "Consultant"
    ag = client.get("/api/admin/dashboard/agent-revenue", headers=admin, params={"search": seeded["agent"]}).json()["data"]["rows"]
    assert len(ag) == 1 and ag[0]["revenue"] == 4000 and ag[0]["payments_count"] == 1
    cu = client.get("/api/admin/dashboard/customer-revenue", headers=admin, params={"search": seeded["cust"]}).json()["data"]["rows"]
    assert len(cu) == 1 and cu[0]["total_paid"] == 4000 and cu[0]["payments_count"] == 1 and cu[0]["company"].startswith("Acme")


def test_new_payment_is_reflected_without_duplicates(client, admin, seeded):
    before = client.get("/api/admin/dashboard/revenue-summary", headers=admin).json()["data"]
    LOOP.run_until_complete(server.db["erp_payments"].insert_one({"id": f"PAY2-{seeded['n']}", "customer_id": seeded["cust"], "employee_id": seeded["emp"], "agent_id": seeded["agent"], "amount": 1000, "total": 1000, "status": "Paid", "payment_date": "2026-10-02", "term_name": "Term 2"}))
    after = client.get("/api/admin/dashboard/revenue-summary", headers=admin).json()["data"]
    for kind in ("employee", "customer", "agent"):
        assert round(after[kind]["total_revenue"] - before[kind]["total_revenue"], 2) == 1000
