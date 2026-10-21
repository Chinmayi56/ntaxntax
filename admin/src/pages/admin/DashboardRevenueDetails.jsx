import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, RefreshCw, Search } from "lucide-react";
import PageHeader from "@/components/shared/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import api, { describeApiError } from "@/lib/api";
import { inr } from "@/lib/utils";

// Admin → Dashboard → Employee / Customer / Agent Revenue details.
// Every value is read from the backend (/admin/dashboard/*-revenue), which aggregates the existing
// erp_payments / erp_invoices / erp_customers / erp_employees / erp_agents data. Nothing is cached or hardcoded here.
const CONFIG = {
  employee: { title: "Employee Revenue", endpoint: "/admin/dashboard/employee-revenue", noun: "employee", searchHint: "Search employee name, ID, email or phone..." },
  customer: { title: "Customer Revenue", endpoint: "/admin/dashboard/customer-revenue", noun: "customer", searchHint: "Search customer name, ID, company, email or phone..." },
  agent: { title: "Agent Revenue", endpoint: "/admin/dashboard/agent-revenue", noun: "agent", searchHint: "Search agent name, ID, email or phone..." },
};
const PAYMENT_STATUSES = ["Paid", "Partial", "Pending", "No Billing"];
const ALL = "all";
const PAGE_SIZE = 25;

const dash = (v) => (v === undefined || v === null || v === "" ? "—" : String(v));
const money = (v) => (v === undefined || v === null || v === "" || !Number.isFinite(Number(v)) ? "—" : inr(Number(v)));
const day = (v) => (v ? String(v).slice(0, 10) : "—");
const latest = (p) => (p ? `${day(p.date)} · ${money(p.amount)}${p.invoice_no ? ` · ${p.invoice_no}` : ""}` : "—");
const list = (a) => (Array.isArray(a) && a.length ? a.join(", ") : "—");
const STATUS_TONE = { paid: "bg-emerald-50 text-emerald-700 border-emerald-200", partial: "bg-amber-50 text-amber-700 border-amber-200", pending: "bg-red-50 text-red-700 border-red-200" };

