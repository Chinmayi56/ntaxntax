"""Single source of truth for interpreting ``billbook_plans`` documents.

Dependency-free (no FastAPI / Mongo imports) so it is unit-testable anywhere and so the Admin
screens, the customer ``GET /api/billbook/plans`` endpoint, checkout and the migration tool all
interpret a stored plan record *identically*.

Principles
  * Admin-managed MongoDB records are authoritative. Nothing here defines prices.
  * Canonical fields (``price``, ``duration_days``, ``duration_months``, ``active``, ``published``,
    ``archived``, ``currency``) win. Legacy aliases are only *read*, never guessed: a value that is
    present but unusable is reported, never replaced by an invented one.
  * One eligibility rule (``configured``) is used for listing, detail and checkout.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from billbook_rules import parse_price

# --------------------------------------------------------------------------------------------
# Field vocabularies (canonical first, then legacy aliases found in older records)
# --------------------------------------------------------------------------------------------
ID_KEYS = ("id", "plan_id", "planId", "slug", "code", "key")
PRICE_KEYS = ("price", "amount", "price_inr", "amount_inr", "selling_price", "sale_price",
              "plan_price", "cost", "fee", "rate", "pricing")
PAISE_KEYS = ("price_paise", "amount_paise")
DAYS_KEYS = ("duration_days", "days", "validity_days", "duration_in_days", "period_days")
MONTHS_KEYS = ("duration_months", "months", "validity_months", "duration_in_months", "period_months")
YEARS_KEYS = ("duration_years", "years", "validity_years")
DURATION_TEXT_KEYS = ("duration", "billing_period", "period", "validity", "term", "duration_label", "interval")
DURATION_UNIT_KEYS = ("duration_unit", "unit", "period_unit", "interval_unit")
ACTIVE_KEYS = ("active", "is_active", "isActive", "enabled", "is_enabled")
PUBLISHED_KEYS = ("published", "is_published", "isPublished", "visible", "is_visible", "public", "is_public")
ARCHIVED_KEYS = ("archived", "is_archived", "isArchived", "deleted", "is_deleted")

_FALSE_WORDS = {"false", "0", "inactive", "unpublished", "no", "off", "disabled", "draft",
                "archived", "deleted", "hidden", "closed", "n", "f"}
_TRUE_WORDS = {"true", "1", "active", "published", "yes", "on", "enabled", "live", "visible", "y", "t"}

ISSUE_TEXT = {
    "PRICE_MISSING": "price is missing or invalid",
    "PRICE_INVALID": "price is missing or invalid",
    "DURATION_MISSING": "duration is missing or invalid",
    "DURATION_INVALID": "duration is missing or invalid",
    "CURRENCY_UNSUPPORTED": "currency is not supported by checkout (only INR)",
    "INACTIVE": "plan is inactive",
    "UNPUBLISHED": "plan is unpublished",
    "ARCHIVED": "plan is archived",
    "DUPLICATE": "plan duplicates another purchasable plan",
}
# Issues that need the Admin to fix configuration (as opposed to a deliberate state like inactive).
CONFIG_ISSUES = {"PRICE_MISSING", "PRICE_INVALID", "DURATION_MISSING", "DURATION_INVALID", "CURRENCY_UNSUPPORTED"}


class PlanValidationError(ValueError):
    """Raised by :func:`validate_plan_input`; the router maps it to HTTP 422."""


# --------------------------------------------------------------------------------------------
# Small coercion helpers
# --------------------------------------------------------------------------------------------
def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def coerce_bool(value: Any, default: bool | None = True) -> bool | None:
    """Interpret legacy booleans / status words consistently. Unknown text -> ``default``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    word = str(value).strip().lower()
    if word in _FALSE_WORDS:
        return False
    if word in _TRUE_WORDS:
        return True
    return default


