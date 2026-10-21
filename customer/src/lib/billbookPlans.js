// Pure helpers (no imports) for reading GET /api/billbook/plans. Kept dependency-free so they can be
// unit-tested with `node --test` and so the page can tell a FAILED request from a genuinely EMPTY plan list.

export class PlansResponseError extends Error {}

/**
 * Validate and normalise the backend body `{ success, message, data: { plans, ... } }`.
 * Throws PlansResponseError for anything else (for example an HTML page served with HTTP 200 when the
 * dev proxy / API base URL is wrong) so the UI shows an error state, never "No plans available".
 */
export function parsePlansResponse(body) {
  const data = body && typeof body === "object" ? body.data : null;
  if (!body || typeof body !== "object" || body.success !== true || !data || typeof data !== "object" || !Array.isArray(data.plans)) {
    throw new PlansResponseError("The subscription service returned an unexpected response. Please try again.");
  }
  const diagnostics = Array.isArray(data.diagnostics) ? data.diagnostics : [];
  return {
    plans: data.plans.filter((p) => p && typeof p === "object" && p.id).map((p) => ({
      ...p,
      features: Array.isArray(p.features) ? p.features : [],
    })),
    missing: Array.isArray(data.missing_configuration) ? data.missing_configuration : [],
    diagnostics,
    types: Array.isArray(data.business_types) ? data.business_types : [],
    hidden: data.plans_hidden === true,
    message: typeof body.message === "string" ? body.message : "",
  };
}

/** Plans an administrator intended to be live (active + published) but that have invalid price/duration. */
export function adminActionDiagnostics(parsed) {
  if (parsed.diagnostics.length) return parsed.diagnostics.filter((d) => d && d.needs_admin_configuration);
  return parsed.missing.map((message) => ({ message }));
}
