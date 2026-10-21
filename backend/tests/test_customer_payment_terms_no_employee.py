"""Add Customer + payment terms must not require employee_id / employee_name.

Runs the real FastAPI app in-process on mongomock-motor (pip install mongomock-motor httpx pytest).
Run:  cd backend && python -m pytest -c /dev/null --rootdir=. tests/test_customer_payment_terms_no_employee.py
"""
import asyncio
import base64
import os
import sys
import uuid
from pathlib import Path

import pytest

pytest.importorskip("mongomock_motor")
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "ntaxco_terms_noemp_test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ.pop("NTAXCO_ENABLE_DEMO_SEED", None)

import motor.motor_asyncio as _motor  # noqa: E402
_motor.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402

LOOP = asyncio.new_event_loop()
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


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


def _new_customer(client, admin, **extra):
    n = uuid.uuid4().hex[:6].upper()
    r = client.post("/api/customers", headers=admin, json={"cust_id": f"C-{n}", "business_name": f"Acme {n}", "assigned_employee": "", **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _term(client, admin, cust, name, amount, **extra):
    return client.post("/api/payments", headers=admin, json={
        "customer_id": cust["id"], "term_name": name, "amount": amount, "total": amount,
        "payment_date": "2026-10-01", "payment_method": "UPI", "status": "Paid",
        "client_request_id": f"t-{uuid.uuid4().hex[:8]}", **extra})


def test_customer_with_blank_or_whitespace_employee_is_created(client, admin):
    assert _new_customer(client, admin)["id"]
    n = uuid.uuid4().hex[:6].upper()
    r = client.post("/api/customers", headers=admin, json={"cust_id": f"C-{n}", "business_name": f"WS {n}", "assigned_employee": "   "})
    assert r.status_code in (200, 201), r.text


def test_term_payments_without_employee_and_custom_term(client, admin):
    cust = _new_customer(client, admin)
    for name, amt in (("Term 1", 10000), ("Term 2", 5000), ("Term 3", 2500), ("Milestone 1", 1000)):
        r = _term(client, admin, cust, name, amt)
        assert r.status_code in (200, 201), r.text
        data = r.json()["data"]
        assert data["term_name"] == name and "employee_id" not in data and "employee_name" not in data


def test_blank_employee_fields_on_payment_are_dropped_not_faked(client, admin):
    cust = _new_customer(client, admin)
    r = _term(client, admin, cust, "Term 1", 700, employee_id="", employee_name="  ")
    assert r.status_code in (200, 201), r.text
    data = r.json()["data"]
    assert "employee_id" not in data and "employee_name" not in data


def test_employee_assignment_still_validated(client, admin):
    n = uuid.uuid4().hex[:6].upper()
    r = client.post("/api/customers", headers=admin, json={"cust_id": f"C-{n}", "business_name": f"Bad {n}", "assigned_employee": "Nobody Such"})
    assert r.status_code == 422, r.text
