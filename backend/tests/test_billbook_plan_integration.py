"""Admin -> customer subscription plan integration, legacy records, duplicates and migration.

Runs with pytest or ``python -m unittest`` (uses the in-memory fakes from
test_billbook_subscription; no network, MongoDB or Razorpay needed). The tests call the real
endpoint functions and the real normalization / migration code.
"""
import asyncio
import copy
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_billbook_subscription import (  # noqa: E402
    CUSTOMER, SECRET, VALID, FakeDB, Harness, HTTPException, sign,
)
import billbook_plans as bp  # noqa: E402
import billbook_rules as rules  # noqa: E402

ADMIN = {"id": "admin-1", "role": "admin"}
PLANS = "/api/billbook/plans"
ADMIN_PLANS = "/api/admin/billbook/plans"


def public(h):
    return h.call(PLANS, "GET")["data"]


def admin_plans(h):
    return {p["id"]: p for p in h.call(ADMIN_PLANS, "GET", ADMIN)["data"]["plans"]}


def create(h, **over):
    body = {"name": "Starter", "id": "starter", "price": "499", "duration_days": "30", "duration_months": "0",
            "currency": "INR", "active": True, "published": True, "features": ["Invoices"], "display_order": 1}
    body.update(over)
    return h.call(ADMIN_PLANS, "POST", body, ADMIN)


# The eight legacy ids from the task, in several plausible historical shapes.
def legacy_docs():
    return [
        {"id": "15_days", "name": "15 Days", "price": 100, "duration_days": 15, "duration_months": 0, "active": True, "published": True},
        {"id": "1_month", "name": "1 Month"},                                    # bare record: no price/duration
        {"id": "6_months", "name": "6 Months", "price": None, "duration_days": 0, "duration_months": 0},
        {"id": "1_year", "name": "1 Year", "price": "₹1,500", "duration": "1 year", "status": "active"},
        {"id": "plan_15d", "name": "15 Days", "amount": "100", "validity_days": 15, "is_active": "yes"},
        {"id": "plan_1m", "name": "1 Month", "price_inr": 200, "billing_period": "1 month", "status": "inactive"},
        {"id": "plan_6m", "name": "6 Months", "price": 0, "months": 6, "published": "false"},
        {"id": "plan_12m", "name": "1 Year", "price": 1500, "duration_months": 12, "duration_days": 0, "archived": "true"},
    ]


