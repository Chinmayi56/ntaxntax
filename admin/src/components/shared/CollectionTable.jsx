import { useCrud } from "@/hooks/useCrud";
import { useEffect, useState } from "react";
import PageHeader from "@/components/shared/PageHeader";
import DataTable from "@/components/shared/DataTable";
import { KpiCard } from "@/components/shared/KpiCard";
import { StatusBadge, PriorityBadge } from "@/components/shared/StatusBadge";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import api, { describeApiError } from "@/lib/api";
import { inr } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/context/AuthContext";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { notifyDataChanged } from "@/lib/dataSync";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { FolderKanban, CheckCircle2, Clock, Building2, CalendarCheck, Users, Wallet, FolderOpen, UserCog, Star, XCircle, Download, Eye, IndianRupee, Plus, Trash2, X } from "lucide-react";

const bar = (v) => (
  <div className="flex items-center gap-2 min-w-[110px]"><Progress value={v} className="h-2 bg-zinc-100 [&>div]:bg-brand" /><span className="text-xs w-9 text-right">{v}%</span></div>
);

function DocumentActions({ row, canDelete, onDelete }) {
  const open = async (download = false) => {
    try {
      const { data } = await api.get(`/documents/${row.id}`); const f = data?.data;
      if (!f?.file_data) throw new Error("Document data is unavailable.");
      if (download) { const a = document.createElement("a"); a.href = f.file_data; a.download = f.file_name || "document"; a.click(); return; }
      const w = window.open("", "_blank"); if (!w) throw new Error("Please allow pop-ups to preview documents.");
      if (String(f.mime_type || "").startsWith("image/") || f.mime_type === "application/pdf") w.document.write(`<title>${f.file_name || "Document"}</title><iframe src="${f.file_data}" style="width:100%;height:100vh;border:0"></iframe>`);
      else { w.close(); const a = document.createElement("a"); a.href = f.file_data; a.download = f.file_name || "document"; a.click(); }
    } catch (e) { toast.error(describeApiError(e, "Unable to open document.")); }
  };
  return (
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="sm" className="h-8 text-royal" title="View" onClick={() => open(false)} data-testid={`doc-view-${row.id}`}><Eye className="h-4 w-4" /></Button>
      <Button variant="ghost" size="sm" className="h-8 text-brand-hover" title="Download" onClick={() => open(true)} data-testid={`doc-download-${row.id}`}><Download className="h-4 w-4" /></Button>
      {canDelete && <Button variant="ghost" size="sm" className="h-8 text-red-600 hover:text-red-700" title="Delete" onClick={() => onDelete(row)} data-testid={`doc-delete-${row.id}`}><Trash2 className="h-4 w-4" /></Button>}
    </div>
  );
}


// Employee -> assigned customer detail. Both requests are authorised server-side:
// GET /customers/{id} 404s unless the customer is assigned to the logged-in employee, and
// GET /documents?customer_id=... only ever returns documents of that employee's assigned customers.
const CUSTOMER_DETAIL_FIELDS = [
  ["business_name", "Business"], ["owner", "Contact person"], ["email", "Email"], ["mobile", "Mobile"],
  ["address", "Address"], ["state", "State"], ["gst_number", "GSTIN"], ["pan", "PAN"],
  ["business_type", "Business type"], ["service_type", "Service"], ["filing_status", "Filing status"],
  ["assigned_employee", "Assigned employee"], ["status", "Status"],
];

