// Run: node --test admin/scripts/billbook-admin-plans.test.mjs
// Source-level checks (no browser / npm install needed) for the Admin subscription-plan screen.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const page = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "pages", "admin", "BillBookAdmin.jsx"), "utf8");

test("the plan form submits the exact canonical fields the backend validates", () => {
  const payload = page.slice(page.indexOf("const payload={id,"), page.indexOf("if(!payload.name||!id)"));
  for (const field of ["id", "name", "description", "price", "currency", "billing_type", "duration_days", "duration_months", "active", "published", "features", "display_order"]) {
    assert.ok(new RegExp(`[{,]${field}[:,}]`).test(payload), `payload is missing ${field}`);
  }
  assert.ok(page.includes('const price=(form.plan_type==="free_trial"||form.plan_type==="trial")?0:Number(form.price)'), "price is numeric and free-trial plans are explicitly zero-priced");
  assert.ok(payload.includes("price,"), "numeric price variable is included in the actual request payload");
  assert.ok(payload.includes('published:form.published!=="unpublished"'), "publication is an explicit boolean");
  assert.ok(!payload.includes("...form"), "form-only/derived fields are not spread into the request");
});

test("client-side validation mirrors the server (price > 0, exactly one duration)", () => {
  assert.ok(page.includes("Enter a price greater than zero."));
  assert.ok(page.includes("Set exactly one duration: days or months"));
  assert.ok(!page.includes("payload.price<0"), "zero prices must not pass client validation");
});

test("the table shows real stored values and never fakes 0 months / Unavailable", () => {
  assert.ok(page.includes("durationText(p)") && page.includes('"Not set"'));
  assert.ok(!page.includes("`${p.duration_months} months`}</td>"), "legacy '0 months' rendering removed");
  assert.ok(!/"Unavailable"/.test(page.slice(page.indexOf("const money"), page.indexOf("const unwrap"))));
  assert.ok(page.includes("money(p.price,p.currency)"));
});

test("administrators are told which plans need configuration, and can fix them in place", () => {
  assert.ok(page.includes("planDiag.needs_configuration") && page.includes("Configure"));
  assert.ok(page.includes("diagnostics?.needs_configuration"));
  assert.ok(page.includes("Duplicate-looking plans are kept for history"));
});

test("publication, activation and archive controls use the existing endpoints", () => {
  assert.ok(page.includes("/admin/billbook/plans/${encodeURIComponent(p.id)}/status"));
  assert.ok(page.includes("/admin/billbook/plans/${encodeURIComponent(p.id)}/archive"));
  assert.ok(page.includes("<Label>Publication</Label>"));
});
