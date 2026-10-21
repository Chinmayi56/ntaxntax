import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useCrud } from "@/hooks/useCrud";
import api, { describeApiError } from "@/lib/api";
import { inr } from "@/lib/utils";
import { notifyDataChanged } from "@/lib/dataSync";
import PageHeader from "@/components/shared/PageHeader";
import CustomerDetailTabs from "@/components/admin/CustomerDetailTabs";
import ImportCustomersDialog from "@/components/admin/ImportCustomersDialog";
import {
  PaymentTermsSection, DocumentsSection, emptyTerms, parseTerms, validateDocFile, guessDocType,
  resolveMime, readFileAsDataUrl, MAX_DOC_FILES,
} from "@/components/admin/CustomerCreateExtras";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import {
  Plus, Download, Printer, Search, SlidersHorizontal, MoreVertical, Pencil, Trash2,
  Eye, Users, FileText, FileCheck2, Landmark, CheckCircle2, Clock3, LoaderCircle,
  CheckCircle, CircleDollarSign, CalendarDays, GripVertical, ChevronLeft, ChevronRight,
  X, RefreshCw, UserPlus, UserRoundPlus, BriefcaseBusiness, FolderKanban, FileArchive, UploadCloud, UserRound,
} from "lucide-react";
import { exportCSV, exportExcel, exportPDF, printRows } from "@/lib/exports";

const BIZ = ["Individual", "Proprietorship", "Partnership", "Private Limited", "Public Limited", "LLP", "Other"];
const STATES = ["Telangana", "Andhra Pradesh", "Karnataka", "Tamil Nadu", "Maharashtra"];
// Account status (Active/Inactive/Suspended) and customer workflow status (Pending/Processing/Completed)
// are separate fields; the filter / bulk selector offer both, the backend routes each to the right field.
const ACCOUNT_STATUS = ["Active", "Inactive", "Suspended"];
const STATUS_FULL = [...ACCOUNT_STATUS, "Pending", "Processing", "Completed"];
const SERVICE_TYPES = ["GST", "TDS", "Income Tax", "Accounting", "ROC", "Other"];
const FILING_STATUS = ["Paid", "Pending", "Processing", "Completed"];
const FREQUENCIES = ["Monthly", "Quarterly", "Yearly"];
const GST_REG_TYPES = ["Regular", "Composition", "Casual Taxable Person", "Non-Resident", "Input Service Distributor", "TDS Deductor", "Unregistered"];
const TAXPAYER_TYPES = ["Regular", "Composition", "SEZ Unit", "SEZ Developer", "Unregistered"];

const CUSTOMER_FIELDS = [
  { key: "cust_id", label: "Customer ID", required: true, section: "Basic Information" },
  { key: "business_name", label: "Business / Company Name", required: true, full: true, section: "Basic Information" },
  { key: "business_type", label: "Customer Type", type: "select", options: BIZ, section: "Basic Information" },
  { key: "status", label: "Account Status", type: "select", options: ACCOUNT_STATUS, default: "Active", section: "Basic Information" },
  { key: "assigned_employee", label: "Consultant / Assigned Employee", section: "Basic Information" },
  { key: "registration_date", label: "Registration Date", type: "date", section: "Basic Information" },
  { key: "gst_number", label: "GSTIN", section: "Tax & Business Information" },
  { key: "pan", label: "PAN", section: "Tax & Business Information" },
  { key: "tan", label: "TAN", section: "Tax & Business Information" },
  { key: "cin", label: "CIN", section: "Tax & Business Information" },
  { key: "gst_registration_type", label: "GST Registration Type", type: "select", options: GST_REG_TYPES, section: "Tax & Business Information" },
  { key: "gst_registration_date", label: "GST Registration Date", type: "date", section: "Tax & Business Information" },
  { key: "taxpayer_type", label: "Taxpayer Type", type: "select", options: TAXPAYER_TYPES, section: "Tax & Business Information" },
  { key: "service_type", label: "Primary Service", type: "select", options: SERVICE_TYPES, section: "Tax & Business Information" },
  { key: "state", label: "State", type: "select", options: STATES, section: "Tax & Business Information" },
  { key: "city", label: "City", section: "Tax & Business Information" },
  { key: "pincode", label: "Pincode", section: "Tax & Business Information" },
  { key: "address", label: "Address", full: true, section: "Tax & Business Information" },
  { key: "owner", label: "Primary Contact Name", section: "Contact Information" },
  { key: "designation", label: "Designation", section: "Contact Information" },
  { key: "mobile", label: "Mobile Number", section: "Contact Information" },
  { key: "alternate_mobile", label: "Alternate Mobile Number", section: "Contact Information" },
  { key: "email", label: "Email", section: "Contact Information" },
  { key: "alternate_email", label: "Alternate Email", section: "Contact Information" },
  { key: "website", label: "Website", section: "Contact Information" },
  { key: "authorized_contact_person", label: "Authorized Contact Person", section: "Business / Contact Details" },
  { key: "billing_contact", label: "Billing Contact", section: "Business / Contact Details" },
  { key: "accounts_contact", label: "Accounts Contact", section: "Business / Contact Details" },
  { key: "communication_address", label: "Communication Address", full: true, section: "Business / Contact Details" },
  { key: "registered_address", label: "Registered Address", full: true, section: "Business / Contact Details" },
  { key: "assigned_agent", label: "Assigned Agent", section: "Assignment & Billing" },
  { key: "agent_commission_percentage", label: "Agent Percentage (%) for this customer", type: "number", section: "Assignment & Billing" },
  { key: "outstanding", label: "Outstanding (₹)", type: "number", section: "Assignment & Billing" },
  { key: "payment_frequency", label: "Payment Frequency", type: "select", options: FREQUENCIES, section: "Assignment & Billing" },
  { key: "filing_status", label: "Payment / Filing Status", type: "select", options: FILING_STATUS, default: "Pending", section: "Assignment & Billing" },
  { key: "notes", label: "Notes", type: "textarea", full: true, section: "Assignment & Billing" },
];

const initialForm = () => Object.fromEntries(CUSTOMER_FIELDS.map((f) => [f.key, f.default ?? ""]));

let docUidCounter = 0;
const todayStr = () => new Date().toISOString().slice(0, 10);