function EmployeeCustomerDialog({ customer, onClose }) {
  const [detail, setDetail] = useState(null);
  const [docs, setDocs] = useState([]);
  const [state, setState] = useState("loading");

  useEffect(() => {
    if (!customer) return undefined;
    let cancelled = false;
    setState("loading"); setDetail(null); setDocs([]);
    (async () => {
      try {
        const [c, d] = await Promise.all([
          api.get(`/customers/${customer.id}`),
          api.get("/documents", { params: { customer_id: customer.id, page: 1, page_size: 500 } }),
        ]);
        if (cancelled) return;
        setDetail(c.data?.data || null);
        setDocs(Array.isArray(d.data?.data) ? d.data.data : []);
        setState("ready");
      } catch (e) {
        if (cancelled) return;
        setState("error");
        toast.error(describeApiError(e, "Unable to load customer details."));
      }
    })();
    return () => { cancelled = true; };
  }, [customer]);

  const fields = detail ? CUSTOMER_DETAIL_FIELDS.filter(([k]) => detail[k] !== undefined && detail[k] !== null && String(detail[k]).trim() !== "") : [];
  const categories = Array.isArray(detail?.service_categories) ? detail.service_categories.filter(Boolean) : [];
  return (
    <Dialog open={!!customer} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto bg-white" data-testid="emp-customer-dialog">
        <DialogHeader>
          <DialogTitle>{detail?.business_name || customer?.business_name || "Customer"}</DialogTitle>
          <DialogDescription>Customer details and documents for a customer assigned to you.</DialogDescription>
        </DialogHeader>
        {state === "loading" && <div className="space-y-2" aria-busy="true"><div className="h-4 w-1/2 rounded bg-zinc-100 animate-pulse" /><div className="h-4 w-2/3 rounded bg-zinc-100 animate-pulse" /><div className="h-4 w-1/3 rounded bg-zinc-100 animate-pulse" /></div>}
        {state === "error" && <p className="text-sm text-red-600" role="alert">This customer is unavailable or is not assigned to you.</p>}
        {state === "ready" && (
          <div className="feedback-enter space-y-5">
            <div className="grid sm:grid-cols-2 gap-x-6">
              {fields.map(([k, label]) => (
                <div key={k} className="flex justify-between gap-4 border-b border-zinc-100 py-2">
                  <span className="text-xs text-zinc-500">{label}</span>
                  <span className="text-sm text-right break-words max-w-[60%]">{String(detail[k])}</span>
                </div>
              ))}
              {typeof detail?.outstanding === "number" && (
                <div className="flex justify-between gap-4 border-b border-zinc-100 py-2"><span className="text-xs text-zinc-500">Outstanding</span><span className="text-sm">{inr(detail.outstanding)}</span></div>
              )}
            </div>
            {categories.length > 0 && (
              <div><h4 className="text-sm font-semibold text-zinc-800 mb-2">Services</h4><div className="flex flex-wrap gap-1.5">{categories.map((c) => <span key={String(c)} className="text-xs rounded bg-zinc-100 px-2 py-1 text-zinc-700">{String(c)}</span>)}</div></div>
            )}
            <div>
              <h4 className="text-sm font-semibold text-zinc-800 mb-2">Documents ({docs.length})</h4>
              {docs.length === 0 ? <p className="text-sm text-zinc-500">No documents uploaded for this customer yet.</p> : (
                <ul className="divide-y divide-zinc-100 border border-zinc-200 rounded-lg">
                  {docs.map((d) => (
                    <li key={d.id} className="flex items-center justify-between gap-3 px-3 py-2">
                      <div className="min-w-0"><p className="text-sm truncate" title={d.file_name || d.name}>{d.file_name || d.name || "Document"}</p><p className="text-xs text-zinc-500">{d.doc_type || d.title || d.mime_type || ""}</p></div>
                      <div className="flex items-center gap-2 shrink-0"><StatusBadge value={d.status} /><DocumentActions row={d} canDelete={false} /></div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

const docColumns = ({ canDelete, onDelete }) => [
  { key: "file_name", label: "Document Name", render: (r) => <span className="inline-block max-w-[240px] truncate align-bottom" title={r.file_name || r.name}>{r.file_name || r.name || "—"}</span> },
  { key: "customer_name", label: "Customer Name", render: (r) => r.customer_name || "—" },
  { key: "doc_type", label: "Document Type", render: (r) => r.doc_type || r.title || r.mime_type || "—", exportValue: (r) => r.doc_type || r.title || r.mime_type || "" },
  { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
  { key: "uploaded_date", label: "Uploaded Date", render: (r) => (r.uploaded_date ? String(r.uploaded_date).slice(0, 10) : "—"), exportValue: (r) => (r.uploaded_date ? String(r.uploaded_date).slice(0, 10) : "") },
  { key: "service_id", label: "Service" },
  { key: "mime_type", label: "File Type" },
  { key: "file_size", label: "Size", render: (r) => r.file_size ? `${Math.ceil(r.file_size / 1024)} KB` : "—" },
  { key: "uploaded_by", label: "Uploaded By" },
  { key: "booking_id", label: "Booking" },
  { key: "actions", label: "Actions", render: (r) => <DocumentActions row={r} canDelete={canDelete} onDelete={onDelete} /> },
];
const DOC_KPI_TONES = { Approved: "green", Pending: "amber", Rejected: "red" };
// Summary cards are derived from the real rows: Total + the standard statuses + any other status
// that actually exists in the database (e.g. "Uploaded").
const docKpis = (rows, selected, onSelect) => {
  const count = (st) => rows.filter((x) => x.status === st).length;
  const extra = [...new Set(rows.map((x) => x.status).filter((st) => st && !["Approved", "Pending", "Rejected"].includes(st)))];
  const defs = [
    { key: "all", title: "Total Documents", value: rows.length, icon: FolderOpen, tone: "royal" },
    { key: "Approved", title: "Approved", value: count("Approved"), icon: CheckCircle2, tone: "green" },
    { key: "Pending", title: "Pending", value: count("Pending"), icon: Clock, tone: "amber" },
    { key: "Rejected", title: "Rejected", value: count("Rejected"), icon: XCircle, tone: "red" },
    ...extra.map((st) => ({ key: st, title: st, value: count(st), icon: FolderOpen, tone: DOC_KPI_TONES[st] || "grey" })),
  ];
  return defs.map((d) => ({ ...d, active: selected === d.key, onClick: () => onSelect(d.key), testId: `doc-kpi-${d.key}` }));
};

const PRESETS = {
  "admin-payments": {
    name: "payments", title: "Payments", breadcrumb: ["Super Admin", "Payments"],
    kpiFn: (r) => [
      { title: "Total Revenue", value: inr(r.filter((x) => x.status === "Completed").reduce((s, x) => s + (x.total || 0), 0)), icon: IndianRupee },
      { title: "Completed", value: r.filter((x) => x.status === "Completed").length, icon: CheckCircle2 },
      { title: "Pending", value: r.filter((x) => x.status === "Pending").length, icon: Clock },
      { title: "Failed", value: r.filter((x) => x.status === "Failed").length, icon: XCircle },
    ],
    columns: [
      { key: "payment_id", label: "Payment ID" }, { key: "customer", label: "Customer" }, { key: "invoice_no", label: "Invoice" },
      { key: "total", label: "Amount", render: (r) => inr(r.total), exportValue: (r) => r.total },
      { key: "gst", label: "GST", render: (r) => inr(r.gst), exportValue: (r) => r.gst },
      { key: "mode", label: "Mode" }, { key: "reference_no", label: "Reference" }, { key: "txn_date", label: "Date" },
      { key: "agent", label: "Agent" }, { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    ],
  },
  "admin-agents": {
    name: "agents", title: "Agent Management", breadcrumb: ["Super Admin", "Agents"],
    kpiFn: (r) => [
      { title: "Total Consultants", value: r.length, icon: UserCog },
      { title: "Active", value: r.filter((x) => x.status === "Active").length, icon: CheckCircle2 },
      { title: "Active Clients", value: r.reduce((s, x) => s + (x.active_clients || 0), 0), icon: Users },
      { title: "Commission YTD", value: inr(r.reduce((s, x) => s + (x.commission_ytd || 0), 0)), icon: Wallet },
    ],
    columns: [
      { key: "agent_id", label: "ID" }, { key: "name", label: "Name" }, { key: "mobile", label: "Mobile" },
      { key: "specialization", label: "Specialization" }, { key: "experience", label: "Exp" }, { key: "region", label: "Region" },
      { key: "active_clients", label: "Clients" },
      { key: "conversion", label: "Conv %", render: (r) => `${r.conversion}%` },
      { key: "rating", label: "Rating", render: (r) => <span className="inline-flex items-center gap-1">{r.rating} <Star className="h-3 w-3 text-brand fill-brand" /></span> },
      { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    ],
  },
  "admin-documents": { name: "documents", title: "Documents", breadcrumb: ["Super Admin", "Documents"], documents: true },
  "emp-documents": { name: "documents", title: "Documents", breadcrumb: ["Employee", "Documents"], documents: true },
  "agent-documents": { name: "documents", title: "Documents", breadcrumb: ["Tax Consultant", "Documents"], documents: true },
  "agent-projects": {
    name: "projects", title: "My Projects", breadcrumb: ["Tax Consultant", "Projects"],
    kpiFn: (r) => [
      { title: "Total", value: r.length, icon: FolderKanban },
      { title: "Completed", value: r.filter((x) => x.status === "Completed").length, icon: CheckCircle2 },
      { title: "Running", value: r.filter((x) => x.status === "Running").length, icon: Clock },
      { title: "Pending", value: r.filter((x) => x.status === "Pending").length, icon: Clock },
    ],
    columns: [
      { key: "project_id", label: "ID" }, { key: "name", label: "Project" }, { key: "client", label: "Client" },
      { key: "priority", label: "Priority", render: (r) => <PriorityBadge value={r.priority} /> },
      { key: "progress", label: "Progress", render: (r) => bar(r.progress) },
      { key: "due_date", label: "Due" }, { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    ],
  },
  "emp-projects": {
    name: "projects", title: "My Projects", breadcrumb: ["Employee", "Projects"],
    kpiFn: (r) => [
      { title: "Total", value: r.length, icon: FolderKanban },
      { title: "Completed", value: r.filter((x) => x.status === "Completed").length, icon: CheckCircle2 },
      { title: "Running", value: r.filter((x) => x.status === "Running").length, icon: Clock },
      { title: "Pending", value: r.filter((x) => x.status === "Pending").length, icon: Clock },
    ],
    columns: [
      { key: "project_id", label: "ID" }, { key: "name", label: "Project" }, { key: "client", label: "Client" },
      { key: "priority", label: "Priority", render: (r) => <PriorityBadge value={r.priority} /> },
      { key: "progress", label: "Progress", render: (r) => bar(r.progress) },
      { key: "due_date", label: "Due" }, { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    ],
  },
  "emp-customers": {
    name: "customers", title: "Assigned Customers", breadcrumb: ["Employee", "Customers"],
    kpiFn: (r) => [{ title: "Customers", value: r.length, icon: Building2 }, { title: "Active", value: r.filter((x) => x.status === "Active").length, icon: CheckCircle2 }, { title: "Outstanding", value: inr(r.reduce((s, x) => s + (x.outstanding || 0), 0)), icon: Clock }],
    columns: [
      { key: "business_name", label: "Business" }, { key: "gst_number", label: "GSTIN" }, { key: "pan", label: "PAN" },
      { key: "mobile", label: "Mobile" }, { key: "state", label: "State" },
      { key: "outstanding", label: "Outstanding", render: (r) => inr(r.outstanding), exportValue: (r) => r.outstanding },
      { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    ],
  },
  "agent-customers": {
    name: "customers", title: "My Customers", breadcrumb: ["Tax Consultant", "Customers"],
    kpiFn: (r) => [{ title: "Customers", value: r.length, icon: Building2 }, { title: "Active", value: r.filter((x) => x.status === "Active").length, icon: CheckCircle2 }],
    columns: [
      { key: "business_name", label: "Business" }, { key: "owner", label: "Contact" }, { key: "gst_number", label: "GSTIN" },
      { key: "mobile", label: "Mobile" }, { key: "state", label: "State" }, { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    ],
  },
  "emp-meetings": {
    name: "appointments", title: "Meetings", breadcrumb: ["Employee", "Meetings"],
    kpiFn: (r) => [{ title: "Total", value: r.length, icon: CalendarCheck }, { title: "Upcoming", value: r.filter((x) => x.status === "Upcoming").length, icon: Clock }, { title: "Completed", value: r.filter((x) => x.status === "Completed").length, icon: CheckCircle2 }],
    columns: [
      { key: "business_name", label: "Client" }, { key: "date", label: "Date" }, { key: "time", label: "Time" },
      { key: "service", label: "Agenda" }, { key: "mode", label: "Mode" }, { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    ],
  },
  "agent-meetings": {
    name: "appointments", title: "Meetings", breadcrumb: ["Tax Consultant", "Meetings"],
    kpiFn: (r) => [{ title: "Total", value: r.length, icon: CalendarCheck }, { title: "Upcoming", value: r.filter((x) => x.status === "Upcoming").length, icon: Clock }, { title: "Completed", value: r.filter((x) => x.status === "Completed").length, icon: CheckCircle2 }],
    columns: [
      { key: "business_name", label: "Client" }, { key: "date", label: "Date" }, { key: "time", label: "Time" },
      { key: "service", label: "Service" }, { key: "mode", label: "Mode" }, { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    ],
  },
};

function AddProjectDialog({ open, onOpenChange, create, role }) {
  const [customers, setCustomers] = useState([]);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    name: "", customer_id: "", client: "", service: "", start_date: "",
    due_date: "", priority: "Medium", status: "Pending", progress: "0",
    description: "",
  });

  useEffect(() => {
    if (!open) return;
    setForm({
      name: "", customer_id: "", client: "", service: "", start_date: "",
      due_date: "", priority: "Medium", status: "Pending", progress: "0",
      description: "",
    });
    api.get("/customers", { params: { page: 1, page_size: 500 } })
      .then(({ data }) => setCustomers(Array.isArray(data?.data) ? data.data : Array.isArray(data) ? data : []))
      .catch(() => setCustomers([]));
  }, [open]);

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) {
      toast.error("Project name is required");
      return;
    }
    if (!form.customer_id) {
      toast.error("Select a customer");
      return;
    }
    setSaving(true);
    try {
      const customer = customers.find((c) => c.id === form.customer_id);
      await create({
        name: form.name.trim(),
        customer_id: form.customer_id,
        client: customer?.business_name || customer?.name || form.client,
        customer: customer?.business_name || customer?.name || form.client,
        service: form.service.trim(),
        start_date: form.start_date,
        due_date: form.due_date,
        priority: form.priority,
        status: form.status,
        progress: Number(form.progress) || 0,
        description: form.description.trim(),
      });
      onOpenChange(false);
    } catch (err) {
      toast.error(describeApiError(err, "Failed to create project"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add Project</DialogTitle>
          <DialogDescription>
            Create a real project in the shared NTAXCO backend. The project is automatically assigned to the logged-in {role}.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Project Name *</label>
              <Input className="mt-1" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Project name" />
            </div>
            <div>
              <label className="text-sm font-medium">Customer *</label>
              <select className="mt-1 flex h-9 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={form.customer_id} onChange={(e) => {
                const c = customers.find((x) => x.id === e.target.value);
                setForm((f) => ({ ...f, customer_id: e.target.value, client: c?.business_name || c?.name || "" }));
              }}>
                <option value="">Select assigned customer</option>
                {customers.map((c) => <option key={c.id} value={c.id}>{c.business_name || c.name || c.id}</option>)}
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Service</label>
              <Input className="mt-1" value={form.service} onChange={(e) => set("service", e.target.value)} placeholder="Service" />
            </div>
            <div>
              <label className="text-sm font-medium">Priority</label>
              <select className="mt-1 flex h-9 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={form.priority} onChange={(e) => set("priority", e.target.value)}>
                <option>Low</option><option>Medium</option><option>High</option><option>Critical</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Start Date</label>
              <Input className="mt-1" type="date" value={form.start_date} onChange={(e) => set("start_date", e.target.value)} />
            </div>
            <div>
              <label className="text-sm font-medium">Due Date</label>
              <Input className="mt-1" type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} />
            </div>
            <div>
              <label className="text-sm font-medium">Status</label>
              <select className="mt-1 flex h-9 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={form.status} onChange={(e) => set("status", e.target.value)}>
                <option>Pending</option><option>Running</option><option>Completed</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Progress (%)</label>
              <Input className="mt-1" type="number" min="0" max="100" value={form.progress} onChange={(e) => set("progress", e.target.value)} />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">Description</label>
            <textarea className="mt-1 min-h-[90px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="Project description or remarks" />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={saving}>{saving ? "Creating..." : "Create Project"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function CollectionTable({ preset }) {
  const cfg = PRESETS[preset];
  const { rows, loading, create, load } = useCrud(cfg.name);
  const { user } = useAuth();
  const [addOpen, setAddOpen] = useState(false);
  const [docFilter, setDocFilter] = useState("all");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const isProjectPortal = preset === "emp-projects" || preset === "agent-projects";
  const isDocs = !!cfg.documents;
  const canDelete = isDocs && user?.role === "admin";

  const confirmDelete = async () => {
    if (!pendingDelete || deleting) return; // prevents double submission
    setDeleting(true);
    try {
      await api.delete(`/documents/${pendingDelete.id}`);
      toast.success("Document deleted");
      setPendingDelete(null);
      await load({ silent: true }); // list, totals and status counts are recomputed from the refreshed rows
      notifyDataChanged(["documents"], "documents");
    } catch (e) {
      toast.error(describeApiError(e, "Unable to delete the document."));
      if (e?.response?.status === 404) { setPendingDelete(null); load({ silent: true }); }
    } finally { setDeleting(false); }
  };

  const isEmployeeCustomers = preset === "emp-customers";
  const [openCustomer, setOpenCustomer] = useState(null);
  const columns = isDocs
    ? docColumns({ canDelete, onDelete: setPendingDelete })
    : isEmployeeCustomers
      ? [...cfg.columns, { key: "actions", label: "Actions", render: (r) => <Button variant="ghost" size="sm" className="h-8 text-royal" onClick={() => setOpenCustomer(r)} data-testid={`emp-customer-view-${r.id}`}><Eye className="h-4 w-4 mr-1" />View</Button> }]
      : cfg.columns;
  const kpis = isDocs ? docKpis(rows, docFilter, setDocFilter) : (cfg.kpiFn ? cfg.kpiFn(rows) : null);
  // If the selected status no longer exists (e.g. its last document was deleted), fall back to all.
  const activeFilter = isDocs && docFilter !== "all" && !rows.some((r) => r.status === docFilter) ? "all" : docFilter;
  const tableRows = isDocs && activeFilter !== "all" ? rows.filter((r) => r.status === activeFilter) : rows;

  return (
    <div className="min-w-0">
      <PageHeader
        title={cfg.title}
        breadcrumb={cfg.breadcrumb}
        subtitle={`${cfg.title} — live data from the shared NTAXCO backend.`}
        actions={isProjectPortal ? (
          <Button onClick={() => setAddOpen(true)} data-testid={`${preset}-add-project`}>
            <Plus className="h-4 w-4 mr-2" /> Add Project
          </Button>
        ) : null}
      />
      {kpis && <div className={`grid grid-cols-2 ${isDocs && kpis.length > 4 ? "lg:grid-cols-5" : "lg:grid-cols-4"} gap-3 sm:gap-4 mb-6`}>{kpis.map((k, i) => <KpiCard key={k.key || i} {...k} active={isDocs ? k.key === activeFilter : false} loading={loading} testId={k.testId || `kpi-${i}`} />)}</div>}
      {isDocs && activeFilter !== "all" && (
        <div className="flex flex-wrap items-center gap-2 mb-3 text-sm" data-testid="doc-filter-banner">
          <span className="text-muted-foreground">Showing only <b>{activeFilter}</b> documents ({tableRows.length})</span>
          <Button variant="outline" size="sm" className="h-7" onClick={() => setDocFilter("all")}><X className="h-3 w-3 mr-1" />Show all documents</Button>
        </div>
      )}
      <DataTable title={cfg.title} columns={columns} rows={tableRows} loading={loading} pageSize={8} testId={`${preset}-table`} searchFields={isDocs ? ["customer_name"] : undefined} />
      {isEmployeeCustomers && <EmployeeCustomerDialog customer={openCustomer} onClose={() => setOpenCustomer(null)} />}
      {isProjectPortal && (
        <AddProjectDialog
          open={addOpen}
          onOpenChange={setAddOpen}
          create={create}
          role={preset === "emp-projects" ? "employee" : "agent"}
        />
      )}
      {canDelete && (
        <AlertDialog open={!!pendingDelete} onOpenChange={(o) => { if (!o && !deleting) setPendingDelete(null); }}>
          <AlertDialogContent className="max-w-[calc(100vw-2rem)] sm:max-w-md">
            <AlertDialogHeader>
              <AlertDialogTitle>Delete document</AlertDialogTitle>
              <AlertDialogDescription>
                Are you sure you want to delete this document?
                {pendingDelete ? <span className="block mt-2 text-zinc-700 break-all">{pendingDelete.file_name || pendingDelete.name}{pendingDelete.customer_name ? ` — ${pendingDelete.customer_name}` : ""}</span> : null}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter className="gap-2 sm:gap-0">
              <AlertDialogCancel disabled={deleting} data-testid="doc-delete-cancel">Cancel</AlertDialogCancel>
              <AlertDialogAction disabled={deleting} onClick={(e) => { e.preventDefault(); confirmDelete(); }} className="bg-red-600 hover:bg-red-700 text-white" data-testid="doc-delete-confirm">{deleting ? "Deleting..." : "Delete"}</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </div>
  );
}
