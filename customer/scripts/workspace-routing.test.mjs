// Run:  node --test customer/scripts/workspace-routing.test.mjs
// Pure-logic + source-level checks (no browser, no npm install needed).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { normalizePortal, postLoginPath, postSignupPath } from "../src/lib/workspace.js";

const src = (p) => readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", p), "utf8");

test("portal normalisation: default + unknown values -> service", () => {
  assert.equal(normalizePortal(undefined), "service");
  assert.equal(normalizePortal(null), "service");
  assert.equal(normalizePortal(""), "service");
  assert.equal(normalizePortal("service"), "service");
  assert.equal(normalizePortal("admin"), "service");
  assert.equal(normalizePortal("<script>"), "service");
  assert.equal(normalizePortal(["ledger", "x"]), "service");
});

test("portal normalisation: ledger is case/space tolerant", () => {
  assert.equal(normalizePortal("ledger"), "ledger");
  assert.equal(normalizePortal(" LEDGER "), "ledger");
});

test("post-login destinations", () => {
  assert.equal(postLoginPath("service"), "/customer/dashboard");
  assert.equal(postLoginPath("ledger"), "/ledger/dashboard");
  assert.equal(postLoginPath("bogus"), "/customer/dashboard");
  assert.equal(postSignupPath("ledger"), "/ledger/dashboard");
  assert.equal(postSignupPath("service"), "/customer");
});

test("ledger login never routes through the My Service dashboard", () => {
  assert.notEqual(postLoginPath("ledger"), postLoginPath("service"));
  assert.ok(!postLoginPath("ledger").startsWith("/customer"));
});

test("App.js routes exist and point at real pages", () => {
  const app = src("App.js");
  assert.match(app, /path="\/login" element=\{<CustomerLoginPage \/>\}/);
  assert.match(app, /path="\/ledger\/login" element=\{<Navigate to="\/login\?portal=ledger" replace \/>\}/);
  assert.match(app, /path="\/ledger\/dashboard" element=\{<LedgerProtected>/);
  assert.match(app, /path="dashboard" element=\{<CustomerDashboard \/>\}/);
  assert.match(app, /loginPath="\/ledger\/login"/);
  assert.ok(!/razorpay|stripe|checkout|subscription/i.test(app));
});

test("login page: selector, copy, footer and shared auth", () => {
  const page = src("pages/auth/CustomerLoginPage.jsx");
  for (const t of [
    "SECURE CUSTOMER ACCESS", "Choose your workspace", "My Service", "Tax & compliance", "My Ledger", "Financial workspace",
    "NTAXCO CUSTOMER PORTAL", "Your tax & compliance workspace.", "Nizam's Tax Consultancy",
    "NTAXCO LEDGER PORTAL", "Your financial workspace is next.",
    "My Ledger is being prepared as the next NTAXCO portal experience. Your existing customer authentication stays shared and secure.",
    "Welcome to NTAXCO", "Sign In", "Sign Up", "Mobile OTP", "Email address", "Back to website",
    "Secure authentication • Existing NTAXCO APIs • Customer data stays account-scoped",
  ]) assert.ok(page.includes(t), `missing: ${t}`);
  for (const s of ["GST", "Income Tax", "TDS", "Documents", "Returns", "Support"]) assert.ok(page.includes(`label: "${s}"`), s);
  assert.ok(!page.includes("TaX"), "typo regression");
  assert.ok(page.includes('normalizePortal(searchParams.get("portal"))'));
  assert.ok(page.includes("postLoginPath(portal)"));
  // reuses existing auth: no new endpoints / no token handling in the page
  assert.ok(!/localStorage|api\.(get|post)|axios|fetch\(/.test(page));
  assert.ok(!/razorpay|stripe|payu|cashfree|checkout/i.test(page));
});