def _first_present(doc: dict, keys: Iterable[str]):
    for key in keys:
        if key in doc and _present(doc[key]):
            return key, doc[key]
    return None, None


def _to_nonneg_int(value: Any) -> int | None:
    """Strict integer parsing. ``None`` for anything that is not a whole, non-negative number."""
    if value is None or isinstance(value, bool):
        return None
    if hasattr(value, "to_decimal"):
        try:
            value = value.to_decimal()
        except Exception:
            return None
    try:
        number = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number < 0 or number != int(number):
        return None
    return int(number)


def doc_id(doc: dict) -> str:
    _key, value = _first_present(doc, ID_KEYS)
    return str(value).strip() if value is not None else ""


def period_label(days: int, months: int) -> str:
    if days:
        return f"{days} day" if days == 1 else f"{days} days"
    if months and months % 12 == 0 and months >= 12:
        years = months // 12
        return "1 year" if years == 1 else f"{years} years"
    if months:
        return "1 month" if months == 1 else f"{months} months"
    return ""


_WORD_DURATIONS = {
    "weekly": (7, 0), "monthly": (0, 1), "quarterly": (0, 3), "half-yearly": (0, 6),
    "half yearly": (0, 6), "halfyearly": (0, 6), "semi-annual": (0, 6), "semiannual": (0, 6),
    "yearly": (0, 12), "annual": (0, 12), "annually": (0, 12),
}
_DURATION_RE = re.compile(r"^(\d+)\s*-?\s*(days?|d|months?|mos?|years?|yrs?|yr)$")


def parse_duration_text(text: Any) -> tuple[int, int] | None:
    """``"15 days"`` -> (15, 0); ``"6 Months"`` -> (0, 6); ``"1 year"`` -> (0, 12); else None."""
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if t in _WORD_DURATIONS:
        return _WORD_DURATIONS[t]
    m = _DURATION_RE.match(t)
    if not m:
        return None
    qty, unit = int(m.group(1)), m.group(2)
    if qty <= 0:
        return None
    if unit.startswith("d"):
        return qty, 0
    if unit.startswith("m"):
        return 0, qty
    return 0, qty * 12


# --------------------------------------------------------------------------------------------
# Price / duration extraction
# --------------------------------------------------------------------------------------------
def extract_price(doc: dict) -> tuple[float | None, str | None, str]:
    """Return (price, source_field, status) where status is ok | missing | invalid."""
    saw_invalid = False
    for key in PRICE_KEYS:
        if key in doc and _present(doc[key]):
            price = parse_price(doc[key])
            if price is not None:
                return price, key, "ok"
            saw_invalid = True
    for key in PAISE_KEYS:  # explicit *_paise fields only; never guess units
        if key in doc and _present(doc[key]):
            paise = parse_price(doc[key])
            if paise is not None:
                return round(paise / 100, 2), key, "ok"
            saw_invalid = True
    return None, None, "invalid" if saw_invalid else "missing"


