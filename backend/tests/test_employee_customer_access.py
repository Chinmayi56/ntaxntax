"""Employee <-> assigned customer / document authorization (server-side).

Live-server pattern like test_security.py: needs REACT_APP_BACKEND_URL pointing at a running
backend + MongoDB, plus ADMIN_EMAIL / ADMIN_PASSWORD and two employee logins
(EMP_A_EMAIL/EMP_A_PASSWORD, EMP_B_EMAIL/EMP_B_PASSWORD) belonging to two different
employee profiles. Creates and cleans up its own customers/documents.
"""
import base64
import os
import uuid
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE_URL}/api"
pytestmark = pytest.mark.skipif(
    not (BASE_URL and os.environ.get("ADMIN_EMAIL") and os.environ.get("EMP_A_EMAIL") and os.environ.get("EMP_B_EMAIL")),
    reason="set REACT_APP_BACKEND_URL, ADMIN_EMAIL/ADMIN_PASSWORD, EMP_A_*/EMP_B_* to run",
)
PDF = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4\n%test\n").decode()


def _login(email, password, role):
    r = requests.post(f"{API}/auth/portal/login", json={"email": email, "password": password, "role": role})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture(scope="module")
def admin():
    return _login(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"], "admin")


@pytest.fixture(scope="module")
def emp_a():
    return _login(os.environ["EMP_A_EMAIL"], os.environ["EMP_A_PASSWORD"], "employee")


@pytest.fixture(scope="module")
def emp_b():
    return _login(os.environ["EMP_B_EMAIL"], os.environ["EMP_B_PASSWORD"], "employee")


def _me(h):
    r = requests.get(f"{API}/employees/me", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.fixture(scope="module")
def setup(admin, emp_a, emp_b):
    a, b = _me(emp_a), _me(emp_b)
    created = []

    def mk(label, emp):
        r = requests.post(f"{API}/customers", headers=admin, json={
            "business_name": f"ZZ-{label}-{uuid.uuid4().hex[:6]}", "mobile": f"9{uuid.uuid4().int % 10**9:09d}",
            "email": f"{uuid.uuid4().hex[:8]}@example.com", "employee_id": emp["id"], "status": "Active"})
        assert r.status_code in (200, 201), r.text
        cust = r.json()["data"]
        d = requests.post(f"{API}/documents", headers=admin, json={
            "customer_id": cust["id"], "file_name": f"{label}.pdf", "file_data": PDF, "file_size": 20, "status": "Approved"})
        assert d.status_code in (200, 201), d.text
        created.append((cust["id"], d.json()["data"]["id"]))
        return cust, d.json()["data"]

    ca, da = mk("A", a)
    cb, db = mk("B", b)
    yield {"a": a, "b": b, "ca": ca, "cb": cb, "da": da, "db": db}
    for cid, did in created:
        requests.delete(f"{API}/documents/{did}", headers=admin)
        requests.delete(f"{API}/customers/{cid}", headers=admin)


def _ids(r):
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["data"]}


def test_employee_lists_only_assigned_customers(emp_a, emp_b, setup):
    assert setup["ca"]["id"] in _ids(requests.get(f"{API}/customers", headers=emp_a))
    assert setup["cb"]["id"] not in _ids(requests.get(f"{API}/customers", headers=emp_a))
    assert setup["cb"]["id"] in _ids(requests.get(f"{API}/customers", headers=emp_b))


def test_employee_customer_detail_is_authorized_server_side(emp_a, setup):
    assert requests.get(f"{API}/customers/{setup['ca']['id']}", headers=emp_a).status_code == 200
    assert requests.get(f"{API}/customers/{setup['cb']['id']}", headers=emp_a).status_code in (403, 404)


def test_employee_document_list_and_detail(emp_a, setup):
    ids = _ids(requests.get(f"{API}/documents", headers=emp_a))
    assert setup["da"]["id"] in ids and setup["db"]["id"] not in ids
    own = requests.get(f"{API}/documents/{setup['da']['id']}", headers=emp_a)
    assert own.status_code == 200 and own.json()["data"].get("file_data")
    other = requests.get(f"{API}/documents/{setup['db']['id']}", headers=emp_a)
    assert other.status_code in (403, 404)
    assert "file_data" not in other.text


def test_employee_cannot_upload_for_unassigned_customer(emp_a, setup):
    r = requests.post(f"{API}/documents", headers=emp_a, json={
        "customer_id": setup["cb"]["id"], "file_name": "x.pdf", "file_data": PDF, "file_size": 20})
    assert r.status_code == 403


def test_reassignment_moves_access(admin, emp_a, emp_b, setup):
    # Admin re-assigns customer A to employee B by name only (as the edit form does).
    r = requests.put(f"{API}/customers/{setup['ca']['id']}", headers=admin, json={"assigned_employee": setup["b"]["name"]})
    assert r.status_code == 200, r.text
    assert setup["ca"]["id"] not in _ids(requests.get(f"{API}/customers", headers=emp_a))
    assert setup["ca"]["id"] in _ids(requests.get(f"{API}/customers", headers=emp_b))
    assert requests.get(f"{API}/documents/{setup['da']['id']}", headers=emp_a).status_code in (403, 404)
    assert requests.get(f"{API}/documents/{setup['da']['id']}", headers=emp_b).status_code == 200
