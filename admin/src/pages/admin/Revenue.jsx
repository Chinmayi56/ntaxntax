import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Building2, CalendarDays, Download, Edit3, RefreshCw, Search, Upload, UserCog, Users, Wallet } from "lucide-react";
import PageHeader from "@/components/shared/PageHeader";
import { KpiCard } from "@/components/shared/KpiCard";
import ModuleKanban from "@/components/shared/ModuleKanban";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import api, { describeApiError } from "@/lib/api";
import { inr } from "@/lib/utils";
import { notifyDataChanged } from "@/lib/dataSync";
import { toast } from "sonner";
import { exportCSV } from "@/lib/exports";

const CONFIG = {
  summary: { label: "Revenue Summary", kind: null, icon: Wallet },
  employees: { label: "Employee Revenue", kind: "employee", icon: Users, endpoint: "/revenue/employees", resource: "employees" },
  agents: { label: "Agent Revenue", kind: "agent", icon: UserCog, endpoint: "/revenue/agents", resource: "agents" },
  customers: { label: "Customer Revenue", kind: "customer", icon: Building2, endpoint: "/revenue/customers", resource: "customers" },
  today: { label: "Today's Revenue", kind: "transaction", period: "today" },
  pending: { label: "Pending Revenue", kind: "pending", period: "pending" },
  month: { label: "This Month Revenue", kind: "transaction", period: "month" },
  year: { label: "This Year Revenue", kind: "transaction", period: "year" },
};

const dash = (v) => (v === undefined || v === null || v === "" || (typeof v === "number" && !Number.isFinite(v)) ? "—" : String(v));
const money = (v) => (Number.isFinite(Number(v)) && v !== null && v !== undefined && v !== "" ? inr(Number(v)) : "—");
const day = (v) => (v ? String(v).slice(0, 10) : "—");
const REVENUE_CARD_DETAILS = [
  ["customer_name", "Customer"], ["customer_id", "Customer ID"],
  ["employee_name", "Employee"], ["employee_id", "Employee ID"],
  ["agent_name", "Agent"], ["agent_id", "Agent ID"],
  ["service", "Service"], ["invoice_no", "Invoice"],
  ["amount", "Amount", money], ["payment_date", "Payment date", day],
  ["payment_method", "Payment method"], ["reference_no", "Reference no"],
  ["payment_status", "Payment status"],
  ["outstanding", "Customer outstanding", money], ["invoice_outstanding", "Invoice outstanding", money],
  ["created_at", "Created", day], ["updated_at", "Updated", day],
  ["kanban_status", "Kanban status"],
];

const PERIODS = ["all", "today", "week", "month", "year", "custom"];
const periodLabel = (p) => ({ all: "All Time", today: "Today", week: "This Week", month: "This Month", year: "This Year", custom: "Custom Range" }[p] || "All Time");

