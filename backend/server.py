from dotenv import load_dotenv
from pathlib import Path
import os
import re
import asyncio
import logging
import uuid
import jwt
import bcrypt

from fastapi import FastAPI, APIRouter, HTTPException, Request, Depends, Body, Query
from starlette.responses import HTMLResponse, StreamingResponse
from io import BytesIO
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime, timezone, timedelta

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

import security
from ledger_access import can_access_ledger
from web_ledger_access import web_ledger_authorized
from otp_guard import otp_send_guard
from billbook_subscription import build_billbook_subscription_router, customer_has_active_subscription
from billbook_rules import subscription_required as billbook_subscription_required
from index_utils import ensure_index


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.getenv("MONGO_URL")


def _mask_mongo_url(url: str) -> str:
    """Redact any credentials in a mongodb:// or mongodb+srv:// URL before
    it ever hits logs — e.g. mongodb+srv://user:pass@host -> mongodb+srv://***@host.
    Previously this was printed to logs unmasked, which leaks DB credentials
    into any log aggregator in production."""
    return re.sub(r"//[^@/]+@", "//***@", url or "")


print("DEBUG MONGO_URL =", _mask_mongo_url(mongo_url))

if not mongo_url:
    raise Exception("MONGO_URL is missing")

# serverSelectionTimeoutMS: fail fast (8s) instead of Motor's 30s default.
# Without this, a wrong/unreachable MONGO_URL (e.g. MongoDB not started
# locally, or a typo'd Atlas URI) makes every DB-touching request hang for
# 30s before erroring — which reads as a generic "Network Error"/timeout in
# the browser with no clue why. See the startup ping below for the actual
# diagnostic message.
client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=8000)

db = client[os.getenv("DB_NAME", "ntaxco")]

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    # Fail loudly and specifically at startup rather than with a bare
    # KeyError. A missing required env var here crashes the whole process
    # on boot — on most hosting platforms that means the platform's own
    # reverse proxy/load balancer will return a raw 502 Bad Gateway (with
    # no CORS headers, since the app never started) for every single
    # request, which the browser reports as a generic "Network Error".
    # This message is what should show up in the deployment's backend
    # logs when that happens.
    raise Exception(
        "JWT_SECRET is missing. Set it in the backend's environment "
        "variables (backend/.env locally, or the hosting platform's env "
        "var settings in production) and restart the backend."
    )
JWT_ALGORITHM = "HS256"
FIXED_ADMIN_PASSWORD = "Admin@12"

# ---------------- Twilio Verify (real SMS OTP) ----------------
# Credentials are read from the environment only — never hard-coded, never
# sent to the frontend, never included in API responses/logs.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_VERIFY_SERVICE_SID = os.getenv("TWILIO_VERIFY_SERVICE_SID")

print(f"[OTP] TWILIO_ACCOUNT_SID loaded: {bool(TWILIO_ACCOUNT_SID)}", flush=True)
print(f"[OTP] TWILIO_AUTH_TOKEN loaded: {bool(TWILIO_AUTH_TOKEN)}", flush=True)
print(f"[OTP] TWILIO_VERIFY_SERVICE_SID loaded: {bool(TWILIO_VERIFY_SERVICE_SID)}", flush=True)

_twilio_client: Optional[TwilioClient] = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    _twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
else:
    # Don't crash the whole backend on import (other routes / dev work should
    # still function); mobile OTP endpoints will fail loudly and safely if
    # actually called without credentials configured.
    logging.getLogger(__name__).warning(
        "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set — mobile OTP login "
        "will not work until Twilio credentials are configured in .env"
    )


def get_twilio_client() -> TwilioClient:
    if _twilio_client is None or not TWILIO_VERIFY_SERVICE_SID:
        raise HTTPException(
            status_code=503,
            detail="Mobile OTP login is not configured on the server. Please contact support.",
        )
    return _twilio_client


def mask_mobile(mobile: str) -> str:
    """Safe-for-logs representation of a mobile number, e.g. 90******58."""
    m = str(mobile or "")
    if len(m) <= 4:
        return "*" * len(m)
    return f"{m[:2]}{'*' * (len(m) - 4)}{m[-2:]}"

PORTAL_ROLES = {"admin", "employee", "customer", "agent"}

PORTAL_ROLE_NAMES = {
    "admin": "Super Admin",
    "employee": "Employee",
    "customer": "Customer",
    "agent": "Tax Consultant",
}


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


app = FastAPI(
    title="NTAXCO ERP API",
    version="1.0.0"
)


@app.get("/")
async def root():
    return {
        "status": "success",
        "message": "NTAXCO ERP Backend is running"
    }


@app.get("/api/health")
async def api_health():
    """Non-secret deployment check: confirms this process serves the /api
    routes the Admin frontend needs (a stale/older backend revision will
    404 here or list fewer features). Exposes no credentials or data."""
    paths = {getattr(r, "path", "") for r in app.routes}
    wanted = {
        "admin_dashboard": "/api/admin/dashboard",
        "admin_analytics": "/api/admin/analytics/summary",
        "admin_profile": "/api/admin/profile/me",
        "admin_global_search": "/api/admin/search",
        "dynamic_config": "/api/admin/dynamic-config",
        "dynamic_records": "/api/dynamic-records/{module_key}",
        "admin_security": "/api/admin/security/overview",
        # Admin -> Customers module. If either is false the process answering this port is a stale
        # backend revision (the symptom: GET /admin/customers/summary -> 404 while the plain customer
        # list still works). Restart uvicorn from THIS project's backend/ folder.
        "customer_summary": "/api/admin/customers/summary",
        "workflow_drag_drop": "/api/admin/workflows/drag-drop",
        "revenue": "/api/revenue",
        "revenue_employees": "/api/revenue/employees",
        "revenue_agents": "/api/revenue/agents",
        "revenue_customers": "/api/revenue/customers",
        "revenue_transactions": "/api/revenue/transactions",
        # Revenue Kanban. Lists/list-item/move are intentionally implemented by
        # the existing generic {board} routes in erp.py; Revenue cards has its
        # own exact route because its response is Revenue-specific.
        "revenue_kanban_lists": "/api/admin/kanban/{board}/lists",
        "revenue_kanban_list_item": "/api/admin/kanban/{board}/lists/{list_id}",
        "revenue_kanban_move": "/api/admin/kanban/{board}/move",
        "revenue_kanban_cards": "/api/admin/kanban/revenue/cards",
    }
    features = {k: (v in paths) for k, v in wanted.items()}
    return {
        "status": "ok",
        "db_name": os.getenv("DB_NAME", "ntaxco"),
        "features": features,
        # Keep these explicit for deployment/startup diagnostics. A generic
        # {board} route must not make Revenue look healthy when its exact
        # endpoints are absent.
        "revenue_kanban_lists": features["revenue_kanban_lists"],
        "revenue_kanban_cards": features["revenue_kanban_cards"],
        "revenue_kanban_move": features["revenue_kanban_move"],
    }


_cors_origins_env = os.getenv("CORS_ORIGINS")

if _cors_origins_env:
    _cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
else:
    # No insecure "allow everything" default. If CORS_ORIGINS isn't set,
    # assume local development (matches frontend/.env's default of
    # http://localhost:3000) rather than silently opening the API to any
    # origin in production. In production this MUST be set explicitly to
    # the deployed frontend's exact origin (scheme + host, no trailing
    # slash), e.g. CORS_ORIGINS=https://your-frontend-domain.vercel.app —
    # otherwise every request from the real deployed frontend will be
    # blocked by the browser and show up there as a generic "Network
    # Error", even though the backend itself is healthy.
    _cors_origins = ["http://localhost:3000", "http://localhost:3001", "http://localhost:3002", "http://127.0.0.1:3000", "http://127.0.0.1:3001", "http://127.0.0.1:3002"]
    logger.warning(
        "CORS_ORIGINS is not set — defaulting to local dev origins %s. "
        "Set CORS_ORIGINS to the deployed frontend's exact URL in production.",
        _cors_origins,
    )

print(f"[CORS] Allowed origins: {_cors_origins}", flush=True)

# LAN dev access (e.g. opening the frontend via its "Network" IP like
# http://192.168.1.23:3000 instead of http://localhost:3000):
#
# The exact-match _cors_origins list above can't include that IP because
# it's assigned by the router/DHCP and changes machine to machine and
# network to network — hard-coding one would break for everyone else and
# need editing every time it changes. Instead of falling back to a
# wildcard ("*", which Starlette also refuses to combine with
# allow_credentials=True), allow_origin_regex is used to match ONLY
# private, non-routable LAN addresses (RFC 1918: 192.168.x.x, 10.x.x.x,
# 172.16-31.x.x) plus localhost/127.0.0.1, on the frontend dev ports this
# project actually uses (3000, 3001). A real public domain used in
# production will never match this pattern, so it adds no risk there.
# Override with CORS_ORIGIN_REGEX in .env if a project needs something
# different; leave unset to keep this safe default.
_cors_origin_regex = os.getenv("CORS_ORIGIN_REGEX") or (
    r"^http://("
    r"localhost"
    r"|127\.0\.0\.1"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
    r"):(3000|3001|3002)$"
)

print(f"[CORS] LAN origin pattern: {_cors_origin_regex}", flush=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


api_router = APIRouter(prefix="/api")



class DynamicConfigModel(BaseModel):
    name: str
    key: Optional[str] = None
    description: Optional[str] = ""
    enabled: bool = True
    fields: List[dict] = []
    options: List[dict] = []
    sections: List[dict] = []
    content: List[dict] = []


# ---------------- Dynamic (Admin-created) ERP modules ----------------
# Slugs that must never be handed out to an admin-created module because
# they already correspond to a real, hard-coded route/collection elsewhere
# in the app. Without this a module named e.g. "Employees" would silently
# shadow (or be shadowed by) the real Employees module.
RESERVED_MODULE_SLUGS = {
    "dashboard", "employees", "leave-requests", "customers", "agents", "bookings",
    "services", "projects", "gst", "income-tax", "itr", "tds", "roc", "accounting",
    "invoices", "payments", "documents", "reports", "notifications", "security",
    "settings", "modules", "dynamic-records", "dynamic-config", "attendance", "leave",
    "tasks", "payslips", "calendar", "meetings", "performance", "profile", "admin-profile", "leads",
    "onboarding", "appointments", "commission", "commissions", "home", "login",
    "support", "messages", "journal", "site-images", "images",
}

# Field types the dynamic-module form builder and record editor understand.
DYNAMIC_FIELD_TYPES = {"text", "textarea", "number", "select", "date"}


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return s or "module"


def _normalize_dynamic_fields(fields: List[dict]) -> List[dict]:
    """Validate + normalize an admin-submitted field list into a safe,
    consistent shape: {key, label, type, required, options}. Unknown field
    types fall back to plain text instead of erroring, so a small typo in
    the type never breaks module creation. Field keys are de-duplicated so
    two fields can never silently overwrite each other's data."""
    normalized = []
    seen_keys = set()
    for f in (fields or []):
        if not isinstance(f, dict):
            continue
        label = str(f.get("label") or f.get("name") or "").strip()
        if not label:
            continue
        ftype = str(f.get("type") or "text").strip().lower()
        if ftype not in DYNAMIC_FIELD_TYPES:
            ftype = "text"
        key = re.sub(r"[^a-z0-9]+", "_", str(f.get("key") or label).lower()).strip("_") or "field"
        base_key, n = key, 2
        while key in seen_keys:
            key = f"{base_key}_{n}"; n += 1
        seen_keys.add(key)
        options = [str(o).strip() for o in (f.get("options") or []) if str(o).strip()] if ftype == "select" else []
        normalized.append({
            "key": key, "label": label, "type": ftype,
            "required": bool(f.get("required", False)), "options": options,
        })
    return normalized


def _validate_dynamic_record(fields: List[dict], body: dict) -> dict:
    """Validate a submitted record's values against a module's field
    configuration (required-ness, number parsing, dropdown membership)
    and return the clean, storable field->value dict."""
    data, errors = {}, []
    for f in fields:
        key, label, ftype = f["key"], f["label"], f["type"]
        val = body.get(key, "")
        if ftype == "number":
            if val in ("", None):
                if f.get("required"):
                    errors.append(f"{label} is required")
                data[key] = None
            else:
                try:
                    num = float(val)
                    data[key] = int(num) if num.is_integer() else num
                except (TypeError, ValueError):
                    errors.append(f"{label} must be a number")
        else:
            sval = "" if val is None else str(val)
            if f.get("required") and not sval.strip():
                errors.append(f"{label} is required")
            if ftype == "select" and sval and f.get("options") and sval not in f["options"]:
                errors.append(f"{label} must be one of: {', '.join(f['options'])}")
            data[key] = sval
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return data

async def get_current_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        session_id = payload.get("sid")
        if session_id:
            if not await security.is_session_active(db, session_id):
                raise HTTPException(status_code=401, detail="Session expired. Please login again.")
            # Best-effort, throttled — never blocks the request on failure.
            await security.touch_session_activity(db, session_id)
        # Transient only (not persisted): lets /auth/logout and other
        # endpoints know which session this request's token belongs to.
        user["_session_id"] = session_id
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please login again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")



@api_router.get("/customer/ledger/access")
async def customer_ledger_access(
    user=Depends(get_current_user)
):
    """Return Ledger entitlement for the authenticated NTAXCO customer."""

    if user.get("role") != "customer":
        raise HTTPException(
            status_code=403,
            detail="Ledger is available to customer accounts only."
        )

    # Authentication already happened in get_current_user; this only decides entitlement.
    # No payment/subscription is consulted (see ledger_access.py).
    allowed = can_access_ledger(user)

    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="My Ledger access has not been enabled for this account. Please contact NTAXCO support."
        )

    return {"success": True, "data": {"allowed": True}}



