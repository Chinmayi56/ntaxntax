"""Add Customer -> Payment Terms (Term 1/2/3) + Customer Documents.

Runs the real FastAPI app in-process on mongomock-motor (pip install mongomock-motor httpx pytest).
Run:  cd backend && python -m pytest -c /dev/null --rootdir=. tests/test_customer_terms_documents.py
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
os.environ.setdefault("DB_NAME", "ntaxco_terms_docs_test")
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


def _new_customer(client, admin, name="Acme Traders"):
    r = client.post("/api/customers", headers=admin, json={
        "cust_id": f"C-{uuid.uuid4().hex[:5]}", "business_name": name, "email": f"{uuid.uuid4().hex[:6]}@example.com", "mobile": "9999999999"})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _term(client, admin, cid, key, name, amount, status="Paid"):
    return client.post("/api/payments", headers=admin, json={
        "customer_id": cid, "term_name": name, "amount": amount, "total": amount, "status": status,
        "payment_date": "2026-10-01", "payment_method": "Cash", "client_request_id": f"initial-{key}"})


def _billing(client, admin, cid):
    r = client.get(f"/api/admin/customers/{cid}/billing", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_customer_without_terms_has_zero_billing(client, admin):
    c = _new_customer(client, admin)
    b = _billing(client, admin, c["id"])
    assert (b["total_billed"], b["total_paid"], b["outstanding"]) == (0, 0, 0)


def test_one_term_and_three_terms_totals(client, admin):
    one = _new_customer(client, admin)
    assert _term(client, admin, one["id"], "term1", "Term 1", 10000).status_code == 200
    b = _billing(client, admin, one["id"])
    assert b["total_paid"] == 10000 and b["total_billed"] == 10000 and b["outstanding"] == 0

    three = _new_customer(client, admin)
    for key, name, amt in (("term1", "Term 1", 10000), ("term2", "Term 2", 5000), ("term3", "Term 3", 7000)):
        assert _term(client, admin, three["id"], key, name, amt).status_code == 200
    b = _billing(client, admin, three["id"])
    assert b["total_paid"] == 22000 and b["total_billed"] == 22000 and b["outstanding"] == 0
    rows = client.get("/api/payments", headers=admin, params={"page_size": 500}).json()["data"]
    mine = [p for p in rows if p.get("customer_id") == three["id"]]
    assert sorted(p["term_name"] for p in mine) == ["Term 1", "Term 2", "Term 3"]


def test_equal_amount_terms_are_not_collapsed(client, admin):
    c = _new_customer(client, admin)
    for key, name in (("term1", "Term 1"), ("term2", "Term 2"), ("term3", "Term 3")):
        assert _term(client, admin, c["id"], key, name, 5000).status_code == 200
    assert _billing(client, admin, c["id"])["total_paid"] == 15000


def test_duplicate_submit_does_not_duplicate_term(client, admin):
    c = _new_customer(client, admin)
    first = _term(client, admin, c["id"], "term1", "Term 1", 10000)
    second = _term(client, admin, c["id"], "term1", "Term 1", 10000)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    rows = client.get("/api/payments", headers=admin, params={"page_size": 500}).json()["data"]
    assert len([p for p in rows if p.get("customer_id") == c["id"]]) == 1
    assert _billing(client, admin, c["id"])["total_paid"] == 10000


def test_invalid_amounts_rejected(client, admin):
    c = _new_customer(client, admin)
    assert _term(client, admin, c["id"], "term1", "Term 1", -5).status_code == 400
    assert _term(client, admin, c["id"], "term1", "Term 1", 0).status_code == 400
    assert client.post("/api/payments", headers=admin, json={"customer_id": c["id"], "term_name": "Term 1", "amount": "abc"}).status_code == 400


def test_later_payment_updates_totals(client, admin):
    c = _new_customer(client, admin)
    assert _term(client, admin, c["id"], "term1", "Term 1", 10000).status_code == 200
    r = client.post("/api/payments", headers=admin, json={
        "customer_id": c["id"], "term_name": "Term 4", "amount": 3000, "status": "Paid", "payment_date": "2026-10-02", "payment_method": "UPI"})
    assert r.status_code == 200, r.text
    b = _billing(client, admin, c["id"])
    assert b["total_paid"] == 13000 and b["total_billed"] == 13000 and b["outstanding"] == 0


def test_pending_term_shows_outstanding(client, admin):
    c = _new_customer(client, admin)
    assert _term(client, admin, c["id"], "term1", "Term 1", 10000).status_code == 200
    assert _term(client, admin, c["id"], "term2", "Term 2", 4000, status="Pending").status_code == 200
    b = _billing(client, admin, c["id"])
    assert b["total_billed"] == 14000 and b["total_paid"] == 10000 and b["outstanding"] == 4000


def _doc(client, admin, cid, name="PAN.pdf", raw=PDF, mime="application/pdf", **extra):
    return client.post("/api/documents", headers=admin, json={
        "customer_id": cid, "doc_type": "PAN Card", "title": "PAN Card", "file_name": name,
        "file_size": len(raw), "file_data": data_url(raw, mime), **extra})


def test_documents_upload_list_view_delete(client, admin):
    c = _new_customer(client, admin)
    other = _new_customer(client, admin, "Other Co")
    pdf = _doc(client, admin, c["id"])
    assert pdf.status_code == 200, pdf.text
    png = _doc(client, admin, c["id"], "Company.png", PNG + b"1", "image/png")
    jpg = _doc(client, admin, c["id"], "Aadhaar.jpg", JPG + b"2", "image/jpeg")
    assert png.status_code == 200 and jpg.status_code == 200
    doc = pdf.json()["data"]
    assert doc["customer_id"] == c["id"] and doc["mime_type"] == "application/pdf" and doc["file_size"] == len(PDF) and doc["file_hash"]

    listing = client.get("/api/documents", headers=admin, params={"page_size": 500}).json()["data"]
    mine = [d for d in listing if d.get("customer_id") == c["id"]]
    assert len(mine) == 3 and all("file_data" not in d for d in mine)
    assert not [d for d in listing if d.get("customer_id") == other["id"]]

    full = client.get(f"/api/documents/{doc['id']}", headers=admin).json()["data"]
    assert full["file_data"].startswith("data:application/pdf;base64,")

    assert client.delete(f"/api/documents/{doc['id']}", headers=admin).status_code == 200
    left = [d for d in client.get("/api/documents", headers=admin, params={"page_size": 500}).json()["data"] if d.get("customer_id") == c["id"]]
    assert len(left) == 2
    assert client.get(f"/api/customers/{c['id']}", headers=admin).status_code == 200


def test_duplicate_document_rejected_with_clear_message(client, admin):
    c = _new_customer(client, admin)
    assert _doc(client, admin, c["id"]).status_code == 200
    again = _doc(client, admin, c["id"], "PAN copy.pdf")
    assert again.status_code == 409 and "already been uploaded" in again.json()["detail"]


@pytest.mark.parametrize("name,raw,mime,expected", [
    ("virus.exe", PDF, "application/pdf", "not allowed"),
    ("notes.txt", b"hello", "text/plain", "Unsupported"),
    ("fake.pdf", b"not really a pdf", "application/pdf", "does not match"),
    ("pic.pdf", PNG, "image/png", "does not match the file type"),
    ("empty.pdf", b"", "application/pdf", "empty"),
])
def test_unsafe_documents_rejected(client, admin, name, raw, mime, expected):
    c = _new_customer(client, admin)
    r = _doc(client, admin, c["id"], name, raw, mime)
    assert r.status_code == 400, r.text
    assert expected.lower() in r.json()["detail"].lower()


def test_oversized_document_rejected(client, admin):
    c = _new_customer(client, admin)
    big = PDF + b"0" * 6_000_000
    r = _doc(client, admin, c["id"], "big.pdf", big)
    assert r.status_code == 400 and "too large" in r.json()["detail"].lower()


def test_document_requires_valid_customer_and_admin_auth(client, admin):
    assert client.post("/api/documents", headers=admin, json={"file_name": "a.pdf", "file_data": data_url(PDF, "application/pdf")}).status_code == 422
    assert client.post("/api/documents", headers=admin, json={"customer_id": "NOPE", "file_name": "a.pdf", "file_data": data_url(PDF, "application/pdf")}).status_code == 422
    assert client.post("/api/documents", json={"customer_id": "x", "file_name": "a.pdf"}).status_code in (401, 403)


def test_customer_cannot_read_another_customers_document(client, admin):
    owner = _new_customer(client, admin)
    doc = _doc(client, admin, owner["id"]).json()["data"]
    email = f"intruder-{uuid.uuid4().hex[:5]}@example.com"
    r = client.post("/api/auth/register", json={"full_name": "Intruder", "email": email, "password": "CustPass#123", "role": "customer", "mobile": "8888888888"})
    assert r.status_code == 200, r.text
    hdr = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    assert client.get(f"/api/documents/{doc['id']}", headers=hdr).status_code in (403, 404)
    assert client.delete(f"/api/documents/{doc['id']}", headers=hdr).status_code in (403, 404)
    assert client.post("/api/payments", headers=hdr, json={"customer_id": owner["id"], "amount": 10}).status_code == 403


def test_deleting_customer_removes_its_documents(client, admin):
    c = _new_customer(client, admin)
    _doc(client, admin, c["id"])
    assert client.delete(f"/api/customers/{c['id']}", headers=admin).status_code == 200
    left = [d for d in client.get("/api/documents", headers=admin, params={"page_size": 500}).json()["data"] if d.get("customer_id") == c["id"]]
    assert left == []
