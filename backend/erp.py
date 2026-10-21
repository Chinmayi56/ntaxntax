"""NTAXCO ERP business modules: MongoDB-backed generic CRUD and workflows.

The legacy seed datasets remain in this source only as schema/reference material;
production startup never inserts them unless NTAXCO_ENABLE_DEMO_SEED=true."""
from fastapi import APIRouter, Body, HTTPException, Query, Depends
from datetime import datetime, timezone, timedelta
from index_utils import ensure_index
import re
import base64
import hashlib
import uuid
import bcrypt
import os
import logging
import asyncio
try:
    from bson import ObjectId
except Exception:  # pragma: no cover - dependency is provided by the existing Mongo stack
    ObjectId = None


def _hash_password(password: str) -> str:
    """Same bcrypt scheme as server.py's hash_password/verify_password so an
    employee password set here authenticates through the existing login path."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _validate_percentage(body: dict, field: str):
    """Shared 0-100 (decimals allowed) validation for agent/customer commission fields."""
    if field not in body or body[field] in (None, ""):
        return
    try:
        value = float(body[field])
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{field.replace('_', ' ').title()} must be a number")
    if value < 0 or value > 100:
        raise HTTPException(status_code=422, detail=f"{field.replace('_', ' ').title()} must be between 0 and 100")
    body[field] = value

# India Standard Time offset. Attendance check-in/out is always evaluated
# and stamped using server-side IST time (never a client-supplied
# timestamp) so it can't be spoofed and stays consistent regardless of the
# employee's browser timezone/clock. Stored as simple "HH:MM" / "YYYY-MM-DD"
# strings to match the existing seeded attendance format.
IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_now() -> datetime:
    return datetime.now(timezone.utc) + IST_OFFSET


def _ist_today_str() -> str:
    return _ist_now().strftime("%Y-%m-%d")


def _ist_time_str() -> str:
    return _ist_now().strftime("%H:%M")


def _hours_between(check_in: str, check_out: str) -> str:
    try:
        ih, im = (int(x) for x in check_in.split(":"))
        oh, om = (int(x) for x in check_out.split(":"))
        mins = (oh * 60 + om) - (ih * 60 + im)
        if mins < 0:
            mins = 0
        return f"{mins // 60}h {mins % 60:02d}m"
    except Exception:
        return "-"

READ_ROLES = {
    "employees": {"admin", "employee"}, "customers": {"admin", "employee", "agent", "customer"},
    "bookings": {"admin", "employee", "agent", "customer"}, "projects": {"admin", "employee", "agent", "customer"},
    "invoices": {"admin", "employee", "customer"}, "documents": {"admin", "employee", "agent", "customer"},
    "tickets": {"admin", "employee", "customer"}, "services": {"admin", "employee", "agent", "customer"},
    "gst": {"admin", "employee", "customer"}, "itr": {"admin", "employee", "customer"},
    "tds": {"admin", "employee", "customer"}, "roc": {"admin", "employee", "customer"},
    "attendance": {"admin", "employee"}, "leaves": {"admin", "employee"}, "tasks": {"admin", "employee"},
    "payslips": {"admin", "employee"}, "leads": {"admin", "agent"}, "appointments": {"admin", "employee", "agent"},
    "commissions": {"admin", "agent"}, "journal": {"admin"}, "payments": {"admin", "customer"},
    "agents": {"admin", "customer"}, "site-images": {"admin"},
}

# Customers need the assignable consultant list to complete a booking, but must never see
# commission rates, contact details or any other admin-only agent data.
PUBLIC_AGENT_FIELDS = ("id", "name", "status")


def _public_agent(item: dict) -> dict:
    return {k: item.get(k) for k in PUBLIC_AGENT_FIELDS if k in item}

WRITE_ROLES = {
    "employees": {"admin"}, "customers": {"admin", "agent"}, "bookings": {"admin", "customer", "agent"},
    "projects": {"admin", "employee", "agent"}, "invoices": {"admin"}, "documents": {"admin", "employee", "customer"},
    "tickets": {"admin", "customer"}, "services": {"admin"}, "gst": {"admin", "employee"}, "itr": {"admin", "employee"},
    "tds": {"admin", "employee"}, "roc": {"admin", "employee"}, "attendance": {"admin", "employee"}, "leaves": {"admin", "employee"},
    "tasks": {"admin", "employee"}, "payslips": {"admin"}, "leads": {"admin", "agent"},
    "appointments": {"admin", "agent"}, "commissions": {"admin"}, "journal": {"admin"},
    "payments": {"admin"}, "agents": {"admin"}, "site-images": {"admin"},
}

# ---------------- Centralized image management ----------------
# Placements an admin-uploaded image can be assigned to on the customer site.
SITE_IMAGE_PLACEMENTS = {"home", "dashboard", "projects", "services"}

# Roughly 5MB of raw image data once base64-encoded (base64 inflates size by
# ~4/3), used to keep a single Mongo document well under the 16MB doc limit
# and to stop an admin from accidentally uploading something huge.
MAX_IMAGE_DATA_LEN = 7_000_000


ALLOWED_DOCUMENT_MIMES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
MAX_DOCUMENT_DATA_LEN = 7_000_000

_DOC_EXT_BY_MIME = {
    "application/pdf": {".pdf"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
    "application/msword": {".doc"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
}
_BLOCKED_DOC_EXTS = {".exe", ".bat", ".cmd", ".com", ".msi", ".js", ".vbs", ".ps1", ".sh", ".jar", ".scr", ".dll", ".php", ".py", ".html", ".htm", ".svg"}


def _sanitize_file_name(raw) -> str:
    name = str(raw or "document").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*\x00').strip().strip(".")
    return (name or "document")[:150]


def _magic_matches(mime: str, raw: bytes) -> bool:
    """Cheap content sniffing so a renamed file cannot masquerade as a PDF/image."""
    if mime == "application/pdf":
        return raw[:5] == b"%PDF-"
    if mime == "image/jpeg":
        return raw[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return raw[:8] == b"\x89PNG\r\n\x1a\n"
    if mime == "image/webp":
        return raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    if mime in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        return raw[:2] == b"PK"
    if mime == "application/msword":
        return raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    return False


def _validate_document_upload(body: dict, user: dict) -> None:
    if "file_data" not in body:
        return
    data = str(body.get("file_data") or "")
    if len(data) > MAX_DOCUMENT_DATA_LEN:
        raise HTTPException(status_code=400, detail="Document is too large. Please upload a file under ~5MB.")
    if not data.startswith("data:") or ";base64," not in data[:120]:
        raise HTTPException(status_code=400, detail="Invalid document upload format")
    mime = data[5:data.find(";base64,")]
    if mime not in ALLOWED_DOCUMENT_MIMES:
        raise HTTPException(status_code=400, detail="Unsupported document type. Allowed: PDF, JPG, JPEG, PNG, WebP, DOC, DOCX, XLSX.")
    file_name = _sanitize_file_name(body.get("file_name"))
    ext = ("." + file_name.rsplit(".", 1)[-1].lower()) if "." in file_name else ""
    if ext in _BLOCKED_DOC_EXTS:
        raise HTTPException(status_code=400, detail=f"Files of type {ext} are not allowed.")
    if ext and ext not in _DOC_EXT_BY_MIME.get(mime, set()):
        raise HTTPException(status_code=400, detail=f"File extension {ext} does not match the file type ({mime}).")
    try:
        raw = base64.b64decode(data[data.find(";base64,") + 8:], validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="The uploaded file could not be decoded.")
    if not raw:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if not _magic_matches(mime, raw):
        raise HTTPException(status_code=400, detail="The file content does not match its declared type.")
    body["file_name"] = file_name
    body["mime_type"] = mime
    body["file_data"] = data
    body["file_size"] = len(raw)
    body["file_hash"] = hashlib.sha256(raw).hexdigest()
    body.setdefault("status", "Uploaded")
    body.setdefault("uploaded_by", user.get("name") or "Customer Upload")
    body.setdefault("uploaded_date", _ist_today_str())

def _validate_site_image(body: dict, *, partial: bool = False) -> None:
    """Normalize + validate a site-image payload in place. When `partial` is
    True (updates), a field is only checked if the caller actually sent it —
    so an edit that only changes the title doesn't need to resend the image."""
    if "placement" in body or not partial:
        placement = str(body.get("placement", "")).strip().lower()
        if placement not in SITE_IMAGE_PLACEMENTS:
            raise HTTPException(
                status_code=400,
                detail=f"Placement must be one of: {', '.join(sorted(SITE_IMAGE_PLACEMENTS))}",
            )
        body["placement"] = placement
    if "image" in body or not partial:
        image = str(body.get("image", "")).strip()
        if not image:
            raise HTTPException(status_code=400, detail="An image (file upload or URL) is required")
        if len(image) > MAX_IMAGE_DATA_LEN:
            raise HTTPException(status_code=400, detail="Image is too large. Please upload an image under ~5MB.")
        body["image"] = image
    if "title" in body:
        body["title"] = str(body.get("title") or "").strip()
    if "status" in body:
        status = str(body.get("status") or "Active").strip() or "Active"
        body["status"] = status

logger = logging.getLogger("ntaxco.notifications")

# Notification kinds. `type` (urgent/warning/information) and `category` remain
# the existing display fields; `kind` is the machine-readable event type that
# links a notification to a customer / booking / service.
KIND_SERVICE_BOOKING = "SERVICE_BOOKING"
KIND_CUSTOMER_REGISTERED = "CUSTOMER_REGISTERED"
KIND_CUSTOMER_LOGIN = "CUSTOMER_LOGIN"
KIND_BOOKING_STATUS = "BOOKING_STATUS"
KIND_LEAVE_REQUEST = "LEAVE_REQUEST"
KIND_LEAVE_STATUS = "LEAVE_STATUS"


async def ensure_notification_indexes(db):
    """Idempotently initialize notification, booking and Task Board indexes.

    Existing equivalent indexes are reused by key/options rather than by name;
    no legacy index is dropped or renamed.
    """
    await ensure_index(
        db["erp_notifications"],
        [("event_key", 1), ("user_id", 1)],
        unique=True,
        partial_filter={"event_key": {"$type": "string"}},
        name="uniq_notification_event_recipient",
    )
    await ensure_index(db["erp_notifications"], [("ts", -1)], name="notification_ts")
    await ensure_index(
        db["erp_payments"],
        [("customer_id", 1), ("client_request_id", 1)],
        unique=True,
        partial_filter={"client_request_id": {"$type": "string"}},
        name="uniq_payment_client_request",
    )
    await ensure_index(
        db["erp_documents"],
        [("customer_id", 1), ("file_hash", 1)],
        name="document_customer_hash",
    )
    await ensure_index(
        db["erp_bookings"],
        [("customer_id", 1), ("client_request_id", 1)],
        unique=True,
        partial_filter={"client_request_id": {"$type": "string"}},
        name="uniq_booking_client_request",
    )
    await ensure_index(
        db["erp_tasks"],
        [("auto_booking_id", 1)],
        unique=True,
        partial_filter={"auto_booking_id": {"$type": "string"}},
        name="uniq_task_per_accepted_booking",
    )
    for _fields, _name in [
        ([("task_board", 1), ("status", 1), ("created_at", -1)], "task_board_status_created"),
        ([("task_board", 1), ("employee_id", 1), ("due_date", 1)], "task_board_employee_due"),
        ([("task_board", 1), ("customer_id", 1), ("due_date", 1)], "task_board_customer_due"),
        ([("task_board", 1), ("service_id", 1), ("due_date", 1)], "task_board_service_due"),
        ([("task_board", 1), ("priority", 1), ("due_date", 1)], "task_board_priority_due"),
        ([("task_board", 1), ("updated_at", -1)], "task_board_updated"),
    ]:
        await ensure_index(db["erp_tasks"], _fields, name=_name)

async def create_notification(db, role, title, description, category, ntype="information", priority=None,
                              user_id=None, kind=None, event_key=None, **links):
    """Insert a notification into the existing `erp_notifications` collection.

    `links` may carry customer_id / booking_id / service_id / customer_name /
    service_name. When `event_key` is given, the same event can never notify
    the same recipient twice (network retries, double submits). Returns the
    number of notification documents created."""
    prio = priority or {"urgent": "High", "warning": "Medium", "information": "Low"}.get(ntype, "Low")
    targets = [user_id] if user_id else [u.get("id") async for u in db.users.find({"role": role}, {"_id": 0, "id": 1})]
    if not targets and role in {"admin", "all"}:
        targets = [None]
    now = datetime.now(timezone.utc).isoformat()
    created = 0
    for uid in targets:
        if event_key and await db["erp_notifications"].find_one({"event_key": event_key, "user_id": uid}, {"_id": 1}):
            continue
        doc = {
            "id": f"NTF-{uuid.uuid4().hex[:8].upper()}", "role": role, "user_id": uid, "title": title,
            "description": description, "category": category, "type": ntype, "priority": prio,
            "read": False, "read_at": None, "ts": now, "created_at": now,
        }
        if kind:
            doc["kind"] = kind
        if event_key:
            doc["event_key"] = event_key
        doc.update({k: v for k, v in links.items() if v not in (None, "")})
        try:
            await db["erp_notifications"].insert_one(doc)
            created += 1
        except Exception as exc:  # duplicate key from a concurrent retry is expected and harmless
            if "duplicate" in str(exc).lower() or "E11000" in str(exc):
                continue
            raise
    return created


async def notify_customer_registered(db, customer: dict):
    """Admin notification for a newly self-registered customer. Never raises:
    the customer record is already persisted, so a notification failure must
    not turn a successful registration into an error."""
    try:
        name = customer.get("business_name") or customer.get("owner") or customer.get("id")
        await create_notification(
            db, "admin", "New customer registered", f"{name} created a customer account", "Customers", "information",
            kind=KIND_CUSTOMER_REGISTERED, event_key=f"customer-registered:{customer.get('id')}",
            customer_id=customer.get("id"), customer_name=name,
        )
    except Exception:
        logger.exception("Could not create CUSTOMER_REGISTERED notification for %s", customer.get("id"))


async def notify_customer_login(db, user: dict, session_id=None):
    """Optional (off by default) login notification — enable with
    NTAXCO_NOTIFY_CUSTOMER_LOGIN=true. One per login session, never raises."""
    if os.getenv("NTAXCO_NOTIFY_CUSTOMER_LOGIN", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        cid = (user.get("meta") or {}).get("customer_id")
        await create_notification(
            db, "admin", "Customer logged in", f"{user.get('name') or user.get('email')} signed in to the customer portal",
            "Customers", "information", priority="Low", kind=KIND_CUSTOMER_LOGIN,
            event_key=f"customer-login:{session_id or uuid.uuid4().hex}", customer_id=cid, customer_name=user.get("name"),
        )
    except Exception:
        logger.exception("Could not create CUSTOMER_LOGIN notification")


def _present_notification(n: dict) -> dict:
    out = dict(n)
    out["is_read"] = bool(out.get("read"))
    out.setdefault("read_at", None)
    out.setdefault("created_at", out.get("ts"))
    return out



def _json_safe(value):
    """Recursively make a MongoDB document JSON-encodable: datetime -> ISO string, NaN/inf -> None,
    ObjectId/Decimal128/bytes/anything else non-JSON -> str. Plain str/int/float/bool/None pass through."""
    import math
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _ok(data, message="OK", pagination=None):
    return {
        "success": True,
        "message": message,
        "data": data,
        "pagination": pagination,
        "meta": {"count": len(data) if isinstance(data, list) else 1},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": str(uuid.uuid4()),
    }

# ---------------- MongoDB collections ----------------
# The second tuple item is intentionally empty: production data is created and
# updated through the authenticated APIs, never through hardcoded seed records.
# ---------------------------------------------------------------------------
# Status model (single source of truth for how the different "statuses" relate)
#
#   customer.status          -> ACCOUNT status (Active / Inactive / Suspended). Never a workflow state.
#   customer.filing_status   -> customer WORKFLOW status shown on the Customers page:
#                               Pending / Processing / Completed (+ "Paid", a payment marker that
#                               is mutually exclusive with the three workflow states).
#   customer.service_type    -> CATEGORY (GST / TDS / Income Tax / ...). Independent of every status.
#   booking.status           -> the real unit of work: Pending / Confirmed / Running / Processing /
#                               Completed / Cancelled.
#   erp_gst/itr/tds/roc.status -> filing-module status: Pending / Running / Completed.
#
# The customer workflow status is DERIVED from the customer's (non-cancelled) bookings, and when an admin
# sets it explicitly it is CASCADED to those bookings and the matching filing record, so the same piece
# of work can never show two different states.
# ---------------------------------------------------------------------------
ACCOUNT_STATUSES = {"Active", "Inactive", "Suspended"}
WORKFLOW_STATUSES = ("Pending", "Processing", "Completed")
BOOKING_STATUSES = {"Pending", "Running", "Confirmed", "Processing", "Completed", "Cancelled"}
BOOKING_TO_WORKFLOW = {"Pending": "Pending", "Confirmed": "Processing", "Running": "Processing",
                       "Processing": "Processing", "Completed": "Completed"}
WORKFLOW_TO_BOOKING = {"Pending": "Pending", "Processing": "Running", "Completed": "Completed"}
WORKFLOW_TO_MODULE = {"Pending": "Pending", "Processing": "Running", "Completed": "Completed"}
BOOKING_TO_MODULE = {"Pending": "Pending", "Confirmed": "Pending", "Running": "Running",
                     "Processing": "Running", "Completed": "Completed"}
MODULE_COLLECTION_BY_CATEGORY = {"gst": "erp_gst", "income tax": "erp_itr", "itr": "erp_itr",
                                 "income-tax": "erp_itr", "tds": "erp_tds", "roc": "erp_roc"}


def _aggregate_status(statuses, mapping):
    """Roll several record statuses up into one Pending / Processing / Completed style value."""
    mapped = [mapping[s] for s in statuses if s in mapping]
    if not mapped:
        return None
    if all(m == "Completed" for m in mapped):
        return "Completed"
    if all(m == "Pending" for m in mapped):
        return "Pending"
    return "Processing" if "Processing" in mapping.values() else "Running"


def _module_collection_for(category):
    return MODULE_COLLECTION_BY_CATEGORY.get(str(category or "").strip().lower())


# ---------------------------------------------------------------------------
# What counts as a SERVICE.
#
# A service is something NTAXCO sells to customers (GST Registration, ITR filing, TDS return, ROC filing,
# Accounting ...). The application name, vendor/developer name, deployment name or a bare placeholder
# ("Service", "Unknown") is never a service. Such a record in the service catalogue makes the Admin
# Drag & Drop panel, the customer's service list, the Booking History and the Task Board show the wrong
# thing, so it is rejected on create/update and quarantined (archived, never deleted) if it already exists.
# ---------------------------------------------------------------------------
NON_SERVICE_LABELS = {
    "strivenest", "strivenet", "strive nest", "strive net",
    "ntaxco", "ntaxco erp", "ntaxco portal",
    "service", "services", "unknown", "undefined", "null", "none", "n a",
}
SERVICE_CATEGORIES = ("GST", "Income Tax", "TDS", "ROC", "Accounting", "Others")
INACTIVE_SERVICE_STATUSES = {"inactive", "archived", "hidden", "disabled"}


def _label_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def is_non_service_label(value) -> bool:
    key = _label_key(value)
    return bool(key) and (key in NON_SERVICE_LABELS or key.replace(" ", "") in {"strivenest", "strivenet"})


def is_valid_service_record(service) -> bool:
    """A real, bookable service record: has an id and a real name/category (not an app/placeholder label)."""
    if not service or not service.get("id"):
        return False
    labels = [service.get("name"), service.get("title"), service.get("category")]
    return not any(is_non_service_label(x) for x in labels if str(x or "").strip())


def is_active_service_record(service) -> bool:
    return str((service or {}).get("status") or "Active").strip().lower() not in INACTIVE_SERVICE_STATUSES


def service_category_bucket(service_or_label) -> str:
    """Map a service record (or a category/name string) onto the six NTAXCO categories."""
    if isinstance(service_or_label, dict):
        candidates = [service_or_label.get("category"), service_or_label.get("name"), service_or_label.get("title")]
    else:
        candidates = [service_or_label]
    for raw in candidates:
        v = _label_key(raw)
        if not v:
            continue
        words = set(v.split())
        if "gst" in words or "gstr" in words:
            return "GST"
        if "tds" in words or "tcs" in words:
            return "TDS"
        if v.startswith("income tax") or "income tax" in v or "itr" in words or "capital gains" in v or "tax planning" in v:
            return "Income Tax"
        if "roc" in words or "mca" in words or "aoc" in words or "mgt" in words or "company registration" in v or "llp" in words:
            return "ROC"
        if "accounting" in v or "bookkeeping" in v or "accounts" in words or "mis" in words:
            return "Accounting"
    return "Others"


def _good_service_name(*candidates):
    """First candidate that is a real service name (never an app/placeholder label)."""
    for c in candidates:
        if str(c or "").strip() and not is_non_service_label(c):
            return str(c).strip()
    return None


COLLECTIONS = {
    "employees": ("erp_employees", [], "EMP"),
    "customers": ("erp_customers", [], "CUS"),
    "bookings": ("erp_bookings", [], "BKG"),
    "projects": ("erp_projects", [], "PRJ"),
    "invoices": ("erp_invoices", [], "INV"),
    "documents": ("erp_documents", [], "DOC"),
    "tickets": ("erp_tickets", [], "TKT"),
    "services": ("erp_services", [], "SVC"),
    "gst": ("erp_gst", [], "GST"),
    "itr": ("erp_itr", [], "ITR"),
    "tds": ("erp_tds", [], "TDS"),
    "roc": ("erp_roc", [], "ROC"),
    "attendance": ("erp_attendance", [], "ATT"),
    "leaves": ("erp_leaves", [], "LV"),
    "tasks": ("erp_tasks", [], "TSK"),
    "payslips": ("erp_payslips", [], "PAY"),
    "leads": ("erp_leads", [], "LEAD"),
    "appointments": ("erp_appointments", [], "APT"),
    "commissions": ("erp_commissions", [], "COM"),
    "journal": ("erp_journal", [], "JE"),
    "payments": ("erp_payments", [], "PMT"),
    "agents": ("erp_agents", [], "AG"),
    "site-images": ("erp_site_images", [], "IMG"),
}

PERSONAL_EMPLOYEE_COLLECTIONS = {"attendance", "leaves", "tasks", "payslips"}
CUSTOMER_SCOPED_COLLECTIONS = {"customers", "bookings", "invoices", "documents", "payments", "tickets", "projects", "gst", "itr", "tds", "roc"}
AGENT_SCOPED_COLLECTIONS = {"leads", "customers", "bookings", "projects", "appointments", "commissions", "documents"}


_BASIC_MODULE_STATUSES = {"Pending", "Running", "Completed"}
_MODULE_COLLECTIONS = ("erp_gst", "erp_itr", "erp_tds", "erp_roc")


async def _set_single_module_status(db, collection, customer_id, status):
    """Update the customer's filing record only when it is the one canonical auto-created record
    (a customer with several historical returns keeps each return's own status) and only when it
    is in one of the plain Pending/Running/Completed states (never overwrite Filed/Approved)."""
    if await db[collection].count_documents({"customer_id": customer_id}) != 1:
        return
    await db[collection].update_many(
        {"customer_id": customer_id, "status": {"$in": list(_BASIC_MODULE_STATUSES), "$ne": status}},
        {"$set": {"status": status}},
    )


async def reconcile_customer_status(db, customer_id):
    """Derive filing-record + customer workflow status from the customer's real bookings.

    Cancelled bookings are ignored. A customer marked "Paid" keeps that payment marker. Returns the
    derived workflow status, or None when the customer has no live bookings to derive from."""
    if not customer_id:
        return None
    bookings = await db["erp_bookings"].find({"customer_id": customer_id}, {"_id": 0}).to_list(5000)
    live = [b for b in bookings if b.get("status") != "Cancelled"]
    if not live:
        return None
    by_module = {}
    for b in live:
        category = None
        if b.get("service_id"):
            svc = await db["erp_services"].find_one({"id": b["service_id"]}, {"_id": 0, "category": 1, "name": 1})
            category = (svc or {}).get("category") or (svc or {}).get("name")
        coll = _module_collection_for(category or b.get("service_category") or b.get("service"))
        if coll:
            by_module.setdefault(coll, []).append(b.get("status"))
    for coll, sts in by_module.items():
        agg = _aggregate_status(sts, BOOKING_TO_MODULE)
        if agg:
            await _set_single_module_status(db, coll, customer_id, agg)
    derived = _aggregate_status([b.get("status") for b in live], BOOKING_TO_WORKFLOW)
    customer = await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0, "filing_status": 1})
    if derived and customer and customer.get("filing_status") != "Paid" and customer.get("filing_status") != derived:
        await db["erp_customers"].update_one({"id": customer_id}, {"$set": {"filing_status": derived}})
    return derived


async def apply_customer_workflow_status(db, customer, new_status):
    """Admin explicitly set the customer workflow status: persist it and cascade to the customer's
    live bookings and filing record. Category (service_type) and account status are untouched.
    Returns the list of bookings whose status changed."""
    cid = customer["id"]
    await db["erp_customers"].update_one({"id": cid}, {"$set": {"filing_status": new_status}})
    changed = []
    if new_status in WORKFLOW_TO_BOOKING:
        target = WORKFLOW_TO_BOOKING[new_status]
        now = datetime.now(timezone.utc).isoformat()
        for b in await db["erp_bookings"].find({"customer_id": cid}, {"_id": 0}).to_list(5000):
            if b.get("status") == "Cancelled" or BOOKING_TO_WORKFLOW.get(b.get("status")) == new_status:
                continue
            await db["erp_bookings"].update_one({"id": b["id"]}, {"$set": {"status": target, "status_updated_at": now}})
            changed.append({**b, "status": target})
        for coll in _MODULE_COLLECTIONS:
            await _set_single_module_status(db, coll, cid, WORKFLOW_TO_MODULE[new_status])
    return changed


def _category_bucket(value):
    """Map a service category / name onto the three dashboard buckets (or None)."""
    v = str(value or "").strip().lower()
    if v == "gst" or v.startswith("gst "):
        return "gst"
    if v == "tds" or v.startswith("tds "):
        return "tds"
    if v in {"income tax", "income-tax", "itr"} or v.startswith("income tax"):
        return "income_tax"
    return None


async def customer_service_categories(db, customer_rows=None):
    """customer_id -> list of service categories the customer really has.

    A customer's category is its assigned `service_type` PLUS the category of every live
    (non-cancelled) booking, resolved through the service catalog (`service_id`). Dashboard cards,
    the table filter and the customer rows all use this one function, so they cannot disagree."""
    if customer_rows is None:
        customer_rows = await db["erp_customers"].find({}, {"_id": 0, "id": 1, "service_type": 1}).to_list(None)
    services = {s["id"]: s for s in await db["erp_services"].find({}, {"_id": 0, "id": 1, "category": 1, "name": 1, "title": 1}).to_list(None) if s.get("id")}
    out = {}
    for c in customer_rows:
        cid = c.get("id")
        if not cid:
            continue
        if str(c.get("service_type") or "").strip():
            out.setdefault(cid, []).append(str(c["service_type"]).strip())
    bookings = await db["erp_bookings"].find({}, {"_id": 0, "customer_id": 1, "service_id": 1, "service": 1, "service_category": 1, "status": 1}).to_list(None)
    for b in bookings:
        cid = b.get("customer_id")
        if not cid or b.get("status") == "Cancelled":
            continue
        svc = services.get(b.get("service_id")) or {}
        cat = svc.get("category") or b.get("service_category") or svc.get("name") or svc.get("title") or b.get("service")
        cat = str(cat or "").strip()
        if cat:
            out.setdefault(cid, []).append(cat)
    # de-duplicate case-insensitively, keep first spelling
    for cid, cats in out.items():
        seen, uniq = set(), []
        for cat in cats:
            if cat.lower() not in seen:
                seen.add(cat.lower())
                uniq.append(cat)
        out[cid] = uniq
    return out


async def customer_status_summary(db):
    """Dashboard counts straight from MongoDB. Every customer lands in exactly one of
    Paid / Pending / Processing / Completed (by filing_status). GST / TDS / Income Tax count a
    customer when that category is its assigned service or the category of one of its live bookings."""
    rows = await db["erp_customers"].find({}, {"_id": 0, "id": 1, "service_type": 1, "filing_status": 1}).to_list(None)
    categories = await customer_service_categories(db, rows)
    out = {"total": len(rows), "gst": 0, "tds": 0, "income_tax": 0,
           "paid": 0, "pending": 0, "processing": 0, "completed": 0, "other": 0}
    for r in rows:
        for bucket in {_category_bucket(c) for c in categories.get(r.get("id"), [])} - {None}:
            out[bucket] += 1
        st = str(r.get("filing_status") or "").strip().lower()
        out[st if st in {"paid", "pending", "processing", "completed"} else "other"] += 1
    return out


async def quarantine_non_service_records(db):
    """Idempotent data repair for records that were never services (e.g. an app/vendor name saved in the
    service catalogue). Nothing is deleted: the bad catalogue record is Archived (so every consumer that
    already hides Archived services stops offering it), customers pointing at it are re-pointed at a real
    service they booked (or cleared), and display names on bookings/invoices/payments/tasks are re-synced
    from the real service record. Returns a summary for the startup log."""
    summary = {"archived_services": [], "customers_fixed": 0, "bookings_renamed": 0, "tasks_renamed": 0, "unresolved_bookings": 0}
    services = await db["erp_services"].find({}, {"_id": 0}).to_list(None)
    bad_ids = set()
    for svc in services:
        if not svc.get("id"):
            continue
        if not is_valid_service_record(svc):
            bad_ids.add(svc["id"])
            if str(svc.get("status") or "").strip().lower() != "archived":
                await db["erp_services"].update_one({"id": svc["id"]}, {"$set": {
                    "status": "Archived",
                    "archived_reason": "Not a service (application/vendor name or placeholder) - archived automatically",
                }})
                summary["archived_services"].append(svc["id"])
    good = {s["id"]: s for s in services if s.get("id") and s["id"] not in bad_ids}

    # Customers whose assigned service/category is one of those labels.
    async for c in db["erp_customers"].find({}, {"_id": 0, "id": 1, "service_id": 1, "service_type": 1}):
        if not (c.get("service_id") in bad_ids or is_non_service_label(c.get("service_type"))):
            continue
        replacement = None
        for b in await db["erp_bookings"].find({"customer_id": c["id"]}, {"_id": 0}).sort("created_at", -1).to_list(200):
            if b.get("status") != "Cancelled" and b.get("service_id") in good:
                replacement = good[b["service_id"]]
                break
        update = ({"service_id": replacement["id"], "service_type": replacement.get("category") or replacement.get("name")}
                  if replacement else {"service_id": None, "service_type": ""})
        await db["erp_customers"].update_one({"id": c["id"]}, {"$set": update})
        summary["customers_fixed"] += 1

    # Display names follow the real service record (service_id is the authoritative foreign key).
    for sid, svc in good.items():
        name = _good_service_name(svc.get("name"), svc.get("title"))
        if not name:
            continue
        for coll in ("erp_bookings", "erp_invoices", "erp_payments"):
            res = await db[coll].update_many({"service_id": sid, "service": {"$ne": name}}, {"$set": {"service": name}})
            if coll == "erp_bookings":
                summary["bookings_renamed"] += res.modified_count
        res = await db["erp_tasks"].update_many({"service_id": sid, "service_name": {"$ne": name}}, {"$set": {"service_name": name}})
        summary["tasks_renamed"] += res.modified_count
    # Bookings that still carry a non-service label and no real service to resolve it from: report, never guess.
    summary["unresolved_bookings"] = await db["erp_bookings"].count_documents({"$or": [
        {"service_id": {"$in": list(bad_ids)}},
        {"service": {"$in": [lbl for lbl in ("strivenest", "strivenet", "StriveNest", "Strivenet", "Strivenest")]}},
    ]})
    return summary


async def ensure_status_model(db):
    """One-time, idempotent cleanup: a workflow value (Pending/Processing/Completed) stored in the
    ACCOUNT status field is moved out of it, then workflow status is re-derived from real bookings so
    legacy rows such as "Completed customer / Pending booking" stop contradicting each other."""
    async for c in db["erp_customers"].find({"status": {"$in": list(WORKFLOW_STATUSES)}}, {"_id": 0, "id": 1, "status": 1, "filing_status": 1}):
        update = {"status": "Active"}
        if c.get("filing_status") in (None, ""):
            update["filing_status"] = c["status"]
        await db["erp_customers"].update_one({"id": c["id"]}, {"$set": update})
    async for c in db["erp_customers"].find({"filing_status": {"$in": [None, ""]}}, {"_id": 0, "id": 1}):
        await db["erp_customers"].update_one({"id": c["id"]}, {"$set": {"filing_status": "Pending"}})
    async for c in db["erp_customers"].find({}, {"_id": 0, "id": 1}):
        await reconcile_customer_status(db, c["id"])


def build_erp_router(db, get_current_user):
    router = APIRouter(prefix="/api")


    # ---------------- Global Admin AI Copilot ----------------
    # The Copilot is intentionally implemented as a small, allow-listed query
    # service over existing ERP collections/helpers. It never accepts MongoDB
    # filters, collection names, aggregation pipelines, or executable code from
    # the browser. Authentication/authorization remains the same as every Admin
    # endpoint in this router.
    async def _copilot_docs(collection, query=None, projection=None, limit=50, sort=None):
        cursor = db[collection].find(query or {}, projection or {"_id": 0})
        if sort:
            cursor = cursor.sort(sort)
        return await cursor.to_list(limit)

    def _copilot_name(row, *keys, default="Unknown"):
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
        return default

    def _copilot_amount(row, *keys):
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                try:
                    return float(str(value).replace(",", "").replace("₹", "").strip())
                except (TypeError, ValueError):
                    pass
        return 0.0

    def _copilot_date_text(row, *keys):
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                if isinstance(value, datetime):
                    return value.date().isoformat()
                return str(value)[:10]
        return ""

    def _copilot_norm_status(value):
        return re.sub(r"[^a-z]+", " ", str(value or "").strip().lower()).strip()

    def _copilot_period_start(period="month"):
        today = _ist_now().date()
        if period == "today":
            return today
        if period == "year":
            return today.replace(month=1, day=1)
        return today.replace(day=1)

    async def _copilot_revenue(period="month"):
        start = _copilot_period_start(period)
        end = _ist_now().date()
        rows = await _build_revenue_rows(start, end)
        total = round(sum(float(r.get("amount") or 0) for r in rows), 2)
        return total, rows

    async def _copilot_pending_invoice_rows(limit=25):
        invoices = await db["erp_invoices"].find({}, {"_id": 0}).sort("created_at", -1).to_list(1000)
        if not invoices:
            return []
        customer_ids = {str(x.get("customer_id")) for x in invoices if x.get("customer_id")}
        billing = await _billing_for_customers(list(customer_ids)) if customer_ids else {}
        rows = []
        for inv in invoices:
            status = _copilot_norm_status(inv.get("status") or inv.get("payment_status"))
            total = _copilot_amount(inv, "total", "amount", "grand_total", "invoice_total")
            paid = _copilot_amount(inv, "paid_amount", "paid", "amount_paid")
            outstanding = _copilot_amount(inv, "outstanding", "pending_amount", "balance", "due_amount")
            if outstanding <= 0 and inv.get("customer_id"):
                state = billing.get(str(inv.get("customer_id"))) or {}
                # If invoice-level paid data is absent, do not falsely mark a paid
                # invoice as pending; only use explicit pending-like statuses.
                if status in {"pending", "due", "unpaid", "partial", "overdue", "processing"}:
                    outstanding = max(total - paid, 0)
            pending = status in {"pending", "due", "unpaid", "partial", "overdue", "processing"} or outstanding > 0
            if not pending:
                continue
            rows.append({
                "id": inv.get("id"),
                "invoice_no": inv.get("invoice_no") or inv.get("invoice_number") or inv.get("id"),
                "customer_name": _copilot_name(inv, "customer_name", "customer", default=inv.get("customer_id") or "Unknown"),
                "amount": round(total, 2),
                "pending_amount": round(max(outstanding, 0), 2),
                "status": inv.get("status") or inv.get("payment_status") or "Pending",
                "date": _copilot_date_text(inv, "invoice_date", "created_at", "date"),
            })
            if len(rows) >= limit:
                break
        return rows


    async def _agent_copilot_chat(question: str, user: dict, history):
        """Role-scoped Copilot for Agents. Identity and all filters come from auth."""
        if user.get("role") != "agent":
            raise HTTPException(status_code=403, detail="Agent access required")

        agent_id = str((user.get("meta") or {}).get("agent_id") or "").strip()
        agent_name = await _agent_name(user)
        if not agent_id and agent_name:
            agent_id = await _resolve_agent_id(name=agent_name)
        if not agent_id and not agent_name:
            raise HTTPException(status_code=404, detail="No agent profile is linked to your account.")

        # Build ownership predicates once. The frontend never supplies agent_id.
        def ownership(fields=("agent_id", "assigned_agent_id", "agent", "assigned_agent", "agent_name")):
            clauses = []
            if agent_id:
                clauses.extend([{f: agent_id} for f in fields if f.endswith("_id") or f == "agent_id"])
            if agent_name:
                clauses.extend([{f: agent_name} for f in fields if not f.endswith("_id")])
            return {"$or": clauses} if clauses else {"_id": {"$exists": False}}

        async def docs(collection, fields=None, limit=100):
            projection = {"_id": 0} if fields is None else {"_id": 0, **{f: 1 for f in fields}}
            return await db[collection].find(ownership()).sort("created_at", -1).to_list(limit)

        def clean_status(value):
            return _copilot_norm_status(value)

        q0 = re.sub(r"\s+", " ", question).strip().lower()
        prev = ""
        for item in reversed(history or []):
            if isinstance(item, dict) and item.get("role") == "user":
                prev = str(item.get("content") or "").strip().lower()
                break
        markers = ("that", "those", "it", "them", "same", "related", "above", "previous", "the pending", "more details")
        q = f"{prev} {q0}".strip() if prev and any(re.search(rf"\b{re.escape(m)}\b", q0) for m in markers) else q0

        def out(payload, reply, route=None, topic=None):
            return {"reply": reply, "result": payload, "details_route": route, "context": {"topic": topic} if topic else {}, "disclaimer": "AI uses only data assigned to your authenticated Agent account."}

        # Security: explicit requests for other agents are never widened.
        if any(x in q for x in ("all agents", "other agents", "another agent", "every agent", "all consultants")):
            return out({"type": "empty"}, "You only have access to your assigned Agent data.", "/agent/dashboard", "restricted")

        if (
            "what should" in q
            or "work on" in q
            or "priority" in q
            or "attention" in q
            or "follow up" in q
            or "follow-up" in q
            or "overdue" in q
        ) and not any(x in q for x in ("appointment", "meeting", "booking", "task", "customer", "lead")):
            leads = await docs("erp_leads", limit=2000)
            appointments = await docs("erp_appointments", limit=2000)
            tasks = await docs("erp_tasks", limit=2000)
            today = _ist_now().date()
            today_s = today.isoformat()
            def date_value(row, *keys):
                return _copilot_date_text(row, *keys)
            followups = [x for x in leads if date_value(x, "follow_up_date", "next_follow_up", "followup_date") == today_s]
            overdue_followups = [x for x in leads if date_value(x, "follow_up_date", "next_follow_up", "followup_date") and date_value(x, "follow_up_date", "next_follow_up", "followup_date") < today_s and clean_status(x.get("status")) not in ("converted", "closed", "lost")]
            todays_appts = [x for x in appointments if date_value(x, "appointment_date", "date", "scheduled_date", "created_at") == today_s]
            overdue_tasks = [x for x in tasks if clean_status(x.get("status")) not in ("completed", "done", "closed") and date_value(x, "due_date", "deadline", "date") and date_value(x, "due_date", "deadline", "date") < today_s]
            priority = [x for x in leads if clean_status(x.get("priority")) in ("high", "urgent", "critical")]
            rows = []
            for x in sorted(priority + followups + overdue_followups, key=lambda r: str(r.get("follow_up_date") or r.get("next_follow_up") or ""))[:10]:
                rows.append({"id": x.get("id"), "lead": _copilot_name(x, "name", "lead_name", "customer_name", default=x.get("id")), "status": x.get("status") or "", "priority": x.get("priority") or "", "follow_up": date_value(x, "follow_up_date", "next_follow_up", "followup_date")})
            reply = f"Today: {len(followups)} follow-up(s), {len(todays_appts)} appointment(s), {len(overdue_tasks)} overdue task(s), and {len(priority)} high-priority lead(s)."
            return out({"type": "table", "columns": ["Lead", "Status", "Priority", "Follow Up"], "rows": rows}, reply, "/agent/leads", "agent_today_summary")

        if "lead" in q:
            rows0 = await docs("erp_leads", limit=2000)
            if "converted" in q:
                rows0 = [x for x in rows0 if clean_status(x.get("status")) in ("converted", "won")]
            elif "qualified" in q:
                rows0 = [x for x in rows0 if "qualif" in clean_status(x.get("status"))]
            elif "high" in q or "priority" in q:
                rows0 = [x for x in rows0 if clean_status(x.get("priority")) in ("high", "urgent", "critical")]
            elif "new" in q:
                rows0 = [x for x in rows0 if clean_status(x.get("status")) in ("new", "lead")]
            rows = [{"id": x.get("id"), "name": _copilot_name(x, "name", "lead_name", "customer_name", default=x.get("id")), "service": _copilot_name(x, "service_name", "service", default=""), "status": x.get("status") or "", "priority": x.get("priority") or "", "assigned_date": _copilot_date_text(x, "assigned_date", "created_at"), "last_contact": _copilot_date_text(x, "last_contact", "last_contacted_at"), "follow_up": _copilot_date_text(x, "follow_up_date", "next_follow_up", "followup_date")} for x in rows0[:50]]
            if "how many" in q or "count" in q:
                return out({"type": "stat", "label": "My leads", "value": len(rows0)}, f"You have {len(rows0)} assigned lead(s).", "/agent/leads", "agent_leads")
            return out({"type": "table", "columns": ["Name", "Service", "Status", "Priority", "Assigned Date", "Last Contact", "Follow Up"], "rows": rows}, f"I found {len(rows0)} assigned lead(s).", "/agent/leads", "agent_leads")

        if "appointment" in q or "meeting" in q:
            rows0 = await docs("erp_appointments", limit=2000)
            today = _ist_now().date().isoformat()
            if "today" in q:
                rows0 = [x for x in rows0 if _copilot_date_text(x, "appointment_date", "date", "scheduled_date", "created_at") == today]
            elif "tomorrow" in q:
                rows0 = [x for x in rows0 if _copilot_date_text(x, "appointment_date", "date", "scheduled_date", "created_at") == (_ist_now().date() + timedelta(days=1)).isoformat()]
            rows = [{"id": x.get("id"), "customer": _copilot_name(x, "customer_name", "customer", default=x.get("customer_id") or "Unknown"), "service": _copilot_name(x, "service_name", "service", default=""), "date": _copilot_date_text(x, "appointment_date", "date", "scheduled_date", "created_at"), "time": x.get("time") or x.get("appointment_time") or "", "status": x.get("status") or "", "location": x.get("location") or x.get("details") or ""} for x in rows0[:50]]
            return out({"type": "table", "columns": ["Customer", "Service", "Date", "Time", "Status", "Location"], "rows": rows}, f"I found {len(rows0)} appointment(s) matching your request.", "/agent/appointments", "agent_appointments")

        if "task" in q:
            rows0 = await docs("erp_tasks", limit=2000)
            if "overdue" in q:
                today = _ist_now().date().isoformat()
                rows0 = [x for x in rows0 if clean_status(x.get("status")) not in ("completed", "done", "closed") and _copilot_date_text(x, "due_date", "deadline", "date") and _copilot_date_text(x, "due_date", "deadline", "date") < today]
            elif "completed" in q:
                rows0 = [x for x in rows0 if clean_status(x.get("status")) in ("completed", "done", "closed")]
            elif "pending" in q or "high" in q:
                rows0 = [x for x in rows0 if clean_status(x.get("status")) not in ("completed", "done", "closed")]
                if "high" in q:
                    rows0 = [x for x in rows0 if clean_status(x.get("priority")) in ("high", "urgent", "critical")]
            rows = [{"id": x.get("id"), "task": _copilot_name(x, "title", "name", default=x.get("id")), "priority": x.get("priority") or "", "due_date": _copilot_date_text(x, "due_date", "deadline", "date"), "status": x.get("status") or "", "customer": _copilot_name(x, "customer_name", "customer", default=x.get("customer_id") or "")} for x in rows0[:50]]
            return out({"type": "table", "columns": ["Task", "Priority", "Due Date", "Status", "Customer"], "rows": rows}, f"I found {len(rows0)} matching task(s).", "/agent/tasks", "agent_tasks")

        if "customer" in q:
            rows0 = await docs("erp_customers", limit=2000)
            if "pending payment" in q or "pending payments" in q:
                billing = await _billing_for_customers([str(x.get("id")) for x in rows0 if x.get("id")])
                rows0 = [x for x in rows0 if float((billing.get(str(x.get("id"))) or {}).get("outstanding") or 0) > 0]
                rows = [{"id": x.get("id"), "customer": _copilot_name(x, "business_name", "name", "owner", default=x.get("id")), "pending": round(float((billing.get(str(x.get("id"))) or {}).get("outstanding") or 0),2)} for x in rows0[:50]]
                return out({"type": "table", "columns": ["Customer", "Pending"], "rows": rows}, f"I found {len(rows0)} customer(s) with pending payments.", "/agent/customers", "agent_customers")
            rows = [{"id": x.get("id"), "customer": _copilot_name(x, "business_name", "name", "owner", default=x.get("id")), "status": x.get("status") or "", "service": _copilot_name(x, "service_name", "service", default="")} for x in rows0[:50]]
            if "how many" in q or "count" in q:
                return out({"type": "stat", "label": "My customers", "value": len(rows0)}, f"You have {len(rows0)} assigned customer(s).", "/agent/customers", "agent_customers")
            return out({"type": "table", "columns": ["Customer", "Status", "Service"], "rows": rows}, f"I found {len(rows0)} assigned customer(s).", "/agent/customers", "agent_customers")

        if "booking" in q:
            rows0 = await docs("erp_bookings", limit=2000)
            if "today" in q:
                today = _ist_now().date().isoformat()
                rows0 = [x for x in rows0 if _copilot_date_text(x, "booking_date", "date", "created_at") == today]
            elif "pending" in q:
                rows0 = [x for x in rows0 if clean_status(x.get("status")) in ("pending", "processing")]
            rows = [{"id": x.get("id"), "booking": x.get("booking_id") or x.get("id"), "customer": _copilot_name(x, "customer_name", "customer", default=x.get("customer_id") or "Unknown"), "service": _copilot_name(x, "service_name", "service", default=x.get("service_id") or "Unknown"), "status": x.get("status") or "", "date": _copilot_date_text(x, "booking_date", "date", "created_at")} for x in rows0[:50]]
            return out({"type": "table", "columns": ["Booking", "Customer", "Service", "Status", "Date"], "rows": rows}, f"I found {len(rows0)} assigned booking(s).", "/agent/bookings", "agent_bookings")

        if "commission" in q or "earned" in q:
            rows0 = await docs("erp_commissions", limit=2000)
            total = round(sum(float(x.get("amount") or x.get("commission_amount") or 0) for x in rows0), 2)
            pending = round(sum(float(x.get("amount") or x.get("commission_amount") or 0) for x in rows0 if clean_status(x.get("status")) in ("pending", "unpaid", "due")), 2)
            return out({"type": "revenue", "label": "My commission", "value": total, "transactions": len(rows0), "breakdown": [{"name": "Pending", "amount": pending}, {"name": "Paid/recorded", "amount": max(total-pending,0)}]}, f"Your available commission total is ₹{total:,.2f}; ₹{pending:,.2f} is marked pending.", "/agent/commission", "agent_commission")

        if "performance" in q or "performing" in q or "conversion" in q:
            leads = await docs("erp_leads", limit=5000)
            bookings = await docs("erp_bookings", limit=5000)
            commissions = await docs("erp_commissions", limit=5000)
            converted = sum(1 for x in leads if clean_status(x.get("status")) in ("converted", "won"))
            rate = round((converted / len(leads) * 100), 2) if leads else 0
            revenue = round(sum(float(x.get("total") or x.get("amount") or 0) for x in bookings if clean_status(x.get("status")) in ("completed", "confirmed", "paid")), 2)
            commission = round(sum(float(x.get("amount") or x.get("commission_amount") or 0) for x in commissions), 2)
            rows = [{"name":"Leads","value":len(leads)},{"name":"Converted","value":converted},{"name":"Conversion Rate %","value":rate},{"name":"Completed/Confirmed Revenue","value":revenue},{"name":"Commission","value":commission}]
            return out({"type":"chart","label":"My performance","rows":rows}, f"You have {len(leads)} leads, {converted} conversions ({rate:.2f}%), ₹{revenue:,.2f} attributed booking value, and ₹{commission:,.2f} commission.", "/agent/performance", "agent_performance")

        return out({"type":"empty", "suggestions": AGENT_COPILOT_SUGGESTIONS}, "I can help with your assigned leads, follow-ups, appointments, tasks, customers, bookings, commission and performance.")

    @router.post("/copilot/chat")
    async def copilot_chat(body: dict = Body(...), user: dict = Depends(get_current_user)):
        question = str(body.get("message") or "").strip()
        if user.get("role") == "agent":
            if not question:
                raise HTTPException(status_code=400, detail="Please enter a question.")
            if len(question) > 1000:
                raise HTTPException(status_code=400, detail="Question is too long.")
            history = body.get("history") if isinstance(body.get("history"), list) else []
            return _ok(await _agent_copilot_chat(question, user, history), message="Agent Copilot response")
        if user.get("role") not in ("admin", "super_admin", "superadmin"):
            raise HTTPException(status_code=403, detail="Admin access required")
        if not question:
            raise HTTPException(status_code=400, detail="Please enter a question.")
        if len(question) > 1000:
            raise HTTPException(status_code=400, detail="Question is too long.")

        # Conversation history is context only. It is never executed as a query.
        # IMPORTANT: do not concatenate every previous question into the current
        # query. Doing that makes an unrelated new question inherit old intents
        # (for example, a revenue question can keep affecting a later employee
        # question). Only use the immediately preceding user turn when the new
        # question is clearly a conversational follow-up.
        history = body.get("history") if isinstance(body.get("history"), list) else []
        previous_user_question = ""
        for item in reversed(history):
            if isinstance(item, dict) and item.get("role") == "user":
                previous_user_question = str(item.get("content") or "").strip()
                break

        current_q = re.sub(r"\s+", " ", question).strip().lower()
        follow_up_markers = (
            "that", "those", "it", "them", "same", "related", "above",
            "previous", "the pending", "the total", "more details",
            "breakdown", "what about", "how much of",
        )
        is_follow_up = any(
            (re.search(rf"\b{re.escape(marker)}\b", current_q) is not None)
            for marker in follow_up_markers
        )
        if is_follow_up and previous_user_question:
            q = re.sub(
                r"\s+",
                " ",
                f"{previous_user_question} {question}",
            ).strip().lower()
        else:
            q = current_q

        def result(payload, reply, details_route=None, context=None):
            return {
                "reply": reply,
                "result": payload,
                "details_route": details_route,
                "context": context or {},
                "disclaimer": "AI can make mistakes. Please verify important data.",
            }

        # Service revenue is a more specific intent than generic revenue.
        if "service" in q and "revenue" in q and any(x in q for x in ("top", "most", "highest")):
            _, rev_rows = await _copilot_revenue("year")
            totals = {}
            for r in rev_rows:
                name = r.get("service") or "Uncategorised"
                totals[name] = totals.get(name, 0) + float(r.get("amount") or 0)
            top = sorted([{"name": k, "revenue": round(v, 2)} for k, v in totals.items()], key=lambda x: x["revenue"], reverse=True)[:5]
            return result({"type": "chart", "label": "Top 5 services by revenue", "rows": top},
                          "Here are the top 5 services by revenue for the current year.", "/admin/revenue", {"topic": "top_services_revenue"})

        # Revenue is deliberately routed through the existing revenue calculation
        # helper so the Copilot and Revenue module share one source of truth.
        if any(x in q for x in ("revenue", "income collected", "sales")):
            if "today" in q:
                period = "today"
                label = "today"
            elif any(x in q for x in ("this year", "yearly", "annual")):
                period = "year"
                label = "this year"
            else:
                period = "month"
                label = "this month"
            total, rev_rows = await _copilot_revenue(period)
            pending = 0.0
            if "pending" in q or "outstanding" in q:
                customers = await db["erp_customers"].find({}, {"_id": 0, "id": 1}).to_list(10000)
                billing = await _billing_for_customers([str(c["id"]) for c in customers if c.get("id")])
                pending = round(sum(max(float(v.get("outstanding") or 0), 0) for v in billing.values()), 2)
            service_totals = {}
            for row in rev_rows:
                name = row.get("service") or "Uncategorised"
                service_totals[name] = service_totals.get(name, 0) + float(row.get("amount") or 0)
            breakdown = sorted(
                [{"name": k, "amount": round(v, 2)} for k, v in service_totals.items()],
                key=lambda x: x["amount"], reverse=True
            )[:5]
            if "pending" in q or "outstanding" in q:
                reply = f"₹{pending:,.2f} is currently pending/outstanding."
                return result({"type": "stat", "label": "Pending revenue", "value": pending, "currency": "INR"},
                              reply, "/admin/revenue", {"topic": "pending_revenue", "period": period})
            reply = f"{label.capitalize()} revenue is ₹{total:,.2f}."
            return result({"type": "revenue", "label": f"Revenue {label}", "value": total, "currency": "INR",
                           "transactions": len(rev_rows), "breakdown": breakdown},
                          reply, "/admin/revenue", {"topic": "revenue", "period": period})

        # Explicit pending invoice intent should win over generic payment intent.
        if "invoice" in q and any(x in q for x in ("pending", "unpaid", "due", "outstanding", "those invoices")):
            rows = await _copilot_pending_invoice_rows()
            reply = f"I found {len(rows)} pending invoice record(s) in the available result set." if rows else "I couldn't find any pending invoices."
            return result({"type": "table", "columns": ["Invoice", "Customer", "Amount", "Pending", "Status", "Date"], "rows": rows},
                          reply, "/admin/invoices", {"topic": "pending_invoices"})

        # Simple entity counts / lists.
        if "service" in q and ("how many" in q or "count" in q or "total" in q):
            services = await db["erp_services"].find({}, {"_id": 0, "id": 1, "name": 1, "title": 1, "status": 1}).to_list(5000)
            services = [s for s in services if is_valid_service_record(s)]
            active = [s for s in services if is_active_service_record(s)]
            value = len(active) if "active" in q else len(services)
            return result({"type": "stat", "label": "Active services" if "active" in q else "Services", "value": value},
                          f"We have {value} {'active ' if 'active' in q else ''}service(s).", "/admin/services", {"topic": "services_count"})

        if "employee" in q and ("show" in q or "list" in q or "who" in q):
            query = {"status": {"$regex": r"^active$", "$options": "i"}} if "active" in q else {}
            rows0 = await _copilot_docs("erp_employees", query, {"_id": 0, "id": 1, "name": 1, "designation": 1, "department": 1, "email": 1, "status": 1}, 1000)
            rows = [{"id": x.get("id"), "name": _copilot_name(x, "name", default=x.get("id")), "designation": x.get("designation") or "", "department": x.get("department") or "", "email": x.get("email") or "", "status": x.get("status") or ""} for x in rows0[:50]]
            return result({"type": "table", "columns": ["Name", "Designation", "Department", "Email", "Status"], "rows": rows},
                          f"Here are {len(rows0)} matching employee(s).", "/admin/employees", {"topic": "employees", "count": len(rows0)})

        if "agent" in q and ("how many" in q or "count" in q or "total" in q):
            agents = await db["erp_agents"].find({}, {"_id": 0, "id": 1, "name": 1, "status": 1}).to_list(5000)
            if "active" in q:
                agents = [a for a in agents if _copilot_norm_status(a.get("status") or "active") == "active"]
            return result({"type": "stat", "label": "Active agents" if "active" in q else "Agents", "value": len(agents)},
                          f"There are {len(agents)} {'active ' if 'active' in q else ''}agent(s).", "/admin/agents", {"topic": "agents_count"})

        if "customer" in q and ("how many" in q or "count" in q or "total" in q):
            customers = await db["erp_customers"].find({}, {"_id": 0, "id": 1, "status": 1}).to_list(10000)
            if "active" in q:
                customers = [c for c in customers if _copilot_norm_status(c.get("status") or "active") == "active"]
            return result({"type": "stat", "label": "Active customers" if "active" in q else "Customers", "value": len(customers)},
                          f"There are {len(customers)} {'active ' if 'active' in q else ''}customer(s).", "/admin/customers", {"topic": "customers_count"})

        if "booking" in q and any(x in q for x in ("pending", "processing", "today")):
            if "today" in q:
                today = _ist_now().date().isoformat()
                rows0 = await db["erp_bookings"].find({}, {"_id": 0}).sort("created_at", -1).to_list(2000)
                rows0 = [b for b in rows0 if _copilot_date_text(b, "booking_date", "date", "created_at") == today]
                label = "Today's bookings"
            else:
                target = "Processing" if "processing" in q else "Pending"
                rows0 = await db["erp_bookings"].find({"status": target}, {"_id": 0}).sort("created_at", -1).to_list(2000)
                label = f"{target} bookings"
            rows = [{"id": b.get("id"), "booking": b.get("booking_id") or b.get("id"), "customer": _copilot_name(b, "customer_name", "customer", default=b.get("customer_id") or "Unknown"),
                     "service": _copilot_name(b, "service_name", "service", default=b.get("service_id") or "Unknown"),
                     "status": b.get("status") or "", "date": _copilot_date_text(b, "booking_date", "date", "created_at")} for b in rows0[:50]]
            if "how many" in q or "count" in q:
                return result({"type": "stat", "label": label, "value": len(rows0)}, f"There are {len(rows0)} {label.lower()}.", "/admin/bookings", {"topic": "bookings", "status": label})
            return result({"type": "table", "columns": ["Booking", "Customer", "Service", "Status", "Date"], "rows": rows},
                          f"I found {len(rows0)} {label.lower()}.", "/admin/bookings", {"topic": "bookings", "status": label})

        if "task" in q and ("completed" in q or "assigned" in q or "most" in q):
            tasks = await db["erp_tasks"].find({}, {"_id": 0}).to_list(10000)
            if "completed" in q:
                tasks = [t for t in tasks if _copilot_norm_status(t.get("status")) == "completed"]
                rows = [{"id": t.get("id"), "task": _copilot_name(t, "title", "name", default=t.get("id")),
                         "employee": _copilot_name(t, "employee_name", default=t.get("employee_id") or "Unassigned"),
                         "status": t.get("status") or "", "priority": t.get("priority") or ""} for t in tasks[:50]]
                return result({"type": "table", "columns": ["Task", "Employee", "Status", "Priority"], "rows": rows},
                              f"I found {len(tasks)} completed task(s).", "/admin/task-board", {"topic": "completed_tasks"})
            counts = {}
            for t in tasks:
                employee = _copilot_name(t, "employee_name", default=t.get("employee_id") or "Unassigned")
                counts[employee] = counts.get(employee, 0) + 1
            top = sorted([{"name": k, "tasks": v} for k, v in counts.items()], key=lambda x: x["tasks"], reverse=True)[:5]
            return result({"type": "chart", "label": "Tasks by employee", "rows": top},
                          "Here are the employees with the most assigned tasks.", "/admin/task-board", {"topic": "tasks_by_employee"})

        if "service" in q and "booking" in q and "most" in q:
            bookings = await db["erp_bookings"].find({}, {"_id": 0, "service_id": 1, "service_name": 1, "service": 1}).to_list(10000)
            counts = {}
            for b in bookings:
                name = _copilot_name(b, "service_name", "service", default=b.get("service_id") or "Unknown")
                counts[name] = counts.get(name, 0) + 1
            top = sorted([{"name": k, "bookings": v} for k, v in counts.items()], key=lambda x: x["bookings"], reverse=True)[:5]
            return result({"type": "chart", "label": "Top services by bookings", "rows": top},
                          "Here are the top services by booking count.", "/admin/services", {"topic": "top_services_bookings"})

        if "pending" in q and ("payment" in q or "customer" in q):
            customers = await db["erp_customers"].find({}, {"_id": 0}).to_list(10000)
            billing = await _billing_for_customers([str(c.get("id")) for c in customers if c.get("id")])
            rows = []
            for c in customers:
                state = billing.get(str(c.get("id"))) or {}
                outstanding = round(max(float(state.get("outstanding") or 0), 0), 2)
                if outstanding > 0:
                    rows.append({"id": c.get("id"), "customer": _copilot_name(c, "business_name", "name", "owner", default=c.get("id")),
                                 "pending": outstanding, "billed": round(float(state.get("total_billed") or 0), 2),
                                 "paid": round(float(state.get("total_paid") or 0), 2)})
            rows.sort(key=lambda x: x["pending"], reverse=True)
            rows = rows[:50]
            return result({"type": "table", "columns": ["Customer", "Billed", "Paid", "Pending"], "rows": rows},
                          f"I found {len(rows)} customer(s) with pending payments in the displayed result set.", "/admin/payments",
                          {"topic": "pending_payments", "count": len(rows)})

        # Follow-up: "show those invoices" / "show those customers" uses the
        # preceding conversational topic supplied by the frontend.
        if any(x in q for x in ("show those invoices", "show the invoices", "show those")) and "invoice" in q:
            rows = await _copilot_pending_invoice_rows()
            return result({"type": "table", "columns": ["Invoice", "Customer", "Amount", "Pending", "Status", "Date"], "rows": rows},
                          f"Here are the related pending invoices ({len(rows)} shown).", "/admin/invoices", {"topic": "pending_invoices"})

        return result({"type": "empty", "suggestions": [
            "How many services do we have?",
            "Show all active employees.",
            "How many active agents are there?",
            "Show total customers.",
            "What is our revenue this month?",
            "Show pending bookings.",
            "Show today's bookings.",
            "Which services have the most bookings?",
            "Show pending invoices.",
            "Show completed tasks.",
            "Which employees have the most assigned tasks?",
            "Show customers with pending payments.",
            "Show the top 5 services by revenue.",
        ]}, "I can answer questions about NTAXCO's live ERP data, including customers, employees, agents, services, bookings, tasks, invoices, payments and revenue.")

    @router.get("/admin/search")
    async def admin_global_search(q: str = Query(..., min_length=1, max_length=100), user: dict = Depends(get_current_user)):
        """Search real NTAXCO ERP records for the authenticated Admin navbar.

        The endpoint intentionally returns only display/navigation fields. It uses the
        existing ERP collections and IDs and never returns credentials, document bytes,
        OTPs, tokens, or password fields.
        """
        if user.get("role") not in ("admin", "super_admin", "superadmin"):
            raise HTTPException(status_code=403, detail="Admin access required")
        term = str(q or "").strip()
        if len(term) < 2:
            return {"query": term, "total": 0, "results": []}

        pattern = re.compile(re.escape(term), re.IGNORECASE)
        limit = 5
        projection = {"_id": 0}

        async def find_many(collection, fields):
            clauses = [{field: pattern} for field in fields]
            safe_projection = {field: 1 for field in fields}
            safe_projection["_id"] = 0
            try:
                return await db[collection].find({"$or": clauses}, safe_projection).limit(limit).to_list(limit)
            except Exception:
                # A single optional collection/search field must never break the whole
                # global search. The collection is simply treated as having no matches.
                logger.exception("Global search failed for collection %s", collection)
                return []

        # Search the canonical collections in parallel. This keeps one frontend request
        # while avoiding serial waits across the ERP's independent MongoDB collections.
        raw = await asyncio.gather(
            find_many("erp_customers", ["id", "name", "business_name", "owner", "mobile", "email", "pan", "gstin", "gst_number", "status"]),
            find_many("erp_employees", ["id", "name", "employee_id", "email", "mobile", "department", "designation", "status"]),
            find_many("erp_agents", ["id", "name", "agent_id", "email", "mobile", "phone", "status"]),
            find_many("erp_bookings", ["id", "booking_id", "customer_id", "customer_name", "service_id", "service_name", "mobile", "status", "booking_date", "created_at"]),
            find_many("erp_services", ["id", "name", "title", "service_id", "category", "description", "status"]),
            find_many("erp_projects", ["id", "project_id", "name", "title", "customer_id", "customer_name", "employee_id", "employee_name", "status"]),
            find_many("erp_tasks", ["id", "task_id", "title", "customer_id", "customer_name", "employee_id", "employee_name", "service_id", "service_name", "status", "priority"]),
            find_many("erp_invoices", ["id", "invoice_no", "invoice_number", "customer_id", "customer_name", "status", "total", "amount", "invoice_date", "created_at"]),
            find_many("erp_payments", ["id", "payment_id", "invoice_id", "invoice_no", "invoice_number", "customer_id", "customer_name", "reference", "payment_reference", "status", "amount", "payment_date", "created_at"]),
            find_many("erp_documents", ["id", "document_id", "name", "title", "file_name", "customer_id", "customer_name", "doc_type", "document_type", "uploaded_at", "created_at"]),
            find_many("erp_gst", ["id", "gst_id", "customer_id", "customer_name", "gstin", "gst_number", "status"]),
            find_many("erp_itr", ["id", "itr_id", "customer_id", "customer_name", "pan", "assessment_year", "return_status", "status"]),
            find_many("erp_tds", ["id", "tds_id", "customer_id", "customer_name", "tan", "status"]),
            find_many("erp_roc", ["id", "roc_id", "company_name", "cin", "status"]),
        )

        customers, employees, agents, bookings, services, projects, tasks, invoices, payments, documents, gst, itr, tds, roc = raw

        # Resolve common foreign keys only for fields needed by the result display.
        customer_ids = {str(x.get("customer_id")) for rows in raw[3:10] for x in rows if x.get("customer_id")}
        customer_ids.update(str(x.get("customer_id")) for rows in (gst, itr, tds) for x in rows if x.get("customer_id"))
        service_ids = {str(x.get("service_id")) for rows in (bookings, tasks) for x in rows if x.get("service_id")}
        employee_ids = {str(x.get("employee_id")) for rows in (projects, tasks) for x in rows if x.get("employee_id")}
        invoice_ids = {str(x.get("invoice_id")) for x in payments if x.get("invoice_id")}

        async def by_ids(collection, ids):
            if not ids:
                return []
            relation_fields = {
                "erp_customers": ["id", "name", "business_name", "owner", "mobile", "email"],
                "erp_services": ["id", "name", "title"],
                "erp_employees": ["id", "name"],
                "erp_invoices": ["id", "invoice_no", "invoice_number"],
            }
            safe_projection = {field: 1 for field in relation_fields.get(collection, ["id", "name"])}
            safe_projection["_id"] = 0
            try:
                return await db[collection].find({"id": {"$in": list(ids)}}, safe_projection).to_list(min(len(ids), 500))
            except Exception:
                return []

        related_customers, related_services, related_employees, related_invoices = await asyncio.gather(
            by_ids("erp_customers", customer_ids),
            by_ids("erp_services", service_ids),
            by_ids("erp_employees", employee_ids),
            by_ids("erp_invoices", invoice_ids),
        )
        customer_map = {str(x.get("id")): x for x in related_customers if x.get("id")}
        service_map = {str(x.get("id")): x for x in related_services if x.get("id")}
        employee_map = {str(x.get("id")): x for x in related_employees if x.get("id")}
        invoice_map = {str(x.get("id")): x for x in related_invoices if x.get("id")}

        def val(obj, *keys, default=""):
            for key in keys:
                value = obj.get(key)
                if value not in (None, ""):
                    return value
            return default

        def date_text(value):
            if value in (None, ""):
                return ""
            if hasattr(value, "isoformat"):
                return value.isoformat()[:10]
            return str(value)[:30]

        def result(type_name, title, subtitle="", meta="", status="", route="", record_id=None):
            rid = str(record_id or "")
            return {
                "type": type_name,
                "id": rid,
                "title": str(title or rid or "Untitled"),
                "subtitle": str(subtitle or ""),
                "meta": str(meta or ""),
                "status": str(status or ""),
                "route": route,
            }

        groups = []

        customer_results = [
            result("customer", val(x, "name", "business_name", "owner", default=x.get("id")),
                   f"Customer ID: {x.get('id', '')}", val(x, "mobile", "email"), val(x, "status", "filing_status", default=""),
                   f"/admin/customers?open={x.get('id', '')}", x.get("id"))
            for x in customers
        ]
        if customer_results: groups.append({"type": "customer", "label": "Customers", "results": customer_results})

        employee_results = [
            result("employee", val(x, "name", default=x.get("id")),
                   f"Employee ID: {val(x, 'employee_id', 'id')}", val(x, "designation", "department", "email", "mobile"),
                   val(x, "status", default=""), f"/admin/employees?open={x.get('id', '')}", x.get("id"))
            for x in employees
        ]
        if employee_results: groups.append({"type": "employee", "label": "Employees", "results": employee_results})

        agent_results = [
            result("agent", val(x, "name", default=x.get("id")),
                   f"Agent ID: {val(x, 'agent_id', 'id')}", val(x, "mobile", "phone", "email"),
                   val(x, "status", default=""), f"/admin/agents?open={x.get('id', '')}", x.get("id"))
            for x in agents
        ]
        if agent_results: groups.append({"type": "agent", "label": "Agents", "results": agent_results})

        booking_results = []
        for x in bookings:
            c = customer_map.get(str(x.get("customer_id")), {})
            s = service_map.get(str(x.get("service_id")), {})
            cid = val(x, "customer_id")
            customer_name = val(x, "customer_name", default=val(c, "name", "business_name", "owner", default=cid))
            service_name = val(x, "service_name", default=val(s, "name", "title", default=val(x, "service_id")))
            booking_results.append(result("booking", val(x, "booking_id", "id"),
                f"{customer_name} • {service_name}", val(x, "mobile", default=val(c, "mobile", "email")),
                val(x, "status", default=""), f"/admin/bookings?open={x.get('id', '')}", x.get("id")))
        if booking_results: groups.append({"type": "booking", "label": "Bookings", "results": booking_results})

        service_results = [
            result("service", val(x, "name", "title", default=x.get("id")),
                   f"Service ID: {val(x, 'service_id', 'id')}", val(x, "category", "description"),
                   val(x, "status", default=""), f"/admin/services?open={x.get('id', '')}", x.get("id"))
            for x in services
        ]
        if service_results: groups.append({"type": "service", "label": "Services", "results": service_results})

        project_results = []
        for x in projects:
            c = customer_map.get(str(x.get("customer_id")), {})
            e = employee_map.get(str(x.get("employee_id")), {})
            customer_name = val(x, "customer_name", default=val(c, "name", "business_name", "owner", default=val(x, "customer_id")))
            employee_name = val(x, "employee_name", default=val(e, "name", default=val(x, "employee_id")))
            project_results.append(result("project", val(x, "name", "title", "project_id", default=x.get("id")),
                f"Project ID: {val(x, 'project_id', 'id')}", f"{customer_name} • {employee_name}",
                val(x, "status", default=""), f"/admin/projects?open={x.get('id', '')}", x.get("id")))
        if project_results: groups.append({"type": "project", "label": "Projects", "results": project_results})

        task_results = []
        for x in tasks:
            c = customer_map.get(str(x.get("customer_id")), {})
            e = employee_map.get(str(x.get("employee_id")), {})
            s = service_map.get(str(x.get("service_id")), {})
            customer_name = val(x, "customer_name", default=val(c, "name", "business_name", "owner", default=val(x, "customer_id")))
            employee_name = val(x, "employee_name", default=val(e, "name", default=val(x, "employee_id")))
            service_name = val(x, "service_name", default=val(s, "name", "title", default=val(x, "service_id")))
            task_results.append(result("task", val(x, "title", default=x.get("id")),
                f"Task ID: {val(x, 'task_id', 'id')}", f"{employee_name} • {customer_name} • {service_name}",
                f"{val(x, 'status', default='')} {('• ' + str(x.get('priority'))) if x.get('priority') else ''}".strip(),
                f"/admin/task-board?task={x.get('id', '')}", x.get("id")))
        if task_results: groups.append({"type": "task", "label": "Tasks", "results": task_results})

        invoice_results = [
            result("invoice", val(x, "invoice_no", "invoice_number", "id"),
                   f"Customer: {val(x, 'customer_name', default=val(customer_map.get(str(x.get('customer_id')), {}), 'name', 'business_name', 'owner', default=val(x, 'customer_id')))}",
                   f"Amount: {val(x, 'total', 'amount', default='')} • {date_text(val(x, 'invoice_date', 'created_at'))}",
                   val(x, "status", default=""), f"/admin/invoices?open={x.get('id', '')}", x.get("id"))
            for x in invoices
        ]
        if invoice_results: groups.append({"type": "invoice", "label": "Invoices", "results": invoice_results})

        payment_results = [
            result("payment", val(x, "payment_id", "id"),
                   f"Customer: {val(x, 'customer_name', default=val(customer_map.get(str(x.get('customer_id')), {}), 'name', 'business_name', 'owner', default=val(x, 'customer_id')))}",
                   f"Amount: {val(x, 'amount', default='')} • Ref: {val(x, 'reference', 'payment_reference', default=val(x, 'invoice_no', 'invoice_number', default=val(invoice_map.get(str(x.get('invoice_id')), {}), 'invoice_no', 'invoice_number')))}",
                   val(x, "status", default=""), f"/admin/payments?open={x.get('id', '')}", x.get("id"))
            for x in payments
        ]
        if payment_results: groups.append({"type": "payment", "label": "Payments", "results": payment_results})

        document_results = [
            result("document", val(x, "name", "title", "file_name", "document_id", default=x.get("id")),
                   f"Customer: {val(x, 'customer_name', default=val(customer_map.get(str(x.get('customer_id')), {}), 'name', 'business_name', 'owner', default=val(x, 'customer_id')))}",
                   f"{val(x, 'doc_type', 'document_type', default='Document')} • {date_text(val(x, 'uploaded_at', 'created_at'))}",
                   "", f"/admin/documents?open={x.get('id', '')}", x.get("id"))
            for x in documents
        ]
        if document_results: groups.append({"type": "document", "label": "Documents", "results": document_results})

        tax_specs = [
            ("gst", "GST", gst, ("GST ID", lambda x: val(x, "gst_id", "id")), lambda x: f"GSTIN: {val(x, 'gstin', 'gst_number')}"),
            ("income_tax", "Income Tax", itr, ("ITR ID", lambda x: val(x, "itr_id", "id")), lambda x: f"PAN: {val(x, 'pan')} • AY: {val(x, 'assessment_year')}"),
            ("tds", "TDS", tds, ("TDS ID", lambda x: val(x, "tds_id", "id")), lambda x: f"TAN: {val(x, 'tan')}"),
            ("roc", "ROC", roc, ("ROC ID", lambda x: val(x, "roc_id", "id")), lambda x: f"CIN: {val(x, 'cin')}"),
        ]
        for type_name, label, rows, id_spec, meta_fn in tax_specs:
            id_label, id_fn = id_spec
            tax_results = [
                result(type_name, val(x, "customer_name", "company_name", default=x.get("id")),
                       f"{id_label}: {id_fn(x)}", meta_fn(x), val(x, "return_status", "status", default=""),
                       f"/admin/{'income-tax' if type_name == 'income_tax' else type_name}?open={x.get('id', '')}", x.get("id"))
                for x in rows
            ]
            if tax_results: groups.append({"type": type_name, "label": label, "results": tax_results})

        total = sum(len(g["results"]) for g in groups)
        return {"query": term, "total": total, "results": groups}

    async def _notify(role, title, description, category, ntype="information", priority=None, user_id=None, **extra):
        await create_notification(db, role, title, description, category, ntype, priority, user_id, **extra)

    async def _audit(user, action, collection, item_id=None, changes=None):
        try:
            await db["erp_audit_logs"].insert_one({
                "id": f"AUD-{uuid.uuid4().hex[:10].upper()}",
                "action": action, "collection": collection, "item_id": item_id,
                "user_id": (user or {}).get("id"), "role": (user or {}).get("role"),
                "changes": changes or {}, "ts": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

    async def _customer_user_by_id(customer_id):
        if not customer_id:
            return None
        return await db.users.find_one({"role": "customer", "$or": [{"id": customer_id}, {"meta.customer_id": customer_id}]}, {"_id": 0})

    async def _agent_user_by_name(agent_name):
        return await db.users.find_one({"role": "agent", "name": agent_name}, {"_id": 0})

    async def _customer_record_for_user(user: dict):
        """Resolve the canonical ERP customer record for a logged-in customer."""
        if user.get("role") != "customer":
            return None
        meta_id = (user.get("meta") or {}).get("customer_id")
        if meta_id:
            record = await db["erp_customers"].find_one({"id": meta_id}, {"_id": 0})
            if record:
                return record
        clauses = []
        if user.get("email"):
            clauses.append({"email": user.get("email")})
        if user.get("mobile"):
            clauses.append({"mobile": user.get("mobile")})
        if clauses:
            record = await db["erp_customers"].find_one({"$or": clauses}, {"_id": 0})
            if record:
                await db.users.update_one({"id": user.get("id")}, {"$set": {"meta.customer_id": record["id"]}})
                return record
        return None

    async def _ensure_customer_meta(user: dict):
        """A customer login must always resolve to its ERP customer record. Accounts whose
        `meta.customer_id` was never linked (imported / legacy customers) are linked here, so
        the customer sees exactly their own bookings and statuses instead of an empty list."""
        meta = user.get("meta") or {}
        if user.get("role") != "customer" or meta.get("customer_id"):
            return
        record = await _customer_record_for_user(user)
        if record:
            user["meta"] = {**meta, "customer_id": record["id"]}

    @router.get("/customer/profile")
    async def get_customer_profile(user: dict = Depends(get_current_user)):
        """Return only the authenticated customer's canonical profile."""
        if user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Customer access required")
        profile = await _customer_record_for_user(user)
        if not profile:
            raise HTTPException(status_code=404, detail="Customer profile not found")
        return _ok({"profile": profile, "user": {"id": user.get("id"), "name": user.get("name"), "email": user.get("email"), "mobile": user.get("mobile"), "role": user.get("role"), "avatar": user.get("avatar"), "meta": user.get("meta", {})}})

    @router.put("/customer/profile")
    async def update_customer_profile(body: dict = Body(...), user: dict = Depends(get_current_user)):
        """Update safe self-service fields on the authenticated customer record."""
        if user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Customer access required")
        profile = await _customer_record_for_user(user)
        if not profile:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        update = {}
        if "full_name" in body:
            full_name = str(body.get("full_name") or "").strip()
            if not full_name:
                raise HTTPException(status_code=422, detail="Customer name is required.")
            if len(full_name) > 120:
                raise HTTPException(status_code=422, detail="Customer name is too long.")
            update["owner"] = full_name
            update["business_name"] = profile.get("business_name") or full_name

        if "mobile" in body:
            mobile = normalize_mobile(str(body.get("mobile") or "").strip())
            if not is_valid_indian_mobile(mobile):
                raise HTTPException(status_code=422, detail="Enter a valid 10-digit Indian mobile number starting with 6-9.")
            other = await db.users.find_one({"role": "customer", "mobile": mobile, "id": {"$ne": user.get("id")}}, {"_id": 0, "id": 1})
            if other:
                raise HTTPException(status_code=409, detail="This mobile number is already linked to another customer account.")
            update["mobile"] = mobile

        if "gstin" in body:
            gstin = str(body.get("gstin") or "").strip().upper()
            if gstin and (len(gstin) != 15 or not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gstin)):
                raise HTTPException(status_code=422, detail="Enter a valid 15-character GSTIN.")
            update["gstin"] = gstin or None
            update["gst_number"] = gstin or None

        if update:
            update["updated_at"] = datetime.now(timezone.utc)
            await db["erp_customers"].update_one({"id": profile["id"]}, {"$set": update})
            user_update = {}
            if "owner" in update:
                user_update["name"] = update["owner"]
            if "mobile" in update:
                user_update["mobile"] = update["mobile"]
            if user_update:
                await db.users.update_one({"id": user.get("id")}, {"$set": user_update})
                user.update(user_update)

        refreshed = await db["erp_customers"].find_one({"id": profile["id"]}, {"_id": 0})
        return _ok({
            "profile": refreshed,
            "user": {"id": user.get("id"), "name": user.get("name"), "email": user.get("email"), "mobile": user.get("mobile"), "role": user.get("role"), "avatar": user.get("avatar"), "meta": user.get("meta", {})},
        }, message="Customer details saved")

    async def _resolve_customer_id(value=None, *, name=None):
        """Return the ERP customer's stable `id`; never persist a display name as a FK."""
        if value:
            value = str(value).strip()
            record = await db["erp_customers"].find_one({"id": value}, {"_id": 0})
            if record:
                return record["id"]
            # Accept a customer portal user id only at the API boundary and
            # immediately translate it to the canonical ERP customer id.
            user = await db.users.find_one(
                {"role": "customer", "$or": [{"id": value}, {"meta.customer_id": value}]},
                {"_id": 0},
            )
            if user:
                record = await _customer_record_for_user(user)
                if record:
                    return record["id"]
        if name:
            record = await db["erp_customers"].find_one({"business_name": str(name).strip()}, {"_id": 0})
            if record:
                return record["id"]
        return None

    async def _resolve_service_id(value=None, *, name=None):
        """Resolve the canonical service record id from an API payload."""
        if value:
            value = str(value).strip()
            service = await db["erp_services"].find_one({"id": value}, {"_id": 0})
            if service:
                return service["id"]
        if name:
            wanted = str(name).strip()
            service = await db["erp_services"].find_one(
                {"$or": [{"name": wanted}, {"title": wanted}]}, {"_id": 0}
            )
            if service:
                return service["id"]
        return None

    async def _resolve_agent_id(value=None, *, name=None):
        """Resolve the canonical agent record id from an API payload."""
        if value:
            value = str(value).strip()
            agent = await db["erp_agents"].find_one({"id": value}, {"_id": 0})
            if agent:
                return agent["id"]
        if name:
            agent = await db["erp_agents"].find_one({"name": str(name).strip()}, {"_id": 0})
            if agent:
                return agent["id"]
        return None

    async def _resolve_employee_id(value=None, *, name=None):
        """Resolve the canonical employee record id from an API payload.

        Matching is whitespace-trimmed and case-insensitive on id / emp_id / name, so a
        consultant typed as "ravi kumar " still resolves to the real "Ravi Kumar" record.
        Returns None when nothing matches (never invents an employee)."""
        import re as _re
        def _exact_ci(text):
            return {"$regex": f"^{_re.escape(text)}$", "$options": "i"}
        value = str(value).strip() if value not in (None, "") else ""
        name = str(name).strip() if name not in (None, "") else ""
        if value:
            employee = await db["erp_employees"].find_one({"$or": [{"id": value}, {"emp_id": value}]}, {"_id": 0})
            if not employee:
                employee = await db["erp_employees"].find_one({"$or": [{"id": _exact_ci(value)}, {"emp_id": _exact_ci(value)}]}, {"_id": 0})
            if employee:
                return employee["id"]
        for candidate in (name, value):
            if candidate:
                employee = await db["erp_employees"].find_one({"name": _exact_ci(candidate)}, {"_id": 0})
                if employee:
                    return employee["id"]
        return None


    async def _sync_customer_dependents(customer_id: str, customer: dict = None):
        """Propagate customer display fields while keeping customer_id authoritative."""
        if not customer_id:
            return
        customer = customer or await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            return
        display = customer.get("business_name") or customer.get("name") or customer_id
        # Foreign keys remain customer_id; these are denormalized display fields only.
        for collection in ("erp_bookings", "erp_invoices", "erp_payments", "erp_projects", "erp_documents", "erp_tickets"):
            await db[collection].update_many({"customer_id": customer_id}, {"$set": {"customer": display}})

    async def _sync_customer_gst_record(customer: dict):
        """Requirement: a customer marked as a GST customer must automatically
        appear in Admin -> GST Module, reusing the existing customer_id/GSTIN
        rather than creating a second, disconnected customer record.

        Keyed by customer_id (one GST record per customer): created the first
        time the customer's service is GST, and kept in sync (name/GSTIN) on
        every subsequent customer edit. Never deletes the GST record if the
        customer's service later changes, so filed-return history is preserved."""
        customer_id = customer.get("id")
        if not customer_id:
            return
        service_type = str(customer.get("service_type") or "").strip().lower()
        if service_type != "gst":
            return
        display = customer.get("business_name") or customer.get("owner") or customer_id
        update = {
            "client": display,
            "gstin": customer.get("gst_number") or "",
            "customer_id": customer_id,
        }
        existing_gst = await db["erp_gst"].find_one({"customer_id": customer_id}, {"_id": 0, "id": 1})
        if existing_gst:
            await db["erp_gst"].update_one({"id": existing_gst["id"]}, {"$set": update})
        else:
            gst_id = f"GST-{uuid.uuid4().hex[:6].upper()}"
            await db["erp_gst"].insert_one({
                "id": gst_id, **update,
                "return_type": "GSTR-1", "fy": "", "period": "", "due_date": "",
                "filed_date": "", "ack": "", "consultant": customer.get("assigned_employee") or "",
                "status": WORKFLOW_TO_MODULE.get(customer.get("filing_status"), "Pending"),
            })

    async def _sync_agent_dependents(agent_id: str, agent: dict = None):
        """Propagate agent display fields without touching attendance/account status."""
        if not agent_id:
            return
        agent = agent or await db["erp_agents"].find_one({"id": agent_id}, {"_id": 0})
        if not agent:
            return
        name = agent.get("name") or agent.get("agent_id") or agent_id
        await db["erp_bookings"].update_many({"agent_id": agent_id}, {"$set": {"assigned_agent": name, "agent": name}})
        await db["erp_commissions"].update_many({"agent_id": agent_id}, {"$set": {"agent": name, "agent_name": name}})
        await db["erp_payments"].update_many({"agent_id": agent_id}, {"$set": {"agent": name}})

    async def _sync_service_dependents(service_id: str, service: dict = None):
        """Propagate service display fields; service_id remains the canonical FK."""
        if not service_id:
            return
        service = service or await db["erp_services"].find_one({"id": service_id}, {"_id": 0})
        if not service:
            return
        name = service.get("name") or service.get("title") or service_id
        for collection in ("erp_bookings", "erp_invoices", "erp_payments"):
            await db[collection].update_many({"service_id": service_id}, {"$set": {"service": name}})

    async def _sync_invoice_dependents(invoice: dict):
        """Keep invoice-linked payments and booking relationships consistent."""
        if not invoice:
            return
        refs = [x for x in (invoice.get("invoice_no"), invoice.get("id")) if x]
        if not refs:
            return
        query = {"$or": [{"invoice_no": str(x)} for x in refs] + [{"invoice_id": str(x)} for x in refs]}
        update = {
            "invoice_id": invoice.get("id"),
            "invoice_no": invoice.get("invoice_no") or invoice.get("id"),
            "customer_id": invoice.get("customer_id"),
            "customer": invoice.get("customer"),
            "service_id": invoice.get("service_id"),
            "service": invoice.get("service"),
            "booking_id": invoice.get("booking_id"),
        }
        await db["erp_payments"].update_many(query, {"$set": update})

    async def _normalize_relationships(name, body: dict, existing: dict = None):
        """Attach/validate Phase-4 foreign keys while retaining display fields for UI."""
        current = existing or {}

        if name == "services":
            for field in ("name", "title", "category"):
                if str(body.get(field) or "").strip() and is_non_service_label(body.get(field)):
                    raise HTTPException(
                        status_code=422,
                        detail=f"'{body.get(field)}' is an application/placeholder name, not a service. Enter the actual service (for example GST Registration, Income Tax Return, TDS Return Filing, ROC Annual Filing, Accounting).",
                    )

        if name == "customers":
            if "payment_frequency" in body and body.get("payment_frequency") not in (None, "", "Monthly", "Quarterly", "Yearly"):
                raise HTTPException(status_code=422, detail="payment_frequency must be Monthly, Quarterly or Yearly")
            if body.get("service_id"):
                sid = await _resolve_service_id(str(body.get("service_id")))
                if not sid:
                    raise HTTPException(status_code=422, detail="Invalid service_id")
                body["service_id"] = sid
                service = await db["erp_services"].find_one({"id": sid}, {"_id": 0})
                if service:
                    body["service_type"] = service.get("category") or service.get("name") or body.get("service_type")
            elif body.get("service_type"):
                sid = await _resolve_service_id(None, name=body.get("service_type"))
                if sid:
                    body["service_id"] = sid
            if body.get("filing_status") and body.get("filing_status") not in {"Paid", "Pending", "Processing", "Completed"}:
                raise HTTPException(status_code=422, detail="Invalid customer payment status")

            # Customer -> Employee is a real relationship. Keep employee_id as the
            # authoritative FK and assigned_employee as the display name used by
            # existing Employee portal queries.
            if "assigned_employee" in body or "employee_id" in body or current.get("employee_id"):
                employee_value = body.get("employee_id") if "employee_id" in body else current.get("employee_id")
                employee_name = body.get("assigned_employee") if "assigned_employee" in body else current.get("assigned_employee")
                # Re-assignment by name: when Admin changes only the consultant name, the stale
                # employee_id from the existing record must not win (it would silently keep the
                # previous employee). An explicitly changed employee_id still takes priority.
                _norm = lambda v: str(v or "").strip().lower()
                _name_changed = "assigned_employee" in body and _norm(body.get("assigned_employee")) != _norm(current.get("assigned_employee"))
                _id_changed = "employee_id" in body and _norm(body.get("employee_id")) != _norm(current.get("employee_id"))
                if _name_changed and not _id_changed:
                    employee_value = ""
                # A blank / whitespace-only value means "no employee assigned" - it is not an
                # attempt to assign one, so it must never trigger employee validation.
                employee_value = str(employee_value).strip() if employee_value not in (None, "") else ""
                employee_name = str(employee_name).strip() if employee_name not in (None, "") else ""
                if not employee_value and not employee_name:
                    body["employee_id"] = None
                    body["assigned_employee"] = ""
                else:
                    eid = await _resolve_employee_id(employee_value, name=employee_name)
                    if not eid:
                        raise HTTPException(status_code=422, detail="A valid employee_id or employee name is required when assigning a customer to an employee")
                    employee = await db["erp_employees"].find_one({"id": eid}, {"_id": 0})
                    body["employee_id"] = eid
                    body["assigned_employee"] = employee.get("name") or employee_name or eid

            agent_name = body.get("assigned_agent") or current.get("assigned_agent")
            if body.get("agent_id") or current.get("agent_id") or agent_name:
                aid = await _resolve_agent_id(
                    body.get("agent_id") if "agent_id" in body else current.get("agent_id"),
                    name=agent_name,
                )
                if aid:
                    body["agent_id"] = aid
                    agent = await db["erp_agents"].find_one({"id": aid}, {"_id": 0})
                    if agent:
                        body.setdefault("assigned_agent", agent.get("name"))
                        # Per-customer commission: default from the agent's own rate the
                        # first time this customer is assigned, but never overwrite a
                        # percentage this specific customer already has — the same agent
                        # can hold different percentages across different customers.
                        no_override = body.get("agent_commission_percentage") in (None, "")
                        no_existing = current.get("agent_commission_percentage") in (None, "", None)
                        if no_override and no_existing and agent.get("commission_percentage") not in (None, ""):
                            body["agent_commission_percentage"] = agent.get("commission_percentage")

        if name in {"bookings", "invoices"}:
            cid = await _resolve_customer_id(
                body.get("customer_id") if "customer_id" in body else current.get("customer_id"),
                name=body.get("customer") or current.get("customer"),
            )
            if cid:
                body["customer_id"] = cid
                customer_record = await db["erp_customers"].find_one({"id": cid}, {"_id": 0, "business_name": 1})
                if customer_record and not body.get("customer"):
                    body["customer"] = customer_record.get("business_name")
            elif name in {"bookings", "invoices"}:
                raise HTTPException(status_code=422, detail="A valid customer_id is required")

        if name in {"bookings", "invoices"}:
            service_name = body.get("service") or body.get("service_name") or current.get("service") or current.get("service_name")
            sid = await _resolve_service_id(
                body.get("service_id") if "service_id" in body else current.get("service_id"),
                name=service_name,
            )
            if sid:
                body["service_id"] = sid
                service = await db["erp_services"].find_one({"id": sid}, {"_id": 0})
                if service and name == "bookings" and existing is None and (not is_valid_service_record(service) or not is_active_service_record(service)):
                    raise HTTPException(status_code=422, detail="A valid service_id is required: the selected service is not available for booking. Please choose a service from the NTAXCO service list.")
                if service:
                    canonical = service.get("name") or service.get("title")
                    if name == "bookings" and canonical:
                        # The booking must carry the ACTUAL booked service name (from the service record),
                        # never free text sent by a client.
                        body["service"] = canonical
                    else:
                        body.setdefault("service", canonical)
            else:
                raise HTTPException(status_code=422, detail="A valid service_id is required: the selected service was not found. Please choose a service from the NTAXCO service list.")

        if name == "bookings":
            agent_name = body.get("assigned_agent") or body.get("agent") or current.get("assigned_agent") or current.get("agent")
            if body.get("agent_id") or current.get("agent_id") or agent_name:
                aid = await _resolve_agent_id(
                    body.get("agent_id") if "agent_id" in body else current.get("agent_id"),
                    name=agent_name,
                )
                if aid:
                    body["agent_id"] = aid
                    agent = await db["erp_agents"].find_one({"id": aid}, {"_id": 0})
                    if agent and agent.get("name"):
                        body["assigned_agent"] = agent["name"]
                elif body.get("agent_id") or current.get("agent_id") or agent_name:
                    raise HTTPException(status_code=422, detail="A valid agent_id is required when a booking is assigned to an agent")

        if name == "invoices" and (body.get("booking_id") or current.get("booking_id")):
            booking_id = body.get("booking_id") or current.get("booking_id")
            booking = await db["erp_bookings"].find_one({"id": str(booking_id)}, {"_id": 0})
            if not booking:
                raise HTTPException(status_code=422, detail="Invalid booking_id")
            body["booking_id"] = booking["id"]
            # Keep invoice customer/service aligned with its booking.
            if booking.get("customer_id"):
                body["customer_id"] = booking["customer_id"]
            if booking.get("service_id"):
                body["service_id"] = booking["service_id"]
            if booking.get("service"):
                body["service"] = booking["service"]

        if name == "payments":
            invoice_ref = body.get("invoice_id") or current.get("invoice_id")
            invoice_no = body.get("invoice_no") or current.get("invoice_no")
            invoice = None
            if invoice_ref:
                invoice = await db["erp_invoices"].find_one({"id": str(invoice_ref)}, {"_id": 0})
            if not invoice and invoice_no:
                invoice = await db["erp_invoices"].find_one({"invoice_no": str(invoice_no)}, {"_id": 0})
            if invoice:
                body["invoice_id"] = invoice["id"]
                body["invoice_no"] = invoice.get("invoice_no") or invoice["id"]
                body["customer_id"] = invoice.get("customer_id")
                body["booking_id"] = invoice.get("booking_id")
                body["service_id"] = invoice.get("service_id")
                if invoice.get("service"):
                    body["service"] = invoice["service"]
            elif invoice_ref or invoice_no:
                raise HTTPException(status_code=404, detail="Invoice not found")
            agent_name = body.get("agent") or current.get("agent")
            if body.get("agent_id") or current.get("agent_id") or agent_name:
                aid = await _resolve_agent_id(
                    body.get("agent_id") if "agent_id" in body else current.get("agent_id"),
                    name=agent_name,
                )
                if aid:
                    body["agent_id"] = aid
                    agent = await db["erp_agents"].find_one({"id": aid}, {"_id": 0})
                    if agent and agent.get("name"):
                        body["agent"] = agent["name"]
                elif body.get("agent_id") or current.get("agent_id"):
                    raise HTTPException(status_code=422, detail="Invalid agent_id")

        if name == "commissions":
            agent_name = body.get("agent") or body.get("agent_name") or current.get("agent") or current.get("agent_name")
            if body.get("agent_id") or current.get("agent_id") or agent_name:
                aid = await _resolve_agent_id(
                    body.get("agent_id") if "agent_id" in body else current.get("agent_id"),
                    name=agent_name,
                )
                if aid:
                    body["agent_id"] = aid
                    agent = await db["erp_agents"].find_one({"id": aid}, {"_id": 0})
                    if agent and agent.get("name"):
                        body["agent"] = agent["name"]
                elif body.get("agent_id") or current.get("agent_id"):
                    raise HTTPException(status_code=422, detail="Invalid agent_id")

    async def _employee_record_for_user(user: dict):
        """Resolve the canonical employee profile for the authenticated employee user.

        Older employee accounts may not have meta.employee_id populated, while newer
        accounts do. Keep both paths compatible so Employee portal modules (especially
        Tasks) do not fail with a misleading 404 just because the auth/profile link is
        missing or was created by an older workflow.
        """
        if user.get("role") != "employee":
            return None

        meta = user.get("meta") or {}
        candidates = []

        # 1) Canonical auth -> employee relationship.
        emp_id = meta.get("employee_id")
        if emp_id:
            candidates.append({"id": str(emp_id)})

        # 2) Existing employee accounts created before meta.employee_id existed.
        for field in ("mobile", "phone"):
            value = user.get(field)
            if value:
                candidates.append({field: str(value)})

        email = str(user.get("email") or "").strip().lower()
        if email:
            candidates.append({"email": email})

        # 3) Some installations store the auth user id on the employee profile.
        user_id = user.get("id")
        if user_id:
            candidates.extend([
                {"user_id": str(user_id)},
                {"auth_user_id": str(user_id)},
            ])

        for query in candidates:
            emp = await db["erp_employees"].find_one(query, {"_id": 0})
            if emp:
                return emp
        return None

    def _assigned_customer_query(emp: dict) -> dict:
        """Mongo filter for customers assigned to this employee.

        employee_id is the authoritative FK written by the existing assignment flow.
        Legacy customers that only carry the display name (assigned_employee) and no
        employee_id are still matched by name so older data keeps working.
        """
        clauses = []
        if emp and emp.get("id"):
            clauses.append({"employee_id": emp["id"]})
        if emp and emp.get("name"):
            clauses.append({"$and": [
                {"assigned_employee": emp["name"]},
                {"$or": [{"employee_id": {"$exists": False}}, {"employee_id": None}, {"employee_id": ""}]},
            ]})
        return {"$or": clauses} if clauses else {"id": "__none__"}

    def _customer_assigned_to_employee(customer: dict, emp: dict) -> bool:
        """Python twin of _assigned_customer_query for an already-loaded customer record."""
        if not customer or not emp:
            return False
        if customer.get("employee_id"):
            return bool(emp.get("id")) and customer.get("employee_id") == emp.get("id")
        return bool(emp.get("name")) and customer.get("assigned_employee") == emp.get("name")

    async def _employee_can_access_customer(customer_id, user: dict) -> bool:
        """Server-side check: is this ERP customer assigned to the authenticated employee?"""
        if not customer_id:
            return False
        emp = await _employee_record_for_user(user)
        if not emp:
            return False
        customer = await db["erp_customers"].find_one({"id": str(customer_id)}, {"_id": 0, "id": 1, "employee_id": 1, "assigned_employee": 1})
        return _customer_assigned_to_employee(customer, emp)

    async def _sync_employee_user(emp: dict, password_hash: str = None):
        """Keep the auth `users` record for an employee in sync with their erp_employees profile.

        password_hash is optional: when the admin supplies a password on Add/Edit
        Employee it is hashed by the caller and passed in here; when omitted, an
        existing account's password is left untouched (never cleared to None) and a
        new account is created exactly as before (password_hash: None, so the
        employee still logs in via mobile/OTP or a later self-registration/reset)."""
        mobile = emp.get("mobile")
        if not mobile:
            return
        update = {
            "name": emp.get("name") or "Employee",
            "meta.employee_id": emp["id"],
            "meta.department": emp.get("department"),
            "meta.designation": emp.get("designation"),
            "meta.manager": emp.get("manager"),
        }
        if password_hash:
            update["password_hash"] = password_hash
        existing = await db.users.find_one({"mobile": mobile, "role": "employee"})
        if existing:
            await db.users.update_one({"id": existing["id"]}, {"$set": update})
        else:
            await db.users.insert_one({
                "id": f"EMPLOYEE-{uuid.uuid4().hex[:8].upper()}",
                "name": emp.get("name") or "Employee",
                "email": emp.get("email"),
                "mobile": mobile,
                "role": "employee",
                "password_hash": password_hash,
                "avatar": None,
                "meta": {
                    "employee_id": emp["id"],
                    "department": emp.get("department"),
                    "designation": emp.get("designation"),
                    "manager": emp.get("manager"),
                    "portal_login": True,
                },
            })

    # ---------------- self-service employee profile ----------------
    @router.get("/employees/me")
    async def get_my_employee_profile(user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Only employees can access this endpoint")
        emp = await _employee_record_for_user(user)
        if not emp:
            raise HTTPException(status_code=404, detail="No employee profile found for this account yet")
        return _ok(emp)

    @router.put("/employees/me")
    async def update_my_employee_profile(body: dict = Body(...), user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Only employees can access this endpoint")
        emp = await _employee_record_for_user(user)
        if not emp:
            raise HTTPException(status_code=404, detail="No employee profile found for this account yet")
        # Employees can only edit their own contact/address details, not HR fields.
        allowed_fields = {"email", "address", "state", "avatar"}
        update = {k: v for k, v in body.items() if k in allowed_fields}
        if update:
            await db["erp_employees"].update_one({"id": emp["id"]}, {"$set": update})
            emp = await db["erp_employees"].find_one({"id": emp["id"]}, {"_id": 0})
        return _ok(emp, message="Profile updated")

    async def _sync_customer_service_records(customer: dict):
        """Keep service modules connected to the canonical customer record.

        Existing filing/return history is preserved. Missing service records are
        created only when the customer's primary service matches the module.
        customer_id is the authoritative foreign key.
        """
        customer_id = customer.get("id")
        if not customer_id:
            return
        service_type = str(customer.get("service_type") or "").strip().lower()
        display = customer.get("business_name") or customer.get("owner") or customer_id
        _init_status = WORKFLOW_TO_MODULE.get(customer.get("filing_status"), "Pending")
        mappings = {
            "gst": ("erp_gst", "client", {
                "customer_id": customer_id, "client": display,
                "gstin": customer.get("gst_number") or "",
                "return_type": "GSTR-1", "fy": "", "period": "",
                "due_date": "", "filed_date": "", "ack": "",
                "consultant": customer.get("assigned_employee") or "", "status": _init_status,
            }),
            "income tax": ("erp_itr", "client", {
                "customer_id": customer_id, "client": display,
                "pan": customer.get("pan") or "", "ay": "2026-27",
                "return_no": "ITR-3", "due_date": "", "filed_date": "",
                "ack": "", "consultant": customer.get("assigned_employee") or "", "status": _init_status,
            }),
            "tds": ("erp_tds", "client", {
                "customer_id": customer_id, "client": display,
                "pan": customer.get("pan") or "", "form": "24Q", "quarter": "Q1",
                "fy": "2026-27", "due_date": "", "filed_date": "",
                "challan": "", "status": _init_status,
            }),
            "roc": ("erp_roc", "company", {
                "customer_id": customer_id, "company": display,
                "cin": customer.get("cin") or "", "form": "AOC-4",
                "fy": "2025-26", "due_date": "", "filed_date": "",
                "consultant": customer.get("assigned_employee") or "", "status": _init_status,
            }),
        }
        entry = mappings.get(service_type)
        if not entry:
            return
        collection, display_key, defaults = entry
        existing = await db[collection].find_one({"customer_id": customer_id}, {"_id": 0, "id": 1})
        if existing:
            update = {
                display_key: display,
                "customer_id": customer_id,
            }
            if collection == "erp_gst":
                update["gstin"] = customer.get("gst_number") or ""
            elif collection in {"erp_itr", "erp_tds"}:
                update["pan"] = customer.get("pan") or ""
            elif collection == "erp_roc":
                update["cin"] = customer.get("cin") or ""
            await db[collection].update_one({"id": existing["id"]}, {"$set": update})
            return
        defaults["id"] = f"{service_type[:3].upper()}-{uuid.uuid4().hex[:6].upper()}"
        await db[collection].insert_one(defaults)

    async def _notify_booking_status_change(item, status, actor="Admin"):
        """Customer (only the booking's owner) + Admin notification for a booking status change."""
        try:
            cust_user = await _customer_user_by_id(item.get("customer_id"))
            await _notify("customer", f"Booking {item.get('id')} update", f"Your booking status is now '{status}'.", "Bookings", "information",
                          user_id=(cust_user or {}).get("id"), kind=KIND_BOOKING_STATUS, booking_id=item.get("id"), customer_id=item.get("customer_id"))
            await _notify("admin", f"Booking {item.get('id')} → {status}", f"{actor} updated booking {item.get('id')} to {status}.", "Bookings", "information",
                          priority=item.get("priority"), kind=KIND_BOOKING_STATUS, booking_id=item.get("id"), customer_id=item.get("customer_id"),
                          customer_name=item.get("customer"), service_id=item.get("service_id"), service_name=item.get("service"), booking_status=status)
        except Exception:
            logger.exception("Booking %s updated but its notifications could not be created", item.get("id"))

    @router.get("/admin/customers/summary")
    async def admin_customers_summary(user: dict = Depends(get_current_user)):
        """Live customer dashboard counts, computed from MongoDB on every call."""
        if user.get("role") not in {"admin", "super_admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="Admin access required")
        return _ok(await customer_status_summary(db), message="Customer summary loaded")

    @router.get("/admin/service-categories")
    async def admin_service_categories(user: dict = Depends(get_current_user)):
        """The real, assignable NTAXCO services grouped under the six categories (GST, Income Tax, TDS, ROC,
        Accounting, Others). Read from the live service catalogue on every call: nothing is hardcoded except
        the category names themselves, and application/placeholder records or inactive/archived services
        are never offered. This is the single source for the Admin Drag & Drop \"Assign any service\" list."""
        if user.get("role") not in {"admin", "super_admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="Admin access required")
        rows = await db["erp_services"].find({}, {"_id": 0}).to_list(5000)
        groups = {c: [] for c in SERVICE_CATEGORIES}
        for svc in rows:
            if not is_valid_service_record(svc) or not is_active_service_record(svc):
                continue
            groups[service_category_bucket(svc)].append({
                "id": svc["id"], "name": _good_service_name(svc.get("name"), svc.get("title")) or svc["id"],
                "category": svc.get("category"), "status": svc.get("status") or "Active",
            })
        for items in groups.values():
            items.sort(key=lambda x: str(x["name"]).lower())
        return _ok([{"category": c, "services": groups[c]} for c in SERVICE_CATEGORIES])

    @router.post("/admin/workflows/drag-drop")
    async def drag_drop_workflow(body: dict = Body(...), user: dict = Depends(get_current_user)):
        """Route wrapper: HTTPExceptions pass through untouched; any other failure is logged with its
        traceback and returned as a JSON `detail` naming the real cause, so Admin sees the actual
        reason instead of a bare "Internal Server Error"."""
        try:
            # Serialization is done HERE (inside the try) so a value FastAPI cannot encode (NaN/inf from an
            # import, ObjectId, Decimal128, bytes ...) is reported with its real cause instead of escaping
            # as a bodiless "Internal Server Error" after this wrapper has already returned.
            return _json_safe(await _drag_drop_workflow(body, user))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Workflow drag/drop failed (body=%s)", {k: body.get(k) for k in ("source_type", "target_type", "source_id", "target_value")})
            raise HTTPException(status_code=500, detail=f"Workflow update failed: {type(exc).__name__}: {exc}")

    async def _drag_drop_workflow(body: dict, user: dict):
        """Validated Phase-5 drag/drop mutations.

        Drag/drop is only a UI affordance; this endpoint is the authoritative
        mutation boundary. It validates the source, target, role and existing
        relationships before changing MongoDB.
        """
        if user.get("role") not in {"admin", "super_admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="Only administrators can perform workflow reassignment")

        source_type = str(body.get("source_type") or "").strip().lower()
        target_type = str(body.get("target_type") or "").strip().lower()
        source_id = str(body.get("source_id") or "").strip()
        target_value = body.get("target_value")
        if not source_type or not target_type or not source_id or target_value in (None, ""):
            raise HTTPException(status_code=400, detail="source_type, target_type, source_id and target_value are required")

        allowed = {
            ("customer", "service"),
            ("customer", "payment-status"),
            ("customer", "payment-frequency"),
            ("customer", "status"),
            ("customer", "agent"),
            ("customer", "employee"),
            ("booking", "status"),
            ("payment", "service"),
            ("payment", "payment-status"),
            ("booking", "service"),
        }
        if (source_type, target_type) not in allowed:
            raise HTTPException(status_code=400, detail="This workflow relationship is not supported")

        collection_by_source = {"customer": "erp_customers", "payment": "erp_payments", "booking": "erp_bookings"}
        collection = collection_by_source[source_type]
        source = await db[collection].find_one({"id": source_id}, {"_id": 0})
        if not source:
            raise HTTPException(status_code=404, detail=f"{source_type.title()} record not found")

        before = {}
        target_display = str(target_value)

        if target_type == "service":
            service_id = await _resolve_service_id(str(target_value), name=str(target_value))
            if not service_id:
                raise HTTPException(status_code=422, detail=f"Service '{target_value}' was not found in the service catalog")
            service = await db["erp_services"].find_one({"id": service_id}, {"_id": 0})
            if not service:
                raise HTTPException(status_code=404, detail="Service not found")
            if not is_valid_service_record(service) or not is_active_service_record(service):
                raise HTTPException(status_code=422, detail="That is not an active NTAXCO service and cannot be assigned. Choose a real service from the catalogue.")
            service_name = service.get("name") or service.get("title") or service_id
            # The displayed service_type/category is whatever Admin set on the
            # service record — never forced back into a fixed enum. Section 2/6/12:
            # a brand-new service (e.g. "Trademark Registration") must be usable
            # here the moment it's created, with no code change and no loss of
            # its real category. service_id remains the authoritative FK either way.
            category = str(service.get("category") or service_name).strip() or "Other"
            if source_type == "customer":
                before = {"service_id": source.get("service_id"), "service_type": source.get("service_type")}
                if str(source.get("service_id") or "") == service_id and str(source.get("service_type") or "") == category:
                    return _ok(source, message="No change needed — customer is already assigned to this service")
                update = {"service_id": service_id, "service_type": category}
            elif source_type == "booking":
                before = {"service_id": source.get("service_id"), "service": source.get("service")}
                if str(source.get("service_id") or "") == service_id:
                    return _ok(source, message="No change needed — booking is already assigned to this service")
                update = {"service_id": service_id, "service": service_name}
            else:  # payment
                if source.get("invoice_id") or source.get("invoice_no"):
                    raise HTTPException(status_code=409, detail="Invoice-linked payments inherit their service from the invoice and cannot be reassigned directly")
                before = {"service_id": source.get("service_id"), "service": source.get("service")}
                if str(source.get("service_id") or "") == service_id:
                    return _ok(source, message="No change needed — payment is already assigned to this service")
                update = {"service_id": service_id, "service": service_name}
            await db[collection].update_one({"id": source_id}, {"$set": update})
            if source_type == "customer":
                # Category change only: connect/create the matching filing record, never touch status.
                fresh = await db[collection].find_one({"id": source_id}, {"_id": 0})
                await _sync_customer_gst_record(fresh)
                await _sync_customer_service_records(fresh)
            message = f"{source_type.title()} assigned to {service_name}"

        elif target_type == "payment-frequency":
            if source_type != "customer":
                raise HTTPException(status_code=400, detail="Payment frequency applies to customers only")
            valid = {"Monthly", "Quarterly", "Yearly"}
            if str(target_value) not in valid:
                raise HTTPException(status_code=400, detail="Invalid payment frequency")
            before = {"payment_frequency": source.get("payment_frequency")}
            update = {"payment_frequency": str(target_value)}
            if source.get("payment_frequency") == str(target_value):
                return _ok(source, message="No change needed — customer already has that payment frequency")
            await db[collection].update_one({"id": source_id}, {"$set": update})
            message = f"Customer payment frequency updated to {target_value}"
        elif target_type == "agent":
            if source_type != "customer":
                raise HTTPException(status_code=400, detail="Agent assignment applies to customers only")
            agent_id = await _resolve_agent_id(str(target_value))
            if not agent_id:
                raise HTTPException(status_code=422, detail="A valid agent_id is required")
            agent = await db["erp_agents"].find_one({"id": agent_id}, {"_id": 0})
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            before = {"agent_id": source.get("agent_id"), "assigned_agent": source.get("assigned_agent"), "agent_commission_percentage": source.get("agent_commission_percentage")}
            if source.get("agent_id") == agent_id:
                return _ok(source, message="No change needed — customer is already assigned to this agent")
            update = {"agent_id": agent_id, "assigned_agent": agent.get("name")}
            # Same per-customer percentage rule as the Add/Edit Customer form: default
            # from the agent's own rate only if this customer doesn't already have one.
            if source.get("agent_commission_percentage") in (None, "") and agent.get("commission_percentage") not in (None, ""):
                update["agent_commission_percentage"] = agent.get("commission_percentage")
            await db[collection].update_one({"id": source_id}, {"$set": update})
            agent_user = await db.users.find_one(
                {"role": "agent", "$or": [{"meta.agent_id": agent_id}, {"name": agent.get("name")}]},
                {"_id": 0, "id": 1},
            )
            if agent_user:
                await _notify(
                    "agent",
                    "Customer assigned",
                    f"{source.get('business_name') or source.get('name') or source_id} was assigned to you by Admin.",
                    "Customers",
                    "information",
                    user_id=agent_user.get("id"),
                )
            message = f"Customer assigned to agent {agent.get('name') or agent_id}"
        elif target_type == "employee":
            if source_type != "customer":
                raise HTTPException(status_code=400, detail="Employee assignment applies to customers only")
            employee_id = await _resolve_employee_id(str(target_value))
            if not employee_id:
                raise HTTPException(status_code=422, detail="A valid employee_id is required")
            employee = await db["erp_employees"].find_one({"id": employee_id}, {"_id": 0})
            if not employee:
                raise HTTPException(status_code=404, detail="Employee not found")
            before = {"employee_id": source.get("employee_id"), "assigned_employee": source.get("assigned_employee")}
            if source.get("employee_id") == employee_id:
                return _ok(source, message="No change needed — customer is already assigned to this employee")
            update = {"employee_id": employee_id, "assigned_employee": employee.get("name") or employee_id}
            await db[collection].update_one({"id": source_id}, {"$set": update})
            employee_user = await db.users.find_one(
                {"role": "employee", "meta.employee_id": employee_id},
                {"_id": 0, "id": 1},
            )
            if employee_user:
                await _notify(
                    "employee",
                    "Customer assigned",
                    f"{source.get('business_name') or source.get('name') or source_id} was assigned to you by Admin.",
                    "Customers",
                    "information",
                    user_id=employee_user.get("id"),
                )
            message = f"Customer assigned to employee {employee.get('name') or employee_id}"

        elif target_type == "status":
            if source_type == "customer":
                # Account status only (Active/Inactive/Suspended). Workflow values belong to
                # filing_status and are routed there so the two concepts never get mixed.
                if str(target_value) in WORKFLOW_STATUSES:
                    target_type = "payment-status"
                elif str(target_value) not in ACCOUNT_STATUSES:
                    raise HTTPException(status_code=400, detail="Invalid customer status")
                else:
                    before = {"status": source.get("status")}
                    update = {"status": str(target_value)}
            elif source_type == "booking":
                if str(target_value) not in BOOKING_STATUSES:
                    raise HTTPException(status_code=400, detail="Invalid booking status")
                before = {"status": source.get("status")}
                update = {"status": str(target_value), "status_updated_at": datetime.now(timezone.utc).isoformat()}
            else:
                raise HTTPException(status_code=400, detail="Status drag/drop is not supported for this record")
            if target_type == "status":
                if source.get("status") == str(target_value):
                    return _ok(source, message="No change needed — record already has that status")
                await db[collection].update_one({"id": source_id}, {"$set": update})
                message = f"{source_type.title()} status updated to {target_value}"
                if source_type == "booking":
                    await reconcile_customer_status(db, source.get("customer_id"))
                    await _notify_booking_status_change({**source, "status": str(target_value)}, str(target_value), "Admin")
        if target_type == "payment-status" and source_type == "customer" and str(target_value) in WORKFLOW_STATUSES:
            before = {"filing_status": source.get("filing_status")}
            changed = await apply_customer_workflow_status(db, source, str(target_value))
            for b in changed:
                await _notify_booking_status_change(b, b["status"], "Admin")
            if source.get("filing_status") == str(target_value) and not changed:
                return _ok(source, message="No change needed — customer is already " + str(target_value))
            message = f"Customer moved to {target_value}" + (f" — {len(changed)} booking(s) updated" if changed else "")
        elif target_type == "payment-status":
            if source_type == "customer":
                valid_statuses = {"Paid", "Pending", "Processing", "Completed"}
                if str(target_value) not in valid_statuses:
                    raise HTTPException(status_code=400, detail="Invalid customer payment status")
                before = {"filing_status": source.get("filing_status")}
                if source.get("filing_status") == str(target_value):
                    return _ok(source, message="No change needed — customer already has that payment status")
                update = {"filing_status": str(target_value)}
            elif source_type == "payment":
                # These are the existing canonical backend payment statuses.
                valid_statuses = {"Completed", "Pending", "Failed"}
                if str(target_value) not in valid_statuses:
                    raise HTTPException(status_code=400, detail="Invalid payment status")
                before = {"status": source.get("status")}
                if source.get("status") == str(target_value):
                    return _ok(source, message="No change needed — payment already has that status")
                update = {"status": str(target_value)}
                amount = round(float(source.get("total") or source.get("amount") or 0), 2)
                if amount <= 0:
                    raise HTTPException(status_code=400, detail="Payment amount must be greater than zero")
                # Preserve existing invoice financial synchronization by using
                # the same rules as the normal payment update path.
            else:
                raise HTTPException(status_code=400, detail="Booking payment status drag/drop is not supported")

            await db[collection].update_one({"id": source_id}, {"$set": update})
            if source_type == "payment":
                invoice_refs = {str(x) for x in (source.get("invoice_no"), source.get("invoice_id")) if x}
                for inv_ref in invoice_refs:
                    invoice = await db["erp_invoices"].find_one({"$or": [{"invoice_no": inv_ref}, {"id": inv_ref}]}, {"_id": 0})
                    if invoice:
                        synced = await _sync_invoice_financials(invoice["id"])
                        await _sync_booking_payment_status(synced or invoice)
            message = f"{source_type.title()} payment status updated to {target_value}"

        updated = await db[collection].find_one({"id": source_id}, {"_id": 0})
        await _audit(user, "workflow_drag_drop", source_type, source_id, {
            "target_type": target_type, "target_value": target_value, "before": before,
            "after": {k: updated.get(k) for k in before.keys()} if updated else {},
        })
        return _ok(updated, message=message)

    def make_crud(name, coll, prefix):
        def guard_write(user):
            allowed = WRITE_ROLES.get(name, {"admin"})
            if user.get("role") not in allowed:
                raise HTTPException(status_code=403, detail=f"Your role is not permitted to modify {name}")

        is_personal = name in PERSONAL_EMPLOYEE_COLLECTIONS
        is_customer_scoped = name in CUSTOMER_SCOPED_COLLECTIONS

        def _owns_booking(item, user):
            # Canonical ownership is the ERP customer record id. The account id
            # is accepted only for legacy rows that predate Phase 4 migration.
            canonical = (user.get("meta") or {}).get("customer_id")
            # Empty ids must never match: an unlinked login (no customer_id) would otherwise
            # match every legacy row that also has no customer_id.
            owner_ids = {i for i in (canonical, user.get("id")) if i}
            row_owner = item.get("customer_id")
            return bool(row_owner) and row_owner in owner_ids

        async def _owned_booking_ids(user):
            owner_ids = [i for i in ((user.get("meta") or {}).get("customer_id"), user.get("id")) if i]
            if not owner_ids:
                return set()
            rows = await db["erp_bookings"].find({"customer_id": {"$in": owner_ids}}, {"_id": 0, "id": 1}).to_list(None)
            return {r["id"] for r in rows if r.get("id")}

        def _owns_via_booking(item, booking_ids):
            # Legacy invoices/payments that were saved without a customer_id still belong to the customer
            # who owns the booking they point at. Rows with neither link are never shown to a customer.
            return not item.get("customer_id") and bool(item.get("booking_id")) and item.get("booking_id") in booking_ids

        async def _employee_name(user):
            emp = await _employee_record_for_user(user)
            return emp.get("name") if emp else None

        async def _employee_can_access_record(item: dict, user: dict) -> bool:
            """Canonical Employee authorization for any customer/work record.

            Employee access is derived from the employee -> customer relationship,
            with task/booking/project assignment accepted only when it resolves back
            to an assigned customer. Frontend filtering is never used as security.
            """
            if user.get("role") != "employee":
                return True
            emp = await _employee_record_for_user(user)
            if not emp:
                return False
            emp_id = str(emp.get("id") or "")
            emp_name = str(emp.get("name") or "")
            # Personal records are handled separately.
            if item.get("employee_id") and str(item.get("employee_id")) == emp_id:
                return True
            customer_id = item.get("customer_id")
            if customer_id and await _employee_can_access_customer(customer_id, user):
                return True
            booking_id = item.get("booking_id")
            if booking_id:
                booking = await db["erp_bookings"].find_one({"id": booking_id}, {"_id": 0, "customer_id": 1})
                if booking and booking.get("customer_id") and await _employee_can_access_customer(booking.get("customer_id"), user):
                    return True
            # Projects/appointments may use the legacy employee-name relationship.
            if item.get("assigned_employee") and str(item.get("assigned_employee")) == emp_name:
                return True
            if item.get("employee") and str(item.get("employee")) == emp_name:
                return True
            if item.get("consultant") and str(item.get("consultant")) == emp_name:
                return True
            return False

        async def _employee_can_access_customer_record(item: dict, user: dict) -> bool:
            if user.get("role") != "employee":
                return True
            return bool(item.get("id") and await _employee_can_access_customer(item.get("id"), user))

        async def _agent_name(user):
            if user.get("role") != "agent":
                return None
            agent_id = (user.get("meta") or {}).get("agent_id")
            if agent_id:
                agent = await db["erp_agents"].find_one({"id": agent_id}, {"_id": 0})
                if agent:
                    return agent.get("name")
            return user.get("name")

        async def _customer_profile(user):
            if user.get("role") != "customer":
                return None
            cid = (user.get("meta") or {}).get("customer_id")
            if cid:
                return await db["erp_customers"].find_one({"id": cid}, {"_id": 0})
            return await db["erp_customers"].find_one({"email": user.get("email")}, {"_id": 0})

        async def _audit(user, action, collection, item_id=None, changes=None):
            try:
                await db["erp_audit_logs"].insert_one({
                    "id": f"AUD-{uuid.uuid4().hex[:10].upper()}",
                    "action": action, "collection": collection, "item_id": item_id,
                    "user_id": user.get("id"), "role": user.get("role"),
                    "changes": changes or {}, "ts": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass

        async def _enrich_customer_financials(items):
            if name != "customers" or not items:
                return items
            customer_ids = [str(i.get("id")) for i in items if i.get("id")]
            billing = await _billing_for_customers(customer_ids)
            categories = await customer_service_categories(db, items)
            for item in items:
                item["service_categories"] = categories.get(item.get("id"), [])
                state = billing.get(str(item.get("id"))) or {}
                item["invoiced_amount"] = state.get("total_billed", 0.0)
                item["paid_amount"] = state.get("total_paid", 0.0)
                item["outstanding"] = state.get("outstanding", 0.0)
                item["invoice_count"] = state.get("invoice_count", 0)
            # Reuse the existing Security login-audit/session system. No second
            # customer activity tracker is created.
            ids = [str(i.get("id")) for i in items if i.get("id")]
            users = await db.users.find({"meta.customer_id": {"$in": ids}}, {"_id": 0, "id": 1, "meta": 1}).to_list(2000)
            user_by_customer = {str((u.get("meta") or {}).get("customer_id")): u for u in users}
            user_ids = [u.get("id") for u in users if u.get("id")]
            audits = await db.login_audits.find({"user_id": {"$in": user_ids}, "event_type": "LOGIN_SUCCESS"}, {"_id": 0}).sort("created_at", -1).to_list(5000) if user_ids else []
            latest = {}
            for a in audits:
                uid = a.get("user_id")
                if uid not in latest:
                    latest[uid] = a
            bookings = await db["erp_bookings"].find({"customer_id": {"$in": ids}}, {"_id": 0, "customer_id": 1, "status": 1, "booking_date": 1}).sort("booking_date", -1).to_list(5000)
            latest_booking = {}
            for b in bookings:
                cid = str(b.get("customer_id") or "")
                if cid and cid not in latest_booking:
                    latest_booking[cid] = b
            for item in items:
                cid = str(item.get("id") or "")
                u = user_by_customer.get(cid)
                a = latest.get(u.get("id")) if u else None
                if a:
                    item["last_login"] = (a.get("login_time") or a.get("created_at")).isoformat() if hasattr((a.get("login_time") or a.get("created_at")), "isoformat") else str(a.get("login_time") or a.get("created_at"))
                    item["login_status"] = "Successful"
                    item["recent_login_activity"] = {"ip": a.get("ip_address"), "device": a.get("device"), "browser": a.get("browser")}
                else:
                    item["last_login"] = None
                    item["login_status"] = "No successful login recorded"
                    item["recent_login_activity"] = None
                b = latest_booking.get(cid)
                item["booking_status"] = b.get("status") if b else None
            return items

        async def _document_visible_to_user(item: dict, user: dict) -> bool:
            """Authorize document access through the real booking/task assignment graph.

            Employee/agent access is intentionally based on assigned task -> booking -> document,
            not merely uploader/assigned-name fields, so unrelated customer files cannot leak.
            """
            role = user.get("role")
            if role in ("admin", "super_admin", "superadmin"):
                return True
            booking_id = item.get("booking_id")
            customer_id = item.get("customer_id")
            if role == "customer":
                # Admin-uploaded customer documents may not have a booking_id.
                # Canonical ownership is the customer's ERP id; legacy account
                # id remains supported by _owns_booking for migrated records.
                canonical_customer_id = (user.get("meta") or {}).get("customer_id")
                owner_ids = {str(x) for x in (canonical_customer_id, user.get("id")) if x}
                return bool(customer_id and str(customer_id) in owner_ids)
            if role == "employee":
                emp = await _employee_record_for_user(user)
                if not emp:
                    return False
                emp_id = emp.get("id")
                # Primary rule: the document belongs to a customer assigned to this employee.
                # (A document linked only to a booking resolves its customer through that booking.)
                owner_customer_id = customer_id
                if not owner_customer_id and booking_id:
                    _bk = await db["erp_bookings"].find_one({"id": booking_id}, {"_id": 0, "customer_id": 1})
                    owner_customer_id = (_bk or {}).get("customer_id")
                if owner_customer_id and await _employee_can_access_customer(owner_customer_id, user):
                    return True
                # Existing policy retained: documents tied to a task assigned to this employee.
                task_query = {"task_board": True, "employee_id": emp_id}
                if booking_id:
                    task_query["booking_id"] = booking_id
                elif customer_id:
                    task_query["customer_id"] = customer_id
                else:
                    return False
                return await db["erp_tasks"].find_one(task_query, {"_id": 1}) is not None
            if role == "agent":
                agent_id = (user.get("meta") or {}).get("agent_id")
                agent_name = await _agent_name(user)
                clauses = []
                if agent_id:
                    clauses.append({"agent_id": agent_id})
                if agent_name:
                    clauses.extend([{"assigned_agent": agent_name}, {"agent": agent_name}, {"agent_name": agent_name}])
                if not clauses:
                    return False
                task_query = {"task_board": True, "$or": clauses}
                if booking_id:
                    task_query["booking_id"] = booking_id
                elif customer_id:
                    task_query["customer_id"] = customer_id
                else:
                    return False
                return await db["erp_tasks"].find_one(task_query, {"_id": 1}) is not None
            return False

        @router.get(f"/{name}", name=f"list_{name}")
        async def list_items(search: str = Query(None), status: str = Query(None), customer_id: str = Query(None), page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
            await _ensure_customer_meta(user)
            if user.get("role") not in READ_ROLES.get(name, {"admin"}):
                raise HTTPException(status_code=403, detail=f"Your role is not permitted to view {name}")
            if name == "employees" and user.get("role") not in ("admin", "employee"):
                raise HTTPException(status_code=403, detail="You are not permitted to view employees")
            # Document bytes (base64) are never loaded for list responses - only GET /documents/{id} reads them.
            list_projection = {"_id": 0, "file_data": 0} if name == "documents" else {"_id": 0}
            items = await db[coll].find({}, list_projection).to_list(None)
            if name == "customers":
                items = [i for i in items if not i.get("is_deleted")]
            if name == "services" and user.get("role") != "admin":
                # Customers/agents/employees only ever see real, active services.
                items = [i for i in items if is_valid_service_record(i) and is_active_service_record(i)]
            if name == "agents" and user.get("role") != "admin":
                # Non-admin (customer) view: assignable agents only, public fields only.
                items = [_public_agent(i) for i in items if str(i.get("status") or "Active").lower() == "active"]
            if is_personal and user.get("role") == "employee":
                emp = await _employee_record_for_user(user)
                emp_id = emp["id"] if emp else "__none__"
                items = [i for i in items if i.get("employee_id") == emp_id]
            if user.get("role") == "employee":
                emp = await _employee_record_for_user(user)
                if name == "employees":
                    items = [i for i in items if emp and i.get("id") == emp["id"]]
                elif name == "customers":
                    items = [i for i in items if _customer_assigned_to_employee(i, emp)]
                elif name in {"projects", "appointments", "documents", "bookings", "gst", "itr", "tds", "roc", "invoices", "tickets", "payments"}:
                    scoped = []
                    for i in items:
                        if name == "documents":
                            allowed = await _document_visible_to_user(i, user)
                        else:
                            allowed = await _employee_can_access_record(i, user)
                        if allowed:
                            scoped.append(i)
                    items = scoped
            if user.get("role") == "agent" and name in AGENT_SCOPED_COLLECTIONS:
                agent_name = await _agent_name(user)
                agent_id = (user.get("meta") or {}).get("agent_id")
                items = [i for i in items if
                         (agent_id and i.get("agent_id") == agent_id) or
                         (agent_name and (i.get("assigned_agent") == agent_name or i.get("agent") == agent_name or i.get("agent_name") == agent_name))]
                if name == "commissions" and agent_name:
                    items = [i for i in items if not i.get("agent_id") or i.get("agent_id") == agent_id]
                if name == "documents":
                    items = [i for i in items if await _document_visible_to_user(i, user)]
            if is_customer_scoped and user.get("role") == "customer":
                if name == "customers":
                    profile = await _customer_profile(user)
                    items = [profile] if profile else []
                else:
                    booking_ids = await _owned_booking_ids(user) if name in {"invoices", "payments"} else set()
                    items = [i for i in items if _owns_booking(i, user) or _owns_via_booking(i, booking_ids)]
            if customer_id:
                # Narrowing filter applied AFTER role scoping, so it can only reduce what the caller may already see.
                items = [i for i in items if i.get("customer_id") == customer_id or (name == "customers" and i.get("id") == customer_id)]
            if search:
                s = search.lower()
                items = [i for i in items if any(s in str(v).lower() for v in i.values())]
            if status and status != "all":
                items = [i for i in items if str(i.get("status", "")).lower() == status.lower()]
            if name == "documents":
                # Document bytes are returned only by GET /documents/{id}; list responses stay lightweight.
                for doc in items:
                    doc.pop("file_data", None)
                # Resolve the customer's real name from the customer record (single source of truth);
                # nothing is duplicated into the document collection.
                cust_ids = list({d.get("customer_id") for d in items if d.get("customer_id")})
                cust_names = {}
                if cust_ids:
                    for c in await db["erp_customers"].find({"id": {"$in": cust_ids}}, {"_id": 0, "id": 1, "business_name": 1, "name": 1, "owner": 1}).to_list(None):
                        cust_names[c["id"]] = c.get("business_name") or c.get("name") or c.get("owner") or ""
                for doc in items:
                    doc["customer_name"] = cust_names.get(doc.get("customer_id")) or ""
            if name == "invoices":
                enriched = []
                for inv in items:
                    state = await _invoice_financial_state(inv)
                    inv.update({"paid_amount": state["paid_amount"], "balance": state["balance"], "payment_status": state["payment_status"]})
                    enriched.append(inv)
                items = enriched
            if name == "customers":
                items = await _enrich_customer_financials(items)
            total = len(items)
            start = (page - 1) * page_size
            items = items[start:start + page_size]
            return _ok(items, pagination={"page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size})

        @router.get(f"/{name}/{{item_id}}", name=f"get_{name}")
        async def get_item(item_id: str, user: dict = Depends(get_current_user)):
            await _ensure_customer_meta(user)
            item = await db[coll].find_one({"id": item_id}, {"_id": 0})
            if not item:
                raise HTTPException(status_code=404, detail=f"{name[:-1]} not found")
            if user.get("role") not in READ_ROLES.get(name, {"admin"}):
                raise HTTPException(status_code=403, detail=f"Your role is not permitted to view {name}")
            if name == "agents" and user.get("role") != "admin":
                if str(item.get("status") or "Active").lower() != "active":
                    raise HTTPException(status_code=404, detail="agent not found")
                return _ok(_public_agent(item))
            if user.get("role") == "employee" and (is_personal or name == "employees"):
                emp = await _employee_record_for_user(user)
                own_id = emp["id"] if emp else None
                owner_id = item.get("employee_id") if is_personal else item.get("id")
                if not own_id or owner_id != own_id:
                    raise HTTPException(status_code=403, detail="You can only view your own records")
            elif name == "employees" and user.get("role") not in ("admin",):
                raise HTTPException(status_code=403, detail="You are not permitted to view this employee")
            elif user.get("role") == "employee" and name in {"customers", "projects", "appointments", "documents", "bookings", "gst", "itr", "tds", "roc", "invoices", "tickets", "payments"}:
                if name == "customers":
                    emp = await _employee_record_for_user(user)
                    allowed = _customer_assigned_to_employee(item, emp)
                elif name == "documents":
                    allowed = await _document_visible_to_user(item, user)
                else:
                    allowed = await _employee_can_access_record(item, user)
                if not allowed:
                    raise HTTPException(status_code=404, detail=f"{name[:-1].title()} not found")
            elif user.get("role") == "agent" and name == "documents":
                if not await _document_visible_to_user(item, user):
                    raise HTTPException(status_code=404, detail="Document not found")
            elif user.get("role") == "customer" and name == "documents":
                # Canonical ownership: document.customer_id must equal the logged-in
                # customer's ERP customer id (user.meta.customer_id). Never leak file_data.
                if not await _document_visible_to_user(item, user):
                    raise HTTPException(status_code=404, detail="Document not found")
            elif user.get("role") == "agent" and name in AGENT_SCOPED_COLLECTIONS:
                agent_name = await _agent_name(user)
                agent_id = (user.get("meta") or {}).get("agent_id")
                if not ((agent_id and item.get("agent_id") == agent_id) or (agent_name and (item.get("assigned_agent") == agent_name or item.get("agent") == agent_name or item.get("agent_name") == agent_name))):
                    raise HTTPException(status_code=404, detail=f"{name[:-1].title()} not found")
            elif name == "customers" and user.get("role") == "customer":
                profile = await _customer_profile(user)
                if not profile or profile.get("id") != item.get("id"):
                    raise HTTPException(status_code=404, detail="Customer not found")
            elif is_customer_scoped and user.get("role") == "customer" and not _owns_booking(item, user):
                if not (name in {"invoices", "payments"} and _owns_via_booking(item, await _owned_booking_ids(user))):
                    raise HTTPException(status_code=404, detail=f"{name[:-1].title()} not found")
            if name == "customers":
                enriched = await _enrich_customer_financials([item])
                item = enriched[0]
            elif name == "invoices":
                state = await _invoice_financial_state(item)
                item.update({"paid_amount": state["paid_amount"], "balance": state["balance"], "payment_status": state["payment_status"]})
            return _ok(item)

        async def _attach_customer_id(body: dict, user: dict):
            if name not in {"customers", "bookings", "projects", "invoices", "documents", "tickets", "payments", "gst", "itr", "tds", "roc"}:
                return
            if user.get("role") == "customer":
                customer = await _customer_record_for_user(user)
                if not customer:
                    raise HTTPException(status_code=404, detail="No customer profile is linked to this account")
                body["customer_id"] = customer["id"]
                return
            if body.get("customer_id"):
                resolved = await _resolve_customer_id(body.get("customer_id"))
                if not resolved:
                    raise HTTPException(status_code=422, detail="Invalid customer_id")
                body["customer_id"] = resolved
                return
            customer_name = body.get("customer") or body.get("client") or body.get("client_name") or body.get("company") or body.get("business_name")
            if customer_name:
                resolved = await _resolve_customer_id(name=customer_name)
                if resolved:
                    body["customer_id"] = resolved

        def _normalize_invoice(body: dict, existing: dict = None):
            if name != "invoices":
                return
            taxable = float(body.get("taxable", (existing or {}).get("taxable", 0)) or 0)
            discount = float(body.get("discount", (existing or {}).get("discount", 0)) or 0)
            rate = float(body.get("rate", (existing or {}).get("rate", 18)) or 18)
            if taxable < 0 or discount < 0 or discount > taxable or rate < 0 or rate > 100:
                raise HTTPException(status_code=400, detail="Invalid taxable amount, discount, or GST rate")
            net = round(taxable - discount, 2)
            gstin = body.get("gst_number", (existing or {}).get("gst_number", ""))
            # Same-state vs interstate is represented by the existing `istate` flag;
            # default to CGST/SGST for local invoices.
            if body.get("istate", (existing or {}).get("istate", False)):
                cgst = sgst = 0; igst = round(net * rate / 100, 2)
            else:
                cgst = round(net * (rate / 2) / 100, 2); sgst = cgst; igst = 0
            total = round(net + cgst + sgst + igst, 2)
            body.update({"taxable": taxable, "discount": discount, "rate": rate, "cgst": cgst, "sgst": sgst, "igst": igst, "total": total})
            if not existing:
                body.setdefault("paid_amount", 0)
                body.setdefault("balance", total)
                body.setdefault("payment_status", "Pending")

        @router.post(f"/{name}", name=f"create_{name}")
        async def create_item(body: dict = Body(...), user: dict = Depends(get_current_user)):
            guard_write(user)
            await _attach_customer_id(body, user)
            await _normalize_relationships(name, body)
            if name == "documents" and user.get("role") == "employee":
                # Employees may only attach documents to customers assigned to them (server-side).
                if not await _employee_can_access_customer(body.get("customer_id"), user):
                    raise HTTPException(status_code=403, detail="You can only add documents for customers assigned to you")
            if name == "documents":
                if user.get("role") in ("admin", "super_admin", "superadmin") and not body.get("customer_id"):
                    raise HTTPException(status_code=422, detail="A valid customer is required to upload a document.")
                if body.get("file_data") and not body.get("file_name"):
                    # Customer portal uploads send the original file name as `name`.
                    body["file_name"] = body.get("name") or body.get("title")
                _validate_document_upload(body, user)
                body["name"] = body.get("name") or body.get("file_name")
                if body.get("file_data") and not body.get("size"):
                    body["size"] = f"{max(1, round(int(body.get('file_size') or 0) / 1024))} KB"
                if body.get("file_hash") and not body.get("booking_id"):
                    dup = await db[coll].find_one({"customer_id": body["customer_id"], "file_hash": body["file_hash"], "booking_id": {"$in": [None, ""]}}, {"_id": 0, "id": 1, "file_name": 1})
                    if dup:
                        raise HTTPException(status_code=409, detail=f"{body.get('file_name') or 'This file'} has already been uploaded for this customer.")
                body.setdefault("title", body.get("doc_type") or body.get("file_name") or "Document")
                body.setdefault("uploaded_by", user.get("name") or user.get("email") or "NTAXCO Admin")
                body.setdefault("uploaded_by_role", user.get("role") or "admin")
                body.setdefault("uploaded_date", _ist_now().isoformat())
                if body.get("file_data"):
                    body["file_size"] = int(body.get("file_size") or 0)
            _normalize_invoice(body)
            if name == "site-images":
                _validate_site_image(body)
                body.setdefault("status", "Active")
                body["created_at"] = datetime.now(timezone.utc).isoformat()
            emp = None
            if is_personal and user.get("role") == "employee":
                emp = await _employee_record_for_user(user)
                if not emp:
                    raise HTTPException(status_code=404, detail="No employee profile is linked to your account yet. Contact your admin.")
                # Employees may only ever create records under their own identity.
                body["employee_id"] = emp["id"]
                body["employee_name"] = emp.get("name")
                if name == "leaves":
                    body["status"] = "Pending"
                if name == "attendance":
                    # This is a Check In. Never trust a client-supplied date/time —
                    # always stamp with server-side IST time so it can't be spoofed
                    # and can't drift from the server's own duplicate-check below.
                    today = _ist_today_str()
                    existing_today = await db[coll].find_one(
                        {"employee_id": emp["id"], "date": today}, {"_id": 0}
                    )
                    if existing_today:
                        # Already checked in today — this is not an error condition,
                        # just return the existing record so the frontend can show
                        # the correct state instead of surfacing a failure.
                        return _ok(existing_today, message="You have already checked in today")
                    body["date"] = today
                    body["check_in"] = _ist_time_str()
                    body["check_out"] = "-"
                    body["hours"] = "-"
                    body.setdefault("status", "Present")

            if name == "projects" and user.get("role") == "employee":
                # Employee-created projects are automatically owned by the logged-in employee.
                # The employee cannot create a project for another employee.
                emp = emp or await _employee_record_for_user(user)
                if not emp:
                    raise HTTPException(status_code=404, detail="No employee profile is linked to your account yet. Contact your admin.")
                body["employee_id"] = emp["id"]
                body["employee_name"] = emp.get("name")
                body["assigned_employee"] = emp.get("name")
                body.setdefault("status", "Pending")
                body.setdefault("progress", 0)

            if name == "projects" and user.get("role") == "agent":
                # Agent-created projects are automatically assigned to the logged-in agent.
                agent_id = (user.get("meta") or {}).get("agent_id")
                agent_name = await _agent_name(user)
                if not agent_name:
                    raise HTTPException(status_code=404, detail="No agent profile is linked to your account yet. Contact your admin.")
                resolved_agent_id = await _resolve_agent_id(agent_id, name=agent_name)
                if not resolved_agent_id:
                    raise HTTPException(status_code=404, detail="No agent profile is linked to your account yet. Contact your admin.")
                body["agent_id"] = resolved_agent_id
                body["assigned_agent"] = agent_name
                body["agent"] = agent_name
                body.setdefault("status", "Pending")
                body.setdefault("progress", 0)

            if name == "payments":
                # Employee is OPTIONAL on a customer payment / payment term. Blank or whitespace-only
                # employee fields are dropped so no empty placeholder is stored and nothing downstream
                # treats them as an assignment. A real, supplied value is preserved untouched; no
                # employee is ever invented.
                for _emp_key in ("employee_id", "employee_name"):
                    if _emp_key in body and str(body.get(_emp_key) or "").strip() == "":
                        body.pop(_emp_key, None)
                # Payments are authoritative financial events. When linked to an
                # invoice, validate the amount and synchronize the invoice in the
                # same request; duplicate IDs/references are rejected.
                try:
                    amount = round(float(body.get("total") or body.get("amount") or 0), 2)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Payment amount must be a valid number")
                if amount != amount or amount in (float("inf"), float("-inf")):
                    raise HTTPException(status_code=400, detail="Payment amount must be a valid number")
                if amount <= 0:
                    raise HTTPException(status_code=400, detail="Payment amount must be greater than zero")
                payment_id = str(body.get("payment_id") or body.get("id") or "").strip()
                if payment_id and await db[coll].find_one({"$or": [{"id": payment_id}, {"payment_id": payment_id}]}):
                    raise HTTPException(status_code=409, detail="A payment with this Payment ID already exists")
                invoice_no = str(body.get("invoice_no") or "").strip()
                term_name_input = str(body.get("term_name") or body.get("term") or "").strip()
                # Customer installment terms are first-class payments and do not
                # require an invoice/booking. They must, however, be attached to
                # a real customer so billing and Revenue can resolve ownership.
                if term_name_input and not invoice_no and not body.get("customer_id"):
                    raise HTTPException(status_code=400, detail="customer_id is required for a customer payment term")
                if invoice_no:
                    invoice = await db["erp_invoices"].find_one({"$or": [{"invoice_no": invoice_no}, {"id": invoice_no}]}, {"_id": 0})
                    if not invoice:
                        raise HTTPException(status_code=404, detail="Invoice not found")
                    state = await _invoice_financial_state(invoice)
                    if state["payment_status"] == "Cancelled":
                        raise HTTPException(status_code=400, detail="Cancelled invoices cannot receive payments")
                    if amount > state["balance"] + 0.009:
                        raise HTTPException(status_code=400, detail=f"Payment exceeds invoice balance of ₹{state['balance']:.2f}")
                    ref = str(body.get("reference_no") or "").strip()
                    if ref and await db[coll].find_one({"invoice_no": invoice.get("invoice_no"), "reference_no": ref}):
                        raise HTTPException(status_code=409, detail="This payment reference is already recorded for the invoice")
                    body["invoice_no"] = invoice.get("invoice_no") or invoice.get("id")
                    body["amount"] = amount
                    body["total"] = amount
                    body.setdefault("customer_id", invoice.get("customer_id"))
                    body.setdefault("customer", invoice.get("customer"))
                    body.setdefault("booking_id", invoice.get("booking_id"))
                body["amount"] = amount
                body["total"] = amount
                body.setdefault("status", "Completed")
                if body["status"] not in {"Completed", "Paid", "Partial", "Pending", "Failed"}:
                    raise HTTPException(status_code=400, detail="Invalid payment status")
                # Duplicate protection (beyond Payment ID / invoice+reference above):
                # 1) a payment reference can only be recorded once per customer;
                # 2) an identical payment re-submitted within seconds (double click / retry) returns the
                #    payment that already exists instead of recording it a second time.
                owner_cid = body.get("customer_id")
                ref_no = str(body.get("reference_no") or "").strip()
                if ref_no and owner_cid and await db[coll].find_one({"customer_id": owner_cid, "reference_no": ref_no}):
                    raise HTTPException(status_code=409, detail="This payment reference is already recorded for the customer")
                now_dt = datetime.now(timezone.utc)
                crid = str(body.get("client_request_id") or "").strip()[:100]
                if crid and owner_cid:
                    prior_payment = await db[coll].find_one({"customer_id": owner_cid, "client_request_id": crid}, {"_id": 0})
                    if prior_payment:
                        return _ok(prior_payment, message="Payment was already recorded")
                    body["client_request_id"] = crid
                else:
                    body.pop("client_request_id", None)
                recent = await db[coll].find_one({
                    "customer_id": owner_cid, "invoice_no": body.get("invoice_no"), "amount": amount, "status": body["status"],
                    "payment_method": body.get("payment_method"), "payment_date": body.get("payment_date"),
                    "reference_no": body.get("reference_no"),
                    "term_name": str(body.get("term_name") or body.get("term") or "").strip(),
                    "recorded_at": {"$gte": (now_dt - timedelta(seconds=15)).isoformat()},
                }, {"_id": 0})
                if recent:
                    return _ok(recent, message="Payment was already recorded")
                # Customer-installment fields are optional for legacy payments but validated when supplied.
                body.setdefault("term_name", str(body.get("term_name") or body.get("term") or "").strip())
                body.setdefault("transaction_reference", str(body.get("transaction_reference") or body.get("reference_no") or "").strip())
                body["reference_no"] = body.get("transaction_reference") or body.get("reference_no") or ""
                body.setdefault("notes", str(body.get("notes") or "").strip())
                body.setdefault("payment_date", _ist_today_str())
                if body.get("term_name") and not body.get("term_name").strip():
                    raise HTTPException(status_code=400, detail="Payment term is required")
                try:
                    datetime.fromisoformat(str(body["payment_date"])[:10])
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Payment date must be a valid date")
                if owner_cid and not invoice_no:
                    if not await db["erp_customers"].find_one({"id": str(owner_cid)}, {"_id": 0, "id": 1}):
                        raise HTTPException(status_code=404, detail="Customer not found")
                    billing_state = (await _billing_for_customers([str(owner_cid)]))[str(owner_cid)]
                    # The cap protects invoiced/booked amounts. A customer billed only through payment terms
                    # (no invoices/bookings yet) can always receive a further term.
                    if body["status"] in {"Completed", "Paid", "Partial"} and float(billing_state.get("invoice_billed") or 0) > 0 and amount > float(billing_state.get("outstanding") or 0) + 0.009:
                        raise HTTPException(status_code=400, detail="Payment amount cannot exceed the outstanding balance.")
                body["recorded_at"] = now_dt.isoformat()
            new_password_hash = None
            if name == "employees":
                # Optional password: never persisted on the erp_employees profile
                # itself — only used to provision/update the real login account below.
                raw_password = body.pop("password", None)
                if raw_password:
                    if len(str(raw_password)) < 8:
                        raise HTTPException(status_code=422, detail="Password must be at least 8 characters long.")
                    new_password_hash = _hash_password(str(raw_password))
            if name in {"agents", "customers"}:
                _validate_percentage(body, "commission_percentage" if name == "agents" else "agent_commission_percentage")
            new_id = body.get("id")
            if name == "employees":
                # Keep the human-entered Employee ID (emp_id) as the canonical id
                # so it lines up with the ID shown in the UI and used for auth linking.
                candidate = body.get("emp_id") or body.get("id")
                if candidate and not await db[coll].find_one({"id": candidate}):
                    new_id = candidate
                else:
                    new_id = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
                body["emp_id"] = new_id
            elif not new_id or await db[coll].find_one({"id": new_id}):
                new_id = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
            body["id"] = new_id
            if name == "bookings" and not body.get("booking_no"):
                body["booking_no"] = new_id  # one booking ID everywhere (website, API, admin, notifications)
            if name == "payments":
                body.setdefault("payment_id", new_id)

            if name == "bookings":
                body.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                if user.get("role") == "customer":
                    body["status"] = "Pending"
                    body["payment_status"] = "Pending"
                else:
                    body.setdefault("status", "Pending")
                    if body.get("status") not in BOOKING_STATUSES:
                        raise HTTPException(status_code=400, detail="Invalid booking status")
                    body.setdefault("payment_status", "Pending")
            if name == "bookings":
                # Idempotent create: a retried/double-submitted request carrying
                # the same client_request_id returns the booking that already
                # exists instead of creating a second booking + notification.
                crid = body.get("client_request_id")
                if crid is not None:
                    crid = str(crid).strip()[:100]
                    if crid:
                        body["client_request_id"] = crid
                        prior = await db[coll].find_one({"customer_id": body.get("customer_id"), "client_request_id": crid}, {"_id": 0})
                        if prior:
                            return _ok(prior, message="Booking already created")
                    else:
                        body.pop("client_request_id", None)
            if name == "customers":
                if body.get("status") in WORKFLOW_STATUSES:
                    body.setdefault("filing_status", body["status"])
                    body["status"] = "Active"
                body.setdefault("status", "Active")
                body.setdefault("filing_status", "Pending")
            try:
                await db[coll].insert_one({**body})
            except Exception as exc:
                if name == "bookings" and body.get("client_request_id") and ("E11000" in str(exc) or "duplicate" in str(exc).lower()):
                    prior = await db[coll].find_one({"customer_id": body.get("customer_id"), "client_request_id": body["client_request_id"]}, {"_id": 0})
                    if prior:
                        return _ok(prior, message="Booking already created")
                logger.exception("Could not save %s record", name)
                raise HTTPException(status_code=503, detail="Database error while saving the record. Nothing was created.") from exc
            body.pop("_id", None)
            if name == "bookings" and body.get("customer_id") and body.get("service_id"):
                # A booking is the source event for customer/service
                # synchronization. Keep the canonical customer account and
                # attach the service without creating another customer.
                service_doc = await db["erp_services"].find_one({"id": body["service_id"]}, {"_id": 0, "category": 1, "name": 1})
                await db["erp_customers"].update_one(
                    {"id": body["customer_id"]},
                    {"$set": {"service_id": body["service_id"], "service_type": (service_doc or {}).get("category") or (service_doc or {}).get("name") or body.get("service")}}
                )
                synced_customer = await db["erp_customers"].find_one({"id": body["customer_id"]}, {"_id": 0})
                await _sync_customer_service_records(synced_customer or {})
                await reconcile_customer_status(db, body["customer_id"])
            if name == "invoices":
                synced_invoice = await _sync_invoice_financials(new_id)
                if synced_invoice:
                    body.update({k: synced_invoice.get(k) for k in ("paid_amount", "balance", "payment_status")})
            if name == "customers":
                await _sync_customer_gst_record(body)
                await _sync_customer_service_records(body)
            if name == "payments" and body.get("invoice_no") and body.get("status") in {"Completed", "Paid", "Partial"}:
                invoice = await db["erp_invoices"].find_one({"$or": [{"invoice_no": body["invoice_no"]}, {"id": body["invoice_no"]}]}, {"_id": 0})
                if invoice:
                    try:
                        synced = await _sync_invoice_financials(invoice["id"])
                        await _sync_booking_payment_status(synced or invoice)
                    except Exception:
                        await db[coll].delete_one({"id": new_id})
                        raise HTTPException(status_code=500, detail="Payment could not be synchronized with the invoice")
            await _audit(user, "create", name, new_id, body)

            if name == "employees":
                # Provision / update the real auth account so this employee can log in.
                await _sync_employee_user(body, password_hash=new_password_hash)
            if name == "leaves" and user.get("role") == "employee":
                try:
                    await _notify("admin", "New leave request", f"{body.get('employee_name', 'An employee')} requested {body.get('leave_type', 'leave')} ({body.get('from_date')} to {body.get('to_date')}).", "Attendance", "warning",
                                  kind=KIND_LEAVE_REQUEST, event_key=f"leave-created:{new_id}", leave_id=new_id, employee_id=body.get("employee_id"), customer_name=body.get("employee_name"))
                except Exception:
                    logger.exception("Leave %s saved but its notification could not be created", new_id)
            if name == "bookings":
                # Persist actual files uploaded during Service Booking as canonical
                # erp_documents records linked to customer + booking + service.
                uploaded_docs = body.get("documents") if isinstance(body.get("documents"), list) else []
                if uploaded_docs:
                    cust_id = body.get("customer_id")
                    service_id = body.get("service_id")
                    for raw in uploaded_docs:
                        if not isinstance(raw, dict) or not raw.get("file_data"):
                            continue
                        doc = {
                            "id": f"DOC-{uuid.uuid4().hex[:12].upper()}",
                            "customer_id": cust_id,
                            "booking_id": new_id,
                            "service_id": service_id,
                            "file_name": str(raw.get("file_name") or "document"),
                            "file_type": str(raw.get("file_type") or ""),
                            "mime_type": str(raw.get("mime_type") or "application/octet-stream"),
                            "file_size": int(raw.get("file_size") or 0),
                            "file_data": raw.get("file_data"),
                            "uploaded_by": user.get("name") or "Customer Upload",
                            "uploaded_by_role": user.get("role") or "customer",
                            "uploaded_date": datetime.now(timezone.utc).isoformat(),
                            "status": "Uploaded",
                            "assigned_employee": body.get("assigned_employee") or "",
                            "assigned_agent": body.get("assigned_agent") or "",
                            "agent_id": body.get("agent_id"),
                        }
                        _validate_document_upload(doc, user)
                        await db["erp_documents"].insert_one(doc)
                # The booking is already persisted. Notification problems are
                # logged and must never fail or corrupt the booking itself.
                try:
                    cust_rec = await db["erp_customers"].find_one({"id": body.get("customer_id")}, {"_id": 0}) or {}
                    cust = cust_rec.get("business_name") or cust_rec.get("owner") or body.get("customer", "A customer")
                    svc = body.get("service", "a service")
                    agent = body.get("assigned_agent", "your consultant"); prio = body.get("priority", "Low")
                    links = dict(customer_id=body.get("customer_id"), booking_id=new_id, service_id=body.get("service_id"),
                                 customer_name=cust, service_name=svc)
                    await _notify("admin", f"New booking {new_id} received", f"{cust} requested {svc}. Priority: {prio}.", "Bookings", "warning", priority=prio,
                                  kind=KIND_SERVICE_BOOKING, event_key=f"booking-created:{new_id}", booking_status=body.get("status", "Pending"), **links)
                    agent_user = await _agent_user_by_name(agent)
                    cust_user = await _customer_user_by_id(body.get("customer_id"))
                    await _notify("agent", f"New booking assigned: {new_id}", f"{cust} — {svc}. Due {body.get('due_date') or 'TBD'}.", "Bookings", "warning", user_id=(agent_user or {}).get("id"))
                    await _notify("customer", f"Booking {new_id} submitted", f"{svc} assigned to {agent}. Status: {body.get('status', 'Pending')}.", "Bookings", "information", user_id=(cust_user or {}).get("id"))
                except Exception:
                    logger.exception("Booking %s saved but its notifications could not be created", new_id)
            return _ok(body, message=f"{name[:-1].title()} created")

        @router.put(f"/{name}/{{item_id}}", name=f"update_{name}")
        async def update_item(item_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
            guard_write(user)
            await _ensure_customer_meta(user)
            existing = await db[coll].find_one({"id": item_id}, {"_id": 0})
            if not existing:
                raise HTTPException(status_code=404, detail="Not found")
            if user.get("role") == "employee" and name in {"customers", "bookings", "projects", "appointments", "documents", "gst", "itr", "tds", "roc", "invoices", "tickets", "payments"}:
                if name == "documents":
                    allowed = await _document_visible_to_user(existing, user)
                elif name == "customers":
                    emp = await _employee_record_for_user(user)
                    allowed = _customer_assigned_to_employee(existing, emp)
                else:
                    allowed = await _employee_can_access_record(existing, user)
                if not allowed:
                    raise HTTPException(status_code=404, detail=f"{name[:-1].title()} not found")
            if user.get("role") == "employee" and name in {"bookings", "projects", "appointments", "documents", "gst", "itr", "tds", "roc", "invoices", "tickets", "payments"}:
                # Employees may update only operational fields on records already in
                # their workload; ownership/assignment cannot be transferred.
                body.pop("customer_id", None)
                body.pop("employee_id", None)
                body.pop("employee_name", None)
                body.pop("assigned_employee", None)
                body.pop("assigned_agent", None)
            if user.get("role") == "employee":
                employee_allowed_fields = {
                    "projects": {"status", "progress", "description", "notes", "due_date"},
                    "appointments": {"status", "notes"},
                    "documents": {"status", "notes"},
                    "gst": {"status", "notes", "remarks", "comment"},
                    "itr": {"status", "notes", "remarks", "comment"},
                    "tds": {"status", "notes", "remarks", "comment"},
                    "roc": {"status", "notes", "remarks", "comment"},
                }
                if name in employee_allowed_fields:
                    body = {k: v for k, v in body.items() if k in employee_allowed_fields[name]}
            if name == "documents" and user.get("role") == "employee":
                if not await _document_visible_to_user(existing, user):
                    raise HTTPException(status_code=404, detail="Document not found")
                if body.get("customer_id") and body.get("customer_id") != existing.get("customer_id"):
                    raise HTTPException(status_code=403, detail="You cannot move a document to another customer")
            if name == "bookings":
                # Booking status is Admin-controlled. A customer must never be able to change it,
                # so reject loudly instead of silently dropping the field.
                if user.get("role") == "customer" and "status" in body:
                    raise HTTPException(status_code=403, detail="Only Admin can change a booking's status")
                # The server owns this timestamp; never trust a client-supplied value.
                body.pop("status_updated_at", None)
                if "status" in body:
                    body["status"] = str(body.get("status") or "").strip()
                    if body["status"] not in BOOKING_STATUSES:
                        raise HTTPException(status_code=400, detail=f"Invalid booking status '{body['status']}'. Allowed: {', '.join(sorted(BOOKING_STATUSES))}")
            # A status-only booking update must not re-resolve service/agent links (legacy rows whose links
            # cannot be re-resolved would otherwise fail with 422 even though only the status changes).
            booking_status_only = name == "bookings" and set(body.keys()) <= {"status"}
            if name in {"bookings", "invoices", "payments", "commissions"} and not booking_status_only:
                await _normalize_relationships(name, body, existing)
            _normalize_invoice(body, existing)
            if name == "site-images":
                _validate_site_image(body, partial=True)

            emp = None
            if is_personal and user.get("role") == "employee":
                emp = await _employee_record_for_user(user)
                own_id = emp["id"] if emp else None
                if not own_id or existing.get("employee_id") != own_id:
                    raise HTTPException(status_code=403, detail="You can only update your own records")
                # Employees cannot approve/reject their own leave requests.
                if name == "leaves":
                    body.pop("status", None)
                body["employee_id"] = own_id
                if name == "attendance":
                    # This is a Check Out. Verify the employee actually checked in
                    # first, and always stamp the check-out time server-side rather
                    # than trusting whatever the browser sends.
                    check_in = existing.get("check_in")
                    if not check_in or check_in == "-":
                        raise HTTPException(status_code=400, detail="Please check in first.")
                    if existing.get("check_out") and existing.get("check_out") != "-":
                        # Already checked out today — return the existing record
                        # instead of failing or silently overwriting it.
                        return _ok(existing, message="You have already checked out today")
                    check_out = _ist_time_str()
                    body["check_out"] = check_out
                    body["hours"] = _hours_between(check_in, check_out)
                    body.setdefault("status", existing.get("status") or "Present")

            if name == "bookings" and "status" in body:
                if user.get("role") not in {"admin", "agent"}:
                    body.pop("status", None)
                elif str(body.get("status")) not in BOOKING_STATUSES:
                    raise HTTPException(status_code=400, detail="Invalid booking status")
            if is_customer_scoped and user.get("role") == "customer":
                if not _owns_booking(existing, user):
                    raise HTTPException(status_code=404, detail=f"{name[:-1].title()} not found")
                # Customers cannot re-assign ownership or fake payment/status state via edits.
                body.pop("customer_id", None)
                if name == "bookings":
                    body.pop("payment_status", None)

            if name == "payments":
                amount = round(float(body.get("total") or body.get("amount") or existing.get("total") or 0), 2)
                if amount <= 0:
                    raise HTTPException(status_code=400, detail="Payment amount must be greater than zero")
                body["amount"] = amount
                body["total"] = amount
                body["term_name"] = str(body.get("term_name") or body.get("term") or existing.get("term_name") or existing.get("term") or "").strip()
                body["transaction_reference"] = str(body.get("transaction_reference") or body.get("reference_no") or existing.get("transaction_reference") or existing.get("reference_no") or "").strip()
                body["reference_no"] = body["transaction_reference"]
                if body["reference_no"] and existing.get("customer_id"):
                    duplicate_ref = await db[coll].find_one({"customer_id": existing.get("customer_id"), "reference_no": body["reference_no"], "id": {"$ne": item_id}}, {"_id": 0, "id": 1})
                    if duplicate_ref:
                        raise HTTPException(status_code=409, detail="This payment reference is already recorded for the customer")
                if "status" in body and body["status"] not in {"Completed", "Paid", "Partial", "Pending", "Failed"}:
                    raise HTTPException(status_code=400, detail="Invalid payment status")
                body.setdefault("notes", existing.get("notes") or "")
                if body.get("payment_date"):
                    try:
                        datetime.fromisoformat(str(body["payment_date"])[:10])
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=400, detail="Payment date must be a valid date")
                if body.get("status") in {"Completed", "Paid", "Partial"} and not (body.get("invoice_no") or existing.get("invoice_no")) and existing.get("customer_id"):
                    billing_state = (await _billing_for_customers([str(existing.get("customer_id"))]))[str(existing.get("customer_id"))]
                    old_received = float(existing.get("total") or existing.get("amount") or 0) if existing.get("status") in {"Completed", "Paid", "Partial"} else 0
                    allowed = float(billing_state.get("outstanding") or 0) + old_received
                    if float(billing_state.get("invoice_billed") or 0) > 0 and amount > allowed + 0.009:
                        raise HTTPException(status_code=400, detail="Payment amount cannot exceed the outstanding balance.")
                if body.get("invoice_no") or existing.get("invoice_no"):
                    inv_no = body.get("invoice_no") or existing.get("invoice_no")
                    invoice = await db["erp_invoices"].find_one({"$or": [{"invoice_no": inv_no}, {"id": inv_no}]}, {"_id": 0})
                    if invoice and body.get("status", existing.get("status")) in {"Completed", "Paid"}:
                        other = await _invoice_financial_state(invoice)
                        old_amount = float(existing.get("total") or existing.get("amount") or 0) if existing.get("status") in {"Completed", "Paid"} else 0
                        allowed_balance = other["balance"] + old_amount
                        if amount > allowed_balance + 0.009:
                            raise HTTPException(status_code=400, detail=f"Payment exceeds invoice balance of ₹{allowed_balance:.2f}")

            body.pop("_id", None); body.pop("id", None)
            update_password_hash = None
            if name == "employees":
                raw_password = body.pop("password", None)
                if raw_password:
                    if len(str(raw_password)) < 8:
                        raise HTTPException(status_code=422, detail="Password must be at least 8 characters long.")
                    update_password_hash = _hash_password(str(raw_password))
            if name in {"agents", "customers"}:
                _validate_percentage(body, "commission_percentage" if name == "agents" else "agent_commission_percentage")
            workflow_change = None
            if name == "customers":
                # Account status and workflow status are different fields: a workflow value sent as
                # "status" is routed to filing_status; the category (service_type) is never touched.
                if body.get("status") in WORKFLOW_STATUSES:
                    body["filing_status"] = body.pop("status")
                new_fs = body.get("filing_status")
                if new_fs in WORKFLOW_STATUSES and new_fs != existing.get("filing_status"):
                    workflow_change = new_fs
            if name == "bookings" and body.get("status") and body.get("status") != existing.get("status"):
                body["status_updated_at"] = datetime.now(timezone.utc).isoformat()
            res = await db[coll].update_one({"id": item_id}, {"$set": body})
            if res.matched_count == 0:
                raise HTTPException(status_code=404, detail="Not found")
            item = await db[coll].find_one({"id": item_id}, {"_id": 0})
            if workflow_change:
                for b in await apply_customer_workflow_status(db, item, workflow_change):
                    await _notify_booking_status_change(b, b["status"], "Admin")
                item = await db[coll].find_one({"id": item_id}, {"_id": 0})
            if name == "bookings" and body.get("status") and body.get("status") != existing.get("status"):
                await reconcile_customer_status(db, item.get("customer_id"))
            if name == "bookings" and body.get("status") == "Confirmed" and user.get("role") in {"admin", "agent"}:
                # Booking ACCEPTED -> create exactly one Task Board task (idempotent, safe to retry).
                try:
                    await _auto_create_booking_task(item, user)
                except Exception as exc:
                    logger.exception("Automatic task creation failed for booking %s", item_id)
                    # Keep booking + task consistent: roll the acceptance back so the Admin can simply retry.
                    await db[coll].update_one({"id": item_id}, {"$set": {"status": existing.get("status"), "status_updated_at": existing.get("status_updated_at")}})
                    await reconcile_customer_status(db, item.get("customer_id"))
                    raise HTTPException(status_code=500, detail="Booking could not be accepted because the Task Board task could not be created. Nothing was changed; please try again.") from exc
            if name == "bookings" and not booking_status_only and item.get("customer_id") and item.get("service_id"):
                service_doc = await db["erp_services"].find_one({"id": item["service_id"]}, {"_id": 0, "category": 1, "name": 1})
                await db["erp_customers"].update_one(
                    {"id": item["customer_id"]},
                    {"$set": {"service_id": item["service_id"], "service_type": (service_doc or {}).get("category") or (service_doc or {}).get("name") or item.get("service")}}
                )
                synced_customer = await db["erp_customers"].find_one({"id": item["customer_id"]}, {"_id": 0})
                await _sync_customer_service_records(synced_customer or {})
            if name == "customers":
                await _sync_customer_dependents(item_id, item)
                await _sync_customer_gst_record(item)
                await _sync_customer_service_records(item)
            if name == "agents":
                await _sync_agent_dependents(item_id, item)
            if name == "services":
                await _sync_service_dependents(item_id, item)
            if name == "invoices":
                item = await _sync_invoice_financials(item_id) or item
                await _sync_invoice_dependents(item)
                await _sync_booking_payment_status(item)
            if name == "payments":
                invoice_refs = {str(x) for x in (existing.get("invoice_no"), item.get("invoice_no")) if x}
                for inv_ref in invoice_refs:
                    invoice = await db["erp_invoices"].find_one({"$or": [{"invoice_no": inv_ref}, {"id": inv_ref}]}, {"_id": 0})
                    if invoice:
                        synced = await _sync_invoice_financials(invoice["id"])
                        await _sync_booking_payment_status(synced or invoice)
            if name == "bookings" and body.get("status") and body.get("status") != existing.get("status"):
                await _audit(user, "booking_status_change", name, item_id, {
                    "booking_id": item_id, "customer_id": item.get("customer_id"),
                    "old_status": existing.get("status"), "new_status": body.get("status"),
                    "changed_by": user.get("id"), "changed_by_name": user.get("name"), "changed_by_role": user.get("role"),
                    "status_updated_at": body.get("status_updated_at"),
                })
            else:
                await _audit(user, "update", name, item_id, body)

            if name == "employees":
                await _sync_employee_user(item, password_hash=update_password_hash)
            if name == "leaves" and body.get("status") and user.get("role") == "admin":
                st = body.get("status")
                await _notify("employee", f"Leave request {st.lower()}", f"Your {item.get('leave_type', 'leave')} request ({item.get('from_date')} to {item.get('to_date')}) was {st.lower()}.", "Attendance", "information" if st == "Approved" else "warning", kind=KIND_LEAVE_STATUS, leave_id=item_id)
            if name == "bookings" and body.get("status") and body.get("status") != existing.get("status"):
                await _notify_booking_status_change(item, body.get("status"), "Admin" if user.get("role") == "admin" else "Consultant")
            return _ok(item, message=f"{name[:-1].title()} updated")

        if name == "customers":
            @router.get("/customers/{item_id}/deletion-eligibility", name="customer_deletion_eligibility")
            async def customer_deletion_eligibility(item_id: str, user: dict = Depends(get_current_user)):
                """Return live, server-computed deletion eligibility without exposing unrelated customer data."""
                if user.get("role") not in {"admin", "super_admin", "superadmin"}:
                    raise HTTPException(status_code=403, detail="Admin access required")
                customer = await db["erp_customers"].find_one({"id": item_id}, {"_id": 0})
                if not customer or customer.get("is_deleted"):
                    raise HTTPException(status_code=404, detail="Active customer not found")
                reasons = []
                bookings = await db["erp_bookings"].find({"customer_id": item_id}, {"_id": 0, "id": 1, "status": 1, "service": 1}).to_list(None)
                terminal = {"completed", "cancelled", "canceled", "closed"}
                for booking in bookings:
                    status_value = str(booking.get("status") or "").strip().lower()
                    if status_value not in terminal:
                        service_name = booking.get("service") or "service booking"
                        reasons.append(f"{service_name} is {booking.get('status') or 'in an unknown status'}.")
                projects = await db["erp_projects"].find({"customer_id": item_id}, {"_id": 0, "status": 1, "name": 1, "project_name": 1}).to_list(None)
                for project in projects:
                    if str(project.get("status") or "").strip().lower() not in terminal:
                        reasons.append(f"Project {project.get('project_name') or project.get('name') or ''} is {project.get('status') or 'in an unknown status'}.".strip())
                invoices = await db["erp_invoices"].find({"customer_id": item_id}, {"_id": 0, "id": 1, "balance": 1, "outstanding": 1, "status": 1, "payment_status": 1}).to_list(None)
                for invoice in invoices:
                    try:
                        balance = float(invoice.get("balance", invoice.get("outstanding", 0)) or 0)
                    except (TypeError, ValueError):
                        balance = 0
                    if balance > 0.009:
                        reasons.append("An invoice has an outstanding balance.")
                        break
                try:
                    outstanding = float(customer.get("outstanding") or 0)
                except (TypeError, ValueError):
                    outstanding = 0
                if outstanding > 0.009:
                    reasons.append("The customer has an outstanding balance.")
                unresolved_payment_statuses = {"pending", "processing", "awaiting payment", "refund pending", "disputed", "under review"}
                payments = await db["erp_payments"].find({"customer_id": item_id}, {"_id": 0, "status": 1}).to_list(None)
                if any(str(payment.get("status") or "").strip().lower() in unresolved_payment_statuses for payment in payments):
                    reasons.append("A payment, refund, or dispute is still unresolved.")
                # Unknown/missing booking statuses are deliberately blocked above; historical financial
                # records are retained by soft deletion and are not cascaded away.
                unique_reasons = list(dict.fromkeys(reasons))
                return _ok({"customer_id": item_id, "eligible": not unique_reasons, "reasons": unique_reasons,
                            "operation": "soft_delete", "booking_count": len(bookings)}, message="Deletion eligibility checked")

        @router.delete(f"/{name}/{{item_id}}", name=f"delete_{name}")
        async def delete_item(item_id: str, user: dict = Depends(get_current_user)):
            guard_write(user)
            await _ensure_customer_meta(user)
            if name == "documents" and user.get("role") not in ("admin", "customer"):
                # Documents can only be removed by Admin (or a customer deleting their own upload, below).
                raise HTTPException(status_code=403, detail="Only an Admin can delete documents")
            if is_personal and user.get("role") == "employee":
                existing = await db[coll].find_one({"id": item_id}, {"_id": 0})
                emp = await _employee_record_for_user(user)
                own_id = emp["id"] if emp else None
                if not existing or not own_id or existing.get("employee_id") != own_id:
                    raise HTTPException(status_code=403, detail="You can only delete your own records")
            if is_customer_scoped and user.get("role") == "customer":
                existing = await db[coll].find_one({"id": item_id}, {"_id": 0})
                if name == "documents" and existing and existing.get("uploaded_by_role") in {"admin", "super_admin", "superadmin"}:
                    raise HTTPException(status_code=403, detail="Customers cannot delete Admin-uploaded documents")
                if not existing or not _owns_booking(existing, user):
                    raise HTTPException(status_code=404, detail=f"{name[:-1].title()} not found")
            if name == "customers":
                if user.get("role") not in {"admin", "super_admin", "superadmin"}:
                    raise HTTPException(status_code=403, detail="Admin access required to remove a customer")
                customer = await db["erp_customers"].find_one({"id": item_id}, {"_id": 0})
                if not customer or customer.get("is_deleted"):
                    raise HTTPException(status_code=404, detail="Active customer not found")
                # Re-run the same eligibility checks on the write request to avoid stale UI/race bypasses.
                bookings = await db["erp_bookings"].find({"customer_id": item_id}, {"_id": 0, "status": 1, "service": 1}).to_list(None)
                terminal = {"completed", "cancelled", "canceled", "closed"}
                blockers = [f"{b.get('service') or 'Service booking'} is {b.get('status') or 'in an unknown status'}." for b in bookings if str(b.get("status") or "").strip().lower() not in terminal]
                projects = await db["erp_projects"].find({"customer_id": item_id}, {"_id": 0, "status": 1, "name": 1, "project_name": 1}).to_list(None)
                blockers.extend([f"Project {x.get('project_name') or x.get('name') or ''} is {x.get('status') or 'in an unknown status'}.".strip() for x in projects if str(x.get("status") or "").strip().lower() not in terminal])
                invoices = await db["erp_invoices"].find({"customer_id": item_id}, {"_id": 0, "balance": 1, "outstanding": 1}).to_list(None)
                for invoice in invoices:
                    try:
                        if float(invoice.get("balance", invoice.get("outstanding", 0)) or 0) > 0.009:
                            blockers.append("An invoice has an outstanding balance.")
                            break
                    except (TypeError, ValueError):
                        blockers.append("An invoice has an invalid balance that requires review.")
                        break
                try:
                    if float(customer.get("outstanding") or 0) > 0.009:
                        blockers.append("The customer has an outstanding balance.")
                except (TypeError, ValueError):
                    blockers.append("The customer balance requires review.")
                payments = await db["erp_payments"].find({"customer_id": item_id}, {"_id": 0, "status": 1}).to_list(None)
                unresolved = {"pending", "processing", "awaiting payment", "refund pending", "disputed", "under review"}
                if any(str(x.get("status") or "").strip().lower() in unresolved for x in payments):
                    blockers.append("A payment, refund, or dispute is still unresolved.")
                blockers = list(dict.fromkeys(blockers))
                if blockers:
                    raise HTTPException(status_code=409, detail={"message": "Customer cannot be removed until all services are completed and financial issues are resolved.", "reasons": blockers})
                # Preserve all bookings, invoices, payments, documents, ledger and audit references.
                # Deactivate the customer rather than cascading a destructive database delete.
                now = datetime.now(timezone.utc).isoformat()
                result = await db["erp_customers"].update_one(
                    {"id": item_id, "is_deleted": {"$ne": True}},
                    {"$set": {"is_deleted": True, "deleted_at": now, "deleted_by": user.get("id"),
                              "deletion_reason": "All service processes completed; admin soft deletion", "status": "Inactive", "account_status": "Inactive"}}
                )
                if result.modified_count == 0:
                    raise HTTPException(status_code=409, detail="Customer was already removed or changed by another administrator")
                # Disable a linked login when the existing auth schema has a customer reference.
                try:
                    await db["users"].update_many(
                        {"$or": [{"customer_id": item_id}, {"meta.customer_id": item_id}], "role": "customer"},
                        {"$set": {"status": "Inactive", "is_disabled": True, "disabled_at": now}}
                    )
                except Exception:
                    logger.exception("Customer %s was soft-deleted but linked login deactivation needs review", item_id)
                await _audit(user, "customer_soft_delete", name, item_id, {
                    "customer_id": item_id, "changed_by": user.get("id"), "changed_by_role": user.get("role"),
                    "deleted_at": now, "operation": "soft_delete", "booking_count": len(bookings)
                })
                return _ok({"id": item_id, "is_deleted": True, "deleted_at": now}, message="Customer removed from active customer management; historical records retained")
            if name == "services":
                # Section 3: a service already connected to customers, invoices,
                # payments or bookings must never be hard-deleted — the historical
                # record (billing, filings, assignments) has to survive. Admins
                # should Activate/Deactivate/Archive it instead (status update),
                # which every consuming module (Customer/Employee/Agent) already
                # reads live from this same service record.
                related = {}
                for label, collection in (
                    ("customers", "erp_customers"), ("bookings", "erp_bookings"),
                    ("invoices", "erp_invoices"), ("payments", "erp_payments"),
                ):
                    related[label] = await db[collection].count_documents({"service_id": item_id})
                if any(related.values()):
                    parts = ", ".join(f"{v} {k}" for k, v in related.items() if v)
                    raise HTTPException(
                        status_code=409,
                        detail=f"This service is linked to {parts}. Deactivate or archive it instead of deleting to preserve historical records.",
                    )
            existing_payment = await db[coll].find_one({"id": item_id}, {"_id": 0}) if name == "payments" else None
            res = await db[coll].delete_one({"id": item_id})
            if res.deleted_count == 0:
                raise HTTPException(status_code=404, detail="Not found")
            if name == "customers":
                # Documents belong to the customer: never leave orphaned files behind.
                await db["erp_documents"].delete_many({"customer_id": item_id})
            if name == "payments" and existing_payment and existing_payment.get("invoice_no"):
                invoice = await db["erp_invoices"].find_one({"$or": [{"invoice_no": existing_payment["invoice_no"]}, {"id": existing_payment["invoice_no"]}]}, {"_id": 0})
                if invoice:
                    synced = await _sync_invoice_financials(invoice["id"])
                    await _sync_booking_payment_status(synced or invoice)
            await _audit(user, "delete", name, item_id)
            if name == "employees":
                await db.users.delete_one({"meta.employee_id": item_id, "role": "employee"})
            return _ok({"id": item_id}, message=f"{name[:-1].title()} deleted")

    for name, (coll, seed, prefix) in COLLECTIONS.items():
        make_crud(name, coll, prefix)

    # ---------------- Public (no-auth) service catalog ----------------
    # The generic `/services` route above requires a logged-in user, which
    # is correct for the portals — but the marketing Customer Site also
    # needs to list live, admin-created services for visitors who have not
    # signed in yet (section 5 "Customer Site: Service list" must reflect
    # whatever Admin just created/edited/deactivated, with no code change
    # and no login required). This mirrors list_items's read-only shape but
    # only exposes active/available services and public-safe fields.
    @router.get("/public/services")
    async def list_public_services():
        items = await db["erp_services"].find({}, {"_id": 0}).to_list(2000)
        visible = [s for s in items if is_active_service_record(s) and is_valid_service_record(s)]
        fields = ("id", "name", "title", "category", "description", "short_description", "price", "discount", "final_price", "image", "banner_image", "icon", "status", "frequency", "pricing_type")
        return _ok([{k: s.get(k) for k in fields if k in s} for s in visible])

    @router.get("/services/{service_id}/stats")
    async def service_stats(service_id: str, user: dict = Depends(get_current_user)):
        """Section 11 'Service Statistics': live counts for one service, driven
        by the same service_id relationships every module already reads —
        no separate/duplicated per-module service list to keep in sync."""
        if user.get("role") not in READ_ROLES.get("services", {"admin"}):
            raise HTTPException(status_code=403, detail="Your role is not permitted to view service statistics")
        service = await db["erp_services"].find_one({"id": service_id}, {"_id": 0})
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        bookings = await db["erp_bookings"].find({"service_id": service_id}, {"_id": 0}).to_list(5000)
        invoices = await db["erp_invoices"].find({"service_id": service_id}, {"_id": 0}).to_list(5000)
        direct_customers = await db["erp_customers"].count_documents({"service_id": service_id})
        booked_customer_ids = {b.get("customer_id") for b in bookings if b.get("customer_id")}
        total_customers = len(booked_customer_ids) if booked_customer_ids or not direct_customers else max(direct_customers, len(booked_customer_ids))
        status_counts = {}
        for b in bookings:
            st = b.get("status") or "Unknown"
            status_counts[st] = status_counts.get(st, 0) + 1
        revenue = round(sum(float(i.get("total") or 0) for i in invoices), 2)
        return _ok({
            "service_id": service_id,
            "customers": total_customers,
            "bookings": len(bookings),
            "invoices": len(invoices),
            "revenue": revenue,
            "status_breakdown": status_counts,
        })

    @router.get("/admin/dashboard")
    async def admin_dashboard(
        start_date: str = Query(None, description="Inclusive dashboard date in YYYY-MM-DD format"),
        end_date: str = Query(None, description="Inclusive dashboard date in YYYY-MM-DD format"),
        user: dict = Depends(get_current_user),
    ):
        """Authoritative Admin Dashboard snapshot.

        This extends the existing dashboard endpoint rather than creating a second
        dashboard API. All sections are derived from the existing ERP collections
        and existing relationship/status conventions. Date filtering is applied
        to date-based analytics only; operational totals remain safe and explicit.
        """
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")

        def parse_day(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value)[:10]).date()
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="Dashboard dates must use YYYY-MM-DD format")

        range_start = parse_day(start_date)
        range_end = parse_day(end_date)
        if range_start and range_end and range_start > range_end:
            raise HTTPException(status_code=422, detail="start_date cannot be after end_date")

        def day_from(*values):
            for value in values:
                if value in (None, ""):
                    continue
                try:
                    return datetime.fromisoformat(str(value).replace("Z", "+00:00")[:10]).date()
                except (TypeError, ValueError):
                    continue
            return None

        def in_range(record, fields):
            if not (range_start or range_end):
                return True
            d = day_from(*(record.get(f) for f in fields))
            if not d:
                return False
            return (not range_start or d >= range_start) and (not range_end or d <= range_end)

        def due_state(due, today=None):
            d = day_from(due)
            if not d:
                return "none"
            today = today or _ist_now().date()
            if d < today:
                return "overdue"
            if d == today:
                return "today"
            if d <= today + timedelta(days=3):
                return "soon"
            return "later"

        async def fetch(name, projection=None, limit=20000):
            return await db[COLLECTIONS.get(name, (f"erp_{name}",))[0]].find({}, projection or {"_id": 0}).to_list(limit)

        # One snapshot of the existing source collections. The dashboard does not
        # introduce any stored totals or duplicate collections.
        customers = await fetch("customers", {"_id": 0})
        employees = await fetch("employees", {"_id": 0})
        agents = await fetch("agents", {"_id": 0})
        projects = await fetch("projects", {"_id": 0})
        invoices = await fetch("invoices", {"_id": 0})
        bookings = await fetch("bookings", {"_id": 0})
        payments = await fetch("payments", {"_id": 0})
        services = await fetch("services", {"_id": 0})
        documents = await fetch("documents", {"_id": 0})
        tasks = await db["erp_tasks"].find({"task_board": True}, {"_id": 0}).to_list(50000)
        compliance_by_kind = {}
        for kind in ("gst", "itr", "tds", "roc"):
            compliance_by_kind[kind] = await db[COLLECTIONS[kind][0]].find({}, {"_id": 0}).to_list(20000)
        compliance = [dict(row, _dashboard_kind=kind) for kind, rows in compliance_by_kind.items() for row in rows]
        notifications = await db["erp_notifications"].find(
            {"$or": [{"role": "all"}, {"role": "admin"}, {"user_id": user.get("id")}]},
            {"_id": 0},
        ).sort("ts", -1).to_list(200)
        audit_logs = await db["erp_audit_logs"].find({}, {"_id": 0}).sort("ts", -1).to_list(100)
        task_activity = await db["erp_task_activity"].find({}, {"_id": 0}).sort("created_at", -1).to_list(100)

        today = _ist_now().date()
        today_str = today.isoformat()

        # Payment map is shared by financial health and service/customer analytics.
        paid_statuses = {"Completed", "Paid", "Partial"}
        paid_by_invoice = {}
        payment_rows = []
        for index, payment in enumerate(payments):
            if str(payment.get("status") or "") not in paid_statuses:
                continue
            amount = float(payment.get("total") or payment.get("amount") or 0)
            if amount <= 0:
                continue
            payment_rows.append(payment)
            # Keep a unique payment identity so a row carrying both invoice_id and
            # invoice_no is never counted twice for the same invoice.
            payment_identity = str(payment.get("id") or payment.get("payment_id") or payment.get("reference_no") or f"row-{index}")
            for key in (payment.get("invoice_id"), payment.get("invoice_no")):
                if key:
                    bucket = paid_by_invoice.setdefault(str(key), {})
                    bucket[payment_identity] = amount

        invoice_states = {}
        for inv in invoices:
            total = max(float(inv.get("total") or 0), 0)
            payment_values = {}
            for invoice_key in (inv.get("id"), inv.get("invoice_no")):
                if invoice_key:
                    payment_values.update(paid_by_invoice.get(str(invoice_key), {}))
            paid = sum(payment_values.values())
            # A legacy invoice marked Paid without a payment row is already part
            # of the existing financial business rule and remains supported.
            if paid <= 0 and inv.get("payment_status") == "Paid":
                paid = total
            paid = min(max(paid, 0), total)
            balance = max(total - paid, 0)
            if str(inv.get("payment_status") or "") == "Cancelled":
                status = "Cancelled"
            elif balance <= 0.009:
                status = "Paid"
            elif day_from(inv.get("due_date")) and day_from(inv.get("due_date")) < today:
                status = "Overdue"
            elif paid > 0:
                status = "Partial"
            else:
                status = "Pending"
            invoice_states[str(inv.get("id") or inv.get("invoice_no"))] = {
                "billed": total, "paid": paid, "balance": balance, "status": status
            }

        # Period-aware records. These use the existing record date fields rather
        # than changing stored timestamps.
        period_customers = [x for x in customers if in_range(x, ("created_at", "createdAt"))]
        period_bookings = [x for x in bookings if in_range(x, ("booking_date", "created_at", "date"))]
        period_invoices = [x for x in invoices if in_range(x, ("invoice_date", "issue_date", "created_at"))]
        period_payments = [x for x in payment_rows if in_range(x, ("payment_date", "recorded_at", "txn_date", "created_at"))]
        period_tasks = [x for x in tasks if in_range(x, ("created_at", "createdAt", "due_date"))]
        period_documents = [x for x in documents if in_range(x, ("uploaded_at", "created_at", "date"))]
        period_compliance = [x for x in compliance if in_range(x, ("created_at", "filed_date", "period", "due_date"))]

        period_service_counts = {}
        for row in period_invoices:
            key = row.get("service") or row.get("service_name") or "Other"
            period_service_counts[key] = period_service_counts.get(key, 0) + 1
        period_payment_counts = {}
        for row in period_payments:
            key = row.get("status") or "Unknown"
            period_payment_counts[key] = period_payment_counts.get(key, 0) + 1
        period_booking_counts = {}
        for row in period_bookings:
            key = row.get("status") or "Unknown"
            period_booking_counts[key] = period_booking_counts.get(key, 0) + 1

        # Financial health uses invoice financial state as the source of truth.
        financial_invoices = period_invoices if (range_start or range_end) else invoices
        total_billed = total_paid = overdue_amount = 0.0
        paid_invoice_count = pending_invoice_count = partial_invoice_count = 0
        for inv in financial_invoices:
            key = str(inv.get("id") or inv.get("invoice_no"))
            st = invoice_states.get(key, {"billed": float(inv.get("total") or 0), "paid": 0, "balance": float(inv.get("total") or 0), "status": "Pending"})
            total_billed += st["billed"]
            total_paid += st["paid"]
            if st["status"] == "Paid":
                paid_invoice_count += 1
            elif st["status"] == "Partial":
                partial_invoice_count += 1
                if day_from(inv.get("due_date")) and day_from(inv.get("due_date")) < today:
                    overdue_amount += st["balance"]
            elif st["status"] == "Overdue":
                overdue_amount += st["balance"]
            elif st["status"] not in ("Cancelled",):
                pending_invoice_count += 1
        outstanding = max(total_billed - total_paid, 0)
        collection_rate = round((total_paid / total_billed) * 100, 2) if total_billed else 0

        # Attention Required: all counts come from live records.
        active_tasks = [t for t in tasks if str(t.get("status") or "TO DO").upper() != "COMPLETED"]
        overdue_tasks = [t for t in active_tasks if due_state(t.get("due_date")) == "overdue"]
        unassigned_tasks = [t for t in tasks if not t.get("employee_id") and not t.get("assigned_employee")]
        pending_bookings = [b for b in bookings if str(b.get("status") or "").lower() in {"pending", "new"}]
        partial_invoices = [i for i in invoices if invoice_states.get(str(i.get("id") or i.get("invoice_no")), {}).get("status") == "Partial"]
        overdue_compliance = [c for c in compliance if due_state(c.get("due_date")) == "overdue" and str(c.get("status") or "").lower() not in {"completed", "filed", "approved"}]
        attention = [
            {"key": "overdue_tasks", "label": "Overdue Tasks", "count": len(overdue_tasks), "to": "/admin/task-board"},
            {"key": "overdue_payments", "label": "Overdue Payments", "count": sum(1 for i in invoices if invoice_states.get(str(i.get("id") or i.get("invoice_no")), {}).get("status") == "Overdue"), "to": "/admin/payments"},
            {"key": "overdue_compliance", "label": "Overdue Compliance", "count": len(overdue_compliance), "to": "/admin/gst"},
            {"key": "pending_bookings", "label": "Pending Bookings", "count": len(pending_bookings), "to": "/admin/bookings"},
            {"key": "unassigned_tasks", "label": "Unassigned Tasks", "count": len(unassigned_tasks), "to": "/admin/task-board"},
            {"key": "partial_invoices", "label": "Partially Paid Invoices", "count": len(partial_invoices), "to": "/admin/invoices"},
        ]

        # Today's Operations intentionally stays anchored to server-side IST.
        def is_today(record, fields):
            return day_from(*(record.get(f) for f in fields)) == today
        today_ops = {
            "new_customers": sum(1 for x in customers if is_today(x, ("created_at", "createdAt"))),
            "new_bookings": sum(1 for x in bookings if is_today(x, ("booking_date", "created_at", "date"))),
            "payments_received": round(sum(float(x.get("total") or x.get("amount") or 0) for x in payment_rows if is_today(x, ("payment_date", "recorded_at", "txn_date", "created_at"))), 2),
            "invoices_created": sum(1 for x in invoices if is_today(x, ("invoice_date", "issue_date", "created_at"))),
            "tasks_created": sum(1 for x in tasks if is_today(x, ("created_at", "createdAt"))),
            "tasks_completed": sum(1 for x in tasks if str(x.get("status") or "").upper() == "COMPLETED" and is_today(x, ("completed_at", "updated_at"))),
            "documents_uploaded": sum(1 for x in documents if is_today(x, ("uploaded_at", "created_at", "date"))),
            "new_notifications": sum(1 for x in notifications if is_today(x, ("ts", "created_at"))),
        }

        # Compliance overview preserves stored status terminology while presenting
        # the requested operational buckets.
        completed_states = {"completed", "filed", "approved"}
        processing_states = {"processing", "running", "confirmed", "in progress"}
        def compliance_bucket(row):
            status = str(row.get("status") or "Pending").strip().lower()
            if status in completed_states:
                return "completed"
            if status in processing_states:
                return "processing"
            if due_state(row.get("due_date")) == "overdue":
                return "overdue"
            return "pending"
        compliance_overview = []
        for kind, label in (("gst", "GST"), ("itr", "Income Tax"), ("tds", "TDS"), ("roc", "ROC")):
            rows = compliance_by_kind[kind]
            counts = {"pending": 0, "processing": 0, "completed": 0, "overdue": 0}
            for row in rows:
                if (range_start or range_end) and not in_range(row, ("created_at", "filed_date", "period", "due_date")):
                    continue
                counts[compliance_bucket(row)] += 1
            to = {"gst": "/admin/gst", "itr": "/admin/income-tax", "tds": "/admin/tds", "roc": "/admin/roc"}[kind]
            compliance_overview.append({"key": kind, "label": label, **counts, "to": to})

        # Employee workload reuses the existing Task Board records.
        emp_names = {str(e.get("id")): e.get("name") or e.get("emp_id") or str(e.get("id")) for e in employees if e.get("id")}
        workload_map = {}
        for task in tasks:
            eid = str(task.get("employee_id") or "").strip()
            name = emp_names.get(eid) or task.get("assigned_employee") or "Unassigned"
            row = workload_map.setdefault(name, {"name": name, "assigned": 0, "todo": 0, "in_progress": 0, "review": 0, "completed": 0, "overdue": 0, "due_today": 0})
            row["assigned"] += 1
            status = str(task.get("status") or "TO DO").upper()
            if status == "TO DO": row["todo"] += 1
            elif status == "IN PROGRESS": row["in_progress"] += 1
            elif status == "REVIEW": row["review"] += 1
            elif status == "COMPLETED": row["completed"] += 1
            if status != "COMPLETED" and due_state(task.get("due_date")) == "overdue": row["overdue"] += 1
            if status != "COMPLETED" and day_from(task.get("due_date")) == today: row["due_today"] += 1
        employee_workload = sorted(workload_map.values(), key=lambda x: (-x["assigned"], x["name"].lower()))

        # Deadlines: use only real due-date-bearing records.
        deadlines = []
        for t in tasks:
            if t.get("due_date") and str(t.get("status") or "").upper() != "COMPLETED":
                deadlines.append({"id": t.get("id"), "title": t.get("title") or "Task", "kind": "Task", "due_date": str(t.get("due_date"))[:10], "to": "/admin/task-board"})
        for kind, label, to in (("gst", "GST", "/admin/gst"), ("itr", "Income Tax", "/admin/income-tax"), ("tds", "TDS", "/admin/tds"), ("roc", "ROC", "/admin/roc")):
            for row in compliance_by_kind[kind]:
                if row.get("due_date") and compliance_bucket(row) != "completed":
                    deadlines.append({"id": row.get("id"), "title": row.get("client") or row.get("customer") or label, "kind": label, "due_date": str(row.get("due_date"))[:10], "to": to})
        for inv in invoices:
            st = invoice_states.get(str(inv.get("id") or inv.get("invoice_no")), {})
            if inv.get("due_date") and st.get("status") not in ("Paid", "Cancelled"):
                deadlines.append({"id": inv.get("id") or inv.get("invoice_no"), "title": inv.get("invoice_no") or "Invoice", "kind": "Invoice", "due_date": str(inv.get("due_date"))[:10], "to": "/admin/invoices"})
        for project in projects:
            if project.get("due_date") and str(project.get("status") or "").lower() not in {"completed", "closed"}:
                deadlines.append({"id": project.get("id"), "title": project.get("name") or "Project", "kind": "Project", "due_date": str(project.get("due_date"))[:10], "to": "/admin/projects"})
        deadlines.sort(key=lambda x: x["due_date"])
        deadline_groups = {
            "overdue": [x for x in deadlines if day_from(x["due_date"]) and day_from(x["due_date"]) < today][:20],
            "due_today": [x for x in deadlines if day_from(x["due_date"]) == today][:20],
            "due_soon": [x for x in deadlines if day_from(x["due_date"]) and today < day_from(x["due_date"]) <= today + timedelta(days=3)][:20],
            "this_week": [x for x in deadlines if day_from(x["due_date"]) and today < day_from(x["due_date"]) <= today + timedelta(days=6)][:20],
        }

        # Customer health is derived from actual customer relationships.
        active_customer_ids = set()
        for b in bookings:
            if str(b.get("status") or "").lower() not in {"cancelled", "completed"} and b.get("customer_id"):
                active_customer_ids.add(str(b["customer_id"]))
        outstanding_customer_ids = set()
        for inv in invoices:
            key = str(inv.get("id") or inv.get("invoice_no"))
            st = invoice_states.get(key, {})
            if st.get("balance", 0) > 0 and inv.get("customer_id"):
                outstanding_customer_ids.add(str(inv["customer_id"]))
        overdue_compliance_customer_ids = {
            str(c.get("customer_id")) for c in overdue_compliance if c.get("customer_id")
        }
        customer_health = {
            "total": len(customers),
            "new_this_month": sum(1 for c in customers if day_from(c.get("created_at"), c.get("createdAt")) and day_from(c.get("created_at"), c.get("createdAt")).strftime("%Y-%m") == today.strftime("%Y-%m")),
            "active": len(active_customer_ids),
            "inactive": max(len(customers) - len(active_customer_ids), 0),
            "with_outstanding": len(outstanding_customer_ids),
            "with_active_services": len(active_customer_ids),
            "with_overdue_compliance": len(overdue_compliance_customer_ids),
            "without_employee": sum(1 for c in customers if not c.get("employee_id") and not c.get("assigned_employee")),
        }

        # Service performance uses the existing service records and relationship FKs.
        service_performance = []
        for service in services:
            sid = service.get("id")
            name = service.get("name") or service.get("title") or sid
            if not sid or not name:
                continue
            sb = [b for b in period_bookings if str(b.get("service_id") or "") == str(sid) or (not b.get("service_id") and b.get("service") == name)]
            si = [i for i in period_invoices if str(i.get("service_id") or "") == str(sid) or (not i.get("service_id") and i.get("service") == name)]
            billed = paid = outstanding_service = 0.0
            for inv in si:
                st = invoice_states.get(str(inv.get("id") or inv.get("invoice_no")), {})
                billed += st.get("billed", float(inv.get("total") or 0))
                paid += st.get("paid", 0)
                outstanding_service += st.get("balance", max(float(inv.get("total") or 0) - float(st.get("paid", 0)), 0))
            completed = sum(1 for b in sb if str(b.get("status") or "").lower() == "completed")
            active = sum(1 for b in sb if str(b.get("status") or "").lower() in {"pending", "confirmed", "processing", "running", "new"})
            service_performance.append({
                "id": sid, "name": name,
                "customers": len({str(b.get("customer_id")) for b in sb if b.get("customer_id")}),
                "bookings": len(sb), "revenue": round(paid, 2),
                "billed": round(billed, 2), "pending": active, "completed": completed,
                "outstanding": round(outstanding_service, 2),
                "completion_rate": round((completed / len(sb)) * 100, 2) if sb else 0,
            })
        service_performance.sort(key=lambda x: (-x["bookings"], x["name"].lower()))

        # Booking funnel reflects actual stored statuses; aliases are presentation only.
        booking_funnel = []
        funnel_labels = [
            ("New/Pending", {"new", "pending"}),
            ("Confirmed", {"confirmed"}),
            ("Processing", {"processing", "running"}),
            ("Completed", {"completed"}),
            ("Cancelled", {"cancelled", "canceled"}),
        ]
        for label, statuses in funnel_labels:
            count = sum(1 for b in period_bookings if str(b.get("status") or "").strip().lower() in statuses)
            booking_funnel.append({"label": label, "count": count})
        total_funnel = sum(x["count"] for x in booking_funnel)
        for row in booking_funnel:
            row["percentage"] = round((row["count"] / total_funnel) * 100, 2) if total_funnel else 0

        # Activity feed reuses audit/task-activity/notification infrastructure.
        activity = []
        for a in audit_logs:
            action = str(a.get("action") or "Activity").replace("_", " ").title()
            activity.append({"title": action, "description": str(a.get("collection") or "").replace("erp_", "").title(), "time": a.get("ts"), "kind": "Audit"})
        for a in task_activity:
            activity.append({"title": a.get("action") or a.get("type") or "Task activity", "description": a.get("comment") or a.get("title") or "", "time": a.get("created_at"), "kind": "Task"})
        for n in notifications[:50]:
            activity.append({"title": n.get("title") or "Notification", "description": n.get("description") or n.get("message") or "", "time": n.get("ts") or n.get("created_at"), "kind": "Notification"})
        activity.sort(key=lambda x: str(x.get("time") or ""), reverse=True)

        # Existing dashboard chart sources are retained, with the selected period
        # applied to date-based records.
        revenue_monthly, customer_monthly, filing_monthly = {}, {}, {}
        for inv in period_invoices:
            month = str(inv.get("invoice_date") or inv.get("issue_date") or inv.get("created_at") or "")[:7]
            st = invoice_states.get(str(inv.get("id") or inv.get("invoice_no")), {})
            if month:
                revenue_monthly[month] = revenue_monthly.get(month, 0) + st.get("paid", 0)
        for cdoc in period_customers:
            month = str(cdoc.get("created_at") or cdoc.get("createdAt") or "")[:7]
            if month:
                customer_monthly[month] = customer_monthly.get(month, 0) + 1
        for kind, key in (("gst", "gst"), ("itr", "itr")):
            for row in compliance_by_kind[kind]:
                if not in_range(row, ("period", "filed_date", "created_at", "due_date")):
                    continue
                month = str(row.get("period") or row.get("filed_date") or row.get("created_at") or "")[:7]
                if month:
                    filing_monthly.setdefault(month, {"gst": 0, "itr": 0})[key] += 1

        performance = [{"name": e.get("name"), "score": float(e.get("performance") or 0)} for e in employees if e.get("name")]
        return _ok({
            "cards": {
                "customers": len(period_customers) if (range_start or range_end) else len(customers),
                "employees": len(employees),
                "active_agents": sum(1 for a in agents if str(a.get("status") or "").lower() == "active"),
                "running_projects": sum(1 for p in projects if str(p.get("status") or "").lower() == "running"),
                "completed_projects": sum(1 for p in projects if str(p.get("status") or "").lower() == "completed"),
                "pending_projects": sum(1 for p in projects if str(p.get("status") or "").lower() == "pending"),
                "revenue": round(total_paid, 2),
                "outstanding": round(outstanding, 2),
                "paid_invoices": paid_invoice_count,
                "pending_invoices": pending_invoice_count,
                "bookings": len(period_bookings) if (range_start or range_end) else len(bookings),
                "compliance_open": sum(1 for c in period_compliance if compliance_bucket(c) not in {"completed"}),
            },
            "projects": projects,
            "invoices": period_invoices,
            "bookings": period_bookings,
            "compliance": period_compliance,
            "charts": {
                "service_counts": period_service_counts,
                "payment_status_counts": period_payment_counts,
                "booking_status_counts": period_booking_counts,
                "revenue_monthly": [{"m": k, "revenue": round(v, 2)} for k, v in sorted(revenue_monthly.items())],
                "customer_growth": [{"m": k, "customers": v} for k, v in sorted(customer_monthly.items())],
                "filing_trend": [{"m": k, **v} for k, v in sorted(filing_monthly.items())],
                "performance": performance,
            },
            "attention_required": attention,
            "today_operations": today_ops,
            "compliance_overview": compliance_overview,
            "financial_health": {
                "total_billed": round(total_billed, 2),
                "total_paid": round(total_paid, 2),
                "outstanding": round(outstanding, 2),
                "overdue_amount": round(overdue_amount, 2),
                "paid_invoices": paid_invoice_count,
                "pending_invoices": pending_invoice_count,
                "partially_paid_invoices": partial_invoice_count,
                "collection_rate": collection_rate,
            },
            "employee_workload": employee_workload,
            "deadlines": deadline_groups,
            "activity_feed": activity[:30],
            "customer_health": customer_health,
            "service_performance": service_performance[:30],
            "booking_funnel": booking_funnel,
            "task_summary": {
                "total": len(period_tasks) if (range_start or range_end) else len(tasks),
                "todo": sum(1 for t in period_tasks if str(t.get("status") or "TO DO").upper() == "TO DO"),
                "in_progress": sum(1 for t in period_tasks if str(t.get("status") or "").upper() == "IN PROGRESS"),
                "review": sum(1 for t in period_tasks if str(t.get("status") or "").upper() == "REVIEW"),
                "completed": sum(1 for t in period_tasks if str(t.get("status") or "").upper() == "COMPLETED"),
                "overdue": sum(1 for t in period_tasks if str(t.get("status") or "").upper() != "COMPLETED" and due_state(t.get("due_date")) == "overdue"),
                "unassigned": sum(1 for t in period_tasks if not t.get("employee_id") and not t.get("assigned_employee")),
                "today_due": sum(1 for t in tasks if str(t.get("status") or "").upper() != "COMPLETED" and day_from(t.get("due_date")) == today),
                "today_completed": sum(1 for t in tasks if str(t.get("status") or "").upper() == "COMPLETED" and day_from(t.get("completed_at"), t.get("updated_at")) == today),
            },
            "task_analytics": {
                "by_status": [
                    {"name": status, "value": sum(1 for t in period_tasks if str(t.get("status") or "TO DO").upper() == status)}
                    for status in ("TO DO", "IN PROGRESS", "REVIEW", "COMPLETED")
                ],
                "by_service": [
                    {"name": name, "value": count}
                    for name, count in sorted(
                        {str(t.get("service") or "Unlinked"): sum(1 for x in period_tasks if str(x.get("service") or "Unlinked") == str(t.get("service") or "Unlinked")) for t in period_tasks}.items(),
                        key=lambda x: (-x[1], x[0].lower())
                    )
                ][:20],
                "workload": [{"name": x["name"], "value": max(x["assigned"] - x["completed"], 0)} for x in employee_workload[:30]],
                "trend": [
                    {
                        "m": month,
                        "created": sum(1 for t in period_tasks if str(t.get("created_at") or t.get("createdAt") or "")[:7] == month),
                        "completed": sum(1 for t in period_tasks if str(t.get("completed_at") or t.get("updated_at") or "")[:7] == month and str(t.get("status") or "").upper() == "COMPLETED"),
                    }
                    for month in sorted({
                        str(t.get("created_at") or t.get("createdAt") or "")[:7] for t in period_tasks if str(t.get("created_at") or t.get("createdAt") or "")[:7]
                    } | {
                        str(t.get("completed_at") or t.get("updated_at") or "")[:7] for t in period_tasks if str(t.get("completed_at") or t.get("updated_at") or "")[:7]
                    })
                ],
            },
            "date_filter": {
                "start_date": range_start.isoformat() if range_start else None,
                "end_date": range_end.isoformat() if range_end else None,
                "today": today_str,
            },
        }, message="Dashboard loaded")

    @router.get("/customer/dashboard")
    async def customer_dashboard(user: dict = Depends(get_current_user)):
        if user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Only customers can access this endpoint")
        customer_profile = await _customer_record_for_user(user)
        cid = customer_profile.get("id") if customer_profile else None
        if not cid:
            raise HTTPException(status_code=404, detail="No customer profile is linked to this account")

        bookings = await db["erp_bookings"].find({"customer_id": cid}, {"_id": 0}).to_list(2000)
        invoices = await db["erp_invoices"].find({"customer_id": cid}, {"_id": 0}).to_list(2000)
        documents = await db["erp_documents"].find({"customer_id": cid}, {"_id": 0}).to_list(2000)
        tickets = await db["erp_tickets"].find({"customer_id": cid}, {"_id": 0}).to_list(2000)
        notifications = await db["erp_notifications"].find({"$or": [{"user_id": cid}, {"role": "all"}]}, {"_id": 0}).to_list(500)

        completed = [b for b in bookings if b.get("status") == "Completed"]
        active = [b for b in bookings if b.get("status") in ("Pending", "Running", "Confirmed", "Processing")]
        paid_invoices = []
        pending_invoices = []
        outstanding = 0
        for inv in invoices:
            state = await _invoice_financial_state(inv)
            inv.update({"paid_amount": state["paid_amount"], "balance": state["balance"], "payment_status": state["payment_status"]})
            if state["payment_status"] == "Paid":
                paid_invoices.append(inv)
            elif state["payment_status"] != "Cancelled":
                pending_invoices.append(inv)
                outstanding += state["balance"]
        payment_pending_bookings = [b for b in bookings if b.get("payment_status") != "Paid"]
        filings = []
        for n in ("gst", "itr", "tds", "roc"):
            rows = await db[COLLECTIONS[n][0]].find({"customer_id": cid}, {"_id": 0}).to_list(500)
            filings.extend(rows)
        spending = {}
        for i in invoices:
            month = str(i.get("invoice_date") or i.get("issue_date") or "")[:7]
            if month:
                spending[month] = spending.get(month, 0) + float(i.get("total") or 0)
        service_usage = {}
        for b in bookings:
            service_usage[b.get("service") or "Other"] = service_usage.get(b.get("service") or "Other", 0) + 1

        return _ok({
            "cards": {
                "total_services": len(bookings), "active_services": len(active),
                "completed_services": len(completed), "pending_payment": len(payment_pending_bookings),
                "outstanding": outstanding, "payments_done": len(paid_invoices),
                "pending_invoices": len(pending_invoices), "documents": len(documents),
                "notifications": len([n for n in notifications if not n.get("read")]),
                "tickets": len([t for t in tickets if t.get("status") not in ("Closed", "Resolved")]),
            },
            "filings": filings,
            "spending": [{"m": k, "amount": v} for k, v in sorted(spending.items())],
            "service_usage": [{"name": k, "value": v} for k, v in service_usage.items()],
            "due_dates": [{"title": b.get("service") or "Service", "date": b.get("due_date"), "type": "Service"} for b in bookings if b.get("due_date")],
        })

    @router.get("/reminders")
    async def reminders(user: dict = Depends(get_current_user)):
        from datetime import date
        today = date.today()
        out = []
        def add(title, company, due, kind, assigned):
            if not due:
                return
            try:
                d = date.fromisoformat(str(due)[:10])
            except Exception:
                return
            days = (d - today).days
            if days < 0:
                prio, ntype = "Overdue", "urgent"
            elif days <= 3:
                prio, ntype = "High", "warning"
            elif days <= 10:
                prio, ntype = "Medium", "information"
            else:
                prio, ntype = "Low", "information"
            out.append({"title": title, "company": company, "due_date": str(due)[:10], "days_remaining": days, "priority": prio, "type": ntype, "kind": kind, "assigned": assigned})

        if user.get("role") == "employee":
            emp = await _employee_record_for_user(user)
            if not emp:
                raise HTTPException(status_code=404, detail="Employee profile not found")
            emp_id = emp.get("id")
            customer_rows = await db["erp_customers"].find({}, {"_id": 0}).to_list(5000)
            assigned_customers = [c for c in customer_rows if _customer_assigned_to_employee(c, emp)]
            customer_ids = {str(c.get("id")) for c in assigned_customers if c.get("id")}
            tasks = await db["erp_tasks"].find({"task_board": True, "employee_id": emp_id}, {"_id": 0}).to_list(5000)
            for r in tasks:
                if str(r.get("status") or "").upper() != "COMPLETED":
                    add(r.get("title") or "Task", r.get("customer") or r.get("customer_id"), r.get("due_date"), "Task", emp.get("name"))
            for key, coll_name, kind in (("gst", "erp_gst", "GST"), ("itr", "erp_itr", "Income Tax"), ("tds", "erp_tds", "TDS"), ("roc", "erp_roc", "ROC")):
                if customer_ids:
                    rows = await db[coll_name].find({"customer_id": {"$in": list(customer_ids)}, "status": {"$ne": "Completed"}}, {"_id": 0}).to_list(5000)
                    for r in rows:
                        customer = next((c for c in assigned_customers if c.get("id") == r.get("customer_id")), {})
                        label = customer.get("business_name") or customer.get("name") or r.get("client") or r.get("company")
                        add(f"{kind} compliance due", label, r.get("due_date"), kind, emp.get("name"))
            appointments = await db["erp_appointments"].find({}, {"_id": 0}).to_list(5000)
            for r in appointments:
                if str(r.get("assigned_employee") or r.get("employee") or "") == str(emp.get("name") or "") or str(r.get("employee_id") or "") == str(emp_id):
                    customer = next((c for c in assigned_customers if c.get("id") == r.get("customer_id")), {})
                    add(f"Meeting: {r.get('service') or r.get('title') or 'Appointment'}", customer.get("business_name") or customer.get("name") or r.get("business_name"), r.get("date"), "Meeting", emp.get("name"))
            projects = await db["erp_projects"].find({}, {"_id": 0}).to_list(5000)
            for r in projects:
                if str(r.get("assigned_employee") or r.get("employee") or "") == str(emp.get("name") or "") or str(r.get("employee_id") or "") == str(emp_id) or str(r.get("customer_id") or "") in customer_ids:
                    add(f"Project: {r.get('name') or r.get('project_id')}", r.get("client") or r.get("customer"), r.get("due_date"), "Project", emp.get("name"))
        else:
            for r in await db["erp_gst"].find({"status": {"$ne": "Completed"}}, {"_id": 0}).to_list(100):
                add(f"GST {r.get('return_type')} due", r.get("client"), r.get("due_date"), "GST", r.get("consultant"))
            for r in await db["erp_itr"].find({"status": {"$ne": "Completed"}}, {"_id": 0}).to_list(100):
                add("Income Tax return due", r.get("client"), r.get("due_date"), "Income Tax", r.get("consultant"))
            for r in await db["erp_tds"].find({"status": {"$ne": "Completed"}}, {"_id": 0}).to_list(100):
                add(f"TDS {r.get('form')} {r.get('quarter')} due", r.get("client"), r.get("due_date"), "TDS", "-")
            for r in await db["erp_roc"].find({"status": {"$ne": "Completed"}}, {"_id": 0}).to_list(100):
                add(f"ROC {r.get('form')} due", r.get("company"), r.get("due_date"), "ROC", r.get("consultant"))
            for r in await db["erp_invoices"].find({"payment_status": {"$ne": "Paid"}}, {"_id": 0}).to_list(100):
                add(f"Invoice {r.get('invoice_no')} payment due", r.get("customer"), r.get("invoice_date"), "Invoice", "-")

        out.sort(key=lambda x: x["days_remaining"])
        summary = {
            "today": len([x for x in out if x["days_remaining"] == 0]),
            "this_week": len([x for x in out if 0 <= x["days_remaining"] <= 7]),
            "overdue": len([x for x in out if x["days_remaining"] < 0]),
            "upcoming": len(out),
        }
        return _ok({"reminders": out, "summary": summary})

    @router.get("/accounting/summary")
    async def accounting_summary(user: dict = Depends(get_current_user)):
        income = 300500
        expenses = 425000
        return _ok({
            "income": income, "expenses": expenses, "profit": income - expenses,
            "cash_flow": [
                {"m": "Apr", "in": 610000, "out": 480000}, {"m": "May", "in": 540000, "out": 460000},
                {"m": "Jun", "in": 720000, "out": 505000}, {"m": "Jul", "in": 680000, "out": 425000},
            ],
            "pnl": [
                {"account": "Service Revenue", "amount": 1560000, "type": "Income"},
                {"account": "Consultancy Revenue", "amount": 640000, "type": "Income"},
                {"account": "Salaries", "amount": 380000, "type": "Expense"},
                {"account": "Office Rent", "amount": 45000, "type": "Expense"},
                {"account": "Software & Tools", "amount": 28000, "type": "Expense"},
                {"account": "Marketing", "amount": 62000, "type": "Expense"},
            ],
            "balance_sheet": [
                {"item": "Cash & Bank", "amount": 1240000, "type": "Asset"},
                {"item": "Accounts Receivable", "amount": 685000, "type": "Asset"},
                {"item": "Fixed Assets", "amount": 520000, "type": "Asset"},
                {"item": "Accounts Payable", "amount": 210000, "type": "Liability"},
                {"item": "Loans", "amount": 300000, "type": "Liability"},
                {"item": "Owner's Equity", "amount": 1935000, "type": "Equity"},
            ],
            "trial_balance": [
                {"account": "Bank", "debit": 1240000, "credit": 0},
                {"account": "Accounts Receivable", "debit": 685000, "credit": 0},
                {"account": "Service Revenue", "debit": 0, "credit": 1560000},
                {"account": "Consultancy Revenue", "debit": 0, "credit": 640000},
                {"account": "Salaries", "debit": 380000, "credit": 0},
                {"account": "Office Rent", "debit": 45000, "credit": 0},
            ],
        })

    def _notification_scope(user):
        return {"$or": [{"user_id": user.get("id")}, {"role": user.get("role"), "user_id": None}, {"role": "all"}]}

    _NOTIF_DB_ERR = "Notification service cannot reach MongoDB. Check MONGO_URL/DB_NAME and make sure MongoDB is running/reachable."

    @router.get("/notifications")
    async def list_notifications(search: str = Query(None), user: dict = Depends(get_current_user)):
        q = _notification_scope(user)
        try:
            items = await db["erp_notifications"].find(q, {"_id": 0}).sort("ts", -1).to_list(10000)
            items = [_present_notification(i) for i in items]
            if search:
                s = search.lower()
                items = [i for i in items if s in (i.get("title", "") + i.get("description", "") + i.get("category", "") + str(i.get("customer_name", "")) + str(i.get("service_name", ""))).lower()]
            unread = len([i for i in items if not i.get("read")])
            return _ok({"notifications": items, "unread": unread})
        except Exception as exc:
            raise HTTPException(status_code=503, detail=_NOTIF_DB_ERR) from exc

    @router.get("/admin/notifications/unread-count")  # Admin-prefixed alias of the same handler (same data, same auth scope)
    @router.get("/notifications/unread-count")
    async def notifications_unread_count(user: dict = Depends(get_current_user)):
        """Cheap count straight from MongoDB — used by the sidebar/header badge poll."""
        try:
            count = await db["erp_notifications"].count_documents({"$and": [_notification_scope(user), {"read": {"$ne": True}}]})
            latest = await db["erp_notifications"].find_one(_notification_scope(user), {"_id": 0, "id": 1}, sort=[("ts", -1)])
            return _ok({"unread": count, "latest_id": (latest or {}).get("id")})
        except Exception as exc:
            raise HTTPException(status_code=503, detail=_NOTIF_DB_ERR) from exc

    @router.get("/notifications/{nid}")
    async def notification_detail(nid: str, user: dict = Depends(get_current_user)):
        """Notification + the customer / booking / service it refers to, resolved
        live from the existing ERP collections (no copies are stored). Only
        Admin receives the linked business records."""
        try:
            n = await db["erp_notifications"].find_one({"$and": [{"id": nid}, _notification_scope(user)]}, {"_id": 0})
        except Exception as exc:
            raise HTTPException(status_code=503, detail=_NOTIF_DB_ERR) from exc
        if not n:
            raise HTTPException(status_code=404, detail="Notification not found")
        out = {"notification": _present_notification(n), "customer": None, "booking": None, "service": None, "leave": None, "task": None, "bookings": []}
        if user.get("role") != "admin":
            return _ok(out)
        try:
            if n.get("customer_id"):
                out["customer"] = await db["erp_customers"].find_one({"id": n["customer_id"]}, {"_id": 0})
            if n.get("booking_id"):
                out["booking"] = await db["erp_bookings"].find_one({"id": n["booking_id"]}, {"_id": 0})
                if out["booking"] and not out["customer"] and out["booking"].get("customer_id"):
                    out["customer"] = await db["erp_customers"].find_one({"id": out["booking"]["customer_id"]}, {"_id": 0})
            if n.get("leave_id"):
                out["leave"] = await db["erp_leaves"].find_one({"id": n["leave_id"]}, {"_id": 0})
            if n.get("task_id"):
                task = await db["erp_tasks"].find_one({"id": n["task_id"], "task_board": True}, {"_id": 0})
                if task:
                    out["task"] = task
            sid = n.get("service_id") or (out["booking"] or {}).get("service_id")
            if sid:
                out["service"] = await db["erp_services"].find_one({"id": sid}, {"_id": 0})
            if out["customer"] and n.get("kind") == KIND_CUSTOMER_REGISTERED:
                out["bookings"] = await db["erp_bookings"].find({"customer_id": out["customer"]["id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Could not load the records linked to this notification.") from exc
        out["missing"] = [k for k, linked in (("customer", n.get("customer_id")), ("booking", n.get("booking_id")), ("leave", n.get("leave_id")), ("task", n.get("task_id"))) if linked and not out[k]]
        return _ok(out)

    @router.post("/notifications/{nid}/read")
    async def mark_read(nid: str, user: dict = Depends(get_current_user)):
        try:
            q = {"$and": [{"id": nid}, _notification_scope(user)]}
            existing = await db["erp_notifications"].find_one(q, {"_id": 0, "read": 1, "read_at": 1})
            if not existing:
                raise HTTPException(status_code=404, detail="Notification not found")
            if not existing.get("read") or not existing.get("read_at"):
                await db["erp_notifications"].update_one(q, {"$set": {"read": True, "read_at": existing.get("read_at") or datetime.now(timezone.utc).isoformat()}})
            return _ok({"id": nid}, message="Marked read")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=_NOTIF_DB_ERR) from exc

    @router.post("/notifications/read-all")
    async def mark_all_read(user: dict = Depends(get_current_user)):
        try:
            await db["erp_notifications"].update_many({"$and": [_notification_scope(user), {"read": {"$ne": True}}]},
                                                      {"$set": {"read": True, "read_at": datetime.now(timezone.utc).isoformat()}})
            return _ok({}, message="All marked read")
        except Exception as exc:
            raise HTTPException(status_code=503, detail=_NOTIF_DB_ERR) from exc

    @router.delete("/notifications/{nid}")
    async def del_notification(nid: str, user: dict = Depends(get_current_user)):
        try:
            res = await db["erp_notifications"].delete_one({"$and": [{"id": nid}, _notification_scope(user)]})
            if not res.deleted_count:
                raise HTTPException(status_code=404, detail="Notification not found")
            return _ok({"id": nid}, message="Deleted")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Notification service cannot reach MongoDB. Check MONGO_URL/DB_NAME.") from exc




    # ---------------- Phase 2B: linked invoice/payment detail + analytics ----------------
    async def _invoice_paid_amount(invoice: dict):
        invoice_no = invoice.get("invoice_no") or invoice.get("id")
        rows = await db["erp_payments"].find(
            {"$or": [{"invoice_id": invoice.get("id")}, {"invoice_no": invoice_no}], "status": {"$in": ["Completed", "Paid", "Partial"]}},
            {"_id": 0}
        ).to_list(5000)
        paid = sum(float(r.get("total") or r.get("amount") or 0) for r in rows)
        if paid <= 0 and invoice.get("payment_status") == "Paid":
            paid = float(invoice.get("total") or 0)
        return round(paid, 2), rows

    async def _invoice_financial_state(invoice: dict):
        """Return backend-authoritative invoice paid/balance/payment-status values."""
        paid, payments = await _invoice_paid_amount(invoice)
        total = round(float(invoice.get("total") or 0), 2)
        balance = round(max(total - paid, 0), 2)
        current = str(invoice.get("payment_status") or invoice.get("status") or "Pending")
        if current == "Cancelled":
            status = current
        elif balance <= 0.009:
            status = "Paid"
        else:
            due_date = str(invoice.get("due_date") or "")[:10]
            today = _ist_today_str()
            if due_date and due_date < today:
                status = "Overdue"
            elif paid > 0:
                status = "Partial"
            else:
                status = "Pending"
        return {"paid_amount": round(paid, 2), "balance": balance, "payment_status": status, "payments": payments}

    async def _sync_invoice_financials(invoice_id: str):
        invoice = await db["erp_invoices"].find_one({"id": invoice_id}, {"_id": 0})
        if not invoice:
            return None
        state = await _invoice_financial_state(invoice)
        await db["erp_invoices"].update_one(
            {"id": invoice_id},
            {"$set": {
                "paid_amount": state["paid_amount"],
                "balance": state["balance"],
                "payment_status": state["payment_status"],
            }},
        )
        invoice.update({k: state[k] for k in ("paid_amount", "balance", "payment_status")})
        return invoice

    async def _sync_booking_payment_status(invoice: dict):
        booking_id = invoice.get("booking_id")
        if not booking_id:
            return
        balance = float(invoice.get("balance") or 0)
        total = float(invoice.get("total") or 0)
        if balance <= 0.009:
            status = "Paid"
        elif total > 0 and balance < total:
            status = "Partial"
        else:
            status = "Pending"
        await db["erp_bookings"].update_one({"id": booking_id}, {"$set": {"payment_status": status}})

    async def _resolve_service(invoice: dict, booking: dict = None):
        service_id = invoice.get("service_id") or (booking or {}).get("service_id")
        service_name = invoice.get("service") or (booking or {}).get("service") or (booking or {}).get("service_name")
        service = None
        if service_id:
            service = await db["erp_services"].find_one({"id": service_id}, {"_id": 0})
        if not service and service_name:
            service = await db["erp_services"].find_one({"name": service_name}, {"_id": 0})
        return service, service_id, service_name

    async def _relationship_authorized(entity: str, record: dict, user: dict):
        role = user.get("role")
        if role == "admin":
            return True
        if role == "customer":
            customer = await _customer_record_for_user(user)
            cid = customer.get("id") if customer else None
            if entity == "customer":
                return bool(cid and record.get("id") == cid)
            return bool(cid and record.get("customer_id") == cid)
        if role == "agent":
            agent_id = (user.get("meta") or {}).get("agent_id")
            if not agent_id:
                agent = await _resolve_agent_id(name=user.get("name"))
                agent_id = agent
            if entity == "agent":
                return bool(agent_id and record.get("id") == agent_id)
            if entity == "booking":
                return record.get("agent_id") == agent_id
            if entity == "payment":
                return record.get("agent_id") == agent_id
        return role in {"employee"} and entity in {"booking", "invoice", "payment"}

    # ---------------- Customer billing: ONE calculation used everywhere ----------------
    # Total Billed  = this customer's own billable fees: every non-cancelled invoice, plus the fee of every
    #                 non-cancelled booking that has no live invoice yet (so a service is never counted twice).
    # Total Paid    = this customer's own Completed/Paid payment records, each counted once.
    # Outstanding   = Total Billed - Total Paid.
    # Nothing here is hardcoded: every figure is read from erp_invoices / erp_bookings / erp_payments / erp_services.
    def _money(value):
        try:
            return round(float(value or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    def _blank(value):
        return value is None or (isinstance(value, str) and not value.strip())

    async def _billing_for_customers(customer_ids):
        ids = list(dict.fromkeys(str(c) for c in customer_ids if c))
        result = {cid: {
            "customer_id": cid, "total_billed": 0.0, "total_paid": 0.0, "outstanding": 0.0, "pending_payments": 0.0,
            "invoice_count": 0, "billable_invoice_count": 0, "payment_count": 0, "items": [], "invoice_billed": 0.0, "term_total": 0.0,
            "ignored": {"cancelled_bookings": 0, "cancelled_invoices": 0, "duplicate_payments": 0},
        } for cid in ids}
        if not ids:
            return result
        invoices = await db["erp_invoices"].find({"customer_id": {"$in": ids}}, {"_id": 0}).to_list(None)
        bookings = await db["erp_bookings"].find({"customer_id": {"$in": ids}}, {"_id": 0}).to_list(None)

        # Which customer owns which invoice reference (id or invoice_no).
        invoice_owner = {}
        for inv in invoices:
            for ref in (inv.get("id"), inv.get("invoice_no")):
                if ref:
                    invoice_owner[str(ref)] = str(inv.get("customer_id"))
        refs = list(invoice_owner.keys())
        pay_query = {"$or": [{"customer_id": {"$in": ids}}]
                     + ([{"invoice_id": {"$in": refs}}, {"invoice_no": {"$in": refs}}] if refs else [])}
        payments = await db["erp_payments"].find(pay_query, {"_id": 0}).to_list(None)
        # Payments that name an invoice outside this customer set: find who owns it so another
        # customer's payment can never be counted here.
        foreign_refs = list({str(r) for p in payments for r in (p.get("invoice_id"), p.get("invoice_no")) if r and str(r) not in invoice_owner})
        if foreign_refs:
            async for inv in db["erp_invoices"].find({"$or": [{"id": {"$in": foreign_refs}}, {"invoice_no": {"$in": foreign_refs}}]}, {"_id": 0, "id": 1, "invoice_no": 1, "customer_id": 1}):
                for ref in (inv.get("id"), inv.get("invoice_no")):
                    if ref:
                        invoice_owner[str(ref)] = str(inv.get("customer_id"))

        # Fee of a booking = the booking's own fee, else its project value, else its service's price (same rule
        # as the Booking History "Fee" column).
        need_price = {b.get("service_id") for b in bookings if b.get("service_id") and _blank(b.get("estimated_fee")) and _blank(b.get("project_value"))}
        prices = {x["id"]: x.get("final_price") async for x in db["erp_services"].find({"id": {"$in": list(need_price)}}, {"_id": 0, "id": 1, "final_price": 1})} if need_price else {}

        inv_by_cust, book_by_cust, pay_by_cust = {}, {}, {}
        for inv in invoices:
            inv_by_cust.setdefault(str(inv.get("customer_id")), []).append(inv)
        for b in bookings:
            book_by_cust.setdefault(str(b.get("customer_id")), []).append(b)
        for p in payments:
            owner = None
            for ref in (p.get("invoice_id"), p.get("invoice_no")):
                if ref and str(ref) in invoice_owner:
                    owner = invoice_owner[str(ref)]   # an invoice-linked payment belongs to the invoice's customer
                    break
            if owner is None and p.get("customer_id"):
                owner = str(p.get("customer_id"))
            if owner in result:
                pay_by_cust.setdefault(owner, []).append(p)

        for cid in ids:
            out = result[cid]
            my_bookings = book_by_cust.get(cid, [])
            cancelled_booking_ids = {b.get("id") for b in my_bookings if str(b.get("status")) == "Cancelled"}
            live_invoice_booking_ids = set()
            billed = 0.0
            out["invoice_count"] = len(inv_by_cust.get(cid, []))
            for inv in inv_by_cust.get(cid, []):
                inv_status = str(inv.get("payment_status") or inv.get("status") or "")
                if inv_status == "Cancelled" or (inv.get("booking_id") and inv.get("booking_id") in cancelled_booking_ids):
                    out["ignored"]["cancelled_invoices"] += 1
                    continue
                amount = _money(inv.get("total"))
                billed += amount
                out["billable_invoice_count"] += 1
                if inv.get("booking_id"):
                    live_invoice_booking_ids.add(inv.get("booking_id"))
                out["items"].append({"type": "invoice", "id": inv.get("id"), "invoice_no": inv.get("invoice_no"), "booking_id": inv.get("booking_id"), "service": inv.get("service"), "amount": amount})
            for b in my_bookings:
                if b.get("id") in cancelled_booking_ids:
                    out["ignored"]["cancelled_bookings"] += 1
                    continue
                if b.get("id") in live_invoice_booking_ids:
                    continue
                fee = b.get("estimated_fee") if not _blank(b.get("estimated_fee")) else (b.get("project_value") if not _blank(b.get("project_value")) else prices.get(b.get("service_id")))
                amount = _money(fee)
                if amount <= 0:
                    continue
                billed += amount
                out["items"].append({"type": "booking", "id": b.get("id"), "booking_id": b.get("id"), "service": b.get("service"), "amount": amount})

            paid = pending = 0.0
            term_total = 0.0   # standalone payment terms (no invoice / booking behind them)
            seen = set()
            for p in pay_by_cust.get(cid, []):
                keys = {("id", str(p.get("id")))}
                if p.get("payment_id"):
                    keys.add(("pid", str(p.get("payment_id"))))
                ref_no = str(p.get("reference_no") or "").strip()
                if ref_no:
                    keys.add(("ref", str(p.get("invoice_no") or p.get("invoice_id") or ""), ref_no))
                if keys & seen:
                    out["ignored"]["duplicate_payments"] += 1   # the same payment must never be counted twice
                    continue
                seen |= keys
                amount = _money(p.get("total") if p.get("total") is not None else p.get("amount"))
                status = str(p.get("status") or "Completed")
                if str(p.get("term_name") or "").strip() and not (p.get("invoice_no") or p.get("invoice_id") or p.get("booking_id")) and status != "Failed":
                    term_total += amount
                if status in {"Completed", "Paid", "Partial"}:
                    paid += amount
                    out["payment_count"] += 1
                elif status in {"Pending", "Failed"}:
                    pending += amount
            # A customer with no invoices/bookings is billed through their payment terms (Term 1/2/3...), so
            # those terms define Total Billed. Where real invoices/bookings exist they remain authoritative.
            out["invoice_billed"] = round(billed, 2)
            out["term_total"] = round(term_total, 2)
            billed = max(billed, term_total)
            out["total_billed"] = round(billed, 2)
            out["total_paid"] = round(paid, 2)
            out["outstanding"] = round(max(billed - paid, 0.0), 2)
            out["pending_payments"] = round(pending, 2)
        return result

    @router.get("/admin/customers/{customer_id}/billing")
    async def admin_customer_billing(customer_id: str, user: dict = Depends(get_current_user)):
        """Total Billed / Total Paid / Outstanding for ONE customer, computed from MongoDB on every call."""
        if user.get("role") not in {"admin", "super_admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="Admin access required")
        if not await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0, "id": 1}):
            raise HTTPException(status_code=404, detail="Customer not found")
        return _ok((await _billing_for_customers([customer_id]))[customer_id])

    @router.get("/customer/billing")
    async def customer_billing(user: dict = Depends(get_current_user)):
        """Billing and payment history for the authenticated customer only."""
        if user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Customer access required")
        customer_id = str((user.get("meta") or {}).get("customer_id") or "").strip()
        if not customer_id:
            profile = await db["erp_customers"].find_one({"email": user.get("email")}, {"_id": 0, "id": 1})
            customer_id = str((profile or {}).get("id") or "").strip()
        if not customer_id or not await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0, "id": 1}):
            raise HTTPException(status_code=404, detail="Customer profile not found")
        state = (await _billing_for_customers([customer_id]))[customer_id]
        payments = await db["erp_payments"].find({"customer_id": customer_id}, {"_id": 0, "file_data": 0}).sort([("payment_date", -1), ("created_at", -1)]).to_list(2000)
        # Legacy payments may be linked through a booking rather than customer_id.
        booking_ids = {str(x.get("id")) for x in await db["erp_bookings"].find({"customer_id": customer_id}, {"_id": 0, "id": 1}).to_list(2000) if x.get("id")}
        if booking_ids:
            legacy = await db["erp_payments"].find({"customer_id": {"$exists": False}, "booking_id": {"$in": list(booking_ids)}}, {"_id": 0, "file_data": 0}).to_list(2000)
            seen = {str(p.get("id")) for p in payments}
            payments.extend(p for p in legacy if str(p.get("id")) not in seen)
        payments.sort(key=lambda p: str(p.get("payment_date") or p.get("created_at") or ""), reverse=True)
        return _ok({**state, "payments": payments})

    @router.get("/admin/customers/{customer_id}/overview")
    async def admin_customer_overview(customer_id: str, user: dict = Depends(get_current_user)):
        """Customer -> services booked -> booking history, straight from MongoDB.
        Bookings (newest first) are joined to the existing Service records."""
        if user.get("role") not in {"admin", "super_admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="Admin access required")
        customer = await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        bookings = await db["erp_bookings"].find({"customer_id": customer_id}, {"_id": 0}).to_list(2000)
        bookings.sort(key=lambda b: str(b.get("created_at") or b.get("booking_date") or ""), reverse=True)
        sids = {b.get("service_id") for b in bookings if b.get("service_id")}
        services = {x["id"]: x async for x in db["erp_services"].find({"id": {"$in": list(sids)}}, {"_id": 0})} if sids else {}
        history = []
        for b in bookings:
            svc = services.get(b.get("service_id")) or {}
            history.append({
                "booking_id": b.get("id"), "service_id": b.get("service_id"),
                # The booked service: its own real service record first, then what the booking stored.
                # An application/placeholder label is never shown as the service.
                "service_name": _good_service_name(svc.get("name"), svc.get("title"), b.get("service")),
                "service_category": svc.get("category") or b.get("service_category"),
                "service_bucket": service_category_bucket(svc or {"category": b.get("service_category"), "name": b.get("service")}),
                "booking_date": b.get("created_at") or b.get("booking_date"),
                "appointment_date": b.get("appointment_date"), "appointment_time": b.get("appointment_time"),
                "status": b.get("status"), "payment_status": b.get("payment_status"), "notes": b.get("notes") or b.get("description"),
                "amount": b.get("estimated_fee") if b.get("estimated_fee") is not None else (b.get("project_value") if b.get("project_value") is not None else svc.get("final_price")),
            })
        # Service cards: the status comes from the real linked booking (the booking is the source of truth),
        # never from the customer-level workflow status. Several bookings of one service -> newest live one,
        # falling back to the newest (cancelled) one so a cancelled service reads "Cancelled".
        service_cards = []
        for sid, svc in services.items():
            linked = [h for h in history if h.get("service_id") == sid]
            live_linked = [h for h in linked if h.get("status") != "Cancelled"]
            pick = (live_linked or linked or [None])[0]
            service_cards.append({**svc, "service_bucket": service_category_bucket(svc),
                                  "booking_id": (pick or {}).get("booking_id"), "status": (pick or {}).get("status"),
                                  "booking_count": len(linked)})
        return _ok({"customer": customer, "services": service_cards, "bookings": history,
                    "booking_count": len(history)})

    @router.get("/relationships/{entity}/{record_id}")
    async def relationship_details(entity: str, record_id: str, user: dict = Depends(get_current_user)):
        """Phase-4 cross-module relationship graph using stable backend IDs."""
        aliases = {
            "customer": ("erp_customers", "customer"),
            "booking": ("erp_bookings", "booking"),
            "service": ("erp_services", "service"),
            "agent": ("erp_agents", "agent"),
            "invoice": ("erp_invoices", "invoice"),
            "payment": ("erp_payments", "payment"),
        }
        if entity not in aliases:
            raise HTTPException(status_code=404, detail="Unknown relationship entity")
        coll, _ = aliases[entity]
        record = await db[coll].find_one({"id": record_id}, {"_id": 0})
        if not record:
            raise HTTPException(status_code=404, detail=f"{entity.title()} not found")
        if not await _relationship_authorized(entity, record, user):
            raise HTTPException(status_code=403, detail="You are not permitted to view these relationships")

        result = {"entity": entity, "record": record, "customer": None, "booking": None,
                  "service": None, "agent": None, "invoice": None, "payments": [],
                  "bookings": [], "invoices": [], "services": [], "agents": [], "customers": []}

        async def find_one(collection, key, value):
            if not value:
                return None
            return await db[collection].find_one({key: value}, {"_id": 0})

        if entity == "customer":
            result["bookings"] = await db["erp_bookings"].find({"customer_id": record["id"]}, {"_id": 0}).to_list(500)
            result["invoices"] = await db["erp_invoices"].find({"customer_id": record["id"]}, {"_id": 0}).to_list(500)
            result["payments"] = await db["erp_payments"].find({"customer_id": record["id"]}, {"_id": 0}).to_list(1000)
            # "Get Services by Customer": a customer can be linked to more than one
            # service — its primary service_id plus every distinct service_id used
            # across its own bookings. Dedup and resolve to full service records so
            # the Admin/Customer/Employee/Agent views can all render the same list.
            service_ids = {b.get("service_id") for b in result["bookings"] if b.get("service_id")}
            if record.get("service_id"):
                service_ids.add(record["service_id"])
            if service_ids:
                result["services"] = await db["erp_services"].find({"id": {"$in": list(service_ids)}}, {"_id": 0}).to_list(500)
        elif entity == "booking":
            result["customer"] = await find_one("erp_customers", "id", record.get("customer_id"))
            result["service"] = await find_one("erp_services", "id", record.get("service_id"))
            result["agent"] = await find_one("erp_agents", "id", record.get("agent_id"))
            result["invoice"] = await find_one("erp_invoices", "booking_id", record.get("id"))
            result["payments"] = await db["erp_payments"].find({"booking_id": record.get("id")}, {"_id": 0}).to_list(1000)
        elif entity == "service":
            result["bookings"] = await db["erp_bookings"].find({"service_id": record["id"]}, {"_id": 0}).to_list(500)
            result["invoices"] = await db["erp_invoices"].find({"service_id": record["id"]}, {"_id": 0}).to_list(500)
            result["payments"] = await db["erp_payments"].find({"service_id": record["id"]}, {"_id": 0}).to_list(1000)
            # "Get Customers by Service": every service has its own customer list —
            # customers assigned this service directly (customer.service_id) plus
            # customers who booked this service (erp_bookings.service_id), deduped.
            customer_ids = {b.get("customer_id") for b in result["bookings"] if b.get("customer_id")}
            direct_customers = await db["erp_customers"].find({"service_id": record["id"]}, {"_id": 0}).to_list(2000)
            customer_ids.update(c["id"] for c in direct_customers if c.get("id"))
            if customer_ids:
                result["customers"] = await db["erp_customers"].find({"id": {"$in": list(customer_ids)}}, {"_id": 0}).to_list(2000)
        elif entity == "agent":
            result["bookings"] = await db["erp_bookings"].find({"agent_id": record["id"]}, {"_id": 0}).to_list(500)
            result["payments"] = await db["erp_payments"].find({"agent_id": record["id"]}, {"_id": 0}).to_list(1000)
        elif entity == "invoice":
            result["customer"] = await find_one("erp_customers", "id", record.get("customer_id"))
            result["booking"] = await find_one("erp_bookings", "id", record.get("booking_id"))
            result["service"] = await find_one("erp_services", "id", record.get("service_id"))
            result["payments"] = await db["erp_payments"].find(
                {"$or": [{"invoice_id": record.get("id")}, {"invoice_no": record.get("invoice_no")}]},
                {"_id": 0}
            ).to_list(1000)
        elif entity == "payment":
            result["customer"] = await find_one("erp_customers", "id", record.get("customer_id"))
            result["booking"] = await find_one("erp_bookings", "id", record.get("booking_id"))
            result["service"] = await find_one("erp_services", "id", record.get("service_id"))
            result["invoice"] = await find_one("erp_invoices", "id", record.get("invoice_id"))
            if not result["invoice"] and record.get("invoice_no"):
                result["invoice"] = await find_one("erp_invoices", "invoice_no", record.get("invoice_no"))

        return _ok(result, message="Relationships loaded")

    @router.get("/admin/invoices/{invoice_id}/details")
    async def invoice_details(invoice_id: str, user: dict = Depends(get_current_user)):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        invoice = await db["erp_invoices"].find_one({"id": invoice_id}, {"_id": 0})
        if not invoice:
            invoice = await db["erp_invoices"].find_one({"invoice_no": invoice_id}, {"_id": 0})
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
        customer = None
        if invoice.get("customer_id"):
            customer = await db["erp_customers"].find_one({"id": invoice["customer_id"]}, {"_id": 0})
        booking = None
        if invoice.get("booking_id"):
            booking = await db["erp_bookings"].find_one({"id": invoice["booking_id"]}, {"_id": 0})
        service, service_id, service_name = await _resolve_service(invoice, booking)
        paid, payments = await _invoice_paid_amount(invoice)
        total = float(invoice.get("total") or 0)
        return _ok({
            "invoice": invoice,
            "customer": customer,
            "booking": booking,
            "service": service,
            "service_id": service_id,
            "service_name": service_name,
            "payments": payments,
            "financials": {"total": round(total, 2), "paid": paid, "balance": round(max(total - paid, 0), 2)},
        }, message="Invoice details loaded")

    @router.get("/admin/payments/{payment_id}/details")
    async def payment_details(payment_id: str, user: dict = Depends(get_current_user)):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        payment = await db["erp_payments"].find_one({"id": payment_id}, {"_id": 0})
        if not payment:
            payment = await db["erp_payments"].find_one({"payment_id": payment_id}, {"_id": 0})
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        customer = None
        if payment.get("customer_id"):
            customer = await db["erp_customers"].find_one({"id": payment["customer_id"]}, {"_id": 0})
        invoice = None
        if payment.get("invoice_id"):
            invoice = await db["erp_invoices"].find_one({"id": payment["invoice_id"]}, {"_id": 0})
        if not invoice and payment.get("invoice_no"):
            invoice = await db["erp_invoices"].find_one({"invoice_no": payment["invoice_no"]}, {"_id": 0})
        booking = None
        if payment.get("booking_id"):
            booking = await db["erp_bookings"].find_one({"id": payment["booking_id"]}, {"_id": 0})
        service, service_id, service_name = await _resolve_service(invoice or {}, booking)
        financials = None
        if invoice:
            state = await _invoice_financial_state(invoice)
            financials = {"total": round(float(invoice.get("total") or 0), 2), "paid": state["paid_amount"], "balance": state["balance"], "payment_status": state["payment_status"]}
        return _ok({
            "payment": payment, "customer": customer, "invoice": invoice, "booking": booking,
            "service": service, "service_id": service_id, "service_name": service_name,
            "financials": financials,
        }, message="Payment details loaded")

    async def _agent_earnings_summary(agent_name: str, agent_id: str = None):
        if agent_id:
            commission_query = {"$or": [
                {"agent_id": agent_id},
                {"agent_id": {"$exists": False}, "agent": agent_name},
                {"agent_id": {"$exists": False}, "agent_name": agent_name},
            ]}
            payment_query = {"payment_type": "Agent Commission", "status": {"$in": ["Completed", "Paid", "Partial"]},
                             "$or": [
                                 {"agent_id": agent_id},
                                 {"agent_id": {"$exists": False}, "agent": agent_name},
                             ]}
        else:
            commission_query = {"$or": [{"agent": agent_name}, {"agent_name": agent_name}]}
            payment_query = {"payment_type": "Agent Commission", "status": {"$in": ["Completed", "Paid", "Partial"]},
                             "$or": [{"agent": agent_name}, {"agent_name": agent_name}]}
        commissions = await db["erp_commissions"].find(commission_query, {"_id": 0}).to_list(5000)
        earned = round(sum(float(c.get("amount") or 0) for c in commissions), 2)
        payments = await db["erp_payments"].find(
            payment_query,
            {"_id": 0}
        ).to_list(5000)
        paid_records = round(sum(float(p.get("total") or p.get("amount") or 0) for p in payments), 2)
        paid = paid_records
        return {
            "agent": agent_name,
            "total_earned": earned,
            "total_paid": paid,
            "due_amount": round(max(earned - paid, 0), 2),
            "commissions": commissions,
            "payments": payments,
        }

    @router.get("/agent/earnings-summary")
    async def agent_earnings_summary(user: dict = Depends(get_current_user)):
        if user.get("role") == "agent":
            agent_name = user.get("name")
            agent_id = (user.get("meta") or {}).get("agent_id")
            if agent_id:
                agent = await db["erp_agents"].find_one({"id": agent_id}, {"_id": 0, "name": 1})
                if agent and agent.get("name"):
                    agent_name = agent["name"]
        elif user.get("role") == "admin":
            agent_name = None
        else:
            raise HTTPException(status_code=403, detail="Agent or admin access required")
        if not agent_name:
            agents = await db["erp_agents"].find({}, {"_id": 0, "name": 1, "id": 1}).to_list(500)
            return _ok({"agents": [await _agent_earnings_summary(a.get("name"), a.get("id")) for a in agents if a.get("name")]}, message="Agent earnings loaded")
        return _ok(await _agent_earnings_summary(agent_name), message="Agent earnings loaded")

    async def _task_payable_amount(task: dict):
        for key in ("payable_amount", "agent_payable", "commission_amount", "agent_amount", "amount", "fee", "estimated_fee"):
            try:
                value = float(task.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return round(value, 2)
        booking_id = task.get("booking_id")
        if booking_id:
            booking = await db["erp_bookings"].find_one({"id": str(booking_id)}, {"_id": 0, "estimated_fee": 1})
            if booking:
                try:
                    return round(float(booking.get("estimated_fee") or 0), 2)
                except (TypeError, ValueError):
                    pass
        return 0.0

    @router.get("/admin/agent-tasks/payable")
    async def list_payable_agent_tasks(user: dict = Depends(get_current_user)):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        tasks = await db["erp_tasks"].find({"status": {"$in": ["Completed", "completed", "Done", "done"]}}, {"_id": 0}).to_list(5000)
        result = []
        for task in tasks:
            agent_id = task.get("agent_id")
            agent_name = task.get("agent") or task.get("assigned_agent") or task.get("agent_name")
            if not agent_id and agent_name:
                agent_id = await _resolve_agent_id(name=agent_name)
            if not agent_id:
                continue
            amount = await _task_payable_amount(task)
            if amount <= 0:
                continue
            paid = await db["erp_payments"].find_one({"payment_type": "Agent Commission", "task_id": str(task.get("id"))}, {"_id": 0, "id": 1})
            if paid:
                continue
            result.append({
                "id": task.get("id"), "task_id": task.get("task_id") or task.get("id"),
                "title": task.get("title") or task.get("task_name") or task.get("name") or task.get("description") or task.get("id"),
                "agent_id": agent_id, "agent": agent_name, "booking_id": task.get("booking_id"),
                "service_id": task.get("service_id"), "completed_date": task.get("completed_date") or task.get("completed_at") or task.get("updated_at"),
                "payable_amount": amount, "status": task.get("status"),
            })
        return _ok(result, message="Completed payable agent tasks loaded")

    @router.post("/admin/agent-payments")
    async def record_agent_payment(body: dict = Body(...), user: dict = Depends(get_current_user)):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        task_id = str(body.get("task_id") or "").strip()
        task = None
        if task_id:
            task = await db["erp_tasks"].find_one({"id": task_id}, {"_id": 0})
            if not task:
                task = await db["erp_tasks"].find_one({"task_id": task_id}, {"_id": 0})
            if not task or str(task.get("status", "")).lower() not in {"completed", "done"}:
                raise HTTPException(status_code=409, detail="Only completed tasks can become payable")
            if await db["erp_payments"].find_one({"payment_type": "Agent Commission", "task_id": str(task.get("id"))}, {"_id": 0}):
                raise HTTPException(status_code=409, detail="This completed task has already been paid")
        agent_id = str(body.get("agent_id") or (task or {}).get("agent_id") or "").strip()
        agent = str(body.get("agent") or (task or {}).get("agent") or (task or {}).get("assigned_agent") or (task or {}).get("agent_name") or "").strip()
        if agent_id:
            agent_doc = await db["erp_agents"].find_one({"id": agent_id}, {"_id": 0})
            if not agent_doc:
                raise HTTPException(status_code=404, detail="Agent not found")
            agent = agent_doc.get("name") or agent
        elif agent:
            agent_id = await _resolve_agent_id(name=agent)
            if not agent_id:
                raise HTTPException(status_code=422, detail="A valid agent_id is required")
        else:
            raise HTTPException(status_code=422, detail="Agent is required")
        task_amount = await _task_payable_amount(task) if task else 0
        amount = round(float(body.get("amount") or body.get("total") or task_amount), 2)
        if task and task_amount <= 0:
            raise HTTPException(status_code=422, detail="Completed task has no payable amount configured")
        if task and abs(amount - task_amount) > 0.009:
            raise HTTPException(status_code=400, detail=f"Task payable amount is ₹{task_amount:.2f}; payment must match it")
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be greater than zero")
        summary = await _agent_earnings_summary(agent, agent_id)
        # A task can become the source of a commission exactly once. This
        # preserves the existing earnings/payment system instead of creating a
        # disconnected payment ledger.
        if task:
            existing_commission = await db["erp_commissions"].find_one({"task_id": str(task.get("id"))}, {"_id": 0})
            if not existing_commission:
                commission_id = f"COM-{uuid.uuid4().hex[:8].upper()}"
                await db["erp_commissions"].insert_one({
                    "id": commission_id, "commission_id": commission_id, "agent_id": agent_id,
                    "agent": agent, "amount": amount, "task_id": str(task.get("id")),
                    "booking_id": task.get("booking_id"), "service_id": task.get("service_id"),
                    "status": "Completed", "earned_date": task.get("completed_date") or task.get("completed_at") or _ist_today_str(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                summary = await _agent_earnings_summary(agent, agent_id)
        if amount > summary["due_amount"] + 0.009:
            raise HTTPException(status_code=400, detail=f"Payment exceeds agent due amount of ₹{summary['due_amount']:.2f}")
        reference = str(body.get("reference_no") or "").strip()
        if reference and await db["erp_payments"].find_one({"payment_type": "Agent Commission", "reference_no": reference}, {"_id": 0}):
            raise HTTPException(status_code=409, detail="This agent payment reference is already recorded")
        payment_id = str(body.get("payment_id") or "").strip() or f"APM-{uuid.uuid4().hex[:8].upper()}"
        if await db["erp_payments"].find_one({"$or": [{"id": payment_id}, {"payment_id": payment_id}]}, {"_id": 0}):
            raise HTTPException(status_code=409, detail="This payment ID already exists")
        date_str = str(body.get("txn_date") or _ist_today_str())[:10]
        doc = {
            "id": payment_id, "payment_id": payment_id, "payment_type": "Agent Commission",
            "agent": agent, "agent_id": agent_id, "task_id": str(task.get("id")) if task else None,
            "booking_id": (task or {}).get("booking_id"), "service_id": (task or {}).get("service_id"),
            "completed_date": (task or {}).get("completed_date") or (task or {}).get("completed_at") if task else None,
            "amount": amount, "total": amount, "mode": body.get("mode") or "Bank Transfer",
            "reference_no": reference, "txn_date": date_str, "status": "Completed", "payment_status": "Paid",
            "remarks": body.get("remarks") or ("Completed task payment" if task else "Agent commission payment"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await db["erp_payments"].insert_one(doc)
        except Exception:
            await db["erp_payments"].delete_one({"id": payment_id})
            raise HTTPException(status_code=500, detail="Agent payment could not be recorded")
        return _ok(doc, message="Agent payment recorded")

    @router.get("/admin/synchronization/summary")
    async def synchronization_summary(user: dict = Depends(get_current_user)):
        """Authoritative Phase-6 snapshot for integration verification and support."""
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        invoices = await db["erp_invoices"].find({}, {"_id": 0}).to_list(10000)
        for invoice in invoices:
            state = await _invoice_financial_state(invoice)
            invoice.update({"paid_amount": state["paid_amount"], "balance": state["balance"], "payment_status": state["payment_status"]})
        payments = await db["erp_payments"].find({}, {"_id": 0}).to_list(10000)
        bookings = await db["erp_bookings"].find({}, {"_id": 0}).to_list(10000)
        customers = await db["erp_customers"].find({}, {"_id": 0}).to_list(10000)
        agents = await db["erp_agents"].find({}, {"_id": 0}).to_list(10000)
        attendance = await db["erp_attendance"].find({}, {"_id": 0}).to_list(10000)
        service_counts = {}
        for row in bookings:
            key = row.get("service") or row.get("service_name") or "Other"
            service_counts[key] = service_counts.get(key, 0) + 1
        return _ok({
            "source_of_truth": {
                "customer_service": "erp_customers.service_id / service_type",
                "booking_status": "erp_bookings.status",
                "agent_account_status": "erp_agents.status",
                "agent_attendance": "erp_attendance.status (attendance records; never agent status)",
                "invoice_amount": "erp_invoices.total",
                "invoice_paid_amount": "sum of eligible erp_payments for the invoice",
                "invoice_balance": "erp_invoices.balance derived from invoice total minus eligible payments",
                "payment_status": "erp_payments.status",
                "agent_earnings": "sum of erp_commissions.amount by agent_id",
                "agent_payments": "erp_payments where payment_type = Agent Commission",
            },
            "counts": {
                "customers": len(customers), "bookings": len(bookings), "invoices": len(invoices),
                "payments": len(payments), "agents": len(agents), "attendance": len(attendance),
            },
            "financials": {
                "invoiced": round(sum(float(i.get("total") or 0) for i in invoices), 2),
                "paid": round(sum(float(i.get("paid_amount") or 0) for i in invoices), 2),
                "balance": round(sum(float(i.get("balance") or 0) for i in invoices), 2),
            },
            "service_counts": service_counts,
        }, message="System synchronization snapshot loaded")

    # ---------------- Revenue module ----------------
    def _revenue_amount(value):
        try:
            return round(float(value or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    def _revenue_date(value):
        raw = str(value or "")[:10]
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _revenue_date_value(row):
        return row.get("txn_date") or row.get("payment_date") or row.get("paid_date") or row.get("created_at") or row.get("invoice_date") or row.get("issue_date")

    def _revenue_person_fields(source, prefix=""):
        return {
            "employee_id": source.get("employee_id"),
            "employee_name": source.get("employee") or source.get("employee_name") or source.get("assigned_employee"),
            "agent_id": source.get("agent_id"),
            "agent_name": source.get("agent") or source.get("agent_name") or source.get("assigned_agent"),
        }

    REVENUE_COLLECTED_STATUSES = {"completed", "paid", "partial"}      # same set _billing_for_customers uses
    REVENUE_INVALID_INVOICE_STATUSES = {"cancelled", "canceled", "void"}
    REVENUE_OUTFLOW_TYPES = {"agent commission", "commission", "payout", "salary", "expense", "refund"}

    def _is_revenue_payment(payment):
        """True only for customer money actually collected: not an agent payout, not pending/failed/cancelled."""
        if str(payment.get("payment_type") or "").strip().lower() in REVENUE_OUTFLOW_TYPES:
            return False
        status = str(payment.get("status") or payment.get("payment_status") or "Completed").strip().lower()
        if status not in REVENUE_COLLECTED_STATUSES:
            return False
        return _revenue_amount(payment.get("total") if payment.get("total") is not None else payment.get("amount")) > 0

    async def _revenue_context():
        """Load the master data once so relationships are resolved by ID (names only as a legacy fallback)."""
        invoices = await db["erp_invoices"].find({}, {"_id": 0}).to_list(20000)
        bookings = await db["erp_bookings"].find({}, {"_id": 0}).to_list(30000)
        customers = await db["erp_customers"].find({}, {"_id": 0}).to_list(20000)
        employees = await db["erp_employees"].find({}, {"_id": 0, "id": 1, "name": 1}).to_list(5000)
        agents = await db["erp_agents"].find({}, {"_id": 0, "id": 1, "name": 1}).to_list(5000)
        invoice_by_ref = {}
        for inv in invoices:
            for key in (inv.get("id"), inv.get("invoice_id"), inv.get("invoice_no")):
                if key:
                    invoice_by_ref[str(key)] = inv
        return {
            "invoices": invoices, "invoice_by_ref": invoice_by_ref,
            "booking_by_id": {str(b.get("id")): b for b in bookings if b.get("id")},
            "customer_by_id": {str(c.get("id")): c for c in customers if c.get("id")},
            "employee_by_id": {str(e.get("id")): e for e in employees if e.get("id")},
            "agent_by_id": {str(a.get("id")): a for a in agents if a.get("id")},
            "employee_id_by_name": {str(e.get("name") or "").strip().lower(): e["id"] for e in employees if e.get("id") and e.get("name")},
            "agent_id_by_name": {str(a.get("name") or "").strip().lower(): a["id"] for a in agents if a.get("id") and a.get("name")},
        }

    def _resolve_people(ctx, *sources):
        """First source that carries an ID wins; names are only used to look an ID up, never to group."""
        emp_id = agent_id = emp_name = agent_name = None
        for src in sources:
            if not src:
                continue
            emp_id = emp_id or src.get("employee_id") or src.get("assigned_employee_id")
            agent_id = agent_id or src.get("agent_id") or src.get("assigned_agent_id")
            emp_name = emp_name or src.get("employee") or src.get("employee_name") or src.get("assigned_employee")
            agent_name = agent_name or src.get("agent") or src.get("agent_name") or src.get("assigned_agent")
        if not emp_id and emp_name:
            emp_id = ctx["employee_id_by_name"].get(str(emp_name).strip().lower())
        if not agent_id and agent_name:
            agent_id = ctx["agent_id_by_name"].get(str(agent_name).strip().lower())
        if emp_id and str(emp_id) in ctx["employee_by_id"]:
            emp_name = ctx["employee_by_id"][str(emp_id)].get("name") or emp_name
        if agent_id and str(agent_id) in ctx["agent_by_id"]:
            agent_name = ctx["agent_by_id"][str(agent_id)].get("name") or agent_name
        return emp_id, emp_name, agent_id, agent_name

    async def _build_revenue_rows(start_date=None, end_date=None, ctx=None):
        """Collected-revenue transactions from the existing invoice/payment source of truth (erp_payments).

        * Each payment is counted once (id / payment_id / invoice+reference_no de-duplication).
        * Agent commission payouts, pending/failed/cancelled/refunded payments and payments of cancelled
          invoices are not revenue.
        * A fully-paid invoice with no payment record falls back to the invoice total exactly once.
        """
        ctx = ctx or await _revenue_context()
        invoices, invoice_by_ref = ctx["invoices"], ctx["invoice_by_ref"]
        payments = await db["erp_payments"].find({}, {"_id": 0, "file_data": 0}).to_list(30000)
        rows, linked_invoice_ids, seen = [], set(), set()

        def in_range(event_date):
            if (start_date or end_date) and not event_date:
                return False
            if start_date and event_date < start_date:
                return False
            if end_date and event_date > end_date:
                return False
            return True

        for payment in payments:
            if not _is_revenue_payment(payment):
                continue
            keys = {("id", str(payment.get("id")))}
            if payment.get("payment_id"):
                keys.add(("pid", str(payment.get("payment_id"))))
            ref_no = str(payment.get("reference_no") or "").strip()
            if ref_no:
                keys.add(("ref", str(payment.get("invoice_no") or payment.get("invoice_id") or ""), ref_no))
            if keys & seen:
                continue
            seen |= keys
            amount = _revenue_amount(payment.get("total") if payment.get("total") is not None else payment.get("amount"))
            ref = payment.get("invoice_id") or payment.get("invoice_no")
            invoice = invoice_by_ref.get(str(ref)) if ref else None
            if invoice and str(invoice.get("payment_status") or invoice.get("status") or "").strip().lower() in REVENUE_INVALID_INVOICE_STATUSES:
                continue
            event_date = _revenue_date(_revenue_date_value(payment)) or _revenue_date((invoice or {}).get("invoice_date"))
            if not in_range(event_date):
                continue
            source = invoice or {}
            booking = ctx["booking_by_id"].get(str(payment.get("booking_id") or source.get("booking_id") or ""))
            customer_id = payment.get("customer_id") or source.get("customer_id") or (booking or {}).get("customer_id")
            customer = ctx["customer_by_id"].get(str(customer_id)) if customer_id else None
            emp_id, emp_name, agent_id, agent_name = _resolve_people(ctx, payment, source, booking, customer)
            customer_name = (customer or {}).get("business_name") or payment.get("customer") or source.get("customer") or (booking or {}).get("customer")
            rows.append({
                "id": payment.get("id") or payment.get("payment_id") or f"PAY-{len(rows)+1}",
                "payment_id": payment.get("payment_id") or payment.get("id"),
                "invoice_id": source.get("id") or source.get("invoice_id") or payment.get("invoice_id"),
                "invoice_no": payment.get("invoice_no") or source.get("invoice_no") or source.get("id"),
                "booking_id": payment.get("booking_id") or source.get("booking_id"),
                "service_id": payment.get("service_id") or source.get("service_id") or (booking or {}).get("service_id"),
                "customer_id": customer_id, "customer_name": customer_name,
                "amount": amount, "date": event_date.isoformat() if event_date else None,
                "service": payment.get("service") or source.get("service") or source.get("service_name") or (booking or {}).get("service"),
                "employee_id": emp_id, "employee_name": emp_name,
                "agent_id": agent_id, "agent_name": agent_name,
                "payment_method": payment.get("payment_method") or payment.get("mode") or payment.get("method"),
                "reference_no": payment.get("reference_no") or payment.get("transaction_reference"),
                "status": payment.get("payment_status") or payment.get("status") or "Paid",
            })
            for key in (source.get("id"), source.get("invoice_id"), source.get("invoice_no"), payment.get("invoice_id"), payment.get("invoice_no")):
                if key:
                    linked_invoice_ids.add(str(key))

        # Legacy invoices marked Paid without any payment record count once, by invoice.
        for invoice in invoices:
            if str(invoice.get("payment_status") or invoice.get("status") or "").strip().lower() in REVENUE_INVALID_INVOICE_STATUSES:
                continue
            if str(invoice.get("payment_status") or "").strip().lower() != "paid":
                continue
            refs = [str(x) for x in (invoice.get("id"), invoice.get("invoice_id"), invoice.get("invoice_no")) if x]
            if any(x in linked_invoice_ids for x in refs):
                continue
            amount = _revenue_amount(invoice.get("total"))
            if amount <= 0:
                continue
            event_date = _revenue_date(invoice.get("paid_date") or invoice.get("invoice_date") or invoice.get("issue_date") or invoice.get("created_at"))
            if not in_range(event_date):
                continue
            booking = ctx["booking_by_id"].get(str(invoice.get("booking_id") or ""))
            customer = ctx["customer_by_id"].get(str(invoice.get("customer_id") or ""))
            emp_id, emp_name, agent_id, agent_name = _resolve_people(ctx, invoice, booking, customer)
            rows.append({
                "id": invoice.get("id") or invoice.get("invoice_no"), "payment_id": None,
                "invoice_id": invoice.get("id"), "invoice_no": invoice.get("invoice_no") or invoice.get("id"),
                "booking_id": invoice.get("booking_id"), "service_id": invoice.get("service_id"),
                "customer_id": invoice.get("customer_id"),
                "customer_name": (customer or {}).get("business_name") or invoice.get("customer"),
                "amount": amount, "date": event_date.isoformat() if event_date else None,
                "service": invoice.get("service") or invoice.get("service_name"),
                "employee_id": emp_id, "employee_name": emp_name, "agent_id": agent_id, "agent_name": agent_name,
                "payment_method": None, "status": "Paid",
            })
        return rows

    def _revenue_period(period, start, end):
        from datetime import date
        today = _ist_now().date()
        period = str(period or "all").strip().lower()
        if period in {"today", "day"}:
            return today, today
        if period in {"week", "this week"}:
            return today - timedelta(days=today.weekday()), today
        if period in {"month", "this month"}:
            return today.replace(day=1), today
        if period in {"year", "this year"}:
            return today.replace(month=1, day=1), today
        def parse(v):
            if not v: return None
            try: return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
            except ValueError: raise HTTPException(status_code=422, detail="Revenue dates must use YYYY-MM-DD format")
        parsed_start, parsed_end = parse(start), parse(end)
        if period == "custom" and (not parsed_start or not parsed_end):
            raise HTTPException(status_code=422, detail="Custom revenue period requires both start_date and end_date")
        if parsed_start and parsed_end and parsed_start > parsed_end:
            raise HTTPException(status_code=422, detail="Revenue start_date cannot be after end_date")
        return parsed_start, parsed_end

    async def _revenue_response(kind, user, search=None, sort="revenue", order="desc", page=1, page_size=25, period="all", start_date=None, end_date=None, rows=None):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        from collections import defaultdict
        start, end = _revenue_period(period, start_date, end_date)
        if rows is None:
            rows = await _build_revenue_rows(start, end)
        groups = defaultdict(lambda: {"revenue": 0.0, "transactions": 0, "dates": []})
        for row in rows:
            if kind == "employee":
                key = row.get("employee_id") or row.get("employee_name")
                name = row.get("employee_name") or "Unassigned"
            elif kind == "agent":
                key = row.get("agent_id") or row.get("agent_name")
                name = row.get("agent_name") or "Unassigned"
            else:
                key = row.get("customer_id") or row.get("customer_name")
                name = row.get("customer_name") or "Unknown Customer"
            if not key:
                continue
            g = groups[str(key)]
            g["name"] = name; g["id"] = key; g["revenue"] += row["amount"]; g["transactions"] += 1
            if row.get("date"): g["dates"].append(row["date"])
        total = round(sum(x["revenue"] for x in groups.values()), 2)
        data = []
        for g in groups.values():
            data.append({"id": g["id"], "name": g["name"], "revenue": round(g["revenue"], 2), "transactions": g["transactions"], "contribution": round((g["revenue"] / total * 100) if total else 0, 2), "last_date": max(g["dates"]) if g["dates"] else None})
        # The detailed views are intentionally complete: people/customers with zero
        # collected revenue remain visible instead of disappearing from the report.
        master_collection = {"employee": "erp_employees", "agent": "erp_agents", "customer": "erp_customers"}[kind]
        name_field = "name" if kind != "customer" else "business_name"
        masters = await db[master_collection].find({}, {"_id": 0}).to_list(5000)
        master_by_id = {str(m.get("id")): m for m in masters if m.get("id") is not None}
        for row in data:
            master = master_by_id.get(str(row.get("id"))) or {}
            row["profile"] = {
                "name": master.get(name_field) or master.get("name") or master.get("business_name") or row.get("name"),
                "email": master.get("email"), "mobile": master.get("mobile") or master.get("phone"),
                "department": master.get("department"), "designation": master.get("designation"),
                "status": master.get("status"), "owner": master.get("owner"),
                "assigned_employee": master.get("assigned_employee"), "assigned_agent": master.get("assigned_agent"),
                "service_type": master.get("service_type"), "commission_percentage": master.get("commission_percentage"),
            }
            row["name"] = row["profile"].get("name") or row.get("name")
        existing_ids = {str(x["id"]) for x in data if x.get("id") is not None}
        for master in masters:
            mid = master.get("id")
            if mid is None or str(mid) in existing_ids:
                continue
            data.append({"id": mid, "name": master.get(name_field) or "Unnamed", "revenue": 0.0, "transactions": 0, "contribution": 0.0, "last_date": None,
                         "profile": {"name": master.get(name_field) or master.get("name") or master.get("business_name"),
                                     "email": master.get("email"), "mobile": master.get("mobile") or master.get("phone"),
                                     "department": master.get("department"), "designation": master.get("designation"),
                                     "status": master.get("status"), "owner": master.get("owner"),
                                     "assigned_employee": master.get("assigned_employee"), "assigned_agent": master.get("assigned_agent"),
                                     "service_type": master.get("service_type"), "commission_percentage": master.get("commission_percentage")}})
        if kind == "customer":
            billing = await _billing_for_customers([str(m.get("id")) for m in masters if m.get("id")])
            for row in data:
                state = billing.get(str(row.get("id"))) or {}
                row["total_billed"] = round(float(state.get("total_billed") or 0), 2)
                row["paid_amount"] = round(float(state.get("total_paid") or 0), 2)
                row["outstanding"] = round(max(float(state.get("outstanding") or 0), 0), 2)
                row["invoice_count"] = state.get("invoice_count", 0)
                services = []
                for item in state.get("items") or []:
                    name = item.get("service")
                    if name and name not in services:
                        services.append(name)
                for r in rows:
                    if str(r.get("customer_id") or "") == str(row.get("id")) and r.get("service") and r["service"] not in services:
                        services.append(r["service"])
                row["services"] = services
                mine = [r for r in rows if str(r.get("customer_id") or "") == str(row.get("id"))]
                latest = max(mine, key=lambda r: str(r.get("date") or ""), default=None)
                row["latest_payment"] = {"date": latest.get("date"), "amount": latest.get("amount"), "invoice_no": latest.get("invoice_no")} if latest else None
        term = str(search or "").strip().lower()
        if term:
            data = [x for x in data if term in str(x["name"]).lower() or term in str(x["id"]).lower()]
        reverse = str(order).lower() != "asc"
        if sort == "name": data.sort(key=lambda x: str(x["name"]).lower(), reverse=reverse)
        elif sort == "transactions": data.sort(key=lambda x: x["transactions"], reverse=reverse)
        else: data.sort(key=lambda x: x["revenue"], reverse=reverse)
        total_rows = len(data)
        offset = (page - 1) * page_size
        return _ok({"kind": kind, "period": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None}, "total_revenue": total, "contributing_count": total_rows, "rows": data[offset:offset + page_size]}, message=f"{kind.title()} revenue loaded", pagination={"page": page, "page_size": page_size, "total": total_rows, "pages": (total_rows + page_size - 1) // page_size})

    @router.get("/revenue")
    async def revenue_summary(period: str = Query("all"), start_date: str = Query(None), end_date: str = Query(None), user: dict = Depends(get_current_user)):
        if user.get("role") != "admin": raise HTTPException(status_code=403, detail="Admin access required")
        start, end = _revenue_period(period, start_date, end_date)
        rows = await _build_revenue_rows(start, end)
        total = round(sum(r["amount"] for r in rows), 2)
        async def count_kind(kind):
            ids = set()
            for r in rows:
                key = r.get(f"{kind}_id") or r.get(f"{kind}_name")
                if key: ids.add(str(key))
            return len(ids)
        employee = await _revenue_response("employee", user, page=1, page_size=500, period=period, start_date=start_date, end_date=end)
        agent = await _revenue_response("agent", user, page=1, page_size=500, period=period, start_date=start_date, end_date=end)
        customer = await _revenue_response("customer", user, page=1, page_size=500, period=period, start_date=start_date, end_date=end)
        today = _ist_now().date()
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)
        today_rows = await _build_revenue_rows(today, today)
        month_rows = await _build_revenue_rows(month_start, today)
        year_rows = await _build_revenue_rows(year_start, today)
        customers = await db["erp_customers"].find({}, {"_id": 0, "id": 1}).to_list(10000)
        billing = await _billing_for_customers([str(c["id"]) for c in customers if c.get("id")])
        pending = round(sum(max(float(v.get("outstanding") or 0), 0) for v in billing.values()), 2)
        return _ok({"total_revenue": total, "transactions": len(rows),
                    "today_revenue": round(sum(r["amount"] for r in today_rows), 2),
                    "today_transactions": len(today_rows),
                    "pending_revenue": pending,
                    "pending_customers": sum(1 for v in billing.values() if float(v.get("outstanding") or 0) > 0),
                    "month_revenue": round(sum(r["amount"] for r in month_rows), 2),
                    "month_transactions": len(month_rows),
                    "year_revenue": round(sum(r["amount"] for r in year_rows), 2),
                    "year_transactions": len(year_rows),
                    "employee": employee["data"], "agent": agent["data"], "customer": customer["data"]}, message="Revenue summary loaded")

    @router.get("/revenue/transactions")
    async def revenue_transactions(period: str = Query("all"), search: str = Query(None), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500), user: dict = Depends(get_current_user)):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        if str(period or "all").lower() == "pending":
            customers = await db["erp_customers"].find({}, {"_id": 0}).to_list(10000)
            billing = await _billing_for_customers([str(c.get("id")) for c in customers if c.get("id")])
            by_id = {str(c.get("id")): c for c in customers if c.get("id")}
            rows = []
            for cid, state in billing.items():
                outstanding = round(max(float(state.get("outstanding") or 0), 0), 2)
                if outstanding <= 0:
                    continue
                c = by_id.get(cid) or {}
                rows.append({"id": cid, "customer_id": cid,
                    "customer_name": c.get("business_name") or c.get("owner") or c.get("name") or cid,
                    "employee_name": c.get("assigned_employee"), "agent_name": c.get("assigned_agent"),
                    "service": c.get("service_type"), "amount": round(float(state.get("total_billed") or 0), 2),
                    "total_billed": round(float(state.get("total_billed") or 0), 2),
                    "paid_amount": round(float(state.get("total_paid") or 0), 2), "pending_amount": outstanding,
                    "date": c.get("updated_at") or c.get("created_at"), "status": "Pending",
                    "invoice_count": state.get("invoice_count", 0), "payment_count": state.get("payment_count", 0)})
            rows.sort(key=lambda r: (-r["pending_amount"], str(r["customer_name"]).lower()))
        else:
            start, end = _revenue_period(period, None, None)
            rows = await _build_revenue_rows(start, end)
            term = str(search or "").strip().lower()
            if term:
                rows = [r for r in rows if any(term in str(r.get(k) or "").lower() for k in ("id", "invoice_no", "customer_name", "employee_name", "agent_name", "service"))]
            rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("id") or "")), reverse=True)
        total = len(rows)
        offset = (page - 1) * page_size
        return _ok(rows[offset:offset + page_size], message="Revenue details loaded", pagination={"page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size})

    @router.get("/revenue/employees")
    async def revenue_employees(search: str = Query(None), sort: str = Query("revenue"), order: str = Query("desc"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500), period: str = Query("all"), start_date: str = Query(None), end_date: str = Query(None), user: dict = Depends(get_current_user)):
        return await _revenue_response("employee", user, search, sort, order, page, page_size, period, start_date, end_date)

    @router.get("/revenue/agents")
    async def revenue_agents(search: str = Query(None), sort: str = Query("revenue"), order: str = Query("desc"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500), period: str = Query("all"), start_date: str = Query(None), end_date: str = Query(None), user: dict = Depends(get_current_user)):
        return await _revenue_response("agent", user, search, sort, order, page, page_size, period, start_date, end_date)

    @router.get("/revenue/customers")
    async def revenue_customers(search: str = Query(None), sort: str = Query("revenue"), order: str = Query("desc"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500), period: str = Query("all"), start_date: str = Query(None), end_date: str = Query(None), user: dict = Depends(get_current_user)):
        return await _revenue_response("customer", user, search, sort, order, page, page_size, period, start_date, end_date)

    # ---------------- Revenue CSV import ----------------
    # Imports payment records into the existing erp_payments collection (the revenue source of truth).
    # Admin only. Every row is validated first; invalid rows are reported and skipped, duplicates
    # (Payment ID, customer+reference, invoice+reference, or repeated rows in the same file) are never inserted.
    REVENUE_IMPORT_STATUSES = {"completed": "Completed", "paid": "Paid", "partial": "Partial", "pending": "Pending", "failed": "Failed"}
    REVENUE_IMPORT_ALIASES = {
        "payment_id": ("payment_id", "payment id", "id"),
        "customer_id": ("customer_id", "customer id"),
        "customer": ("customer", "customer_name", "customer name", "client"),
        "amount": ("amount", "total", "payment amount"),
        "payment_date": ("payment_date", "payment date", "date"),
        "payment_method": ("payment_method", "payment method", "method", "mode"),
        "status": ("status", "payment status"),
        "reference_no": ("reference_no", "reference", "reference no", "transaction_reference", "transaction reference"),
        "invoice_no": ("invoice_no", "invoice", "invoice no"),
        "term_name": ("term_name", "term", "payment term"),
        "notes": ("notes", "remarks"),
    }

    @router.post("/revenue/import")
    async def import_revenue(body: dict = Body(...), user: dict = Depends(get_current_user)):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Only an Admin can import revenue data")
        import csv, io
        text = str(body.get("csv") or "")
        if not text.strip():
            raise HTTPException(status_code=400, detail="The file is empty.")
        if len(text) > 5_000_000:
            raise HTTPException(status_code=400, detail="The file is too large (limit about 5 MB).")
        text = text.lstrip("\ufeff")
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            raise HTTPException(status_code=400, detail="The file is empty.")
        norm = [re.sub(r"\s+", " ", str(h or "").strip().lower()) for h in header]
        col = {}
        for field, aliases in REVENUE_IMPORT_ALIASES.items():
            for a in aliases:
                if a in norm:
                    col[field] = norm.index(a)
                    break
        missing = [label for label, ok in (
            ("customer (or customer_id)", "customer" in col or "customer_id" in col),
            ("amount", "amount" in col), ("payment_date", "payment_date" in col)) if not ok]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing required column(s): {', '.join(missing)}. Expected columns: customer or customer_id, amount, payment_date (optional: payment_id, payment_method, status, reference_no, invoice_no, term_name, notes).")
        data_rows = [(i + 2, r) for i, r in enumerate(reader) if any(str(c or "").strip() for c in r)]
        if not data_rows:
            raise HTTPException(status_code=400, detail="The file has no data rows.")
        if len(data_rows) > 2000:
            raise HTTPException(status_code=400, detail="Too many rows (limit 2000 per import).")

        customers = await db["erp_customers"].find({}, {"_id": 0, "id": 1, "business_name": 1, "name": 1, "owner": 1}).to_list(None)
        by_id = {str(c["id"]): c for c in customers if c.get("id")}
        by_name = {}
        for c in customers:
            for n in (c.get("business_name"), c.get("name"), c.get("owner")):
                if n:
                    by_name.setdefault(str(n).strip().lower(), []).append(c)

        def cell(r, field):
            i = col.get(field)
            return str(r[i]).strip() if i is not None and i < len(r) and r[i] is not None else ""

        errors, duplicates, to_insert = [], [], []
        seen_pid, seen_ref = set(), set()
        for line, r in data_rows:
            problems = []
            cid_raw, cname = cell(r, "customer_id"), cell(r, "customer")
            customer = None
            if cid_raw:
                customer = by_id.get(cid_raw)
                if not customer:
                    problems.append(f"customer_id '{cid_raw}' not found")
            elif cname:
                matches = by_name.get(cname.lower(), [])
                if len(matches) == 1:
                    customer = matches[0]
                elif not matches:
                    problems.append(f"customer '{cname}' not found")
                else:
                    problems.append(f"customer '{cname}' is ambiguous - use customer_id")
            else:
                problems.append("customer is required")
            try:
                amount = round(float(cell(r, "amount").replace(",", "").replace("₹", "")), 2)
                if amount != amount or amount <= 0 or amount in (float("inf"), float("-inf")):
                    raise ValueError
            except ValueError:
                amount = None
                problems.append("amount must be a number greater than zero")
            pdate = cell(r, "payment_date")[:10]
            try:
                datetime.fromisoformat(pdate)
            except ValueError:
                problems.append("payment_date must be a valid date (YYYY-MM-DD)")
            raw_status = cell(r, "status")
            status = REVENUE_IMPORT_STATUSES.get(raw_status.lower()) if raw_status else "Completed"
            if not status:
                problems.append(f"status '{raw_status}' is invalid (use Completed, Paid, Partial, Pending or Failed)")
            if problems:
                errors.append({"row": line, "message": "; ".join(problems)})
                continue
            cid = str(customer["id"])
            pid, ref, invoice_no = cell(r, "payment_id"), cell(r, "reference_no"), cell(r, "invoice_no")
            dup_reason = None
            if pid and (pid in seen_pid or await db["erp_payments"].find_one({"$or": [{"id": pid}, {"payment_id": pid}]}, {"_id": 0, "id": 1})):
                dup_reason = f"payment_id {pid} already exists"
            elif ref and ((cid, ref) in seen_ref or await db["erp_payments"].find_one({"customer_id": cid, "reference_no": ref}, {"_id": 0, "id": 1})):
                dup_reason = f"reference {ref} is already recorded for this customer"
            elif not pid and not ref and await db["erp_payments"].find_one({"customer_id": cid, "amount": amount, "payment_date": pdate, "term_name": cell(r, "term_name"), "invoice_no": invoice_no}, {"_id": 0, "id": 1}):
                dup_reason = "an identical payment already exists"
            if dup_reason:
                duplicates.append({"row": line, "message": dup_reason})
                continue
            if invoice_no:
                invoice = await db["erp_invoices"].find_one({"$or": [{"invoice_no": invoice_no}, {"id": invoice_no}]}, {"_id": 0})
                if not invoice:
                    errors.append({"row": line, "message": f"invoice '{invoice_no}' not found"}); continue
                if str(invoice.get("customer_id") or cid) != cid:
                    errors.append({"row": line, "message": f"invoice '{invoice_no}' belongs to a different customer"}); continue
                inv_state = await _invoice_financial_state(invoice)
                already = sum(x["amount"] for x in to_insert if x.get("invoice_no") == invoice.get("invoice_no") and x["status"] in {"Completed", "Paid", "Partial"})
                if status in {"Completed", "Paid", "Partial"} and amount + already > inv_state["balance"] + 0.009:
                    errors.append({"row": line, "message": f"amount exceeds invoice balance of {inv_state['balance']:.2f}"}); continue
                invoice_no = invoice.get("invoice_no") or invoice.get("id")
            new_id = pid or f"{COLLECTIONS['payments'][2]}-{uuid.uuid4().hex[:6].upper()}"
            if pid:
                seen_pid.add(pid)
            if ref:
                seen_ref.add((cid, ref))
            to_insert.append({
                "id": new_id, "payment_id": new_id, "customer_id": cid,
                "customer": customer.get("business_name") or customer.get("name") or customer.get("owner"),
                "amount": amount, "total": amount, "status": status,
                "payment_method": cell(r, "payment_method") or "Bank Transfer",
                "payment_date": pdate, "reference_no": ref, "transaction_reference": ref,
                "invoice_no": invoice_no or None, "term_name": cell(r, "term_name"), "notes": cell(r, "notes"),
                "recorded_at": datetime.now(timezone.utc).isoformat(), "imported": True,
            })
        imported_ids = []
        if to_insert:
            try:
                await db["erp_payments"].insert_many([dict(x) for x in to_insert], ordered=True)
            except Exception as e:
                logging.getLogger(__name__).exception("Revenue import failed")
                raise HTTPException(status_code=500, detail="The import could not be saved. No further records were processed.") from e
            imported_ids = [x["id"] for x in to_insert]
            for inv_no in {x["invoice_no"] for x in to_insert if x.get("invoice_no") and x["status"] in {"Completed", "Paid", "Partial"}}:
                invoice = await db["erp_invoices"].find_one({"$or": [{"invoice_no": inv_no}, {"id": inv_no}]}, {"_id": 0})
                if invoice:
                    synced = await _sync_invoice_financials(invoice["id"])
                    await _sync_booking_payment_status(synced or invoice)
            await _audit(user, "import", "payments", None, {"imported": len(imported_ids), "file": str(body.get("filename") or "")[:120]})
        return _ok({"imported": len(imported_ids), "duplicates": duplicates, "errors": errors, "total_rows": len(data_rows)},
                   message=f"{len(imported_ids)} record(s) imported")

    # ---------------- Admin Dashboard revenue cards (Employee / Customer / Agent) ----------------
    # These reuse the Revenue module's financial source of truth (_build_revenue_rows over erp_payments +
    # erp_invoices, and _billing_for_customers for billed/paid/outstanding). No new collection, no stored totals:
    # every call re-reads MongoDB, so a new payment shows up on the next dashboard load.
    def _require_admin_revenue(user):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")

    @router.get("/admin/dashboard/revenue-summary")
    async def dashboard_revenue_summary(user: dict = Depends(get_current_user)):
        """Totals for the three Dashboard cards. One pass over the revenue rows, grouped exactly like
        /revenue/{employees,agents,customers} so the card total equals the details-page total."""
        _require_admin_revenue(user)
        rows = await _build_revenue_rows()
        def total_for(kind):
            keys, amount = set(), 0.0
            for r in rows:
                key = r.get(f"{kind}_id") or r.get(f"{kind}_name")
                if not key:
                    continue
                keys.add(str(key)); amount += r["amount"]
            return {"total_revenue": round(amount, 2), "contributing_count": len(keys)}
        return _ok({"employee": total_for("employee"), "customer": total_for("customer"), "agent": total_for("agent"),
                    "total_revenue": round(sum(r["amount"] for r in rows), 2), "transactions": len(rows)},
                   message="Dashboard revenue summary loaded")

    def _payment_status_label(billed, paid, outstanding):
        if billed <= 0 and paid <= 0:
            return "No Billing"
        if outstanding <= 0:
            return "Paid"
        return "Partial" if paid > 0 else "Pending"

    async def _dashboard_revenue_detail(kind, user, search, status, department, payment_status, sort, order, page, page_size):
        _require_admin_revenue(user)
        rows = await _build_revenue_rows()
        base = (await _revenue_response(kind, user, None, "revenue", "desc", 1, 100000, "all", None, None, rows=rows))["data"]["rows"]

        def row_key(r):
            return str(r.get(f"{kind}_id") or r.get(f"{kind}_name") or "")
        mine = {}
        for r in rows:
            k = row_key(r)
            if k:
                mine.setdefault(k, []).append(r)

        handled, billing = {}, {}
        if kind != "customer":
            name_to_id = {str(b.get("name") or "").strip().lower(): str(b.get("id")) for b in base if b.get("name")}
            customers = await db["erp_customers"].find({}, {"_id": 0, "id": 1, "assigned_employee": 1, "assigned_employee_id": 1, "assigned_agent": 1, "assigned_agent_id": 1}).to_list(20000)
            for c in customers:
                pid = c.get(f"assigned_{kind}_id") or name_to_id.get(str(c.get(f"assigned_{kind}") or "").strip().lower())
                if pid and c.get("id"):
                    handled.setdefault(str(pid), set()).add(str(c["id"]))
            for k, rs in mine.items():
                for r in rs:
                    if r.get("customer_id"):
                        handled.setdefault(k, set()).add(str(r["customer_id"]))
            all_ids = sorted({cid for ids in handled.values() for cid in ids})
            billing = await _billing_for_customers(all_ids)

        out = []
        for b in base:
            rid = str(b.get("id"))
            p = b.get("profile") or {}
            rs = mine.get(rid, [])
            latest = max(rs, key=lambda r: str(r.get("date") or ""), default=None)
            latest_payment = {"date": latest.get("date"), "amount": latest.get("amount"), "invoice_no": latest.get("invoice_no"), "customer_name": latest.get("customer_name")} if latest else None
            if kind == "customer":
                billed, paid, outstanding = float(b.get("total_billed") or 0), float(b.get("paid_amount") or 0), float(b.get("outstanding") or 0)
                out.append({"id": b.get("id"), "name": p.get("owner") or p.get("name") or b.get("name"), "company": p.get("name") or b.get("name"),
                            "email": p.get("email"), "phone": p.get("mobile"), "status": p.get("status"),
                            "services": b.get("services") or [], "total_billed": round(billed, 2), "total_paid": round(paid, 2), "outstanding": round(outstanding, 2),
                            "payments_count": b.get("transactions", 0), "latest_payment": b.get("latest_payment"),
                            "payment_status": _payment_status_label(billed, paid, outstanding), "revenue": b.get("revenue", 0.0)})
                continue
            cids = handled.get(rid, set())
            billed = paid = outstanding = 0.0
            services = []
            for r in rs:
                if r.get("service") and r["service"] not in services:
                    services.append(r["service"])
            for cid in cids:
                st = billing.get(cid) or {}
                billed += float(st.get("total_billed") or 0); paid += float(st.get("total_paid") or 0); outstanding += max(float(st.get("outstanding") or 0), 0)
                for item in st.get("items") or []:
                    if item.get("service") and item["service"] not in services:
                        services.append(item["service"])
            rec = {"id": b.get("id"), "name": p.get("name") or b.get("name"), "email": p.get("email"), "phone": p.get("mobile"), "status": p.get("status"),
                   "customers_count": len(cids), "revenue": b.get("revenue", 0.0), "total_billed": round(billed, 2), "total_paid": round(paid, 2),
                   "outstanding": round(outstanding, 2), "payments_count": b.get("transactions", 0), "latest_payment": latest_payment, "services": services,
                   "bookings_count": len({r.get("booking_id") for r in rs if r.get("booking_id")})}
            if kind == "employee":
                rec["department"] = p.get("department"); rec["role"] = p.get("designation")
            else:
                rec["commission_percentage"] = p.get("commission_percentage")
            out.append(rec)

        term = str(search or "").strip().lower()
        if term:
            out = [x for x in out if any(term in str(x.get(k) or "").lower() for k in ("id", "name", "email", "phone", "company"))]
        if status:
            out = [x for x in out if str(x.get("status") or "").lower() == status.strip().lower()]
        if department and kind == "employee":
            out = [x for x in out if str(x.get("department") or "").lower() == department.strip().lower()]
        if payment_status and kind == "customer":
            out = [x for x in out if str(x.get("payment_status") or "").lower() == payment_status.strip().lower()]
        reverse = str(order).lower() != "asc"
        if sort == "name":
            out.sort(key=lambda x: str(x.get("name") or "").lower(), reverse=reverse)
        else:
            out.sort(key=lambda x: float(x.get("revenue") or 0), reverse=reverse)
        total = len(out)
        offset = (page - 1) * page_size
        totals = {"revenue": round(sum(float(x.get("revenue") or 0) for x in out), 2), "total_billed": round(sum(x["total_billed"] for x in out), 2),
                  "total_paid": round(sum(x["total_paid"] for x in out), 2), "outstanding": round(sum(x["outstanding"] for x in out), 2)}
        filters = {"statuses": sorted({str(x.get("status")) for x in out if x.get("status")})}
        if kind == "employee":
            filters["departments"] = sorted({str(x.get("department")) for x in out if x.get("department")})
        return _ok({"kind": kind, "totals": totals, "filters": filters, "rows": out[offset:offset + page_size]},
                   message=f"{kind.title()} revenue details loaded",
                   pagination={"page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size})

    @router.get("/admin/dashboard/employee-revenue")
    async def dashboard_employee_revenue(search: str = Query(None), status: str = Query(None), department: str = Query(None), sort: str = Query("revenue"), order: str = Query("desc"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500), user: dict = Depends(get_current_user)):
        return await _dashboard_revenue_detail("employee", user, search, status, department, None, sort, order, page, page_size)

    @router.get("/admin/dashboard/customer-revenue")
    async def dashboard_customer_revenue(search: str = Query(None), status: str = Query(None), payment_status: str = Query(None), sort: str = Query("revenue"), order: str = Query("desc"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500), user: dict = Depends(get_current_user)):
        return await _dashboard_revenue_detail("customer", user, search, status, None, payment_status, sort, order, page, page_size)

    @router.get("/admin/dashboard/agent-revenue")
    async def dashboard_agent_revenue(search: str = Query(None), status: str = Query(None), sort: str = Query("revenue"), order: str = Query("desc"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500), user: dict = Depends(get_current_user)):
        return await _dashboard_revenue_detail("agent", user, search, status, None, None, sort, order, page, page_size)

    @router.get("/admin/analytics/summary")
    async def analytics_summary(user: dict = Depends(get_current_user)):
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        invoices = await db["erp_invoices"].find({}, {"_id": 0}).to_list(10000)
        payments = await db["erp_payments"].find({}, {"_id": 0}).to_list(10000)
        bookings = await db["erp_bookings"].find({}, {"_id": 0}).to_list(10000)
        service_counts = {}
        for row in invoices:
            key = row.get("service") or row.get("service_name") or "Other"
            service_counts[key] = service_counts.get(key, 0) + 1
        payment_status = {}
        for row in payments:
            key = row.get("status") or "Unknown"
            payment_status[key] = payment_status.get(key, 0) + 1
        booking_status = {}
        for row in bookings:
            key = row.get("status") or "Unknown"
            booking_status[key] = booking_status.get(key, 0) + 1
        total_invoiced = 0.0
        total_paid = 0.0
        total_balance = 0.0
        for invoice in invoices:
            state = await _invoice_financial_state(invoice)
            total_invoiced += float(invoice.get("total") or 0)
            total_paid += float(state["paid_amount"])
            total_balance += float(state["balance"])
        return _ok({
            "service_counts": service_counts,
            "payment_status_counts": payment_status,
            "booking_status_counts": booking_status,
            "invoice_financials": {"invoiced": round(total_invoiced, 2), "paid": round(total_paid, 2), "balance": round(total_balance, 2)},
            "collection_rate": round((total_paid / total_invoiced) * 100, 2) if total_invoiced else 0,
        }, message="Analytics summary loaded")

    # ---------------- Payments (modular Razorpay: real when keys present, else demo test-mock) ----------------
    def _rzp_client():
        kid = os.environ.get("RAZORPAY_KEY_ID"); ksec = os.environ.get("RAZORPAY_KEY_SECRET")
        if kid and ksec:
            try:
                import razorpay
                return razorpay.Client(auth=(kid, ksec)), kid
            except Exception:
                return None, None
        return None, None

    @router.post("/payments/create-order")
    async def create_order(body: dict = Body(...), user: dict = Depends(get_current_user)):
        amount_rupees = float(body.get("amount") or 0)
        if amount_rupees <= 0:
            raise HTTPException(status_code=400, detail="Invalid amount")
        amount_paise = int(round(amount_rupees * 100))
        client, kid = _rzp_client()
        if client:
            order = client.order.create({"amount": amount_paise, "currency": "INR", "payment_capture": 1})
            return _ok({"order_id": order["id"], "amount": amount_paise, "currency": "INR", "key_id": kid, "mode": "live"})
        order_id = f"order_{uuid.uuid4().hex[:14]}"
        return _ok({"order_id": order_id, "amount": amount_paise, "currency": "INR",
                    "key_id": os.environ.get("RAZORPAY_KEY_ID", "rzp_test_DEMO1234567890"), "mode": "test"})

    @router.post("/payments/verify")
    async def verify_payment(body: dict = Body(...), user: dict = Depends(get_current_user)):
        order_id = body.get("order_id")
        if not order_id:
            raise HTTPException(status_code=422, detail="order_id is required")
        payment_id = body.get("payment_id") or f"pay_{uuid.uuid4().hex[:14]}"
        signature = body.get("signature")
        booking_id = body.get("booking_id")
        invoice_id = body.get("invoice_id")
        fee = float(body.get("amount") or 0)
        gst = float(body.get("gst") or 0)
        agent = body.get("agent", "Vikram Singh")

        # Idempotency: if this order was already verified (customer refreshed the
        # page, double-clicked, or retried after a slow response), return the
        # original result instead of creating a second payment/invoice pair.
        existing_payment = await db["erp_payments"].find_one({"order_id": order_id}, {"_id": 0})
        if existing_payment:
            return _ok({
                "payment_id": existing_payment["payment_id"], "order_id": order_id,
                "reference_no": existing_payment["reference_no"], "invoice_no": existing_payment["invoice_no"],
                "receipt_no": existing_payment.get("receipt_no", ""), "amount": existing_payment["amount"],
                "gst": existing_payment["gst"], "total": existing_payment["total"],
                "status": existing_payment["status"], "date": existing_payment["txn_date"],
            }, message="Payment already verified")

        invoice = None
        if invoice_id:
            invoice = await db["erp_invoices"].find_one(
                {"$or": [{"id": str(invoice_id)}, {"invoice_no": str(invoice_id)}]},
                {"_id": 0},
            )
            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")
            if user.get("role") == "customer":
                customer_profile = await _customer_record_for_user(user)
                canonical_customer_id = customer_profile.get("id") if customer_profile else None
                if invoice.get("customer_id") != canonical_customer_id:
                    raise HTTPException(status_code=403, detail="You can only pay your own invoice")
            if invoice.get("payment_status") == "Cancelled":
                raise HTTPException(status_code=400, detail="Cancelled invoices cannot receive payments")
            state = await _invoice_financial_state(invoice)
            requested_total = round(fee + gst, 2)
            if requested_total <= 0:
                raise HTTPException(status_code=400, detail="Invalid payment amount")
            if requested_total > state["balance"] + 0.009:
                raise HTTPException(status_code=400, detail=f"Payment exceeds invoice balance of ₹{state['balance']:.2f}")
            if invoice.get("booking_id"):
                booking_id = invoice.get("booking_id")

        booking = None
        if booking_id:
            booking = await db["erp_bookings"].find_one({"id": booking_id}, {"_id": 0})
            if not booking:
                raise HTTPException(status_code=404, detail="Booking not found")
            # A customer can only ever pay for their own booking — never trust a
            # booking_id blindly, or another customer's pending booking could be
            # marked paid on their behalf.
            if user.get("role") == "customer":
                customer_profile = await _customer_record_for_user(user)
                canonical_customer_id = customer_profile.get("id") if customer_profile else None
                if booking.get("customer_id") != canonical_customer_id:
                    raise HTTPException(status_code=403, detail="You can only pay for your own booking")

        # The customer name shown on the invoice/payment record comes from the
        # authenticated user or the existing invoice/booking, never an unauthenticated
        # client-supplied relationship key.
        if invoice:
            customer = invoice.get("customer") or user.get("name") or "Customer"
            customer_id = invoice.get("customer_id")
        elif booking:
            customer = booking.get("customer") or user.get("name") or "Customer"
            customer_id = booking.get("customer_id")
        elif user.get("role") == "customer":
            customer_profile = await _customer_record_for_user(user)
            customer = (customer_profile or {}).get("business_name") or user.get("name") or "Customer"
            customer_id = customer_profile.get("id") if customer_profile else None
        else:
            customer = body.get("customer", "Customer")
            customer_id = None

        client, _ = _rzp_client()
        if client and signature:
            try:
                client.utility.verify_payment_signature({
                    "razorpay_order_id": order_id, "razorpay_payment_id": payment_id, "razorpay_signature": signature,
                })
            except Exception:
                raise HTTPException(status_code=400, detail="Payment signature verification failed")

        now = datetime.now(timezone.utc)
        total = round(fee + gst)
        inv_no = f"INV-{3200 + int(now.timestamp()) % 800}"
        rcpt_no = f"RCPT-{uuid.uuid4().hex[:8].upper()}"
        txn_ref = f"TXN{int(now.timestamp())}"
        date_str = now.strftime("%Y-%m-%d")

        service_id = (invoice or booking or {}).get("service_id") if (invoice or booking) else None
        service_name = (invoice or booking or {}).get("service") if (invoice or booking) else body.get("service")
        agent_id = (invoice or booking or {}).get("agent_id") if (invoice or booking) else None
        target_invoice_id = invoice.get("id") if invoice else inv_no
        await db["erp_payments"].insert_one({
            "id": payment_id, "payment_id": payment_id, "order_id": order_id, "reference_no": txn_ref,
            "customer": customer, "customer_id": customer_id, "invoice_id": target_invoice_id,
            "invoice_no": invoice.get("invoice_no") if invoice else inv_no,
            "amount": round(fee), "gst": round(gst), "total": total,
            "mode": "Razorpay", "status": "Completed", "txn_date": date_str, "agent": agent,
            "agent_id": agent_id, "service": service_name, "service_id": service_id,
            "booking_id": booking_id, "receipt_no": rcpt_no,
            "remarks": "Invoice payment via Razorpay (test)" if invoice else "Booking payment via Razorpay (test)",
        })
        if not invoice:
            await db["erp_invoices"].insert_one({
                "id": inv_no, "invoice_no": inv_no, "customer": customer, "customer_id": customer_id,
                "booking_id": booking_id, "service": service_name, "service_id": service_id,
                "amount": round(fee), "gst": round(gst),
                "total": total, "paid_amount": total, "balance": 0, "status": "Paid", "payment_status": "Paid",
                "issue_date": date_str, "due_date": date_str,
            })
        else:
            synced_invoice = await _sync_invoice_financials(invoice["id"])
            await _sync_invoice_dependents(synced_invoice or invoice)
            await _sync_booking_payment_status(synced_invoice or invoice)
        if booking_id:
            await db["erp_bookings"].update_one({"id": booking_id}, {"$set": {"status": "Confirmed", "payment_status": "Paid"}})

        effective_invoice_no = invoice.get("invoice_no") if invoice else inv_no
        await _notify("admin", f"Payment received — {txn_ref}", f"{customer} paid ₹{total:,} for booking {booking_id or ''}. Invoice {effective_invoice_no}.", "Invoice", "information")
        await _notify("agent", f"Booking {booking_id or ''} paid & confirmed", f"{customer} completed payment of ₹{total:,}. You can begin the work.", "Invoice", "information")
        await _notify("customer", f"Payment successful — {inv_no}", f"Your payment of ₹{total:,} is confirmed. Booking {booking_id or ''} is now Confirmed.", "Invoice", "information")

        return _ok({
            "payment_id": payment_id, "order_id": order_id, "reference_no": txn_ref, "invoice_no": effective_invoice_no,
            "receipt_no": rcpt_no, "amount": round(fee), "gst": round(gst), "total": total,
            "status": "Completed", "date": date_str,
        }, message="Payment verified")

    # ---------------- Per-booking chat thread ----------------
    async def _authorize_booking_chat(booking_id: str, user: dict):
        booking = await db["erp_bookings"].find_one({"id": booking_id}, {"_id": 0})
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        role = user.get("role")
        if role == "admin":
            return booking
        if role == "customer":
            customer = await _customer_record_for_user(user)
            if customer and booking.get("customer_id") == customer.get("id"):
                return booking
        if role == "agent":
            agent_id = (user.get("meta") or {}).get("agent_id")
            if agent_id and booking.get("agent_id") == agent_id:
                return booking
        if role == "employee":
            emp = await _employee_record_for_user(user)
            if emp and booking.get("assigned_employee") == emp.get("name"):
                return booking
        raise HTTPException(status_code=404, detail="Booking not found")

    @router.get("/bookings/{booking_id}/messages")
    async def list_booking_messages(booking_id: str, user: dict = Depends(get_current_user)):
        await _authorize_booking_chat(booking_id, user)
        msgs = await db["erp_booking_chat"].find({"booking_id": booking_id}, {"_id": 0}).sort("ts", 1).to_list(500)
        return _ok({"messages": msgs})

    @router.post("/bookings/{booking_id}/messages")
    async def add_booking_message(booking_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        await _authorize_booking_chat(booking_id, user)
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message is required")
        role = user.get("role", "customer")
        name = user.get("name") or {"customer": "Customer", "agent": "Consultant", "admin": "NTAXCO Admin", "employee": "NTAXCO Team"}.get(role, role.title())
        doc = {
            "id": f"MSG-{uuid.uuid4().hex[:8].upper()}", "booking_id": booking_id,
            "sender_role": role, "sender_name": name, "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        await db["erp_booking_chat"].insert_one({**doc})
        target = "agent" if role == "customer" else "customer"
        await _notify(target, f"New message on {booking_id}", f"{name}: {text[:60]}", "Bookings", "information")
        return _ok(doc, message="Message sent")


    @router.get("/employee/dashboard")
    async def employee_dashboard(user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Employee access required")
        emp = await _employee_record_for_user(user)
        if not emp:
            raise HTTPException(status_code=404, detail="Employee profile not found")
        emp_name, emp_id = emp.get("name"), emp.get("id")
        customers = await db["erp_customers"].count_documents(_assigned_customer_query(emp))
        projects = await db["erp_projects"].find({"assigned_employee": emp_name}, {"_id": 0}).to_list(2000)
        tasks = await db["erp_tasks"].find({"employee_id": emp_id}, {"_id": 0}).to_list(2000)
        attendance = await db["erp_attendance"].find({"employee_id": emp_id}, {"_id": 0}).to_list(500)
        leaves = await db["erp_leaves"].find({"employee_id": emp_id}, {"_id": 0}).to_list(500)
        docs = await db["erp_documents"].find({"$or": [{"uploaded_by": emp_name}, {"assigned_employee": emp_name}]}, {"_id": 0}).to_list(2000)
        # Employee Module: "Assigned customer services" / "Employee service
        # workload" (section 5) — grouped from this employee's own assigned
        # customers, resolved against the live service catalog. No separate
        # per-employee service list is maintained; it's derived on read.
        assigned_customers = await db["erp_customers"].find(_assigned_customer_query(emp), {"_id": 0}).to_list(2000)
        service_ids = {c.get("service_id") for c in assigned_customers if c.get("service_id")}
        services_by_id = {}
        if service_ids:
            for s in await db["erp_services"].find({"id": {"$in": list(service_ids)}}, {"_id": 0}).to_list(500):
                services_by_id[s["id"]] = s
        workload = {}
        for c in assigned_customers:
            label = (services_by_id.get(c.get("service_id")) or {}).get("name") or (services_by_id.get(c.get("service_id")) or {}).get("title") or c.get("service_type") or "Unassigned"
            workload[label] = workload.get(label, 0) + 1
        return _ok({
            "employee": emp, "cards": {
                "customers": customers, "projects": len(projects),
                "running_projects": sum(1 for p in projects if p.get("status") == "Running"),
                "completed_tasks": sum(1 for t in tasks if str(t.get("status","")).lower() in ("completed","done")),
                "pending_tasks": sum(1 for t in tasks if str(t.get("status","")).lower() not in ("completed","done")),
                "attendance_days": len(attendance), "pending_leaves": sum(1 for l in leaves if l.get("status") == "Pending"),
                "documents": len(docs),
            },
            "projects": projects, "tasks": tasks, "attendance": attendance[-31:], "leaves": leaves,
            "service_workload": [{"service": k, "customers": v} for k, v in sorted(workload.items(), key=lambda kv: -kv[1])],
        })

    @router.get("/agent/dashboard")
    async def agent_dashboard(user: dict = Depends(get_current_user)):
        if user.get("role") != "agent":
            raise HTTPException(status_code=403, detail="Agent access required")
        agent_id = (user.get("meta") or {}).get("agent_id")
        agent_doc = await db["erp_agents"].find_one({"id": agent_id}, {"_id": 0}) if agent_id else None
        agent_name = (agent_doc or {}).get("name") or user.get("name")
        leads = await db["erp_leads"].find({"$or": [{"assigned_agent": agent_name}, {"agent": agent_name}, {"agent_name": agent_name}]}, {"_id": 0}).to_list(2000)
        customers = await db["erp_customers"].count_documents({"assigned_agent": agent_name})
        bookings = await db["erp_bookings"].find(
            {"$or": [{"agent_id": agent_id}, {"assigned_agent": agent_name}]},
            {"_id": 0},
        ).to_list(2000)
        projects = await db["erp_projects"].find({"assigned_agent": agent_name}, {"_id": 0}).to_list(2000)
        commissions = await db["erp_commissions"].find({"agent": agent_name}, {"_id": 0}).to_list(2000)
        appointments = await db["erp_appointments"].find({"$or": [{"assigned_agent": agent_name}, {"agent": agent_name}]}, {"_id": 0}).to_list(2000)
        notifications = await db["erp_notifications"].count_documents({"$or": [{"role": "agent", "user_id": user.get("id")}, {"role": "agent", "user_id": {"$exists": False}}, {"role": "all"}]})
        monthly = {}
        for row in commissions:
            period = row.get("period") or str(row.get("created_at",""))[:7] or "Unknown"
            monthly[period] = monthly.get(period, 0) + float(row.get("amount") or 0)
        # Agent Module: "Agent service workload" (section 5) — grouped from
        # this agent's own bookings, resolved against the live service
        # catalog. Customer-specific Agent Percentage is untouched by this;
        # it's read straight off each customer record, never averaged/globalized.
        workload = {}
        for b in bookings:
            label = b.get("service") or "Unassigned"
            workload[label] = workload.get(label, 0) + 1
        return _ok({
            "cards": {
                "leads": len(leads), "new_leads": sum(1 for x in leads if str(x.get("status","")).lower() in ("new","open")),
                "qualified_leads": sum(1 for x in leads if str(x.get("status","")).lower() == "qualified"),
                "converted_customers": sum(1 for x in leads if str(x.get("status","")).lower() in ("converted","won")),
                "followups": sum(1 for x in leads if str(x.get("status","")).lower() in ("follow-up","followup")),
                "appointments": sum(1 for x in appointments if str(x.get("status","")).lower() not in ("completed","cancelled")),
                "customers": customers, "projects": len(projects), "bookings": len(bookings),
                "commission": sum(float(x.get("amount") or 0) for x in commissions), "notifications": notifications,
            },
            "leads": leads, "bookings": bookings, "projects": projects, "commissions": commissions,
            "appointments": appointments, "commission_trend": [{"period": k, "amount": v} for k,v in sorted(monthly.items())],
            "service_workload": [{"service": k, "bookings": v} for k, v in sorted(workload.items(), key=lambda kv: -kv[1])],
        })

    @router.post("/calculators/gst")
    async def gst_calculator(body: dict = Body(...), user: dict = Depends(get_current_user)):
        if user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Customer access required")
        try:
            amount = float(body.get("amount", 0)); rate = float(body.get("rate", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Amount and GST rate must be numbers")
        if amount < 0 or rate < 0 or rate > 100:
            raise HTTPException(status_code=422, detail="Enter a valid amount and GST rate")
        mode = str(body.get("mode", "exclusive")).lower()
        interstate = bool(body.get("interstate", False))
        if mode == "inclusive":
            base = round(amount / (1 + rate / 100), 2) if rate else round(amount, 2)
            gst = round(amount - base, 2)
            total = round(amount, 2)
        else:
            base = round(amount, 2); gst = round(base * rate / 100, 2); total = round(base + gst, 2)
        return _ok({"base_amount": base, "gst_amount": gst,
                    "cgst": 0 if interstate else round(gst/2,2),
                    "sgst": 0 if interstate else round(gst/2,2),
                    "igst": gst if interstate else 0, "final_amount": total,
                    "mode": mode, "rate": rate}, message="GST estimate calculated")

    @router.post("/calculators/income-tax")
    async def income_tax_calculator(body: dict = Body(...), user: dict = Depends(get_current_user)):
        if user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Customer access required")
        try:
            annual = max(0.0, float(body.get("annual_income", 0) or 0))
            deductions = max(0.0, float(body.get("deductions", 0) or 0))
            other = max(0.0, float(body.get("other_income", 0) or 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Income and deductions must be numbers")
        regime = str(body.get("regime", "new")).lower()
        taxable = max(0.0, annual + other - deductions)
        if regime == "old":
            slabs = [(250000,0),(500000,.05),(1000000,.20),(float("inf"),.30)]
            tax = 0.0; prev=0.0
            for upper, rate in slabs:
                taxable_slice=max(0.0,min(taxable,upper)-prev); tax += taxable_slice*rate
                if taxable <= upper: break
                prev=upper
            if taxable <= 500000: tax=max(0.0,tax-12500)
        else:
            slabs = [(400000,0),(800000,.05),(1200000,.10),(1600000,.15),(2000000,.20),(2400000,.25),(float("inf"),.30)]
            tax=0.0; prev=0.0
            for upper, rate in slabs:
                taxable_slice=max(0.0,min(taxable,upper)-prev); tax += taxable_slice*rate
                if taxable <= upper: break
                prev=upper
            if taxable <= 1200000: tax=max(0.0,tax-60000)
        cess = round(tax * .04, 2)
        estimated = round(tax + cess, 2)
        return _ok({"gross_income": round(annual+other,2), "deductions": round(deductions,2),
                    "taxable_income": round(taxable,2), "income_tax": round(tax,2),
                    "cess": cess, "estimated_tax": estimated, "regime": regime,
                    "assessment_year": "2026-27"}, message="Income tax estimate calculated")

    @router.get("/agent/tasks")
    async def list_agent_tasks(user: dict = Depends(get_current_user)):
        if user.get("role") != "agent":
            raise HTTPException(status_code=403, detail="Agent access required")
        agent_id = (user.get("meta") or {}).get("agent_id")
        agent_name = await _agent_name(user)
        if not agent_id and not agent_name:
            raise HTTPException(status_code=404, detail="Agent profile not found")
        query = {"task_board": True, "$or": ([{"agent_id": agent_id}] if agent_id else []) + ([{"assigned_agent": agent_name}, {"agent": agent_name}, {"agent_name": agent_name}] if agent_name else [])}
        tasks = await db["erp_tasks"].find(query, {"_id": 0}).sort([("due_date", 1), ("created_at", -1)]).to_list(5000)
        return _ok([await _task_enrich(t) for t in tasks])

    @router.get("/agent/tasks/{task_id}")
    async def get_agent_task(task_id: str, user: dict = Depends(get_current_user)):
        if user.get("role") != "agent":
            raise HTTPException(status_code=403, detail="Agent access required")
        agent_id = (user.get("meta") or {}).get("agent_id")
        agent_name = await _agent_name(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        allowed = task and ((agent_id and task.get("agent_id") == agent_id) or (agent_name and task.get("assigned_agent") == agent_name) or (agent_name and task.get("agent") == agent_name))
        if not allowed:
            raise HTTPException(status_code=404, detail="Task not found")
        return _ok(await _task_enrich(task))


    # ---------------- Reusable MongoDB-backed Kanban lists ----------------
    KANBAN_BOARDS = {
        "tasks": ("erp_tasks", "task_board"),
        "revenue": ("erp_payments", None),
        "tds": ("erp_tds", None),
        "gst": ("erp_gst", None),
        "itr": ("erp_itr", None),
        "roc": ("erp_roc", None),
    }
    KANBAN_STANDARD = ("TO DO", "IN PROGRESS", "REVIEW", "COMPLETED")
    KANBAN_MODULE_STATUS = {
        "TO DO": "Pending",
        "IN PROGRESS": "Running",
        "REVIEW": "Running",
        "COMPLETED": "Completed",
    }

    async def _kanban_board(board: str):
        key = str(board or "").strip().lower()
        if key not in KANBAN_BOARDS:
            raise HTTPException(status_code=404, detail="Kanban board not found")
        return key, KANBAN_BOARDS[key]

    async def _kanban_lists(board: str):
        key, _ = await _kanban_board(board)
        return await db["erp_kanban_lists"].find({"board": key}, {"_id": 0}).sort([("position", 1), ("created_at", 1)]).to_list(500)

    @router.get("/admin/kanban/{board}/lists")
    async def list_kanban_lists(board: str, user: dict = Depends(get_current_user)):
        _admin_only(user)
        return _ok(await _kanban_lists(board))

    @router.post("/admin/kanban/{board}/lists")
    async def create_kanban_list(board: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        key, _ = await _kanban_board(board)
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="List name is required")
        if len(name) > 80:
            raise HTTPException(status_code=422, detail="List name must be 80 characters or less")
        existing = await db["erp_kanban_lists"].find_one({"board": key, "name_ci": name.lower()}, {"_id": 0})
        if existing:
            raise HTTPException(status_code=409, detail="A list with this name already exists")
        count = await db["erp_kanban_lists"].count_documents({"board": key})
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": f"KBL-{uuid.uuid4().hex[:10].upper()}",
            "board": key, "name": name, "name_ci": name.lower(),
            "position": count, "created_at": now, "updated_at": now,
            "created_by": user.get("id"), "created_by_name": user.get("name") or user.get("email") or "Admin",
        }
        await db["erp_kanban_lists"].insert_one(doc)
        doc.pop("_id", None)
        return _ok(doc, message="Kanban list created")

    @router.put("/admin/kanban/{board}/lists/{list_id}")
    async def rename_kanban_list(board: str, list_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        key, _ = await _kanban_board(board)
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="List name is required")
        existing = await db["erp_kanban_lists"].find_one({"id": list_id, "board": key}, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Kanban list not found")
        duplicate = await db["erp_kanban_lists"].find_one({"board": key, "name_ci": name.lower(), "id": {"$ne": list_id}}, {"_id": 0})
        if duplicate:
            raise HTTPException(status_code=409, detail="A list with this name already exists")
        await db["erp_kanban_lists"].update_one({"id": list_id, "board": key}, {"$set": {"name": name, "name_ci": name.lower(), "updated_at": datetime.now(timezone.utc).isoformat()}})
        return _ok(await db["erp_kanban_lists"].find_one({"id": list_id, "board": key}, {"_id": 0}), message="Kanban list renamed")

    @router.delete("/admin/kanban/{board}/lists/{list_id}")
    async def delete_kanban_list(board: str, list_id: str, user: dict = Depends(get_current_user)):
        _admin_only(user)
        key, (coll, task_flag) = await _kanban_board(board)
        existing = await db["erp_kanban_lists"].find_one({"id": list_id, "board": key}, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Kanban list not found")
        # Deleting a custom list never deletes records. Records are safely returned to their
        # standard/status-derived column.
        await db[coll].update_many({"kanban_list_id": list_id}, {"$set": {"kanban_list_id": None}, "$unset": {"kanban_list_name": ""}})
        await db["erp_kanban_lists"].delete_one({"id": list_id, "board": key})
        return _ok({"id": list_id}, message="Kanban list deleted")

    def _revenue_default_column(payment_status):
        st = str(payment_status or "").strip().lower()
        if st in {"completed", "paid"}:
            return "COMPLETED"
        if st == "partial":
            return "IN PROGRESS"
        return "TO DO"

    @router.get("/admin/kanban/revenue/cards")
    async def revenue_kanban_cards(user: dict = Depends(get_current_user)):
        """Real revenue records (erp_payments) enriched from invoices/bookings/customers for the Revenue Kanban.

        Kanban placement (kanban_list_id / kanban_status) is stored beside, never instead of, the payment
        status. Outstanding comes from the same billing calculation used by the Customer module.
        """
        _admin_only(user)
        ctx = await _revenue_context()
        rows = await _build_revenue_rows(ctx=ctx)
        payments = await db["erp_payments"].find({}, {"_id": 0, "file_data": 0}).to_list(30000)
        by_id = {str(p.get("id")): p for p in payments if p.get("id")}
        customer_ids = list({str(r.get("customer_id")) for r in rows if r.get("customer_id")})
        billing = await _billing_for_customers(customer_ids)
        paid_by_invoice = {}
        for r in rows:
            ref = r.get("invoice_id") or r.get("invoice_no")     # one canonical key per row: never counted twice
            if ref:
                paid_by_invoice[str(ref)] = paid_by_invoice.get(str(ref), 0.0) + r["amount"]
        cards = []
        for r in rows:
            payment = by_id.get(str(r.get("id")))
            if not payment:        # legacy paid invoice without a payment record: reported in Revenue, not movable
                continue
            invoice = ctx["invoice_by_ref"].get(str(r.get("invoice_id") or r.get("invoice_no") or ""))
            invoice_total = _revenue_amount((invoice or {}).get("total")) if invoice else 0.0
            invoice_paid = paid_by_invoice.get(str(r.get("invoice_id") or r.get("invoice_no") or ""), 0.0)
            state = billing.get(str(r.get("customer_id") or "")) or {}
            pay_status = r.get("status") or "Paid"
            column = payment.get("kanban_status") if payment.get("kanban_status") in KANBAN_STANDARD else _revenue_default_column(pay_status)
            cards.append({
                "id": r["id"], "payment_id": r.get("payment_id"),
                "customer_id": r.get("customer_id"), "customer": r.get("customer_name"), "customer_name": r.get("customer_name"),
                "service": r.get("service"), "service_id": r.get("service_id"),
                "invoice_id": r.get("invoice_id"), "invoice_no": r.get("invoice_no"), "booking_id": r.get("booking_id"),
                "amount": r["amount"], "payment_date": r.get("date"), "payment_method": r.get("payment_method"),
                "reference_no": payment.get("reference_no"),
                "status": pay_status, "payment_status": pay_status,
                "employee_id": r.get("employee_id"), "employee_name": r.get("employee_name"),
                "agent_id": r.get("agent_id"), "agent_name": r.get("agent_name"),
                "invoice_total": invoice_total if invoice else None,
                "invoice_outstanding": round(max(invoice_total - invoice_paid, 0.0), 2) if invoice else None,
                "outstanding": round(max(float(state.get("outstanding") or 0), 0.0), 2),
                "created_at": payment.get("created_at"), "updated_at": payment.get("updated_at"),
                "kanban_list_id": payment.get("kanban_list_id"), "kanban_list_name": payment.get("kanban_list_name"),
                "kanban_status": payment.get("kanban_list_name") or column, "kanban_column": column,
                "kanban_updated_at": payment.get("kanban_updated_at"),
            })
        cards.sort(key=lambda c: (str(c.get("payment_date") or ""), str(c.get("id"))), reverse=True)
        return _ok(cards, message="Revenue Kanban cards loaded")

    @router.post("/admin/kanban/{board}/move")
    async def move_kanban_record(board: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        key, (coll, task_flag) = await _kanban_board(board)
        record_id = str(body.get("record_id") or "").strip()
        if not record_id:
            raise HTTPException(status_code=422, detail="record_id is required")
        record = await db[coll].find_one({"id": record_id}, {"_id": 0})
        if not record or (task_flag and record.get(task_flag) is not True):
            raise HTTPException(status_code=404, detail="Kanban record not found")
        if key == "revenue" and not _is_revenue_payment(record):
            raise HTTPException(status_code=404, detail="Kanban record not found")
        list_id = str(body.get("list_id") or "").strip() or None
        target = str(body.get("target") or "").strip().upper()
        now_iso = datetime.now(timezone.utc).isoformat()
        # Revenue cards keep the payment's own updated_at: a Kanban move is workflow state, not a financial edit.
        update = {("kanban_updated_at" if key == "revenue" else "updated_at"): now_iso, "kanban_list_id": list_id}
        if list_id:
            custom = await db["erp_kanban_lists"].find_one({"id": list_id, "board": key}, {"_id": 0})
            if not custom:
                raise HTTPException(status_code=404, detail="Kanban list not found")
            update["kanban_list_name"] = custom["name"]
        else:
            update["kanban_list_name"] = None
            if target not in KANBAN_STANDARD:
                raise HTTPException(status_code=422, detail="Invalid Kanban target")
            if key == "tasks":
                _validate_task_status_transition(record.get("status"), target)
                update["status"] = target
                update["completed_at"] = datetime.now(timezone.utc).isoformat() if target == "COMPLETED" else None
            elif key == "revenue":
                # Revenue workflow is independent from the authoritative payment status.
                update["kanban_status"] = target
                update["kanban_completed_at"] = now_iso if target == "COMPLETED" else None
            else:
                update["kanban_status"] = target
                update["status"] = KANBAN_MODULE_STATUS[target]
                if target == "COMPLETED":
                    update["completed_at"] = datetime.now(timezone.utc).isoformat()
        await db[coll].update_one({"id": record_id}, {"$set": update})
        fresh = await db[coll].find_one({"id": record_id}, {"_id": 0})
        if key == "tasks":
            fresh = await _task_enrich(fresh)
            if target and target != record.get("status"):
                await _task_history(record_id, "Status changed", user, {
                    "from": record.get("status"), "to": target, "source": "kanban"
                })
                await _task_notify(
                    fresh, "admin-status", f"Task status updated: {fresh.get('title')}",
                    f"Admin moved the task to {target}.",
                    employee=True, event_suffix=f"kanban:{fresh.get('updated_at')}"
                )
                if target == "REVIEW":
                    await _task_notify(fresh, "review", f"Task moved to Review: {fresh.get('title')}",
                                        f"Task '{fresh.get('title')}' is ready for review.", event_suffix=target)
                elif target == "COMPLETED":
                    await _task_notify(fresh, "completed", f"Task completed: {fresh.get('title')}",
                                        f"Task '{fresh.get('title')}' has been completed.", event_suffix=target)
        if key == "revenue":
            fresh = {k: v for k, v in fresh.items() if k != "file_data"}
        return _ok(fresh, message="Kanban position updated")

    # ---------------- Admin Task Board (Trello-style, MongoDB-backed) ----------------
    TASK_STATUSES = ("TO DO", "IN PROGRESS", "REVIEW", "COMPLETED")
    TASK_PRIORITIES = ("Low", "Medium", "High", "Urgent")

    def _admin_only(user):
        if user.get("role") not in ("admin", "super_admin", "superadmin"):
            raise HTTPException(status_code=403, detail="Admin access required")

    async def _task_employee(employee_id):
        if not employee_id:
            return None
        return await db["erp_employees"].find_one({"id": str(employee_id)}, {"_id": 0})

    async def _task_customer(customer_id):
        if not customer_id:
            return None
        return await db["erp_customers"].find_one({"id": str(customer_id)}, {"_id": 0})

    async def _task_service(service_id):
        if not service_id:
            return None
        return await db["erp_services"].find_one({"id": str(service_id)}, {"_id": 0})

    async def _task_booking(booking_id):
        if not booking_id:
            return None
        return await db["erp_bookings"].find_one({"id": str(booking_id)}, {"_id": 0})

    def _task_customer_name(c):
        return (c or {}).get("business_name") or (c or {}).get("owner") or (c or {}).get("name") or (c or {}).get("id")

    def _task_service_name(s):
        return (s or {}).get("name") or (s or {}).get("title") or (s or {}).get("category") or (s or {}).get("id")

    def _task_employee_name(e):
        return (e or {}).get("name") or (e or {}).get("emp_id") or (e or {}).get("id")

    def _task_booking_no(b):
        return (b or {}).get("booking_no") or (b or {}).get("id")

    async def _task_enrich(task):
        if not task:
            return None
        task = dict(task)
        task.pop("_id", None)
        # Resolve names live from canonical records. IDs remain the only persisted relationships.
        c = await _task_customer(task.get("customer_id"))
        e = await _task_employee(task.get("employee_id"))
        b = await _task_booking(task.get("booking_id"))
        # The booking is the source of truth for which service the customer bought; the task's own
        # service_id is only used when there is no (resolvable) booking.
        s = (await _task_service(b.get("service_id")) if b and b.get("service_id") else None) or await _task_service(task.get("service_id"))
        task["customer"] = _task_customer_name(c) if c else None
        # Service shown on the Task Board is always the service actually booked: the live service record,
        # else the snapshot stored on the task, else the linked booking's own service.
        task["service"] = _good_service_name(
            _task_service_name(s) if s else None, (b or {}).get("service"), task.get("service_name"),
        )
        if s:
            task["service_id"] = s.get("id")
            task["service_bucket"] = service_category_bucket(s)
        task["assigned_employee"] = _task_employee_name(e) if e else None
        task["booking_no"] = _task_booking_no(b) if b else None
        if b:
            task["booking_status"] = b.get("status")
            task["payment_status"] = b.get("payment_status")
            task["booking_details"] = {
                k: b.get(k) for k in ("id", "booking_no", "customer_id", "service_id", "status",
                                      "payment_status", "booking_date", "due_date", "priority", "notes", "created_at")
            }
        # Customer documents are independent of the booking. Include all documents
        # for the linked customer and booking documents when a booking exists.
        doc_query = {"customer_id": task.get("customer_id")} if task.get("customer_id") else None
        docs = await db["erp_documents"].find(
            doc_query or {"id": "__no_customer__"},
            {"_id": 0, "file_data": 0}
        ).sort("uploaded_date", -1).to_list(500)
        task["documents"] = [
            {k: d.get(k) for k in ("id", "customer_id", "booking_id", "service_id", "file_name",
                                    "file_type", "mime_type", "file_size", "uploaded_by",
                                    "uploaded_by_role", "uploaded_date", "status", "assigned_employee",
                                    "assigned_agent", "agent_id")}
            for d in docs
        ]
        if c:
            try:
                _financial = (await _billing_for_customers([c.get("id")])).get(str(c.get("id")), {})
            except Exception:
                _financial = {}
            task["customer_details"] = {
                **{k: c.get(k) for k in ("id", "name", "owner", "business_name", "gst_number", "gstin",
                                         "pan", "mobile", "phone", "email", "address", "state", "city", "pincode")},
                "invoiced_amount": _financial.get("total_billed", c.get("invoiced_amount", 0)),
                "paid_amount": _financial.get("total_paid", c.get("paid_amount", 0)),
                "outstanding": _financial.get("outstanding", c.get("outstanding", 0)),
            }
        else:
            task["customer_details"] = None
        task["attachments"] = [
            {k: a.get(k) for k in ("id", "file_name", "mime_type", "file_type", "file_size",
                                    "uploaded_by", "uploaded_at")}
            for a in (task.get("attachments") or [])
        ]
        activity = await db["erp_task_activity"].find(
            {"task_id": task.get("id")},
            {"_id": 0}
        ).sort("created_at", 1).to_list(500)
        task["activity"] = activity
        return task

    async def _task_history(task_id, action, user, details=None):
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "id": f"ACT-{uuid.uuid4().hex[:10].upper()}",
            "task_id": task_id,
            "action": action,
            "details": details or {},
            "user_id": (user or {}).get("id"),
            "user_name": (user or {}).get("name") or (user or {}).get("email") or "Admin",
            "created_at": now,
        }
        await db["erp_task_activity"].insert_one(entry)
        return {k: v for k, v in entry.items() if k != "_id"}

    async def _employee_user_id(employee_id):
        if not employee_id:
            return None
        account = await db.users.find_one(
            {"role": "employee", "meta.employee_id": str(employee_id)},
            {"_id": 0, "id": 1},
        )
        return (account or {}).get("id")

    async def _task_notify(task, event, title, description, *, employee=False, event_suffix=None):
        try:
            event_key = f"task:{task.get('id')}:{event}:{event_suffix or task.get('updated_at') or task.get('created_at')}"
            links = {
                "task_id": task.get("id"),
                "customer_id": task.get("customer_id"),
                "booking_id": task.get("booking_id"),
                "service_id": task.get("service_id"),
            }
            if employee and task.get("employee_id"):
                uid = await _employee_user_id(task.get("employee_id"))
                if uid:
                    await _notify("employee", title, description, "Tasks", "information", user_id=uid,
                                  kind="TASK", event_key=event_key, **links)
                    return
            await _notify("admin", title, description, "Tasks", "information",
                          kind="TASK", event_key=event_key, **links)
        except Exception:
            logger.exception("Task notification failed for %s", task.get("id"))

    async def _validate_task_links(body):
        customer_id = str(body.get("customer_id") or "").strip() or None
        booking_id = str(body.get("booking_id") or "").strip() or None
        service_id = str(body.get("service_id") or "").strip() or None
        employee_id = str(body.get("employee_id") or "").strip() or None

        customer = await _task_customer(customer_id)
        booking = await _task_booking(booking_id)
        service = await _task_service(service_id)
        employee = await _task_employee(employee_id)

        if customer_id and not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if booking_id and not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if service_id and not service:
            raise HTTPException(status_code=404, detail="Service not found")
        if employee_id and not employee:
            raise HTTPException(status_code=404, detail="Employee not found")

        # Booking is the canonical source for its customer/service relationship.
        if booking:
            booking_customer_id = booking.get("customer_id")
            booking_service_id = booking.get("service_id")
            if booking_customer_id:
                if customer_id and customer_id != booking_customer_id:
                    raise HTTPException(status_code=400, detail="Selected customer does not belong to the selected booking")
                customer_id = booking_customer_id
                customer = await _task_customer(customer_id)
            if booking_service_id:
                if service_id and service_id != booking_service_id:
                    raise HTTPException(status_code=400, detail="Selected service does not belong to the selected booking")
                service_id = booking_service_id
                service = await _task_service(service_id)

        return customer_id, booking_id, service_id, employee_id, customer, booking, service, employee

    def _task_due_state(due_date):
        if not due_date:
            return "none"
        try:
            due = datetime.fromisoformat(str(due_date).replace("Z", "+00:00")).date()
        except Exception:
            try:
                due = datetime.strptime(str(due_date)[:10], "%Y-%m-%d").date()
            except Exception:
                return "none"
        today = datetime.now(timezone.utc).date()
        if due < today:
            return "overdue"
        if due == today:
            return "today"
        if (due - today).days == 1:
            return "tomorrow"
        return "upcoming"

    def _task_status_rank(status):
        """Return the canonical forward-only workflow position for a Task Board task."""
        try:
            return TASK_STATUSES.index(str(status or "").strip().upper())
        except ValueError:
            return -1

    def _validate_task_status_transition(current_status, requested_status):
        current = str(current_status or "TO DO").strip().upper()
        requested = str(requested_status or "").strip().upper()
        if requested not in TASK_STATUSES:
            raise HTTPException(status_code=422, detail="Invalid task status")
        if current not in TASK_STATUSES:
            # Legacy/invalid records can only be normalized to the first valid
            # workflow state; never use this path to bypass the normal rule.
            if requested != "TO DO":
                raise HTTPException(status_code=422, detail="Task has an invalid current status and cannot be moved forward.")
            return
        current_rank = _task_status_rank(current)
        requested_rank = _task_status_rank(requested)
        if requested_rank < current_rank:
            raise HTTPException(
                status_code=422,
                detail=f"Task status can only move forward: {current} → {requested} is not allowed.",
            )

    async def _auto_create_booking_task(booking, user):
        """Create the Task Board task for an accepted booking. Idempotent: at most one task per booking."""
        booking_id = str(booking.get("id") or "")
        if not booking_id:
            return None
        existing_task = await db["erp_tasks"].find_one({"$or": [{"auto_booking_id": booking_id}, {"booking_id": booking_id, "task_board": True}]}, {"_id": 0})
        if existing_task:
            return existing_task
        customer = await _task_customer(booking.get("customer_id"))
        service = await _task_service(booking.get("service_id"))
        # Assignment priority: booking's employee -> otherwise leave Unassigned (never a random employee).
        employee = await _task_employee(booking.get("employee_id")) if booking.get("employee_id") else None
        if not employee and booking.get("assigned_employee"):
            name = str(booking["assigned_employee"]).strip()
            matches = await db["erp_employees"].find({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}, {"_id": 0}).to_list(3)
            employee = matches[0] if len(matches) == 1 else None
        # Real service only: the booked service's name, never an app name or a generic "Service" placeholder.
        service_name = _good_service_name(_task_service_name(service) if service else None, booking.get("service")) or f"Booking {booking.get('booking_no') or booking_id}"
        customer_name = _task_customer_name(customer) if customer else (booking.get("customer") or "")
        priority = str(booking.get("priority") or "Medium").strip().title()
        if priority not in TASK_PRIORITIES:
            priority = "Medium"
        now = datetime.now(timezone.utc).isoformat()
        task_id = f"TSK-{uuid.uuid4().hex[:8].upper()}"
        title = f"{service_name} – {customer_name}" if customer_name else str(service_name)
        doc = {
            "id": task_id, "title": title,
            "description": f"Auto-created from accepted booking {booking.get('booking_no') or booking_id}. Service: {service_name}. Customer: {customer_name}." + (f" Notes: {booking.get('notes')}" if booking.get("notes") else ""),
            "priority": priority, "status": "TO DO",
            "employee_id": (employee or {}).get("id"),
            "customer_id": booking.get("customer_id"), "booking_id": booking_id, "service_id": booking.get("service_id"),
            "service_name": service_name,
            "agent_id": booking.get("agent_id"), "agent": booking.get("assigned_agent") or booking.get("agent"),
            "due_date": str(booking.get("due_date") or "").strip() or None,
            "booking_status": booking.get("status"), "payment_status": booking.get("payment_status"),
            "task_board": True, "auto_created": True, "auto_booking_id": booking_id,
            "created_by": (user or {}).get("id"), "created_by_name": (user or {}).get("name") or (user or {}).get("email") or "Admin",
            "created_at": now, "updated_at": now,
        }
        try:
            await db["erp_tasks"].insert_one({**doc})
        except Exception as exc:
            if "E11000" in str(exc) or "duplicate" in str(exc).lower():
                return await db["erp_tasks"].find_one({"auto_booking_id": booking_id}, {"_id": 0})
            raise
        await _task_history(task_id, "Task auto-created from accepted booking", user, {"booking_id": booking_id, "status": "TO DO"})
        if doc["employee_id"]:
            await _task_history(task_id, "Assigned to employee", user, {"employee_id": doc["employee_id"], "employee_name": _task_employee_name(employee)})
            await _task_notify(doc, "auto-assigned", f"Task assigned: {title}", f"You have been assigned '{title}' (booking {booking.get('booking_no') or booking_id}).", employee=True, event_suffix="auto")
        else:
            await _task_notify(doc, "auto-unassigned", f"Unassigned task: {title}", f"Booking {booking.get('booking_no') or booking_id} was accepted. Task '{title}' needs an assignee.", event_suffix="auto")
        return doc

    async def _notify_overdue_tasks(tasks):
        for t in tasks:
            if t.get("status") == "COMPLETED" or _task_due_state(t.get("due_date")) != "overdue":
                continue
            try:
                await _notify("admin", f"Task overdue: {t.get('title')}",
                              f"{t.get('title')} is overdue.",
                              "Tasks", "warning", priority="High", kind="TASK_OVERDUE",
                              event_key=f"task-overdue:{t.get('id')}:{str(t.get('due_date'))}",
                              task_id=t.get("id"), customer_id=t.get("customer_id"),
                              booking_id=t.get("booking_id"), service_id=t.get("service_id"))
            except Exception:
                logger.exception("Unable to create overdue task notification")

    @router.get("/admin/tasks/options")
    async def task_options(user: dict = Depends(get_current_user)):
        _admin_only(user)
        customers = await db["erp_customers"].find({}, {"_id": 0, "id": 1, "business_name": 1, "owner": 1, "name": 1}).sort("business_name", 1).to_list(10000)
        services = await db["erp_services"].find({}, {"_id": 0, "id": 1, "name": 1, "title": 1, "category": 1}).sort("name", 1).to_list(10000)
        employees = await db["erp_employees"].find({}, {"_id": 0, "id": 1, "emp_id": 1, "name": 1, "status": 1}).sort("name", 1).to_list(10000)
        bookings = await db["erp_bookings"].find({}, {"_id": 0, "id": 1, "booking_no": 1, "customer_id": 1, "service_id": 1, "customer": 1, "service": 1, "booking_date": 1}).sort("created_at", -1).to_list(10000)
        return _ok({"customers": customers, "services": services, "employees": employees, "bookings": bookings})

    @router.get("/admin/tasks/summary")
    async def task_summary(user: dict = Depends(get_current_user)):
        _admin_only(user)
        # The summary only needs these scalar fields; avoid transferring full task
        # descriptions, comments, checklists and attachment metadata for every task.
        tasks = await db["erp_tasks"].find(
            {"task_board": True},
            {"_id": 0, "status": 1, "due_date": 1, "completed_at": 1,
             "updated_at": 1, "created_at": 1, "employee_id": 1},
        ).to_list(10000)
        await _notify_overdue_tasks(tasks)
        counts = {s: 0 for s in TASK_STATUSES}
        overdue = today_due = today_completed = this_month = this_year = 0
        now_ist = _ist_now()
        today = now_ist.strftime("%Y-%m-%d")
        month = now_ist.strftime("%Y-%m")
        year = now_ist.strftime("%Y")
        for t in tasks:
            s = str(t.get("status") or "TO DO").upper()
            if s in counts:
                counts[s] += 1
            due = str(t.get("due_date") or "")[:10]
            if due == today and s != "COMPLETED":
                today_due += 1
            if s == "COMPLETED" and str(t.get("completed_at") or t.get("updated_at") or "")[:10] == today:
                today_completed += 1
            date_basis = due or str(t.get("created_at") or "")[:10]
            if date_basis.startswith(month):
                this_month += 1
            if date_basis.startswith(year):
                this_year += 1
            if _task_due_state(t.get("due_date")) == "overdue" and s != "COMPLETED":
                overdue += 1
        unassigned = await db["erp_tasks"].count_documents({"task_board": True, "$or": [{"employee_id": None}, {"employee_id": ""}, {"employee_id": {"$exists": False}}]})
        pending = len(tasks) - counts["COMPLETED"]
        return _ok({"total": len(tasks), "todo": counts["TO DO"], "in_progress": counts["IN PROGRESS"],
                    "review": counts["REVIEW"], "completed": counts["COMPLETED"], "overdue": overdue,
                    "unassigned": unassigned, "today_due": today_due, "today_completed": today_completed,
                    "this_month": this_month, "this_year": this_year, "pending": pending})

    @router.get("/admin/tasks/analytics")
    async def task_analytics(user: dict = Depends(get_current_user)):
        _admin_only(user)
        tasks = await db["erp_tasks"].find({"task_board": True}, {"_id": 0}).to_list(50000)
        by_status = {s: 0 for s in TASK_STATUSES}
        by_priority = {p: 0 for p in TASK_PRIORITIES}
        by_service, workload, created, completed = {}, {}, {}, {}
        completion_hours = []
        today = _ist_today_str()
        week_start = (datetime.fromisoformat(today) - timedelta(days=datetime.fromisoformat(today).weekday())).date().isoformat()
        month_start = today[:8] + "01"
        overdue = due_today = completed_today = completed_week = completed_month = 0
        service_names = {s["id"]: _task_service_name(s) async for s in db["erp_services"].find({}, {"_id": 0})}
        emp_names = {e["id"]: _task_employee_name(e) async for e in db["erp_employees"].find({}, {"_id": 0})}
        for t in tasks:
            st = str(t.get("status") or "TO DO").upper()
            pr = str(t.get("priority") or "Medium").title()
            if st in by_status: by_status[st] += 1
            if pr in by_priority: by_priority[pr] += 1
            if st != "COMPLETED" and _task_due_state(t.get("due_date")) == "overdue": overdue += 1
            if st != "COMPLETED" and str(t.get("due_date") or "")[:10] == today: due_today += 1
            if st == "COMPLETED":
                done = str(t.get("completed_at") or t.get("updated_at") or "")[:10]
                if done == today: completed_today += 1
                if done >= week_start: completed_week += 1
                if done >= month_start: completed_month += 1
                try:
                    created_dt = datetime.fromisoformat(str(t.get("created_at")).replace("Z", "+00:00"))
                    done_dt = datetime.fromisoformat(str(t.get("completed_at") or t.get("updated_at")).replace("Z", "+00:00"))
                    completion_hours.append(max(0, (done_dt - created_dt).total_seconds() / 3600))
                except Exception:
                    pass
            sname = service_names.get(t.get("service_id")) or "No Service"
            by_service[sname] = by_service.get(sname, 0) + 1
            if st != "COMPLETED":
                wname = emp_names.get(t.get("employee_id")) if t.get("employee_id") else None
                wname = wname or ("Unassigned" if not t.get("employee_id") else "Unknown")
                workload[wname] = workload.get(wname, 0) + 1
            c_month = str(t.get("created_at") or "")[:7]
            if c_month: created[c_month] = created.get(c_month, 0) + 1
            if st == "COMPLETED":
                d_month = str(t.get("completed_at") or t.get("updated_at") or "")[:7]
                if d_month: completed[d_month] = completed.get(d_month, 0) + 1
        total = len(tasks)
        completed_total = by_status["COMPLETED"]
        months = sorted(set(created) | set(completed))
        return _ok({
            "summary": {
                "total": total, "completed": completed_total, "in_progress": by_status["IN PROGRESS"],
                "review": by_status["REVIEW"], "todo": by_status["TO DO"], "overdue": overdue,
                "due_today": due_today, "completion_percentage": round((completed_total / total * 100), 1) if total else 0,
                "pending_percentage": round(((total - completed_total) / total * 100), 1) if total else 0,
                "overdue_percentage": round((overdue / total * 100), 1) if total else 0,
                "average_completion_hours": round(sum(completion_hours) / len(completion_hours), 1) if completion_hours else 0,
                "completed_today": completed_today, "completed_this_week": completed_week, "completed_this_month": completed_month,
            },
            "by_status": [{"name": s, "value": by_status[s]} for s in TASK_STATUSES],
            "by_priority": [{"name": p, "value": by_priority[p]} for p in TASK_PRIORITIES],
            "by_service": sorted([{"name": k, "value": v} for k, v in by_service.items()], key=lambda x: -x["value"]),
            "workload": sorted([{"name": k, "value": v} for k, v in workload.items()], key=lambda x: -x["value"]),
            "trend": [{"m": m, "created": created.get(m, 0), "completed": completed.get(m, 0)} for m in months],
        })


    @router.get("/employee/tasks")
    async def list_employee_tasks(user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Employee access required")
        emp = await _employee_record_for_user(user)
        if not emp:
            raise HTTPException(status_code=404, detail="Employee profile not found")
        tasks = await db["erp_tasks"].find(
            {"task_board": True, "employee_id": emp.get("id")},
            {"_id": 0},
        ).sort([("due_date", 1), ("created_at", -1)]).to_list(5000)
        return _ok([await _task_enrich(task) for task in tasks])

    @router.get("/employee/tasks/{task_id}")
    async def get_employee_task(task_id: str, user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Employee access required")
        emp = await _employee_record_for_user(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0}) if emp else None
        if not emp or not task or task.get("employee_id") != emp.get("id"):
            raise HTTPException(status_code=404, detail="Task not found")
        return _ok(await _task_enrich(task))

    @router.put("/employee/tasks/{task_id}")
    async def update_employee_task(task_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Employee access required")
        emp = await _employee_record_for_user(user)
        if not emp:
            raise HTTPException(status_code=404, detail="Employee profile not found")
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        if not task or task.get("employee_id") != emp.get("id"):
            raise HTTPException(status_code=404, detail="Task not found")
        status = str(body.get("status") or "").strip().upper()
        _validate_task_status_transition(task.get("status"), status)
        changes = {}
        if status != task.get("status"):
            changes["status"] = {"from": task.get("status"), "to": status}
            await db["erp_tasks"].update_one(
                {"id": task_id},
                {"$set": {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            await db["erp_task_activity"].insert_one({
                "id": f"TA-{uuid.uuid4().hex[:10].upper()}",
                "task_id": task_id,
                "type": "status",
                "text": f"Status changed from {task.get('status') or 'Unknown'} to {status}",
                "actor_id": user.get("id"),
                "actor_name": user.get("name") or emp.get("name"),
                "actor_role": "employee",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            # The Admin portal receives a task-linked MongoDB notification. The
            # task_id is intentionally included so clicking it opens the exact task.
            updated_task = await db["erp_tasks"].find_one({"id": task_id}, {"_id": 0}) or task
            await _task_notify(
                updated_task,
                "employee-status",
                f"Task status updated: {updated_task.get('title')}",
                f"{emp.get('name') or 'Employee'} moved this task to {status}.",
                event_suffix=f"employee:{updated_task.get('updated_at')}",
            )
        fresh = await db["erp_tasks"].find_one({"id": task_id}, {"_id": 0})
        return _ok(await _task_enrich(fresh), message="Task updated" if changes else "No change needed")

    @router.post("/employee/tasks/{task_id}/comment")
    async def add_employee_task_comment(task_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Employee access required")
        emp = await _employee_record_for_user(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0}) if emp else None
        if not emp or not task or task.get("employee_id") != emp.get("id"):
            raise HTTPException(status_code=404, detail="Task not found")
        comment = str(body.get("comment") or "").strip()
        if not comment:
            raise HTTPException(status_code=422, detail="Comment is required")
        activity = {
            "id": f"TA-{uuid.uuid4().hex[:10].upper()}",
            "task_id": task_id,
            "type": "comment",
            "text": comment,
            "actor_id": user.get("id"),
            "actor_name": user.get("name") or emp.get("name"),
            "actor_role": "employee",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db["erp_task_activity"].insert_one(activity)
        await _audit(user, "task_comment", "tasks", task_id, {"comment": comment})
        await _task_notify(
            task,
            "employee-comment",
            f"Task comment added: {task.get('title') or task_id}",
            f"{emp.get('name') or 'Employee'} added a comment to this task.",
            event_suffix=activity["id"],
        )
        return _ok(activity, message="Comment added")

    @router.get("/employee/workspace/summary")
    async def employee_workspace_summary(user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Employee access required")
        emp = await _employee_record_for_user(user)
        if not emp:
            raise HTTPException(status_code=404, detail="Employee profile not found")
        emp_id = emp.get("id")
        customer_rows = await db["erp_customers"].find({}, {"_id": 0}).to_list(5000)
        customers = [c for c in customer_rows if _customer_assigned_to_employee(c, emp)]
        customer_ids = {str(c.get("id")) for c in customers if c.get("id")}
        tasks = await db["erp_tasks"].find({"task_board": True, "employee_id": emp_id}, {"_id": 0}).to_list(5000)
        today = _ist_today_str()
        def due_bucket(t):
            d = str(t.get("due_date") or "")[:10]
            return d
        overdue = [t for t in tasks if due_bucket(t) and due_bucket(t) < today and str(t.get("status") or "").upper() != "COMPLETED"]
        due_today = [t for t in tasks if due_bucket(t) == today and str(t.get("status") or "").upper() != "COMPLETED"]
        in_progress = [t for t in tasks if str(t.get("status") or "").upper() == "IN PROGRESS"]
        review = [t for t in tasks if str(t.get("status") or "").upper() == "REVIEW"]
        compliance_counts = {}
        for key, coll_name in (("gst", "erp_gst"), ("itr", "erp_itr"), ("tds", "erp_tds"), ("roc", "erp_roc")):
            rows = await db[coll_name].find({"customer_id": {"$in": list(customer_ids)}}, {"_id": 0}).to_list(5000) if customer_ids else []
            compliance_counts[key] = len(rows)
        doc_rows = await db["erp_documents"].find({}, {"_id": 0, "file_data": 0}).to_list(5000)
        authorized_docs = [d for d in doc_rows if d.get("customer_id") and str(d.get("customer_id")) in customer_ids]
        appointments = await db["erp_appointments"].find({}, {"_id": 0}).to_list(5000)
        authorized_appointments = [
            a for a in appointments
            if str(a.get("employee_id") or "") == str(emp_id)
            or str(a.get("assigned_employee") or a.get("employee") or "") == str(emp.get("name") or "")
            or (a.get("customer_id") and str(a.get("customer_id")) in customer_ids)
        ]
        projects = await db["erp_projects"].find({}, {"_id": 0}).to_list(5000)
        authorized_projects = [
            p for p in projects
            if str(p.get("employee_id") or "") == str(emp_id)
            or str(p.get("assigned_employee") or p.get("employee") or "") == str(emp.get("name") or "")
            or (p.get("customer_id") and str(p.get("customer_id")) in customer_ids)
        ]
        recent_activity = await db["erp_task_activity"].find(
            {"task_id": {"$in": [t.get("id") for t in tasks if t.get("id")]}},
            {"_id": 0}
        ).sort("created_at", -1).to_list(10)
        return _ok({
            "employee": {k: emp.get(k) for k in ("id", "name", "email", "department", "designation")},
            "counts": {
                "customers": len(customers),
                "tasks": len(tasks),
                "pending_tasks": len([t for t in tasks if str(t.get("status") or "").upper() != "COMPLETED"]),
                "completed_tasks": len([t for t in tasks if str(t.get("status") or "").upper() == "COMPLETED"]),
                "due_today": len(due_today),
                "overdue": len(overdue),
                "in_progress": len(in_progress),
                "review": len(review),
                "documents": len(authorized_docs),
                "projects": len(authorized_projects),
                "meetings": len(authorized_appointments),
                **compliance_counts,
            },
            "deadlines": [
                {"id": t.get("id"), "title": t.get("title"), "customer_id": t.get("customer_id"),
                 "due_date": t.get("due_date"), "status": t.get("status"), "priority": t.get("priority")}
                for t in sorted(tasks, key=lambda x: str(x.get("due_date") or "9999-99-99"))[:20]
            ],
            "recent_activity": recent_activity,
        })


    @router.get("/employee/customers/{customer_id}/workspace")
    async def employee_customer_workspace(customer_id: str, user: dict = Depends(get_current_user)):
        if user.get("role") != "employee":
            raise HTTPException(status_code=403, detail="Employee access required")
        customer = await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0})
        if not customer or not await _employee_can_access_customer(customer_id, user):
            raise HTTPException(status_code=404, detail="Customer not found")
        bookings = await db["erp_bookings"].find({"customer_id": customer_id}, {"_id": 0}).sort("booking_date", -1).to_list(500)
        tasks = await db["erp_tasks"].find({"task_board": True, "customer_id": customer_id}, {"_id": 0}).sort("created_at", -1).to_list(500)
        docs = await db["erp_documents"].find({"customer_id": customer_id}, {"_id": 0, "file_data": 0}).sort("uploaded_date", -1).to_list(500)
        compliance = {}
        for key, coll_name in (("gst", "erp_gst"), ("itr", "erp_itr"), ("tds", "erp_tds"), ("roc", "erp_roc")):
            compliance[key] = await db[coll_name].find({"customer_id": customer_id}, {"_id": 0}).sort("created_at", -1).to_list(500)
        return _ok({
            "customer": customer,
            "bookings": bookings,
            "tasks": [await _task_enrich(t) for t in tasks],
            "documents": docs,
            "compliance": compliance,
            "activity": await db["erp_task_activity"].find(
                {"task_id": {"$in": [t.get("id") for t in tasks if t.get("id")]}},
                {"_id": 0}
            ).sort("created_at", -1).to_list(500),
        })


    async def _find_admin_task_by_identifier(task_id: str):
        """Resolve an existing task using the canonical public task id first.

        Older/legacy records may have only a Mongo ObjectId or a task_id field.
        The API boundary normalizes all of those to the stored public ``id`` so
        every Task Board entry point uses the same identifier thereafter.
        """
        raw = str(task_id or "").strip()
        if not raw:
            return None
        task = await db["erp_tasks"].find_one({"id": raw, "task_board": True}, {"_id": 0})
        if task:
            return task
        task = await db["erp_tasks"].find_one({"task_id": raw, "task_board": True}, {"_id": 0})
        if task:
            return task
        if ObjectId is not None:
            try:
                oid = ObjectId(raw)
                task = await db["erp_tasks"].find_one({"_id": oid, "task_board": True}, {"_id": 0})
                if task:
                    return task
            except Exception:
                pass
        return None

    @router.get("/admin/tasks")
    async def list_admin_tasks(
        search: str = Query(None),
        status: str = Query(None),
        priority: str = Query(None),
        employee_id: str = Query(None),
        customer_id: str = Query(None),
        service_id: str = Query(None),
        booking_id: str = Query(None),
        due: str = Query(None),
        my_tasks: bool = Query(False),
        unassigned: bool = Query(False),
        overdue: bool = Query(False),
        due_today: bool = Query(False),
        high_priority: bool = Query(False),
        sort_by: str = Query("created_at"),
        sort_order: str = Query("desc"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        user: dict = Depends(get_current_user),
    ):
        _admin_only(user)
        query = {"task_board": True}

        if status and status != "all":
            query["status"] = str(status).strip().upper()
        if priority and priority != "all":
            query["priority"] = str(priority).strip().title()
        if employee_id and employee_id != "all":
            query["employee_id"] = employee_id
        if customer_id and customer_id != "all":
            query["customer_id"] = customer_id
        if service_id and service_id != "all":
            query["service_id"] = service_id
        if booking_id and booking_id != "all":
            query["booking_id"] = booking_id

        # Resolve related names to IDs so search remains server-side without
        # duplicating customer/employee/service records into tasks.
        if search and search.strip():
            s = re.escape(search.strip())
            regex = {"$regex": s, "$options": "i"}
            ors = [{"title": regex}, {"description": regex}, {"id": regex}]
            related = await asyncio.gather(
                db["erp_customers"].find(
                    {"$or": [{"business_name": regex}, {"owner": regex}, {"name": regex}, {"email": regex}, {"mobile": regex}]},
                    {"_id": 0, "id": 1}
                ).to_list(200),
                db["erp_employees"].find(
                    {"$or": [{"name": regex}, {"emp_id": regex}]},
                    {"_id": 0, "id": 1}
                ).to_list(200),
                db["erp_services"].find(
                    {"$or": [{"name": regex}, {"title": regex}, {"category": regex}]},
                    {"_id": 0, "id": 1}
                ).to_list(200),
            )
            if related[0]:
                ors.append({"customer_id": {"$in": [x["id"] for x in related[0]]}})
            if related[1]:
                ors.append({"employee_id": {"$in": [x["id"] for x in related[1]]}})
            if related[2]:
                ors.append({"service_id": {"$in": [x["id"] for x in related[2]]}})
            booking_rows = await db["erp_bookings"].find(
                {"$or": [{"booking_no": regex}, {"id": regex}]},
                {"_id": 0, "id": 1}
            ).to_list(200)
            if booking_rows:
                ors.append({"booking_id": {"$in": [x["id"] for x in booking_rows]}})
            query["$or"] = ors

        if my_tasks:
            my_emp_id = (user.get("meta") or {}).get("employee_id")
            my_ors = [{"created_by": user.get("id")}]
            if my_emp_id:
                my_ors.append({"employee_id": my_emp_id})
            query["$or"] = my_ors if not query.get("$or") else {"$and": [{"$or": query.pop("$or")}, {"$or": my_ors}]}

        if unassigned:
            query["$or"] = [{"employee_id": None}, {"employee_id": ""}, {"employee_id": {"$exists": False}}]
        if high_priority:
            query["priority"] = {"$in": ["High", "Urgent"]}
        if overdue or due == "overdue":
            today = _ist_today_str()
            query["due_date"] = {"$lt": today}
            query["status"] = {"$ne": "COMPLETED"}
        elif due_today or due == "today":
            query["due_date"] = _ist_today_str()
            query["status"] = {"$ne": "COMPLETED"}
        elif due and due in {"tomorrow", "upcoming"}:
            today_dt = datetime.fromisoformat(_ist_today_str())
            if due == "tomorrow":
                target = (today_dt + timedelta(days=1)).date().isoformat()
                query["due_date"] = target
            else:
                query["due_date"] = {"$gt": _ist_today_str()}

        sort_map = {
            "due_date": "due_date",
            "priority": "priority",
            "created_at": "created_at",
            "updated_at": "updated_at",
            "customer": "customer_id",
            "employee": "employee_id",
            "task_name": "title",
            "title": "title",
        }
        sort_field = sort_map.get(sort_by, "created_at")
        direction = 1 if str(sort_order).lower() == "asc" else -1

        total = await db["erp_tasks"].count_documents(query)
        cursor = db["erp_tasks"].find(query, {"_id": 0}).sort([(sort_field, direction), ("id", 1)])
        raw_tasks = await cursor.skip((page - 1) * page_size).limit(page_size).to_list(page_size)
        await _notify_overdue_tasks(raw_tasks)
        result = [await _task_enrich(t) for t in raw_tasks]

        return _ok(
            result,
            pagination={
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": (total + page_size - 1) // page_size,
            },
        )

    @router.get("/admin/tasks/{task_id}")
    async def get_admin_task(task_id: str, user: dict = Depends(get_current_user)):
        _admin_only(user)
        task = await _find_admin_task_by_identifier(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        canonical_id = str(task.get("id") or task.get("task_id") or task_id)
        item = await _task_enrich(task)
        history = await db["erp_task_activity"].find({"task_id": canonical_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
        item["activity"] = history
        item["id"] = canonical_id
        return _ok(item)

    @router.post("/admin/tasks")
    async def create_admin_task(body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        title = str(body.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="Task title is required")
        priority = str(body.get("priority") or "Medium").strip().title()
        status = str(body.get("status") or "TO DO").strip().upper()
        if priority not in TASK_PRIORITIES:
            raise HTTPException(status_code=422, detail="Priority must be Low, Medium, High or Urgent")
        if status not in TASK_STATUSES:
            raise HTTPException(status_code=422, detail="Invalid task status")

        (customer_id, booking_id, service_id, employee_id, customer, booking, service, employee) = await _validate_task_links(body)
        now = datetime.now(timezone.utc).isoformat()
        task_id = f"TSK-{uuid.uuid4().hex[:8].upper()}"
        doc = {
            "id": task_id, "title": title, "description": str(body.get("description") or "").strip(),
            "priority": priority, "status": status, "employee_id": employee_id,
            "customer_id": customer_id, "booking_id": booking_id, "service_id": service_id,
            "service_name": (_task_service_name(service) if service else None) or ((booking or {}).get("service") or None),
            "due_date": str(body.get("due_date") or "").strip() or None,
            "task_board": True, "created_by": user.get("id"), "created_by_name": user.get("name") or user.get("email") or "Admin",
            "checklist": [], "attachments": [],
            "created_at": now, "updated_at": now,
        }
        await db["erp_tasks"].insert_one(doc)
        await _task_history(task_id, "Task created", user, {"status": status, "priority": priority})
        if employee_id:
            await _task_history(task_id, "Assigned to employee", user, {"employee_id": employee_id, "employee_name": _task_employee_name(employee)})
            await _task_notify(doc, "assigned", f"Task assigned: {title}",
                               f"You have been assigned '{title}'.", employee=True, event_suffix="created")
        await _task_notify(doc, "created", f"New task: {title}",
                           f"Task '{title}' was created.", event_suffix="created")
        return _ok(await _task_enrich(doc), message="Task created")

    @router.put("/admin/tasks/{task_id}")
    async def update_admin_task(task_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        existing = await db["erp_tasks"].find_one({"id": task_id}, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")

        changes = {}
        for key in ("title", "description", "priority", "due_date"):
            if key in body:
                value = body.get(key)
                if key == "title":
                    value = str(value or "").strip()
                    if not value:
                        raise HTTPException(status_code=422, detail="Task title is required")
                if key == "priority":
                    value = str(value or "Medium").strip().title()
                    if value not in TASK_PRIORITIES:
                        raise HTTPException(status_code=422, detail="Invalid priority")
                if key == "due_date":
                    value = str(value or "").strip() or None
                if existing.get(key) != value:
                    changes[key] = {"from": existing.get(key), "to": value}

        if "status" in body:
            status = str(body.get("status") or "").strip().upper()
            _validate_task_status_transition(existing.get("status"), status)
            if status != existing.get("status"):
                changes["status"] = {"from": existing.get("status"), "to": status}

        if "employee_id" in body:
            new_emp_id = str(body.get("employee_id") or "").strip() or None
            if new_emp_id:
                emp = await _task_employee(new_emp_id)
                if not emp:
                    raise HTTPException(status_code=404, detail="Employee not found")
            if new_emp_id != existing.get("employee_id"):
                changes["employee_id"] = {"from": existing.get("employee_id"), "to": new_emp_id}

        if "customer_id" in body or "booking_id" in body or "service_id" in body:
            candidate = dict(existing)
            candidate.update({k: body[k] for k in ("customer_id", "booking_id", "service_id") if k in body})
            links = await _validate_task_links(candidate)
            changes["links"] = {
                "from": {"customer_id": existing.get("customer_id"), "booking_id": existing.get("booking_id"), "service_id": existing.get("service_id")},
                "to": {"customer_id": links[0], "booking_id": links[1], "service_id": links[2]},
            }
            for k, v in zip(("customer_id", "booking_id", "service_id"), links[:3]):
                if existing.get(k) != v:
                    changes[k] = {"from": existing.get(k), "to": v}

        if not changes:
            return _ok(await _task_enrich(existing), message="No changes")

        update = {key: body.get(key) for key in ()}
        for key in ("title", "description", "priority", "due_date"):
            if key in body and key in changes:
                update[key] = changes[key]["to"]
        if "status" in changes:
            update["status"] = changes["status"]["to"]
            # An explicit status edit returns the task to the corresponding standard
            # Kanban column instead of leaving it attached to a deleted/custom list.
            update["kanban_list_id"] = None
            update["kanban_list_name"] = None
            # Completion timestamp feeds the dashboard completion trend.
            update["completed_at"] = datetime.now(timezone.utc).isoformat() if update["status"] == "COMPLETED" else None
        if "employee_id" in changes:
            update["employee_id"] = changes["employee_id"]["to"]
        for key in ("customer_id", "booking_id", "service_id"):
            if key in changes:
                update[key] = changes[key]["to"]
        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db["erp_tasks"].update_one({"id": task_id}, {"$set": update})
        updated = await db["erp_tasks"].find_one({"id": task_id}, {"_id": 0})

        if "status" in changes:
            label = changes["status"]["to"]
            await _task_history(task_id, "Status changed", user, changes["status"])
            if label == "REVIEW":
                await _task_notify(updated, "review", f"Task moved to Review: {updated.get('title')}",
                                    f"Task '{updated.get('title')}' is ready for review.", event_suffix=label)
            if label == "COMPLETED":
                await _task_notify(updated, "completed", f"Task completed: {updated.get('title')}",
                                    f"Task '{updated.get('title')}' has been completed.", event_suffix=label)
            if updated.get("employee_id"):
                await _task_notify(
                    updated,
                    "admin-status",
                    f"Task status updated: {updated.get('title')}",
                    f"Admin moved your task to {label}.",
                    employee=True,
                    event_suffix=f"admin:{updated.get('updated_at')}",
                )
        if "employee_id" in changes:
            new_emp = await _task_employee(changes["employee_id"]["to"])
            await _task_history(task_id, "Task reassigned", user, {
                "from_employee_id": changes["employee_id"]["from"], "to_employee_id": changes["employee_id"]["to"],
                "to_employee_name": _task_employee_name(new_emp) if new_emp else None,
            })
            if changes["employee_id"]["to"]:
                await _task_notify(updated, "reassigned", f"Task assigned: {updated.get('title')}",
                                   f"You have been assigned '{updated.get('title')}'.", employee=True, event_suffix=updated.get("updated_at"))
            await _task_notify(updated, "reassigned-admin", f"Task reassigned: {updated.get('title')}",
                               f"Task '{updated.get('title')}' was reassigned.", event_suffix=updated.get("updated_at"))
        # Notify the current assignee when Admin changes task details. Assignment
        # itself has its own notification above, so avoid sending a duplicate here.
        non_assignment_changes = {k: v for k, v in changes.items() if k != "employee_id"}
        if non_assignment_changes and updated.get("employee_id") and "status" not in changes:
            await _task_notify(
                updated,
                "updated",
                f"Task updated: {updated.get('title')}",
                "Admin updated the details of a task assigned to you.",
                employee=True,
                event_suffix=f"admin-edit:{updated.get('updated_at')}",
            )
        await _task_history(task_id, "Task updated", user, {"changes": changes})
        return _ok(await _task_enrich(updated), message="Task updated")

    @router.delete("/admin/tasks/{task_id}")
    async def delete_admin_task(task_id: str, user: dict = Depends(get_current_user)):
        _admin_only(user)
        existing = await db["erp_tasks"].find_one({"id": task_id}, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")
        await db["erp_tasks"].delete_one({"id": task_id})
        await db["erp_task_activity"].delete_many({"task_id": task_id})
        return _ok({"id": task_id}, message="Task deleted")

    @router.post("/admin/tasks/{task_id}/comment")
    async def comment_admin_task(task_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        task = await db["erp_tasks"].find_one({"id": task_id}, {"_id": 0})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        comment = str(body.get("comment") or "").strip()
        if not comment:
            raise HTTPException(status_code=422, detail="Comment is required")
        activity = await _task_history(task_id, "Comment added", user, {"comment": comment})
        if task.get("employee_id"):
            await _task_notify(
                task, "comment", f"New comment: {task.get('title')}",
                f"Admin added a comment to '{task.get('title')}'.",
                employee=True, event_suffix=activity.get("id")
            )
        return _ok(activity, message="Comment added")


    @router.post("/admin/tasks/{task_id}/checklist")
    async def add_task_checklist_item(task_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        text = str(body.get("text") or body.get("title") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="Checklist item is required")
        item = {"id": f"CHK-{uuid.uuid4().hex[:10].upper()}", "text": text, "completed": False,
                "created_at": datetime.now(timezone.utc).isoformat(), "created_by": user.get("id"),
                "created_by_name": user.get("name") or user.get("email") or "Admin"}
        await db["erp_tasks"].update_one({"id": task_id}, {"$push": {"checklist": item},
                                                          "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}})
        await _task_history(task_id, "Checklist item added", user, {"item": item})
        return _ok(item, message="Checklist item added")

    @router.put("/admin/tasks/{task_id}/checklist/{item_id}")
    async def update_task_checklist_item(task_id: str, item_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        item = next((x for x in (task.get("checklist") or []) if x.get("id") == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Checklist item not found")
        changes = {}
        if "text" in body:
            value = str(body.get("text") or "").strip()
            if not value:
                raise HTTPException(status_code=422, detail="Checklist item is required")
            if value != item.get("text"): changes["text"] = {"from": item.get("text"), "to": value}
        if "completed" in body:
            value = bool(body.get("completed"))
            if value != bool(item.get("completed")): changes["completed"] = {"from": bool(item.get("completed")), "to": value}
        if not changes:
            return _ok(item, message="No changes")
        update_fields = {}
        for key in ("text", "completed"):
            if key in changes: update_fields[f"checklist.$.{key}"] = changes[key]["to"]
        await db["erp_tasks"].update_one({"id": task_id, "checklist.id": item_id},
                                         {"$set": {**update_fields, "updated_at": datetime.now(timezone.utc).isoformat()}})
        await _task_history(task_id, "Checklist item updated", user, {"item_id": item_id, "changes": changes})
        updated = next((x for x in (await db["erp_tasks"].find_one({"id": task_id}, {"_id": 0})).get("checklist", []) if x.get("id") == item_id), item)
        return _ok(updated, message="Checklist item updated")

    @router.delete("/admin/tasks/{task_id}/checklist/{item_id}")
    async def delete_task_checklist_item(task_id: str, item_id: str, user: dict = Depends(get_current_user)):
        _admin_only(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        item = next((x for x in (task.get("checklist") or []) if x.get("id") == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Checklist item not found")
        await db["erp_tasks"].update_one({"id": task_id}, {"$pull": {"checklist": {"id": item_id}},
                                                          "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}})
        await _task_history(task_id, "Checklist item deleted", user, {"item": item})
        return _ok({"id": item_id}, message="Checklist item deleted")

    @router.post("/admin/tasks/{task_id}/attachments")
    async def upload_task_attachment(task_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        attachment = dict(body)
        _validate_document_upload(attachment, user)
        attachment = {
            "id": f"ATT-{uuid.uuid4().hex[:12].upper()}",
            "file_name": attachment.get("file_name"),
            "file_type": attachment.get("file_type") or attachment.get("mime_type"),
            "mime_type": attachment.get("mime_type"),
            "file_size": attachment.get("file_size"),
            "file_data": attachment.get("file_data"),
            "file_hash": attachment.get("file_hash"),
            "uploaded_by": user.get("name") or user.get("email") or "Admin",
            "uploaded_by_id": user.get("id"),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        # Keep attachment bytes task-scoped; they are never returned by _task_enrich.
        await db["erp_tasks"].update_one({"id": task_id}, {"$push": {"attachments": attachment},
                                                          "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}})
        await _task_history(task_id, "Attachment uploaded", user, {"attachment_id": attachment["id"], "file_name": attachment["file_name"]})
        public = {k: attachment.get(k) for k in ("id", "file_name", "file_type", "mime_type", "file_size", "uploaded_by", "uploaded_by_id", "uploaded_at")}
        return _ok(public, message="Attachment uploaded")

    @router.get("/admin/tasks/{task_id}/attachments/{attachment_id}")
    async def get_task_attachment(task_id: str, attachment_id: str, user: dict = Depends(get_current_user)):
        _admin_only(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        attachment = next((x for x in (task.get("attachments") or []) if x.get("id") == attachment_id), None)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return _ok({k: attachment.get(k) for k in ("id", "file_name", "file_type", "mime_type", "file_size", "file_data", "uploaded_by", "uploaded_at")})

    @router.delete("/admin/tasks/{task_id}/attachments/{attachment_id}")
    async def delete_task_attachment(task_id: str, attachment_id: str, user: dict = Depends(get_current_user)):
        _admin_only(user)
        task = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        attachment = next((x for x in (task.get("attachments") or []) if x.get("id") == attachment_id), None)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        await db["erp_tasks"].update_one({"id": task_id}, {"$pull": {"attachments": {"id": attachment_id}},
                                                          "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}})
        await _task_history(task_id, "Attachment deleted", user, {"attachment_id": attachment_id, "file_name": attachment.get("file_name")})
        return _ok({"id": attachment_id}, message="Attachment deleted")

    @router.post("/admin/tasks/{task_id}/duplicate")
    async def duplicate_admin_task(task_id: str, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
        _admin_only(user)
        source = await db["erp_tasks"].find_one({"id": task_id, "task_board": True}, {"_id": 0})
        if not source:
            raise HTTPException(status_code=404, detail="Task not found")
        candidate = dict(source)
        for key in ("_id", "id", "created_at", "updated_at", "completed_at", "kanban_list_id", "kanban_list_name", "activity"):
            candidate.pop(key, None)
        candidate["id"] = f"TSK-{uuid.uuid4().hex[:8].upper()}"
        candidate["title"] = str(body.get("title") or f"Copy of {source.get('title') or 'Task'}").strip()
        candidate["status"] = "TO DO"
        candidate["customer_id"] = body.get("customer_id", source.get("customer_id"))
        candidate["booking_id"] = body.get("booking_id", source.get("booking_id"))
        candidate["service_id"] = body.get("service_id", source.get("service_id"))
        candidate["employee_id"] = body.get("employee_id", source.get("employee_id"))
        candidate["due_date"] = body.get("due_date", None)
        candidate["checklist"] = []
        for item in source.get("checklist") or []:
            candidate["checklist"].append({**item, "id": f"CHK-{uuid.uuid4().hex[:10].upper()}", "completed": False})
        candidate["attachments"] = []
        candidate["created_by"] = user.get("id")
        candidate["created_by_name"] = user.get("name") or user.get("email") or "Admin"
        candidate["created_at"] = datetime.now(timezone.utc).isoformat()
        candidate["updated_at"] = candidate["created_at"]
        links = await _validate_task_links(candidate)
        candidate.update({"customer_id": links[0], "booking_id": links[1], "service_id": links[2], "employee_id": links[3]})
        await db["erp_tasks"].insert_one(candidate)
        await _task_history(candidate["id"], "Task duplicated", user, {"source_task_id": source["id"], "source_title": source.get("title")})
        if candidate.get("employee_id"):
            await _task_notify(candidate, "assigned", f"Task assigned: {candidate.get('title')}",
                               f"You have been assigned '{candidate.get('title')}'.", employee=True, event_suffix="duplicated")
        return _ok(await _task_enrich(candidate), message="Task duplicated")

    @router.post("/admin/tasks/bulk")
    async def bulk_admin_tasks(body: dict = Body(...), user: dict = Depends(get_current_user)):
        _admin_only(user)
        ids = [str(x).strip() for x in (body.get("task_ids") or []) if str(x).strip()]
        action = str(body.get("action") or "").strip().lower()
        if not ids or not action:
            raise HTTPException(status_code=422, detail="task_ids and action are required")
        found = await db["erp_tasks"].find({"id": {"$in": ids}, "task_board": True}, {"_id": 0}).to_list(len(ids))
        if not found:
            raise HTTPException(status_code=404, detail="No matching tasks found")
        now = datetime.now(timezone.utc).isoformat()
        results = []
        for task in found:
            update = {}
            if action == "assign":
                emp_id = str(body.get("employee_id") or "").strip()
                if emp_id and not await _task_employee(emp_id):
                    raise HTTPException(status_code=404, detail="Employee not found")
                update["employee_id"] = emp_id or None
            elif action == "priority":
                value = str(body.get("priority") or "").strip().title()
                if value not in TASK_PRIORITIES: raise HTTPException(status_code=422, detail="Invalid priority")
                update["priority"] = value
            elif action == "status":
                value = str(body.get("status") or "").strip().upper()
                _validate_task_status_transition(task.get("status"), value)
                update["status"] = value
                update["completed_at"] = now if value == "COMPLETED" else None
            elif action == "due_date":
                update["due_date"] = str(body.get("due_date") or "").strip() or None
            elif action == "delete":
                await db["erp_tasks"].delete_one({"id": task["id"]})
                await db["erp_task_activity"].delete_many({"task_id": task["id"]})
                results.append({"id": task["id"], "action": "deleted"})
                continue
            else:
                raise HTTPException(status_code=422, detail="Unsupported bulk action")
            update["updated_at"] = now
            await db["erp_tasks"].update_one({"id": task["id"]}, {"$set": update})
            changed = {k: {"from": task.get(k), "to": v} for k, v in update.items() if k != "updated_at" and task.get(k) != v}
            await _task_history(task["id"], f"Bulk {action}", user, {"changes": changed})
            results.append({"id": task["id"], "action": action, "changes": changed})
        return _ok(results, message=f"Bulk {action} completed")

    @router.get("/admin/tasks/workload")
    async def admin_task_workload(user: dict = Depends(get_current_user)):
        _admin_only(user)
        employees = await db["erp_employees"].find({}, {"_id": 0, "id": 1, "name": 1, "emp_id": 1}).sort("name", 1).to_list(5000)
        rows = []
        for emp in employees:
            task_rows = await db["erp_tasks"].find(
                {"task_board": True, "employee_id": emp["id"]},
                {"_id": 0}
            ).sort("due_date", 1).to_list(10000)
            counts = {s: 0 for s in TASK_STATUSES}
            overdue_count = high_count = 0
            for t in task_rows:
                st = str(t.get("status") or "TO DO").upper()
                if st in counts: counts[st] += 1
                if st != "COMPLETED" and _task_due_state(t.get("due_date")) == "overdue": overdue_count += 1
                if t.get("priority") in ("High", "Urgent"): high_count += 1
            task_items = []
            for t in task_rows:
                canonical_id = str(t.get("id") or t.get("task_id") or "").strip()
                if not canonical_id:
                    continue
                task_items.append({
                    "id": canonical_id,
                    "title": t.get("title") or "Untitled task",
                    "status": str(t.get("status") or "TO DO").upper(),
                    "priority": t.get("priority") or "Medium",
                    "due_date": t.get("due_date"),
                    "customer_id": t.get("customer_id"),
                    "booking_id": t.get("booking_id"),
                    "service_id": t.get("service_id"),
                })
            rows.append({"employee_id": emp["id"], "employee": _task_employee_name(emp), "total": len(task_rows),
                         "todo": counts["TO DO"], "in_progress": counts["IN PROGRESS"], "review": counts["REVIEW"],
                         "completed": counts["COMPLETED"], "overdue": overdue_count, "high_priority": high_count,
                         "tasks": task_items})
        return _ok(rows)

    @router.get("/admin/tasks/{task_id}/history")
    async def history_admin_task(task_id: str, user: dict = Depends(get_current_user)):
        _admin_only(user)
        if not await db["erp_tasks"].find_one({"id": task_id}, {"_id": 1}):
            raise HTTPException(status_code=404, detail="Task not found")
        items = await db["erp_task_activity"].find({"task_id": task_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
        return _ok(items)

    return router


async def ensure_phase4_relationships(db):
    """Backfill and index stable Phase-4 foreign keys without changing display fields.

    Existing records are migrated once where an unambiguous match exists. Names
    are used only to locate legacy records during migration; all persisted
    relationships after this pass use backend IDs.
    """
    # Link customer portal accounts to their canonical ERP customer record.
    async for user in db.users.find({"role": "customer"}, {"_id": 0, "id": 1, "email": 1, "mobile": 1, "meta": 1}):
        if (user.get("meta") or {}).get("customer_id"):
            continue
        clauses = []
        if user.get("email"):
            clauses.append({"email": user["email"]})
        if user.get("mobile"):
            clauses.append({"mobile": user["mobile"]})
        if clauses:
            customer = await db["erp_customers"].find_one({"$or": clauses}, {"_id": 0, "id": 1})
            if customer:
                await db.users.update_one({"id": user["id"]}, {"$set": {"meta.customer_id": customer["id"]}})

    customers = await db["erp_customers"].find({}, {"_id": 0, "id": 1, "business_name": 1}).to_list(10000)
    customer_by_name = {str(x.get("business_name", "")).strip().lower(): x["id"] for x in customers if x.get("business_name")}

    services = await db["erp_services"].find({}, {"_id": 0, "id": 1, "name": 1, "title": 1}).to_list(10000)
    service_by_name = {}
    for x in services:
        for key in (x.get("name"), x.get("title")):
            if key:
                service_by_name[str(key).strip().lower()] = x["id"]

    agents = await db["erp_agents"].find({}, {"_id": 0, "id": 1, "name": 1}).to_list(10000)
    agent_by_name = {str(x.get("name", "")).strip().lower(): x["id"] for x in agents if x.get("name")}

    async def canonical_customer(value, display=None):
        if value:
            if any(x["id"] == value for x in customers):
                return value
            account = await db.users.find_one(
                {"role": "customer", "$or": [{"id": value}, {"meta.customer_id": value}]},
                {"_id": 0, "meta": 1},
            )
            cid = (account or {}).get("meta", {}).get("customer_id")
            if cid:
                return cid
        if display:
            return customer_by_name.get(str(display).strip().lower())
        return None

    async def canonical_service(value, display=None):
        if value and any(x["id"] == value for x in services):
            return value
        return service_by_name.get(str(display).strip().lower()) if display else None

    async def canonical_agent(value, display=None):
        if value and any(x["id"] == value for x in agents):
            return value
        return agent_by_name.get(str(display).strip().lower()) if display else None

    # Customer relationships.
    for collection in ("erp_bookings", "erp_invoices", "erp_payments", "erp_projects", "erp_documents", "erp_tickets", "erp_gst", "erp_itr", "erp_tds", "erp_roc"):
        async for row in db[collection].find({}, {"_id": 0, "id": 1, "customer_id": 1, "customer": 1, "client": 1, "client_name": 1, "company": 1, "business_name": 1}):
            cid = await canonical_customer(
                row.get("customer_id"),
                row.get("customer") or row.get("client") or row.get("client_name") or row.get("company") or row.get("business_name"),
            )
            if cid and row.get("customer_id") != cid:
                await db[collection].update_one({"id": row["id"]}, {"$set": {"customer_id": cid}})

    # Service relationships.
    for collection in ("erp_bookings", "erp_invoices", "erp_payments"):
        async for row in db[collection].find({}, {"_id": 0, "id": 1, "service_id": 1, "service": 1, "service_name": 1}):
            sid = await canonical_service(row.get("service_id"), row.get("service") or row.get("service_name"))
            if sid and row.get("service_id") != sid:
                await db[collection].update_one({"id": row["id"]}, {"$set": {"service_id": sid}})

    # Agent relationships.
    for collection in ("erp_bookings", "erp_commissions", "erp_payments", "erp_leads", "erp_appointments", "erp_projects", "erp_documents"):
        async for row in db[collection].find({}, {"_id": 0, "id": 1, "agent_id": 1, "assigned_agent": 1, "agent": 1, "agent_name": 1}):
            aid = await canonical_agent(row.get("agent_id"), row.get("assigned_agent") or row.get("agent") or row.get("agent_name"))
            if aid and row.get("agent_id") != aid:
                await db[collection].update_one({"id": row["id"]}, {"$set": {"agent_id": aid}})

    # Invoice relationships on payments.
    async for row in db["erp_payments"].find({}, {"_id": 0, "id": 1, "invoice_id": 1, "invoice_no": 1}):
        invoice = None
        if row.get("invoice_id"):
            invoice = await db["erp_invoices"].find_one({"id": row["invoice_id"]}, {"_id": 0, "id": 1})
        if not invoice and row.get("invoice_no"):
            invoice = await db["erp_invoices"].find_one({"invoice_no": row["invoice_no"]}, {"_id": 0, "id": 1, "invoice_no": 1})
        if invoice and row.get("invoice_id") != invoice["id"]:
            await db["erp_payments"].update_one({"id": row["id"]}, {"$set": {"invoice_id": invoice["id"]}})

    # Query indexes for relationship-heavy screens and financial reconciliation.
    indexes = [
        ("erp_bookings", [("customer_id", 1), ("service_id", 1), ("agent_id", 1)]),
        ("erp_invoices", [("customer_id", 1), ("booking_id", 1), ("service_id", 1)]),
        ("erp_payments", [("customer_id", 1), ("invoice_id", 1), ("booking_id", 1), ("service_id", 1), ("agent_id", 1)]),
        ("erp_commissions", [("agent_id", 1)]),
    ]
    for collection, fields in indexes:
        await ensure_index(db[collection], fields, name="phase4_relationships")


async def seed_erp(db):
    """Optional development seed, disabled by default.

    Real ERP deployments must start with an empty/real MongoDB database and
    create records through the authenticated Admin/portal workflows. Existing
    MongoDB data is never deleted or overwritten by startup.
    """
    import os
    if os.getenv("NTAXCO_ENABLE_DEMO_SEED", "false").lower() != "true":
        return
    for name, (coll, seed, prefix) in COLLECTIONS.items():
        if await db[coll].count_documents({}) == 0 and seed:
            await db[coll].insert_many([{**d} for d in seed])
