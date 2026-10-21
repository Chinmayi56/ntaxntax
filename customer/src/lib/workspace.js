// Workspace (portal) helpers for the shared NTAXCO customer login.
export const PORTAL_SERVICE = "service";
export const PORTAL_BILLBOOK = "billbook";
export const PORTAL_LEDGER = PORTAL_BILLBOOK; // legacy URL compatibility only
export const SERVICE_HOME = "/customer/dashboard";
export const BILLBOOK_HOME = "/my-billbook";
export const LEDGER_HOME = BILLBOOK_HOME;
export function normalizePortal(value) {
  const v = String(value ?? "").trim().toLowerCase();
  return [PORTAL_BILLBOOK, "ledger"].includes(v) ? PORTAL_BILLBOOK : PORTAL_SERVICE;
}
export function postLoginPath(portal) { return normalizePortal(portal) === PORTAL_BILLBOOK ? BILLBOOK_HOME : SERVICE_HOME; }
export function postSignupPath(portal) { return normalizePortal(portal) === PORTAL_BILLBOOK ? BILLBOOK_HOME : "/customer"; }
