# My BillBook subscription flow

**Workspace selection → Plans → Customer details → Razorpay payment → Verified activation → `/login?portal=billbook`**

## What was found in the project (before changes)
- No subscription, plan, price or webhook code existed. The only Razorpay code is `POST /api/payments/create-order` and `/api/payments/verify` (invoice/booking payments) in `backend/erp.py`; it falls back to a **demo/test mock** when keys are missing and skips signature checks without keys. It was left untouched.
- No plan prices exist anywhere in the project, so **none were invented** (see "Configuring prices").
- Admin Payments reads the `erp_payments` collection. Verified subscription payments are mirrored there (no second history).

## Files
New
- `backend/billbook_rules.py` – plan catalog (names/durations only), price parsing, validation, signature checks, expiry maths.
- `backend/billbook_subscription.py` – API: plans, status, checkout, verify, cancel-attempt, webhook, admin price config.
- `backend/tests/test_billbook_subscription.py` – 23 tests.
- `customer/src/pages/billbook/BillBookSubscription.jsx` + `.css` – plans, details, payment-status pages.
- `customer/src/lib/billbookSubscription.js`, `customer/src/lib/billbookValidation.js`
- `customer/scripts/billbook-subscription.test.mjs`

Modified
- `backend/server.py` – registers the router; `/api/billbook/data` GET/PUT now require an active subscription (403 `subscription_required`).
- `backend/requirements.txt` – `razorpay>=1.4.2` (already imported lazily by existing code; now listed).
- `customer/src/App.js` – routes `/billbook`, `/billbook/plans`, `/billbook/details`, `/billbook/payment-status`.
- `customer/src/pages/auth/CustomerLoginPage.jsx` – My BillBook → plans; optional safe `?next=/billbook/...`.
- `customer/src/billbook/BillBookApp.tsx` – `subscription_required` → plans page.

## API (all under `/api`)
| Method | Path | Notes |
|---|---|---|
| GET | `/billbook/plans` | four plans; `price` null + `configuration_issue` when not configured |
| GET | `/billbook/subscription/status` | customer only; derived state: none/active/expired/pending/failed/cancelled |
| POST | `/billbook/subscription/checkout` | validates details + plan, server-side price, creates Razorpay order, stores pending subscription |
| POST | `/billbook/subscription/verify` | checks HMAC signature **and** fetches the payment from Razorpay (order, amount, `captured`) before activating |
| GET | `/billbook/subscription/order/{order_id}` | owner-only status polling |
| POST | `/billbook/subscription/cancel-attempt` | marks an unpaid pending order cancelled |
| POST | `/billbook/subscription/webhook` | HMAC-verified (`RAZORPAY_WEBHOOK_SECRET`), handles `payment.captured`/`order.paid`/`payment.failed` |
| PUT | `/admin/billbook/plans/{plan_id}` | admin sets `price`, `active`, `features` |

New collections: `billbook_plans` (prices), `billbook_subscriptions`. Existing: `erp_payments` (mirror), `billbook_customer_data`.

## Configuring prices (required before launch)
Plan ids: `15_days`, `1_month`, `6_months`, `1_year`. Either:
- admin API: `PUT /api/admin/billbook/plans/1_month` with `{"price": <rupees>}`, or
- env: `BILLBOOK_PRICE_15_DAYS`, `BILLBOOK_PRICE_1_MONTH`, `BILLBOOK_PRICE_6_MONTHS`, `BILLBOOK_PRICE_1_YEAR`.
Until set, a plan shows "Price not configured" and cannot be bought.

## Environment
- `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` (existing). Without them checkout returns 503 – there is no mock activation.
- `RAZORPAY_WEBHOOK_SECRET` (new) and a Razorpay webhook pointing to `https://<api>/api/billbook/subscription/webhook` for `payment.captured` and `payment.failed`.
- `BILLBOOK_SUBSCRIPTION_REQUIRED` (default `true`; set `false` only to keep existing customers on free access during migration).

## Business rules implemented
- Expiry: 15 days = +15 days; 1 month / 6 months / 1 year = calendar months (month-end clamped).
- Renewal while active: new term starts when the current one ends. Same active plan needs `renew=true`.
- Pending orders for the same plan are reused (no duplicate orders); failed/cancelled attempts allow a new attempt.
- A customer can only verify/poll their own orders; amount, customer and status are never taken from the browser.

## Run / test
```
cd backend && python -m unittest tests.test_billbook_subscription -v      # or pytest
node --test customer/scripts/billbook-subscription.test.mjs
cd customer && npm install && npm start     # CRA/craco; admin: cd admin && npm install && npm start
```
