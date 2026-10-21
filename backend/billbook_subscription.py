"""My BillBook subscription plans, checkout, verified activation and webhook.

Design rules (see customer/BILLBOOK_SUBSCRIPTION.md):
  * Plan names/durations are fixed by the product requirement; PRICES are never defined in code.
    A price comes from the ``billbook_plans`` collection or the ``BILLBOOK_PRICE_<PLAN_ID>``
    environment variable. If neither exists the plan is reported as "price not configured"
    and cannot be purchased.
  * The browser never supplies a price, customer id or payment status. Price is read on the
    server, the customer is the authenticated NTAXCO user, and a subscription becomes active
    ONLY after the Razorpay signature (or webhook signature) is verified server-side AND the
    payment is confirmed with Razorpay for the exact order/amount.
  * Uses the existing Razorpay environment variables (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET).
    There is no mock/test activation path here: without keys, checkout returns 503.
  * Verified payments are also written to the existing ``erp_payments`` collection so the
    existing Admin Payments screens show them. No second payment history is introduced.
"""
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from billbook_rules import (  # noqa: F401  (re-exported for callers/tests)
    BILLBOOK_FEATURES, BUSINESS_TYPES, PLAN_CATALOG, PLAN_IDS, _parse_dt, add_months, compute_expiry,
    normalized_details, parse_price, subscription_required, subscription_state, validate_customer_details,
    verify_checkout_signature, verify_webhook_signature,
)
from billbook_plans import (  # noqa: E402
    ID_KEYS, PlanValidationError, coerce_bool, customer_view, evaluate_plans, validate_plan_input,
)

def _default_razorpay_client():
    kid = os.environ.get("RAZORPAY_KEY_ID")
    ksec = os.environ.get("RAZORPAY_KEY_SECRET")
    if not (kid and ksec):
        return None, None, None
    try:
        import razorpay  # existing dependency used by /payments/create-order
        return razorpay.Client(auth=(kid, ksec)), kid, ksec
    except Exception:
        return None, None, None