def extract_duration(doc: dict, fallback: dict | None = None) -> dict:
    """Resolve (days, months) from canonical fields, legacy aliases or duration text.

    Returns {days, months, valid, source, status} with status ok | missing | invalid.
    A catalog duration is used ONLY when the record has no duration information at all.
    """
    dk, raw_days = _first_present(doc, DAYS_KEYS)
    mk, raw_months = _first_present(doc, MONTHS_KEYS)
    yk, raw_years = _first_present(doc, YEARS_KEYS)
    any_field = any(k for k in (dk, mk, yk))
    # A canonical key that exists but is null/blank still counts as "field present but empty".
    explicit_canonical = any(k in doc for k in ("duration_days", "duration_months"))

    days = _to_nonneg_int(raw_days) if dk else 0
    months = _to_nonneg_int(raw_months) if mk else 0
    years = _to_nonneg_int(raw_years) if yk else 0
    if None in (days, months, years):
        return {"days": 0, "months": 0, "valid": False, "source": dk or mk or yk, "status": "invalid"}
    months += years * 12
    source = dk or mk or yk

    if not days and not months:
        # Canonical numbers are empty/zero: look for textual or unit-based legacy descriptions.
        for key in DURATION_TEXT_KEYS:
            if key not in doc or not _present(doc[key]):
                continue
            parsed = parse_duration_text(doc[key]) if isinstance(doc[key], str) else None
            if parsed is None:
                number = _to_nonneg_int(doc[key]) if not isinstance(doc[key], str) else None
                unit_key, unit = _first_present(doc, DURATION_UNIT_KEYS)
                if number and unit:
                    parsed = parse_duration_text(f"{number} {str(unit).strip().lower()}")
            if parsed:
                return {"days": parsed[0], "months": parsed[1], "valid": True, "source": key, "status": "ok"}
        text_present = any(k in doc and _present(doc[k]) for k in DURATION_TEXT_KEYS)
        if not any_field and not explicit_canonical and not text_present and fallback:
            fd, fm = int(fallback.get("duration_days") or 0), int(fallback.get("duration_months") or 0)
            if bool(fd) != bool(fm):
                return {"days": fd, "months": fm, "valid": True, "source": "catalog", "status": "ok"}
        status = "invalid" if (any_field or explicit_canonical or text_present) else "missing"
        return {"days": 0, "months": 0, "valid": False, "source": source, "status": status}

    if bool(days) == bool(months):  # both positive
        return {"days": days, "months": months, "valid": False, "source": source, "status": "invalid"}
    return {"days": days, "months": months, "valid": True, "source": source, "status": "ok"}


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


# --------------------------------------------------------------------------------------------
# Normalization of one plan
# --------------------------------------------------------------------------------------------
def normalize_plan(doc: dict | None, base: dict | None = None, env: dict | None = None,
                   default_features: list | None = None, plan_id: str | None = None) -> dict:
    """Canonical, read-only view of one plan. ``doc`` is the stored record (or ``None``)."""
    doc = {k: v for k, v in (doc or {}).items() if k != "_id"}
    base = base or {}
    pid = plan_id or doc_id(doc) or str(base.get("id") or "")
    issues: list[str] = []

    price, price_key, price_status = extract_price(doc)
    plan_type = str(doc.get("plan_type") or "paid").strip().lower()
    is_trial = plan_type in {"trial", "free_trial"}
    # Zero is a valid no-charge value only for an explicitly typed trial plan.
    # Never infer a trial from a zero price on a paid/legacy plan.
    if is_trial and price is None:
        raw_trial_price = doc.get("price", None)
        if raw_trial_price is None or (str(raw_trial_price).strip() in {"", "0", "0.0", "0.00"}):
            price, price_key, price_status = 0.0, "trial_no_price", "ok"
    price_source = "database" if price is not None else None
    if price is None and base and env is not None:
        env_price = parse_price(env.get(f"BILLBOOK_PRICE_{pid.upper()}"))
        if env_price is not None:
            price, price_source, price_status = env_price, "environment", "ok"
    if price is None or (price <= 0 and not is_trial):
        issues.append("PRICE_INVALID" if price_status == "invalid" or price is not None else "PRICE_MISSING")
    if is_trial and price is not None and price > 0:
        issues.append("PRICE_INVALID")

    dur = extract_duration(doc, base if base else None)
    if not dur["valid"]:
        issues.append("DURATION_INVALID" if dur["status"] == "invalid" else "DURATION_MISSING")

    active_key, active_raw = _first_present(doc, ACTIVE_KEYS)
    status_value = doc.get("status")
    if active_key:
        active = bool(coerce_bool(active_raw, True))
    else:
        active = bool(coerce_bool(status_value, True)) if _present(status_value) else True
    published_key, published_raw = _first_present(doc, PUBLISHED_KEYS)
    published = bool(coerce_bool(published_raw, True)) if published_key else True
    archived_key, archived_raw = _first_present(doc, ARCHIVED_KEYS)
    archived = bool(coerce_bool(archived_raw, False)) if archived_key else False
    if str(status_value or "").strip().lower() in {"archived", "deleted"}:
        archived = True
        active = False

    currency = str(doc.get("currency") or "INR").strip().upper() or "INR"
    if currency != "INR":
        issues.append("CURRENCY_UNSUPPORTED")
    if not active:
        issues.append("INACTIVE")
    if not published:
        issues.append("UNPUBLISHED")
    if archived:
        issues.append("ARCHIVED")

    raw_features = doc.get("features")
    if isinstance(raw_features, list):
        features = [str(x).strip() for x in raw_features if str(x).strip()]
    else:
        features = list(default_features or []) if base else []

    days, months = dur["days"], dur["months"]
    plan = {
        **{k: v for k, v in base.items()},
        **doc,
        "id": pid,
        "name": str(doc.get("name") or base.get("name") or pid),
        "duration_days": days,
        "duration_months": months,
        "billing_period": period_label(days, months) if dur["valid"] else None,
        "price": price,
        "currency": currency,
        "features": features,
        "active": active,
        "published": published,
        "archived": archived,
        "price_source": price_source,
        "price_field": price_key,
        "duration_source": dur["source"],
        "database_managed": bool(doc),
        "duration_valid": bool(dur["valid"]),
        "issues": issues,
    }
    plan.setdefault("description", "")
    plan.setdefault("created_at", None)
    plan.setdefault("display_order", 0)
    plan.setdefault("billing_type", "one_time")
    return plan