// ---------------------------------------------------------------------------
// Export / Import. Export fetches every page of the SAME endpoint the current card/detail view
// reads (so it always matches what the card represents) and builds the CSV from those real rows.
// Import posts the CSV text to POST /revenue/import (admin only), which validates every row,
// skips duplicates and writes to the existing payments collection.
// ---------------------------------------------------------------------------
const TXN_EXPORT_COLUMNS = [
  { key: "payment_id", label: "Payment ID", exportValue: (r) => r.payment_id || r.id || "" },
  { key: "customer_id", label: "Customer ID" },
  { key: "customer_name", label: "Customer", exportValue: (r) => r.customer_name || r.customer || "" },
  { key: "employee_name", label: "Employee" },
  { key: "agent_name", label: "Agent" },
  { key: "service", label: "Service" },
  { key: "invoice_no", label: "Invoice No", exportValue: (r) => r.invoice_no || "" },
  { key: "amount", label: "Amount" },
  { key: "date", label: "Payment Date", exportValue: (r) => String(r.date || "").slice(0, 10) },
  { key: "payment_method", label: "Payment Method" },
  { key: "status", label: "Status" },
  { key: "reference_no", label: "Reference No" },
];
const PENDING_EXPORT_COLUMNS = [
  { key: "customer_id", label: "Customer ID" },
  { key: "customer_name", label: "Customer" },
  { key: "employee_name", label: "Employee" },
  { key: "agent_name", label: "Agent" },
  { key: "service", label: "Service" },
  { key: "total_billed", label: "Total Billed" },
  { key: "paid_amount", label: "Total Paid" },
  { key: "pending_amount", label: "Outstanding" },
  { key: "status", label: "Status" },
];
const ENTITY_EXPORT_COLUMNS = (kind) => kind === "customer" ? [
  { key: "id", label: "Customer ID" }, { key: "name", label: "Customer" },
  { key: "services", label: "Services", exportValue: (r) => (r.services || []).join("; ") },
  { key: "total_billed", label: "Total Billed" }, { key: "paid_amount", label: "Total Paid" }, { key: "outstanding", label: "Outstanding" },
  { key: "revenue", label: "Collected Revenue" }, { key: "transactions", label: "Transactions" },
  { key: "latest_payment", label: "Latest Payment", exportValue: (r) => (r.latest_payment ? `${String(r.latest_payment.date || "").slice(0, 10)} ${r.latest_payment.amount}` : "") },
] : [
  { key: "id", label: kind === "employee" ? "Employee ID" : "Agent ID" }, { key: "name", label: kind === "employee" ? "Employee" : "Agent" },
  { key: "contact", label: "Contact", exportValue: (r) => (r.profile || {}).email || (r.profile || {}).mobile || "" },
  { key: "revenue", label: "Collected Revenue" }, { key: "transactions", label: "Transactions" },
  { key: "last_date", label: "Last Revenue", exportValue: (r) => String(r.last_date || "").slice(0, 10) },
];

async function fetchAllPages(url, params) {
  const out = [];
  let page = 1; let pages = 1;
  do {
    const res = await api.get(url, { params: { ...params, page, page_size: 500 } });
    const body = res.data || {};
    const list = body?.data?.rows || (Array.isArray(body?.data) ? body.data : []);
    out.push(...list);
    pages = Number(body?.pagination?.pages || 1);
    page += 1;
  } while (page <= pages && page <= 100);
  return out;
}