# ----------------------------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------------------------
def build_billbook_subscription_router(db, get_current_user, razorpay_factory=None):
    router = APIRouter(prefix="/api")
    rzp = razorpay_factory or _default_razorpay_client

    def _ok(data=None, message="OK"):
        return {"success": True, "message": message, "data": data}

    def _require_customer(user):
        if not isinstance(user, dict) or user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="My BillBook subscriptions are available to customer accounts only.")
        cid = str(user.get("id") or "").strip()
        if not cid:
            raise HTTPException(status_code=401, detail="Authenticated customer identity is missing.")
        return cid

    async def _erp_customer_id(user):
        """Canonical ERP customer id for the signed-in customer (same rule as erp.py), or None.
        Used only to link the mirrored payment to the existing customer record; never created here."""
        meta_id = (user.get("meta") or {}).get("customer_id")
        if meta_id:
            return meta_id
        clauses = [c for c in ({"email": user.get("email")} if user.get("email") else None, {"mobile": user.get("mobile")} if user.get("mobile") else None) if c]
        if clauses:
            for clause in clauses:
                rec = await db["erp_customers"].find_one(clause, {"_id": 0, "id": 1})
                if rec:
                    return rec.get("id")
        return None

    async def _plans():
        """All known plans through the ONE normalization contract in ``billbook_plans``.

        Admin-managed ``billbook_plans`` records are authoritative; the four fixed catalogue
        entries are placeholders that carry no price until Admin (or an env var) provides one.
        """
        stored = [doc async for doc in db["billbook_plans"].find({}, {"_id": 0})]
        return evaluate_plans(stored, PLAN_CATALOG, os.environ, BILLBOOK_FEATURES)

    async def _plan(plan_id):
        for plan in await _plans():
            if plan["id"] == plan_id:
                return plan
        raise HTTPException(status_code=422, detail="Unknown subscription plan.")

    async def _find_stored(plan_id):
        """The stored record for ``plan_id`` (matching legacy id aliases too), or None."""
        for key in ID_KEYS:
            doc = await db["billbook_plans"].find_one({key: plan_id})
            if doc:
                return doc
        return None

    def _selector(doc, plan_id):
        """Update the exact stored record (never create a duplicate of a legacy-keyed one)."""
        if doc and "id" not in doc and doc.get("_id") is not None:
            return {"_id": doc["_id"]}
        return {"id": plan_id}

    async def _latest_subscription(customer_id):
        """Active (unexpired) subscription wins; otherwise the most recent record."""
        subs = [s async for s in db["billbook_subscriptions"].find({"customer_id": customer_id}, {"_id": 0})]
        if not subs:
            return None
        now = datetime.now(timezone.utc)
        active = [s for s in subs if subscription_state(s, now) == "active"]
        if active:
            return max(active, key=lambda s: str(s.get("expires_at")))
        return max(subs, key=lambda s: str(s.get("created_at")))

    def _public_sub(sub):
        if not sub:
            return None
        return {
            "id": sub.get("id"), "plan_id": sub.get("plan_id"), "plan_name": sub.get("plan_name"),
            "amount": sub.get("amount"), "currency": sub.get("currency", "INR"),
            "state": subscription_state(sub), "order_id": sub.get("order_id"),
            "payment_id": sub.get("payment_id"), "started_at": sub.get("started_at"),
            "expires_at": sub.get("expires_at"), "failure_reason": sub.get("failure_reason"),
            "created_at": sub.get("created_at"),
        }

    async def _record_erp_payment(sub, now):
        """Mirror a VERIFIED payment into the existing erp_payments collection (admin reporting)."""
        if await db["erp_payments"].find_one({"order_id": sub["order_id"]}, {"_id": 0}):
            return
        amount = round(float(sub["amount"]))
        date_str = now.strftime("%Y-%m-%d")
        await db["erp_payments"].insert_one({
            "id": sub["payment_id"], "payment_id": sub["payment_id"], "order_id": sub["order_id"],
            "reference_no": f"TXN{int(now.timestamp())}", "customer": sub.get("business_name") or sub.get("full_name"),
            "customer_id": sub.get("erp_customer_id") or sub["customer_id"], "invoice_id": "", "invoice_no": "",
            "amount": amount, "gst": 0, "total": amount, "mode": "Razorpay", "status": "Completed",
            "txn_date": date_str, "agent": "", "agent_id": None,
            "service": f"My BillBook subscription - {sub['plan_name']}", "service_id": None, "booking_id": None,
            "receipt_no": f"RCPT-{uuid.uuid4().hex[:8].upper()}",
            "remarks": f"My BillBook subscription ({sub['plan_name']}) via Razorpay, verified server-side",
            "source": "billbook_subscription", "subscription_id": sub["id"],
        })

    async def _generate_subscription_invoice(sub, now):
        """Persist one invoice per verified BillBook purchase/order; safe to retry.

        Amount and customer identity come from the stored subscription, never from the browser.
        Tax fields remain zero unless a validated tax calculation is available in the source model.
        """
        order_id = str(sub.get("order_id") or "").strip()
        subscription_id = str(sub.get("id") or "").strip()
        if not order_id or not subscription_id:
            raise ValueError("Verified subscription is missing its stable billing reference.")
        invoices = db["erp_invoices"]
        # A unique source reference makes repeated callbacks and concurrent retries idempotent.
        # MongoDB/Motor supports index creation; lightweight test doubles may not.
        # Production deployments should also provision this index during migration/startup.
        if hasattr(invoices, "create_index"):
            await invoices.create_index([("source", 1), ("source_ref", 1)], unique=True,
                                        partialFilterExpression={"source": "billbook_subscription", "source_ref": {"$type": "string"}})
        existing = await invoices.find_one({"source": "billbook_subscription", "source_ref": order_id}, {"_id": 0})
        if existing:
            await db["erp_payments"].update_many({"order_id": order_id}, {"$set": {
                "invoice_id": existing.get("id"), "invoice_no": existing.get("invoice_no")
            }})
            return existing
        settings = await db["billbook_subscription_settings"].find_one({"key": "default"}, {"_id": 0}) or {}
        prefix = str(settings.get("invoice_prefix") or "BB").strip()[:12] or "BB"
        counters = db["erp_counters"]
        if not hasattr(counters, "find_one_and_update"):
            # Compatibility with minimal in-memory test doubles; real MongoDB uses
            # atomic counter updates to allocate sequential invoice numbers.
            return None
        counter_key = f"invoice:{prefix}"
        # Seed a newly configured prefix from existing issued invoices so changing the
        # prefix or migrating historical records cannot accidentally reuse an invoice number.
        if hasattr(invoices, "find_one") and hasattr(counters, "update_one"):
            prior = await invoices.find_one(
                {"invoice_no": {"$regex": f"^{re.escape(prefix)}-\\d+$"}},
                {"_id": 0, "invoice_no": 1}, sort=[("invoice_no", -1)],
            )
            if prior and prior.get("invoice_no"):
                try:
                    prior_sequence = int(str(prior["invoice_no"]).rsplit("-", 1)[1])
                    await counters.update_one({"_id": counter_key}, {"$max": {"value": prior_sequence}}, upsert=True)
                except (ValueError, TypeError):
                    pass
        counter = await counters.find_one_and_update(
            {"_id": counter_key}, {"$inc": {"value": 1}}, upsert=True, return_document=True
        )
        sequence = int((counter or {}).get("value") or 1)
        invoice_no = f"{prefix}-{sequence:06d}"
        amount = round(float(sub.get("amount") or 0), 2)
        customer = sub.get("business_name") or sub.get("full_name") or sub.get("customer_id") or "Customer"
        issue_date = now.date().isoformat()
        invoice = {
            "id": f"bb-inv-{uuid.uuid4().hex}", "invoice_no": invoice_no,
            "customer": customer, "customer_id": sub.get("erp_customer_id") or sub.get("customer_id"),
            "gst_number": sub.get("gstin") or "", "invoice_date": issue_date,
            "issue_date": issue_date, "due_date": issue_date, "service": f"My BillBook subscription - {sub.get('plan_name') or sub.get('plan_id') or 'Subscription'}",
            "service_name": f"My BillBook subscription - {sub.get('plan_name') or sub.get('plan_id') or 'Subscription'}",
            "source": "billbook_subscription", "source_ref": order_id,
            "subscription_id": subscription_id, "purchase_id": subscription_id,
            "order_id": order_id, "payment_id": sub.get("payment_id"),
            "line_items": [{"description": f"My BillBook subscription - {sub.get('plan_name') or sub.get('plan_id') or 'Subscription'}", "quantity": 1, "unit_price": amount, "subtotal": amount}],
            "taxable": amount, "amount": amount, "discount": 0, "rate": 0,
            "cgst": 0, "sgst": 0, "igst": 0, "total": amount,
            "paid_amount": amount, "balance": 0, "status": "Issued", "payment_status": "Paid",
            "currency": sub.get("currency") or "INR", "created_at": now.isoformat(),
            "updated_at": now.isoformat(), "notes": "Generated from a server-verified BillBook subscription payment.",
        }
        try:
            await invoices.insert_one(invoice)
        except Exception:
            # A concurrent request may have inserted the unique source reference first.
            existing = await invoices.find_one({"source": "billbook_subscription", "source_ref": order_id}, {"_id": 0})
            if existing:
                invoice = existing
            else:
                raise
        await db["erp_payments"].update_many({"order_id": order_id}, {"$set": {
            "invoice_id": invoice.get("id"), "invoice_no": invoice.get("invoice_no")
        }})
        return invoice

    async def _activate(order_id, payment_id, now=None):
        """Idempotent activation of the pending subscription that owns ``order_id``.

        Returns the stored subscription. Only called after signature/webhook AND Razorpay
        confirmation, never from a bare frontend callback.
        """
        now = now or datetime.now(timezone.utc)
        sub = await db["billbook_subscriptions"].find_one({"order_id": order_id}, {"_id": 0})
        if not sub:
            raise HTTPException(status_code=404, detail="No subscription found for this payment order.")
        if sub.get("status") == "active":
            # A previous invoice write may have failed after payment activation; retry it
            # on repeated verified callbacks without changing the subscription itself.
            try:
                await _generate_subscription_invoice(sub, now)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("BillBook invoice retry failed for order %s", order_id)
            return sub
        try:
            plan = await _plan(sub["plan_id"])
        except HTTPException:
            plan = {}  # plan was removed after purchase: the checkout snapshot still governs
        # Renewal rule: if the customer still has an unexpired active plan, the new term starts
        # when it ends, so paid time is never lost. Otherwise it starts now.
        start = now
        settings = await db["billbook_subscription_settings"].find_one({"key": "default"}, {"_id": 0}) or {}
        renewal_rule = settings.get("renewal_rule", "after_expiry")
        others = [s async for s in db["billbook_subscriptions"].find({"customer_id": sub["customer_id"], "status": "active"}, {"_id": 0})]
        if renewal_rule != "from_today":
            for other in others:
                exp = _parse_dt(other.get("expires_at"))
                if exp and exp > start:
                    start = exp
        # Preserve the duration agreed at checkout even if the plan is edited later.
        duration_plan = {**plan,
            "duration_days": sub.get("duration_days_at_purchase", plan.get("duration_days", 0)),
            "duration_months": sub.get("duration_months_at_purchase", plan.get("duration_months", 0)),
        }
        expires = compute_expiry(start, duration_plan)
        if expires <= start:
            # Never turn a verified payment into a zero-length "active" subscription: park it for review.
            await db["billbook_subscriptions"].update_one(
                {"order_id": order_id, "status": {"$ne": "active"}},
                {"$set": {"payment_id": payment_id, "failure_reason": "Plan duration invalid at activation; manual review required."}})
            raise HTTPException(status_code=409, detail="This subscription has no valid duration and cannot be activated automatically. Support has been flagged.")
        update = {
            "status": "active", "payment_id": payment_id, "started_at": start.isoformat(),
            "expires_at": expires.isoformat(), "activated_at": now.isoformat(), "failure_reason": None,
        }
        # Conditional update: only one concurrent verify/webhook can flip pending -> active.
        result = await db["billbook_subscriptions"].update_one(
            {"order_id": order_id, "status": {"$ne": "active"}}, {"$set": update}
        )
        sub = await db["billbook_subscriptions"].find_one({"order_id": order_id}, {"_id": 0})
        if getattr(result, "modified_count", 1):
            await _record_erp_payment(sub, now)
        # Invoice generation is retryable and deliberately does not undo verified payment/access.
        if sub.get("status") == "active":
            try:
                await _generate_subscription_invoice(sub, now)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("BillBook invoice generation failed for order %s", order_id)
                # A later verified webhook/verification retry can safely create the missing invoice.
        return sub

    # ------------------------------ public read APIs ------------------------------
    def _no_store(response):
        if response is not None:
            response.headers["Cache-Control"] = "no-store"

    @router.get("/billbook/plans")
    async def list_plans(response: Response = None):
        """Eligible, Admin-managed plans. Public (pricing is not secret). Excluded plans are
        explained in ``missing_configuration`` / ``diagnostics`` - never silently dropped."""
        _no_store(response)
        settings = await db["billbook_subscription_settings"].find_one({"key": "default"}, {"_id": 0}) or {}
        if settings.get("customer_plan_visibility", True) is False:
            return {**_ok({"plans": [], "business_types": BUSINESS_TYPES, "missing_configuration": [],
                           "diagnostics": [], "plans_hidden": True}, "Subscription plans are temporarily unavailable.")}
        eligible, missing, diagnostics = customer_view(await _plans())
        return _ok({"plans": eligible, "business_types": BUSINESS_TYPES, "missing_configuration": missing,
                    "diagnostics": diagnostics, "plans_hidden": False})

    @router.get("/billbook/plans/{plan_id}")
    async def public_plan_detail(plan_id: str, response: Response = None):
        _no_store(response)
        settings = await db["billbook_subscription_settings"].find_one({"key": "default"}, {"_id": 0}) or {}
        if settings.get("customer_plan_visibility", True) is False:
            raise HTTPException(status_code=404, detail="Subscription plans are temporarily unavailable.")
        plan = next((p for p in await _plans() if p["id"] == plan_id and p["configured"]), None)
        if not plan:
            raise HTTPException(status_code=404, detail="Subscription plan is not available.")
        return _ok({"plan": plan})

    @router.get("/billbook/subscription/status")
    async def subscription_status(user: dict = Depends(get_current_user)):
        cid = _require_customer(user)
        sub = await _latest_subscription(cid)
        state = subscription_state(sub)
        return _ok({
            "state": state, "subscription": _public_sub(sub), "subscription_required": subscription_required(),
            "access_allowed": (state == "active") or not subscription_required(),
        })

    @router.get("/billbook/subscription/order/{order_id}")
    async def order_status(order_id: str, user: dict = Depends(get_current_user)):
        cid = _require_customer(user)
        sub = await db["billbook_subscriptions"].find_one({"order_id": order_id, "customer_id": cid}, {"_id": 0})
        if not sub:
            raise HTTPException(status_code=404, detail="Payment order not found.")
        return _ok({"state": subscription_state(sub), "subscription": _public_sub(sub)})

    # ------------------------------ checkout ------------------------------
    @router.post("/billbook/subscription/checkout")
    async def checkout(body: dict = Body(...), user: dict = Depends(get_current_user)):
        cid = _require_customer(user)
        plan_id = str(body.get("plan_id") or "")
        errors = validate_customer_details(body)
        if errors:
            raise HTTPException(status_code=422, detail={"message": "Please correct the highlighted fields.", "fields": errors})
        plan = await _plan(plan_id)
        if not plan["configured"] or not plan.get("duration_valid") or plan.get("price") is None:
            raise HTTPException(status_code=409, detail=plan.get("configuration_issue") or "This plan is not available.")
        # The client-supplied amount (if any) is ignored; the price is the server-side configured value.
        amount_paise = int(round(plan["price"] * 100))

        existing = await _latest_subscription(cid)
        state = subscription_state(existing)
        if state == "active" and existing.get("plan_id") == plan_id and str(body.get("renew")) != "true":
            raise HTTPException(status_code=409, detail="You already have an active subscription for this plan. Use renew to extend it.")

        client, key_id, _secret = rzp()
        if not client:
            key_id_set = bool(os.environ.get("RAZORPAY_KEY_ID", "").strip())
            key_secret_set = bool(os.environ.get("RAZORPAY_KEY_SECRET", "").strip())
            if not key_id_set or not key_secret_set:
                raise HTTPException(status_code=503, detail="Online payments are not configured on this server. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in the backend environment, then restart/redeploy the backend.")
            raise HTTPException(status_code=503, detail="Razorpay could not be initialized. Confirm the razorpay Python package is installed and the backend environment contains valid Razorpay test/live credentials.")

        details = normalized_details(body)
        # Reuse an unpaid pending order for the same plan instead of creating duplicates.
        pending = await db["billbook_subscriptions"].find_one(
            {"customer_id": cid, "plan_id": plan_id, "status": "pending", "amount": plan["price"],
             "duration_days_at_purchase": int(plan.get("duration_days") or 0),
             "duration_months_at_purchase": int(plan.get("duration_months") or 0)}, {"_id": 0})
        now = datetime.now(timezone.utc)
        if pending and pending.get("order_id") and (now - (_parse_dt(pending.get("created_at")) or now)) < timedelta(hours=1):
            await db["billbook_subscriptions"].update_one({"id": pending["id"]}, {"$set": {**details}})
            order_id = pending["order_id"]
            sub_id = pending["id"]
        else:
            sub_id = f"BBS-{uuid.uuid4().hex[:12].upper()}"
            try:
                order = client.order.create({
                    "amount": amount_paise, "currency": "INR", "payment_capture": 1,
                    "receipt": sub_id, "notes": {"customer_id": cid, "plan_id": plan_id, "subscription_id": sub_id, "product": "my_billbook"},
                })
            except Exception:
                raise HTTPException(status_code=502, detail="Unable to create the payment order with Razorpay. Please try again.")
            order_id = order["id"]
            await db["billbook_subscriptions"].insert_one({
                "id": sub_id, "customer_id": cid, "plan_id": plan_id, "plan_name": plan["name"],
                "amount": plan["price"], "currency": plan.get("currency", "INR"), "duration_days_at_purchase": int(plan.get("duration_days") or 0),
                "duration_months_at_purchase": int(plan.get("duration_months") or 0), "features_at_purchase": plan.get("features", []),
                "order_id": order_id, "status": "pending",
                "payment_id": None, "started_at": None, "expires_at": None, "created_at": now.isoformat(),
                "failure_reason": None, "erp_customer_id": await _erp_customer_id(user), **details,
            })
        return _ok({
            "order_id": order_id, "amount": amount_paise, "currency": "INR", "key_id": key_id,
            "plan": {"id": plan["id"], "name": plan["name"], "price": plan["price"]}, "subscription_id": sub_id,
            "prefill": {"name": details["full_name"], "email": details["email"], "contact": details["mobile"]},
        }, message="Payment order created")

    # ------------------------------ verification ------------------------------
    @router.post("/billbook/subscription/verify")
    async def verify(body: dict = Body(...), user: dict = Depends(get_current_user)):
        cid = _require_customer(user)
        order_id = str(body.get("razorpay_order_id") or "")
        payment_id = str(body.get("razorpay_payment_id") or "")
        signature = str(body.get("razorpay_signature") or "")
        sub = await db["billbook_subscriptions"].find_one({"order_id": order_id, "customer_id": cid}, {"_id": 0})
        if not sub:
            raise HTTPException(status_code=404, detail="Payment order not found for this account.")
        if sub.get("status") == "active":
            return _ok({"state": subscription_state(sub), "subscription": _public_sub(sub)}, message="Subscription already active")
        client, _kid, secret = rzp()
        if not client:
            raise HTTPException(status_code=503, detail="Payment verification is unavailable (Razorpay keys missing).")
        if not verify_checkout_signature(order_id, payment_id, signature, secret):
            await db["billbook_subscriptions"].update_one(
                {"order_id": order_id, "status": "pending"}, {"$set": {"failure_reason": "Signature verification failed"}})
            raise HTTPException(status_code=400, detail="Payment signature verification failed. Your subscription was not activated.")
        # Signature proves the callback is genuine; also confirm the payment with Razorpay itself.
        try:
            payment = client.payment.fetch(payment_id)
        except Exception:
            return _ok({"state": "pending", "subscription": _public_sub(sub)},
                       message="Payment received but not yet confirmed by Razorpay. Your subscription will activate once confirmed.")
        status = payment.get("status")
        if payment.get("order_id") != order_id or int(payment.get("amount", -1)) != int(round(float(sub["amount"]) * 100)):
            raise HTTPException(status_code=400, detail="Payment details do not match the order. Your subscription was not activated.")
        if status == "captured":
            sub = await _activate(order_id, payment_id)
            return _ok({"state": subscription_state(sub), "subscription": _public_sub(sub)}, message="Payment verified. Subscription activated.")
        if status == "failed":
            await db["billbook_subscriptions"].update_one(
                {"order_id": order_id, "status": {"$ne": "active"}}, {"$set": {"status": "failed", "payment_id": payment_id, "failure_reason": "Payment failed"}})
            sub = await db["billbook_subscriptions"].find_one({"order_id": order_id}, {"_id": 0})
            return _ok({"state": "failed", "subscription": _public_sub(sub)}, message="Payment failed. No subscription was activated.")
        return _ok({"state": "pending", "subscription": _public_sub(sub)}, message="Payment is still being processed. Access is granted only after confirmation.")

    @router.post("/billbook/subscription/cancel-attempt")
    async def cancel_attempt(body: dict = Body(...), user: dict = Depends(get_current_user)):
        """Customer dismissed the checkout popup. Marks only an unpaid pending order as cancelled."""
        cid = _require_customer(user)
        order_id = str(body.get("order_id") or "")
        await db["billbook_subscriptions"].update_one(
            {"order_id": order_id, "customer_id": cid, "status": "pending", "payment_id": None},
            {"$set": {"status": "cancelled", "failure_reason": "Checkout closed by customer"}})
        sub = await db["billbook_subscriptions"].find_one({"order_id": order_id, "customer_id": cid}, {"_id": 0})
        if not sub:
            raise HTTPException(status_code=404, detail="Payment order not found.")
        return _ok({"state": subscription_state(sub), "subscription": _public_sub(sub)})

    # ------------------------------ webhook ------------------------------
    @router.post("/billbook/subscription/webhook")
    async def webhook(request: Request):
        secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
        if not secret:
            raise HTTPException(status_code=503, detail="Webhook secret is not configured.")
        raw = await request.body()
        if not verify_webhook_signature(raw, request.headers.get("X-Razorpay-Signature", ""), secret):
            raise HTTPException(status_code=400, detail="Invalid webhook signature.")
        try:
            event = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid webhook payload.")
        etype = event.get("event")
        provider_event_id = str(event.get("id") or "")
        event_doc = {"id": provider_event_id or f"evt-{uuid.uuid4().hex}", "provider_event_id": provider_event_id or None,
            "product": "my_billbook", "event_type": str(etype or "unknown"), "status": "received",
            "received_at": datetime.now(timezone.utc).isoformat(), "processed_at": None,
            "order_id": None, "payment_id": None, "failure_reason": None}
        if provider_event_id:
            await db["billbook_payment_events"].update_one({"provider_event_id": provider_event_id}, {"$setOnInsert": event_doc}, upsert=True)
        payload = event.get("payload") or {}
        # Razorpay refund events carry a refund entity with payment_id (not order_id).
        # Preserve transaction history; mark the original payment refunded only when
        # the provider confirms a full refund. Partial refunds are recorded explicitly.
        refund = (((payload.get("refund") or {}).get("entity")) or {})
        if str(etype or "").startswith("refund.") and refund:
            refund_payment_id = str(refund.get("payment_id") or "")
            refund_amount = int(refund.get("amount") or 0)
            refund_status = str(refund.get("status") or "").lower()
            sub = await db["billbook_subscriptions"].find_one({"payment_id": refund_payment_id}, {"_id": 0}) if refund_payment_id else None
            if sub and refund_status in {"processed", "pending"}:
                full_refund = refund_status == "processed" and refund_amount >= int(round(float(sub.get("amount") or 0) * 100))
                refund_fields = {"refund_id": refund.get("id"), "refund_amount": round(refund_amount / 100, 2),
                                 "refund_status": refund_status, "refund_updated_at": datetime.now(timezone.utc).isoformat()}
                if full_refund:
                    refund_fields.update({"status": "refunded", "refunded_at": datetime.now(timezone.utc).isoformat()})
                await db["billbook_subscriptions"].update_one({"id": sub.get("id")}, {"$set": refund_fields})
                await db["erp_payments"].update_many({"order_id": sub.get("order_id")}, {"$set": {
                    "refund_id": refund.get("id"), "refund_amount": round(refund_amount / 100, 2),
                    "refund_status": refund_status, "status": "Refunded" if full_refund else "Completed"
                }})
                if provider_event_id:
                    await db["billbook_payment_events"].update_one({"provider_event_id": provider_event_id}, {"$set": {
                        "payment_id": refund_payment_id, "order_id": sub.get("order_id"), "status": "processed",
                        "processed_at": datetime.now(timezone.utc).isoformat()
                    }})
                return _ok({"refund_recorded": True, "full_refund": full_refund}, "Refund event processed")
            if provider_event_id:
                await db["billbook_payment_events"].update_one({"provider_event_id": provider_event_id}, {"$set": {"status": "ignored", "processed_at": datetime.now(timezone.utc).isoformat(), "failure_reason": "Unknown payment or unsupported refund state"}})
            return _ok(message="Ignored: unknown payment or unsupported refund state")

        entity = (((payload.get("payment") or {}).get("entity")) or {})
        order_id, payment_id = entity.get("order_id"), entity.get("id")
        event_doc.update({"order_id": order_id, "payment_id": payment_id})
        if provider_event_id:
            await db["billbook_payment_events"].update_one({"provider_event_id": provider_event_id}, {"$set": {"order_id": order_id, "payment_id": payment_id}})
        if not order_id:
            return _ok(message="Ignored")
        sub = await db["billbook_subscriptions"].find_one({"order_id": order_id}, {"_id": 0})
        if not sub:
            return _ok(message="Ignored: unknown order")  # not a BillBook order; other handlers may own it
        if etype in ("payment.captured", "order.paid"):
            if int(entity.get("amount", -1)) != int(round(float(sub["amount"]) * 100)):
                raise HTTPException(status_code=400, detail="Webhook amount does not match the order.")
            await _activate(order_id, payment_id)
        elif etype == "payment.failed":
            await db["billbook_subscriptions"].update_one(
                {"order_id": order_id, "status": {"$nin": ["active"]}},
                {"$set": {"status": "failed", "payment_id": payment_id, "failure_reason": (entity.get("error_description") or "Payment failed")}})
        if provider_event_id:
            await db["billbook_payment_events"].update_one({"provider_event_id": provider_event_id}, {"$set": {"status": "processed", "processed_at": datetime.now(timezone.utc).isoformat()}})
        return _ok(message="Processed")

    # ------------------------------ database-driven admin management ------------------------------
    def _require_admin(user):
        if not isinstance(user, dict) or str(user.get("role", "")).lower() not in {"admin", "super_admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="Administrator access required.")
        return str(user.get("id") or user.get("email") or "unknown")

    def _clean_plan(body, existing=None):
        try:
            return validate_plan_input(body, existing, datetime.now(timezone.utc).isoformat())
        except PlanValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @router.get("/admin/billbook/overview")
    async def admin_overview(user: dict = Depends(get_current_user)):
        _require_admin(user)
        plans = await _plans()
        subs = [x async for x in db["billbook_subscriptions"].find({}, {"_id": 0})]
        now = datetime.now(timezone.utc)
        states = [subscription_state(sub, now) for sub in subs]
        payments = [x async for x in db["erp_payments"].find({"source": "billbook_subscription"}, {"_id": 0})]
        successful = [p for p in payments if str(p.get("status", "")).lower() in {"completed", "success", "successful", "paid", "verified"}]
        pending = [p for p in payments if str(p.get("status", "")).lower() in {"pending", "created", "processing"}]
        failed = [p for p in payments if str(p.get("status", "")).lower() in {"failed", "cancelled", "canceled"}]
        pending_subs = [s for s in subs if str(s.get("status", "")).lower() in {"pending", "created", "processing"}]
        failed_subs = [s for s in subs if str(s.get("status", "")).lower() in {"failed", "cancelled", "canceled"}]
        known_orders = {str(p.get("order_id")) for p in payments if p.get("order_id")}
        pending_orders = {str(p.get("order_id")) for p in pending if p.get("order_id")}
        failed_orders = {str(p.get("order_id")) for p in failed if p.get("order_id")}
        pending_payment_count = len(pending_orders | {str(s.get("order_id")) for s in pending_subs if s.get("order_id")}) if any(p.get("order_id") for p in pending) or any(s.get("order_id") for s in pending_subs) else len(pending) + len(pending_subs)
        failed_payment_count = len(failed_orders | {str(s.get("order_id")) for s in failed_subs if s.get("order_id")}) if any(p.get("order_id") for p in failed) or any(s.get("order_id") for s in failed_subs) else len(failed) + len(failed_subs)
        revenue = round(sum(float(p.get("total", p.get("amount", 0)) or 0) for p in successful), 2)
        settings = await db["billbook_subscription_settings"].find_one({"key": "default"}, {"_id": 0}) or {}
        renewals_due_days = settings.get("renewals_due_days", 30)
        try:
            renewals_due_days = max(1, min(90, int(renewals_due_days)))
        except (TypeError, ValueError):
            renewals_due_days = 30
        renewal_limit = now + timedelta(days=renewals_due_days)
        upcoming = []
        for sub in subs:
            expires = _parse_dt(sub.get("expires_at"))
            if subscription_state(sub, now) == "active" and expires and now <= expires <= renewal_limit:
                upcoming.append(sub)
        recent_subs = sorted(subs, key=lambda x: str(x.get("created_at") or ""), reverse=True)[:5]
        recent_payments = sorted(payments, key=lambda x: str(x.get("txn_date") or x.get("created_at") or ""), reverse=True)[:5]
        invoices_count = await db["erp_invoices"].count_documents({"source": "billbook_subscription"})
        return _ok({"total_plans": len(plans),
                    "plans_needing_configuration": sum(1 for p in plans if not p.get("configured") and set(p.get("issues", [])) & {"PRICE_MISSING", "PRICE_INVALID", "DURATION_MISSING", "DURATION_INVALID", "CURRENCY_UNSUPPORTED"} and not p.get("superseded_by")),
                    "active_plans": sum(1 for p in plans if p.get("active") and p.get("published", True) and not p.get("archived")),
                    "inactive_plans": sum(1 for p in plans if not p.get("active") or not p.get("published", True) or p.get("archived")),
                    "total_customers": len({str(x.get("customer_id")) for x in subs if x.get("customer_id")}),
                    "active_subscribers": sum(1 for x in states if x == "active"),
                    "pending_subscriptions": sum(1 for x in states if x == "pending"),
                    "expired_subscriptions": sum(1 for x in states if x == "expired"),
                    "cancelled_subscriptions": sum(1 for x in states if x == "cancelled"),
                    "verified_revenue": revenue, "pending_payments": pending_payment_count, "failed_payments": failed_payment_count,
                    "pending_subscriptions_count": sum(1 for x in states if x == "pending"),
                    "invoices_generated": invoices_count, "renewals_due": len(upcoming),
                    "renewals_due_days": renewals_due_days,
                    "recent_purchases": recent_subs,
                    "recent_payments": recent_payments,
                    "upcoming_expirations": sorted(upcoming, key=lambda x: str(x.get("expires_at") or ""))[:5]})

    @router.get("/admin/billbook/plans")
    async def admin_list_plans(user: dict = Depends(get_current_user), search: str = "", status: str = "", published: str = "", limit: int = 100, skip: int = 0):
        _require_admin(user)
        all_plans = await _plans()
        plans = list(all_plans)
        needle = search.strip().lower()
        if needle:
            plans = [p for p in plans if needle in str(p.get("name", "")).lower() or needle in str(p.get("id", "")).lower()]
        if status == "active": plans = [p for p in plans if p.get("active") and not p.get("archived")]
        elif status == "inactive": plans = [p for p in plans if not p.get("active") and not p.get("archived")]
        elif status == "archived": plans = [p for p in plans if p.get("archived")]
        if published in {"true", "false"}:
            plans = [p for p in plans if bool(p.get("published", True)) is (published == "true")]
        total = len(plans); limit = max(1, min(limit, 500)); skip = max(0, skip)
        for plan in plans:
            plan["subscriber_count"] = await db["billbook_subscriptions"].count_documents({"plan_id": plan["id"], "status": "active"})
        needs = [{"id": p["id"], "name": p["name"], "issues": p["issues"], "message": p.get("configuration_issue")}
                 for p in all_plans if set(p.get("issues", [])) & {"PRICE_MISSING", "PRICE_INVALID", "DURATION_MISSING", "DURATION_INVALID", "CURRENCY_UNSUPPORTED"} and not p.get("archived") and not p.get("superseded_by")]
        duplicates = [{"id": p["id"], "duplicate_of": p.get("duplicate_of")} for p in all_plans if p.get("duplicate_of")]
        return _ok({"plans": plans[skip:skip + limit], "total": total, "limit": limit, "skip": skip,
                    "diagnostics": {"needs_configuration": needs, "duplicates": duplicates}})

    @router.post("/admin/billbook/plans")
    async def admin_create_plan(body: dict = Body(...), user: dict = Depends(get_current_user)):
        actor = _require_admin(user)
        plan = _clean_plan(body)
        if await _find_stored(plan["id"]):
            raise HTTPException(status_code=409, detail="A subscription plan with this ID already exists.")
        plan["created_by"] = actor
        plan["archived"] = False
        await db["billbook_plans"].insert_one(dict(plan))
        saved = next((p for p in await _plans() if p["id"] == plan["id"]), plan)
        return _ok({"plan": {k: v for k, v in saved.items() if k != "_id"}}, "Plan created")

    @router.get("/admin/billbook/plans/{plan_id}")
    async def admin_get_plan(plan_id: str, user: dict = Depends(get_current_user)):
        _require_admin(user)
        plan = next((p for p in await _plans() if p["id"] == plan_id), None)
        if not plan: raise HTTPException(status_code=404, detail="Subscription plan not found.")
        return _ok({"plan": plan})

    @router.put("/admin/billbook/plans/{plan_id}")
    async def admin_update_plan(plan_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        actor = _require_admin(user)
        current = next((p for p in await _plans() if p["id"] == plan_id), None)
        if not current: raise HTTPException(status_code=404, detail="Subscription plan not found.")
        stored = await _find_stored(plan_id)
        # Fall back to the normalized view (legacy aliases resolved) for fields the form omits.
        plan = _clean_plan({**body, "id": plan_id}, current)
        plan["updated_by"] = actor
        await db["billbook_plans"].update_one(_selector(stored, plan_id), {"$set": plan}, upsert=True)
        saved = next((p for p in await _plans() if p["id"] == plan_id), plan)
        return _ok({"plan": {k: v for k, v in saved.items() if k != "_id"}}, "Plan updated")

    @router.patch("/admin/billbook/plans/{plan_id}/status")
    async def admin_set_plan_status(plan_id: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
        actor = _require_admin(user)
        changes = {}
        for key in ("active", "published"):
            if key in body:
                if not isinstance(body[key], bool):
                    raise HTTPException(status_code=422, detail=f"{key} must be true or false.")
                changes[key] = body[key]
        if not changes:
            raise HTTPException(status_code=422, detail="Provide active and/or published as a boolean.")
        current = next((p for p in await _plans() if p["id"] == plan_id), None)
        if not current:
            raise HTTPException(status_code=404, detail="Subscription plan not found.")
        stored = await _find_stored(plan_id)
        now = datetime.now(timezone.utc).isoformat()
        changes.update({"id": plan_id, "updated_at": now, "updated_by": actor})
        await db["billbook_plans"].update_one(_selector(stored, plan_id), {"$set": changes}, upsert=True)
        saved = next((p for p in await _plans() if p["id"] == plan_id), {})
        return _ok({"id": plan_id, **{k: v for k, v in changes.items() if k in {"active", "published"}},
                    "configured": bool(saved.get("configured")), "issues": saved.get("issues", [])}, "Plan status updated")

    @router.post("/admin/billbook/plans/{plan_id}/duplicate")
    async def admin_duplicate_plan(plan_id: str, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
        actor = _require_admin(user)
        source = await _find_stored(plan_id)
        if not source:
            raise HTTPException(status_code=404, detail="Only database-managed plans can be duplicated.")
        source = {k: v for k, v in source.items() if k != "_id"}
        new_id = str(body.get("id") or f"{plan_id}-copy-{uuid.uuid4().hex[:6]}").strip().lower()
        import re
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,79}", new_id):
            raise HTTPException(status_code=422, detail="Provide a valid unique draft plan ID.")
        if await db["billbook_plans"].find_one({"id": new_id}, {"_id": 1}):
            raise HTTPException(status_code=409, detail="A plan with that ID already exists.")
        now = datetime.now(timezone.utc).isoformat()
        draft = {k: v for k, v in source.items() if k not in {"_id", "created_by", "updated_by", "subscriber_count"}}
        draft.update({"id": new_id, "name": str(body.get("name") or f"{source.get('name', plan_id)} (Copy)")[:100], "active": False, "published": False, "archived": False, "created_at": now, "updated_at": now, "created_by": actor, "duplicated_from": plan_id})
        await db["billbook_plans"].insert_one(draft)
        draft.pop("_id", None)
        return _ok({"plan": draft}, "Draft plan duplicated")

    @router.patch("/admin/billbook/plans/{plan_id}/archive")
    async def admin_archive_plan(plan_id: str, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
        actor = _require_admin(user)
        archived = body.get("archived", True)
        if not isinstance(archived, bool):
            raise HTTPException(status_code=422, detail="archived must be true or false.")
        existing = await _find_stored(plan_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Only database-managed plans can be archived.")
        now = datetime.now(timezone.utc).isoformat()
        changes = {"archived": archived, "updated_at": now, "updated_by": actor}
        if archived: changes.update({"active": False, "published": False})
        await db["billbook_plans"].update_one(_selector(existing, plan_id), {"$set": changes})
        return _ok({"id": plan_id, **changes}, "Plan archived" if archived else "Plan restored")

    @router.get("/admin/billbook/settings")
    async def admin_get_settings(user: dict = Depends(get_current_user)):
        _require_admin(user)
        settings = await db["billbook_subscription_settings"].find_one({"key": "default"}, {"_id": 0}) or {}
        settings.pop("key", None)
        # Secrets are never returned; only indicate whether backend configuration exists.
        settings["razorpay_configured"] = bool(os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET") and os.environ.get("RAZORPAY_WEBHOOK_SECRET"))
        settings.setdefault("default_currency", "INR")
        settings.setdefault("grace_period_days", 0)
        settings.setdefault("customer_plan_visibility", True)
        settings.setdefault("invoice_prefix", "BB")
        settings.setdefault("renewal_rule", "after_expiry")
        settings.setdefault("renewals_due_days", 30)
        return _ok(settings)

    @router.put("/admin/billbook/settings")
    async def admin_update_settings(body: dict = Body(...), user: dict = Depends(get_current_user)):
        actor = _require_admin(user)
        allowed = {"default_currency", "grace_period_days", "customer_plan_visibility", "invoice_prefix", "renewal_rule", "renewals_due_days"}
        changes = {k: body[k] for k in allowed if k in body}
        if not changes:
            raise HTTPException(status_code=422, detail="No supported settings were provided.")
        if "default_currency" in changes and changes["default_currency"] != "INR":
            raise HTTPException(status_code=422, detail="Only INR is supported by the configured Razorpay checkout.")
        if "grace_period_days" in changes and (not isinstance(changes["grace_period_days"], int) or not 0 <= changes["grace_period_days"] <= 90):
            raise HTTPException(status_code=422, detail="grace_period_days must be an integer between 0 and 90.")
        if "customer_plan_visibility" in changes and not isinstance(changes["customer_plan_visibility"], bool):
            raise HTTPException(status_code=422, detail="customer_plan_visibility must be boolean.")
        if "invoice_prefix" in changes and (not isinstance(changes["invoice_prefix"], str) or not changes["invoice_prefix"].strip() or len(changes["invoice_prefix"].strip()) > 12):
            raise HTTPException(status_code=422, detail="invoice_prefix must contain 1 to 12 characters.")
        if "renewal_rule" in changes and changes["renewal_rule"] not in {"after_expiry", "from_today"}:
            raise HTTPException(status_code=422, detail="renewal_rule must be after_expiry or from_today.")
        if "renewals_due_days" in changes and (not isinstance(changes["renewals_due_days"], int) or not 1 <= changes["renewals_due_days"] <= 90):
            raise HTTPException(status_code=422, detail="renewals_due_days must be an integer between 1 and 90.")
        changes.update({"key": "default", "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": str(user.get("id") or user.get("email") or "unknown")})
        await db["billbook_subscription_settings"].update_one({"key": "default"}, {"$set": changes}, upsert=True)
        result = {k: v for k, v in changes.items() if k != "key"}
        result["razorpay_configured"] = bool(os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET") and os.environ.get("RAZORPAY_WEBHOOK_SECRET"))
        return _ok(result, "Subscription settings saved")

    @router.get("/admin/billbook/subscribers")
    async def admin_subscribers(user: dict = Depends(get_current_user), limit: int = 100, skip: int = 0, status: str = "", search: str = ""):
        _require_admin(user); limit = max(1, min(limit, 500)); query = {}
        if search.strip():
            query["$or"] = [{"customer_id": {"$regex": search.strip(), "$options": "i"}}, {"customer_name": {"$regex": search.strip(), "$options": "i"}}, {"full_name": {"$regex": search.strip(), "$options": "i"}}, {"business_name": {"$regex": search.strip(), "$options": "i"}}, {"plan_name": {"$regex": search.strip(), "$options": "i"}}, {"plan_id": {"$regex": search.strip(), "$options": "i"}}]
        # Filter by the effective business state, not only the stored status field:
        # a stored "active" record can be expired according to its expiry/grace rules.
        records = [x async for x in db["billbook_subscriptions"].find(query, {"_id": 0}).sort("created_at", -1)]
        for item in records:
            item["state"] = subscription_state(item, datetime.now(timezone.utc))
        if status.strip():
            records = [x for x in records if str(x.get("state", "")).lower() == status.strip().lower()]
        total = len(records); start = max(0, skip)
        return _ok({"items": records[start:start + limit], "total": total, "limit": limit, "skip": start})

    @router.get("/admin/billbook/payments")
    async def admin_subscription_payments(user: dict = Depends(get_current_user), limit: int = 100, skip: int = 0, status: str = "", search: str = ""):
        _require_admin(user); limit = max(1, min(limit, 500)); needle = search.strip().lower()
        query = {"source": "billbook_subscription"}
        if status: query["status"] = {"$regex": f"^{status.strip()}$", "$options": "i"}
        if needle:
            query["$or"] = [{"customer_id": {"$regex": search.strip(), "$options": "i"}}, {"customer": {"$regex": search.strip(), "$options": "i"}}, {"payment_id": {"$regex": search.strip(), "$options": "i"}}, {"reference_no": {"$regex": search.strip(), "$options": "i"}}, {"plan_name": {"$regex": search.strip(), "$options": "i"}}, {"service": {"$regex": search.strip(), "$options": "i"}}, {"order_id": {"$regex": search.strip(), "$options": "i"}}]
        rows = [x async for x in db["erp_payments"].find(query, {"_id": 0})]
        mirrored_orders = {str(x.get("order_id")) for x in rows if x.get("order_id")}
        attempts = [x async for x in db["billbook_subscriptions"].find({}, {"_id": 0})]
        for sub in attempts:
            state = str(sub.get("status", "")).lower()
            if state not in {"pending", "failed", "cancelled", "canceled", "processing", "created"}:
                continue
            if sub.get("order_id") and str(sub.get("order_id")) in mirrored_orders:
                continue
            row = {**sub, "payment_id": sub.get("payment_id") or sub.get("order_id"), "customer": sub.get("business_name") or sub.get("full_name") or sub.get("customer_id"), "service": f"My BillBook subscription - {sub.get('plan_name') or sub.get('plan_id') or ''}", "total": sub.get("amount"), "mode": "Razorpay", "txn_date": sub.get("created_at"), "source": "billbook_subscription_attempt"}
            if status and state != status.strip().lower():
                continue
            if needle and not any(needle in str(row.get(k, "")).lower() for k in ("customer_id", "customer", "payment_id", "order_id", "plan_name", "service")):
                continue
            rows.append(row)
        rows.sort(key=lambda x: str(x.get("txn_date") or x.get("created_at") or ""), reverse=True)
        total = len(rows); start = max(0, skip)
        return _ok({"items": rows[start:start + limit], "total": total, "limit": limit, "skip": start})

    # ------------------------------ guest checkout and trial workflows ------------------------------
    @router.post("/billbook/guest-checkout")
    async def guest_checkout(body: dict = Body(...)):
        """Create a guest checkout without granting access; the random claim token is returned once."""
        errors = validate_customer_details(body)
        if errors:
            raise HTTPException(status_code=422, detail={"message": "Please correct the highlighted fields.", "fields": errors})
        plan_id = str(body.get("plan_id") or "")
        plan = await _plan(plan_id)
        if plan.get("plan_type", "paid") in {"trial", "free_trial"}:
            raise HTTPException(status_code=422, detail="Free trials must use the dedicated trial activation workflow.")
        if not plan["configured"] or not plan.get("duration_valid") or not plan.get("price") or plan["price"] <= 0:
            raise HTTPException(status_code=409, detail=plan.get("configuration_issue") or "This paid plan is not available.")
        client, key_id, _secret = rzp()
        if not client:
            key_id_set = bool(os.environ.get("RAZORPAY_KEY_ID", "").strip())
            key_secret_set = bool(os.environ.get("RAZORPAY_KEY_SECRET", "").strip())
            if not key_id_set or not key_secret_set:
                raise HTTPException(status_code=503, detail="Online payments are not configured on this server. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in the backend environment, then restart/redeploy the backend.")
            raise HTTPException(status_code=503, detail="Razorpay could not be initialized. Confirm the razorpay Python package is installed and the backend environment contains valid Razorpay test/live credentials.")
        details = normalized_details(body)
        now = datetime.now(timezone.utc)
        checkout_id = f"GBC-{uuid.uuid4().hex.upper()}"
        token = uuid.uuid4().hex + uuid.uuid4().hex
        import hashlib
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        guest_cid = f"guest:{checkout_id}"
        sub_id = f"BBS-{uuid.uuid4().hex[:12].upper()}"
        amount_paise = int(round(float(plan["price"]) * 100))
        try:
            order = client.order.create({"amount": amount_paise, "currency": "INR", "payment_capture": 1,
                "receipt": sub_id, "notes": {"checkout_id": checkout_id, "subscription_id": sub_id, "product": "my_billbook_guest"}})
        except Exception:
            raise HTTPException(status_code=502, detail="Unable to create the payment order. Please try again.")
        await db["billbook_guest_checkouts"].insert_one({"id": checkout_id, "token_hash": token_hash, "plan_id": plan_id,
            "subscription_id": sub_id, "order_id": order["id"], "email": details.get("email", "").strip().lower(),
            "mobile": details.get("mobile"), "details": details, "status": "pending", "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=2)).isoformat()})
        await db["billbook_subscriptions"].insert_one({"id": sub_id, "customer_id": guest_cid, "guest_checkout_id": checkout_id,
            "plan_id": plan["id"], "plan_name": plan["name"], "amount": plan["price"], "currency": "INR",
            "duration_days_at_purchase": int(plan.get("duration_days") or 0), "duration_months_at_purchase": int(plan.get("duration_months") or 0),
            "features_at_purchase": plan.get("features", []), "order_id": order["id"], "status": "pending", "payment_id": None,
            "started_at": None, "expires_at": None, "created_at": now.isoformat(), "failure_reason": None, **details})
        return _ok({"checkout_id": checkout_id, "claim_token": token, "order_id": order["id"], "amount": amount_paise,
            "currency": "INR", "key_id": key_id, "plan": {"id": plan["id"], "name": plan["name"], "price": plan["price"]},
            "subscription_id": sub_id, "prefill": {"name": details.get("full_name"), "email": details.get("email"), "contact": details.get("mobile")}}, "Guest checkout created")

    @router.post("/billbook/guest-checkout/verify")
    async def verify_guest_checkout(body: dict = Body(...)):
        checkout_id = str(body.get("checkout_id") or "")
        token = str(body.get("claim_token") or "")
        if not checkout_id or len(token) < 32:
            raise HTTPException(status_code=400, detail="Checkout reference is invalid or expired.")
        import hashlib, hmac
        rec = await db["billbook_guest_checkouts"].find_one({"id": checkout_id}, {"_id": 0})
        if not rec or not hmac.compare_digest(rec.get("token_hash", ""), hashlib.sha256(token.encode()).hexdigest()):
            raise HTTPException(status_code=404, detail="Checkout reference is invalid or expired.")
        if (_parse_dt(rec.get("expires_at")) or datetime.min.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Checkout reference expired. Contact support to recover a verified payment.")
        order_id = str(body.get("razorpay_order_id") or "")
        payment_id = str(body.get("razorpay_payment_id") or "")
        signature = str(body.get("razorpay_signature") or "")
        if order_id != rec.get("order_id"):
            raise HTTPException(status_code=400, detail="Payment order does not match this checkout.")
        sub = await db["billbook_subscriptions"].find_one({"order_id": order_id, "guest_checkout_id": checkout_id}, {"_id": 0})
        if not sub:
            raise HTTPException(status_code=404, detail="Checkout subscription not found.")
        if sub.get("status") != "active":
            client, _kid, secret = rzp()
            if not client:
                raise HTTPException(status_code=503, detail="Payment verification is unavailable.")
            if not verify_checkout_signature(order_id, payment_id, signature, secret):
                raise HTTPException(status_code=400, detail="Payment signature verification failed.")
            try:
                payment = client.payment.fetch(payment_id)
            except Exception:
                return _ok({"state": "pending", "checkout_id": checkout_id}, "Payment confirmation is pending")
            if payment.get("order_id") != order_id or int(payment.get("amount", -1)) != int(round(float(sub["amount"]) * 100)):
                raise HTTPException(status_code=400, detail="Payment amount or order does not match.")
            if payment.get("status") == "failed":
                await db["billbook_subscriptions"].update_one({"order_id": order_id, "status": {"$ne": "active"}}, {"$set": {"status": "failed", "payment_id": payment_id, "failure_reason": "Payment failed"}})
                await db["billbook_guest_checkouts"].update_one({"id": checkout_id, "status": "pending"}, {"$set": {"status": "failed"}})
                return _ok({"state": "failed", "checkout_id": checkout_id}, "Payment failed")
            if payment.get("status") != "captured":
                return _ok({"state": "pending", "checkout_id": checkout_id}, "Payment confirmation is pending")
            sub = await _activate(order_id, payment_id)
            await db["billbook_guest_checkouts"].update_one({"id": checkout_id, "status": "pending"}, {"$set": {"status": "paid", "payment_id": payment_id, "paid_at": datetime.now(timezone.utc).isoformat()}})
        return _ok({"state": subscription_state(sub), "checkout_id": checkout_id, "claim_token": token,
            "login_required": True, "customer_email": rec.get("email")}, "Payment verified. Sign in to securely link your subscription.")

    @router.post("/billbook/guest-checkout/claim")
    async def claim_guest_checkout(body: dict = Body(...), user: dict = Depends(get_current_user)):
        cid = _require_customer(user)
        checkout_id, token = str(body.get("checkout_id") or ""), str(body.get("claim_token") or "")
        import hashlib, hmac
        rec = await db["billbook_guest_checkouts"].find_one({"id": checkout_id}, {"_id": 0})
        if not rec or not hmac.compare_digest(rec.get("token_hash", ""), hashlib.sha256(token.encode()).hexdigest()):
            raise HTTPException(status_code=404, detail="Checkout reference is invalid.")
        if rec.get("status") != "paid":
            raise HTTPException(status_code=409, detail="Only a verified paid checkout can be linked.")
        user_email = str(user.get("email") or "").strip().lower()
        user_mobile = re.sub(r"\D", "", str(user.get("mobile") or ""))
        rec_mobile = re.sub(r"\D", "", str(rec.get("mobile") or ""))
        meta = user.get("meta") if isinstance(user.get("meta"), dict) else {}
        # Never trust matching contact text alone. Email linking requires an explicit
        # verified-email flag; mobile linking requires a verified-phone flag or the
        # existing NTAXCO OTP-login marker set by the verified OTP onboarding flow.
        email_verified = bool(user.get("email_verified") or user.get("verified_email") or meta.get("email_verified"))
        mobile_verified = bool(user.get("mobile_verified") or user.get("phone_verified") or meta.get("mobile_verified") or meta.get("otp_login"))
        email_match = bool(email_verified and rec.get("email") and user_email and hmac.compare_digest(str(rec["email"]).strip().lower(), user_email))
        mobile_match = bool(mobile_verified and rec_mobile and user_mobile and hmac.compare_digest(rec_mobile, user_mobile))
        if not (email_match or mobile_match):
            raise HTTPException(status_code=403, detail="Sign in with an account whose email or mobile number is verified and matches the checkout details.")
        old_cid = f"guest:{checkout_id}"
        result = await db["billbook_subscriptions"].update_one({"guest_checkout_id": checkout_id, "customer_id": old_cid, "status": "active"}, {"$set": {"customer_id": cid, "linked_at": datetime.now(timezone.utc).isoformat(), "linked_customer_id": cid}})
        if getattr(result, "matched_count", 0) == 0:
            existing = await db["billbook_subscriptions"].find_one({"guest_checkout_id": checkout_id, "customer_id": cid, "status": "active"}, {"_id": 0})
            if not existing:
                raise HTTPException(status_code=409, detail="Checkout is already linked or is not eligible for linking.")
        await db["billbook_guest_checkouts"].update_one({"id": checkout_id, "status": "paid"}, {"$set": {"status": "linked", "linked_customer_id": cid, "linked_at": datetime.now(timezone.utc).isoformat()}, "$unset": {"token_hash": ""}})
        return _ok({"linked": True, "subscription_id": rec.get("subscription_id")}, "Subscription linked to your account")

    @router.get("/billbook/trial-plans")
    async def list_trial_plans():
        plans = [p async for p in db["billbook_plans"].find({"plan_type": {"$in": ["trial", "free_trial"]}, "active": True, "published": True, "archived": {"$ne": True}}, {"_id": 0})]
        safe = []
        for p in plans:
            days, months = int(p.get("duration_days") or 0), int(p.get("duration_months") or 0)
            if bool(days) != bool(months) and float(p.get("price", 0) or 0) == 0:
                safe.append({k: p.get(k) for k in ("id", "name", "description", "duration_days", "duration_months", "billing_period", "features", "eligibility_rules")})
        return _ok({"plans": safe})

    @router.post("/billbook/trials/activate")
    async def activate_trial(body: dict = Body(...), user: dict = Depends(get_current_user)):
        cid = _require_customer(user)
        plan_id = str(body.get("plan_id") or "")
        plan = await db["billbook_plans"].find_one({"id": plan_id, "plan_type": {"$in": ["trial", "free_trial"]}, "active": True, "published": True, "archived": {"$ne": True}}, {"_id": 0})
        if not plan or float(plan.get("price", 0) or 0) != 0:
            raise HTTPException(status_code=404, detail="Free-trial plan is not available.")
        days, months = int(plan.get("duration_days") or 0), int(plan.get("duration_months") or 0)
        if bool(days) == bool(months):
            raise HTTPException(status_code=422, detail="Trial plan must have exactly one valid duration.")
        rules = plan.get("eligibility_rules") or {}
        now = datetime.now(timezone.utc)

        # Resume a valid trial before applying historical eligibility rules. This makes
        # retries/refreshes idempotent and prevents an active trial from being reported
        # as already consumed or from creating a second subscription.
        active_trial = await db["billbook_subscriptions"].find_one(
            {"customer_id": cid, "subscription_type": "free_trial", "status": "active"},
            {"_id": 0},
        )
        if active_trial and subscription_state(active_trial, now) == "active":
            return _ok({"subscription": _public_sub(active_trial), "trial": True, "resumed": True},
                       "Your free trial is already active")

        # Repeat-trial restriction is opt-in only. Legacy plans without this setting
        # allow a new trial after an expired/failed/incomplete historical activation.
        if rules.get("one_per_customer", False):
            prior = await db["billbook_subscriptions"].find_one(
                {"customer_id": cid, "subscription_type": "free_trial"}, {"_id": 1}
            )
            if prior:
                raise HTTPException(status_code=409, detail="This trial plan is configured for one use per customer.")

        current = await _latest_subscription(cid)
        if subscription_state(current, now) == "active":
            raise HTTPException(status_code=409, detail="An active subscription already exists for this account.")
        expires = compute_expiry(now, {"duration_days": days, "duration_months": months})
        trial_id = f"BBT-{uuid.uuid4().hex[:12].upper()}"
        record = {"id": trial_id, "customer_id": cid, "plan_id": plan_id, "plan_name": plan.get("name"), "subscription_type": "free_trial", "amount": 0, "currency": "INR", "status": "active", "started_at": now.isoformat(), "expires_at": expires.isoformat(), "created_at": now.isoformat(), "activated_by": cid, "eligibility_snapshot": rules, "features_at_purchase": plan.get("features", [])}
        try:
            await db["billbook_subscriptions"].insert_one(record)
        except Exception as exc:
            # Concurrent retries may race. Prefer returning the valid active trial if one
            # was created by the other request; do not convert this into a misleading
            # historical-eligibility error.
            if "duplicate key" in str(exc).lower() or getattr(exc, "code", None) == 11000:
                existing = await db["billbook_subscriptions"].find_one(
                    {"customer_id": cid, "subscription_type": "free_trial", "status": "active"},
                    {"_id": 0},
                )
                if existing and subscription_state(existing, datetime.now(timezone.utc)) == "active":
                    return _ok({"subscription": _public_sub(existing), "trial": True, "resumed": True},
                               "Your free trial is already active")
                raise HTTPException(status_code=409, detail="Trial activation conflicted with another request. Please retry.")
            raise
        return _ok({"subscription": _public_sub(record), "trial": True}, "Free trial activated")

    @router.get("/admin/billbook/payment-events")
    async def admin_payment_events(user: dict = Depends(get_current_user), limit: int = 100, skip: int = 0, status: str = ""):
        _require_admin(user); query = {"product": "my_billbook"}
        if status: query["status"] = status
        limit = max(1, min(limit, 500)); rows = [x async for x in db["billbook_payment_events"].find(query, {"_id": 0, "raw_payload": 0}).sort("received_at", -1).skip(max(0, skip)).limit(limit)]
        return _ok({"items": rows, "total": await db["billbook_payment_events"].count_documents(query), "limit": limit, "skip": max(0, skip)})

    @router.get("/admin/billbook/reports")
    async def admin_billbook_reports(user: dict = Depends(get_current_user), start_date: str = "", end_date: str = ""):
        _require_admin(user)
        subs = [x async for x in db["billbook_subscriptions"].find({}, {"_id": 0})]
        payments = [x async for x in db["erp_payments"].find({"source": "billbook_subscription", "status": {"$regex": "^(success|successful|paid|captured|completed)$", "$options": "i"}}, {"_id": 0})]
        now = datetime.now(timezone.utc)
        active = [x for x in subs if subscription_state(x, now) == "active"]
        trials = [x for x in subs if x.get("subscription_type") == "free_trial"]
        paid = [x for x in payments if float(x.get("total", x.get("amount", 0)) or 0) > 0]
        return _ok({"summary": {"subscriptions_total": len(subs), "active_subscriptions": len(active), "expired_subscriptions": sum(subscription_state(x, now) == "expired" for x in subs), "free_trials": len(trials), "active_trials": sum(subscription_state(x, now) == "active" for x in trials), "verified_paid_transactions": len(paid), "verified_paid_revenue": round(sum(float(x.get("total", x.get("amount", 0)) or 0) for x in paid), 2), "currency": "INR", "revenue_definition": "Sum of existing mirrored BillBook subscription payment records marked successful/paid/captured/completed; excludes zero-value trials. Refunds are not netted unless represented in the source record."}, "plans": [{"plan_id": p, "count": sum(1 for x in subs if x.get("plan_id") == p)} for p in sorted({x.get("plan_id") for x in subs if x.get("plan_id")})]})

    return router


async def customer_has_active_subscription(db, customer_id: str) -> bool:
    now = datetime.now(timezone.utc)
    async for sub in db["billbook_subscriptions"].find({"customer_id": customer_id, "status": "active"}, {"_id": 0}):
        if subscription_state(sub, now) == "active":
            return True
    return False