class AdminToCustomerFlow(unittest.TestCase):
    def test_admin_create_persists_real_values_and_shows_in_admin_list_and_customer_endpoint(self):
        h = Harness(prices={})
        res = create(h)
        stored = h.db["billbook_plans"].docs[0]
        self.assertEqual((stored["price"], stored["duration_days"], stored["duration_months"]), (499.0, 30, 0))
        self.assertTrue(stored["active"] and stored["published"] and stored["archived"] is False)
        self.assertEqual(res["data"]["plan"]["price"], 499.0)
        row = admin_plans(h)["starter"]                      # what the Admin table renders
        self.assertEqual((row["price"], row["duration_days"], row["billing_period"]), (499.0, 30, "30 days"))
        self.assertTrue(row["configured"] and row["issues"] == [])
        pub = public(h)
        self.assertEqual([p["id"] for p in pub["plans"]], ["starter"])
        self.assertEqual(pub["plans"][0]["price"], 499.0)
        self.assertEqual(pub["plans"][0]["currency"], "INR")
        for key in ("plans", "business_types", "missing_configuration"):
            self.assertIn(key, pub)

    def test_editing_price_and_duration_is_reflected_after_reload(self):
        h = Harness(prices={})
        create(h)
        self.assertEqual(public(h)["plans"][0]["price"], 499.0)
        h.call(ADMIN_PLANS + "/{plan_id}", "PUT", "starter", {"name": "Starter", "price": 650, "duration_days": 0, "duration_months": 3}, ADMIN)
        plan = public(h)["plans"][0]
        self.assertEqual((plan["price"], plan["duration_months"], plan["billing_period"]), (650.0, 3, "3 months"))

    def test_inactive_unpublished_and_archived_plans_are_not_purchasable(self):
        h = Harness(prices={})
        for pid in ("a", "b", "c"):
            create(h, id=f"plan-{pid}", name=f"Plan {pid}")
        status = ADMIN_PLANS + "/{plan_id}/status"
        h.call(status, "PATCH", "plan-a", {"active": False}, ADMIN)
        h.call(status, "PATCH", "plan-b", {"published": False}, ADMIN)
        h.call(ADMIN_PLANS + "/{plan_id}/archive", "PATCH", "plan-c", {"archived": True}, ADMIN)
        self.assertEqual(public(h)["plans"], [])
        for pid in ("plan-a", "plan-b", "plan-c"):
            with self.assertRaises(HTTPException) as cm:
                h.checkout({**VALID, "plan_id": pid})
            self.assertEqual(cm.exception.status_code, 409, pid)
        flags = admin_plans(h)
        self.assertIn("INACTIVE", flags["plan-a"]["issues"])
        self.assertIn("UNPUBLISHED", flags["plan-b"]["issues"])
        self.assertIn("ARCHIVED", flags["plan-c"]["issues"])
        # restoring does not silently activate/publish
        h.call(ADMIN_PLANS + "/{plan_id}/archive", "PATCH", "plan-c", {"archived": False}, ADMIN)
        self.assertEqual(public(h)["plans"], [])
        # explicit re-activation brings a plan back
        h.call(status, "PATCH", "plan-a", {"active": True}, ADMIN)
        self.assertEqual([p["id"] for p in public(h)["plans"]], ["plan-a"])

    def test_invalid_prices_are_rejected_by_admin_api(self):
        h = Harness(prices={})
        for bad in ("abc", 0, "0", -5, "nan", "inf", True, None, ""):
            with self.assertRaises(HTTPException, msg=repr(bad)) as cm:
                create(h, price=bad)
            self.assertEqual(cm.exception.status_code, 422)
        self.assertEqual(h.db["billbook_plans"].docs, [])

    def test_invalid_durations_are_rejected_by_admin_api(self):
        h = Harness(prices={})
        for days, months in ((0, 0), (10, 2), (-1, 0), ("1.5", 0), ("x", 0), (0, 500)):
            with self.assertRaises(HTTPException, msg=str((days, months))) as cm:
                create(h, duration_days=days, duration_months=months)
            self.assertEqual(cm.exception.status_code, 422)
        self.assertEqual(h.db["billbook_plans"].docs, [])

    def test_non_inr_plan_cannot_be_created_or_purchased(self):
        h = Harness(prices={})
        with self.assertRaises(HTTPException):
            create(h, currency="USD")
        h.db["billbook_plans"].docs = [{"id": "usd", "name": "USD", "price": 10, "currency": "USD", "duration_months": 1, "duration_days": 0}]
        self.assertEqual(public(h)["plans"], [])
        with self.assertRaises(HTTPException) as cm:
            h.checkout({**VALID, "plan_id": "usd"})
        self.assertEqual(cm.exception.status_code, 409)

    def test_plan_visibility_setting_is_reported_not_confused_with_empty(self):
        h = Harness(prices={"1_month": 200})
        h.db["billbook_subscription_settings"].docs.append({"key": "default", "customer_plan_visibility": False})
        data = public(h)
        self.assertEqual(data["plans"], [])
        self.assertTrue(data["plans_hidden"])

    def test_responses_are_not_cacheable(self):
        h = Harness()
        resp = types.SimpleNamespace(headers={})
        h.call(PLANS, "GET", resp)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_admin_endpoints_and_public_endpoint_access_rules(self):
        h = Harness()
        for method, path, args in (("GET", ADMIN_PLANS, ()), ("POST", ADMIN_PLANS, ({"name": "x"},)),
                                   ("PUT", ADMIN_PLANS + "/{plan_id}", ("1_month", {"price": 5})),
                                   ("PATCH", ADMIN_PLANS + "/{plan_id}/status", ("1_month", {"active": False})),
                                   ("PATCH", ADMIN_PLANS + "/{plan_id}/archive", ("1_month", {"archived": True}))):
            with self.assertRaises(HTTPException) as cm:
                h.call(path, method, *args, CUSTOMER)
            self.assertEqual(cm.exception.status_code, 403, (method, path))
        self.assertTrue(public(h)["plans"])  # public listing needs no login