function RevenueToolbar({ cfg, params, search, onImported }) {
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  const fileRef = useRef(null);

  const doExport = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      let rows; let columns; let name = cfg.label;
      if (cfg.kind === "employee" || cfg.kind === "agent" || cfg.kind === "customer") {
        rows = await fetchAllPages(cfg.endpoint, { ...params, search: search || undefined });
        columns = ENTITY_EXPORT_COLUMNS(cfg.kind);
      } else if (cfg.kind === "pending") {
        rows = await fetchAllPages("/revenue/transactions", { period: "pending" });
        columns = PENDING_EXPORT_COLUMNS;
      } else if (cfg.kind === "transaction") {
        rows = await fetchAllPages("/revenue/transactions", { period: cfg.period, search: search || undefined });
        columns = TXN_EXPORT_COLUMNS;
      } else {
        rows = await fetchAllPages("/revenue/transactions", { period: "all" });
        columns = TXN_EXPORT_COLUMNS; name = "All Revenue Transactions";
      }
      if (!rows.length) { toast.info("There are no records to export for this view."); return; }
      exportCSV(`${name.replace(/[^A-Za-z0-9 _-]/g, "")}-${new Date().toISOString().slice(0, 10)}`, columns, rows);
      toast.success(`Exported ${rows.length} record${rows.length === 1 ? "" : "s"}`);
    } catch (e) {
      toast.error(describeApiError(e, "Unable to export revenue data."));
    } finally { setExporting(false); }
  };

  const onFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || importing) return;
    if (!/\.csv$/i.test(file.name) && file.type !== "text/csv") { toast.error("Please choose a .csv file."); return; }
    if (file.size > 5 * 1024 * 1024) { toast.error("The file is too large (limit about 5 MB)."); return; }
    setImporting(true);
    try {
      const text = await file.text();
      const res = await api.post("/revenue/import", { csv: text, filename: file.name });
      const data = res.data?.data || {};
      setResult({ ...data, filename: file.name });
      if (data.imported > 0) {
        toast.success(`${data.imported} record${data.imported === 1 ? "" : "s"} imported`);
        notifyDataChanged(["payments", "revenue", "invoices", "customers", "bookings"], "revenue-import");
        onImported?.();
      } else {
        toast.info("No new records were imported.");
      }
    } catch (e) {
      setResult({ failed: describeApiError(e, "Import failed."), filename: file.name });
    } finally { setImporting(false); }
  };

  return <>
    <Button variant="outline" className="border-zinc-300" onClick={doExport} disabled={exporting} data-testid="revenue-export"><Download className="h-4 w-4 mr-1.5" />{exporting ? "Exporting..." : "Export"}</Button>
    <Button variant="outline" className="border-zinc-300" onClick={() => fileRef.current?.click()} disabled={importing} data-testid="revenue-import"><Upload className="h-4 w-4 mr-1.5" />{importing ? "Importing..." : "Import"}</Button>
    <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden" onChange={onFile} data-testid="revenue-import-file" />
    <Dialog open={!!result} onOpenChange={(o) => { if (!o) setResult(null); }}>
      <DialogContent className="bg-white w-[calc(100vw-1.5rem)] max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader><DialogTitle>Import result</DialogTitle><DialogDescription className="break-all">{result?.filename}</DialogDescription></DialogHeader>
        {result?.failed ? <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 flex gap-2"><AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" /><span>{result.failed}</span></div> : result && <div className="space-y-3 text-sm">
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-lg border p-3"><div className="text-xl font-bold text-emerald-700">{result.imported || 0}</div><div className="text-xs text-zinc-500">Imported</div></div>
            <div className="rounded-lg border p-3"><div className="text-xl font-bold text-amber-600">{(result.duplicates || []).length}</div><div className="text-xs text-zinc-500">Duplicates skipped</div></div>
            <div className="rounded-lg border p-3"><div className="text-xl font-bold text-red-600">{(result.errors || []).length}</div><div className="text-xs text-zinc-500">Invalid rows</div></div>
          </div>
          {(result.errors || []).length > 0 && <div><div className="font-semibold text-red-700 mb-1">Invalid rows (not imported)</div><ul className="max-h-48 overflow-y-auto rounded-lg border bg-red-50/40 p-2 space-y-1">{result.errors.slice(0, 200).map((er, i) => <li key={i}><b>Row {er.row}:</b> {er.message}</li>)}</ul></div>}
          {(result.duplicates || []).length > 0 && <div><div className="font-semibold text-amber-700 mb-1">Duplicates (skipped)</div><ul className="max-h-40 overflow-y-auto rounded-lg border bg-amber-50/40 p-2 space-y-1">{result.duplicates.slice(0, 200).map((er, i) => <li key={i}><b>Row {er.row}:</b> {er.message}</li>)}</ul></div>}
        </div>}
        <DialogFooter><Button onClick={() => setResult(null)}>Close</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </>;
}