def issue_message(plan: dict) -> str:
    reasons: list[str] = []
    for code in plan.get("issues", []):
        text = ISSUE_TEXT.get(code, code)
        if code == "DUPLICATE" and plan.get("duplicate_of"):
            text = f"duplicates the purchasable plan '{plan['duplicate_of']}' (same name and duration)"
        if text not in reasons:
            reasons.append(text)
    return f"The {plan.get('name') or plan.get('id')} plan is unavailable: {', '.join(reasons) or 'configuration is invalid'}."


# --------------------------------------------------------------------------------------------
# Whole-catalogue evaluation (merge catalogue + stored records, de-duplicate, diagnose)
# --------------------------------------------------------------------------------------------
def _sort_key(plan: dict):
    return (int(plan.get("display_order") or 0), str(plan.get("created_at") or ""), plan["id"])


def evaluate_plans(stored_docs: Iterable[dict], catalog: list[dict], env: dict | None = None,
                   default_features: list | None = None) -> list[dict]:
    """Return every known plan (catalogue placeholders + stored records) in one normalized contract.

    ``configured`` is the single "purchasable" flag: valid price, valid duration, INR, active,
    published, not archived and not a duplicate of another purchasable plan.
    """
    env = env if env is not None else {}
    catalog_by_id = {c["id"]: c for c in catalog}
    stored: dict[str, dict] = {}
    for doc in stored_docs:
        pid = doc_id(doc)
        if pid and pid not in stored:
            stored[pid] = doc

    plans: dict[str, dict] = {}
    for base in catalog:
        plans[base["id"]] = normalize_plan(stored.get(base["id"]), base, env, default_features, base["id"])
    for pid, doc in stored.items():
        if pid not in plans:
            plans[pid] = normalize_plan(doc, None, env, default_features, pid)

    # --- duplicates -------------------------------------------------------------------------
    # Same (duration, normalized name) is "the same product". The winner keeps being purchasable;
    # the other records are kept (history!) but not purchasable. Catalogue ids win ties, then
    # display order, creation time and id, so the outcome is deterministic.
    groups: dict[tuple, list[dict]] = {}
    for plan in plans.values():
        if plan["duration_valid"]:
            groups.setdefault((plan["duration_days"], plan["duration_months"], _name_key(plan["name"])), []).append(plan)
    for members in groups.values():
        eligible = [p for p in members if not p["issues"]]
        if len(members) > 1 and eligible:
            winner = min(eligible, key=lambda p: (0 if p["id"] in catalog_by_id else 1, *_sort_key(p)))
            for p in members:
                if p is winner:
                    continue
                if not p["issues"]:
                    p["issues"].append("DUPLICATE")
                    p["duplicate_of"] = winner["id"]
                elif not p["database_managed"]:
                    p["superseded_by"] = winner["id"]  # unconfigured placeholder; nothing to fix
    # Informational: same duration but different names (could be intentional, so not blocked).
    by_duration: dict[tuple, list[dict]] = {}
    for plan in plans.values():
        if plan["duration_valid"] and not plan["archived"] and plan["database_managed"]:
            by_duration.setdefault((plan["duration_days"], plan["duration_months"]), []).append(plan)
    for members in by_duration.values():
        for p in members:
            others = [o["id"] for o in members if o is not p and _name_key(o["name"]) != _name_key(p["name"])]
            if others:
                p["shares_duration_with"] = others

    for plan in plans.values():
        plan["configured"] = not plan["issues"]
        if plan["configured"]:
            plan.pop("configuration_issue", None)
        else:
            plan["configuration_issue"] = issue_message(plan)
    return sorted(plans.values(), key=_sort_key)