export default function DashboardRevenueDetails({ kind = "employee" }) {
  const cfg = CONFIG[kind] || CONFIG.employee;
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState(ALL);
  const [department, setDepartment] = useState(ALL);
  const [paymentStatus, setPaymentStatus] = useState(ALL);
  const [sort, setSort] = useState("revenue_desc");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [pagination, setPagination] = useState({ page: 1, pages: 1, total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (opts) => {
    const silent = opts && opts.silent === true;
    if (!silent) setLoading(true);
    setError("");
    try {
      const [field, order] = sort === "name_asc" ? ["name", "asc"] : sort === "revenue_asc" ? ["revenue", "asc"] : ["revenue", "desc"];
      const params = { search: search || undefined, status: status !== ALL ? status : undefined, sort: field, order, page, page_size: PAGE_SIZE };
      if (kind === "employee" && department !== ALL) params.department = department;
      if (kind === "customer" && paymentStatus !== ALL) params.payment_status = paymentStatus;
      const res = await api.get(cfg.endpoint, { params });
      setData(res.data?.data || null);
      setPagination(res.data?.pagination || { page: 1, pages: 1, total: 0 });
    } catch (e) {
      setError(describeApiError(e, `Unable to load ${cfg.title.toLowerCase()}.`));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [cfg, kind, search, status, department, paymentStatus, sort, page]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [search, status, department, paymentStatus, sort, kind]);

  // Re-read MongoDB-backed totals whenever a payment/invoice/etc. changes elsewhere in Admin.
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; }, [load]);
  useEffect(() => {
    const RELEVANT = new Set(["*", "revenue", "payments", "invoices", "bookings", "customers", "employees", "agents", "services"]);
    const onChange = (event) => {
      const resources = event?.detail?.resources || [];
      if (!resources.length || resources.some((r) => RELEVANT.has(r))) loadRef.current({ silent: true });
    };
    const onVisible = () => { if (!document.hidden) loadRef.current({ silent: true }); };
    window.addEventListener("ntaxco:data-changed", onChange);
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.removeEventListener("ntaxco:data-changed", onChange); document.removeEventListener("visibilitychange", onVisible); };
  }, []);

  const rows = data?.rows || [];
  const totals = data?.totals || {};
  const filters = data?.filters || {};
  const isCustomer = kind === "customer";
  const isEmployee = kind === "employee";

  return (
    <div>
      <PageHeader
        title={cfg.title}
        subtitle="Live figures aggregated from NTAXCO payment, invoice and billing records."
        breadcrumb={["Super Admin", "Dashboard", cfg.title]}
        actions={<Button variant="outline" className="border-zinc-300" onClick={() => load()} disabled={loading}><RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
        {[["Total Revenue", totals.revenue], ["Total Billed", totals.total_billed], ["Total Paid", totals.total_paid], ["Total Outstanding", totals.outstanding]].map(([label, value]) => (
          <div key={label} className="bg-white border border-zinc-200 rounded-xl shadow-sm p-4">
            <p className="text-xs text-zinc-500">{label}</p>
            {loading && !data ? <Skeleton className="h-7 w-24 mt-1" /> : <p className="text-xl font-semibold text-zinc-900 mt-1">{error ? "—" : money(value || 0)}</p>}
          </div>
        ))}
      </div>

      <div className="bg-white border border-zinc-200 rounded-xl shadow-sm overflow-hidden">
        <div className="p-4 border-b border-zinc-200 flex flex-col xl:flex-row gap-3 xl:items-center xl:justify-between">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => navigate("/admin/dashboard")}><ArrowLeft className="h-4 w-4 mr-1" />Dashboard</Button>
            <span className="text-zinc-300">/</span><span className="font-semibold text-zinc-900">{cfg.title}</span>
          </div>
          <div className="flex flex-col sm:flex-row sm:flex-wrap gap-2">
            <div className="relative"><Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-400" /><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={cfg.searchHint} className="pl-9 w-full sm:w-72" /></div>
            <Select value={status} onValueChange={setStatus}><SelectTrigger className="w-full sm:w-36 bg-white"><SelectValue placeholder="Status" /></SelectTrigger><SelectContent className="bg-white"><SelectItem value={ALL}>All Status</SelectItem>{(filters.statuses || []).map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent></Select>
            {isEmployee && <Select value={department} onValueChange={setDepartment}><SelectTrigger className="w-full sm:w-40 bg-white"><SelectValue placeholder="Department" /></SelectTrigger><SelectContent className="bg-white"><SelectItem value={ALL}>All Departments</SelectItem>{(filters.departments || []).map((d) => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent></Select>}
            {isCustomer && <Select value={paymentStatus} onValueChange={setPaymentStatus}><SelectTrigger className="w-full sm:w-40 bg-white"><SelectValue placeholder="Payment status" /></SelectTrigger><SelectContent className="bg-white"><SelectItem value={ALL}>All Payment Status</SelectItem>{PAYMENT_STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent></Select>}
            <Select value={sort} onValueChange={setSort}><SelectTrigger className="w-full sm:w-44 bg-white"><SelectValue /></SelectTrigger><SelectContent className="bg-white"><SelectItem value="revenue_desc">Highest Revenue</SelectItem><SelectItem value="revenue_asc">Lowest Revenue</SelectItem><SelectItem value="name_asc">Name (A–Z)</SelectItem></SelectContent></Select>
          </div>
        </div>

        {error ? (
          <div className="m-4 rounded-xl border border-red-200 bg-red-50 p-5">
            <p className="font-semibold text-red-800">Unable to load {cfg.title.toLowerCase()}</p>
            <p className="text-sm text-red-700 mt-1">{error}</p>
            <Button variant="outline" className="mt-3" onClick={() => load()}>Retry</Button>
          </div>
        ) : loading ? (
          <div className="p-4 space-y-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : rows.length === 0 ? (
          <div className="p-12 text-center text-sm text-zinc-500">No revenue data available.</div>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>{isCustomer ? "Customer" : isEmployee ? "Employee" : "Agent"}</TableHead>
                  {isCustomer && <TableHead>Company</TableHead>}
                  <TableHead>Email</TableHead>
                  <TableHead>Phone</TableHead>
                  {isEmployee && <><TableHead>Department</TableHead><TableHead>Role</TableHead></>}
                  {!isCustomer && <TableHead>Status</TableHead>}
                  {!isCustomer && <TableHead>Customers</TableHead>}
                  <TableHead>Services</TableHead>
                  <TableHead>Revenue</TableHead>
                  <TableHead>Total Billed</TableHead>
                  <TableHead>Total Paid</TableHead>
                  <TableHead>Outstanding</TableHead>
                  <TableHead>Payments</TableHead>
                  <TableHead>Latest Payment</TableHead>
                  {isCustomer && <TableHead>Payment Status</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.id} data-testid={`${kind}-revenue-row`}>
                    <TableCell className="text-xs text-zinc-500">{dash(r.id)}</TableCell>
                    <TableCell className="font-medium text-zinc-900">{dash(r.name)}</TableCell>
                    {isCustomer && <TableCell>{dash(r.company)}</TableCell>}
                    <TableCell>{dash(r.email)}</TableCell>
                    <TableCell>{dash(r.phone)}</TableCell>
                    {isEmployee && <><TableCell>{dash(r.department)}</TableCell><TableCell>{dash(r.role)}</TableCell></>}
                    {!isCustomer && <TableCell>{r.status ? <Badge variant="outline">{r.status}</Badge> : "—"}</TableCell>}
                    {!isCustomer && <TableCell>{r.customers_count ?? 0}</TableCell>}
                    <TableCell className="max-w-[220px] truncate" title={list(r.services)}>{list(r.services)}</TableCell>
                    <TableCell className="font-semibold">{money(r.revenue || 0)}</TableCell>
                    <TableCell>{money(r.total_billed || 0)}</TableCell>
                    <TableCell className="text-emerald-700">{money(r.total_paid || 0)}</TableCell>
                    <TableCell className="text-red-600 font-semibold">{money(r.outstanding || 0)}</TableCell>
                    <TableCell>{r.payments_count || 0}</TableCell>
                    <TableCell className="whitespace-nowrap">{latest(r.latest_payment)}</TableCell>
                    {isCustomer && <TableCell><span className={`inline-flex px-2 py-0.5 rounded-full border text-xs font-medium ${STATUS_TONE[String(r.payment_status || "").toLowerCase()] || "bg-zinc-50 text-zinc-600 border-zinc-200"}`}>{dash(r.payment_status)}</span></TableCell>}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {!error && (
          <div className="p-4 border-t border-zinc-200 flex items-center justify-between text-sm">
            <span className="text-zinc-500">{pagination.total || 0} records · Page {pagination.page || page} of {pagination.pages || 1}</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Previous</Button>
              <Button variant="outline" size="sm" disabled={page >= (pagination.pages || 1) || loading} onClick={() => setPage((p) => p + 1)}>Next</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