class LegacyRecords(unittest.TestCase):
    def setUp(self):
        self.h = Harness(prices={})
        self.h.db["billbook_plans"].docs = legacy_docs()
        self.before = copy.deepcopy(self.h.db["billbook_plans"].docs)
        os.environ.pop("BILLBOOK_PRICE_1_MONTH", None)

    def test_each_legacy_record_is_interpreted_consistently(self):
        plans = admin_plans(self.h)
        self.assertEqual(set(plans), {"15_days", "1_month", "6_months", "1_year", "plan_15d", "plan_1m", "plan_6m", "plan_12m"})
        # explicit, sensible values are honoured (including text prices and duration text)
        self.assertEqual((plans["1_year"]["price"], plans["1_year"]["duration_months"]), (1500.0, 12))
        self.assertTrue(plans["1_year"]["configured"])
        self.assertEqual((plans["plan_15d"]["price"], plans["plan_15d"]["duration_days"]), (100.0, 15))
        self.assertEqual((plans["plan_1m"]["price"], plans["plan_1m"]["duration_months"]), (200.0, 1))
        # intentional states are respected, never silently flipped
        self.assertIn("INACTIVE", plans["plan_1m"]["issues"])
        self.assertIn("UNPUBLISHED", plans["plan_6m"]["issues"])
        self.assertIn("ARCHIVED", plans["plan_12m"]["issues"])
        # nothing is invented for genuinely missing configuration
        self.assertIn("PRICE_MISSING", plans["1_month"]["issues"])
        self.assertIsNone(plans["1_month"]["price"])
        self.assertIn("DURATION_INVALID", plans["6_months"]["issues"])
        self.assertEqual(plans["6_months"]["duration_days"] + plans["6_months"]["duration_months"], 0)
        self.assertIsNone(plans["6_months"]["billing_period"])      # never "0 months"
        self.assertIn("PRICE_INVALID", plans["plan_6m"]["issues"])  # price 0 is invalid, not free

    def test_customer_endpoint_lists_only_purchasable_plans_and_explains_the_rest(self):
        data = public(self.h)
        ids = [p["id"] for p in data["plans"]]
        self.assertEqual(sorted(ids), ["15_days", "1_year"])          # plan_15d duplicates 15_days; plan_12m archived
        self.assertTrue(data["missing_configuration"])
        joined = " ".join(data["missing_configuration"])
        self.assertIn("1 Month", joined)
        self.assertIn("price is missing or invalid", joined)
        self.assertIn("duration is missing or invalid", joined)
        self.assertNotIn("plan_12m", joined)  # archived plans are not noise for customers
        reasons = {d["id"]: d["reasons"] for d in data["diagnostics"]}
        self.assertIn("PRICE_MISSING", reasons["1_month"])
        self.assertNotIn("plan_15d", reasons)  # pure duplicate: nothing to configure

    def test_duplicate_looking_ids_do_not_create_two_purchasable_plans(self):
        plans = admin_plans(self.h)
        self.assertTrue(plans["15_days"]["configured"])
        self.assertFalse(plans["plan_15d"]["configured"])
        self.assertEqual(plans["plan_15d"]["duplicate_of"], "15_days")
        self.assertIn("DUPLICATE", plans["plan_15d"]["issues"])
        with self.assertRaises(HTTPException) as cm:
            self.h.checkout({**VALID, "plan_id": "plan_15d"})
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(self.h.checkout({**VALID, "plan_id": "15_days"})["amount"], 10000)
        diag = self.h.call(ADMIN_PLANS, "GET", ADMIN)["data"]["diagnostics"]
        self.assertIn({"id": "plan_15d", "duplicate_of": "15_days"}, diag["duplicates"])

    def test_unconfigured_placeholder_is_superseded_by_a_configured_equivalent(self):
        h = Harness(prices={})
        h.db["billbook_plans"].docs = [{"id": "plan_1m", "name": "1 Month", "price": 299, "duration_months": 1, "duration_days": 0}]
        data = public(h)
        self.assertEqual([p["id"] for p in data["plans"]], ["plan_1m"])
        self.assertFalse(any("1 Month plan is unavailable" in m for m in data["missing_configuration"]))
        self.assertTrue(h.checkout({**VALID, "plan_id": "plan_1m"})["order_id"])

    def test_same_duration_with_a_different_name_is_kept_but_flagged(self):
        h = Harness(prices={})
        h.db["billbook_plans"].docs = [
            {"id": "basic", "name": "Basic", "price": 100, "duration_months": 1, "duration_days": 0},
            {"id": "pro", "name": "Pro", "price": 300, "duration_months": 1, "duration_days": 0},
        ]
        self.assertEqual({p["id"] for p in public(h)["plans"]}, {"basic", "pro"})
        self.assertEqual(admin_plans(h)["basic"]["shares_duration_with"], ["pro"])

    def test_reading_never_mutates_stored_documents(self):
        public(self.h)
        admin_plans(self.h)
        self.assertEqual(self.h.db["billbook_plans"].docs, self.before)

    def test_admin_can_fix_a_missing_price_through_the_existing_api_without_duplicating_records(self):
        n = len(self.h.db["billbook_plans"].docs)
        self.h.call(ADMIN_PLANS + "/{plan_id}", "PUT", "1_month", {"name": "1 Month", "price": 249}, ADMIN)
        self.assertEqual(len(self.h.db["billbook_plans"].docs), n)
        stored = next(d for d in self.h.db["billbook_plans"].docs if d["id"] == "1_month")
        self.assertEqual((stored["price"], stored["duration_months"], stored["duration_days"]), (249.0, 1, 0))
        self.assertIn("1_month", [p["id"] for p in public(self.h)["plans"]])

    def test_plan_stored_under_a_legacy_key_is_updated_in_place_not_duplicated(self):
        h = Harness(prices={})
        h.db["billbook_plans"].docs = [{"_id": "oid1", "plan_id": "oldplan", "name": "Old Plan", "amount": 400, "months": 2}]
        self.assertIn("oldplan", [p["id"] for p in public(h)["plans"]])
        h.call(ADMIN_PLANS + "/{plan_id}", "PUT", "oldplan", {"name": "Old Plan", "price": 450}, ADMIN)
        self.assertEqual(len(h.db["billbook_plans"].docs), 1)
        self.assertEqual(h.db["billbook_plans"].docs[0]["price"], 450.0)
        self.assertEqual(public(h)["plans"][0]["price"], 450.0)


