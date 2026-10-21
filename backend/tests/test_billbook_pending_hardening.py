"""Regression checks for hardening added to the existing BillBook/customer flows.

These checks intentionally supplement (not replace) integration tests against MongoDB/Razorpay.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SERVER = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
SUBSCRIPTIONS = (ROOT / "backend" / "billbook_subscription.py").read_text(encoding="utf-8")
CUSTOMERS_UI = (ROOT / "admin" / "src" / "pages" / "ledger" / "WebLedgerCustomers.jsx").read_text(encoding="utf-8")


class CustomerEditHardeningTests(unittest.TestCase):
    def test_update_route_rejects_protected_fields_and_empty_updates(self):
        self.assertIn('unsupported = sorted(set(body) - allowed)', SERVER)
        self.assertIn('"Provide at least one customer field to update."', SERVER)
        self.assertIn('"One or more fields cannot be edited."', SERVER)

    def test_edit_validation_covers_email_pan_pin_and_tax_classifications(self):
        for expected in (
            '"Enter a valid email address."', '"PAN format is invalid."',
            '"PIN code must contain six digits."',
            '"Tax classifications contain an unsupported value."',
        ):
            self.assertIn(expected, SERVER)

    def test_mobile_normalization_and_duplicate_checks_are_shared_by_create_and_edit(self):
        self.assertGreaterEqual(SERVER.count('"mobile": {"$in": [mobile, "+91" + mobile, "91" + mobile]}'), 2)
        self.assertIn('if mobile.startswith("+91")', SERVER)

    def test_frontend_sends_only_editable_fields(self):
        self.assertIn('const editableFields = ["name", "mobile", "email"', CUSTOMERS_UI)
        self.assertIn('api.patch(`/web-ledger/customers/${editing.id}`, payload)', CUSTOMERS_UI)
        self.assertIn('window.dispatchEvent(new Event("ntaxco:data-changed"))', CUSTOMERS_UI)


class SubscriptionHardeningTests(unittest.TestCase):
    def test_guest_claim_requires_verified_contact_match(self):
        self.assertIn('email_verified = bool(user.get("email_verified") or user.get("verified_email") or meta.get("email_verified"))', SUBSCRIPTIONS)
        self.assertIn('mobile_verified = bool(user.get("mobile_verified") or user.get("phone_verified") or meta.get("mobile_verified") or meta.get("otp_login"))', SUBSCRIPTIONS)
        self.assertIn('if not (email_match or mobile_match):', SUBSCRIPTIONS)

    def test_refund_webhook_records_refunds_without_deleting_history(self):
        self.assertIn('str(etype or "").startswith("refund.")', SUBSCRIPTIONS)
        self.assertIn('"refund_amount": round(refund_amount / 100, 2)', SUBSCRIPTIONS)
        self.assertIn('"status": "Refunded" if full_refund else "Completed"', SUBSCRIPTIONS)
        self.assertIn('"status": "refunded"', SUBSCRIPTIONS)


if __name__ == "__main__":
    unittest.main()