@api_router.get("/admin/dynamic-config")
async def list_dynamic_configs(user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    return await db.dynamic_configs.find({}, {"_id": 0}).to_list(1000)

@api_router.post("/admin/dynamic-config")
async def create_dynamic_config(payload: DynamicConfigModel, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Module name is required")

    existing_all = await db.dynamic_configs.find({}, {"_id": 0}).to_list(1000)
    if any((c.get("name") or "").strip().lower() == name.lower() for c in existing_all):
        raise HTTPException(status_code=409, detail=f"A module named '{name}' already exists")

    doc = payload.model_dump()
    doc["name"] = name
    doc["fields"] = _normalize_dynamic_fields(doc.get("fields"))

    # Generate a safe, unique, URL-friendly slug — never trust a
    # client-supplied key as-is, and never collide with a reserved/existing
    # route so the new module can't shadow (or be shadowed by) another page.
    base_key = _slugify(doc.get("key") or name)
    existing_keys = {c.get("key") for c in existing_all}
    key, n = base_key, 2
    while key in existing_keys or key in RESERVED_MODULE_SLUGS:
        key = f"{base_key}-{n}"; n += 1
    doc["key"] = key

    doc["id"] = str(uuid.uuid4())
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.dynamic_configs.insert_one(doc); doc.pop("_id", None)
    return doc

@api_router.put("/admin/dynamic-config/{config_id}")
async def update_dynamic_config(config_id: str, payload: DynamicConfigModel, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    existing = await db.dynamic_configs.find_one({"id": config_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Configuration not found")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Module name is required")

    others = await db.dynamic_configs.find({"id": {"$ne": config_id}}, {"_id": 0}).to_list(1000)
    if any((c.get("name") or "").strip().lower() == name.lower() for c in others):
        raise HTTPException(status_code=409, detail=f"A module named '{name}' already exists")

    doc = payload.model_dump()
    doc["name"] = name
    doc["fields"] = _normalize_dynamic_fields(doc.get("fields"))
    # The slug/key is the module's permanent identity (sidebar route, record
    # storage) — it's set once at creation and never changes on edit, so
    # existing links and saved records never silently break.
    doc["key"] = existing["key"]
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.dynamic_configs.update_one({"id": config_id}, {"$set": doc})
    return {"id": config_id, **doc}

@api_router.delete("/admin/dynamic-config/{config_id}")
async def delete_dynamic_config(config_id: str, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    existing = await db.dynamic_configs.find_one({"id": config_id}, {"_id": 0})
    await db.dynamic_configs.delete_one({"id": config_id})
    if existing and existing.get("key"):
        # Cascade-delete this module's records so deleting a module doesn't
        # leave orphaned data behind under a now-unreachable module_key.
        await db.dynamic_records.delete_many({"module_key": existing["key"]})
        await db.dynamic_lists.delete_many({"module_key": existing["key"]})
    return {"success": True}

@api_router.get("/public/dynamic-config")
async def public_dynamic_configs():
    return await db.dynamic_configs.find({"enabled":True},{"_id":0}).to_list(1000)


# Publicly-readable, admin-managed images (Settings → Images) for the
# customer site. Unlike the generic /site-images CRUD (which requires an
# authenticated session), this powers pages visitors can see before logging
# in — e.g. the marketing Home page — so it intentionally takes no auth
# dependency and only ever returns Active images.
VALID_SITE_IMAGE_PLACEMENTS = {"home", "dashboard", "projects", "services"}

@api_router.get("/public/site-images")
async def public_site_images(placement: Optional[str] = Query(None)):
    query = {"status": "Active"}
    if placement:
        placement = placement.strip().lower()
        if placement not in VALID_SITE_IMAGE_PLACEMENTS:
            raise HTTPException(status_code=400, detail=f"Placement must be one of: {', '.join(sorted(VALID_SITE_IMAGE_PLACEMENTS))}")
        query["placement"] = placement
    items = await db["erp_site_images"].find(query, {"_id": 0}).sort("created_at", -1).to_list(200)
    return {"success": True, "data": items}


# ---------------- Customer/Home page images (Settings → Images) ----------------
# The Customer/Home page has always shown exactly 3 fixed images (hero,
# analytics, office). This makes those 3 — and ONLY those 3 — editable from
# Admin Settings → Images, using the same upload/validate/store approach as
# the existing site-images feature above. This is intentionally a separate,
# fixed-slot collection (erp_home_images) rather than the generic
# erp_site_images list: the 3 slots are stable identifiers an admin edits in
# place, not an open-ended gallery, and none of this touches the existing
# Featured Images (site-images/placement="home") functionality.
HOME_IMAGE_DEFAULTS = {
    "customer_home_image_1": {
        "label": "Home Page Image 1",
        "image": "https://images.unsplash.com/photo-1517048676732-d65bc937f952?crop=entropy&cs=srgb&fm=jpg&q=85",
    },
    "customer_home_image_2": {
        "label": "Home Page Image 2",
        "image": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?crop=entropy&cs=srgb&fm=jpg&q=85",
    },
    "customer_home_image_3": {
        "label": "Home Page Image 3",
        "image": "https://images.unsplash.com/photo-1560179707-f14e90ef3623?crop=entropy&cs=srgb&fm=jpg&q=85",
    },
}
# Same ~5MB-of-base64 ceiling used by the existing site-images feature, kept
# as its own constant here since this file loads before erp.py is imported.
MAX_HOME_IMAGE_DATA_LEN = 7_000_000

async def _ensure_home_image_defaults() -> None:
    """Lazily create the 3 fixed home-page-image records the first time
    they're needed, seeded with the exact images the Customer/Home page has
    always shown — so enabling this feature causes zero visual change until
    an admin actually edits one."""
    existing_ids = {d["id"] async for d in db["erp_home_images"].find({}, {"_id": 0, "id": 1})}
    missing = [key for key in HOME_IMAGE_DEFAULTS if key not in existing_ids]
    if not missing:
        return
    now = datetime.now(timezone.utc).isoformat()
    docs = [{
        "id": key,
        "label": HOME_IMAGE_DEFAULTS[key]["label"],
        "image": HOME_IMAGE_DEFAULTS[key]["image"],
        "status": "Active",
        "created_at": now,
        "updated_at": now,
    } for key in missing]
    if docs:
        await db["erp_home_images"].insert_many(docs)

@api_router.get("/admin/home-images")
async def list_home_images(user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    await _ensure_home_image_defaults()
    items = await db["erp_home_images"].find({}, {"_id": 0}).sort("id", 1).to_list(10)
    return {"success": True, "data": items}

@api_router.put("/admin/home-images/{image_key}")
async def update_home_image(image_key: str, body: dict = Body(...), user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    if image_key not in HOME_IMAGE_DEFAULTS:
        raise HTTPException(status_code=404, detail="Unknown home page image slot")
    await _ensure_home_image_defaults()
    update = {}
    if "image" in body:
        image = str(body.get("image") or "").strip()
        if not image:
            raise HTTPException(status_code=400, detail="An image (file upload or URL) is required")
        if len(image) > MAX_HOME_IMAGE_DATA_LEN:
            raise HTTPException(status_code=400, detail="Image is too large. Please upload an image under ~5MB.")
        update["image"] = image
    if "status" in body:
        status = str(body.get("status") or "Active").strip() or "Active"
        if status not in ("Active", "Inactive"):
            raise HTTPException(status_code=400, detail="Status must be Active or Inactive")
        update["status"] = status
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db["erp_home_images"].update_one({"id": image_key}, {"$set": update})
    item = await db["erp_home_images"].find_one({"id": image_key}, {"_id": 0})
    return {"success": True, "message": "Home page image updated", "data": item}

@api_router.get("/public/home-images")
async def public_home_images():
    """Unauthenticated — powers the Customer/Home page for logged-out
    visitors. Only returns Active slots; the frontend falls back to its
    built-in default image for any slot that's missing/inactive, so the
    3 existing Home page positions never go blank."""
    await _ensure_home_image_defaults()
    items = await db["erp_home_images"].find({"status": "Active"}, {"_id": 0}).to_list(10)
    return {"success": True, "data": {item["id"]: item["image"] for item in items}}


# ---------------- Generic dynamic-module record CRUD ----------------
# One reusable set of endpoints backs EVERY admin-created dynamic module —
# nothing here is specific to any particular module name. Records for all
# dynamic modules live in a single `dynamic_records` collection, scoped by
# `module_key`, and are always validated against that module's *current*
# saved field configuration before being written.

async def _get_dynamic_module_or_404(module_key: str) -> dict:
    config = await db.dynamic_configs.find_one({"key": module_key}, {"_id": 0})
    if not config:
        raise HTTPException(status_code=404, detail="Module not found")
    return config

@api_router.get("/dynamic-records/{module_key}")
async def list_dynamic_records(module_key: str, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    await _get_dynamic_module_or_404(module_key)
    items = await db.dynamic_records.find({"module_key": module_key}, {"_id": 0}).to_list(5000)
    return _ok(items)

@api_router.post("/dynamic-records/{module_key}")
async def create_dynamic_record(module_key: str, body: dict = Body(...), user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    config = await _get_dynamic_module_or_404(module_key)
    if config.get("enabled") is False:
        raise HTTPException(status_code=403, detail="This module is currently disabled")
    data = _validate_dynamic_record(config.get("fields") or [], body)
    # Board support: every card lives in a list. If the client didn't say
    # which one (or it no longer exists) fall back to the module's default list.
    lists = await _ensure_board(module_key)
    list_id = body.get("list_id")
    if not any(l["id"] == list_id for l in lists):
        list_id = lists[0]["id"]
    last = await db.dynamic_records.find({"module_key": module_key, "list_id": list_id}, {"_id": 0, "position": 1}).to_list(5000)
    position = max([int(r.get("position") or 0) for r in last], default=-1) + 1
    now = datetime.now(timezone.utc).isoformat()
    doc = {"id": f"REC-{uuid.uuid4().hex[:8].upper()}", "module_key": module_key, **data,
           "list_id": list_id, "position": position, "created_at": now, "updated_at": now}
    await db.dynamic_records.insert_one({**doc}); doc.pop("_id", None)
    return _ok(doc, message="Record created")

@api_router.put("/dynamic-records/{module_key}/{record_id}")
async def update_dynamic_record(module_key: str, record_id: str, body: dict = Body(...), user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    config = await _get_dynamic_module_or_404(module_key)
    existing = await db.dynamic_records.find_one({"id": record_id, "module_key": module_key}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Record not found")
    data = _validate_dynamic_record(config.get("fields") or [], body)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.dynamic_records.update_one({"id": record_id, "module_key": module_key}, {"$set": data})
    item = await db.dynamic_records.find_one({"id": record_id, "module_key": module_key}, {"_id": 0})
    return _ok(item, message="Record updated")

@api_router.delete("/dynamic-records/{module_key}/{record_id}")
async def delete_dynamic_record(module_key: str, record_id: str, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")
    res = await db.dynamic_records.delete_one({"id": record_id, "module_key": module_key})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Record not found")
    return _ok({"id": record_id}, message="Record deleted")



# ---------------- Dynamic module BOARD (Trello/Kanban-style) ----------------
# MODULE = BOARD, LIST = column, RECORD = card, FIELD = card data.
# Lists live in `dynamic_lists` (scoped by module_key). Cards are the existing
# `dynamic_records`; they gain two plain attributes: `list_id` and `position`.
# Nothing here replaces the existing field system or record CRUD.
DEFAULT_LIST_NAME = "General"


def _require_admin(user: dict):
    if user.get("role") not in ("admin", "super_admin", "superadmin"):
        raise HTTPException(403, "Admin access required")


async def _ensure_board(module_key: str) -> List[dict]:
    """Return the module's lists (ordered). Safely migrates legacy modules:
    creates a default list when none exist and assigns any record that has no
    (or a dangling) list_id to the first list. Never deletes data."""
    lists = await db.dynamic_lists.find({"module_key": module_key}, {"_id": 0}).to_list(1000)
    lists.sort(key=lambda l: (int(l.get("position") or 0), l.get("created_at") or ""))
    now = datetime.now(timezone.utc).isoformat()
    if not lists:
        default = {"id": f"LST-{uuid.uuid4().hex[:8].upper()}", "module_key": module_key,
                   "name": DEFAULT_LIST_NAME, "description": "", "position": 0,
                   "created_at": now, "updated_at": now}
        await db.dynamic_lists.insert_one({**default}); default.pop("_id", None)
        lists = [default]
    valid_ids = [l["id"] for l in lists]
    orphans = await db.dynamic_records.find(
        {"module_key": module_key, "$or": [{"list_id": {"$exists": False}}, {"list_id": None}, {"list_id": {"$nin": valid_ids}}]},
        {"_id": 0, "id": 1, "created_at": 1}).to_list(5000)
    if orphans:
        orphans.sort(key=lambda r: r.get("created_at") or "")
        base = len(await db.dynamic_records.find({"module_key": module_key, "list_id": valid_ids[0]}, {"_id": 0, "id": 1}).to_list(5000))
        for i, r in enumerate(orphans):
            await db.dynamic_records.update_one({"id": r["id"], "module_key": module_key},
                                                {"$set": {"list_id": valid_ids[0], "position": base + i}})
    return lists


class DynamicListModel(BaseModel):
    name: str
    description: Optional[str] = ""


class DynamicMoveCardModel(BaseModel):
    list_id: str
    position: Optional[int] = None   # index inside the destination list (None = bottom)


class DynamicReorderListsModel(BaseModel):
    list_ids: List[str]


async def _board_payload(module_key: str) -> dict:
    lists = await _ensure_board(module_key)
    cards = await db.dynamic_records.find({"module_key": module_key}, {"_id": 0}).to_list(5000)
    cards.sort(key=lambda r: (int(r.get("position") or 0), r.get("created_at") or ""))
    return {"lists": lists, "cards": cards}


@api_router.get("/dynamic-board/{module_key}")
async def get_dynamic_board(module_key: str, user=Depends(get_current_user)):
    _require_admin(user)
    await _get_dynamic_module_or_404(module_key)
    return _ok(await _board_payload(module_key))


@api_router.post("/dynamic-board/{module_key}/lists")
async def create_dynamic_list(module_key: str, payload: DynamicListModel, user=Depends(get_current_user)):
    _require_admin(user)
    await _get_dynamic_module_or_404(module_key)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "List name is required")
    lists = await _ensure_board(module_key)
    if any((l.get("name") or "").strip().lower() == name.lower() for l in lists):
        raise HTTPException(409, f"A list named '{name}' already exists")
    now = datetime.now(timezone.utc).isoformat()
    doc = {"id": f"LST-{uuid.uuid4().hex[:8].upper()}", "module_key": module_key, "name": name,
           "description": (payload.description or "").strip(),
           "position": max([int(l.get("position") or 0) for l in lists], default=-1) + 1,
           "created_at": now, "updated_at": now}
    await db.dynamic_lists.insert_one({**doc}); doc.pop("_id", None)
    return _ok(doc, message="List created")


@api_router.put("/dynamic-board/{module_key}/lists/{list_id}")
async def update_dynamic_list(module_key: str, list_id: str, payload: DynamicListModel, user=Depends(get_current_user)):
    _require_admin(user)
    await _get_dynamic_module_or_404(module_key)
    lists = await _ensure_board(module_key)
    if not any(l["id"] == list_id for l in lists):
        raise HTTPException(404, "List not found")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "List name is required")
    if any(l["id"] != list_id and (l.get("name") or "").strip().lower() == name.lower() for l in lists):
        raise HTTPException(409, f"A list named '{name}' already exists")
    patch = {"name": name, "description": (payload.description or "").strip(),
             "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.dynamic_lists.update_one({"id": list_id, "module_key": module_key}, {"$set": patch})
    item = await db.dynamic_lists.find_one({"id": list_id, "module_key": module_key}, {"_id": 0})
    return _ok(item, message="List updated")


@api_router.delete("/dynamic-board/{module_key}/lists/{list_id}")
async def delete_dynamic_list(module_key: str, list_id: str, move_to: Optional[str] = Query(None),
                              user=Depends(get_current_user)):
    """Deleting a list never silently deletes cards. If the list has cards the
    caller must say where they go (?move_to=<other list id>); otherwise 409."""
    _require_admin(user)
    await _get_dynamic_module_or_404(module_key)
    lists = await _ensure_board(module_key)
    if not any(l["id"] == list_id for l in lists):
        raise HTTPException(404, "List not found")
    if len(lists) <= 1:
        raise HTTPException(400, "A board needs at least one list")
    cards = await db.dynamic_records.find({"module_key": module_key, "list_id": list_id}, {"_id": 0, "id": 1, "position": 1}).to_list(5000)
    if cards:
        if not move_to or move_to == list_id or not any(l["id"] == move_to for l in lists):
            raise HTTPException(409, f"This list contains {len(cards)} card(s). Choose a list to move them to before deleting.")
        dest = await db.dynamic_records.find({"module_key": module_key, "list_id": move_to}, {"_id": 0, "position": 1}).to_list(5000)
        base = max([int(r.get("position") or 0) for r in dest], default=-1) + 1
        cards.sort(key=lambda r: int(r.get("position") or 0))
        for i, c in enumerate(cards):
            await db.dynamic_records.update_one({"id": c["id"], "module_key": module_key},
                                                {"$set": {"list_id": move_to, "position": base + i}})
    await db.dynamic_lists.delete_one({"id": list_id, "module_key": module_key})
    return _ok({"id": list_id, "moved_cards": len(cards)}, message="List deleted")


@api_router.put("/dynamic-board/{module_key}/lists-order")
async def reorder_dynamic_lists(module_key: str, payload: DynamicReorderListsModel, user=Depends(get_current_user)):
    _require_admin(user)
    await _get_dynamic_module_or_404(module_key)
    lists = await _ensure_board(module_key)
    existing = [l["id"] for l in lists]
    wanted = [i for i in payload.list_ids if i in existing]
    # any list the client forgot keeps its relative order at the end
    ordered = wanted + [i for i in existing if i not in wanted]
    for pos, lid in enumerate(ordered):
        await db.dynamic_lists.update_one({"id": lid, "module_key": module_key}, {"$set": {"position": pos}})
    return _ok(await _board_payload(module_key), message="Lists reordered")


@api_router.put("/dynamic-board/{module_key}/cards/{record_id}/move")
async def move_dynamic_card(module_key: str, record_id: str, payload: DynamicMoveCardModel, user=Depends(get_current_user)):
    """Move a card to a list (and optionally to an index inside it). Persists
    list_id + position for the moved card and renumbers both affected lists."""
    _require_admin(user)
    await _get_dynamic_module_or_404(module_key)
    lists = await _ensure_board(module_key)
    if not any(l["id"] == payload.list_id for l in lists):
        raise HTTPException(404, "Destination list not found")
    card = await db.dynamic_records.find_one({"id": record_id, "module_key": module_key}, {"_id": 0})
    if not card:
        raise HTTPException(404, "Card not found")
    src_id = card.get("list_id")

    async def _ordered(list_id):
        rows = await db.dynamic_records.find({"module_key": module_key, "list_id": list_id}, {"_id": 0, "id": 1, "position": 1, "created_at": 1}).to_list(5000)
        rows.sort(key=lambda r: (int(r.get("position") or 0), r.get("created_at") or ""))
        return [r["id"] for r in rows]

    dest_ids = [i for i in await _ordered(payload.list_id) if i != record_id]
    idx = len(dest_ids) if payload.position is None else max(0, min(int(payload.position), len(dest_ids)))
    dest_ids.insert(idx, record_id)
    now = datetime.now(timezone.utc).isoformat()
    for pos, rid in enumerate(dest_ids):
        patch = {"position": pos}
        if rid == record_id:
            patch.update({"list_id": payload.list_id, "updated_at": now})
        await db.dynamic_records.update_one({"id": rid, "module_key": module_key}, {"$set": patch})
    if src_id and src_id != payload.list_id:
        for pos, rid in enumerate(await _ordered(src_id)):
            await db.dynamic_records.update_one({"id": rid, "module_key": module_key}, {"$set": {"position": pos}})
    item = await db.dynamic_records.find_one({"id": record_id, "module_key": module_key}, {"_id": 0})
    return _ok(item, message="Card moved")


# ---------------- helpers ----------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_token(user_id: str, role: str, token_type: str = "access", session_id: str = None) -> str:
    delta = timedelta(minutes=60) if token_type == "access" else timedelta(days=7)
    payload = {
        "sub": user_id,
        "role": role,
        "type": token_type,
        "exp": datetime.now(timezone.utc) + delta,
        "iat": datetime.now(timezone.utc),
    }
    if session_id:
        # Ties this otherwise-stateless JWT to a backend-managed
        # UserSession record (see security.py) so Active Sessions /
        # session termination can actually take effect on future requests.
        payload["sid"] = session_id
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def public_user(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "name": doc["name"],
        "email": doc.get("email"),
        "mobile": doc.get("mobile"),
        "role": doc["role"],
        "avatar": doc.get("avatar"),
        "meta": doc.get("meta", {}),
    }


def is_valid_indian_mobile(mobile: str) -> bool:
    m = mobile.replace("+91", "").replace(" ", "").strip()
    return len(m) == 10 and m[0] in "6789" and m.isdigit()


def normalize_mobile(mobile: str) -> str:
    return mobile.replace("+91", "").replace(" ", "").strip()


def to_e164_india(mobile: str) -> str:
    """Convert a normalized 10-digit Indian mobile number to E.164, e.g.
    9014564558 -> +919014564558. Assumes is_valid_indian_mobile() already
    passed for this value."""
    return f"+91{mobile}"


# ---------------- schemas ----------------
class AdminLogin(BaseModel):
    email: EmailStr
    password: str


class PortalEmailLogin(BaseModel):
    email: EmailStr
    password: str
    role: str


class SendOtp(BaseModel):
    mobile: str
    role: str


class VerifyOtp(BaseModel):
    mobile: str
    otp: str
    role: str


class RefreshReq(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    role: str
    mobile: Optional[str] = None
    gstin: Optional[str] = None


# Roles that the public /api/auth/register endpoint is allowed to create.
# "admin" (Super Admin) must never be reachable through public
# registration — it is intentionally left out of this set.
PUBLIC_REGISTRATION_ROLES = {"employee", "customer", "agent"}


# ---------------- auth endpoints ----------------
async def auth_response(user: dict, *, method: str, request: Request = None):
    """Builds the standard login response AND, as a side effect, creates a
    backend-managed UserSession + LOGIN_SUCCESS LoginAudit record for the
    Security module (see security.py). This runs for every successful
    login across all four portals and both authentication methods."""
    session = await security.create_user_session(db, user=user, method=method, request=request)
    sid = session.get("session_id")
    access_token = create_token(user["id"], user["role"], "access", session_id=sid)
    refresh_token = create_token(user["id"], user["role"], "refresh", session_id=sid)
    await security.record_login_audit(
        db, mask_mobile=mask_mobile, event_type="LOGIN_SUCCESS", status="SUCCESS", method=method,
        user=user, role=user.get("role"),
        mobile=user.get("mobile") if method == "MOBILE_OTP" else None,
        email=user.get("email") if method == "EMAIL_PASSWORD" else None,
        request=request, session_id=sid,
    )
    if user.get("role") == "customer":
        from erp import notify_customer_login
        await notify_customer_login(db, user, session_id=sid)
    return {
        "success": True,
        "message": "Login successful",
        "data": {
            "user": public_user(user),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@api_router.post("/auth/register")
async def register(body: RegisterRequest, request: Request = None):
    """
    Real Email + Password account creation for the Employee / Customer /
    Agent portals.

    This is NOT a second auth system: it writes into the same `users`
    collection as every other login path, hashes passwords with the same
    bcrypt helper (hash_password/verify_password) used elsewhere in this
    file, and a successfully registered account authenticates through the
    existing /api/auth/portal/login endpoint (see the `meta.registered`
    branch there) and the existing JWT issuance in auth_response().

    Super Admin accounts can never be created here — see
    PUBLIC_REGISTRATION_ROLES.
    """
    full_name = str(body.full_name or "").strip()
    if not full_name:
        raise HTTPException(status_code=422, detail="Full name is required.")

    # EmailStr already guarantees a structurally valid address; also
    # trim + lowercase it, same normalization used by every other login
    # endpoint in this file.
    email = str(body.email).strip().lower()

    password = str(body.password or "")
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters long.")

    role = str(body.role or "").strip().lower()
    if role not in PUBLIC_REGISTRATION_ROLES:
        raise HTTPException(
            status_code=422,
            detail="Invalid role. Allowed roles for registration: employee, customer, agent.",
        )

    mobile = None
    if body.mobile and str(body.mobile).strip():
        mobile = normalize_mobile(str(body.mobile))
        if not is_valid_indian_mobile(mobile):
            raise HTTPException(
                status_code=422,
                detail="Enter a valid 10-digit Indian mobile number starting with 6-9.",
            )

    gstin = str(body.gstin or "").strip().upper()
    if gstin and (len(gstin) != 15 or not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gstin)):
        raise HTTPException(status_code=422, detail="Enter a valid 15-character GSTIN.")

    # A real account can register once per email + role. Existing accounts
    # are upgraded only when they have not yet been given a real password.
    existing = await db.users.find_one({"email": email, "role": role}, {"_id": 0})
    if existing and (existing.get("meta") or {}).get("registered"):
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Please login instead.",
        )

    password_hash = hash_password(password)
    now = datetime.now(timezone.utc)

    try:
        if existing:
            # Upgrade an account created by a verified onboarding flow instead
            # of creating a second, disconnected user record.
            update = {
                "name": full_name,
                "password_hash": password_hash,
                "mobile": mobile or existing.get("mobile"),
                "status": "active",
                "updated_at": now,
                "meta.registered": True,
                "meta.fixed_login": False,
            }
            await db.users.update_one({"id": existing["id"]}, {"$set": update})
            user = await db.users.find_one({"id": existing["id"]}, {"_id": 0})
        else:
            user = {
                "id": f"{role.upper()}-{uuid.uuid4().hex[:8].upper()}",
                "name": full_name,
                "email": email,
                "mobile": mobile,
                "role": role,
                "password_hash": password_hash,
                "avatar": None,
                "meta": {"registered": True},
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            await db.users.insert_one(user)
    except PyMongoError:
        # Covers a rare race: two requests registering the same email+role
        # concurrently both pass the find_one() check above. The partial
        # unique index on (email, role) for registered accounts (created
        # at startup — see create_security_indexes-style setup below)
        # rejects the second insert/update.
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Please login instead.",
        )

    if role == "customer":
        # Keep registration attached to exactly one canonical ERP customer.
        # This is important when an imported/legacy user is upgraded instead of
        # creating a second customer record for the same login.
        customer_id = (user.get("meta") or {}).get("customer_id")
        customer = None
        if customer_id:
            customer = await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            clauses = [{"email": email}]
            if mobile:
                clauses.append({"mobile": mobile})
            customer = await db["erp_customers"].find_one({"$or": clauses}, {"_id": 0})
        if customer:
            customer_id = customer["id"]
            customer_update = {"business_name": customer.get("business_name") or full_name, "owner": full_name, "email": email, "mobile": mobile or customer.get("mobile")}
            if gstin:
                customer_update["gstin"] = gstin
                customer_update["gst_number"] = gstin
            await db["erp_customers"].update_one({"id": customer_id}, {"$set": customer_update})
        else:
            customer_id = f"CUS-{uuid.uuid4().hex[:8].upper()}"
            await db["erp_customers"].insert_one({
                "id": customer_id, "cust_id": customer_id, "business_name": full_name,
                "owner": full_name, "email": email, "mobile": mobile,
                "gstin": gstin or None, "gst_number": gstin or None,
                "status": "Active", "onboarding_status": "Pending",
                "filing_status": "Pending", "notes": "Self-registered customer",
            })
            from erp import notify_customer_registered
            await notify_customer_registered(db, {
                "id": customer_id, "business_name": full_name, "owner": full_name,
                "email": email, "mobile": mobile,
            })
        await db.users.update_one({"id": user["id"]}, {"$set": {"meta.customer_id": customer_id}})
        user.setdefault("meta", {})["customer_id"] = customer_id

    await security.record_login_audit(
        db, mask_mobile=mask_mobile, event_type="REGISTER_SUCCESS", status="SUCCESS",
        method="EMAIL_PASSWORD", user=user, role=role, email=email, request=request,
    )

    # Register the account and log the user straight in, reusing the same
    # JWT/session issuance every other login path uses.
    return await auth_response(user, method="EMAIL_PASSWORD", request=request)


@api_router.post("/auth/admin/login")
async def admin_login(body: AdminLogin, request: Request = None):
    """Authenticate an existing Admin account stored in MongoDB."""
    email = str(body.email).strip().lower()
    user = await db.users.find_one({"email": email, "role": "admin"}, {"_id": 0})
    if not user or not user.get("password_hash") or not verify_password(body.password, user["password_hash"]):
        await security.record_login_audit(db, mask_mobile=mask_mobile, event_type="LOGIN_FAILED", status="FAILED",
            method="EMAIL_PASSWORD", role="admin", email=email, request=request, failure_reason="INVALID_CREDENTIALS")
        raise HTTPException(status_code=401, detail="Invalid admin email or password")
    if user.get("is_blocked"):
        raise HTTPException(status_code=403, detail="This account has been blocked. Please contact support.")
    return await auth_response(user, method="EMAIL_PASSWORD", request=request)


@api_router.post("/auth/portal/login")
async def portal_email_login(body: PortalEmailLogin, request: Request = None):
    """Authenticate an existing Employee/Customer/Agent account from MongoDB."""
    role = str(body.role).strip().lower()
    if role not in PORTAL_ROLES:
        raise HTTPException(status_code=400, detail="Invalid portal role")
    if role == "admin":
        return await admin_login(AdminLogin(email=body.email, password=body.password), request=request)
    email = str(body.email).strip().lower()
    user = await db.users.find_one({"email": email, "role": role}, {"_id": 0})
    if not user or not user.get("password_hash") or not verify_password(body.password, user["password_hash"]):
        await security.record_login_audit(db, mask_mobile=mask_mobile, event_type="LOGIN_FAILED", status="FAILED",
            method="EMAIL_PASSWORD", role=role, email=email, request=request, failure_reason="INVALID_CREDENTIALS")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("is_blocked"):
        raise HTTPException(status_code=403, detail="This account has been blocked. Please contact support.")
    return await auth_response(user, method="EMAIL_PASSWORD", request=request)


@api_router.post("/auth/mobile/send-otp")
async def send_otp(body: SendOtp, request: Request = None):
    """
    Mobile login OTP — sends a real SMS OTP via Twilio Verify.

    The OTP itself is generated and managed entirely by Twilio; it is
    never generated by, stored in, or returned by this backend.
    """

    mobile = normalize_mobile(body.mobile)
    role = str(body.role).strip().lower()

    allowed_roles = {
        "admin",
        "employee",
        "customer",
        "agent",
    }

    if role not in allowed_roles:
        raise HTTPException(
            status_code=400,
            detail="Invalid portal role"
        )

    if not is_valid_indian_mobile(mobile):
        await security.record_login_audit(
            db, mask_mobile=mask_mobile, event_type="OTP_SEND_FAILED", status="FAILED",
            method="MOBILE_OTP", role=role, mobile=mobile, request=request,
            failure_reason="INVALID_MOBILE",
        )
        raise HTTPException(
            status_code=422,
            detail="Enter a valid 10-digit Indian mobile number starting with 6-9"
        )

    client_ip = security.get_client_ip(request) if request else ""
    if client_ip == "Unknown":
        client_ip = ""  # never pool unidentifiable clients into one shared IP bucket

    # A second request for the same number while the first is still being
    # sent (double click, Enter + click, flaky retry) must NOT trigger a
    # second SMS / burn Twilio quota: share the first request's outcome.
    inflight = otp_send_guard.get_inflight(mobile)
    if inflight is not None:
        return await asyncio.shield(inflight)

    blocked = otp_send_guard.check(mobile, client_ip)
    if blocked:
        retry_after, reason = blocked
        await security.record_login_audit(
            db, mask_mobile=mask_mobile, event_type="OTP_SEND_FAILED", status="FAILED",
            method="MOBILE_OTP", role=role, mobile=mobile, request=request,
            failure_reason="OTP_RATE_LIMITED",
        )
        raise _otp_rate_limit_error(reason, retry_after)

    task = asyncio.ensure_future(_perform_otp_send(mobile, role, client_ip, request))
    otp_send_guard.set_inflight(mobile, task)
    task.add_done_callback(lambda t: otp_send_guard.clear_inflight(mobile, t))
    return await asyncio.shield(task)


def _otp_rate_limit_error(reason: str, retry_after: int) -> HTTPException:
    """429 with a human-readable message plus machine-readable cooldown data."""
    return HTTPException(
        status_code=429,
        detail={
            "message": otp_send_guard.message(reason, retry_after),
            "code": "OTP_RATE_LIMITED",
            "reason": reason,
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


async def _perform_otp_send(mobile: str, role: str, client_ip: str, request: Request):
    """The single place that actually calls Twilio Verify to send an OTP."""
    twilio_client = get_twilio_client()
    e164_mobile = to_e164_india(mobile)

    def _send():
        return twilio_client.verify.v2.services(
            TWILIO_VERIFY_SERVICE_SID
        ).verifications.create(to=e164_mobile, channel="sms")

    try:
        # Twilio's SDK is synchronous; run it in a worker thread so it never
        # blocks the event loop (which also lets duplicate requests be seen
        # and de-duplicated while the first one is still in flight).
        verification = await asyncio.to_thread(_send)
    except TwilioRestException as e:
        logger.warning(f"Twilio send-otp failed for {mask_mobile(mobile)}: code={e.code} status={e.status}")
        await security.record_login_audit(
            db, mask_mobile=mask_mobile, event_type="OTP_SEND_FAILED", status="FAILED",
            method="MOBILE_OTP", role=role, mobile=mobile, request=request,
            failure_reason="TWILIO_SEND_FAILED",
        )
        if e.status == 429 or e.code == 20429:
            # Twilio's own per-number limit was reached. Stop forwarding more
            # requests for a while and tell the user exactly how long to wait.
            otp_send_guard.record_provider_limit(mobile)
            retry_after = otp_send_guard.twilio_lock or 600
            raise _otp_rate_limit_error("provider_limit", retry_after)
        if e.code in (60200, 60203, 60212):
            raise HTTPException(status_code=422, detail="Enter a valid mobile number.")
        raise HTTPException(status_code=502, detail="Unable to send OTP right now. Please try again shortly.")
    except Exception:
        logger.exception(f"Unexpected error sending OTP for {mask_mobile(mobile)}")
        await security.record_login_audit(
            db, mask_mobile=mask_mobile, event_type="OTP_SEND_FAILED", status="FAILED",
            method="MOBILE_OTP", role=role, mobile=mobile, request=request,
            failure_reason="TWILIO_SEND_FAILED",
        )
        raise HTTPException(status_code=502, detail="Unable to send OTP right now. Please try again shortly.")

    if verification.status not in ("pending", "approved"):
        logger.warning(f"Twilio send-otp unexpected status for {mask_mobile(mobile)}: {verification.status}")
        await security.record_login_audit(
            db, mask_mobile=mask_mobile, event_type="OTP_SEND_FAILED", status="FAILED",
            method="MOBILE_OTP", role=role, mobile=mobile, request=request,
            failure_reason="TWILIO_SEND_FAILED",
        )
        raise HTTPException(status_code=502, detail="Unable to send OTP right now. Please try again shortly.")

    # Only a successful send counts toward the cooldown / window caps.
    otp_send_guard.record_success(mobile, client_ip)

    logger.info(f"OTP sent via Twilio Verify to {mask_mobile(mobile)} (role={role})")
    await security.record_login_audit(
        db, mask_mobile=mask_mobile, event_type="OTP_SENT", status="SUCCESS",
        method="MOBILE_OTP", role=role, mobile=mobile, request=request,
    )

    return {
        "success": True,
        "message": f"OTP sent to +91 {mobile}",
        "data": {
            "mobile": mobile,
            "expires_in": 600,
            "cooldown_seconds": otp_send_guard.cooldown,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@api_router.post("/auth/mobile/verify-otp")
async def verify_otp(body: VerifyOtp, request: Request = None):
    """
    Verify the real Twilio Verify SMS OTP, then issue the existing TAXCO
    JWT for the resolved user. Twilio only proves the phone number was
    reached — it never issues, and is never trusted for, TAXCO's own
    role/authorization decisions.
    """

    mobile = normalize_mobile(body.mobile)
    role = str(body.role).strip().lower()
    otp = str(body.otp).strip()

    allowed_roles = {
        "admin",
        "employee",
        "customer",
        "agent",
    }

    if role not in allowed_roles:
        raise HTTPException(
            status_code=400,
            detail="Invalid portal role"
        )

    if not is_valid_indian_mobile(mobile):
        raise HTTPException(
            status_code=422,
            detail="Invalid mobile number"
        )

    if not otp:
        raise HTTPException(status_code=422, detail="Please enter the OTP.")

    twilio_client = get_twilio_client()
    e164_mobile = to_e164_india(mobile)

    try:
        verification_check = twilio_client.verify.v2.services(
            TWILIO_VERIFY_SERVICE_SID
        ).verification_checks.create(to=e164_mobile, code=otp)
    except TwilioRestException as e:
        logger.warning(f"Twilio verify-otp failed for {mask_mobile(mobile)}: code={e.code} status={e.status}")
        # Twilio returns 404 ("not found") once a verification has expired
        # or there is no pending verification for this number — treat that
        # the same as an invalid/expired OTP rather than a server error.
        if e.status == 404 or e.code == 20404:
            await security.record_login_audit(
                db, mask_mobile=mask_mobile, event_type="LOGIN_FAILED", status="FAILED",
                method="MOBILE_OTP", role=role, mobile=mobile, request=request, failure_reason="EXPIRED_OTP",
            )
            raise HTTPException(status_code=401, detail="OTP expired or not requested. Please request a new OTP.")
        if e.status == 429 or e.code == 20429:
            await security.record_login_audit(
                db, mask_mobile=mask_mobile, event_type="LOGIN_FAILED", status="FAILED",
                method="MOBILE_OTP", role=role, mobile=mobile, request=request, failure_reason="TWILIO_VERIFY_FAILED",
            )
            raise HTTPException(status_code=429, detail="Too many attempts. Please request a new OTP and try again.")
        await security.record_login_audit(
            db, mask_mobile=mask_mobile, event_type="LOGIN_FAILED", status="FAILED",
            method="MOBILE_OTP", role=role, mobile=mobile, request=request, failure_reason="TWILIO_VERIFY_FAILED",
        )
        raise HTTPException(status_code=401, detail="Invalid OTP. Please try again.")
    except Exception:
        logger.exception(f"Unexpected error verifying OTP for {mask_mobile(mobile)}")
        raise HTTPException(status_code=502, detail="Unable to verify OTP right now. Please try again shortly.")

    if verification_check.status != "approved":
        logger.info(f"OTP not approved for {mask_mobile(mobile)}: status={verification_check.status}")
        await security.record_login_audit(
            db, mask_mobile=mask_mobile, event_type="LOGIN_FAILED", status="FAILED",
            method="MOBILE_OTP", role=role, mobile=mobile, request=request, failure_reason="INVALID_OTP",
        )
        raise HTTPException(status_code=401, detail="Invalid OTP. Please try again.")

    logger.info(f"OTP approved by Twilio for {mask_mobile(mobile)} (role={role})")

    # Find existing user by mobile + role
    user = await db.users.find_one(
        {
            "mobile": mobile,
            "role": role,
        },
        {
            "_id": 0,
        }
    )

    # SECURITY: Twilio only verifies that this phone number received the
    # SMS — it says nothing about which TAXCO role that phone should have.
    # Never let a caller mint themselves a brand-new Super Admin account
    # just by owning a phone number and claiming role="admin". Admin
    # accounts must already exist (seeded, or created by an existing
    # admin) before they can log in via mobile OTP.
    if not user and role == "admin":
        raise HTTPException(
            status_code=403,
            detail="This mobile number is not registered as a Super Admin.",
        )

    # If user doesn't exist, create a real MongoDB user.
    if not user:
        role_names = {
            "admin": "Super Admin",
            "employee": "Employee",
            "customer": "Customer",
            "agent": "Tax Consultant",
        }

        meta = {
            "portal_login": True,
            "otp_login": True,
        }
        name = role_names.get(role, "NTAXCO User")

        if role == "customer":
            customer_record = await db["erp_customers"].find_one({"mobile": mobile}, {"_id": 0})
            if customer_record:
                name = customer_record.get("business_name") or name
                meta["customer_id"] = customer_record["id"]

        if role == "agent":
            agent_record = await db["erp_agents"].find_one({"mobile": mobile}, {"_id": 0})
            if agent_record:
                name = agent_record.get("name") or name
                meta["agent_id"] = agent_record["id"]

        # If an admin has already created an employee profile with this mobile
        # number, link this login to that real employee record instead of
        # creating a disconnected generic account.
        if role == "employee":
            existing_employee = await db["erp_employees"].find_one({"mobile": mobile}, {"_id": 0})
            if existing_employee:
                name = existing_employee.get("name", name)
                meta.update({
                    "employee_id": existing_employee["id"],
                    "department": existing_employee.get("department"),
                    "designation": existing_employee.get("designation"),
                    "manager": existing_employee.get("manager"),
                })

        user = {
            "id": f"{role.upper()}-{uuid.uuid4().hex[:8].upper()}",
            "name": name,
            "email": None,
            "mobile": mobile,
            "role": role,
            "password_hash": None,
            "avatar": None,
            "meta": meta,
        }

        await db.users.insert_one(user)
    elif role == "employee" and not (user.get("meta") or {}).get("employee_id"):
        # Existing employee login without a linked profile yet — try to link
        # it now in case an admin has since created the matching employee record.
        existing_employee = await db["erp_employees"].find_one({"mobile": mobile}, {"_id": 0})
        if existing_employee:
            update = {
                "name": existing_employee.get("name", user.get("name")),
                "meta.employee_id": existing_employee["id"],
                "meta.department": existing_employee.get("department"),
                "meta.designation": existing_employee.get("designation"),
                "meta.manager": existing_employee.get("manager"),
            }
            await db.users.update_one({"id": user["id"]}, {"$set": update})
            user = await db.users.find_one({"id": user["id"]}, {"_id": 0})

    if user.get("is_blocked"):
        await security.record_login_audit(
            db, mask_mobile=mask_mobile, event_type="LOGIN_FAILED", status="FAILED",
            method="MOBILE_OTP", user=user, role=role, mobile=mobile, request=request,
            failure_reason="ACCOUNT_BLOCKED",
        )
        raise HTTPException(status_code=403, detail="This account has been blocked. Please contact support.")

    return await auth_response(user, method="MOBILE_OTP", request=request)


@api_router.post("/auth/refresh")
async def refresh(body: RefreshReq):
    try:
        payload = jwt.decode(body.refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        sid = payload.get("sid")
        if sid and not await security.is_session_active(db, sid):
            raise HTTPException(status_code=401, detail="Session expired. Please login again.")
        return {
            "success": True,
            "message": "Token refreshed",
            "data": {"access_token": create_token(user["id"], user["role"], "access", session_id=sid)},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"success": True, "message": "OK", "data": public_user(user), "timestamp": datetime.now(timezone.utc).isoformat()}


@api_router.post("/auth/logout")
async def logout(request: Request, user: dict = Depends(get_current_user)):
    session_id = user.get("_session_id")
    if session_id:
        await security.close_session(db, session_id, "LOGGED_OUT")
    await security.record_login_audit(
        db, mask_mobile=mask_mobile, event_type="LOGOUT", status="SUCCESS",
        method=None, user=user, role=user.get("role"), request=request, session_id=session_id,
    )
    return {"success": True, "message": "Logged out successfully", "data": None, "timestamp": datetime.now(timezone.utc).isoformat()}


@api_router.get("/")
async def api_root():
    return {
        "message": "NTAXCO ERP API",
        "status": "running"
    }


@api_router.get("/health")
async def health():
    return {"status": "ok", "service": "ntaxco-erp", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------- seeding ----------------
async def seed_users():
    """Provision only an explicitly configured Admin account on first startup.
    No Employee/Agent/Customer demo users are created automatically.
    """
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if not admin_email or not admin_password:
        logger.info("ADMIN_EMAIL/ADMIN_PASSWORD not configured; no admin account will be auto-created.")
        return
    admin_email = admin_email.strip().lower()
    existing = await db.users.find_one({"email": admin_email, "role": "admin"})
    if existing:
        return
    await db.users.insert_one({
        "id": f"ADMIN-{uuid.uuid4().hex[:8].upper()}",
        "name": "Super Admin", "email": admin_email, "mobile": None, "role": "admin",
        "password_hash": hash_password(admin_password), "avatar": None,
        "meta": {"provisioned_by": "environment"}, "status": "active",
        "created_at": datetime.now(timezone.utc),
    })


@app.on_event("startup")
async def startup():
    # Verify MongoDB is actually reachable BEFORE anything else. If it
    # isn't, fail loudly with a specific, actionable log line instead of
    # letting create_index()/seeding hang (up to serverSelectionTimeoutMS)
    # and then raise a raw pymongo error. On most hosts a startup failure
    # here means the process exits and never binds to its port, so every
    # frontend request gets connection-refused — which is exactly the
    # generic "Network Error" this project is trying to eliminate. This
    # log line is what to check first when that happens.
    try:
        await client.admin.command("ping")
    except PyMongoError as e:
        logger.error(
            "Cannot reach MongoDB at the configured MONGO_URL (%s). The "
            "backend cannot start until this is fixed — every API request "
            "(including login) will otherwise fail with a connection error "
            "on the frontend. Check that MongoDB is running and reachable, "
            "and that MONGO_URL is correct. Original error: %s",
            _mask_mongo_url(mongo_url), e,
        )
        raise
    except Exception:
        # Anything other than a genuine pymongo connectivity error (e.g. a
        # mocked Mongo client in tests that doesn't implement admin
        # commands) — this check is a diagnostic aid only, so don't block
        # startup on it.
        pass
    await ensure_index(db.users, "id", unique=True, name="uniq_user_id")
    # BillBook plan IDs are globally unique; subscriber/payment histories remain append-only.
    await ensure_index(db["billbook_plans"], "id", unique=True, name="uniq_billbook_plan_id")
    await ensure_index(db["billbook_subscriptions"], "order_id", unique=True, sparse=True, name="uniq_billbook_subscription_order")
    await ensure_index(db["billbook_subscriptions"], [("customer_id", 1), ("created_at", -1)], name="billbook_subscriber_history")
    await ensure_index(db["billbook_guest_checkouts"], "id", unique=True, name="uniq_billbook_guest_checkout_id")
    await ensure_index(db["billbook_payment_events"], "provider_event_id", unique=True, sparse=True, name="uniq_billbook_provider_event_id")
    await ensure_index(db["billbook_subscriptions"], [("customer_id", 1), ("subscription_type", 1)], unique=True, name="uniq_billbook_one_trial_per_customer", partial_filter={"subscription_type": "free_trial"})
    # Prevents two real POST /api/auth/register accounts from ever sharing
    # an email+role pair (handles the race where two requests register
    # the same email concurrently). Scoped with partialFilterExpression to
    # ONLY real registered accounts (meta.registered == True) so it can
    # never collide with, or block, the pre-existing demo/OTP
    # placeholder-account flows, which don't set that flag and may
    # already contain duplicate emails across roles.
    await ensure_index(
        db.users,
        [("email", 1), ("role", 1)],
        unique=True,
        partial_filter={"meta.registered": True},
        name="uniq_registered_email_role",
    )
    await seed_users()
    await seed_erp(db)
    await ensure_phase4_relationships(db)
    try:
        # Archive any "service" that is really an app/vendor name (e.g. StriveNest) and re-sync service names.
        from erp import quarantine_non_service_records
        _repair = await quarantine_non_service_records(db)
        if any(_repair.values()):
            logger.warning("Service data repair: %s", _repair)
    except Exception as exc:  # a cleanup problem must never stop the API from booting
        logger.warning("Service data repair skipped: %s", exc)
    try:
        from erp import ensure_status_model
        await ensure_status_model(db)
    except Exception as exc:  # a cleanup problem must never stop the API from booting
        logger.warning("Customer/booking status reconciliation skipped: %s", exc)
    await security.create_security_indexes(db)
    try:
        from erp import ensure_notification_indexes
        await ensure_notification_indexes(db)
    except Exception as exc:  # duplicates already in legacy data must not stop the API from booting
        logger.warning("Notification/booking duplicate-guard indexes not created: %s", exc)
    try:
        await ensure_index(db["erp_tasks"],
            [("task_board", 1), ("status", 1), ("priority", 1), ("employee_id", 1),
             ("customer_id", 1), ("service_id", 1), ("due_date", 1)],
            name="admin_task_filters",
        )
        await ensure_index(db["erp_task_activity"],
            [("task_id", 1), ("created_at", 1)], name="task_activity_history"
        )
        await ensure_index(db["erp_kanban_lists"],
            [("board", 1), ("name_ci", 1)], unique=True, name="kanban_board_name"
        )
        await ensure_index(db["erp_kanban_lists"],
            [("board", 1), ("position", 1)], name="kanban_board_position"
        )
    except Exception as exc:
        logger.warning("Task Board indexes not created: %s", exc)
    logger.info("NTAXCO ERP backend started, users seeded.")




# ---------------- Prime Services: customer bookings + admin configuration ----------------
PRIME_SERVICES = [
    {"id":"gst","service_type":"GST Services","description":"GST registration, return filing, reconciliation and compliance assistance."},
    {"id":"tds","service_type":"TDS Services","description":"TDS return preparation, filing and compliance assistance."},
    {"id":"roc","service_type":"ROC Services","description":"Company and LLP annual filing and applicable ROC compliance support."},
    {"id":"income-tax","service_type":"Income Tax Services","description":"Income tax return preparation, filing assistance and tax compliance."},
    {"id":"payroll","service_type":"Payroll Services","description":"Payroll processing, salary calculations, payslips and applicable compliance."},
]
PRIME_STATUSES = ["Pending", "Under Review", "Contacted", "In Progress", "Completed", "Rejected", "Cancelled"]

def _prime_completion_label(doc):
    value = doc.get("estimated_duration_value")
    unit = doc.get("estimated_duration_unit")
    if value is None or unit not in ("hours", "days", "working_days"):
        return (str(doc.get("customer_completion_label") or "").strip() or "Completion time will be confirmed by the NTAXCO team.")
    unit_label = {"hours": "hour" if float(value) == 1 else "hours", "days": "day" if float(value) == 1 else "days", "working_days": "working day" if float(value) == 1 else "working days"}[unit]
    return str(doc.get("customer_completion_label") or "").strip() or f"Estimated completion: {value} {unit_label}"

def _prime_public(doc):
    result = {k: doc.get(k) for k in ("id", "service_type", "description", "base_price", "pricing_label", "price_mode", "active", "available", "booking_instructions", "updated_at", "estimated_duration_value", "estimated_duration_unit", "customer_completion_label", "completion_estimate_notes")}
    result["customer_completion_label"] = _prime_completion_label(doc)
    return result

async def _ensure_prime_catalog():
    now = datetime.now(timezone.utc).isoformat()
    for item in PRIME_SERVICES:
        await db["erp_prime_services"].update_one(
            {"id": item["id"]},
            {"$setOnInsert": {**item, "base_price": None, "pricing_label": "Get a Quote", "price_mode": "quote", "active": True, "available": True, "working_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"], "opening_time": "09:00", "closing_time": "18:00", "timezone": "Asia/Kolkata", "booking_instructions": "Requests may be submitted online; our team will respond during working hours.", "estimated_duration_value": None, "estimated_duration_unit": None, "customer_completion_label": "", "completion_estimate_notes": "", "created_at": now, "updated_at": now}},
            upsert=True,
        )

async def _require_prime_admin(user):
    if str(user.get("role", "")).lower() != "admin":
        raise HTTPException(status_code=403, detail="Administrator permission is required.")

@api_router.get("/prime-services")
async def prime_services_catalog():
    await _ensure_prime_catalog()
    docs = await db["erp_prime_services"].find({}, {"_id": 0}).sort("id", 1).to_list(20)
    by_id = {d.get("id"): d for d in docs}
    return [_prime_public(by_id[x["id"]]) for x in PRIME_SERVICES if x["id"] in by_id]

@api_router.get("/public/prime-services")
async def public_prime_services():
    await _ensure_prime_catalog()
    docs = await db["erp_prime_services"].find({"active": True, "available": True}, {"_id": 0}).sort("id", 1).to_list(20)
    return [_prime_public(d) for d in docs]

@api_router.get("/prime-services/{service_id}")
async def prime_service_detail(service_id: str):
    await _ensure_prime_catalog()
    doc = await db["erp_prime_services"].find_one({"id": service_id}, {"_id": 0})
    if not doc: raise HTTPException(status_code=404, detail="Prime service not found.")
    return _prime_public(doc)

@api_router.patch("/admin/prime-services/{service_id}")
async def prime_service_update(service_id: str, body: dict = Body(...), user=Depends(get_current_user)):
    await _require_prime_admin(user)
    await _ensure_prime_catalog()
    allowed = {"description", "base_price", "pricing_label", "price_mode", "active", "available", "working_days", "opening_time", "closing_time", "timezone", "booking_instructions", "estimated_duration_value", "estimated_duration_unit", "customer_completion_label", "completion_estimate_notes"}
    changes = {k: v for k, v in body.items() if k in allowed}
    if not changes: raise HTTPException(status_code=422, detail="No supported configuration fields were supplied.")
    if "base_price" in changes and changes["base_price"] is not None:
        try: price = float(changes["base_price"])
        except (TypeError, ValueError): raise HTTPException(status_code=422, detail="Base price must be a valid amount in INR.")
        if price < 0 or round(price, 2) != price: raise HTTPException(status_code=422, detail="Base price must be non-negative with at most two decimal places.")
        changes["base_price"] = price
    if "price_mode" in changes and changes["price_mode"] not in ("fixed", "quote"):
        raise HTTPException(status_code=422, detail="Price mode must be fixed or quote.")
    if "estimated_duration_value" in changes:
        value = changes["estimated_duration_value"]
        if value is None or value == "":
            changes["estimated_duration_value"] = None
        else:
            try: value = float(value)
            except (TypeError, ValueError): raise HTTPException(status_code=422, detail="Estimated duration must be a positive number.")
            if value <= 0 or value > 3650 or round(value, 2) != value: raise HTTPException(status_code=422, detail="Estimated duration must be greater than zero and no more than 3650, with at most two decimal places.")
            changes["estimated_duration_value"] = value
    if "estimated_duration_unit" in changes and changes["estimated_duration_unit"] not in (None, "", "hours", "days", "working_days"):
        raise HTTPException(status_code=422, detail="Duration unit must be hours, days, or working_days.")
    if changes.get("estimated_duration_value") is not None and not changes.get("estimated_duration_unit", "") and not (await db["erp_prime_services"].find_one({"id": service_id}, {"_id": 0}) or {}).get("estimated_duration_unit"):
        raise HTTPException(status_code=422, detail="Select a duration unit for the estimated duration.")
    for key, limit in (("customer_completion_label", 180), ("completion_estimate_notes", 600)):
        if key in changes:
            value = str(changes[key] or "").strip()
            if len(value) > limit: raise HTTPException(status_code=422, detail=f"{key.replace('_', ' ').title()} must be {limit} characters or fewer.")
            changes[key] = value
    if "working_days" in changes and (not isinstance(changes["working_days"], list) or any(d not in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"] for d in changes["working_days"])):
        raise HTTPException(status_code=422, detail="Working days contains an invalid day.")
    for key in ("opening_time", "closing_time"):
        if key in changes and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(changes[key])):
            raise HTTPException(status_code=422, detail=f"{key.replace('_',' ').title()} must use HH:MM format.")
    existing = await db["erp_prime_services"].find_one({"id": service_id}, {"_id": 0})
    if not existing: raise HTTPException(status_code=404, detail="Prime service not found.")
    if "opening_time" in changes or "closing_time" in changes:
        start = changes.get("opening_time", existing.get("opening_time", "09:00")); end = changes.get("closing_time", existing.get("closing_time", "18:00"))
        if start >= end: raise HTTPException(status_code=422, detail="Opening time must be earlier than closing time.")
    now = datetime.now(timezone.utc).isoformat()
    changes.update({"updated_at": now, "updated_by": user.get("id") or user.get("email") or user.get("name") or "admin"})
    await db["erp_prime_services"].update_one({"id": service_id}, {"$set": changes})
    await db["erp_prime_service_audit"].insert_one({"id": str(uuid.uuid4()), "service_id": service_id, "changes": changes, "admin_id": user.get("id"), "admin_name": user.get("name"), "created_at": now})
    return _prime_public(await db["erp_prime_services"].find_one({"id": service_id}, {"_id": 0}))

# Lightweight per-process throttling for the public guest endpoint. In production,
# also enforce rate limits at the reverse proxy/API gateway for multi-worker deployments.
_prime_guest_rate = {}

def _normalize_indian_mobile_number(value):
    """Return a normalized 10-digit Indian mobile number, or None if invalid."""
    raw = str(value or "").strip()
    # Do not silently strip letters or unsupported punctuation from user input.
    if not raw or not re.fullmatch(r"[+0-9\s()-]+", raw):
        return None
    compact = re.sub(r"[\s()-]", "", raw)
    national = compact[3:] if compact.startswith("+91") else compact
    if not re.fullmatch(r"[6-9][0-9]{9}", national):
        return None
    return national


def _normalize_prime_booking(body):
    name = str(body.get("customer_name", "")).strip()
    mobile = _normalize_indian_mobile_number(body.get("mobile_number", ""))
    service_id = str(body.get("service_id", body.get("service_type", ""))).strip()
    gst = str(body.get("gst_number", "") or "").strip().upper()
    if not name:
        raise HTTPException(status_code=422, detail="Customer name is required.")
    if len(name) > 120:
        raise HTTPException(status_code=422, detail="Customer name must be 120 characters or fewer.")
    if not mobile:
        raise HTTPException(status_code=422, detail="Enter a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9.")
    if service_id not in {x["id"] for x in PRIME_SERVICES}:
        raise HTTPException(status_code=422, detail="Select a supported Prime Service.")
    if gst and not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gst):
        raise HTTPException(status_code=422, detail="Enter a valid GSTIN or leave the field empty.")
    return name, mobile, service_id, gst

async def _insert_prime_booking(name, mobile, service_id, gst, customer_id=None, booking_source="guest", email=None, requirements=None, idempotency_key=None):
    await _ensure_prime_catalog()
    service = await db["erp_prime_services"].find_one({"id": service_id}, {"_id": 0})
    if not service or not service.get("active", True) or not service.get("available", True):
        raise HTTPException(status_code=409, detail="This Prime Service is temporarily unavailable. Please contact NTAXCO.")
    now = datetime.now(timezone.utc).isoformat()
    if booking_source == "guest" and idempotency_key:
        prior = await db["erp_prime_service_bookings"].find_one({"idempotency_key": idempotency_key}, {"_id": 0})
        if prior:
            return {k: prior.get(k) for k in ("booking_reference", "customer_name", "mobile_number", "gst_number", "service_id", "service_type", "service_name", "status", "price_mode", "quoted_price", "service_price", "currency", "working_hours", "pricing_label", "estimated_duration_value", "estimated_duration_unit", "customer_completion_label", "completion_estimate_notes", "created_at")}
    reference = "PS-" + uuid.uuid4().hex[:10].upper()
    # Guest customers are separate from authenticated ERP accounts. Reuse a guest
    # Prime Customer only when the normalized mobile number matches.
    prime_customer_id = None
    created_prime_customer = False
    reused_prime_customer = False
    if booking_source == "guest":
        customers = db["erp_prime_customers"]
        await customers.create_index("prime_customer_reference", unique=True)
        await customers.create_index("mobile_number")
        existing_customer = await customers.find_one({"mobile_number": mobile, "source": "public_prime_booking"}, {"_id": 0})
        if existing_customer:
            reused_prime_customer = True
            prime_customer_id = existing_customer["id"]
            await customers.update_one({"id": prime_customer_id}, {"$set": {"customer_name": name[:120], "gst_number": gst or existing_customer.get("gst_number"), "updated_at": now}, "$inc": {"total_bookings": 1}, "$addToSet": {"service_ids": service_id}})
        else:
            prime_customer_id = str(uuid.uuid4())
            created_prime_customer = True
            await customers.insert_one({"id": prime_customer_id, "prime_customer_reference": "PC-" + uuid.uuid4().hex[:10].upper(), "customer_name": name[:120], "mobile_number": mobile, "gst_number": gst or None, "source": "public_prime_booking", "status": "New", "created_at": now, "updated_at": now, "booking_ids": [], "service_ids": [service_id], "total_bookings": 1, "assigned_employee_id": None, "admin_notes": None})
    working_hours = None
    completion_label = _prime_completion_label(service)
    duration_value = service.get("estimated_duration_value")
    duration_unit = service.get("estimated_duration_unit") if duration_value is not None else None
    doc = {
        "id": str(uuid.uuid4()), "booking_reference": reference, "customer_id": customer_id,
        "prime_customer_id": prime_customer_id, "booking_source": booking_source, "customer_name": name[:120], "mobile_number": mobile,
        "email": (email or "")[:254] or None, "gst_number": gst or None, "service_id": service_id,
        "service_type": service["service_type"], "service_name": service["service_type"], "additional_requirements": (requirements or "")[:3000] or None,
        "status": "Pending", "price_mode": service.get("price_mode", "quote"),
        "quoted_price": service.get("base_price") if service.get("price_mode") == "fixed" else None,
        "service_price": service.get("base_price") if service.get("price_mode") == "fixed" else None,
        "currency": "INR", "working_hours": working_hours, "pricing_label": service.get("pricing_label", "Get a Quote"),
        "estimated_duration_value": duration_value, "estimated_duration_unit": duration_unit,
        "customer_completion_label": completion_label, "completion_estimate_notes": str(service.get("completion_estimate_notes") or "")[:600], "admin_notes": None,
        "assigned_employee_id": None, "assignment_history": [], "created_at": now, "updated_at": now, "status_history": [{"status":"Pending", "at":now, "updated_by":"public_booking"}]
    }
    if booking_source == "guest" and idempotency_key:
        doc["idempotency_key"] = idempotency_key
    await db["erp_prime_service_bookings"].create_index("booking_reference", unique=True)
    await db["erp_prime_service_bookings"].create_index("idempotency_key", unique=True, sparse=True)
    await db["erp_prime_service_bookings"].create_index("prime_customer_id")
    await db["erp_prime_service_bookings"].create_index([("mobile_number", 1)])
    try:
        await db["erp_prime_service_bookings"].insert_one(doc)
    except Exception:
        # Recover the customer-side write if the booking insert fails. Existing
        # customers have their booking count rolled back; newly-created guest
        # records are removed so they cannot become orphaned.
        if prime_customer_id and created_prime_customer:
            await db["erp_prime_customers"].delete_one({"id": prime_customer_id})
        elif prime_customer_id and reused_prime_customer:
            await db["erp_prime_customers"].update_one({"id": prime_customer_id}, {"$inc": {"total_bookings": -1}, "$set": {"updated_at": now}})
        raise
    if prime_customer_id:
        await db["erp_prime_customers"].update_one({"id": prime_customer_id}, {"$addToSet": {"booking_ids": doc["id"]}, "$set": {"updated_at": now}})
    logger.info("Prime service booking created: reference=%s source=%s service=%s", reference, booking_source, service_id)
    doc.pop("_id", None)
    if booking_source == "guest":
        # Public confirmation is deliberately allow-listed; internal identifiers,
        # notes, status/assignment histories and authenticated account fields stay private.
        return {k: doc.get(k) for k in ("booking_reference", "customer_name", "mobile_number", "gst_number", "service_id", "service_type", "service_name", "status", "price_mode", "quoted_price", "service_price", "currency", "working_hours", "pricing_label", "estimated_duration_value", "estimated_duration_unit", "customer_completion_label", "completion_estimate_notes", "created_at")}
    doc.pop("admin_notes", None); doc.pop("status_history", None)
    return doc

@api_router.post("/public/prime-service-bookings", status_code=201)
async def create_public_prime_service_booking(body: dict = Body(...), request: Request = None):
    # Per-client best-effort throttling; shared infrastructure should also rate-limit this route.
    now_epoch = datetime.now(timezone.utc).timestamp()
    client_ip = (request.client.host if request and request.client else "unknown")
    prior = [t for t in _prime_guest_rate.get(client_ip, []) if now_epoch - t < 600]
    if len(prior) >= 10:
        raise HTTPException(status_code=429, detail="Too many booking attempts. Please try again in a few minutes.")
    prior.append(now_epoch)
    _prime_guest_rate[client_ip] = prior
    name, mobile, service_id, gst = _normalize_prime_booking(body)
    try:
        idempotency_key = str(body.get("idempotency_key", "")).strip()[:100] or None
        return await _insert_prime_booking(name, mobile, service_id, gst, idempotency_key=idempotency_key)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Public Prime Service booking creation failed")
        raise HTTPException(status_code=503, detail="We could not submit your booking right now. Please try again.")

@api_router.post("/prime-service-bookings", status_code=201)
async def create_prime_service_booking(body: dict = Body(...), user=Depends(get_current_user)):
    name, mobile, service_id, gst = _normalize_prime_booking(body)
    email = str(body.get("email", "")).strip()
    if email and not re.fullmatch(r"[^\\s@]+@[^\\s@]+\\.[^\\s@]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    requirements = str(body.get("additional_requirements", "")).strip()
    return await _insert_prime_booking(name, mobile, service_id, gst, user.get("id"), "authenticated", email, requirements)

@api_router.get("/prime-service-bookings/my")
async def my_prime_service_bookings(user=Depends(get_current_user)):
    items = await db["erp_prime_service_bookings"].find({"customer_id": user.get("id")}, {"_id": 0, "admin_notes": 0}).sort("created_at", -1).to_list(200)
    return items

@api_router.get("/admin/prime-service-bookings")
async def admin_prime_service_bookings(page: int = 1, page_size: int = 20, status: Optional[str] = None, service_type: Optional[str] = None, q: Optional[str] = None, user=Depends(get_current_user)):
    await _require_prime_admin(user)
    page_size = min(max(page_size, 1), 100)
    query = {}
    if status: query["status"] = status
    if service_type: query["service_type"] = service_type
    if q:
        query["$or"] = [{"booking_reference":{"$regex":re.escape(q),"$options":"i"}}, {"customer_name":{"$regex":re.escape(q),"$options":"i"}}, {"mobile_number":{"$regex":re.escape(q),"$options":"i"}}]
    total = await db["erp_prime_service_bookings"].count_documents(query)
    items = await db["erp_prime_service_bookings"].find(query, {"_id":0}).sort("created_at",-1).skip((page-1)*page_size).limit(page_size).to_list(page_size)
    return {"items":items,"total":total,"page":page,"page_size":page_size}

@api_router.patch("/admin/prime-service-bookings/{booking_id}")
async def admin_update_prime_booking(booking_id: str, body: dict = Body(...), user=Depends(get_current_user)):
    await _require_prime_admin(user)
    allowed = {}
    if "status" in body:
        if body["status"] not in PRIME_STATUSES: raise HTTPException(status_code=422, detail="Unsupported booking status.")
        allowed["status"] = body["status"]
    if "admin_notes" in body: allowed["admin_notes"] = str(body["admin_notes"] or "")[:5000]
    if not allowed: raise HTTPException(status_code=422, detail="No supported booking updates supplied.")
    existing = await db["erp_prime_service_bookings"].find_one({"id": booking_id}) or await db["erp_prime_service_bookings"].find_one({"booking_reference": booking_id})
    if not existing: raise HTTPException(status_code=404, detail="Booking not found.")
    now = datetime.now(timezone.utc).isoformat(); allowed["updated_at"] = now
    if "status" in allowed:
        allowed["$dummy"] = None
        history = existing.get("status_history", []) + [{"previous_status": existing.get("status"), "status":allowed["status"],"at":now,"updated_by":user.get("id") or user.get("email"), "remarks": str(body.get("remarks", ""))[:1000] or None}]
        allowed.pop("$dummy", None); allowed["status_history"] = history
        if allowed["status"] == "Completed": allowed["completed_at"] = now
    await db["erp_prime_service_bookings"].update_one({"id":existing["id"]},{"$set":allowed})
    result = await db["erp_prime_service_bookings"].find_one({"id":existing["id"]},{"_id":0})
    return result


@api_router.get("/admin/prime-customers/stats")
async def admin_prime_customer_stats(user=Depends(get_current_user)):
    await _require_prime_admin(user)
    customers = db["erp_prime_customers"]
    bookings = db["erp_prime_service_bookings"]
    return {"total_customers": await customers.count_documents({}), "new_or_unassigned": await customers.count_documents({"$or": [{"status": "New"}, {"assigned_employee_id": None}]}), "active_customers": await customers.count_documents({"status": "Active"}), "completed_bookings": await bookings.count_documents({"status": "Completed"})}

@api_router.get("/admin/prime-customers")
async def admin_prime_customers(page: int = 1, page_size: int = 20, q: Optional[str] = None, status: Optional[str] = None, assigned: Optional[str] = None, service_id: Optional[str] = None, user=Depends(get_current_user)):
    await _require_prime_admin(user)
    page_size = min(max(page_size, 1), 100)
    query = {}
    if status: query["status"] = status
    clauses = []
    if assigned == "yes": clauses.append({"assigned_employee_id": {"$ne": None}})
    elif assigned == "no": clauses.append({"$or": [{"assigned_employee_id": None}, {"assigned_employee_id": {"$exists": False}}]})
    if service_id: query["service_ids"] = service_id
    if q:
        clauses.append({"$or": [{"prime_customer_reference": {"$regex": re.escape(q), "$options": "i"}}, {"customer_name": {"$regex": re.escape(q), "$options": "i"}}, {"mobile_number": {"$regex": re.escape(q), "$options": "i"}}]})
    if clauses: query["$and"] = clauses
    total = await db["erp_prime_customers"].count_documents(query)
    items = await db["erp_prime_customers"].find(query, {"_id": 0}).sort("updated_at", -1).skip((page-1)*page_size).limit(page_size).to_list(page_size)
    for item in items:
        item["bookings"] = await db["erp_prime_service_bookings"].count_documents({"prime_customer_id": item.get("id")})
        latest = await db["erp_prime_service_bookings"].find_one({"prime_customer_id": item.get("id")}, {"_id": 0, "created_at": 1}, sort=[("created_at", -1)])
        item["latest_booking_at"] = latest.get("created_at") if latest else item.get("created_at")
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@api_router.get("/admin/prime-customers/{prime_customer_id}/bookings")
async def admin_prime_customer_bookings(prime_customer_id: str, user=Depends(get_current_user)):
    await _require_prime_admin(user)
    customer = await db["erp_prime_customers"].find_one({"id": prime_customer_id}, {"_id": 0})
    if not customer: raise HTTPException(status_code=404, detail="Prime Customer not found.")
    return await db["erp_prime_service_bookings"].find({"prime_customer_id": prime_customer_id}, {"_id": 0}).sort("created_at", -1).to_list(500)

@api_router.get("/admin/prime-customers/{prime_customer_id}")
async def admin_prime_customer_detail(prime_customer_id: str, user=Depends(get_current_user)):
    await _require_prime_admin(user)
    customer = await db["erp_prime_customers"].find_one({"id": prime_customer_id}, {"_id": 0})
    if not customer: raise HTTPException(status_code=404, detail="Prime Customer not found.")
    customer["bookings"] = await db["erp_prime_service_bookings"].find({"prime_customer_id": prime_customer_id}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return customer

@api_router.patch("/admin/prime-customers/{prime_customer_id}")
async def admin_update_prime_customer(prime_customer_id: str, body: dict = Body(...), user=Depends(get_current_user)):
    await _require_prime_admin(user)
    allowed = {}
    if "status" in body:
        if body["status"] not in ["New", "Active", "Inactive"]: raise HTTPException(status_code=422, detail="Unsupported Prime Customer status.")
        allowed["status"] = body["status"]
    if "admin_notes" in body: allowed["admin_notes"] = str(body["admin_notes"] or "")[:5000]
    if not allowed: raise HTTPException(status_code=422, detail="No supported customer updates supplied.")
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db["erp_prime_customers"].update_one({"id": prime_customer_id}, {"$set": allowed})
    if not result.matched_count: raise HTTPException(status_code=404, detail="Prime Customer not found.")
    return await db["erp_prime_customers"].find_one({"id": prime_customer_id}, {"_id": 0})

@api_router.post("/admin/prime-service-bookings/{booking_id}/assign")
async def admin_assign_prime_booking(booking_id: str, body: dict = Body(...), user=Depends(get_current_user)):
    await _require_prime_admin(user)
    employee_id = str(body.get("employee_id", "")).strip()
    if not employee_id: raise HTTPException(status_code=422, detail="Select an employee.")
    employee = await db["erp_employees"].find_one({"$or": [{"id": employee_id}, {"employee_id": employee_id}]}, {"_id": 0})
    if not employee: raise HTTPException(status_code=404, detail="Employee not found.")
    if employee.get("active") is False or str(employee.get("status", "active")).lower() in ("inactive", "disabled", "terminated"): raise HTTPException(status_code=422, detail="Only active employees can be assigned.")
    booking = await db["erp_prime_service_bookings"].find_one({"$or": [{"id": booking_id}, {"booking_reference": booking_id}]}, {"_id": 0})
    if not booking: raise HTTPException(status_code=404, detail="Booking not found.")
    now = datetime.now(timezone.utc).isoformat()
    employee_name = employee.get("name") or employee.get("full_name") or employee.get("employee_name") or employee_id
    history = booking.get("assignment_history", []) + [{"previous_employee_id": booking.get("assigned_employee_id"), "employee_id": employee_id, "employee_name": employee_name, "assigned_by": user.get("id") or user.get("email"), "assigned_at": now, "notes": str(body.get("notes", ""))[:2000]}]
    await db["erp_prime_service_bookings"].update_one({"id": booking["id"]}, {"$set": {"assigned_employee_id": employee_id, "assigned_employee_name": employee_name, "assignment_history": history, "updated_at": now}})
    return await db["erp_prime_service_bookings"].find_one({"id": booking["id"]}, {"_id": 0})


_web_ledger_authorized = web_ledger_authorized

async def _require_web_ledger(user: dict):
    if not _web_ledger_authorized(user):
        raise HTTPException(status_code=403, detail="Web Ledger is restricted to authorized Admin accounts. Contact your NTAXCO administrator.")


@api_router.get("/web-ledger/access")
async def web_ledger_access(user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    return {"success": True, "data": {"allowed": True}}


@api_router.get("/web-ledger/customers")
async def web_ledger_customers(q: str = "", classification: str = "all", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    # Archived records are excluded by default but remain discoverable for restore.
    query = {"archived": True} if classification == "Archived" else {"$or": [{"archived": {"$exists": False}}, {"archived": False}]}
    term = q.strip()
    if term:
        query.setdefault("$and", []).append({"$or": [{"name": {"$regex": re.escape(term), "$options": "i"}}, {"business_name": {"$regex": re.escape(term), "$options": "i"}}, {"mobile": {"$regex": re.escape(term), "$options": "i"}}, {"gstin": {"$regex": re.escape(term), "$options": "i"}}, {"gst_number": {"$regex": re.escape(term), "$options": "i"}}]})
    if classification in ("GST", "TDS", "Income Tax"):
        query.setdefault("$and", []).append({"$or": [{"tax_classifications": classification}, {"service_type": {"$regex": "^" + re.escape(classification) + "$", "$options": "i"}}]})
    elif classification == "Prime":
        query["$and"] = query.get("$and", []) + [{"$or": [{"is_prime": True}, {"customer_type": {"$regex": "^Prime$", "$options": "i"}}]}]
    elif classification in ("Paid", "Pending", "Processing", "Completed"):
        query["$and"] = query.get("$and", []) + [{"$or": [{"service_status": {"$regex": "^" + classification + "$", "$options": "i"}}, {"status": {"$regex": "^" + classification + "$", "$options": "i"}}]}]
    total = await db["erp_customers"].count_documents(query)
    rows = await db["erp_customers"].find(query, {"_id": 0}).sort("updated_at", -1).skip((page-1)*page_size).limit(page_size).to_list(page_size)
    all_active = await db["erp_customers"].find({"$or": [{"archived": {"$exists": False}}, {"archived": False}]}, {"_id": 0, "tax_classifications": 1, "service_type": 1, "is_prime": 1, "filing_status": 1, "status": 1}).to_list(10000)
    stats = {"total": len(all_active), "gst": 0, "tds": 0, "income_tax": 0, "prime": 0, "pending": 0, "processing": 0, "completed": 0, "paid": 0}
    for c in all_active:
        classes = c.get("tax_classifications") or ([c.get("service_type")] if c.get("service_type") else [])
        norm = {str(x).lower().replace(" ", "") for x in classes}
        stats["gst"] += int("gst" in norm or bool(c.get("gstin") or c.get("gst_number")))
        stats["tds"] += int("tds" in norm)
        stats["income_tax"] += int("incometax" in norm or "income-tax" in norm)
        stats["prime"] += int(bool(c.get("is_prime") or str(c.get("customer_type", "")).lower() == "prime"))
        # Workflow summaries use service_status only; account status and filing_status are not payment signals.
        status = str(c.get("service_status") or "").strip().lower()
        if status in ("completed", "complete", "delivered"): stats["completed"] += 1
        elif status in ("processing", "in progress", "running"): stats["processing"] += 1
        elif status in ("pending", "new", "under review"): stats["pending"] += 1
    # Reconcile only persisted, non-draft invoices and persisted receipt rows.
    invoices = await db["web_ledger_invoices"].find({"scope": "ntaxco_internal", "status": {"$nin": ["draft", "cancelled", "void"]}}, {"_id": 0}).to_list(length=100000)
    receipts = await db["web_ledger_payments"].find({"scope": "ntaxco_internal"}, {"_id": 0, "invoice_id": 1, "amount": 1}).to_list(length=100000)
    receipt_by_invoice = {}
    for receipt in receipts:
        receipt_by_invoice[str(receipt.get("invoice_id"))] = receipt_by_invoice.get(str(receipt.get("invoice_id")), 0.0) + float(receipt.get("amount") or 0)
    for customer in rows:
        names = {str(customer.get("name") or "").strip().casefold(), str(customer.get("business_name") or "").strip().casefold()}
        related = [inv for inv in invoices if (inv.get("customer_id") and str(inv.get("customer_id")) == str(customer.get("id"))) or (not inv.get("customer_id") and str(inv.get("customer_name") or "").strip().casefold() in names - {""})]
        customer.update(_web_ledger_financial_totals(related, receipt_by_invoice))
    return {"success": True, "data": {"items": rows, "total": total, "page": page, "page_size": page_size, "stats": stats}}

@api_router.post("/web-ledger/customers")
async def web_ledger_create_customer(body: dict = Body(...), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    name = str(body.get("name", "")).strip()
    mobile = re.sub(r"[\s()-]", "", str(body.get("mobile", "")))
    if mobile.startswith("+91"): mobile = mobile[3:]
    elif mobile.startswith("91") and len(mobile) == 12: mobile = mobile[2:]
    if not name: raise HTTPException(status_code=422, detail="Customer name is required.")
    if not re.fullmatch(r"[6-9]\d{9}", mobile): raise HTTPException(status_code=422, detail="Enter a valid Indian mobile number.")
    email = str(body.get("email", "")).strip().lower()
    if email and (len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)): raise HTTPException(status_code=422, detail="Enter a valid email address.")
    gstin = str(body.get("gstin", "")).strip().upper()
    if gstin and not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gstin): raise HTTPException(status_code=422, detail="GSTIN format is invalid.")
    duplicate = await db["erp_customers"].find_one({"mobile": {"$in": [mobile, "+91" + mobile, "91" + mobile]}, "archived": {"$ne": True}}, {"_id": 0, "id": 1})
    if duplicate: raise HTTPException(status_code=409, detail="A customer with this mobile number already exists. Review the existing record first.")
    now = datetime.now(timezone.utc).isoformat()
    record = {"id": _ledger_record_id(), "name": name[:200], "mobile": mobile, "email": email, "business_name": str(body.get("business_name", "")).strip()[:200], "customer_type": str(body.get("customer_type", "Individual")), "gstin": gstin, "gst_number": gstin, "pan": str(body.get("pan", "")).strip().upper()[:10], "billing_address": str(body.get("billing_address", ""))[:1000], "shipping_address": str(body.get("shipping_address", ""))[:1000], "state": str(body.get("state", ""))[:100], "pincode": str(body.get("pincode", ""))[:10], "customer_group": str(body.get("customer_group", ""))[:100], "tax_classifications": [x for x in body.get("tax_classifications", []) if x in ("GST", "TDS", "Income Tax")], "is_prime": bool(body.get("is_prime", False)), "notes": str(body.get("notes", ""))[:2000], "status": "Pending", "filing_status": "Pending", "archived": False, "created_at": now, "updated_at": now, "created_by": str(user.get("id") or user.get("email") or "")}
    await db["erp_customers"].insert_one(record)
    record.pop("_id", None)
    return {"success": True, "data": record}

@api_router.patch("/web-ledger/customers/{customer_id}")
async def web_ledger_update_customer(customer_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    existing = await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0})
    if not existing: raise HTTPException(status_code=404, detail="Customer not found.")
    allowed = {"name", "mobile", "email", "business_name", "customer_type", "gstin", "pan", "billing_address", "shipping_address", "state", "pincode", "customer_group", "tax_classifications", "is_prime", "notes"}
    unsupported = sorted(set(body) - allowed)
    if unsupported:
        raise HTTPException(status_code=422, detail={"message": "One or more fields cannot be edited.", "fields": unsupported})
    update = dict(body)
    if not update:
        raise HTTPException(status_code=422, detail="Provide at least one customer field to update.")
    if "name" in update:
        update["name"] = str(update["name"] or "").strip()
        if not update["name"]: raise HTTPException(status_code=422, detail="Customer name is required.")
        if len(update["name"]) > 200: raise HTTPException(status_code=422, detail="Customer name is too long.")
    if "mobile" in update:
        mobile = re.sub(r"[\s()-]", "", str(update["mobile"] or ""))
        if mobile.startswith("+91"): mobile = mobile[3:]
        elif mobile.startswith("91") and len(mobile) == 12: mobile = mobile[2:]
        if not re.fullmatch(r"[6-9]\d{9}", mobile): raise HTTPException(status_code=422, detail="Enter a valid Indian mobile number.")
        dup = await db["erp_customers"].find_one({"mobile": {"$in": [mobile, "+91" + mobile, "91" + mobile]}, "id": {"$ne": customer_id}, "archived": {"$ne": True}}, {"_id": 0, "id": 1})
        if dup: raise HTTPException(status_code=409, detail="Another customer already uses this mobile number.")
        update["mobile"] = mobile
    if "email" in update:
        email = str(update["email"] or "").strip().lower()
        if email and (len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)):
            raise HTTPException(status_code=422, detail="Enter a valid email address.")
        update["email"] = email
    if "business_name" in update:
        update["business_name"] = str(update["business_name"] or "").strip()[:200]
    if "customer_type" in update:
        value = str(update["customer_type"] or "").strip()
        if value not in {"Individual", "Business", "Prime"}:
            raise HTTPException(status_code=422, detail="Customer type must be Individual, Business, or Prime.")
        update["customer_type"] = value
    if "gstin" in update:
        update["gstin"] = str(update["gstin"] or "").strip().upper()
        update["gst_number"] = update["gstin"]
        if update["gstin"] and not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", update["gstin"]): raise HTTPException(status_code=422, detail="GSTIN format is invalid.")
    if "pan" in update:
        update["pan"] = str(update["pan"] or "").strip().upper()
        if update["pan"] and not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", update["pan"]): raise HTTPException(status_code=422, detail="PAN format is invalid.")
    if "pincode" in update:
        update["pincode"] = str(update["pincode"] or "").strip()
        if update["pincode"] and not re.fullmatch(r"[0-9]{6}", update["pincode"]): raise HTTPException(status_code=422, detail="PIN code must contain six digits.")
    if "tax_classifications" in update:
        if not isinstance(update["tax_classifications"], list) or any(x not in ("GST", "TDS", "Income Tax") for x in update["tax_classifications"]):
            raise HTTPException(status_code=422, detail="Tax classifications contain an unsupported value.")
        update["tax_classifications"] = list(dict.fromkeys(update["tax_classifications"]))
    for field, limit in (("billing_address", 1000), ("shipping_address", 1000), ("state", 100), ("customer_group", 100), ("notes", 2000)):
        if field in update:
            update[field] = str(update[field] or "").strip()[:limit]
    if "is_prime" in update and not isinstance(update["is_prime"], bool):
        raise HTTPException(status_code=422, detail="Prime classification must be true or false.")
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db["erp_customers"].update_one({"id": customer_id}, {"$set": update})
    result = await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0})
    return {"success": True, "data": result}



@api_router.get("/web-ledger/customers/export")
async def web_ledger_export_customers(q: str = "", classification: str = "all", user: dict = Depends(get_current_user)):
    """Return the full filtered customer result set for an authorized export."""
    await _require_web_ledger(user)
    query = {"$or": [{"archived": {"$exists": False}}, {"archived": False}]}
    term = q.strip()
    clauses = []
    if term:
        clauses.append({"$or": [{"name": {"$regex": re.escape(term), "$options": "i"}}, {"business_name": {"$regex": re.escape(term), "$options": "i"}}, {"mobile": {"$regex": re.escape(term), "$options": "i"}}, {"gstin": {"$regex": re.escape(term), "$options": "i"}}, {"gst_number": {"$regex": re.escape(term), "$options": "i"}}]})
    if classification in ("GST", "TDS", "Income Tax"):
        clauses.append({"$or": [{"tax_classifications": classification}, {"service_type": {"$regex": "^" + re.escape(classification) + "$", "$options": "i"}}]})
    elif classification == "Prime":
        clauses.append({"$or": [{"is_prime": True}, {"customer_type": {"$regex": "^Prime$", "$options": "i"}}]})
    if clauses: query["$and"] = clauses
    items = await db["erp_customers"].find(query, {"_id": 0}).sort("updated_at", -1).to_list(length=100000)
    return {"success": True, "data": items}


@api_router.get("/web-ledger/customers/{customer_id}/ledger")
async def web_ledger_customer_ledger(
    customer_id: str,
    start_date: str = "",
    end_date: str = "",
    user: dict = Depends(get_current_user),
):
    """Return the selected NTAXCO customer's persisted financial activity.

    Invoice debits and recorded invoice receipts are presented using the
    accounts-receivable convention (invoice = debit, receipt = credit).
    An opening balance is only used when explicitly configured on the customer.
    """
    await _require_web_ledger(user)
    if start_date and not re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", start_date):
        raise HTTPException(status_code=422, detail="start_date must use YYYY-MM-DD.")
    if end_date and not re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", end_date):
        raise HTTPException(status_code=422, detail="end_date must use YYYY-MM-DD.")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date.")
    customer = await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    customer_names = [x for x in (customer.get("name"), customer.get("business_name")) if x]
    invoices = await db["web_ledger_invoices"].find({
        "scope": "ntaxco_internal",
        "$or": [{"customer_id": customer_id}, {"customer_name": {"$in": customer_names}}],
    }, {"_id": 0}).sort("created_at", 1).to_list(length=10000)
    invoices = [i for i in invoices if i.get("status") not in ("draft", "cancelled", "void")]
    invoice_ids = [i.get("id") for i in invoices if i.get("id")]
    payments = await db["web_ledger_payments"].find(
        {"scope": "ntaxco_internal", "invoice_id": {"$in": invoice_ids}}, {"_id": 0}
    ).sort("created_at", 1).to_list(length=10000) if invoice_ids else []
    transactions = await db["web_ledger_transactions"].find({
        "scope": "ntaxco_internal", "reversed_at": {"$exists": False},
        "$or": [{"customer_id": customer_id}, {"customer_name": {"$in": customer_names}}],
    }, {"_id": 0}).sort("date", 1).to_list(length=10000)

    def txn_date(item, fallback=""):
        value = str(item.get("issue_date") or item.get("date") or item.get("created_at") or fallback)
        return value[:10]

    entries = []
    for inv in invoices:
        date = txn_date(inv)
        if (start_date and date < start_date) or (end_date and date > end_date):
            continue
        entries.append({
            "date": date, "reference": inv.get("invoice_number") or inv.get("id") or "—",
            "description": "Invoice", "category": "Sales", "debit": round(float(inv.get("grand_total") or 0), 2),
            "credit": 0, "source_type": "invoice", "source_id": inv.get("id"),
        })
    for payment in payments:
        date = txn_date(payment)
        if (start_date and date < start_date) or (end_date and date > end_date):
            continue
        invoice = next((i for i in invoices if i.get("id") == payment.get("invoice_id")), {})
        entries.append({
            "date": date, "reference": payment.get("reference") or payment.get("id") or invoice.get("invoice_number") or "—",
            "description": "Receipt" + (f" for {invoice.get('invoice_number')}" if invoice.get("invoice_number") else ""),
            "category": "Receipt", "debit": 0, "credit": round(float(payment.get("amount") or 0), 2),
            "source_type": "payment", "source_id": payment.get("id"),
        })
    entries.sort(key=lambda x: (x["date"], x["source_type"], x["reference"]))
    opening_raw = customer.get("opening_balance")
    opening_configured = opening_raw is not None and str(opening_raw).strip() != ""
    try:
        opening_balance = round(float(opening_raw), 2) if opening_configured else None
    except (TypeError, ValueError):
        opening_balance, opening_configured = None, False
    running = opening_balance if opening_configured else 0.0
    for entry in entries:
        running = round(running + entry["debit"] - entry["credit"], 2)
        entry["running_balance"] = running
    sales = round(sum(e["debit"] for e in entries), 2)
    received = round(sum(e["credit"] for e in entries), 2)
    period_transactions = [
        t for t in transactions
        if (not start_date or txn_date(t) >= start_date)
        and (not end_date or txn_date(t) <= end_date)
    ]
    credits = round(sum(abs(float(t.get("amount") or 0)) for t in period_transactions
                        if str(t.get("type", "")).lower() in ("credit_note", "credit note")), 2)
    outstanding = round(max(0, sales - received - credits), 2)
    return {"success": True, "data": {
        "customer": customer, "invoices": invoices, "payments": payments, "transactions": transactions,
        "entries": entries, "opening_balance": opening_balance, "opening_balance_configured": opening_configured,
        "sales": sales, "received": received, "credit_notes": credits, "outstanding": outstanding,
        "total_debits": round(sum(e["debit"] for e in entries), 2),
        "total_credits": round(sum(e["credit"] for e in entries), 2),
        "limitations": "Opening balance is not configured unless explicitly stored on the customer. Legacy invoices without customer_id are matched by exact customer/business name. The statement lists invoice debits and invoice-linked receipt credits; unlinked receipts and unclassified transactions are not silently included.",
    }}

@api_router.post("/web-ledger/customers/{customer_id}/archive")
async def web_ledger_archive_customer(customer_id: str, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    customer = await db["erp_customers"].find_one({"id": customer_id}, {"_id": 0})
    if not customer: raise HTTPException(status_code=404, detail="Customer not found.")
    archived = body.get("archived", True)
    if not isinstance(archived, bool): raise HTTPException(status_code=422, detail="archived must be true or false.")
    # Soft-delete only: invoices, receipts, subscriptions and transaction history are untouched.
    now = datetime.now(timezone.utc).isoformat()
    changes = {"archived": archived, "updated_at": now, "archive_changed_by": str(user.get("id") or user.get("email") or "")}
    if archived: changes["archived_at"] = now
    else: changes["restored_at"] = now
    await db["erp_customers"].update_one({"id": customer_id}, {"$set": changes})
    return {"success": True, "data": {"id": customer_id, "archived": archived}}

@api_router.get("/web-ledger/app", response_class=HTMLResponse)
async def web_ledger_app(user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    raise HTTPException(
        status_code=410,
        detail="The standalone Ledger page has been retired. Open Web Ledger from the authenticated NTAXCO Admin portal.",
    )


# Persistent, server-authorized Ledger records. These collections are scoped to the
# NTAXCO internal business ledger; no customer-owned workspace data is mixed in.
def _ledger_record_id():
    return str(uuid.uuid4())


def _web_ledger_financial_totals(invoices, receipts_by_invoice):
    """Pure aggregation for persisted, non-draft invoices and receipt amounts."""
    sales = round(sum(float(item.get("grand_total") or 0) for item in invoices), 2)
    received = round(sum(float(receipts_by_invoice.get(str(item.get("id")), 0) or 0) for item in invoices), 2)
    return {"sales": sales, "received": received, "outstanding": round(max(0, sales - received), 2)}


@api_router.get("/web-ledger/transactions")
async def web_ledger_list_transactions(user: dict = Depends(get_current_user), limit: int = Query(500, ge=1, le=1000)):
    await _require_web_ledger(user)
    records = await db["web_ledger_transactions"].find({"scope": "ntaxco_internal", "reversed_at": {"$exists": False}}, {"_id": 0}).sort("date", -1).to_list(length=limit)
    return {"success": True, "data": records}


@api_router.post("/web-ledger/transactions")
async def web_ledger_create_transaction(body: dict = Body(...), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="A valid transaction amount is required.")
    if not (amount > 0 and amount < 1_000_000_000_000):
        raise HTTPException(status_code=422, detail="Amount must be greater than zero and within the supported range.")
    kind = str(body.get("type", "")).lower().strip()
    if kind not in {"income", "expense"}:
        raise HTTPException(status_code=422, detail="Transaction type must be income or expense.")
    description = str(body.get("description", "")).strip()
    date_value = str(body.get("date", "")).strip()
    if not description or not date_value:
        raise HTTPException(status_code=422, detail="Description and date are required.")
    now = datetime.now(timezone.utc).isoformat()
    record = {"id": _ledger_record_id(), "scope": "ntaxco_internal", "type": kind,
              "amount": round(amount, 2), "description": description[:500], "date": date_value[:40],
              "category": str(body.get("category", "Uncategorized"))[:100],
              "payment_method": str(body.get("payment_method", ""))[:80],
              "reference": str(body.get("reference", ""))[:120], "notes": str(body.get("notes", ""))[:2000],
              "created_by": str(user.get("id") or user.get("email") or ""),
              "created_at": now, "updated_at": now}
    await db["web_ledger_transactions"].insert_one(record)
    record.pop("_id", None)
    return {"success": True, "data": record}


@api_router.get("/web-ledger/dashboard")
async def web_ledger_dashboard(user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    rows = await db["web_ledger_transactions"].find({"scope": "ntaxco_internal", "reversed_at": {"$exists": False}}, {"_id": 0, "type": 1, "amount": 1}).to_list(length=100000)
    income = round(sum(float(r.get("amount", 0)) for r in rows if r.get("type") == "income"), 2)
    expenses = round(sum(float(r.get("amount", 0)) for r in rows if r.get("type") == "expense"), 2)
    return {"success": True, "data": {"income": income, "expenses": expenses, "net_balance": round(income-expenses, 2), "transaction_count": len(rows)}}


@api_router.get("/web-ledger/invoices")
async def web_ledger_list_invoices(user: dict = Depends(get_current_user), limit: int = Query(500, ge=1, le=1000)):
    await _require_web_ledger(user)
    records = await db["web_ledger_invoices"].find({"scope": "ntaxco_internal"}, {"_id": 0}).sort("created_at", -1).to_list(length=limit)
    return {"success": True, "data": records}


@api_router.post("/web-ledger/invoices")
async def web_ledger_create_invoice(body: dict = Body(...), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    customer = str(body.get("customer_name", "")).strip()
    canonical_customer_id = str(body.get("customer_id", "") or "").strip()
    if canonical_customer_id and not await db["erp_customers"].find_one({"id": canonical_customer_id, "archived": {"$ne": True}}, {"_id": 1}):
        raise HTTPException(status_code=422, detail="The selected canonical customer does not exist or is archived.")
    items = body.get("items")
    if not customer or not isinstance(items, list) or not items:
        raise HTTPException(status_code=422, detail="Customer name and at least one invoice item are required.")
    issue_date = str(body.get("issue_date", datetime.now(timezone.utc).date().isoformat()))
    due_date = str(body.get("due_date", "") or "")
    if not _ledger_valid_date(issue_date) or (due_date and not _ledger_valid_date(due_date)):
        raise HTTPException(status_code=422, detail="Invoice and due dates must be valid YYYY-MM-DD dates.")
    total = 0.0
    normalized = []
    for item in items:
        try:
            qty, price = float(item.get("quantity", 1)), float(item.get("unit_price", 0))
        except (AttributeError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Invoice item quantity and price must be numeric.")
        if qty <= 0 or price < 0 or qty >= 1_000_000 or price >= 1_000_000_000:
            raise HTTPException(status_code=422, detail="Invoice item quantity must be positive and values within the supported range.")
        if not str(item.get("description", "")).strip():
            raise HTTPException(status_code=422, detail="Each invoice item requires a description.")
        line_total = round(qty * price, 2)
        total += line_total
        normalized.append({"description": str(item.get("description", ""))[:500], "quantity": qty, "unit_price": price, "line_total": line_total})
    now = datetime.now(timezone.utc).isoformat()
    invoice_no = "NTX-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8].upper()
    record = {"id": _ledger_record_id(), "scope": "ntaxco_internal", "invoice_number": invoice_no,
              "customer_name": customer[:250], "customer_id": canonical_customer_id or None, "issue_date": str(body.get("issue_date", datetime.now(timezone.utc).date().isoformat()))[:40],
              "due_date": str(body.get("due_date", ""))[:40], "items": normalized, "subtotal": round(total, 2),
              "tax_amount": round(max(0, float(body.get("tax_amount", 0) or 0)), 2), "amount_paid": 0,
              "status": "draft", "created_by": str(user.get("id") or user.get("email") or ""), "created_at": now, "updated_at": now}
    record["grand_total"] = round(record["subtotal"] + record["tax_amount"], 2)
    await db["web_ledger_invoices"].insert_one(record)
    record.pop("_id", None)
    return {"success": True, "data": record}


# Additional auditable NTAXCO internal Ledger operations. All queries are scoped.
@api_router.patch("/web-ledger/transactions/{record_id}")
async def web_ledger_update_transaction(record_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    allowed = {"type", "amount", "description", "date", "category", "payment_method", "reference", "notes"}
    changes = {k: v for k, v in body.items() if k in allowed}
    if not changes:
        raise HTTPException(status_code=422, detail="No editable transaction fields were supplied.")
    existing = await db["web_ledger_transactions"].find_one({"id": record_id, "scope": "ntaxco_internal"}, {"_id": 0, "reversed_at": 1})
    if not existing: raise HTTPException(status_code=404, detail="Transaction not found.")
    if existing.get("reversed_at"): raise HTTPException(status_code=409, detail="Reversed transactions cannot be edited.")
    if "type" in changes:
        changes["type"] = str(changes["type"]).lower().strip()
        if changes["type"] not in {"income", "expense"}:
            raise HTTPException(status_code=422, detail="Transaction type must be income or expense.")
    if "date" in changes and not _ledger_valid_date(changes["date"]):
        raise HTTPException(status_code=422, detail="Date must be a valid YYYY-MM-DD date.")
    if "description" in changes:
        changes["description"] = str(changes["description"]).strip()
        if not changes["description"] or len(changes["description"]) > 500:
            raise HTTPException(status_code=422, detail="Description is required and must be at most 500 characters.")
    if "amount" in changes:
        try: amount = float(changes["amount"])
        except (TypeError, ValueError): raise HTTPException(status_code=422, detail="A valid amount is required.")
        if not 0 < amount < 1_000_000_000_000: raise HTTPException(status_code=422, detail="Amount must be greater than zero.")
        changes["amount"] = round(amount, 2)
    if "description" in changes and not str(changes["description"]).strip(): raise HTTPException(status_code=422, detail="Description is required.")
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    changes["updated_by"] = str(user.get("id") or user.get("email") or "")
    result = await db["web_ledger_transactions"].update_one({"id": record_id, "scope": "ntaxco_internal", "reversed_at": {"$exists": False}}, {"$set": changes})
    if not result.matched_count:
        latest = await db["web_ledger_transactions"].find_one({"id": record_id, "scope": "ntaxco_internal"}, {"_id": 0, "reversed_at": 1})
        if not latest: raise HTTPException(status_code=404, detail="Transaction not found.")
        raise HTTPException(status_code=409, detail="Reversed transactions cannot be edited.")
    await db["web_ledger_audit"].insert_one({"scope":"ntaxco_internal","record_type":"transaction","record_id":record_id,"action":"updated","actor":changes["updated_by"],"changes":{k:str(v)[:500] for k,v in changes.items() if k not in {"updated_at"}},"created_at":changes["updated_at"]})
    return {"success": True}

@api_router.delete("/web-ledger/transactions/{record_id}")
async def web_ledger_reverse_transaction(record_id: str, reason: str = Query(""), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    old = await db["web_ledger_transactions"].find_one({"id":record_id,"scope":"ntaxco_internal"},{"_id":0})
    if not old: raise HTTPException(status_code=404, detail="Transaction not found.")
    if old.get("reversed_at"): raise HTTPException(status_code=409, detail="Transaction has already been reversed.")
    now=datetime.now(timezone.utc).isoformat(); actor=str(user.get("id") or user.get("email") or "")
    changed = await db["web_ledger_transactions"].update_one({"id":record_id,"scope":"ntaxco_internal","reversed_at":{"$exists":False}}, {"$set":{"reversed_at":now,"reversed_by":actor,"updated_at":now}})
    if not changed.modified_count: raise HTTPException(status_code=409, detail="Transaction has already been reversed.")
    await db["web_ledger_audit"].insert_one({"scope":"ntaxco_internal","record_type":"transaction","record_id":record_id,"action":"reversed","actor":actor,"reason":str(reason).strip()[:500],"original_reference":old.get("reference"),"created_at":now})
    return {"success":True,"message":"Transaction reversed; original record retained for audit."}

@api_router.get("/web-ledger/reports")
async def web_ledger_reports(start_date: str = Query(""), end_date: str = Query(""), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    query={"scope":"ntaxco_internal","reversed_at":{"$exists":False}}
    if start_date: query["date"]={"$gte":start_date}
    if end_date: query.setdefault("date",{})["$lte"]=end_date
    rows=await db["web_ledger_transactions"].find(query,{"_id":0}).sort("date",-1).to_list(length=100000)
    income=round(sum(float(x.get("amount",0)) for x in rows if x.get("type")=="income"),2)
    expenses=round(sum(float(x.get("amount",0)) for x in rows if x.get("type")=="expense"),2)
    invoices=await db["web_ledger_invoices"].find({"scope":"ntaxco_internal","status":{"$ne":"cancelled"}},{"_id":0,"grand_total":1,"amount_paid":1,"status":1}).to_list(length=100000)
    receivables=round(sum(max(0,float(i.get("grand_total",0))-float(i.get("amount_paid",0))) for i in invoices if i.get("status")!="draft"),2)
    by_date = {}
    by_category = {}
    for row in rows:
        amount = float(row.get("amount", 0))
        kind = row.get("type")
        day = str(row.get("date", "Unknown"))[:10]
        category = str(row.get("category") or "Uncategorized")
        for key, bucket in ((day, by_date), (category, by_category)):
            entry = bucket.setdefault(key, {"income": 0.0, "expenses": 0.0, "count": 0})
            entry["income" if kind == "income" else "expenses"] += amount
            entry["count"] += 1
    def rounded_groups(groups):
        return {key: {"income": round(val["income"], 2), "expenses": round(val["expenses"], 2),
                      "net_operating_result": round(val["income"] - val["expenses"], 2),
                      "count": val["count"]} for key, val in groups.items()}
    return {"success":True,"data":{"income":income,"expenses":expenses,"net_operating_result":round(income-expenses,2),
        "receivables":receivables,"transactions":rows,"date_summary":rounded_groups(by_date),
        "category_summary":rounded_groups(by_category)}}

@api_router.get("/web-ledger/invoices/{invoice_id}")
async def web_ledger_invoice_detail(invoice_id: str, user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    item=await db["web_ledger_invoices"].find_one({"id":invoice_id,"scope":"ntaxco_internal"},{"_id":0})
    if not item: raise HTTPException(status_code=404,detail="Invoice not found.")
    payments=await db["web_ledger_payments"].find({"scope":"ntaxco_internal","invoice_id":invoice_id},{"_id":0}).sort("created_at",-1).to_list(length=1000)
    return {"success":True,"data":{"invoice":item,"payments":payments}}

@api_router.patch("/web-ledger/invoices/{invoice_id}")
async def web_ledger_update_draft_invoice(invoice_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
    await _require_web_ledger(user)
    invoice = await db["web_ledger_invoices"].find_one(
        {"id": invoice_id, "scope": "ntaxco_internal"}, {"_id": 0}
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.get("status") != "draft":
        raise HTTPException(status_code=409, detail="Only draft invoices can be edited.")
    customer = str(body.get("customer_name", invoice.get("customer_name", ""))).strip()
    items = body.get("items", invoice.get("items", []))
    if not customer or not isinstance(items, list) or not items:
        raise HTTPException(status_code=422, detail="Customer name and at least one invoice item are required.")
    normalized = []
    subtotal = 0.0
    for item in items:
        if not isinstance(item, dict) or not str(item.get("description", "")).strip():
            raise HTTPException(status_code=422, detail="Each invoice item requires a description.")
        try:
            qty, price = float(item.get("quantity", 1)), float(item.get("unit_price", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Invoice item quantity and price must be numeric.")
        if qty <= 0 or price < 0 or qty >= 1_000_000 or price >= 1_000_000_000:
            raise HTTPException(status_code=422, detail="Invoice quantity or price is outside the supported range.")
        line_total = round(qty * price, 2)
        subtotal += line_total
        normalized.append({"description": str(item["description"]).strip()[:500], "quantity": qty,
                           "unit_price": price, "line_total": line_total})
    try:
        tax = float(body.get("tax_amount", invoice.get("tax_amount", 0)) or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Tax amount must be numeric.")
    if tax < 0 or tax >= 1_000_000_000:
        raise HTTPException(status_code=422, detail="Tax amount is outside the supported range.")
    issue_date = str(body.get("issue_date", invoice.get("issue_date", "")))
    due_date = str(body.get("due_date", invoice.get("due_date", "")) or "")
    if not _ledger_valid_date(issue_date) or (due_date and not _ledger_valid_date(due_date)):
        raise HTTPException(status_code=422, detail="Invoice and due dates must be valid YYYY-MM-DD dates.")
    now = datetime.now(timezone.utc).isoformat()
    changes = {"customer_name": customer[:250], "items": normalized, "issue_date": issue_date,
               "due_date": due_date, "subtotal": round(subtotal, 2), "tax_amount": round(tax, 2),
               "grand_total": round(subtotal + tax, 2), "updated_at": now,
               "updated_by": str(user.get("id") or user.get("email") or "")}
    await db["web_ledger_invoices"].update_one({"id": invoice_id, "scope": "ntaxco_internal", "status": "draft"}, {"$set": changes})
    await db["web_ledger_audit"].insert_one({"scope": "ntaxco_internal", "record_type": "invoice",
        "record_id": invoice_id, "action": "draft_updated", "actor": changes["updated_by"], "created_at": now})
    return {"success": True}


@api_router.post("/web-ledger/invoices/{invoice_id}/cancel")
async def web_ledger_cancel_invoice(invoice_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
    """Cancel a draft/issued invoice without deleting the record or its history."""
    await _require_web_ledger(user)
    reason = str(body.get("reason", "")).strip()
    if not reason or len(reason) > 500:
        raise HTTPException(status_code=422, detail="A cancellation reason of 1–500 characters is required.")
    invoice = await db["web_ledger_invoices"].find_one(
        {"id": invoice_id, "scope": "ntaxco_internal"}, {"_id": 0}
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.get("status") not in {"draft", "issued"}:
        raise HTTPException(status_code=409, detail="Only draft or issued invoices can be cancelled.")
    try:
        amount_paid = float(invoice.get("amount_paid", 0) or 0)
    except (TypeError, ValueError):
        amount_paid = 0
    payment_count = await db["web_ledger_payments"].count_documents(
        {"scope": "ntaxco_internal", "invoice_id": invoice_id}
    )
    if amount_paid > 0 or payment_count:
        raise HTTPException(status_code=409, detail="Invoices with payment records cannot be cancelled through Web Ledger.")
    now = datetime.now(timezone.utc).isoformat()
    actor = str(user.get("id") or user.get("email") or "")
    result = await db["web_ledger_invoices"].update_one(
        {"id": invoice_id, "scope": "ntaxco_internal", "status": invoice.get("status"), "amount_paid": {"$in": [0, None]}},
        {"$set": {"status": "cancelled", "cancelled_at": now, "cancelled_by": actor,
                   "cancellation_reason": reason, "updated_at": now}}
    )
    if not result.matched_count:
        raise HTTPException(status_code=409, detail="Invoice status changed; refresh and try again.")
    await db["web_ledger_audit"].insert_one({"scope": "ntaxco_internal", "record_type": "invoice",
        "record_id": invoice_id, "action": "cancelled", "actor": actor, "reason": reason, "created_at": now})
    return {"success": True, "data": {"id": invoice_id, "status": "cancelled"}}


@api_router.get("/web-ledger/invoices/{invoice_id}/pdf")
async def web_ledger_invoice_pdf(invoice_id: str, user: dict = Depends(get_current_user)):
    """Generate a printable PDF from the persisted invoice and payment records."""
    await _require_web_ledger(user)
    invoice = await db["web_ledger_invoices"].find_one(
        {"id": invoice_id, "scope": "ntaxco_internal"}, {"_id": 0}
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    payments = await db["web_ledger_payments"].find(
        {"scope": "ntaxco_internal", "invoice_id": invoice_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(length=1000)
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        stream = BytesIO()
        doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm)
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="NTAXCOBrand", parent=styles["Title"], textColor=colors.HexColor("#15345b"), fontSize=18, leading=22))
        story = [Paragraph("NTAXCO — Nizam's Tax Consultancy", styles["NTAXCOBrand"]),
                 Paragraph("INVOICE", styles["Heading2"]), Spacer(1, 5*mm)]
        safe = lambda value: str(value if value is not None else "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story += [Paragraph(f"<b>Invoice number:</b> {safe(invoice.get('invoice_number'))}", styles["BodyText"]),
                  Paragraph(f"<b>Issue date:</b> {safe(invoice.get('issue_date'))}", styles["BodyText"]),
                  Paragraph(f"<b>Due date:</b> {safe(invoice.get('due_date') or '—')}", styles["BodyText"]),
                  Paragraph(f"<b>Customer:</b> {safe(invoice.get('customer_name'))}", styles["BodyText"]),
                  Paragraph(f"<b>Status:</b> {safe(invoice.get('status'))}", styles["BodyText"]), Spacer(1, 5*mm)]
        data = [["Description", "Quantity", "Rate (INR)", "Amount (INR)"]]
        for item in invoice.get("items", []):
            desc = str(item.get("description", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            data.append([Paragraph(desc or "—", styles["BodyText"]), str(item.get("quantity", 1)),
                         f"{float(item.get('unit_price',0)):,.2f}", f"{float(item.get('line_total',0)):,.2f}"])
        table = Table(data, colWidths=[82*mm, 22*mm, 32*mm, 34*mm], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e8eef5")),
            ("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#15345b")),("GRID",(0,0),(-1,-1),0.4,colors.HexColor("#cbd5e1")),
            ("ALIGN",(1,1),(-1,-1),"RIGHT"),("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
            ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
        story += [table, Spacer(1, 5*mm)]
        totals = [["Subtotal", f"INR {float(invoice.get('subtotal',0)):,.2f}"],
                  ["Tax", f"INR {float(invoice.get('tax_amount',0)):,.2f}"],
                  ["Grand total", f"INR {float(invoice.get('grand_total',0)):,.2f}"],
                  ["Amount paid (existing records)", f"INR {float(invoice.get('amount_paid',0)):,.2f}"],
                  ["Balance", f"INR {max(0,float(invoice.get('grand_total',0))-float(invoice.get('amount_paid',0))):,.2f}"]]
        totals_table = Table(totals, colWidths=[105*mm, 65*mm], hAlign="RIGHT")
        totals_table.setStyle(TableStyle([("ALIGN",(1,0),(1,-1),"RIGHT"),("LINEABOVE",(0,2),(-1,2),0.8,colors.HexColor("#15345b")),
            ("FONTNAME",(0,2),(-1,2),"Helvetica-Bold"),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
        story.append(totals_table)
        if payments:
            story += [Spacer(1, 6*mm), Paragraph("Recorded payment history", styles["Heading3"])]
            pdata = [["Date", "Method", "Amount"]]
            for payment in payments:
                pdata.append([str(payment.get("payment_date","—")), str(payment.get("method","—")), f"INR {float(payment.get('amount',0)):,.2f}"])
            ptable = Table(pdata, colWidths=[55*mm, 65*mm, 50*mm], repeatRows=1)
            ptable.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e8eef5")),("GRID",(0,0),(-1,-1),0.4,colors.HexColor("#cbd5e1")),("ALIGN",(2,1),(2,-1),"RIGHT")]))
            story.append(ptable)
        doc.build(story)
        stream.seek(0)
        filename = f"{invoice.get('invoice_number','invoice')}.pdf".replace("/", "-")
        return StreamingResponse(stream, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except ImportError:
        raise HTTPException(status_code=503, detail="PDF generation dependency is unavailable on this server.")


@api_router.post("/web-ledger/invoices/{invoice_id}/issue")
async def web_ledger_issue_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    await _require_web_ledger(user); now=datetime.now(timezone.utc).isoformat()
    result=await db["web_ledger_invoices"].update_one({"id":invoice_id,"scope":"ntaxco_internal","status":"draft"},{"$set":{"status":"issued","issued_at":now,"updated_at":now}})
    if not result.matched_count: raise HTTPException(status_code=409,detail="Only a draft invoice can be issued.")
    await db["web_ledger_audit"].insert_one({"scope":"ntaxco_internal","record_type":"invoice","record_id":invoice_id,"action":"issued","actor":str(user.get("id") or user.get("email") or ""),"created_at":now})
    return {"success":True}

@api_router.get("/billbook/data")
async def get_billbook_data(user: dict = Depends(get_current_user)):
    """Fetch the authenticated customer's My BillBook document only."""
    if user.get("role") != "customer":
        raise HTTPException(status_code=403, detail="My BillBook is available to customer accounts only.")
    customer_id = str(user.get("id") or "").strip()
    if not customer_id:
        raise HTTPException(status_code=401, detail="Authenticated customer identity is missing.")
    if billbook_subscription_required() and not await customer_has_active_subscription(db, customer_id):
        raise HTTPException(status_code=403, detail={"code": "subscription_required", "message": "An active My BillBook subscription is required."})
    doc = await db["billbook_customer_data"].find_one({"customer_id": customer_id}, {"_id": 0, "data": 1, "updated_at": 1})
    return {"success": True, "data": doc.get("data") if doc else None, "updated_at": doc.get("updated_at") if doc else None}

@api_router.put("/billbook/data")
async def save_billbook_data(body: dict = Body(...), user: dict = Depends(get_current_user)):
    """Persist My BillBook data under the authenticated customer's immutable identity."""
    if user.get("role") != "customer":
        raise HTTPException(status_code=403, detail="My BillBook is available to customer accounts only.")
    customer_id = str(user.get("id") or "").strip()
    if not customer_id:
        raise HTTPException(status_code=401, detail="Authenticated customer identity is missing.")
    if billbook_subscription_required() and not await customer_has_active_subscription(db, customer_id):
        raise HTTPException(status_code=403, detail={"code": "subscription_required", "message": "An active My BillBook subscription is required."})
    data = body.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="A valid BillBook data object is required.")
    required_arrays = ("customers", "suppliers", "products", "invoices", "payments", "expenses", "purchases", "quotations", "salesOrders")
    for key in required_arrays:
        if key in data and not isinstance(data[key], list):
            raise HTTPException(status_code=422, detail=f"{key} must be an array.")
    # Bound request size to protect the API; uploaded documents belong in object storage, not this JSON document.
    import json as _json
    if len(_json.dumps(data, default=str, separators=(",", ":"))) > 2_000_000:
        raise HTTPException(status_code=413, detail="My BillBook data exceeds the 2 MB limit. Remove embedded files or images and retry.")
    now = datetime.now(timezone.utc).isoformat()
    # A unique owner key prevents duplicate tenant documents under concurrent first saves.
    await db["billbook_customer_data"].create_index("customer_id", unique=True)
    await db["billbook_customer_data"].update_one(
        {"customer_id": customer_id},
        {"$set": {"customer_id": customer_id, "data": data, "updated_at": now, "updated_by": customer_id}, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"success": True, "updated_at": now}

app.include_router(api_router)
from erp import build_erp_router, seed_erp, ensure_phase4_relationships, _ok
from admin_profile import build_admin_profile_router
# Register the ERP router once, then verify the actual route templates immediately.
# Revenue Kanban lists/list-item/move intentionally use the existing generic
# {board} implementation; requiring literal /revenue paths here would reject a
# valid router and prevent FastAPI from starting.
erp_router = build_erp_router(db, get_current_user)
app.include_router(erp_router)
# My BillBook subscription plans / Razorpay checkout / webhook (uses existing Razorpay env keys).
app.include_router(build_billbook_subscription_router(db, get_current_user))
_critical_routes = {getattr(r, "path", "") for r in app.routes}
_missing_critical_routes = [
    "/api/revenue",
    "/api/revenue/employees",
    "/api/revenue/agents",
    "/api/revenue/customers",
    "/api/revenue/transactions",
    "/api/admin/kanban/{board}/lists",
    "/api/admin/kanban/{board}/lists/{list_id}",
    "/api/admin/kanban/{board}/move",
    "/api/admin/kanban/revenue/cards",
]
_missing_critical_routes = [p for p in _missing_critical_routes if p not in _critical_routes]
if _missing_critical_routes:
    raise RuntimeError(
        "This NTAXCO backend copy is missing required Revenue routes: "
        + ", ".join(_missing_critical_routes)
        + ". Start the backend from this project's backend folder."
    )
print("[ROUTES] Revenue + Revenue Kanban routes registered (generic lists/list-item/move + exact revenue cards)", flush=True)
app.include_router(security.build_security_router(db, get_current_user, mask_mobile))
app.include_router(build_admin_profile_router(db, get_current_user))
from customer_import import build_customer_import_router
app.include_router(build_customer_import_router(db, get_current_user))
from starlette.middleware.cors import CORSMiddleware



@app.on_event("startup")
async def ensure_web_ledger_indexes():
    # Unique invoice numbers and common transaction date queries.
    await ensure_index(
        db["web_ledger_invoices"],
        [("scope", 1), ("invoice_number", 1)],
        unique=True,
        name="ledger_invoice_number_unique",
    )
    await ensure_index(
        db["web_ledger_transactions"],
        [("scope", 1), ("date", -1)],
        name="ledger_transactions_date",
    )

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
