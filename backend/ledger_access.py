"""My Ledger access rules (no payment / subscription logic).

Authentication is always done by the existing NTAXCO backend (``get_current_user``).
This module only answers: "may this *already authenticated customer* open My Ledger?"

Two non-payment modes:
  * default (LEDGER_OPEN_ACCESS unset/false): deny-by-default. The customer's server-side
    user record must carry an explicit entitlement (the pre-existing rule).
  * LEDGER_OPEN_ACCESS=true: every authenticated *customer* may open My Ledger. Use this
    for the "no payment required" phase. It never bypasses authentication or the role check.
"""
import os

_TRUE = {"1", "true", "yes", "on"}


def ledger_open_access_enabled(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get("LEDGER_OPEN_ACCESS", "")).strip().lower() in _TRUE


def has_ledger_entitlement(user: dict) -> bool:
    """Explicit, server-side entitlement on the authenticated user's own record."""
    if not isinstance(user, dict):
        return False
    meta = user.get("meta") if isinstance(user.get("meta"), dict) else {}
    permissions = meta.get("permissions")
    permissions = permissions if isinstance(permissions, list) else []
    return (
        user.get("ledger_access") is True
        or meta.get("ledger_access") is True
        or "ledger" in permissions
    )


def can_access_ledger(user: dict, environ=None) -> bool:
    if not isinstance(user, dict) or user.get("role") != "customer":
        return False
    return ledger_open_access_enabled(environ) or has_ledger_entitlement(user)
