"""Customer Documents: list (no file_data), single GET with file_data, isolation, filenames.

Runs the real FastAPI app in-process on mongomock-motor.
Run:  cd backend && python -m pytest -c /dev/null --rootdir=. tests/test_customer_document_preview.py
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
os.environ.setdefault("DB_NAME", "ntaxco_doc_preview_test")
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
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
DOCX = b"PK\x03\x04" + b"\x00" * 32


def data_url(raw, mime):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client):
    email, pw = f"admin-{uuid.uuid4().hex[:6]}@example.com", "AdminPass#123"
    LOOP.run_until_complete(server.db.users.insert_one({
        "id": f"ADMIN-{uuid.uuid4().hex[:6].upper()}", "name": "Test Admin", "email": email, "role": "admin",
        "password_hash": server.hash_password(pw), "status": "active", "meta": {}}))
    r = client.post("/api/auth/admin/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _register(client, admin, name, mobile):
    email = f"{name.lower()}.{uuid.uuid4().hex[:5]}@example.com"
    r = client.post("/api/auth/register", json={"full_name": name, "email": email, "password": "CustPass#123",
                                                 "role": "customer", "mobile": mobile})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    cid = [c for c in client.get("/api/customers", headers=admin).json()["data"] if c.get("email") == email][0]["id"]
    return headers, cid


@pytest.fixture(scope="module")
def world(client, admin):
    a_h, a = _register(client, admin, "DocAlpha", "9200000001")
    b_h, b = _register(client, admin, "DocBravo", "9200000002")
    return dict(a_h=a_h, a=a, b_h=b_h, b=b)


def _admin_upload(client, admin, cid, name, raw, mime):
    r = client.post("/api/documents", headers=admin, json={
        "customer_id": cid, "doc_type": "PAN", "file_name": name, "file_data": data_url(raw, mime)})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_admin_upload_is_owned_by_customer_and_stores_file_data(client, admin, world):
    doc = _admin_upload(client, admin, world["a"], "PAN.pdf", PDF, "application/pdf")
    assert doc["customer_id"] == world["a"]
    stored = LOOP.run_until_complete(server.db["erp_documents"].find_one({"id": doc["id"]}))
    assert stored["customer_id"] == world["a"] and stored["file_data"].startswith("data:application/pdf;base64,")


def test_list_has_no_file_data_and_single_get_has_it(client, admin, world):
    doc = _admin_upload(client, admin, world["a"], "List.pdf", PDF, "application/pdf")
    rows = client.get("/api/documents", headers=world["a_h"]).json()["data"]
    mine = [d for d in rows if d["id"] == doc["id"]]
    assert mine and "file_data" not in mine[0]
    r = client.get(f"/api/documents/{doc['id']}", headers=world["a_h"])
    assert r.status_code == 200
    assert r.json()["data"]["file_data"].startswith("data:application/pdf;base64,")
    admin_view = client.get(f"/api/documents/{doc['id']}", headers=admin)
    assert admin_view.status_code == 200 and admin_view.json()["data"]["file_data"]
    assert all("file_data" not in d for d in client.get("/api/documents", headers=admin).json()["data"])


def test_other_customer_cannot_read_document(client, admin, world):
    doc = _admin_upload(client, admin, world["a"], "Private.pdf", PDF, "application/pdf")
    r = client.get(f"/api/documents/{doc['id']}", headers=world["b_h"])
    assert r.status_code in (403, 404)
    assert "file_data" not in r.text
    assert doc["id"] not in [d["id"] for d in client.get("/api/documents", headers=world["b_h"]).json()["data"]]
    assert client.get(f"/api/documents/{doc['id']}").status_code in (401, 403)


@pytest.mark.parametrize("name,raw,mime", [
    ("test.jpg", JPG, "image/jpeg"), ("test.png", PNG, "image/png"), ("test.webp", WEBP, "image/webp"),
    ("test.docx", DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
def test_customer_upload_keeps_real_file_name_and_owner(client, world, name, raw, mime):
    # Exactly what the Customer page sends: `name`, no customer_id.
    r = client.post("/api/documents", headers=world["a_h"], json={
        "name": name, "category": "Uploads", "mime_type": mime, "file_data": data_url(raw, mime)})
    assert r.status_code == 200, r.text
    doc = r.json()["data"]
    assert doc["customer_id"] == world["a"] and doc["file_name"] == name
    full = client.get(f"/api/documents/{doc['id']}", headers=world["a_h"]).json()["data"]
    assert full["file_name"] == name and full["mime_type"] == mime and full["file_data"]


def test_customer_cannot_choose_another_customer(client, world):
    r = client.post("/api/documents", headers=world["a_h"], json={
        "customer_id": world["b"], "name": "x.pdf", "file_data": data_url(PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["customer_id"] == world["a"]


def test_upload_validation_still_enforced(client, world):
    bad = client.post("/api/documents", headers=world["a_h"], json={"name": "evil.exe", "file_data": data_url(PDF, "application/pdf")})
    assert bad.status_code == 400
    mismatch = client.post("/api/documents", headers=world["a_h"], json={"name": "fake.pdf", "file_data": data_url(b"not a pdf", "application/pdf")})
    assert mismatch.status_code == 400