export default function Revenue({ view = "summary" }) {
  const cfg = CONFIG[view] || CONFIG.summary;
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [period, setPeriod] = useState(searchParams.get("period") || "all");
  const [startDate, setStartDate] = useState(searchParams.get("start_date") || "");
  const [endDate, setEndDate] = useState(searchParams.get("end_date") || "");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("revenue");
  const [order, setOrder] = useState("desc");
  const [page, setPage] = useState(1);
  const [summary, setSummary] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [edit, setEdit] = useState(null);
  const pageSize = 25;

  const params = useMemo(() => ({
    period,
    ...(period === "custom" ? { start_date: startDate || undefined, end_date: endDate || undefined } : {}),
  }), [period, startDate, endDate]);

  const loadSummary = useCallback(async () => {
    const response = await api.get("/revenue", { params });
    setSummary(response.data?.data || null);
  }, [params]);

  const load = useCallback(async (opts) => {
    const silent = opts && opts.silent === true;
    if (!silent) setLoading(true);
    setError("");
    try {
      if (cfg.kind === "transaction" || cfg.kind === "pending") {
        const response = await api.get("/revenue/transactions", { params: { period: cfg.period, search: search || undefined, page, page_size: pageSize } });
        setDetail(response.data || null);
        await loadSummary();
      } else {
        const [summaryRes, detailRes] = await Promise.all([
          api.get("/revenue", { params }),
          cfg.endpoint ? api.get(cfg.endpoint, { params: { ...params, search: search || undefined, sort, order, page, page_size: pageSize } }) : Promise.resolve(null),
        ]);
        setSummary(summaryRes.data?.data || null);
        setDetail(detailRes?.data || null);
      }
    } catch (e) {
      setError(describeApiError(e, `Unable to load ${cfg.label.toLowerCase()}.`));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [cfg, params, search, sort, order, page, loadSummary]);

  useEffect(() => { load(); }, [load]);

  // Real-time sync through the existing mechanism: any payment/invoice/booking/customer change made
  // elsewhere in Admin fires "ntaxco:data-changed"; Revenue re-reads MongoDB-backed totals. Also refresh
  // when the tab becomes visible again so changes made in another tab/window show up.
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; }, [load]);
  useEffect(() => {
    const RELEVANT = new Set(["*", "revenue", "payments", "invoices", "bookings", "customers", "employees", "agents", "services", "commissions"]);
    const onChange = (event) => {
      const detail = event?.detail || {};
      if (String(detail.source || "").startsWith("kanban:") || detail.source === "revenue") return; // no money changed / already reloading
      const resources = detail.resources || [];
      if (!resources.length || resources.some((r) => RELEVANT.has(r))) loadRef.current({ silent: true });
    };
    const onVisible = () => { if (!document.hidden) loadRef.current({ silent: true }); };
    window.addEventListener("ntaxco:data-changed", onChange);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("ntaxco:data-changed", onChange);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
  useEffect(() => { setPage(1); }, [search, period, startDate, endDate, sort, order, view]);

  const refreshAll = async () => {
    await load();
    toast.success("Revenue data refreshed");
  };

  const go = (path) => navigate(`/admin/revenue${path ? `/${path}` : ""}`);

  const openEdit = async (kind, id, fallback = null) => {
    try {
      const resource = kind === "employee" ? "employees" : kind === "customer" ? "customers" : kind === "agent" ? "agents" : "payments";
      const response = await api.get(`/${resource}/${id}`);
      setEdit({ kind, resource, data: response.data?.data || fallback || {} });
    } catch (e) {
      toast.error(describeApiError(e, "Unable to load record for editing"));
    }
  };

  const saveEdit = async (resource, id, body) => {
    try {
      const response = await api.put(`/${resource}/${id}`, body);
      toast.success("Record updated successfully");
      setEdit(null);
      notifyDataChanged([resource, "revenue", "payments", "invoices", "customers", "agents", "employees"], "revenue");
      await load();
      return response.data?.data;
    } catch (e) {
      toast.error(describeApiError(e, "Unable to save changes"));
      throw e;
    }
  };

  const summaryData = summary || {};
  const rows = detail?.data?.rows || (Array.isArray(detail?.data) ? detail.data : []);
  const pagination = detail?.pagination || { page: 1, pages: 1, total: rows.length };

  return (
    <div>
      <PageHeader
        title={cfg.label}
        subtitle={cfg.kind === "transaction" || cfg.kind === "pending" ? "Live revenue details from NTAXCO financial records." : "Real collected revenue from the existing NTAXCO invoice and payment data."}
        breadcrumb={["Super Admin", "Revenue", cfg.label]}
        actions={<div className="flex flex-wrap items-center gap-2"><RevenueToolbar cfg={cfg} params={params} search={search} onImported={() => load({ silent: true })} /><Button variant="outline" className="border-zinc-300" onClick={refreshAll} disabled={loading}><RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button></div>}
      />

      {(view === "summary" || cfg.kind === "employee" || cfg.kind === "agent" || cfg.kind === "customer") && (
        <div className="flex flex-col lg:flex-row gap-3 mb-6">
          <Select value={period} onValueChange={setPeriod}><SelectTrigger className="w-full lg:w-44 bg-white"><SelectValue /></SelectTrigger><SelectContent className="bg-white">{PERIODS.map((p) => <SelectItem key={p} value={p}>{periodLabel(p)}</SelectItem>)}</SelectContent></Select>
          {period === "custom" && <><Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="w-full lg:w-44 bg-white" /><Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="w-full lg:w-44 bg-white" /></>}
        </div>
      )}

      {view === "summary" && <>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-5">
          <KpiCard title="Employee Revenue" value={inr(summaryData.employee?.total_revenue || 0)} icon={Users} to="/admin/revenue/employees" loading={loading} tone="purple" />
          <KpiCard title="Customer Revenue" value={inr(summaryData.customer?.total_revenue || 0)} icon={Building2} to="/admin/revenue/customers" loading={loading} tone="green" />
          <KpiCard title="Agent Revenue" value={inr(summaryData.agent?.total_revenue || 0)} icon={UserCog} to="/admin/revenue/agents" loading={loading} tone="orange" />
        </div>
        <SummaryCards summary={summaryData} loading={loading} go={go} />
        <div className="mt-8">
          <ModuleKanban
            board="revenue"
            resource="payments"
            recordsEndpoint="/admin/kanban/revenue/cards"
            showPeople
            title="Revenue"
            subtitle="Live payment/revenue records with an independent Kanban workflow. Payment status remains authoritative."
            searchFields={["customer", "customer_name", "customer_id", "invoice_no", "invoice_id", "service", "employee_name", "agent_name", "payment_status"]}
            detailFields={REVENUE_CARD_DETAILS}
            onEdit={(row) => openEdit("payment", row.id, row)}
          />
        </div>
      </>}

      {view !== "summary" && (cfg.kind === "employee" || cfg.kind === "agent" || cfg.kind === "customer") && (
        <EntityRevenueView cfg={cfg} rows={rows} pagination={pagination} loading={loading} error={error} search={search} setSearch={setSearch} sort={sort} setSort={setSort} order={order} setOrder={setOrder} page={page} setPage={setPage} go={go} onEdit={(row) => openEdit(cfg.kind, row.id, row)} />
      )}

      {view !== "summary" && (cfg.kind === "transaction" || cfg.kind === "pending") && (
        <TransactionRevenueView cfg={cfg} rows={rows} pagination={pagination} loading={loading} error={error} search={search} setSearch={setSearch} page={page} setPage={setPage} go={go} onEdit={(row) => openEdit("payment", row.id, row)} onPendingEdit={(row) => openEdit("customer", row.customer_id, row)} />
      )}

      {view === "summary" && error && <ErrorBox message={error} onRetry={load} />}
      <EditRecordDialog edit={edit} onClose={() => setEdit(null)} onSave={saveEdit} />
    </div>
  );
}

function SummaryCards({ summary, loading, go }) {
  const cards = [
    ["today", "Today's Revenue", summary?.today_transactions || 0, summary?.today_revenue || 0, CalendarDays, "purple"],
    ["pending", "Pending Revenue", summary?.pending_customers || 0, summary?.pending_revenue || 0, Wallet, "red"],
    ["month", "This Month Revenue", summary?.month_transactions || 0, summary?.month_revenue || 0, CalendarDays, "teal"],
    ["year", "This Year Revenue", summary?.year_transactions || 0, summary?.year_revenue || 0, CalendarDays, "royal"],
  ];
  return <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-8">
    {cards.map(([path, title, count, amount, Icon, tone]) => <KpiCard key={path} title={title} value={inr(amount || 0)} icon={Icon} to={`/admin/revenue/${path}`} loading={loading} tone={tone} trend={undefined} />)}
  </div>;
}

function EntityRevenueView({ cfg, rows, pagination, loading, error, search, setSearch, sort, setSort, order, setOrder, page, setPage, go, onEdit }) {
  const toggleSort = (value) => { if (sort === value) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(value); setOrder("desc"); } };
  const isCustomer = cfg.kind === "customer";
  const label = isCustomer ? "Customer" : cfg.kind === "employee" ? "Employee" : "Agent";
  const arrow = (key) => (sort === key ? (order === "asc" ? "↑" : "↓") : "");
  return <div className="bg-white border border-zinc-200 rounded-xl shadow-sm overflow-hidden">
    <div className="p-4 border-b border-zinc-200 flex flex-col xl:flex-row gap-3 xl:items-center xl:justify-between">
      <div className="flex items-center gap-2"><Button variant="ghost" size="sm" onClick={() => go("")}><ArrowLeft className="h-4 w-4 mr-1" />Revenue</Button><span className="text-zinc-300">/</span><span className="font-semibold text-zinc-900">{cfg.label}</span></div>
      <div className="flex flex-col sm:flex-row gap-2">
        <div className="relative"><Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-400" /><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={`Search ${isCustomer ? "customers" : `${cfg.kind}s`}...`} className="pl-9 w-full sm:w-64" /></div>
        <Select value={sort} onValueChange={(v) => { setSort(v); setOrder("desc"); }}><SelectTrigger className="w-36"><SelectValue /></SelectTrigger><SelectContent className="bg-white"><SelectItem value="revenue">Revenue</SelectItem><SelectItem value="name">Name</SelectItem><SelectItem value="transactions">Transactions</SelectItem></SelectContent></Select>
      </div>
    </div>
    {error ? <ErrorBox message={error} /> : loading ? <LoadingRows /> : rows.length === 0 ? <div className="p-12 text-center text-sm text-zinc-500">No {cfg.kind} revenue data available for the selected period.</div> : <div className="overflow-x-auto">
      <Table>
        <TableHeader><TableRow>
          <TableHead onClick={() => toggleSort("name")} className="cursor-pointer">{label}</TableHead>
          {isCustomer ? <>
            <TableHead>Services</TableHead>
            <TableHead>Total Billed</TableHead><TableHead>Total Paid</TableHead><TableHead>Outstanding</TableHead>
            <TableHead onClick={() => toggleSort("revenue")} className="cursor-pointer">Collected (period) {arrow("revenue")}</TableHead>
          </> : <>
            <TableHead>ID</TableHead><TableHead>Contact</TableHead><TableHead>Department / Service</TableHead>
            <TableHead onClick={() => toggleSort("revenue")} className="cursor-pointer">Collected Revenue {arrow("revenue")}</TableHead>
          </>}
          <TableHead onClick={() => toggleSort("transactions")} className="cursor-pointer">Transactions {arrow("transactions")}</TableHead>
          <TableHead>{isCustomer ? "Latest Payment" : "Last Revenue"}</TableHead>
          <TableHead>Action</TableHead>
        </TableRow></TableHeader>
        <TableBody>{rows.map((r) => {
          const p = r.profile || {};
          const latest = r.latest_payment;
          return <TableRow key={r.id}>
            <TableCell className="font-medium text-zinc-900">{dash(r.name)}{isCustomer && <div className="text-xs font-normal text-zinc-400">{dash(r.id)}</div>}</TableCell>
            {isCustomer ? <>
              <TableCell className="max-w-[220px] truncate" title={(r.services || []).join(", ")}>{(r.services || []).length ? r.services.join(", ") : "—"}</TableCell>
              <TableCell>{money(r.total_billed || 0)}</TableCell>
              <TableCell className="text-emerald-700">{money(r.paid_amount || 0)}</TableCell>
              <TableCell className="text-red-600 font-semibold">{money(r.outstanding || 0)}</TableCell>
              <TableCell className="font-semibold">{money(r.revenue || 0)}</TableCell>
            </> : <>
              <TableCell>{dash(r.id)}</TableCell>
              <TableCell>{dash(p.email || p.mobile)}</TableCell>
              <TableCell>{dash(p.department || p.service_type || p.designation)}</TableCell>
              <TableCell className="font-semibold">{money(r.revenue || 0)}</TableCell>
            </>}
            <TableCell>{r.transactions || 0}</TableCell>
            <TableCell>{isCustomer ? (latest ? `${day(latest.date)} · ${money(latest.amount)}` : "—") : day(r.last_date)}</TableCell>
            <TableCell><Button size="sm" variant="outline" onClick={() => onEdit(r)}><Edit3 className="h-3.5 w-3.5 mr-1" />Edit</Button></TableCell>
          </TableRow>;
        })}</TableBody>
      </Table>
    </div>}
    {!error && <div className="p-4 border-t border-zinc-200 flex items-center justify-between text-sm"><span className="text-zinc-500">{pagination.total || 0} records · Page {pagination.page || page} of {pagination.pages || 1}</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Previous</Button><Button variant="outline" size="sm" disabled={page >= (pagination.pages || 1) || loading} onClick={() => setPage((p) => p + 1)}>Next</Button></div></div>}
  </div>;
}

function TransactionRevenueView({ cfg, rows, pagination, loading, error, search, setSearch, page, setPage, go, onEdit, onPendingEdit }) {
  return <div className="bg-white border border-zinc-200 rounded-xl shadow-sm overflow-hidden">
    <div className="p-4 border-b border-zinc-200 flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between"><div className="flex items-center gap-2"><Button variant="ghost" size="sm" onClick={() => go("")}><ArrowLeft className="h-4 w-4 mr-1" />Revenue</Button><span className="text-zinc-300">/</span><span className="font-semibold text-zinc-900">{cfg.label}</span></div>{cfg.kind === "transaction" && <div className="relative"><Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-400" /><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search customer, invoice, service..." className="pl-9 w-full sm:w-80" /></div>}</div>
    {error ? <ErrorBox message={error} /> : loading ? <LoadingRows /> : rows.length === 0 ? <div className="p-12 text-center text-sm text-zinc-500">No {cfg.kind === "pending" ? "pending revenue" : "revenue transactions"} found.</div> : <div className="overflow-x-auto"><Table><TableHeader><TableRow><TableHead>Customer</TableHead><TableHead>Employee</TableHead><TableHead>Agent</TableHead><TableHead>Service</TableHead><TableHead>Invoice</TableHead><TableHead>{cfg.kind === "pending" ? "Billed" : "Amount"}</TableHead>{cfg.kind === "pending" && <><TableHead>Paid</TableHead><TableHead>Pending</TableHead></>}<TableHead>Date</TableHead><TableHead>Status</TableHead><TableHead>Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((r) => <TableRow key={r.id}><TableCell className="font-medium">{r.customer_name || r.customer || "—"}</TableCell><TableCell>{r.employee_name || "—"}</TableCell><TableCell>{r.agent_name || "—"}</TableCell><TableCell>{r.service || "—"}</TableCell><TableCell>{r.invoice_no || r.invoice_id || "—"}</TableCell><TableCell className="font-semibold">{inr(cfg.kind === "pending" ? r.total_billed : r.amount)}</TableCell>{cfg.kind === "pending" && <><TableCell className="text-emerald-700">{inr(r.paid_amount || 0)}</TableCell><TableCell className="text-red-600 font-semibold">{inr(r.pending_amount || 0)}</TableCell></>}<TableCell>{String(r.date || "").slice(0, 10) || "—"}</TableCell><TableCell>{r.status || "—"}</TableCell><TableCell>{cfg.kind === "pending" ? <Button size="sm" variant="outline" onClick={() => r.customer_id && onPendingEdit(r)}><Edit3 className="h-3.5 w-3.5 mr-1" />Edit Customer</Button> : <Button size="sm" variant="outline" onClick={() => onEdit(r)}><Edit3 className="h-3.5 w-3.5 mr-1" />Edit</Button>}</TableCell></TableRow>)}</TableBody></Table></div>}
    {!error && <div className="p-4 border-t border-zinc-200 flex items-center justify-between text-sm"><span className="text-zinc-500">{pagination.total || 0} records · Page {pagination.page || page} of {pagination.pages || 1}</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Previous</Button><Button variant="outline" size="sm" disabled={page >= (pagination.pages || 1) || loading} onClick={() => setPage((p) => p + 1)}>Next</Button></div></div>}
  </div>;
}