class CheckoutAndHistory(unittest.TestCase):
    def test_checkout_amount_comes_from_the_server_record_after_an_admin_edit(self):
        h = Harness(prices={})
        create(h, price=500)
        first = h.checkout({**VALID, "plan_id": "starter", "amount": 1})
        self.assertEqual(first["amount"], 50000)
        h.call(ADMIN_PLANS + "/{plan_id}", "PUT", "starter", {"name": "Starter", "price": 750}, ADMIN)
        second = h.checkout({**VALID, "plan_id": "starter", "amount": 1})
        self.assertEqual(second["amount"], 75000)
        self.assertNotEqual(first["order_id"], second["order_id"], "a stale-price pending order must not be reused")
        self.assertEqual(h.client.orders[-1]["amount"], 75000)

    def test_editing_a_plan_does_not_change_existing_subscriptions_or_payments(self):
        h = Harness(prices={})
        create(h, price=500, duration_days=0, duration_months=1, id="monthly", name="Monthly")
        order = h.checkout({**VALID, "plan_id": "monthly"})
        h.client.payments["pay_1"] = {"order_id": order["order_id"], "amount": 50000, "status": "captured"}
        # Admin edits price/duration after the customer paid but before verification
        h.call(ADMIN_PLANS + "/{plan_id}", "PUT", "monthly", {"name": "Monthly", "price": 900, "duration_days": 0, "duration_months": 12}, ADMIN)
        h.call("/api/billbook/subscription/verify", "POST", {
            "razorpay_order_id": order["order_id"], "razorpay_payment_id": "pay_1",
            "razorpay_signature": sign(order["order_id"], "pay_1")}, CUSTOMER)
        sub = h.db["billbook_subscriptions"].docs[0]
        self.assertEqual(sub["status"], "active")
        self.assertEqual(sub["amount"], 500.0)                      # agreed price preserved
        start, end = (datetime.fromisoformat(sub[k]) for k in ("started_at", "expires_at"))
        self.assertLess(end - start, timedelta(days=32))             # agreed 1-month term, not the new 12 months
        snapshot = copy.deepcopy(sub)
        # a later plan edit/archival leaves history untouched
        h.call(ADMIN_PLANS + "/{plan_id}/archive", "PATCH", "monthly", {"archived": True}, ADMIN)
        self.assertEqual(h.db["billbook_subscriptions"].docs[0], snapshot)

    def test_verified_payment_activates_even_if_the_plan_was_removed_afterwards(self):
        h = Harness(prices={})
        create(h, id="monthly", name="Monthly", duration_days=0, duration_months=1, price=500)
        order = h.checkout({**VALID, "plan_id": "monthly"})
        h.client.payments["pay_1"] = {"order_id": order["order_id"], "amount": 50000, "status": "captured"}
        h.db["billbook_plans"].docs.clear()
        h.call("/api/billbook/subscription/verify", "POST", {
            "razorpay_order_id": order["order_id"], "razorpay_payment_id": "pay_1",
            "razorpay_signature": sign(order["order_id"], "pay_1")}, CUSTOMER)
        self.assertEqual(h.db["billbook_subscriptions"].docs[0]["status"], "active")

    def test_zero_duration_never_becomes_an_active_paid_subscription(self):
        h = Harness(prices={})
        create(h, id="monthly", name="Monthly", duration_days=0, duration_months=1, price=500)
        order = h.checkout({**VALID, "plan_id": "monthly"})
        # corrupt the purchase snapshot as if written by a faulty older release
        h.db["billbook_subscriptions"].docs[0].update({"duration_days_at_purchase": 0, "duration_months_at_purchase": 0})
        h.db["billbook_plans"].docs.clear()
        h.client.payments["pay_1"] = {"order_id": order["order_id"], "amount": 50000, "status": "captured"}
        with self.assertRaises(HTTPException) as cm:
            h.call("/api/billbook/subscription/verify", "POST", {
                "razorpay_order_id": order["order_id"], "razorpay_payment_id": "pay_1",
                "razorpay_signature": sign(order["order_id"], "pay_1")}, CUSTOMER)
        self.assertEqual(cm.exception.status_code, 409)
        sub = h.db["billbook_subscriptions"].docs[0]
        self.assertNotEqual(sub["status"], "active")
        self.assertIn("manual review", sub["failure_reason"])

    def test_bad_signature_still_never_activates(self):
        h = Harness(prices={})
        create(h, id="monthly", name="Monthly", duration_days=0, duration_months=1, price=500)
        order = h.checkout({**VALID, "plan_id": "monthly"})
        h.client.payments["pay_1"] = {"order_id": order["order_id"], "amount": 50000, "status": "captured"}
        with self.assertRaises(HTTPException) as cm:
            h.call("/api/billbook/subscription/verify", "POST", {
                "razorpay_order_id": order["order_id"], "razorpay_payment_id": "pay_1",
                "razorpay_signature": sign(order["order_id"], "pay_1", "wrong-secret")}, CUSTOMER)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(h.db["billbook_subscriptions"].docs[0]["status"], "pending")