def customer_view(plans: list[dict]) -> tuple[list[dict], list[str], list[dict]]:
    """Return purchasable paid plans only; trials use the dedicated trial API."""
    # Keep trial plans in the canonical Admin/MongoDB catalogue, but never expose them
    # as ordinary purchasable cards. The explicit plan_type is authoritative (not its name).
    paid_plans = [p for p in plans if str(p.get("plan_type") or "paid").strip().lower() not in {"trial", "free_trial"}]
    eligible = [p for p in paid_plans if p["configured"]]
    missing: list[str] = []
    diagnostics: list[dict] = []
    for p in paid_plans:
        if p["configured"] or p.get("superseded_by"):
            continue
        issues = p["issues"]
        if "ARCHIVED" in issues or issues == ["DUPLICATE"]:
            continue  # deliberately retired / de-duplicated: nothing for anyone to fix
        missing.append(p["configuration_issue"])
        diagnostics.append({
            "id": p["id"], "name": p["name"], "reasons": list(issues), "message": p["configuration_issue"],
            "needs_admin_configuration": bool(set(issues) & CONFIG_ISSUES) and p["active"] and p["published"],
        })
    return eligible, missing, diagnostics


# --------------------------------------------------------------------------------------------
# Admin input validation (create / update)
# --------------------------------------------------------------------------------------------
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,79}")


