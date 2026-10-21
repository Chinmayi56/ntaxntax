"""Dynamic module BOARD (Trello/Kanban) — lists, cards, drag/move, ordering, legacy migration.

Runs the real FastAPI app in-process on mongomock-motor.
Run:  cd backend && python -m pytest -c /dev/null --rootdir=. tests/test_dynamic_board.py -q
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
os.environ.setdefault("DB_NAME", "ntaxco_dynamic_board")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ.pop("NTAXCO_ENABLE_DEMO_SEED", None)

import motor.motor_asyncio as _motor  # noqa: E402
_motor.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client):
    email, pw = f"admin-{uuid.uuid4().hex[:6]}@example.com", "AdminPass#123"
    run(server.db.users.insert_one({
        "id": f"ADMIN-{uuid.uuid4().hex[:6].upper()}", "name": "Test Admin", "email": email, "role": "admin",
        "password_hash": server.hash_password(pw), "status": "active", "meta": {}}))
    r = client.post("/api/auth/admin/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture(scope="module")
def module_key(client, admin):
    r = client.post("/api/admin/dynamic-config", headers=admin, json={
        "name": f"Employee Management {uuid.uuid4().hex[:4]}", "description": "Manage employee workflow",
        "fields": [{"label": "Employee Name", "type": "text", "required": True},
                   {"label": "Employee ID", "type": "text"},
                   {"label": "Designation", "type": "select", "options": ["Developer", "Accountant"]}]})
    assert r.status_code == 200, r.text
    return r.json()["key"]


def board(client, admin, key):
    r = client.get(f"/api/dynamic-board/{key}", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def names(client, admin, key, list_id):
    b = board(client, admin, key)
    cards = sorted([c for c in b["cards"] if c["list_id"] == list_id], key=lambda c: c["position"])
    return [c["employee_name"] for c in cards]


def test_new_module_gets_default_list_and_requires_auth(client, admin, module_key):
    assert client.get(f"/api/dynamic-board/{module_key}").status_code == 401
    b = board(client, admin, module_key)
    assert [l["name"] for l in b["lists"]] == ["General"]
    assert b["cards"] == []


def test_lists_crud_and_order(client, admin, module_key):
    ids = {}
    for n in ["New Employees", "Documents Pending", "Verification", "Completed"]:
        r = client.post(f"/api/dynamic-board/{module_key}/lists", headers=admin, json={"name": n, "description": f"{n} list"})
        assert r.status_code == 200, r.text
        ids[n] = r.json()["data"]["id"]
    assert client.post(f"/api/dynamic-board/{module_key}/lists", headers=admin, json={"name": "new employees"}).status_code == 409
    assert client.post(f"/api/dynamic-board/{module_key}/lists", headers=admin, json={"name": "  "}).status_code == 400
    r = client.put(f"/api/dynamic-board/{module_key}/lists/{ids['Verification']}", headers=admin, json={"name": "In Verification"})
    assert r.status_code == 200 and r.json()["data"]["name"] == "In Verification"
    order = [ids["Completed"], ids["New Employees"]]
    r = client.put(f"/api/dynamic-board/{module_key}/lists-order", headers=admin, json={"list_ids": order})
    assert r.status_code == 200
    got = [l["id"] for l in board(client, admin, module_key)["lists"]]
    assert got[:2] == order and set(got) >= set(ids.values())  # persisted; unlisted lists keep their place after
    # restore a sensible order for the following tests
    full = [l["id"] for l in board(client, admin, module_key)["lists"]]
    full.sort(key=lambda i: ["General", "New Employees", "Documents Pending", "In Verification", "Completed"].index(
        next(l["name"] for l in board(client, admin, module_key)["lists"] if l["id"] == i)))
    client.put(f"/api/dynamic-board/{module_key}/lists-order", headers=admin, json={"list_ids": full})
    pytest.board_lists = ids


def test_cards_create_in_list_and_validate(client, admin, module_key):
    ids = pytest.board_lists
    for n in ["John", "Ravi", "Priya"]:
        r = client.post(f"/api/dynamic-records/{module_key}", headers=admin,
                        json={"employee_name": n, "employee_id": n.upper(), "list_id": ids["New Employees"]})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["list_id"] == ids["New Employees"]
    assert client.post(f"/api/dynamic-records/{module_key}", headers=admin, json={"employee_id": "X"}).status_code == 400  # required field
    assert names(client, admin, module_key, ids["New Employees"]) == ["John", "Ravi", "Priya"]
    # unknown list -> falls back to the default list instead of an orphan
    r = client.post(f"/api/dynamic-records/{module_key}", headers=admin, json={"employee_name": "Stray", "list_id": "LST-NOPE"})
    assert r.status_code == 200
    b = board(client, admin, module_key)
    assert r.json()["data"]["list_id"] == b["lists"][0]["id"]


def test_move_between_lists_persists(client, admin, module_key):
    ids = pytest.board_lists
    b = board(client, admin, module_key)
    john = next(c for c in b["cards"] if c["employee_name"] == "John")
    r = client.put(f"/api/dynamic-board/{module_key}/cards/{john['id']}/move", headers=admin, json={"list_id": ids["Documents Pending"]})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["list_id"] == ids["Documents Pending"]
    # "refresh": fetch the board again
    assert names(client, admin, module_key, ids["Documents Pending"]) == ["John"]
    assert names(client, admin, module_key, ids["New Employees"]) == ["Ravi", "Priya"]
    # editing the card's fields must NOT lose its list
    r = client.put(f"/api/dynamic-records/{module_key}/{john['id']}", headers=admin, json={"employee_name": "John Kumar"})
    assert r.status_code == 200
    assert names(client, admin, module_key, ids["Documents Pending"]) == ["John Kumar"]


def test_reorder_within_list_and_insert_position(client, admin, module_key):
    ids = pytest.board_lists
    b = board(client, admin, module_key)
    priya = next(c for c in b["cards"] if c["employee_name"] == "Priya")
    r = client.put(f"/api/dynamic-board/{module_key}/cards/{priya['id']}/move", headers=admin, json={"list_id": ids["New Employees"], "position": 0})
    assert r.status_code == 200
    assert names(client, admin, module_key, ids["New Employees"]) == ["Priya", "Ravi"]
    # drop into the middle of another list
    ravi = next(c for c in b["cards"] if c["employee_name"] == "Ravi")
    client.put(f"/api/dynamic-board/{module_key}/cards/{ravi['id']}/move", headers=admin, json={"list_id": ids["Documents Pending"], "position": 0})
    assert names(client, admin, module_key, ids["Documents Pending"]) == ["Ravi", "John Kumar"]
    assert names(client, admin, module_key, ids["New Employees"]) == ["Priya"]


def test_move_errors(client, admin, module_key):
    ids = pytest.board_lists
    b = board(client, admin, module_key)
    any_card = b["cards"][0]
    assert client.put(f"/api/dynamic-board/{module_key}/cards/{any_card['id']}/move", headers=admin, json={"list_id": "LST-NOPE"}).status_code == 404
    assert client.put(f"/api/dynamic-board/{module_key}/cards/REC-NOPE/move", headers=admin, json={"list_id": ids["Completed"]}).status_code == 404
    assert client.put(f"/api/dynamic-board/nope-module/cards/{any_card['id']}/move", headers=admin, json={"list_id": ids["Completed"]}).status_code == 404


def test_delete_list_never_loses_cards(client, admin, module_key):
    ids = pytest.board_lists
    url = f"/api/dynamic-board/{module_key}/lists/{ids['Documents Pending']}"
    assert client.delete(url, headers=admin).status_code == 409                     # has cards, no target
    assert client.delete(url, headers=admin, params={"move_to": ids["Documents Pending"]}).status_code == 409
    r = client.delete(url, headers=admin, params={"move_to": ids["Completed"]})
    assert r.status_code == 200 and r.json()["data"]["moved_cards"] == 2
    assert names(client, admin, module_key, ids["Completed"]) == ["Ravi", "John Kumar"]
    assert all(l["id"] != ids["Documents Pending"] for l in board(client, admin, module_key)["lists"])


def test_legacy_records_land_in_default_list(client, admin):
    r = client.post("/api/admin/dynamic-config", headers=admin, json={
        "name": f"Legacy Mod {uuid.uuid4().hex[:4]}", "fields": [{"label": "Title", "type": "text"}]})
    key = r.json()["key"]
    # records created before boards existed: no list_id / position at all
    for i in range(3):
        run(server.db.dynamic_records.insert_one({"id": f"REC-OLD{i}", "module_key": key, "title": f"Old {i}",
                                                  "created_at": f"2025-01-0{i+1}T00:00:00+00:00"}))
    b = board(client, admin, key)
    assert [l["name"] for l in b["lists"]] == ["General"]
    assert [c["title"] for c in sorted(b["cards"], key=lambda c: c["position"])] == ["Old 0", "Old 1", "Old 2"]
    assert all(c["list_id"] == b["lists"][0]["id"] for c in b["cards"])
    # last remaining list can't be deleted
    assert client.delete(f"/api/dynamic-board/{key}/lists/{b['lists'][0]['id']}", headers=admin).status_code == 400


def test_deleting_module_removes_its_lists(client, admin):
    r = client.post("/api/admin/dynamic-config", headers=admin, json={"name": f"Temp {uuid.uuid4().hex[:4]}", "fields": [{"label": "A", "type": "text"}]})
    cfg = r.json()
    board(client, admin, cfg["key"])
    assert client.delete(f"/api/admin/dynamic-config/{cfg['id']}", headers=admin).status_code == 200
    assert run(server.db.dynamic_lists.count_documents({"module_key": cfg["key"]})) == 0
