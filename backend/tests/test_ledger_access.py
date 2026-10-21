"""Unit tests for My Ledger access rules. Pure logic: no DB, no network, no payment."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledger_access import can_access_ledger, has_ledger_entitlement, ledger_open_access_enabled  # noqa: E402

CLOSED = {}
OPEN = {"LEDGER_OPEN_ACCESS": "true"}


def customer(**extra):
    return {"id": "c1", "role": "customer", **extra}


def test_denied_by_default_without_entitlement():
    assert can_access_ledger(customer(), CLOSED) is False


def test_explicit_entitlements_allow():
    assert can_access_ledger(customer(ledger_access=True), CLOSED)
    assert can_access_ledger(customer(meta={"ledger_access": True}), CLOSED)
    assert can_access_ledger(customer(meta={"permissions": ["ledger"]}), CLOSED)


def test_loose_values_are_not_entitlements():
    assert not has_ledger_entitlement(customer(ledger_access="true"))
    assert not has_ledger_entitlement(customer(ledger_access=1))
    assert not has_ledger_entitlement(customer(meta={"permissions": "ledger"}))
    assert not has_ledger_entitlement(customer(meta={"permissions": ["billing"]}))
    assert not has_ledger_entitlement(customer(meta="ledger"))


def test_open_access_allows_any_authenticated_customer():
    assert can_access_ledger(customer(), OPEN) is True


def test_open_access_flag_parsing():
    for v in ("true", "TRUE", " 1 ", "yes", "on"):
        assert ledger_open_access_enabled({"LEDGER_OPEN_ACCESS": v})
    for v in ("", "false", "0", "no", "off", "maybe"):
        assert not ledger_open_access_enabled({"LEDGER_OPEN_ACCESS": v})
    assert not ledger_open_access_enabled({})


def test_non_customers_never_allowed_even_when_open():
    for role in ("admin", "super_admin", "employee", "agent", None):
        assert can_access_ledger({"id": "x", "role": role, "ledger_access": True}, OPEN) is False


def test_unauthenticated_or_malformed_user_denied():
    for bad in (None, {}, [], "customer"):
        assert can_access_ledger(bad, OPEN) is False