def validate_plan_input(body: dict, existing: dict | None, now_iso: str) -> dict:
    """Return the canonical document to persist, or raise :class:`PlanValidationError`."""
    body = body if isinstance(body, dict) else {}
    existing = existing or {}
    name = str(body.get("name") if body.get("name") is not None else existing.get("name") or "").strip()
    plan_id = str(body.get("id") or existing.get("id") or "").strip().lower()
    if not name or len(name) > 100:
        raise PlanValidationError("Plan name is required and must be at most 100 characters.")
    if not _SLUG_RE.fullmatch(plan_id):
        raise PlanValidationError("Plan ID must be a unique slug using lowercase letters, numbers, hyphens or underscores.")

    plan_type = str(body.get("plan_type", existing.get("plan_type", "paid")) or "paid").strip().lower()
    if plan_type not in {"paid", "trial", "free_trial"}:
        raise PlanValidationError("plan_type must be paid or free_trial.")
    if plan_type == "free_trial":
        raw_price = body.get("price", existing.get("price", 0))
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            price = -1
        if price != 0:
            raise PlanValidationError("Free-trial plans must have a price of zero.")
        plan_type = "free_trial"
    elif "price" in body:
        price = parse_price(body.get("price"))
        if price is None:
            raise PlanValidationError("Paid plan price must be greater than zero.")
    else:
        price = parse_price(existing.get("price"))
        if price is None:
            raise PlanValidationError("This paid plan has no valid price yet. Enter a price greater than zero.")

    def _int(key, default):
        raw = body[key] if key in body else existing.get(key, default)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return default
        value = _to_nonneg_int(raw)
        if value is None:
            raise PlanValidationError(f"{key.replace('_', ' ').capitalize()} must be a whole number of zero or more.")
        return value

    days, months, order = _int("duration_days", 0), _int("duration_months", 0), _int("display_order", 0)
    if bool(days) == bool(months):
        raise PlanValidationError("Set exactly one positive duration: either days or months (the other must be 0).")
    if days > 3650 or months > 120:
        raise PlanValidationError("Duration is too long (maximum 3650 days or 120 months).")

    currency = str(body.get("currency") or existing.get("currency") or "INR").strip().upper()
    if currency != "INR":
        raise PlanValidationError("Only INR is currently supported by the configured Razorpay subscription checkout.")

    features = body.get("features", existing.get("features", []))
    if features is None:
        features = []
    if not isinstance(features, list):
        raise PlanValidationError("Features must be a list of strings.")

    if "active" in body:
        active = coerce_bool(body["active"], None)
    elif "status" in body:
        active = coerce_bool(body["status"], None)
    else:
        active = existing.get("active", True)
    if active is None:
        raise PlanValidationError("Active status must be true/false (or 'active'/'inactive').")
    published = coerce_bool(body["published"], None) if "published" in body else existing.get("published", True)
    if published is None:
        raise PlanValidationError("Published must be true or false.")

    return {
        "id": plan_id, "name": name,
        "description": str(body.get("description", existing.get("description", "")) or "")[:2000],
        "duration_days": days, "duration_months": months, "billing_period": period_label(days, months),
        "price": price, "currency": currency, "plan_type": plan_type,
        "eligibility_rules": body.get("eligibility_rules", existing.get("eligibility_rules", {"one_per_customer": False})),
        "billing_type": str(body.get("billing_type") or existing.get("billing_type") or "one_time")[:40],
        "features": [str(x).strip()[:160] for x in features if str(x).strip()][:30],
        "display_order": order, "active": bool(active), "published": bool(published),
        "created_at": existing.get("created_at") or now_iso, "updated_at": now_iso,
    }


# --------------------------------------------------------------------------------------------
# Safe, idempotent migration planning (pure; the CLI applies the result)
# --------------------------------------------------------------------------------------------
MIGRATION_VERSION = 1


