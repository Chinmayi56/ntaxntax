// Run:  node --test customer/scripts/billbook-subscription.test.mjs
// Pure-logic + source-level checks (no browser, no npm install needed).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { validateDetails, safeNextPath, normalizeMobile } from "../src/lib/billbookValidation.js";

const src = (p) => readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", p), "utf8");
const VALID = { full_name: "Asha Rao", mobile: "9876543210", email: "asha@example.com", business_name: "Asha Traders", business_type: "LLP", gstin: "" };
const TYPES = ["LLP", "Other"];

test("valid details pass; GSTIN is optional", () => {
  assert.deepEqual(validateDetails(VALID, TYPES), {});
  assert.deepEqual(validateDetails({ ...VALID, gstin: "36abcde1234f1z5" }, TYPES), {});
});
test("all required fields are enforced", () => {
  assert.deepEqual(Object.keys(validateDetails({}, TYPES)).sort(), ["business_name", "business_type", "email", "full_name", "mobile"]);
});
test("email, Indian mobile and GSTIN formats", () => {
  assert.ok(validateDetails({ ...VALID, email: "nope" }, TYPES).email);
  assert.ok(validateDetails({ ...VALID, mobile: "12345" }, TYPES).mobile);
  assert.ok(validateDetails({ ...VALID, mobile: "5876543210" }, TYPES).mobile);
  assert.equal(normalizeMobile("+91 98765-43210"), "9876543210");
  assert.deepEqual(validateDetails({ ...VALID, mobile: "+91 98765 43210" }, TYPES), {});
  assert.ok(validateDetails({ ...VALID, gstin: "BAD" }, TYPES).gstin);
  assert.ok(validateDetails({ ...VALID, business_type: "Hacker" }, TYPES).business_type);
});
test("post-login next path only allows /billbook/ pages", () => {
  assert.equal(safeNextPath("/billbook/details?plan=1_month"), "/billbook/details?plan=1_month");
  for (const bad of ["https://evil.test", "//evil.test", "/customer/dashboard", "/billbook/../admin", "javascript:alert(1)", "", null, undefined]) assert.equal(safeNextPath(bad), null, String(bad));
});
test("routes, selector hook-up and login destination", () => {
  const app = src("App.js");
  assert.match(app, /path="\/billbook\/plans" element=\{<BillBookPlansPage \/>\}/);
  assert.match(app, /path="\/billbook\/details" element=\{<BillBookDetailsPage \/>\}/);
  assert.match(app, /path="\/billbook\/payment-status" element=\{<BillBookProtected>/);
  assert.match(app, /path="\/my-billbook\/\*" element=\{<BillBookProtected>/); // unchanged
  assert.match(app, /loginPath="\/login\?portal=billbook"/);                   // unchanged
  const login = src("pages/auth/CustomerLoginPage.jsx");
  assert.ok(login.includes('navigate("/billbook/plans")'));                    // My BillBook -> plans
  for (const t of ["My Service", "My BillBook", "Tax & compliance", "Billing & business records", "Choose your workspace", "SECURE CUSTOMER ACCESS", "Back to website"]) assert.ok(login.includes(t), t);
});
test("subscription page: four-column grid, no hard-coded prices, no secrets, no storage of payment state", () => {
  const page = src("pages/billbook/BillBookSubscription.jsx");
  const css = src("pages/billbook/BillBookSubscription.css");
  const lib = src("lib/billbookSubscription.js");
  assert.match(css, /grid-template-columns:repeat\(4,/);
  assert.ok(!/₹\s?\d|\bprice:\s*\d|amount:\s*\d/.test(page + lib), "no literal prices");
  assert.ok(!/key_secret|RAZORPAY_KEY_SECRET/i.test(page + lib));
  assert.ok(!/localStorage/.test(page + lib), "no local-storage payment records");
  assert.ok(lib.includes("/billbook/subscription/verify") && lib.includes("razorpay_signature"));
  // the activation message is shown only from the backend-reported state
  assert.ok(page.includes('result.state === "active"'));
  assert.ok(page.includes("LOGIN_PATH") && page.includes('"/login?portal=billbook"'));
});

// ---------------------------------------------------------------- plans response handling
import { parsePlansResponse, adminActionDiagnostics, PlansResponseError } from "../src/lib/billbookPlans.js";

const OK_BODY = {
  success: true, message: "OK",
  data: {
    plans: [{ id: "starter", name: "Starter", price: 499, currency: "INR", billing_period: "30 days", configured: true }],
    business_types: ["LLP"], missing_configuration: [], diagnostics: [],
  },
};
test("a valid response keeps every returned plan with its real price, currency and period", () => {
  const parsed = parsePlansResponse(OK_BODY);
  assert.equal(parsed.plans.length, 1);
  assert.deepEqual([parsed.plans[0].price, parsed.plans[0].currency, parsed.plans[0].billing_period], [499, "INR", "30 days"]);
  assert.deepEqual(parsed.plans[0].features, []); // missing features never crash the page
  assert.equal(parsed.hidden, false);
});
test("a genuinely empty plan list is a valid result, not an error", () => {
  const parsed = parsePlansResponse({ success: true, message: "OK", data: { plans: [], missing_configuration: [] } });
  assert.deepEqual(parsed.plans, []);
});
test("unexpected bodies (HTML with HTTP 200, wrong shape, success=false) are errors, never 'No plans'", () => {
  for (const bad of ["<!doctype html><html></html>", "", null, undefined, {}, { success: true }, { success: true, data: {} },
    { success: true, data: { plans: "nope" } }, { success: false, data: { plans: [] } }, [], 42]) {
    assert.throws(() => parsePlansResponse(bad), PlansResponseError, JSON.stringify(bad));
  }
});
test("only plans an administrator meant to be live are shown as configuration problems", () => {
  const parsed = parsePlansResponse({ success: true, data: { plans: [], missing_configuration: ["A", "B"], diagnostics: [
    { id: "a", message: "A", needs_admin_configuration: true }, { id: "b", message: "B", needs_admin_configuration: false }] } });
  assert.deepEqual(adminActionDiagnostics(parsed).map((d) => d.id), ["a"]);
  // older backends without structured diagnostics still surface their messages
  const legacy = parsePlansResponse({ success: true, data: { plans: [], missing_configuration: ["X"] } });
  assert.deepEqual(adminActionDiagnostics(legacy).map((d) => d.message), ["X"]);
});
test("admin-controlled hiding of plans is reported separately", () => {
  assert.equal(parsePlansResponse({ success: true, data: { plans: [], plans_hidden: true } }).hidden, true);
});
test("the customer plans page reads only the backend list: distinct states, no hard-coded fallback plans", () => {
  const page = src("pages/billbook/BillBookSubscription.jsx");
  const lib = src("lib/billbookSubscription.js");
  assert.ok(lib.includes('api.get("/billbook/plans")') && lib.includes("parsePlansResponse"));
  assert.ok(page.includes("No plans available") && page.includes("Could not load plans") && page.includes("Some plans are not configured yet"));
  assert.ok(page.includes("e instanceof PlansResponseError"), "a malformed response must reach the error state");
  assert.ok(!/plans:\s*\[\s*\{/.test(page + lib), "no embedded fallback plan list");
});
