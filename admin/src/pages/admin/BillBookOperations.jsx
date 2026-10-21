import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import api, { describeApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";

const titles = {
  history: "Payment History", renewals: "Renewals & Expiry", trials: "Free Trials",
  events: "Webhook & Payment Events", reports: "BillBooks Reports",
};
const unwrap = (r) => r?.data?.data ?? r?.data ?? {};
const date = (v) => v ? new Date(v).toLocaleString("en-IN") : "—";
const amount = (v) => v == null ? "—" : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(Number(v));

export default function BillBookOperations() {
  const section = useLocation().pathname.split("/")[3] || "reports";
  const title = titles[section] || "BillBooks Operations";
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const links = [
    ["Overview", "/admin/billbook"], ["Plans Management", "/admin/billbook/plans"],
    ["Subscriptions", "/admin/billbook/subscriptions"], ["Payments & Transactions", "/admin/billbook/payments"],
    ["Payment History", "/admin/billbook/history"], ["Renewals & Expiry", "/admin/billbook/renewals"],
    ["Free Trials", "/admin/billbook/trials"], ["Webhook & Payment Events", "/admin/billbook/events"],
    ["Reports", "/admin/billbook/reports"],
  ];
  const load = async () => {
    setLoading(true); setError("");
    try {
      let r;
      if (section === "events") r = await api.get("/admin/billbook/payment-events");
      else if (section === "reports") r = await api.get("/admin/billbook/reports");
      else if (section === "trials") r = await api.get("/admin/billbook/subscribers", { params: { limit: 500, status: "active" } });
      else if (section === "renewals") r = await api.get("/admin/billbook/subscribers", { params: { limit: 500 } });
      else r = await api.get("/admin/billbook/payments", { params: { limit: 500 } });
      setData(unwrap(r));
    } catch (e) { setError(describeApiError(e, `Unable to load ${title.toLowerCase()}.`)); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [section]);
  let rows = data.items || [];
  if (section === "trials") rows = rows.filter((x) => x.subscription_type === "free_trial");
  if (section === "renewals") rows = rows.filter((x) => x.expires_at && ["active", "expired"].includes(String(x.state || x.status).toLowerCase()));
  if (section === "history") rows = data.items || [];
  const columns = section === "events"
    ? [["Event", "event_type"], ["Provider event ID", "provider_event_id"], ["Order", "order_id"], ["Payment", "payment_id"], ["Status", "status"], ["Received", "received_at"], ["Processed", "processed_at"]]
    : section === "trials" || section === "renewals"
      ? [["Customer / Business", "business_name"], ["Plan", "plan_name"], ["Type", "subscription_type"], ["Status", "state"], ["Start", "started_at"], ["Expiry", "expires_at"]]
      : [["Customer", "customer"], ["Plan / Service", "service"], ["Amount", "total"], ["Status", "status"], ["Order", "order_id"], ["Payment", "payment_id"], ["Date", "txn_date"]];
  return <div className="space-y-5 p-1">
    <div><p className="text-sm text-zinc-500">My BillBook / {title}</p><h1 className="mt-1 text-2xl font-bold">{title}</h1><p className="mt-1 text-sm text-zinc-500">Live records from the existing BillBook subscription database and payment records.</p></div>
    <nav className="flex flex-wrap gap-2 border-b pb-3">{links.map(([label, href]) => <Link key={href} to={href} className={`rounded-lg px-3 py-2 text-sm ${href.endsWith(`/${section}`) || (section === "reports" && href.endsWith("/reports")) ? "bg-royal-faint text-royal" : "text-zinc-600 hover:bg-zinc-100"}`}>{label}</Link>)}</nav>
    <div className="flex justify-end"><Button variant="outline" onClick={load}>Refresh</Button></div>
    {error && <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
    {section === "reports" && <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{Object.entries(data.summary || {}).filter(([k, v]) => typeof v === "number").map(([k, v]) => <div key={k} className="rounded-xl border bg-white p-4"><p className="text-sm text-zinc-500">{k.replaceAll("_", " ")}</p><p className="mt-2 text-2xl font-semibold">{k.includes("revenue") ? amount(v) : v}</p></div>)}<p className="sm:col-span-2 xl:col-span-4 text-sm text-zinc-500">{data.summary?.revenue_definition || "Revenue includes only verified paid subscription records and excludes free trials."}</p></div>}
    {section !== "reports" && <div className="overflow-x-auto rounded-xl border bg-white"><table className="w-full min-w-[900px] text-left text-sm"><thead className="bg-zinc-50"><tr>{columns.map(([label]) => <th key={label} className="p-3 font-semibold">{label}</th>)}</tr></thead><tbody>{!loading && rows.map((row, i) => <tr key={row.id || row.provider_event_id || row.order_id || i} className="border-t">{columns.map(([label, key]) => <td key={key} className="p-3">{key.includes("_at") || key === "txn_date" ? date(row[key]) : key === "total" ? amount(row[key]) : (row[key] ?? row.customer_name ?? row.full_name ?? "—")}</td>)}</tr>)}</tbody></table>{!loading && !rows.length && <p className="p-8 text-center text-sm text-zinc-500">No matching records are available.</p>}{loading && <p className="p-8 text-center text-sm text-zinc-500">Loading records…</p>}</div>}
  </div>;
}