def plan_migration(stored_docs: Iterable[dict], catalog: list[dict], env: dict | None = None,
                   default_features: list | None = None, accept_name_durations: bool = False) -> dict:
    """Work out additive canonicalisation of legacy plan records.

    Rules: only ADD/RETYPE canonical fields whose value is already determinable from the same
    record (legacy alias, duration text, explicit legacy status). Never delete a record or a legacy
    field, never invent a price, never activate/publish anything that was not explicitly stored as
    such, never write a zero duration. Re-running on migrated data yields no changes.
    """
    docs = [d for d in stored_docs]
    evaluated = {p["id"]: p for p in evaluate_plans(docs, catalog, env, default_features)}
    updates, unchanged, needs_action, unidentified = [], [], [], []
    catalog_ids = {c["id"] for c in catalog}

    for doc in docs:
        pid = doc_id(doc)
        if not pid:
            unidentified.append({"_id": str(doc.get("_id")), "reason": "record has no usable id / plan_id / slug"})
            continue
        base = next((c for c in catalog if c["id"] == pid), None)
        norm = normalize_plan(doc, base, {}, default_features, pid)  # no env: never persist env prices
        changes: dict[str, Any] = {}
        notes: list[str] = []

        if "id" not in doc:
            changes["id"] = pid
            notes.append(f"added canonical id from legacy key")
        canonical_price = parse_price(doc.get("price"))
        if norm["price"] is not None and norm["price_source"] == "database":
            if canonical_price is None:
                changes["price"] = norm["price"]
                notes.append(f"price {norm['price']} taken from legacy field '{norm['price_field']}'")
            elif not isinstance(doc.get("price"), (int, float)) or isinstance(doc.get("price"), bool):
                if canonical_price == norm["price"]:
                    changes["price"] = norm["price"]
                    notes.append("price re-typed from text to number (same value)")
        dur_ok = norm["duration_valid"] and norm["duration_source"] != "catalog"
        canonical_pair_ok = (_to_nonneg_int(doc.get("duration_days")) is not None and _to_nonneg_int(doc.get("duration_months")) is not None
                             and bool(_to_nonneg_int(doc.get("duration_days"))) != bool(_to_nonneg_int(doc.get("duration_months"))))
        if (dur_ok or (norm["duration_valid"] and base)) and not canonical_pair_ok:
            changes["duration_days"], changes["duration_months"] = norm["duration_days"], norm["duration_months"]
            changes["billing_period"] = norm["billing_period"]
            notes.append(f"duration {norm['billing_period']} taken from '{norm['duration_source']}'")
        elif not norm["duration_valid"] and accept_name_durations:
            guess = parse_duration_text(norm["name"])
            if guess:
                changes["duration_days"], changes["duration_months"] = guess
                changes["billing_period"] = period_label(*guess)
                notes.append(f"duration {changes['billing_period']} taken from plan name (explicitly accepted)")

        for canon, keys, value in (("active", ACTIVE_KEYS, norm["active"]), ("published", PUBLISHED_KEYS, norm["published"]),
                                   ("archived", ARCHIVED_KEYS, norm["archived"])):
            status_word = str(doc.get("status") or "").strip().lower()
            has_legacy = any(k in doc and _present(doc[k]) for k in keys) or (
                (canon == "active" and _present(doc.get("status")))
                or (canon == "archived" and status_word in {"archived", "deleted"}))
            if has_legacy and not isinstance(doc.get(canon), bool):
                changes[canon] = value
                notes.append(f"{canon}={value} from stored legacy value")
        if norm["archived"] and not isinstance(doc.get("archived"), bool):
            changes["active"] = False
        if "currency" not in doc or not _present(doc.get("currency")):
            changes["currency"] = "INR"
            notes.append("currency set to INR (the only currency checkout supports)")

        if changes:
            changes.update({"migration_version": MIGRATION_VERSION})
            updates.append({"id": pid, "_id": doc.get("_id"), "set": changes, "notes": notes})
        else:
            unchanged.append(pid)

        remaining = []
        after = {**doc, **changes}
        after_norm = normalize_plan(after, base, {}, default_features, pid)
        for code in after_norm["issues"]:
            if code in CONFIG_ISSUES:
                remaining.append(code)
        if remaining:
            needs_action.append({"id": pid, "name": norm["name"], "problems": sorted(set(remaining)),
                                 "action": "Admin must set a valid " + " and ".join(sorted({"price" if c.startswith("PRICE") else "duration" if c.startswith("DURATION") else "currency" for c in remaining})) + " in Subscription Plans."})

    duplicates = [{"id": p["id"], "duplicate_of": p["duplicate_of"]} for p in evaluated.values() if p.get("duplicate_of")]
    return {"version": MIGRATION_VERSION, "updates": updates, "unchanged": unchanged,
            "needs_admin_action": needs_action, "duplicates": duplicates, "unidentified": unidentified,
            "catalog_placeholders_without_record": sorted(catalog_ids - {doc_id(d) for d in docs})}
