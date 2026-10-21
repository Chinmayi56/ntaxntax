# Prime Customer workflow

## Data

- `erp_prime_services` remains the source of current public price, price mode, availability, working days and opening/closing times.
- Existing `erp_prime_service_bookings` is preserved. Guest bookings now snapshot the selected service name, price, currency and working hours and link to `prime_customer_id`.
- `erp_prime_customers` stores guest customer-management records separately from authenticated ERP accounts. Guest records are matched by normalized Indian mobile number and have a unique `prime_customer_reference`, booking relationship, service IDs, count, status and admin notes.
- Booking reference and Prime Customer reference indexes are unique; customer ID and mobile lookups are indexed.

## APIs

Public: `GET /api/public/prime-services`, `POST /api/public/prime-service-bookings`.

Admin (existing authentication and admin-role check required): `GET /api/admin/prime-customers`, `GET /api/admin/prime-customers/stats`, `GET /api/admin/prime-customers/{id}`, `GET /api/admin/prime-customers/{id}/bookings`, `PATCH /api/admin/prime-customers/{id}`, `POST /api/admin/prime-service-bookings/{id}/assign`. Existing `GET /api/admin/prime-service-bookings` and `PATCH /api/admin/prime-service-bookings/{id}` remain available.

Customer status values: `New`, `Active`, `Inactive`. Booking status values continue to use the existing Prime Services conventions (`Pending`, `Under Review`, `Contacted`, `In Progress`, `Completed`, `Rejected`, `Cancelled`).

## Deployment notes

Indexes are ensured when the booking flow first touches the relevant collections. No migration drops or renames the existing booking collection. Keep the existing `MONGO_URL`, `DB_NAME`, `JWT_SECRET`, CORS and authentication configuration. For multi-worker production deployments, enforce public booking rate limits at the API gateway as well as the best-effort in-process guard.
