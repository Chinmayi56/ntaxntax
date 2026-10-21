"""End-to-end Revenue + Revenue Kanban check against an in-memory MongoDB (mongomock-motor).

Not collected by pytest directly (no test_ prefix): tests/test_revenue_kanban.py runs it in a clean subprocess so
the in-memory client is installed before server.py is imported. Fixtures exist only inside this throw-away
in-memory database - no mock data is ever written to the project or to a real MongoDB.
"""
import os, sys, asyncio
os.environ.update(MONGO_URL="mongodb://mock", DB_NAME="t", JWT_SECRET="x"*40, ADMIN_EMAIL="admin@t.com", ADMIN_PASSWORD="Admin@12345")
import motor.motor_asyncio as m
from mongomock_motor import AsyncMongoMockClient
m.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import server
def client():
    c = TestClient(server.app); c.__enter__()
    r = c.post("/api/auth/admin/login", json={"email":"admin@t.com","password":"Admin@12345"})
    print("login", r.status_code, r.text[:200])
    tok = r.json()["data"]["access_token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    return c

import asyncio
from datetime import datetime, timedelta, timezone
c = client()
db = server.db
loop = asyncio.new_event_loop()
run = lambda coro: loop.run_until_complete(coro)
today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
d = lambda n: (today - timedelta(days=n)).isoformat()

# Test fixtures live ONLY in the in-memory mock DB used by this test (never in project code/data).
run(db.erp_customers.insert_many([
 {"id":"CUS-1","business_name":"Alpha Traders","assigned_employee":"Emp One","assigned_agent":"Agent A"},
 {"id":"CUS-2","business_name":"Beta Foods","assigned_employee":"Emp Two"},
 {"id":"CUS-3","business_name":"Gamma (no payments)"},
]))
run(db.erp_employees.insert_many([{"id":"EMP-1","name":"Emp One"},{"id":"EMP-2","name":"Emp Two"}]))
run(db.erp_agents.insert_many([{"id":"AG-1","name":"Agent A"}]))
run(db.erp_invoices.insert_many([
 {"id":"INV-1","invoice_no":"INV-1","customer_id":"CUS-1","customer":"Alpha Traders","service":"GST Filing","total":1000,"payment_status":"Partial","employee_id":"EMP-1","agent_id":"AG-1"},
 {"id":"INV-2","invoice_no":"INV-2","customer_id":"CUS-2","customer":"Beta Foods","service":"ITR","total":500,"payment_status":"Paid"},
 {"id":"INV-3","invoice_no":"INV-3","customer_id":"CUS-1","customer":"Alpha Traders","service":"TDS","total":300,"payment_status":"Cancelled","status":"Cancelled"},
]))
run(db.erp_payments.insert_many([
 {"id":"PMT-1","customer_id":"CUS-1","invoice_id":"INV-1","amount":400,"total":400,"status":"Completed","payment_date":d(0),"reference_no":"R1","mode":"UPI"},
 {"id":"PMT-1DUP","customer_id":"CUS-1","invoice_id":"INV-1","amount":400,"total":400,"status":"Completed","payment_date":d(0),"reference_no":"R1"},   # duplicate reference
 {"id":"PMT-2","customer_id":"CUS-2","invoice_id":"INV-2","amount":500,"total":500,"status":"Paid","payment_date":d(3),"created_at":d(3)},
 {"id":"PMT-3","customer_id":"CUS-1","invoice_id":"INV-1","amount":100,"total":100,"status":"Pending","payment_date":d(1)},                          # pending => not collected
 {"id":"APM-1","payment_type":"Agent Commission","agent_id":"AG-1","agent":"Agent A","amount":250,"total":250,"status":"Completed","txn_date":d(0)}, # payout => not revenue
 {"id":"PMT-9","customer_id":"CUS-1","invoice_id":"INV-3","amount":300,"total":300,"status":"Completed","payment_date":d(2)},                      # cancelled invoice
]))
ok = True
def check(name, cond, extra=""):
    global ok; ok &= bool(cond); print(("PASS" if cond else "FAIL"), name, extra)

r = c.get("/api/revenue"); j = r.json()["data"]
check("summary 200", r.status_code == 200)
check("total_revenue = 900 (400 + 500; no dup/pending/payout/cancelled-invoice)", j["total_revenue"] == 900, j["total_revenue"])
check("today_revenue = 400", j["today_revenue"] == 400, j["today_revenue"])
check("pending = existing billing rule (billed 1500 - paid incl. cancelled-invoice payment 1000) = 500? -> CUS-1 1000-700", j["pending_revenue"] == 300, j["pending_revenue"])
check("month/year >= today", j["year_revenue"] >= j["month_revenue"] >= j["today_revenue"], (j["month_revenue"], j["year_revenue"]))
r = c.get("/api/revenue/customers"); rows = r.json()["data"]["rows"]; by = {x["id"]: x for x in rows}
check("customers 200 + all 3 customers listed", r.status_code == 200 and set(by) == {"CUS-1","CUS-2","CUS-3"}, list(by))
a = by["CUS-1"]
check("CUS-1 billed/paid/outstanding", (a["total_billed"], a["paid_amount"], a["outstanding"]) == (1000, 700, 300), (a["total_billed"], a["paid_amount"], a["outstanding"]))
check("CUS-1 services + latest payment + txns", a["services"] == ["GST Filing"] and a["latest_payment"]["amount"] == 400 and a["transactions"] == 1, (a["services"], a["latest_payment"], a["transactions"]))
check("CUS-3 zero row has billing fields", by["CUS-3"]["total_billed"] == 0 and by["CUS-3"]["services"] == [])
r = c.get("/api/revenue/employees"); e = {x["id"]: x for x in r.json()["data"]["rows"]}
check("employees: EMP-1 400, EMP-2 500 (by id; EMP-2 via customer assignment)", e["EMP-1"]["revenue"] == 400 and e["EMP-2"]["revenue"] == 500, {k: v["revenue"] for k, v in e.items()})
r = c.get("/api/revenue/agents"); g = {x["id"]: x for x in r.json()["data"]["rows"]}
check("agents: AG-1 = 400 (payout NOT counted)", g["AG-1"]["revenue"] == 400, {k: v["revenue"] for k, v in g.items()})
r = c.get("/api/revenue/transactions?period=today"); check("transactions today", r.status_code == 200 and len(r.json()["data"]) == 1)
r = c.get("/api/revenue/transactions?period=pending"); pr = r.json()["data"]
check("pending transactions: only CUS-1 outstanding 300", len(pr) == 1 and pr[0]["pending_amount"] == 300, pr and pr[0]["pending_amount"])

# ---- Kanban
r = c.get("/api/admin/kanban/revenue/lists"); check("GET kanban lists 200", r.status_code == 200, r.status_code)
r = c.get("/api/admin/kanban/revenue/cards"); cards = r.json()["data"]; cb = {x["id"]: x for x in cards}
check("cards 200 real payments only", r.status_code == 200 and set(cb) == {"PMT-1", "PMT-2"}, list(cb))
c1 = cb["PMT-1"]
check("card fields", c1["customer"] == "Alpha Traders" and c1["service"] == "GST Filing" and c1["amount"] == 400 and c1["employee_name"] == "Emp One" and c1["agent_name"] == "Agent A" and c1["invoice_no"] == "INV-1" and c1["payment_method"] == "UPI" and c1["outstanding"] == 300 and c1["invoice_outstanding"] == 600, c1)
check("no None/NaN amounts", all(isinstance(x["amount"], (int, float)) and x["amount"] == x["amount"] for x in cards))
before = run(db.erp_payments.find_one({"id": "PMT-2"}, {"_id": 0}))
check("default column COMPLETED for paid", cb["PMT-2"]["kanban_column"] == "COMPLETED")
r = c.post("/api/admin/kanban/revenue/move", json={"record_id": "PMT-2", "target": "REVIEW"}); check("move -> REVIEW 200", r.status_code == 200, r.text[:120])
after = run(db.erp_payments.find_one({"id": "PMT-2"}, {"_id": 0}))
changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
check("only kanban fields changed in MongoDB", changed <= {"kanban_status", "kanban_list_id", "kanban_list_name", "kanban_updated_at"}, changed)
check("payment status/amount/customer untouched", all(after[k] == before[k] for k in ("status", "amount", "total", "customer_id", "invoice_id")))
cards2 = {x["id"]: x for x in c.get("/api/admin/kanban/revenue/cards").json()["data"]}
check("after 'refresh' card stays in REVIEW, payment status still Paid", cards2["PMT-2"]["kanban_column"] == "REVIEW" and cards2["PMT-2"]["payment_status"] == "Paid")
check("revenue totals unchanged by move", c.get("/api/revenue").json()["data"]["total_revenue"] == 900)
check("move of agent payout rejected", c.post("/api/admin/kanban/revenue/move", json={"record_id": "APM-1", "target": "REVIEW"}).status_code == 404)
check("move of pending payment rejected (not revenue)", c.post("/api/admin/kanban/revenue/move", json={"record_id": "PMT-3", "target": "REVIEW"}).status_code == 404)
check("invalid target 422", c.post("/api/admin/kanban/revenue/move", json={"record_id": "PMT-2", "target": "NOPE"}).status_code == 422)
# custom list CRUD
r = c.post("/api/admin/kanban/revenue/lists", json={"name": "Waiting for Client"}); lid = r.json()["data"]["id"]; check("POST list", r.status_code == 200)
check("PUT list", c.put(f"/api/admin/kanban/revenue/lists/{lid}", json={"name": "Waiting"}).status_code == 200)
r = c.post("/api/admin/kanban/revenue/move", json={"record_id": "PMT-1", "list_id": lid}); check("move to custom list", r.status_code == 200)
cc = {x["id"]: x for x in c.get("/api/admin/kanban/revenue/cards").json()["data"]}
check("custom list persisted", cc["PMT-1"]["kanban_list_id"] == lid and cc["PMT-1"]["kanban_status"] == "Waiting")
check("DELETE list returns cards to status column", c.delete(f"/api/admin/kanban/revenue/lists/{lid}").status_code == 200 and {x["id"]: x for x in c.get("/api/admin/kanban/revenue/cards").json()["data"]}["PMT-1"]["kanban_list_id"] is None)
# real-time: new payment shows immediately
run(db.erp_payments.insert_one({"id":"PMT-NEW","customer_id":"CUS-1","invoice_id":"INV-1","amount":100,"total":100,"status":"Completed","payment_date":d(0),"reference_no":"R-NEW"}))
j = c.get("/api/revenue").json()["data"]
check("new payment reflected: total 1000, pending 200", j["total_revenue"] == 1000 and j["pending_revenue"] == 200, (j["total_revenue"], j["pending_revenue"]))
# payment edit via existing API
r = c.put("/api/payments/PMT-NEW", json={"status": "Pending"}); check("PUT payment 200", r.status_code == 200, r.text[:100])
check("edited payment leaves revenue", c.get("/api/revenue").json()["data"]["total_revenue"] == 900)
h = c.get("/api/health").json()["features"]; check("health flags", all(h[k] for k in ("revenue", "revenue_kanban_lists", "revenue_kanban_move", "revenue_kanban_cards")), h)
for p in ["/api/revenue","/api/revenue/employees","/api/revenue/agents","/api/revenue/customers","/api/revenue/transactions","/api/admin/kanban/revenue/lists"]:
    check(f"GET {p} 200", c.get(p).status_code == 200)
print("ALL OK" if ok else "SOME FAILED")
