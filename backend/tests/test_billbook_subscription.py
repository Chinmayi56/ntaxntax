"""Tests for My BillBook subscriptions (plans, validation, verified activation, webhook).

Runs with pytest (real FastAPI) or plain ``python -m unittest``. The router tests use an
in-memory async fake DB and a fake Razorpay client, so no network, Mongo or Razorpay account
is needed. They exercise the real endpoint functions.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # real FastAPI when installed; minimal stand-in otherwise (sandbox without network)
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    fa = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            self.status_code, self.detail = status_code, detail

    class _Route:
        def __init__(self, path, method, endpoint):
            self.path, self.methods, self.endpoint = path, {method}, endpoint

    class APIRouter:
        def __init__(self, prefix=""):
            self.prefix, self.routes = prefix, []

        def _reg(self, method):
            def deco(path, **_):
                def wrap(fn):
                    self.routes.append(_Route(self.prefix + path, method, fn))
                    return fn
                return wrap
            return deco

        def __getattr__(self, name):
            if name in ("get", "post", "put", "patch"):
                return self._reg(name.upper())
            raise AttributeError(name)

    fa.APIRouter, fa.HTTPException = APIRouter, HTTPException
    fa.Body = lambda *a, **k: None
    fa.Depends = lambda *a, **k: None
    fa.Request = object
    fa.Response = object
    sys.modules["fastapi"] = fa

from fastapi import HTTPException  # noqa: E402
import billbook_rules as rules  # noqa: E402
import billbook_subscription as bs  # noqa: E402

SECRET = "test_secret_key"
WEBHOOK_SECRET = "whsec_test"
UTC = timezone.utc


# ------------------------------ fakes ------------------------------
def _match(doc, query):
    for k, v in query.items():
        if isinstance(v, dict):
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
            if "$nin" in v and doc.get(k) in v["$nin"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return dict(next(self._it))
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query=None, projection=None):
        return FakeCursor([d for d in self.docs if _match(d, query or {})])

    async def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _match(d, query or {}):
                return dict(d)
        return None

    async def count_documents(self, query=None):
        return sum(1 for d in self.docs if _match(d, query or {}))

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                return types.SimpleNamespace(modified_count=1, matched_count=1)
        if upsert:
            self.docs.append(dict(update.get("$set", {})))
            return types.SimpleNamespace(modified_count=1, matched_count=0)
        return types.SimpleNamespace(modified_count=0, matched_count=0)


class FakeDB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, FakeCollection())


class FakeRazorpay:
    def __init__(self):
        self.orders, self.payments = [], {}
        self.order = types.SimpleNamespace(create=self._create)
        self.payment = types.SimpleNamespace(fetch=self._fetch)

    def _create(self, data):
        oid = f"order_{len(self.orders) + 1}"
        self.orders.append({**data, "id": oid})
        return {"id": oid}

    def _fetch(self, pid):
        if pid not in self.payments:
            raise RuntimeError("not found")
        return self.payments[pid]


def sign(order_id, payment_id, secret=SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


VALID = {
    "plan_id": "1_month", "full_name": "Asha Rao", "mobile": "9876543210", "email": "asha@example.com",
    "business_name": "Asha Traders", "business_type": "Trader / Retailer", "gstin": "",
}
CUSTOMER = {"id": "cust-1", "role": "customer", "name": "Asha"}


class Harness:
    def __init__(self, prices=None, keys=True):
        self.db, self.client = FakeDB(), FakeRazorpay()
        prices = {"15_days": 100, "1_month": 200, "6_months": 900, "1_year": 1500} if prices is None else prices
        for pid, price in prices.items():
            self.db["billbook_plans"].docs.append({"id": pid, "price": price})
        factory = (lambda: (self.client, "rzp_test_key", SECRET)) if keys else (lambda: (None, None, None))
        self.router = bs.build_billbook_subscription_router(self.db, lambda: None, factory)

    def ep(self, path, method):
        for r in self.router.routes:
            if r.path == path and method in r.methods:
                return r.endpoint
        raise AssertionError(f"no route {method} {path}")

    def call(self, path, method, *args, **kwargs):
        return asyncio.run(self.ep(path, method)(*args, **kwargs))

    def checkout(self, body=None, user=CUSTOMER):
        return self.call("/api/billbook/subscription/checkout", "POST", body or dict(VALID), user)["data"]


# ------------------------------ pure rules ------------------------------
class RulesTests(unittest.TestCase):
    def test_four_fixed_plans_no_prices_in_code(self):
        self.assertEqual([p["name"] for p in rules.PLAN_CATALOG], ["15 Days", "1 Month", "6 Months", "1 Year"])
        self.assertTrue(all("price" not in p for p in rules.PLAN_CATALOG))

    def test_parse_price_never_invents(self):
        for bad in (None, "", "abc", 0, -5, True):
            self.assertIsNone(rules.parse_price(bad))
        self.assertEqual(rules.parse_price("1,499"), 1499.0)

    def test_expiry_calculation(self):
        start = datetime(2026, 1, 31, 10, 0, tzinfo=UTC)
        by_id = {p["id"]: p for p in rules.PLAN_CATALOG}
        self.assertEqual(rules.compute_expiry(start, by_id["15_days"]), datetime(2026, 2, 15, 10, 0, tzinfo=UTC))
        self.assertEqual(rules.compute_expiry(start, by_id["1_month"]), datetime(2026, 2, 28, 10, 0, tzinfo=UTC))
        self.assertEqual(rules.compute_expiry(start, by_id["6_months"]), datetime(2026, 7, 31, 10, 0, tzinfo=UTC))
        self.assertEqual(rules.compute_expiry(start, by_id["1_year"]), datetime(2027, 1, 31, 10, 0, tzinfo=UTC))

    def test_validation(self):
        self.assertEqual(rules.validate_customer_details(VALID), {})
        errs = rules.validate_customer_details({})
        self.assertEqual(set(errs), {"full_name", "mobile", "email", "business_name", "business_type"})
        self.assertIn("mobile", rules.validate_customer_details({**VALID, "mobile": "12345"}))
        self.assertIn("mobile", rules.validate_customer_details({**VALID, "mobile": "5876543210"}))
        self.assertEqual(rules.validate_customer_details({**VALID, "mobile": "+91 98765 43210"}), {})
        self.assertIn("email", rules.validate_customer_details({**VALID, "email": "nope"}))
        self.assertIn("gstin", rules.validate_customer_details({**VALID, "gstin": "BAD"}))
        self.assertEqual(rules.validate_customer_details({**VALID, "gstin": "36abcde1234f1z5"}), {})
        self.assertIn("business_type", rules.validate_customer_details({**VALID, "business_type": "Hacker"}))

    def test_signatures(self):
        self.assertTrue(rules.verify_checkout_signature("o", "p", sign("o", "p"), SECRET))
        self.assertFalse(rules.verify_checkout_signature("o", "p", "bad", SECRET))
        self.assertFalse(rules.verify_checkout_signature("o", "p", "", SECRET))
        self.assertFalse(rules.verify_checkout_signature("o", "p", sign("o", "p"), ""))
        body = b'{"a":1}'
        good = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(rules.verify_webhook_signature(body, good, WEBHOOK_SECRET))
        self.assertFalse(rules.verify_webhook_signature(body + b" ", good, WEBHOOK_SECRET))

    def test_state_is_derived_from_expiry(self):
        now = datetime.now(UTC)
        self.assertEqual(rules.subscription_state(None), "none")
        self.assertEqual(rules.subscription_state({"status": "active", "expires_at": (now + timedelta(days=1)).isoformat()}), "active")
        self.assertEqual(rules.subscription_state({"status": "active", "expires_at": (now - timedelta(days=1)).isoformat()}), "expired")
        self.assertEqual(rules.subscription_state({"status": "pending"}), "pending")
        self.assertEqual(rules.subscription_state({"status": "failed"}), "failed")

    def test_gate_default_on(self):
        self.assertTrue(rules.subscription_required({}))
        self.assertFalse(rules.subscription_required({"BILLBOOK_SUBSCRIPTION_REQUIRED": "false"}))


# ------------------------------ endpoint flow ------------------------------
class FlowTests(unittest.TestCase):
    def test_admin_plan_update_route_is_registered_once_and_publish_state_is_enforced(self):
        h = Harness(prices={"1_month": 200})
        matches = [r for r in h.router.routes if r.path == "/api/admin/billbook/plans/{plan_id}" and "PUT" in r.methods]
        self.assertEqual(len(matches), 1, "There must be one authoritative PUT route for plan updates.")
        h.db["billbook_plans"].docs[0].update({"name": "One Month", "duration_months": 1, "duration_days": 0})
        admin = {"id": "admin-1", "role": "admin"}
        h.call("/api/admin/billbook/plans/{plan_id}/status", "PATCH", "1_month", {"published": False}, admin)
        public = h.call("/api/billbook/plans", "GET")["data"]["plans"]
        self.assertFalse(any(p["id"] == "1_month" for p in public))
        stored = h.db["billbook_plans"].docs[0]
        self.assertFalse(stored["published"])

    def test_admin_overview_route_is_registered(self):
        h = Harness()
        self.assertTrue(any(r.path == "/api/admin/billbook/overview" and "GET" in r.methods for r in h.router.routes))

    def test_public_plans_only_include_active_priced_plans(self):
        h = Harness(prices={"1_month": 200})
        os.environ.pop("BILLBOOK_PRICE_15_DAYS", None)
        data = h.call("/api/billbook/plans", "GET")["data"]
        by_id = {p["id"]: p for p in data["plans"]}
        self.assertEqual(list(by_id), ["1_month"])
        self.assertEqual(by_id["1_month"]["price"], 200.0)
        self.assertEqual(len(data["missing_configuration"]), 3)
        self.assertTrue(all("price is missing or invalid" in x for x in data["missing_configuration"]))
        with self.assertRaises(HTTPException) as cm:
            h.checkout({**VALID, "plan_id": "15_days"})
        self.assertEqual(cm.exception.status_code, 409)

    def test_public_plans_report_configuration_diagnostics_instead_of_empty_missing_list(self):
        h = Harness(prices={})
        h.db["billbook_plans"].docs = [
            {"id": "1_month", "price": "bad", "duration_months": 1, "duration_days": 0, "active": True, "published": True},
            {"id": "6_months", "price": 900, "duration_months": 0, "duration_days": 0, "active": True, "published": True},
            {"id": "1_year", "price": 1500, "duration_months": 12, "duration_days": 0, "active": False, "published": True},
        ]
        data = h.call("/api/billbook/plans", "GET")["data"]
        self.assertEqual(data["plans"], [])
        self.assertEqual(len(data["missing_configuration"]), 4)
        self.assertTrue(any("price is missing or invalid" in x for x in data["missing_configuration"]))
        self.assertTrue(any("duration is missing or invalid" in x for x in data["missing_configuration"]))
        self.assertTrue(any("plan is inactive" in x for x in data["missing_configuration"]))

    def test_legacy_price_and_duration_aliases_are_read_without_mutating_documents(self):
        h = Harness(prices={})
        legacy = {"id": "legacy", "name": "Legacy Plan", "amount": "499", "months": "1", "active": "active", "published": "true"}
        h.db["billbook_plans"].docs = [legacy]
        data = h.call("/api/billbook/plans", "GET")["data"]
        self.assertEqual(len(data["plans"]), 1)
        plan = data["plans"][0]
        self.assertEqual(plan["price"], 499.0)
        self.assertEqual(plan["duration_months"], 1)
        self.assertEqual(plan["billing_period"], "1 month")
        self.assertEqual(h.db["billbook_plans"].docs[0], legacy)

    def test_admin_save_persists_canonical_price_duration_and_status(self):
        h = Harness(prices={})
        admin = {"id": "admin-1", "role": "admin"}
        result = h.call("/api/admin/billbook/plans", "POST", {
            "name": "Starter", "id": "starter", "price": "499",
            "duration_days": "30", "duration_months": "0", "currency": "INR",
            "billing_type": "one_time", "active": True, "published": True,
            "features": ["Invoices"], "display_order": 1,
        }, admin)
        stored = h.db["billbook_plans"].docs[0]
        self.assertEqual(stored["price"], 499.0)
        self.assertEqual(stored["duration_days"], 30)
        self.assertEqual(stored["duration_months"], 0)
        self.assertTrue(stored["active"])
        self.assertTrue(stored["published"])
        self.assertEqual(result["data"]["plan"]["price"], 499.0)
        public = h.call("/api/billbook/plans", "GET")["data"]["plans"]
        self.assertEqual(public[0]["id"], "starter")

    def test_invalid_duration_cannot_be_purchased(self):
        h = Harness(prices={})
        h.db["billbook_plans"].docs = [{
            "id": "broken", "name": "Broken", "price": 499,
            "duration_days": 0, "duration_months": 0, "active": True, "published": True,
        }]
        data = h.call("/api/billbook/plans", "GET")["data"]
        self.assertEqual(data["plans"], [])
        self.assertTrue(any("duration is missing or invalid" in x for x in data["missing_configuration"]))
        with self.assertRaises(HTTPException) as cm:
            h.checkout({**VALID, "plan_id": "broken"})
        self.assertEqual(cm.exception.status_code, 409)

    def test_checkout_uses_server_price_and_ignores_client_amount(self):
        h = Harness()
        data = h.checkout({**VALID, "amount": 1, "price": 1, "customer_id": "someone-else", "status": "active"})
        self.assertEqual(data["amount"], 20000)  # 200 INR in paise, from server config
        sub = h.db["billbook_subscriptions"].docs[0]
        self.assertEqual(sub["customer_id"], "cust-1")
        self.assertEqual(sub["status"], "pending")
        self.assertEqual(sub["amount"], 200.0)

    def test_checkout_validation_and_role(self):
        h = Harness()
        with self.assertRaises(HTTPException) as cm:
            h.checkout({**VALID, "email": "bad"})
        self.assertEqual(cm.exception.status_code, 422)
        with self.assertRaises(HTTPException) as cm:
            h.checkout(user={"id": "a1", "role": "admin"})
        self.assertEqual(cm.exception.status_code, 403)
        with self.assertRaises(HTTPException) as cm:
            h.checkout({**VALID, "plan_id": "lifetime"})
        self.assertEqual(cm.exception.status_code, 422)

    def test_no_keys_means_503_never_a_mock_activation(self):
        h = Harness(keys=False)
        with self.assertRaises(HTTPException) as cm:
            h.checkout()
        self.assertEqual(cm.exception.status_code, 503)
        self.assertEqual(h.db["billbook_subscriptions"].docs, [])

    def test_duplicate_checkout_reuses_pending_order(self):
        h = Harness()
        a, b = h.checkout(), h.checkout()
        self.assertEqual(a["order_id"], b["order_id"])
        self.assertEqual(len(h.db["billbook_subscriptions"].docs), 1)
        self.assertEqual(len(h.client.orders), 1)

    def _verify(self, h, order_id, payment_id="pay_1", signature=None):
        body = {"razorpay_order_id": order_id, "razorpay_payment_id": payment_id,
                "razorpay_signature": signature if signature is not None else sign(order_id, payment_id)}
        return h.call("/api/billbook/subscription/verify", "POST", body, CUSTOMER)

    def test_bad_signature_never_activates(self):
        h = Harness()
        oid = h.checkout()["order_id"]
        h.client.payments["pay_1"] = {"order_id": oid, "amount": 20000, "status": "captured"}
        with self.assertRaises(HTTPException) as cm:
            self._verify(h, oid, signature="forged")
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(h.db["billbook_subscriptions"].docs[0]["status"], "pending")
        self.assertEqual(h.db["erp_payments"].docs, [])

    def test_valid_signature_but_unconfirmed_or_wrong_amount_does_not_activate(self):
        h = Harness()
        oid = h.checkout()["order_id"]
        # Razorpay cannot confirm the payment yet -> stays pending
        res = self._verify(h, oid)["data"]
        self.assertEqual(res["state"], "pending")
        # authorized-but-not-captured -> pending
        h.client.payments["pay_1"] = {"order_id": oid, "amount": 20000, "status": "authorized"}
        self.assertEqual(self._verify(h, oid)["data"]["state"], "pending")
        # amount mismatch -> rejected
        h.client.payments["pay_1"] = {"order_id": oid, "amount": 100, "status": "captured"}
        with self.assertRaises(HTTPException):
            self._verify(h, oid)
        self.assertEqual(h.db["billbook_subscriptions"].docs[0]["status"], "pending")

    def test_verified_capture_activates_once_and_is_idempotent(self):
        h = Harness()
        oid = h.checkout()["order_id"]
        h.client.payments["pay_1"] = {"order_id": oid, "amount": 20000, "status": "captured"}
        res = self._verify(h, oid)["data"]
        self.assertEqual(res["state"], "active")
        sub = h.db["billbook_subscriptions"].docs[0]
        start, end = rules._parse_dt(sub["started_at"]), rules._parse_dt(sub["expires_at"])
        self.assertEqual(end, rules.add_months(start, 1))
        self.assertEqual(len(h.db["erp_payments"].docs), 1)
        self.assertEqual(h.db["erp_payments"].docs[0]["total"], 200)
        # duplicate verify + duplicate webhook change nothing
        self._verify(h, oid)
        self.assertEqual(len(h.db["erp_payments"].docs), 1)
        self.assertEqual(h.db["billbook_subscriptions"].docs[0]["expires_at"], sub["expires_at"])
        status = h.call("/api/billbook/subscription/status", "GET", CUSTOMER)["data"]
        self.assertEqual(status["state"], "active")
        self.assertTrue(status["access_allowed"])

    def test_other_customer_cannot_verify_my_order(self):
        h = Harness()
        oid = h.checkout()["order_id"]
        h.client.payments["pay_1"] = {"order_id": oid, "amount": 20000, "status": "captured"}
        body = {"razorpay_order_id": oid, "razorpay_payment_id": "pay_1", "razorpay_signature": sign(oid, "pay_1")}
        with self.assertRaises(HTTPException) as cm:
            h.call("/api/billbook/subscription/verify", "POST", body, {"id": "cust-2", "role": "customer"})
        self.assertEqual(cm.exception.status_code, 404)

    def test_failed_payment_allows_new_attempt_without_duplicate_active(self):
        h = Harness()
        oid = h.checkout()["order_id"]
        h.client.payments["pay_f"] = {"order_id": oid, "amount": 20000, "status": "failed"}
        self.assertEqual(self._verify(h, oid, "pay_f")["data"]["state"], "failed")
        self.assertEqual(h.call("/api/billbook/subscription/status", "GET", CUSTOMER)["data"]["access_allowed"], False)
        oid2 = h.checkout()["order_id"]
        self.assertNotEqual(oid, oid2)
        self.assertEqual(sum(1 for s in h.db["billbook_subscriptions"].docs if s["status"] == "active"), 0)

    def test_cancelled_checkout_marks_cancelled_only_when_unpaid(self):
        h = Harness()
        oid = h.checkout()["order_id"]
        res = h.call("/api/billbook/subscription/cancel-attempt", "POST", {"order_id": oid}, CUSTOMER)["data"]
        self.assertEqual(res["state"], "cancelled")
        self.assertEqual(h.call("/api/billbook/subscription/status", "GET", CUSTOMER)["data"]["access_allowed"], False)

    def test_active_same_plan_blocked_and_renewal_extends(self):
        h = Harness()
        oid = h.checkout()["order_id"]
        h.client.payments["pay_1"] = {"order_id": oid, "amount": 20000, "status": "captured"}
        self._verify(h, oid)
        with self.assertRaises(HTTPException) as cm:
            h.checkout()
        self.assertEqual(cm.exception.status_code, 409)
        first_expiry = rules._parse_dt(h.db["billbook_subscriptions"].docs[0]["expires_at"])
        oid2 = h.checkout({**VALID, "renew": "true"})["order_id"]
        h.client.payments["pay_2"] = {"order_id": oid2, "amount": 20000, "status": "captured"}
        self._verify(h, oid2, "pay_2")
        second = [s for s in h.db["billbook_subscriptions"].docs if s["order_id"] == oid2][0]
        self.assertEqual(rules._parse_dt(second["started_at"]), first_expiry)  # paid time is not lost

    def test_expired_subscription_reports_expired_and_blocks_access(self):
        h = Harness()
        h.db["billbook_subscriptions"].docs.append({
            "id": "x", "customer_id": "cust-1", "plan_id": "1_month", "plan_name": "1 Month", "status": "active",
            "amount": 200.0, "order_id": "o", "expires_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
            "created_at": datetime.now(UTC).isoformat()})
        status = h.call("/api/billbook/subscription/status", "GET", CUSTOMER)["data"]
        self.assertEqual(status["state"], "expired")
        self.assertFalse(status["access_allowed"])
        self.assertFalse(asyncio.run(bs.customer_has_active_subscription(h.db, "cust-1")))

    def _webhook(self, h, event, secret=WEBHOOK_SECRET, sig=None):
        raw = json.dumps(event).encode()
        good = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

        class Req:
            headers = {"X-Razorpay-Signature": sig if sig is not None else good}

            async def body(self_inner):
                return raw
        return h.call("/api/billbook/subscription/webhook", "POST", Req())

    def test_webhook_signature_required_and_activates(self):
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = WEBHOOK_SECRET
        try:
            h = Harness()
            oid = h.checkout()["order_id"]
            event = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_w", "order_id": oid, "amount": 20000}}}}
            with self.assertRaises(HTTPException) as cm:
                self._webhook(h, event, sig="forged")
            self.assertEqual(cm.exception.status_code, 400)
            self.assertEqual(h.db["billbook_subscriptions"].docs[0]["status"], "pending")
            self._webhook(h, event)
            self.assertEqual(h.db["billbook_subscriptions"].docs[0]["status"], "active")
            self._webhook(h, event)  # duplicate delivery
            self.assertEqual(len(h.db["erp_payments"].docs), 1)
            # wrong amount is rejected
            oid2 = h.checkout({**VALID, "plan_id": "6_months"})["order_id"]
            bad = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_x", "order_id": oid2, "amount": 5}}}}
            with self.assertRaises(HTTPException):
                self._webhook(h, bad)
            # failed event never activates
            failed = {"event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_y", "order_id": oid2, "amount": 90000}}}}
            self._webhook(h, failed)
            self.assertEqual([s for s in h.db["billbook_subscriptions"].docs if s["order_id"] == oid2][0]["status"], "failed")
        finally:
            os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)

    def test_webhook_without_secret_is_refused(self):
        os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)
        h = Harness()
        with self.assertRaises(HTTPException) as cm:
            self._webhook(h, {"event": "x"})
        self.assertEqual(cm.exception.status_code, 503)

    def test_admin_can_configure_price_others_cannot(self):
        h = Harness(prices={})
        with self.assertRaises(HTTPException) as cm:
            h.call("/api/admin/billbook/plans/{plan_id}", "PUT", "1_month", {"price": 10}, CUSTOMER)
        self.assertEqual(cm.exception.status_code, 403)
        admin = {"id": "ad", "role": "admin"}
        with self.assertRaises(HTTPException):
            h.call("/api/admin/billbook/plans/{plan_id}", "PUT", "1_month", {"price": -3}, admin)
        res = h.call("/api/admin/billbook/plans/{plan_id}", "PUT", "1_month", {"price": 250}, admin)
        self.assertEqual(res["data"]["plan"]["price"], 250.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
