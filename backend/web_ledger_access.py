"""Authorization policy for the internal NTAXCO Admin Web Ledger."""

ADMIN_ROLES = {"admin", "super_admin", "superadmin"}

def web_ledger_authorized(user: dict) -> bool:
    """Allow authenticated administrator accounts to use the Admin Web Ledger.

    The API endpoint already requires a valid authenticated user. Keep this
    role gate server-side so regular customers/employees cannot gain access by
    setting a client-side flag or adding an entitlement to their profile.
    """
    if not isinstance(user, dict):
        return False
    role = str(user.get("role", "")).strip().lower().replace("-", "_").replace(" ", "_")
    return role in ADMIN_ROLES