function CustomerFormDialog({ open, onOpenChange, editing, onSaved }) {
  const { update } = useCrud("customers");
  const [form, setForm] = useState(initialForm());
  const [saving, setSaving] = useState(false);
  const [terms, setTerms] = useState(emptyTerms());
  const [customTerms, setCustomTerms] = useState([]);
  const customIdRef = useRef(0);
  const [termMeta, setTermMeta] = useState({ payment_date: todayStr(), payment_method: "Cash" });
  const [termErrors, setTermErrors] = useState({});
  const [docItems, setDocItems] = useState([]);
  const [savedTermKeys, setSavedTermKeys] = useState([]);
  // Synchronous guards: a double click can fire twice before React re-renders, so state alone is not enough.
  const savingRef = useRef(false);
  const createdRef = useRef(null);        // the customer created by THIS dialog session (never created twice)
  const savedTermsRef = useRef(new Set()); // payment terms already stored for that customer
  const docItemsRef = useRef([]);
  docItemsRef.current = docItems;

  useEffect(() => {
    if (!open) return;
    setForm(editing ? { ...initialForm(), ...editing } : initialForm());
    setTerms(emptyTerms());
    setCustomTerms([]);
    customIdRef.current = 0;
    setTermMeta({ payment_date: todayStr(), payment_method: "Cash" });
    setTermErrors({});
    setSavedTermKeys([]);
    setDocItems((old) => { old.forEach((i) => i.previewUrl && URL.revokeObjectURL(i.previewUrl)); return []; });
    createdRef.current = null;
    savedTermsRef.current = new Set();
    savingRef.current = false;
  }, [open, editing]);

  const set = (key, value) => setForm((s) => ({ ...s, [key]: value }));
  const parsedTerms = useMemo(() => parseTerms(terms, customTerms), [terms, customTerms]);
  const setTerm = (key, value) => {
    setTerms((t) => ({ ...t, [key]: value }));
    setTermErrors((e) => { if (!e[key]) return e; const { [key]: _drop, ...rest } = e; return rest; });
  };

  const addCustomTerm = () => setCustomTerms((rows) => [...rows, { id: (customIdRef.current += 1), name: "", amount: "" }]);
  const changeCustomTerm = (id, field, value) => {
    setCustomTerms((rows) => rows.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
    setTermErrors((e) => { const k = `custom-${id}`; if (!e[k]) return e; const { [k]: _drop, ...rest } = e; return rest; });
  };
  const removeCustomTerm = (id) => setCustomTerms((rows) => rows.filter((r) => r.id !== id));

  const patchItem = (uid, patch) => setDocItems((items) => items.map((i) => (i.uid === uid ? { ...i, ...patch } : i)));

  const addFiles = (files) => {
    const additions = [];
    const current = docItemsRef.current;
    let rejected = 0;
    for (const file of files) {
      const dup = [...current, ...additions].some((i) => i.file.name === file.name && i.file.size === file.size && i.file.lastModified === file.lastModified);
      if (dup) { toast.info(`${file.name} is already in the list.`); continue; }
      if (current.length + additions.length >= MAX_DOC_FILES) { rejected += 1; continue; }
      const error = validateDocFile(file);
      additions.push({
        uid: `doc-${Date.now()}-${docUidCounter += 1}`, file, docType: guessDocType(file.name),
        status: error ? "invalid" : "ready", error,
        previewUrl: !error && resolveMime(file).startsWith("image/") ? URL.createObjectURL(file) : "",
      });
    }
    if (rejected) toast.error(`You can attach up to ${MAX_DOC_FILES} documents at a time.`);
    const bad = additions.filter((a) => a.status === "invalid");
    if (bad.length) toast.error(`${bad[0].file.name}: ${bad[0].error}${bad.length > 1 ? ` (+${bad.length - 1} more not accepted)` : ""}`);
    if (additions.length) setDocItems((items) => [...items, ...additions]);
  };
  const removeItem = (uid) => setDocItems((items) => {
    const gone = items.find((i) => i.uid === uid);
    if (gone?.previewUrl) URL.revokeObjectURL(gone.previewUrl);
    return items.filter((i) => i.uid !== uid);
  });

  const uploadDocument = async (customerId, item) => {
    patchItem(item.uid, { status: "uploading", error: "" });
    try {
      const mime = resolveMime(item.file);
      const raw = await readFileAsDataUrl(item.file);
      const file_data = `data:${mime};base64,${raw.slice(raw.indexOf(",") + 1)}`;
      const baseName = item.file.name.replace(/\.[^.]+$/, "");
      await api.post("/documents", {
        customer_id: customerId, doc_type: item.docType, title: item.docType === "Other Document" ? baseName : item.docType,
        description: "", file_name: item.file.name, file_size: item.file.size, file_data,
      });
      patchItem(item.uid, { status: "uploaded", error: "" });
      return true;
    } catch (err) {
      const reason = describeApiError(err, "Please try again.");
      patchItem(item.uid, { status: "error", error: `Unable to upload ${item.file.name}. ${reason}` });
      return false;
    }
  };

  const save = async () => {
    if (savingRef.current) return;
    for (const field of CUSTOMER_FIELDS) {
      if (field.required && !String(form[field.key] ?? "").trim()) return toast.error(`${field.label} is required`);
    }
    if (!editing) {
      if (Object.keys(parsedTerms.errors).length) {
        setTermErrors(parsedTerms.errors);
        return toast.error(Object.values(parsedTerms.errors)[0]);
      }
      if (parsedTerms.entries.length && !termMeta.payment_date) return toast.error("Payment date is required for the payment terms.");
    }
    savingRef.current = true;
    setSaving(true);
    let customer = editing ? editing : createdRef.current;
    let failures = 0;
    try {
      // 1) Customer: created once per dialog session (a retry reuses the saved customer instead of duplicating it).
      if (!customer) {
        const payload = { ...form };
        CUSTOMER_FIELDS.forEach((f) => { if (f.type === "number") payload[f.key] = Number(payload[f.key]) || 0; });
        const { data } = await api.post("/customers", payload);
        customer = data?.data;
        if (!customer?.id) throw new Error("The server did not return the new customer ID.");
        createdRef.current = customer;
        notifyDataChanged(["customers"], "customers");
      } else if (editing) {
        const payload = { ...form };
        CUSTOMER_FIELDS.forEach((f) => { if (f.type === "number") payload[f.key] = Number(payload[f.key]) || 0; });
        await update(editing.id, payload);
      }
    } catch (e) {
      toast.error(describeApiError(e, "Unable to save customer"));
      savingRef.current = false; setSaving(false);
      return;
    }

    try {
      // 2) Payment terms (new customer only): blank terms are skipped; saved terms are never re-posted.
      if (!editing) {
        for (const entry of parsedTerms.entries) {
          if (savedTermsRef.current.has(entry.key)) continue;
          try {
            await api.post("/payments", {
              customer_id: customer.id, customer: customer.business_name || form.business_name,
              term_name: entry.name, amount: entry.amount, total: entry.amount,
              payment_date: termMeta.payment_date, payment_method: termMeta.payment_method,
              status: "Paid", notes: "Recorded while adding the customer",
              source: "customer_creation",
              // Deterministic per customer + term: a retry or double submit can never store the same term twice.
              client_request_id: `initial-${entry.key}`,
            });
            savedTermsRef.current.add(entry.key);
            setSavedTermKeys([...savedTermsRef.current]);
          } catch (err) {
            failures += 1;
            setTermErrors((prev) => ({ ...prev, [entry.key]: `${entry.name} could not be saved: ${describeApiError(err, "Please try again.")}` }));
          }
        }
        if (parsedTerms.entries.length) notifyDataChanged(["payments", "customers", "invoices"], "customer-terms");
      }
      // 3) Documents: uploaded one by one against the customer's ID; failures stay in the list for a retry.
      const queue = docItemsRef.current.filter((i) => i.status === "ready" || i.status === "error");
      for (const item of queue) {
        const ok = await uploadDocument(customer.id, item);
        if (!ok) failures += 1;
      }
      if (queue.length) notifyDataChanged(["documents"], "customer-documents");
    } catch (e) {
      failures += 1;
      toast.error(describeApiError(e, "Something interrupted saving the payment terms or documents."));
    }

    savingRef.current = false;
    setSaving(false);
    if (failures) {
      toast.warning(editing
        ? `Customer saved, but ${failures} item(s) could not be saved. Fix them below and press Retry.`
        : `Customer ${customer.business_name || ""} was created, but ${failures} item(s) could not be saved. Fix them below and press Retry — nothing will be duplicated.`);
      onSaved?.(customer, { created: !editing, partial: true });
      return;
    }
    toast.success(editing ? "Customer updated" : "Customer saved with payment terms and documents");
    onSaved?.(customer, { created: !editing });
    onOpenChange(false);
  };

  const hasFailures = docItems.some((i) => i.status === "error") || Object.keys(termErrors).length > 0;
  const retryMode = !editing && !!createdRef.current && hasFailures;
  const guardedOpenChange = (next) => { if (!next && savingRef.current) return; onOpenChange(next); };

  return (
    <Dialog open={open} onOpenChange={guardedOpenChange}>
      <DialogContent className="bg-white max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-heading">{editing ? "Edit Customer" : "Add Customer"}</DialogTitle>
          <DialogDescription>Customer information is saved to the existing NTAXCO backend and database.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 py-2">
          {CUSTOMER_FIELDS.map((f, i) => (
            <div key={f.key} className={f.full ? "sm:col-span-2" : ""}>
              {f.section && f.section !== CUSTOMER_FIELDS[i - 1]?.section && (
                <div className="sm:col-span-2 -mx-1 pt-2 pb-1.5 text-xs font-bold uppercase tracking-wide text-royal border-b border-zinc-200 mb-1">{f.section}</div>
              )}
              <Label className="text-sm font-medium text-zinc-700">{f.label}{f.required && <span className="text-red-500"> *</span>}</Label>
              {f.type === "select" ? (
                <Select value={String(form[f.key] ?? "")} onValueChange={(v) => set(f.key, v)} disabled={retryMode}>
                  <SelectTrigger className="mt-1.5 border-zinc-300"><SelectValue placeholder={`Select ${f.label}`} /></SelectTrigger>
                  <SelectContent className="bg-white">{(f.options || []).map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}</SelectContent>
                </Select>
              ) : f.type === "textarea" ? (
                <Textarea value={form[f.key] ?? ""} onChange={(e) => set(f.key, e.target.value)} rows={4} className="mt-1.5 border-zinc-300" disabled={retryMode} />
              ) : (
                <Input type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"} value={form[f.key] ?? ""} onChange={(e) => set(f.key, e.target.value)} className="mt-1.5 border-zinc-300" disabled={retryMode} />
              )}
            </div>
          ))}
          {editing ? (
            <div className="sm:col-span-2 rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-600">
              Payments for an existing customer are managed from <b>Customer Details → Payments</b>, so nothing is ever duplicated here.
            </div>
          ) : (
            <PaymentTermsSection
              terms={terms} onChange={setTerm} meta={termMeta}
              onMetaChange={(k, v) => setTermMeta((m) => ({ ...m, [k]: v }))}
              errors={termErrors} total={parsedTerms.total} disabled={saving} lockedKeys={savedTermKeys}
              customTerms={customTerms} onCustomChange={changeCustomTerm} onCustomAdd={addCustomTerm} onCustomRemove={removeCustomTerm}
            />
          )}
          <DocumentsSection items={docItems} onAdd={addFiles} onRemove={removeItem} onDocType={(uid, v) => patchItem(uid, { docType: v })} disabled={saving} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>{retryMode ? "Close" : "Cancel"}</Button>
          <Button className="bg-brand text-zinc-900 hover:bg-brand-hover font-semibold" onClick={save} disabled={saving} data-testid="customer-save-button">
            {saving ? "Saving..." : retryMode ? "Retry failed items" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const cardStyles = {
  royal: { icon: "bg-blue-50 text-blue-600", border: "border-blue-100" },
  purple: { icon: "bg-violet-50 text-violet-600", border: "border-violet-100" },
  orange: { icon: "bg-orange-50 text-orange-600", border: "border-orange-100" },
  green: { icon: "bg-emerald-50 text-emerald-600", border: "border-emerald-100" },
  red: { icon: "bg-red-50 text-red-500", border: "border-red-100" },
  amber: { icon: "bg-amber-50 text-amber-600", border: "border-amber-100" },
  teal: { icon: "bg-teal-50 text-teal-600", border: "border-teal-100" },
};

function SummaryCard({ title, value, icon: Icon, tone, meta, onClick, active }) {
  const style = cardStyles[tone] || cardStyles.royal;
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-left min-w-0 rounded-xl border bg-white p-4 shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-brand/30 ${style.border} ${active ? "ring-2 ring-blue-200" : ""}`}
    >
      <div className="flex items-start gap-3">
        <div className={`h-10 w-10 shrink-0 rounded-xl flex items-center justify-center ${style.icon}`}><Icon className="h-5 w-5" /></div>
        <div className="min-w-0">
          <div className="text-[11px] font-semibold text-zinc-500 truncate">{title}</div>
          <div className="mt-1 font-heading text-xl sm:text-2xl font-bold text-slate-900">{typeof value === "number" ? value.toLocaleString("en-IN") : "—"}</div>
          <div className="mt-1 text-[11px] text-zinc-500">{meta}</div>
        </div>
      </div>
    </button>
  );
}

function DragTarget({ label, icon: Icon, tone = "blue", onDropCustomer, onClick, busy }) {
  const tones = {
    blue: "border-blue-100 bg-blue-50/50 text-blue-700",
    purple: "border-violet-100 bg-violet-50/50 text-violet-700",
    orange: "border-orange-100 bg-orange-50/50 text-orange-700",
    green: "border-emerald-100 bg-emerald-50/50 text-emerald-700",
    red: "border-red-100 bg-red-50/50 text-red-600",
    teal: "border-teal-100 bg-teal-50/50 text-teal-700",
  };
  return (
    <button
      type="button"
      className={`w-full flex items-center gap-3 rounded-xl border px-3 py-3 text-left transition-all hover:shadow-sm hover:-translate-y-px ${tones[tone]}`}
      onDragOver={(e) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "move"; }}
      onDrop={(e) => { e.preventDefault(); onDropCustomer?.(e.dataTransfer?.getData("text/plain") || null); }}
      onClick={() => onClick?.()}
      disabled={busy}
      title="Drag a customer row here to update it"
    >
      <span className="text-zinc-400"><GripVertical className="h-4 w-4" /></span>
      <span className="h-8 w-8 rounded-full bg-white/80 flex items-center justify-center shrink-0"><Icon className="h-4 w-4" /></span>
      <span className="text-sm font-semibold truncate flex-1">{label}</span>
      {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <span className="text-[10px] text-zinc-400">Drop</span>}
    </button>
  );
}

export default function CustomerManagement() {
  const { rows, loading, load, create, update } = useCrud("customers", { pollMs: 20000, fetchAll: true });
  const navigate = useNavigate();
  const [primeStats, setPrimeStats] = useState(null);
  const [primeStatsLoading, setPrimeStatsLoading] = useState(true);
  const [primeStatsError, setPrimeStatsError] = useState("");
  const loadPrimeStats = useCallback(async () => {
    setPrimeStatsLoading(true);
    try {
      const { data } = await api.get("/admin/prime-customers/stats");
      setPrimeStats(data || null);
      setPrimeStatsError("");
    } catch (e) {
      setPrimeStatsError(describeApiError(e, "Unable to load Prime Customer statistics."));
    } finally { setPrimeStatsLoading(false); }
  }, []);
  useEffect(() => {
    loadPrimeStats();
    const onChange = (event) => {
      const resources = event?.detail?.resources || [];
      if (resources.some((resource) => ["prime-customers", "prime-service-bookings", "bookings", "*"].includes(resource))) loadPrimeStats();
    };
    window.addEventListener("ntaxco:data-changed", onChange);
    const onVisible = () => { if (!document.hidden) loadPrimeStats(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.removeEventListener("ntaxco:data-changed", onChange); document.removeEventListener("visibilitychange", onVisible); };
  }, [loadPrimeStats]);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [detailTab, setDetailTab] = useState("services");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState({ key: "business_name", dir: 1 });
  const [dragCustomerId, setDragCustomerId] = useState(null);
  const [dropBusy, setDropBusy] = useState(false);
  const [bulkStatus, setBulkStatus] = useState("");
  const [catalog, setCatalog] = useState([]);   // every service in GET /services (real ids + categories)
  const [serviceGroups, setServiceGroups] = useState([]); // GET /admin/service-categories: real services under GST / Income Tax / TDS / ROC / Accounting / Others
  const [employees, setEmployees] = useState([]);
  const [agents, setAgents] = useState([]);
  const [cardFilter, setCardFilter] = useState(null); // { kind: "service" | "filing", value }
  // Dashboard counts always come from the database (GET /admin/customers/summary), never from the
  // rows currently held in the browser.
  const [summaryData, setSummaryData] = useState(null); // null = not loaded yet (cards show "—", never a fake 0)
  const [summaryError, setSummaryError] = useState("");
  const loadSummary = useCallback(async () => {
    try {
      const { data } = await api.get("/admin/customers/summary");
      if (data?.data && typeof data.data.total === "number") { setSummaryData(data.data); setSummaryError(""); }
      else setSummaryError("The customer summary response was not in the expected format.");
    } catch (e) {
      // Keep the last confirmed counts (if any) but tell the admin why they may be stale.
      setSummaryError(describeApiError(e, "Unable to load customer counts"));
    }
  }, []);
  useEffect(() => {
    loadSummary();
    const onChange = (e) => { const r = e?.detail?.resources || []; if (r.includes("customers") || r.includes("bookings") || r.includes("*")) loadSummary(); };
    const tick = () => { if (!document.hidden) loadSummary(); };
    window.addEventListener("ntaxco:data-changed", onChange);
    const timer = window.setInterval(tick, 20000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.removeEventListener("ntaxco:data-changed", onChange); window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [loadSummary]);
  const pageSize = 15;
  const [searchParams, setSearchParams] = useSearchParams();

  // Deep link from a notification: /admin/customers?open=<customer id>
  useEffect(() => {
    const wanted = searchParams.get("open");
    if (!wanted || !rows.length) return;
    const match = rows.find((r) => r.id === wanted);
    if (match) {
      setDetailTab("services");
      setSelectedCustomer(match);
      setTimeout(() => document.getElementById("customer-detail-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
    } else if (!loading) {
      toast.error("That customer could not be found.");
    }
    setSearchParams({}, { replace: true });
  }, [rows, loading, searchParams, setSearchParams]);

  useEffect(() => {
    setSelectedCustomer((current) => {
      if (!current) return null;
      return rows.find((r) => r.id === current.id) || null;
    });
  }, [rows]);

  useEffect(() => {
    let active = true;
    const loadCatalog = () => Promise.all([
      api.get("/services", { params: { page: 1, page_size: 500 } }).then(({ data }) => {
        if (active) setCatalog(Array.isArray(data?.data) ? data.data.filter((x) => x?.id) : []);
      }).catch(() => {}),
      api.get("/admin/service-categories").then(({ data }) => {
        if (active) setServiceGroups(Array.isArray(data?.data) ? data.data : []);
      }).catch(() => {}),
      api.get("/employees", { params: { page: 1, page_size: 500 } }).then(({ data }) => {
        if (active) setEmployees(Array.isArray(data?.data) ? data.data.filter((x) => x?.id && String(x.status || "Active").toLowerCase() !== "inactive") : []);
      }).catch(() => {}),
      api.get("/agents", { params: { page: 1, page_size: 500 } }).then(({ data }) => {
        if (active) setAgents(Array.isArray(data?.data) ? data.data.filter((x) => x?.id && String(x.status || "Active").toLowerCase() !== "inactive") : []);
      }).catch(() => {}),
    ]);
    loadCatalog();
    const onChange = (e) => { const r = e?.detail?.resources || []; if (r.includes("services") || r.includes("*")) loadCatalog(); };
    window.addEventListener("ntaxco:data-changed", onChange);
    return () => { active = false; window.removeEventListener("ntaxco:data-changed", onChange); };
  }, []);

  // The three category targets resolve to a real service id from the live catalog (category first,
  // then name); nothing is hardcoded. A category with no service in the catalog has no id -> not droppable.
  const serviceTargets = useMemo(() => {
    const live = catalog.filter((x) => !["inactive", "disabled"].includes(String(x.status || "").toLowerCase()));
    const pick = (cat, aliases) => {
      const want = new Set([cat.toLowerCase(), ...aliases]);
      const hit = live.find((x) => want.has(String(x.category || "").trim().toLowerCase()))
        || live.find((x) => want.has(String(x.name || x.title || "").trim().toLowerCase()));
      return hit?.id;
    };
    return { GST: pick("GST", []), TDS: pick("TDS", []), "Income Tax": pick("Income Tax", ["income-tax", "itr"]) };
  }, [catalog]);
  const counts = useMemo(() => {
    const d = summaryData;
    return {
      total: d ? d.total : null, gst: d ? d.gst : null, tds: d ? d.tds : null, income: d ? d.income_tax : null,
      paid: d ? d.paid : null, pending: d ? d.pending : null, processing: d ? d.processing : null, completed: d ? d.completed : null,
    };
  }, [summaryData]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const bucket = (v) => { const x = String(v || "").trim().toLowerCase(); return x === "income-tax" || x === "itr" ? "income tax" : x; };
    let out = rows.filter((r) => {
      if (statusFilter !== "All" && String(r.status || "") !== statusFilter && String(r.filing_status || "") !== statusFilter) return false;
      if (cardFilter?.kind === "service") {
        const cats = [r.service_type, ...(Array.isArray(r.service_categories) ? r.service_categories : [])].map(bucket);
        if (!cats.includes(bucket(cardFilter.value))) return false;
      }
      if (cardFilter?.kind === "filing" && String(r.filing_status || "").toLowerCase() !== cardFilter.value.toLowerCase()) return false;
      if (!q) return true;
      return ["cust_id","business_name","gst_number","business_type","state","assigned_employee","assigned_agent","email","mobile","service_type","service_categories","filing_status"]
        .some((key) => String(r[key] ?? "").toLowerCase().includes(q));
    });
    if (sort.key) {
      out = [...out].sort((a, b) => {
        const av = String(a[sort.key] ?? "").toLowerCase(), bv = String(b[sort.key] ?? "").toLowerCase();
        if (av < bv) return -1 * sort.dir;
        if (av > bv) return 1 * sort.dir;
        return 0;
      });
    }
    return out;
  }, [rows, query, statusFilter, sort, cardFilter]);

  useEffect(() => setPage(1), [query, statusFilter, cardFilter]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageRows = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const pageSelected = pageRows.length > 0 && pageRows.every((r) => selectedIds.has(r.id));

  const toggleSort = (key) => setSort((s) => ({ key, dir: s.key === key ? -s.dir : 1 }));
  const toggleAll = () => {
    const next = new Set(selectedIds);
    if (pageSelected) pageRows.forEach((r) => next.delete(r.id)); else pageRows.forEach((r) => next.add(r.id));
    setSelectedIds(next);
  };
  const toggleOne = (id) => setSelectedIds((old) => { const n = new Set(old); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const openAdd = () => { setEditing(null); setFormOpen(true); };
  const openEdit = (customer) => { setEditing(customer); setFormOpen(true); };
  const refresh = async () => { await load(); };
  // After Add Customer: refresh the list/summary and open the new customer's details (payments + documents).
  const handleCustomerSaved = async (customer, info = {}) => {
    await Promise.all([load({ silent: true }), loadSummary()]);
    if (info.created && customer?.id) {
      const fresh = await api.get(`/customers/${encodeURIComponent(customer.id)}`).then((r) => r.data?.data).catch(() => customer);
      selectAndView(fresh || customer, "payments", "customer-detail-panel");
    } else if (selectedCustomer?.id === customer?.id) {
      const fresh = await api.get(`/customers/${encodeURIComponent(customer.id)}`).then((r) => r.data?.data).catch(() => null);
      if (fresh) setSelectedCustomer(fresh);
    }
  };
  const selectAndView = (customer, tab = "services", sectionId = null) => {
    setDetailTab(tab);
    setSelectedCustomer(customer);
    setTimeout(() => {
      const target = sectionId
        ? document.getElementById(sectionId)
        : document.getElementById("customer-detail-panel");
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
  };

  const applyDrop = async (targetType, targetValue, displayName, droppedId = null) => {
    // The real customer id: the row that was actually dragged (read from the drop event), else the
    // single checked row, else the customer open in the detail panel.
    const id = droppedId || dragCustomerId || (selectedIds.size === 1 ? [...selectedIds][0] : null) || selectedCustomer?.id;
    if (!id) return toast.info("Drag a customer row onto a target, or select one customer first.");
    setDropBusy(true);
    try {
      // Nothing in the UI changes until the backend confirms; a failure leaves the previous state.
      const { data } = await api.post("/admin/workflows/drag-drop", {
        source_type: "customer", target_type: targetType, source_id: id, target_value: targetValue,
      });
      await Promise.all([load({ silent: true }), loadSummary()]);
      // Booking history, service status, filing modules and the open customer panel all listen for this.
      notifyDataChanged(["customers", "bookings", "invoices", "payments", "projects", "gst", "itr", "tds", "roc"], "customers-drop");
      const msg = data?.message || `${displayName} updated successfully`;
      if (/^No change needed/i.test(msg)) toast.info(msg); else toast.success(msg);
    } catch (e) {
      toast.error(`${displayName}: ${describeApiError(e, "The update could not be saved. The previous state was kept.")}${e?.response?.status ? ` (HTTP ${e.response.status})` : ""}`);
    } finally { setDragCustomerId(null); setDropBusy(false); }
  };

  const doBulkStatus = async () => {
    if (!bulkStatus || !selectedIds.size) return;
    setDropBusy(true);
    try {
      await Promise.all([...selectedIds].map((id) => update(id, { status: bulkStatus })));
      toast.success(`${selectedIds.size} customer(s) updated successfully.`);
      setSelectedIds(new Set());
      setBulkStatus("");
      await load();
    } catch (e) {
      toast.error(describeApiError(e, "Bulk status update failed"));
    } finally { setDropBusy(false); }
  };

  const deleteCustomer = async (customer) => {
    try {
      // The eligibility endpoint is authoritative; the DELETE endpoint rechecks it again.
      const { data } = await api.get(`/customers/${encodeURIComponent(customer.id)}/deletion-eligibility`);
      const eligibility = data?.data || data;
      if (!eligibility?.eligible) {
        const reasons = Array.isArray(eligibility?.reasons) && eligibility.reasons.length
          ? eligibility.reasons.join("\n")
          : "The customer does not currently meet the deletion policy.";
        toast.error(`Customer cannot be removed yet: ${reasons}`);
        return;
      }
      const name = customer.business_name || customer.name || customer.id;
      const confirmed = window.confirm(
        `Remove customer ${name} (${customer.cust_id || customer.id}) from active Customer Management?\n\n` +
        `All service bookings must be completed or cancelled, with no outstanding balances or unresolved payments. ` +
        `This is a soft deletion: historical bookings, invoices, payments, documents and audit records will be retained.`
      );
      if (!confirmed) return;
      await api.delete(`/customers/${encodeURIComponent(customer.id)}`);
      if (selectedCustomer?.id === customer.id) setSelectedCustomer(null);
      notifyDataChanged(["customers", "bookings", "invoices", "payments"], "customers");
      toast.success("Customer removed from active management; history retained.");
      await Promise.all([load(), loadSummary()]);
    } catch (e) {
      // The API client surfaces server validation details; do not hide or bypass a rejection.
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 409) {
        const reasons = Array.isArray(detail?.reasons) ? detail.reasons.join("\n") : (detail?.message || "Eligibility changed. Refresh and review the customer.");
        toast.error(reasons);
      } else {
        toast.error(describeApiError(e, "Unable to remove customer. Please try again."));
      }
    }
  };

  const exportColumns = [
    { key: "cust_id", label: "ID" }, { key: "business_name", label: "Business Name" },
    { key: "gst_number", label: "GSTIN" }, { key: "pan", label: "PAN" },
    { key: "business_type", label: "Type" }, { key: "service_type", label: "Service" },
    { key: "state", label: "State" }, { key: "assigned_employee", label: "Consultant" },
    { key: "outstanding", label: "Outstanding" }, { key: "filing_status", label: "Payment / Filing Status" },
    { key: "status", label: "Customer Status" },
  ];

  const pct = (n) => (counts.total === null ? "Loading…" : counts.total ? `${Math.round((n / counts.total) * 100)}% of total` : "0% of total");
  const summary = [
    { title: "Total Customers", value: counts.total, icon: Users, tone: "royal", meta: counts.total === null ? "Loading…" : "Live database total", filter: "All" },
    { title: "GST Customers", value: counts.gst, icon: FileText, tone: "royal", meta: pct(counts.gst), service: "GST" },
    { title: "TDS Customers", value: counts.tds, icon: FileCheck2, tone: "purple", meta: pct(counts.tds), service: "TDS" },
    { title: "Income Tax Customers", value: counts.income, icon: Landmark, tone: "orange", meta: pct(counts.income), service: "Income Tax" },
    { title: "Paid Customers", value: counts.paid, icon: CheckCircle2, tone: "green", meta: pct(counts.paid), filing: "Paid" },
    { title: "Pending", value: counts.pending, icon: Clock3, tone: "red", meta: pct(counts.pending), filing: "Pending" },
    { title: "Processing", value: counts.processing, icon: LoaderCircle, tone: "amber", meta: pct(counts.processing), filing: "Processing" },
    { title: "Completed", value: counts.completed, icon: CheckCircle, tone: "teal", meta: pct(counts.completed), filing: "Completed" },
  ];

  const handleSummaryClick = (item) => {
    setQuery("");
    setStatusFilter("All");
    if (item.filter === "All") setCardFilter(null);
    else if (item.service) setCardFilter({ kind: "service", value: item.service });
    else if (item.filing) setCardFilter({ kind: "filing", value: item.filing });
  };

  const renderSortHeader = (label, key) => (
    <button type="button" onClick={() => toggleSort(key)} className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wide text-zinc-500 hover:text-zinc-900">
      {label}<span className="text-[10px]">{sort.key === key ? (sort.dir === 1 ? "↑" : "↓") : "↕"}</span>
    </button>
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Customers"
        breadcrumb={["Super Admin", "Customers"]}
        subtitle="Manage customers — connected to the shared NTAXCO backend."
      />

      {summaryError && (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          <span className="font-semibold">Customer counts could not be refreshed:</span>
          <span className="min-w-0 flex-1">{summaryError}</span>
          <Button size="sm" variant="outline" className="h-7 border-red-200 bg-white text-red-700" onClick={loadSummary}>Retry</Button>
        </div>
      )}

      <section className="rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 to-white p-4 sm:p-5 shadow-sm" aria-labelledby="prime-customers-card-title">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-12 w-12 shrink-0 rounded-xl bg-amber-100 text-amber-700 flex items-center justify-center"><UserRoundPlus className="h-6 w-6" /></div>
            <div className="min-w-0">
              <h2 id="prime-customers-card-title" className="font-heading text-base sm:text-lg font-bold text-slate-900">Prime Service Customers</h2>
              <p className="text-xs sm:text-sm text-slate-500">Manage guest customers and their Prime Service booking history.</p>
              {primeStatsError ? <div role="alert" className="mt-1 text-xs text-red-600">{primeStatsError}</div> : <p className="mt-1 text-xs text-slate-500">{primeStatsLoading ? "Loading customer statistics…" : `${Number(primeStats?.new_or_unassigned ?? 0).toLocaleString("en-IN")} new or unassigned`}</p>}
            </div>
          </div>
          <div className="flex items-center gap-3 sm:justify-end">
            <div className="sm:text-right"><p className="text-xs font-semibold uppercase tracking-wide text-amber-800">Total Prime Customers</p><p className="text-2xl font-black text-slate-900">{primeStatsLoading ? "…" : primeStats ? Number(primeStats.total_customers ?? 0).toLocaleString("en-IN") : "—"}</p></div>
            {primeStatsError && <Button type="button" size="sm" variant="outline" onClick={loadPrimeStats}>Retry</Button>}
            <Button type="button" onClick={() => navigate("/admin/prime-customers")} className="bg-amber-500 hover:bg-amber-600 text-slate-950 font-semibold">View Prime Customers</Button>
          </div>
        </div>
      </section>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        {summary.map((item) => (
          <SummaryCard key={item.title} {...item} onClick={() => handleSummaryClick(item)} active={
            (item.filter === "All" && !query && statusFilter === "All" && !cardFilter) ||
            (item.service && cardFilter?.kind === "service" && cardFilter.value === item.service) ||
            (item.filing && cardFilter?.kind === "filing" && cardFilter.value === item.filing)
          } />
        ))}
      </div>

      <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={openAdd} className="bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg"><Plus className="h-4 w-4 mr-1.5" />Add Customer</Button>
          <Button variant="outline" className="border-zinc-300 rounded-lg" onClick={() => setImportOpen(true)}><UploadCloud className="h-4 w-4 mr-1.5" />Import</Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="outline" className="border-zinc-300 rounded-lg"><Download className="h-4 w-4 mr-1.5" />Export</Button></DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="bg-white">
              <DropdownMenuItem onClick={() => exportCSV("Customers", exportColumns, filtered)}>Export CSV</DropdownMenuItem>
              <DropdownMenuItem onClick={() => exportExcel("Customers", exportColumns, filtered)}>Export Excel</DropdownMenuItem>
              <DropdownMenuItem onClick={() => exportPDF("Customers", exportColumns, filtered)}>Export PDF</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="outline" className="border-zinc-300 rounded-lg" onClick={() => { const id = selectedIds.values().next().value; const customer = filtered.find((r) => r.id === id); if (customer) selectAndView(customer); else toast.info("Select a customer to view."); }} disabled={selectedIds.size !== 1}><Eye className="h-4 w-4 mr-1.5" />View</Button>
          <Button variant="outline" className="border-zinc-300 rounded-lg" onClick={() => printRows("Customers", exportColumns, filtered)}><Printer className="h-4 w-4 mr-1.5" />Print</Button>
          {selectedIds.size > 0 && (
            <div className="flex items-center gap-2 ml-1">
              <span className="text-xs font-medium text-zinc-500">{selectedIds.size} selected</span>
              <Select value={bulkStatus} onValueChange={setBulkStatus}>
                <SelectTrigger className="w-40 h-9 border-zinc-300"><SelectValue placeholder="Bulk status" /></SelectTrigger>
                <SelectContent className="bg-white">{STATUS_FULL.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
              </Select>
              <Button size="sm" onClick={doBulkStatus} disabled={!bulkStatus || dropBusy} className="bg-emerald-600 hover:bg-emerald-700 text-white">Apply</Button>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search customer..." className="pl-9 h-10 border-zinc-300 rounded-lg" />
          </div>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-36 h-10 border-zinc-300 rounded-lg"><SelectValue /></SelectTrigger>
            <SelectContent className="bg-white">
              <SelectItem value="All">All Status</SelectItem>{STATUS_FULL.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button variant="outline" size="icon" className="h-10 w-10 border-zinc-300 rounded-lg" title="Filters"><SlidersHorizontal className="h-4 w-4" /></Button>
          <Button variant="outline" size="icon" className="h-10 w-10 border-zinc-300 rounded-lg" title="Refresh" onClick={refresh} disabled={loading}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /></Button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_255px] gap-4 items-start">
        <div className="rounded-xl border border-zinc-200 bg-white shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-[1050px] w-full text-sm">
              <thead className="bg-slate-50 border-b border-zinc-200">
                <tr>
                  <th className="w-10 px-3 py-3 text-left"><input type="checkbox" aria-label="Select all customers" checked={pageSelected} onChange={toggleAll} /></th>
                  {[
                    ["ID","cust_id"],["Business Name","business_name"],["GSTIN","gst_number"],["Type","business_type"],["State","state"],
                    ["Consultant","assigned_employee"],["Outstanding","outstanding"],["Status","status"],
                  ].map(([label,key]) => <th key={key} className="px-3 py-3 text-left whitespace-nowrap">{renderSortHeader(label,key)}</th>)}
                  <th className="px-3 py-3 text-left text-[11px] font-bold uppercase tracking-wide text-zinc-500">Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading ? Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="border-b border-zinc-100"><td colSpan={10} className="px-4 py-5"><div className="h-5 bg-zinc-100 rounded animate-pulse" /></td></tr>
                )) : pageRows.length === 0 ? (
                  <tr><td colSpan={10} className="px-6 py-12 text-center"><div className="text-sm font-semibold text-zinc-700">No customers found</div><div className="text-xs text-zinc-500 mt-1">Try changing your search or filters.</div></td></tr>
                ) : pageRows.map((r) => (
                  <tr
                    key={r.id}
                    draggable
                    onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", r.id); setDragCustomerId(r.id); }}
                    onDragEnd={() => setDragCustomerId(null)}
                    className={`border-b border-zinc-100 hover:bg-blue-50/30 transition-colors ${dragCustomerId === r.id ? "bg-blue-50" : ""}`}
                  >
                    <td className="px-3 py-3"><input type="checkbox" aria-label={`Select ${r.business_name || r.cust_id}`} checked={selectedIds.has(r.id)} onChange={() => toggleOne(r.id)} /></td>
                    <td className="px-3 py-3 font-semibold text-blue-700 whitespace-nowrap">{r.cust_id || r.id}</td>
                    <td className="px-3 py-3 font-medium text-slate-900">{r.business_name || "—"}</td>
                    <td className="px-3 py-3 text-zinc-600">{r.gst_number || "—"}</td>
                    <td className="px-3 py-3 text-zinc-600">{r.business_type || "—"}</td>
                    <td className="px-3 py-3 text-zinc-600">{r.state || "—"}</td>
                    <td className="px-3 py-3 text-zinc-700">{r.assigned_employee || "—"}</td>
                    <td className="px-3 py-3 font-medium text-zinc-800">{inr(r.outstanding)}</td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap items-center gap-1">
                        <StatusBadge value={r.filing_status || r.status} />
                        {r.status && r.status !== "Active" && r.status !== r.filing_status && <StatusBadge value={r.status} />}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1">
                        <Button size="sm" className="h-8 px-3 bg-blue-600 hover:bg-blue-700 text-white" onClick={() => selectAndView(r)}><Eye className="h-3.5 w-3.5 mr-1" />View</Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Edit" onClick={() => openEdit(r)}><Pencil className="h-4 w-4 text-blue-600" /></Button>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-8 w-8" title="More"><MoreVertical className="h-4 w-4 text-zinc-500" /></Button></DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="bg-white w-52">
                            <DropdownMenuItem onClick={() => selectAndView(r)}><Eye className="h-4 w-4 mr-2" />View Customer</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => openEdit(r)}><Pencil className="h-4 w-4 mr-2" />Edit Customer</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => toast.info("Use the Drag & Drop panel to assign a service.")}><BriefcaseBusiness className="h-4 w-4 mr-2" />Assign Service</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => toast.info("Use the existing customer edit form to assign an employee.")}><UserPlus className="h-4 w-4 mr-2" />Assign Employee / Agent</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => selectAndView(r, "details", "customer-projects-section")}><FolderKanban className="h-4 w-4 mr-2" />View Projects</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => selectAndView(r, "payments")}><CircleDollarSign className="h-4 w-4 mr-2" />View Payments</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => selectAndView(r, "services", "customer-documents-section")}><FileArchive className="h-4 w-4 mr-2" />View Documents</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => openEdit(r)}><SlidersHorizontal className="h-4 w-4 mr-2" />Change Status</DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem className="text-red-600 focus:text-red-600" onClick={() => deleteCustomer(r)}><Trash2 className="h-4 w-4 mr-2" />Delete Customer</DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!loading && filtered.length > 0 && (
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 px-4 py-3 text-xs text-zinc-500">
              <span>Showing {(currentPage - 1) * pageSize + 1}–{Math.min(currentPage * pageSize, filtered.length)} of {filtered.length}</span>
              <div className="flex items-center gap-1">
                <Button variant="outline" size="icon" className="h-8 w-8" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}><ChevronLeft className="h-4 w-4" /></Button>
                <span className="px-2 font-semibold text-zinc-700">{currentPage} / {totalPages}</span>
                <Button variant="outline" size="icon" className="h-8 w-8" disabled={currentPage >= totalPages} onClick={() => setPage(currentPage + 1)}><ChevronRight className="h-4 w-4" /></Button>
              </div>
            </div>
          )}
        </div>

        <aside className="rounded-xl border border-blue-100 bg-white shadow-sm p-3.5">
          <div className="flex items-start gap-2 mb-3">
            <div className="h-9 w-9 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center"><GripVertical className="h-5 w-5" /></div>
            <div><h2 className="font-heading text-sm font-bold text-slate-900">Drag &amp; Drop Customers</h2><p className="text-[11px] text-zinc-500 mt-0.5">Move customers to update their status or category.</p></div>
          </div>
          <div className="space-y-2">
            {[
              ...employees.map((employee) => ({
                label: `Employee: ${employee.name || employee.id}`,
                icon: UserRound,
                tone: "teal",
                type: "employee",
                value: employee.id,
              })),
              ...agents.map((agent) => ({
                label: `Agent: ${agent.name || agent.id}`,
                icon: UserPlus,
                tone: "purple",
                type: "agent",
                value: agent.id,
              })),
              { label: "Pending", icon: Clock3, tone: "red", type: "payment-status", value: "Pending" },
              { label: "Processing", icon: LoaderCircle, tone: "orange", type: "payment-status", value: "Processing" },
              { label: "Completed", icon: CheckCircle, tone: "teal", type: "payment-status", value: "Completed" },
              { label: "Yearly Payment", icon: CalendarDays, tone: "purple", type: "payment-frequency", value: "Yearly" },
              { label: "GST Customer", icon: FileText, tone: "blue", type: "service", service: "GST" },
              { label: "TDS Customer", icon: FileCheck2, tone: "purple", type: "service", service: "TDS" },
              { label: "Income Tax Customer", icon: Landmark, tone: "orange", type: "service", service: "Income Tax" },
              { label: "New Customer", icon: UserPlus, tone: "green", type: "status", value: "Active" },
            ].map((t) => {
              const value = t.type === "service" ? serviceTargets[t.service] : t.value;
              const go = (id) => value ? applyDrop(t.type, value, t.label, id) : toast.error(`${t.service} service is not available in the service catalog.`);
              return <DragTarget key={t.label} label={t.label} icon={t.icon} tone={t.tone} onDropCustomer={go} onClick={() => go(null)} busy={dropBusy} />;
            })}
          </div>
          {serviceGroups.length > 0 && (
            <div className="mt-3" data-testid="assign-service-groups">
              <div className="text-[11px] font-semibold text-zinc-500 mb-1.5">Assign any service</div>
              <div className="space-y-2 max-h-72 overflow-y-auto pr-0.5">
                {serviceGroups.map((group) => (
                  <div key={group.category}>
                    <div className="text-[10px] font-bold uppercase tracking-wide text-blue-700 mb-1">{group.category}</div>
                    {group.services.length === 0
                      ? <div className="text-[11px] text-zinc-400 pl-1 pb-1">No {group.category} service in the catalogue yet.</div>
                      : <div className="space-y-1.5">{group.services.map((svc) => (
                        <DragTarget key={svc.id} label={svc.name} icon={BriefcaseBusiness} tone="blue"
                          onDropCustomer={(id) => applyDrop("service", svc.id, svc.name, id)} onClick={() => applyDrop("service", svc.id, svc.name, null)} busy={dropBusy} />
                      ))}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="mt-3 rounded-lg bg-slate-50 border border-slate-100 p-2 text-[10px] text-zinc-500">
            Drag any customer row here, or select a row and tap a target. Updates are saved through the existing backend workflow endpoint.
          </div>
        </aside>
      </div>

      {selectedCustomer && (
        <section id="customer-detail-panel" className="rounded-xl border border-blue-100 bg-white shadow-sm overflow-hidden">
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-blue-100 bg-blue-50/50">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-full bg-blue-600 text-white flex items-center justify-center"><Users className="h-5 w-5" /></div>
              <div><h2 className="font-heading text-base font-bold text-slate-900">View Customer</h2><p className="text-xs text-blue-700">Customer details, services, payments and more.</p></div>
            </div>
            <Button variant="outline" size="sm" className="border-blue-200 bg-white" onClick={() => setSelectedCustomer(null)}><X className="h-4 w-4 mr-1" />Hide</Button>
          </div>
          <div className="px-4 py-4">
            <div className="grid grid-cols-2 md:grid-cols-7 gap-3 pb-4 border-b border-zinc-100">
              {[
                ["Customer ID", selectedCustomer.cust_id || selectedCustomer.id],
                ["Business Name", selectedCustomer.business_name || "—"],
                ["GSTIN", selectedCustomer.gst_number || "—"],
                ["Type", selectedCustomer.business_type || "—"],
                ["State", selectedCustomer.state || "—"],
                ["Consultant", selectedCustomer.assigned_employee || "—"],
              ].map(([label,value]) => <div key={label} className="min-w-0"><div className="text-[10px] text-zinc-500">{label}</div><div className="text-sm font-semibold text-slate-900 truncate mt-0.5">{value}</div></div>)}
              <div><div className="text-[10px] text-zinc-500">Status</div><div className="mt-1"><StatusBadge value={selectedCustomer.filing_status || selectedCustomer.status} /></div></div>
            </div>
            <div className="mt-4">
              <CustomerDetailTabs key={`${selectedCustomer.id}-${detailTab}`} customer={selectedCustomer} initialTab={detailTab} onEdit={() => openEdit(selectedCustomer)} onRefresh={refresh} />
            </div>
          </div>
        </section>
      )}

      <CustomerFormDialog open={formOpen} onOpenChange={setFormOpen} editing={editing} onSaved={handleCustomerSaved} />
      <ImportCustomersDialog open={importOpen} onOpenChange={setImportOpen} onImported={refresh} />
    </div>
  );
}
