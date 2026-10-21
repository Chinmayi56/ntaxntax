# My BillBook integration

- Customer route: `/my-billbook/*` (the BillBook screens use an isolated in-memory router beneath this NTAXCO route).
- Login: existing NTAXCO customer email/password or mobile OTP flow. No BillBook login or demo account is used.
- API: `GET /api/billbook/data` and `PUT /api/billbook/data`; both require the existing NTAXCO Bearer access token and customer role.
- Storage: MongoDB collection `billbook_customer_data`, one document per immutable NTAXCO `customer_id`; every operation scopes by the authenticated identity on the server. Existing NTAXCO collections are not modified.
- New customer records start empty; the BillBooks sample/demo business records are not seeded into customer accounts.
- Frontend development continues to use the existing customer `.env` / `REACT_APP_API_URL` configuration and existing `/api` proxy. No new secret is required.

Note: the supplied BillBooks code describes some integrations and notifications as demo-only and its Google Drive integration still depends on its own configuration. This integration persists the BillBook `UserData` document in MongoDB, but binary attachments should be stored in an object-storage service rather than embedded in this JSON document.

## Subscription gate
`/api/billbook/data` now requires an active subscription (see `BILLBOOK_SUBSCRIPTION.md`). Set `BILLBOOK_SUBSCRIPTION_REQUIRED=false` to disable the gate.
