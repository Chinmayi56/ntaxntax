// Pure validation helpers for the My BillBook subscription flow (no imports, unit-testable).
// The backend (billbook_rules.py) re-validates everything; this is only for inline feedback.
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MOBILE_REGEX = /^[6-9][0-9]{9}$/;
export const GSTIN_REGEX = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

export function normalizeMobile(value) {
  let m = String(value ?? "").replace(/[\s-]/g, "");
  if (m.startsWith("+91")) m = m.slice(3);
  else if (m.startsWith("91") && m.length === 12) m = m.slice(2);
  return m;
}

export function validateDetails(values, businessTypes = []) {
  const errors = {};
  const v = values || {};
  const name = String(v.full_name ?? "").trim();
  const mobile = normalizeMobile(v.mobile);
  const email = String(v.email ?? "").trim();
  const business = String(v.business_name ?? "").trim();
  const btype = String(v.business_type ?? "").trim();
  const gstin = String(v.gstin ?? "").trim().toUpperCase();
  if (!name) errors.full_name = "Full name is required.";
  else if (name.length > 120) errors.full_name = "Full name is too long.";
  if (!mobile) errors.mobile = "Mobile number is required.";
  else if (!MOBILE_REGEX.test(mobile)) errors.mobile = "Enter a valid 10-digit Indian mobile number.";
  if (!email) errors.email = "Email address is required.";
  else if (!EMAIL_REGEX.test(email)) errors.email = "Enter a valid email address.";
  if (!business) errors.business_name = "Business name is required.";
  else if (business.length > 160) errors.business_name = "Business name is too long.";
  if (!btype) errors.business_type = "Business type is required.";
  else if (businessTypes.length && !businessTypes.includes(btype)) errors.business_type = "Select a valid business type.";
  if (gstin && !GSTIN_REGEX.test(gstin)) errors.gstin = "Enter a valid 15-character GSTIN.";
  return errors;
}

// Only same-app subscription paths may be used as a post-login destination (no open redirects).
export function safeNextPath(value) {
  const v = String(value ?? "");
  if (!/^\/billbook\/[A-Za-z0-9_\-/?=&%.]*$/.test(v) || v.includes("//") || v.includes("..")) return null;
  return v;
}

export function formatDate(value) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