class NormalizationUnits(unittest.TestCase):
    def test_price_extraction(self):
        for raw, expected in (({"price": "₹1,499"}, 1499.0), ({"price": "Rs. 250/-"}, 250.0), ({"amount": "99.5"}, 99.5),
                              ({"price": {"amount": "300", "currency": "INR"}}, 300.0), ({"price_paise": 49900}, 499.0),
                              ({"price": "bad", "price_inr": 120}, 120.0)):
            self.assertEqual(bp.extract_price(raw)[0], expected, raw)
        for raw, status in (({}, "missing"), ({"price": None}, "missing"), ({"price": 0}, "invalid"),
                            ({"price": "free"}, "invalid"), ({"price": float("inf")}, "invalid")):
            price, _src, st = bp.extract_price(raw)
            self.assertIsNone(price, raw)
            self.assertEqual(st, status, raw)

    def test_duration_extraction(self):
        for raw, expected in (({"duration_days": 15}, (15, 0)), ({"duration_months": "6"}, (0, 6)), ({"months": 1}, (0, 1)),
                              ({"duration": "1 year"}, (0, 12)), ({"billing_period": "15 days"}, (15, 0)),
                              ({"duration": 3, "duration_unit": "months"}, (0, 3)), ({"duration_years": 2}, (0, 24)),
                              ({"duration": "Monthly"}, (0, 1))):
            d = bp.extract_duration(raw)
            self.assertTrue(d["valid"], raw)
            self.assertEqual((d["days"], d["months"]), expected, raw)
        for raw in ({}, {"duration_days": 0, "duration_months": 0}, {"duration_days": 5, "duration_months": 1},
                    {"duration_days": -3}, {"duration_months": "abc"}, {"duration": "soon"}, {"duration": 30}):
            self.assertFalse(bp.extract_duration(raw)["valid"], raw)

    def test_catalogue_duration_only_when_the_record_has_no_duration_information(self):
        base = rules.PLAN_CATALOG[1]
        self.assertTrue(bp.extract_duration({"id": "1_month"}, base)["valid"])
        self.assertFalse(bp.extract_duration({"id": "1_month", "duration_days": 0, "duration_months": 0}, base)["valid"])

    def test_boolean_words(self):
        for word in ("false", "inactive", "draft", "disabled", "archived", "no", 0):
            self.assertFalse(bp.coerce_bool(word, True), word)
        for word in ("true", "active", "published", "yes", 1, True):
            self.assertTrue(bp.coerce_bool(word, False), word)
        self.assertTrue(bp.coerce_bool(None, True))
        self.assertFalse(bp.coerce_bool("???", False))


