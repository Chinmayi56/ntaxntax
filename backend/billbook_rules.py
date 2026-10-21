"""Pure (dependency-free) rules for My BillBook subscriptions: plan catalog, price parsing,
customer-detail validation, Razorpay signature checks and expiry calculation.

Kept free of FastAPI/Mongo imports so it can be unit-tested anywhere.
Prices are NEVER defined here - see billbook_subscription.py.
"""
import hashlib
import hmac
import math
import os
import re
from calendar import monthrange
from datetime import datetime, timedelta, timezone

PLAN_CATALOG = [
    {"id": "15_days", "name": "15 Days", "duration_days": 15, "duration_months": 0, "billing_period": "15 days"},
    {"id": "1_month", "name": "1 Month", "duration_days": 0, "duration_months": 1, "billing_period": "1 month"},
    {"id": "6_months", "name": "6 Months", "duration_days": 0, "duration_months": 6, "billing_period": "6 months"},
    {"id": "1_year", "name": "1 Year", "duration_days": 0, "duration_months": 12, "billing_period": "1 year"},
]
PLAN_IDS = [p["id"] for p in PLAN_CATALOG]

# Descriptive feature list taken from the existing My BillBook workspace description
# (customers, invoices, inventory, payments, reports). Identical for every plan - no
# plan-specific entitlement is invented.
BILLBOOK_FEATURES = [
    "Business customer and supplier records",
    "GST-ready invoices and quotations",
    "Inventory and product management",
    "Payments, purchases and expense tracking",
    "Business reports",
]

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MOBILE_RE = re.compile(r"^[6-9][0-9]{9}$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
BUSINESS_TYPES = [
    "Proprietorship", "Partnership", "LLP", "Private Limited", "Public Limited",
    "Trader / Retailer", "Manufacturer", "Service Provider", "Freelancer / Professional", "Other",
]


# ----------------------------------------------------------------------------------------------
# Pure helpers (unit-tested without a database)
# ----------------------------------------------------------------------------------------------
def add_months(start: datetime, months: int) -> datetime:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def compute_expiry(start: datetime, plan: dict) -> datetime:
    """Expiry = start + configured duration (calendar months for month/year plans)."""
    end = start
    if plan.get("duration_months"):
        end = add_months(end, int(plan["duration_months"]))
    if plan.get("duration_days"):
        end = end + timedelta(days=int(plan["duration_days"]))
    return end


def parse_price(value):
    """Return a positive, finite price (float, rupees) or None when missing/invalid. Never invents one.

    Accepts numbers, numeric strings (``"1,499"``, ``"₹499"``, ``"Rs. 499/-"``), ``Decimal`` /
    BSON ``Decimal128`` values and ``{"amount": ...}`` style sub-documents that legacy records used.
    """
    if value is None or isinstance(value, bool):
        return None
    if hasattr(value, "to_decimal"):  # bson.Decimal128 without importing bson
        try:
            value = value.to_decimal()
        except Exception:
            return None
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "inr"):
            if key in value:
                return parse_price(value[key])
        return None
    if isinstance(value, str):
        text = value.strip()
        text = re.sub(r"(?i)^(inr|rs\.?|\u20b9)\s*", "", text)
        text = re.sub(r"(?i)\s*(/-|only|inr|rs\.?)$", "", text)
        value = text.replace(",", "").strip()
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return round(price, 2)


def validate_customer_details(body: dict) -> dict:
    """Return {field: message} errors; empty dict when valid."""
    errors = {}
    name = str(body.get("full_name") or "").strip()
    mobile = re.sub(r"[\s\-]", "", str(body.get("mobile") or ""))
    if mobile.startswith("+91"):
        mobile = mobile[3:]
    elif mobile.startswith("91") and len(mobile) == 12:
        mobile = mobile[2:]
    email = str(body.get("email") or "").strip()
    business = str(body.get("business_name") or "").strip()
    btype = str(body.get("business_type") or "").strip()
    gstin = str(body.get("gstin") or "").strip().upper()
    if not name:
        errors["full_name"] = "Full name is required."
    elif len(name) > 120:
        errors["full_name"] = "Full name is too long."
    if not mobile:
        errors["mobile"] = "Mobile number is required."
    elif not MOBILE_RE.match(mobile):
        errors["mobile"] = "Enter a valid 10-digit Indian mobile number."
    if not email:
        errors["email"] = "Email address is required."
    elif not EMAIL_RE.match(email) or len(email) > 254:
        errors["email"] = "Enter a valid email address."
    if not business:
        errors["business_name"] = "Business name is required."
    elif len(business) > 160:
        errors["business_name"] = "Business name is too long."
    if not btype:
        errors["business_type"] = "Business type is required."
    elif btype not in BUSINESS_TYPES:
        errors["business_type"] = "Select a valid business type."
    if gstin and not GSTIN_RE.match(gstin):
        errors["gstin"] = "Enter a valid 15-character GSTIN."
    return errors


def normalized_details(body: dict) -> dict:
    mobile = re.sub(r"[\s\-]", "", str(body.get("mobile") or ""))
    if mobile.startswith("+91"):
        mobile = mobile[3:]
    elif mobile.startswith("91") and len(mobile) == 12:
        mobile = mobile[2:]
    return {
        "full_name": str(body.get("full_name") or "").strip(),
        "mobile": mobile,
        "email": str(body.get("email") or "").strip().lower(),
        "business_name": str(body.get("business_name") or "").strip(),
        "business_type": str(body.get("business_type") or "").strip(),
        "gstin": str(body.get("gstin") or "").strip().upper(),
    }


def verify_checkout_signature(order_id: str, payment_id: str, signature: str, secret: str) -> bool:
    """Razorpay checkout signature: HMAC-SHA256(order_id|payment_id, key_secret)."""
    if not (order_id and payment_id and signature and secret):
        return False
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature))


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Razorpay webhook signature: HMAC-SHA256(raw body, webhook secret)."""
    if not (raw_body is not None and signature and secret):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature))


def subscription_state(sub: dict, now: datetime = None) -> str:
    """Derive the actual status from stored facts - never from a client value."""
    now = now or datetime.now(timezone.utc)
    if not sub:
        return "none"
    status = sub.get("status")
    if status == "active":
        expires = _parse_dt(sub.get("expires_at"))
        return "active" if expires and expires > now else "expired"
    return status or "unknown"


def _parse_dt(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def subscription_required(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get("BILLBOOK_SUBSCRIPTION_REQUIRED", "true")).strip().lower() not in {"0", "false", "no", "off"}