const EDIT_FIELDS = {
  employee: [
    ["name", "Name"], ["email", "Email"], ["mobile", "Mobile"], ["department", "Department"], ["designation", "Designation"], ["manager", "Manager"], ["address", "Address"], ["state", "State"], ["status", "Status"],
  ],
  customer: [
    ["business_name", "Business Name"], ["owner", "Owner"], ["email", "Email"], ["mobile", "Mobile"], ["phone", "Phone"], ["gst_number", "GST Number"], ["pan", "PAN"], ["address", "Address"], ["state", "State"], ["city", "City"], ["pincode", "Pincode"], ["assigned_employee", "Assigned Employee"], ["assigned_agent", "Assigned Agent"], ["status", "Status"],
  ],
  agent: [
    ["name", "Name"], ["email", "Email"], ["mobile", "Mobile"], ["phone", "Phone"], ["commission_percentage", "Commission %"], ["bank_account", "Bank Account"], ["address", "Address"], ["state", "State"], ["status", "Status"],
  ],
  payment: [
    ["amount", "Amount"], ["payment_date", "Payment Date"], ["payment_method", "Payment Method"], ["reference_no", "Reference No"], ["status", "Payment Status"],
  ],
};

function EditRecordDialog({ edit, onClose, onSave }) {
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);
  useEffect(() => { setForm(edit?.data ? { ...edit.data } : {}); }, [edit]);
  if (!edit) return null;
  const fields = EDIT_FIELDS[edit.kind] || [];
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const save = async () => {
    setSaving(true);
    try {
      const body = {};
      for (const [key] of fields) if (form[key] !== undefined) body[key] = form[key];
      if (edit.kind === "payment" && body.amount !== undefined) body.amount = Number(body.amount);
      if (edit.kind === "agent" && body.commission_percentage !== undefined) body.commission_percentage = Number(body.commission_percentage);
      await onSave(edit.resource, edit.data.id, body);
    } finally { setSaving(false); }
  };
  return <Dialog open={!!edit} onOpenChange={(open) => !open && onClose()}><DialogContent className="bg-white max-w-2xl max-h-[90vh] overflow-y-auto"><DialogHeader><DialogTitle>Edit {edit.kind === "payment" ? "Revenue Record" : edit.kind[0].toUpperCase() + edit.kind.slice(1)}</DialogTitle><DialogDescription>Changes are saved through the existing FastAPI and MongoDB data flow.</DialogDescription></DialogHeader><div className="grid grid-cols-1 md:grid-cols-2 gap-4">{fields.map(([key, label]) => <div key={key} className="space-y-1.5"><Label>{label}</Label>{key === "status" ? <Select value={String(form[key] ?? "")} onValueChange={(v) => set(key, v)}><SelectTrigger><SelectValue placeholder="Select status" /></SelectTrigger><SelectContent className="bg-white">{(edit.kind === "payment" ? [...new Set(["Completed", "Paid", "Partial", "Pending", "Failed", ...(form[key] ? [String(form[key])] : [])])] : ["Active", "Inactive", "Suspended"]).map((v) => <SelectItem key={v} value={v}>{v}</SelectItem>)}</SelectContent></Select> : <Input value={form[key] ?? ""} type={key === "commission_percentage" || key === "amount" ? "number" : key === "payment_date" ? "date" : "text"} onChange={(e) => set(key, e.target.value)} />}</div>)}</div><div className="mt-2 rounded-lg bg-zinc-50 border border-zinc-200 p-3 text-xs text-zinc-600">ID: <span className="font-semibold">{edit.data.id}</span>. Calculated revenue totals are read-only and continue to come from the underlying financial records.</div><DialogFooter><Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button><Button onClick={save} disabled={saving}>{saving ? "Saving..." : "Save"}</Button></DialogFooter></DialogContent></Dialog>;
}

function LoadingRows() { return <div className="p-4 space-y-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>; }
function ErrorBox({ message, onRetry }) { return <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-5"><p className="font-semibold text-red-800">Unable to load revenue</p><p className="text-sm text-red-700 mt-1">{message}</p>{onRetry && <Button variant="outline" className="mt-3" onClick={onRetry}>Retry</Button>}</div>; }
