"""Isolated authorization tests for the internal Admin Web Ledger."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_ledger_access import web_ledger_authorized


def test_all_admin_role_variants_can_access_without_extra_entitlement():
    assert web_ledger_authorized({"role": "admin"})
    assert web_ledger_authorized({"role": "super_admin"})
    assert web_ledger_authorized({"role": "superadmin"})
    assert web_ledger_authorized({"role": "Admin"})
    assert web_ledger_authorized({"role": "ADMIN"})


def test_non_admins_never_gain_access_from_entitlement():
    for role in ("customer", "agent", "employee", "user", "", None):
        assert not web_ledger_authorized({"role": role, "ledger_access": True})
        assert not web_ledger_authorized({"role": role, "meta": {"permissions": ["web-ledger"]}})
    assert not web_ledger_authorized(None)
    assert not web_ledger_authorized("admin")