class Migration(unittest.TestCase):
    def apply(self, docs, result):
        for update in result["updates"]:
            for doc in docs:
                if bp.doc_id(doc) == update["id"]:
                    doc.update(update["set"])

    def test_dry_run_never_invents_data_and_reports_remaining_work(self):
        docs = legacy_docs()
        result = bp.plan_migration(docs, rules.PLAN_CATALOG, {}, rules.BILLBOOK_FEATURES)
        updated = {u["id"]: u["set"] for u in result["updates"]}
        # canonical fields are ADDED from values the record already carries
        self.assertEqual(updated["1_year"]["price"], 1500.0)
        self.assertEqual((updated["1_year"]["duration_days"], updated["1_year"]["duration_months"]), (0, 12))
        self.assertEqual(updated["plan_15d"]["price"], 100.0)
        self.assertEqual(updated["plan_1m"]["duration_months"], 1)
        # explicit legacy states are normalized, never reversed
        self.assertIs(updated["plan_1m"]["active"], False)
        self.assertIs(updated["plan_6m"]["published"], False)
        self.assertIs(updated["plan_12m"]["archived"], True)
        # nothing is invented
        self.assertNotIn("price", updated.get("1_month", {}))
        self.assertNotIn("price", updated.get("plan_6m", {}))
        self.assertNotIn("duration_days", updated.get("6_months", {}))
        self.assertTrue(all(u["set"].get("duration_days", 1) or u["set"].get("duration_months", 1) for u in result["updates"]))
        need = {n["id"]: n["problems"] for n in result["needs_admin_action"]}
        self.assertIn("PRICE_MISSING", need["1_month"])
        self.assertIn("DURATION_INVALID", need["6_months"])
        self.assertIn("PRICE_INVALID", need["plan_6m"])
        self.assertNotIn("1_year", need)
        self.assertIn({"id": "plan_15d", "duplicate_of": "15_days"}, result["duplicates"])

    def test_migration_is_idempotent_and_preserves_records_and_legacy_fields(self):
        docs = legacy_docs()
        original_ids = [bp.doc_id(d) for d in docs]
        first = bp.plan_migration(docs, rules.PLAN_CATALOG, {}, rules.BILLBOOK_FEATURES)
        self.assertTrue(first["updates"])
        self.apply(docs, first)
        self.assertEqual([bp.doc_id(d) for d in docs], original_ids)             # nothing deleted / no new ids
        self.assertEqual(next(d for d in docs if d["id"] == "plan_15d")["amount"], "100")   # legacy field kept
        second = bp.plan_migration(docs, rules.PLAN_CATALOG, {}, rules.BILLBOOK_FEATURES)
        self.assertEqual(second["updates"], [], "re-running must be a no-op")
        self.assertEqual(second["needs_admin_action"], first["needs_admin_action"])

    def test_migration_does_not_change_what_customers_can_buy_except_by_canonicalising(self):
        docs = legacy_docs()
        before = {p["id"]: p["configured"] for p in bp.evaluate_plans(docs, rules.PLAN_CATALOG, {}, rules.BILLBOOK_FEATURES)}
        self.apply(docs, bp.plan_migration(docs, rules.PLAN_CATALOG, {}, rules.BILLBOOK_FEATURES))
        after = {p["id"]: p["configured"] for p in bp.evaluate_plans(docs, rules.PLAN_CATALOG, {}, rules.BILLBOOK_FEATURES)}
        self.assertEqual(before, after)

    def test_name_derived_duration_requires_explicit_acceptance(self):
        docs = [{"id": "x1", "name": "6 Months", "price": 900}]
        self.assertEqual(bp.plan_migration(docs, [], {}, [])["updates"][0]["set"].get("duration_months"), None)
        accepted = bp.plan_migration(docs, [], {}, [], accept_name_durations=True)
        self.assertEqual(accepted["updates"][0]["set"]["duration_months"], 6)

    def test_records_without_any_usable_id_are_reported_not_touched(self):
        result = bp.plan_migration([{"_id": "z", "name": "Mystery", "price": 5}], [], {}, [])
        self.assertEqual(result["updates"], [])
        self.assertEqual(len(result["unidentified"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class FreeTrialPlanConfiguration(unittest.TestCase):
    def test_free_trial_can_be_created_without_a_price_and_is_zero_value(self):
        h = Harness(prices={})
        result = create(h, id="trial-15-days", name="15 Day Free Trial", plan_type="free_trial",
                        price=0, duration_days=15, duration_months=0,
                        eligibility_rules={"one_per_customer": True})
        stored = h.db["billbook_plans"].docs[0]
        self.assertEqual(stored["price"], 0)
        self.assertEqual(stored["plan_type"], "free_trial")
        self.assertEqual(stored["duration_days"], 15)
        self.assertEqual(result["data"]["plan"]["price"], 0)
        # Trials remain Admin/MongoDB managed but are not ordinary paid-plan cards.
        self.assertFalse(any(p.get("plan_type") in {"trial", "free_trial"} for p in public(h)["plans"]))

    def test_free_trial_rejects_positive_price_and_invalid_duration(self):
        h = Harness(prices={})
        for body in ({"plan_type": "free_trial", "price": 399},
                     {"plan_type": "free_trial", "price": 0, "duration_days": 0, "duration_months": 0}):
            with self.assertRaises(HTTPException):
                create(h, **body)
        self.assertEqual(h.db["billbook_plans"].docs, [])

    def test_paid_plan_cannot_be_created_without_positive_price(self):
        h = Harness(prices={})
        for value in (None, "", 0, -1):
            with self.assertRaises(HTTPException):
                create(h, price=value, plan_type="paid")
        self.assertEqual(h.db["billbook_plans"].docs, [])
