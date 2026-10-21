import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatDate, formatTime } from "@/lib/notificationFormat";
import { toast } from "sonner";
import api, { describeApiError } from "@/lib/api";
import { useCrud } from "@/hooks/useCrud";
import { notifyDataChanged } from "@/lib/dataSync";
import { inr } from "@/lib/utils";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatusBadge } from "@/components/shared/StatusBadge";
import RelationshipPanel from "@/components/shared/RelationshipPanel";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { isImageMime, formatBytes, resolveMime } from "@/components/admin/CustomerCreateExtras";
import {
  Pencil, Upload, Download, Trash2, Loader2, FileText, ReceiptText, CalendarDays,
  CheckCircle2, Clock3, Landmark, FileCheck2, Building2, WalletCards, BriefcaseBusiness, ImageIcon,
} from "lucide-react";

const DOC_TYPES = [
  "GST Certificate", "PAN Card", "TAN Certificate", "CIN Certificate",
  "Company Registration Certificate", "Address Proof", "Other Document",
];
const ALLOWED_DOC_MIMES = [
  "application/pdf", "image/jpeg", "image/png", "image/webp",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
];
const MAX_DOC_BYTES = 5 * 1024 * 1024;
// Statuses Admin can pick for a booking (all are valid BOOKING_STATUSES on the backend). Legacy "Running" /
// "Confirmed" rows keep their real value: it is appended to the list for that row only, so nothing is rewritten.
const ADMIN_BOOKING_STATUSES = ["Pending", "Confirmed", "Processing", "Completed", "Cancelled"];

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function SectionCard({ title, children, className = "", id }) {
  return (
    <div id={id} className={`rounded-xl border border-zinc-200 bg-white p-4 ${className}`}>
      <div className="text-xs font-bold uppercase tracking-wide text-blue-700 mb-3">{title}</div>
      {children}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex flex-col gap-1 py-2.5 border-b border-zinc-100 last:border-b-0">
      <span className="text-[11px] text-zinc-500">{label}</span>
      <span className="text-sm font-medium text-zinc-800 break-words">{value ?? "—"}</span>
    </div>
  );
}

// Same six categories as the backend (service_category_bucket); used only when a row has no bucket yet.
function bucketOf(label) {
  const v = String(label || "").toLowerCase();
  if (/\bgst/.test(v)) return "GST";
  if (/\btds\b|\btcs\b/.test(v)) return "TDS";
  if (v.includes("income tax") || /\bitr\b/.test(v)) return "Income Tax";
  if (/\broc\b|\bmca\b|\baoc\b|\bmgt\b|\bllp\b/.test(v)) return "ROC";
  if (v.includes("account") || v.includes("bookkeeping") || /\bmis\b/.test(v)) return "Accounting";
  return "Others";
}

// Lazy image thumbnail: list responses never carry file bytes, so the picture is fetched (authorised, one
// document at a time) only when the row is on screen.
function DocThumb({ doc }) {
  const [src, setSrc] = useState("");
  const [failed, setFailed] = useState(false);
  const holder = useRef(null);
  const isImage = isImageMime(doc.mime_type);
  useEffect(() => {
    if (!isImage || src || failed) return undefined;
    let active = true;
    const load = () => api.get(`/documents/${encodeURIComponent(doc.id)}`)
      .then(({ data }) => { if (active && data?.data?.file_data) setSrc(data.data.file_data); else if (active) setFailed(true); })
      .catch(() => { if (active) setFailed(true); });
    if (typeof IntersectionObserver === "undefined" || !holder.current) { load(); return () => { active = false; }; }
    const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) { io.disconnect(); load(); } });
    io.observe(holder.current);
    return () => { active = false; io.disconnect(); };
  }, [doc.id, isImage, src, failed]);
  return (
    <div ref={holder} className="h-10 w-10 shrink-0 rounded-md bg-zinc-100 flex items-center justify-center overflow-hidden">
      {src ? <img src={src} alt={doc.file_name || "Document"} className="h-10 w-10 object-cover" /> : isImage ? <ImageIcon className="h-5 w-5 text-zinc-400" /> : <FileText className="h-5 w-5 text-zinc-400" />}
    </div>
  );
}

function ServiceIcon({ name }) {
  const n = String(name || "").toLowerCase();
  const Icon = n.includes("income") ? Landmark : n.includes("tds") ? FileCheck2 : n.includes("gst") ? FileText : n.includes("roc") ? Building2 : BriefcaseBusiness;
  return <Icon className="h-5 w-5" />;
}

export default function CustomerDetailTabs({ customer, onEdit, onRefresh, initialTab = "services" }) {
  const { rows: allBookings, load: loadBookings } = useCrud("bookings", { pollMs: 15000, fetchAll: true });
  const { rows: allPayments, load: loadPayments, create: createPayment, update: updatePayment, remove: removePayment } = useCrud("payments", { fetchAll: true });
  const { rows: allDocuments, load: loadDocuments, remove: removeDocument } = useCrud("documents", { fetchAll: true });

  const bookings = useMemo(() => allBookings.filter((b) => b.customer_id === customer.id), [allBookings, customer.id]);
  const payments = useMemo(() => allPayments.filter((p) => p.customer_id === customer.id), [allPayments, customer.id]);
  const documents = useMemo(() => allDocuments.filter((d) => d.customer_id === customer.id), [allDocuments, customer.id]);

  // Total Bill / Total Paid / Outstanding come from the backend (GET /admin/customers/{id}/billing), which
  // calculates them from this customer's own bookings, invoices and payment records in MongoDB. The browser
  // never adds numbers up itself, so a figure can only be what the database says.
  const [billing, setBilling] = useState(null);
  const [billingError, setBillingError] = useState("");
  const loadBilling = useCallback(async () => {
    try {
      const { data } = await api.get(`/admin/customers/${encodeURIComponent(customer.id)}/billing`);
      setBilling(data.data); setBillingError("");
    } catch (e) {
      setBillingError(describeApiError(e, "Unable to load billing"));
    }
  }, [customer.id]);
  useEffect(() => {
    setBilling(null);
    let active = true;
    const refresh = () => { if (active) loadBilling(); };
    refresh();
    const onChange = (e) => {
      const r = e?.detail?.resources || [];
      if (["payments", "invoices", "bookings", "customers", "services", "*"].some((x) => r.includes(x))) refresh();
    };
    window.addEventListener("ntaxco:data-changed", onChange);
    const tick = () => { if (!document.hidden) refresh(); };
    const timer = window.setInterval(tick, 15000);
    document.addEventListener("visibilitychange", tick);
    return () => { active = false; window.removeEventListener("ntaxco:data-changed", onChange); window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [loadBilling]);
  // null until the backend has answered (shown as "—", never as a made-up 0).
  const totals = useMemo(() => ({
    billed: billing ? billing.total_billed : null,
    paid: billing ? billing.total_paid : null,
    outstanding: billing ? billing.outstanding : null,
    pendingPayments: billing ? billing.pending_payments : null,
  }), [billing]);
  const money = (v) => (v == null ? "—" : inr(v));

  // Booking history + resolved services straight from MongoDB (newest first).
  const [overview, setOverview] = useState(null);
  const [overviewError, setOverviewError] = useState("");
  const [savingBooking, setSavingBooking] = useState(null); // booking id whose status is being saved
  const savingRef = useRef(null);                         // blocks background refreshes while a save is in flight
  const [statusErrors, setStatusErrors] = useState({});     // booking id -> real backend error
  const loadOverview = useCallback(async () => {
    try {
      const { data } = await api.get(`/admin/customers/${encodeURIComponent(customer.id)}/overview`);
      setOverview(data.data); setOverviewError("");
      return data.data;
    } catch (e) {
      setOverviewError(describeApiError(e, "Unable to load booking history"));
      throw e;
    }
  }, [customer.id]);
  useEffect(() => {
    let active = true;
    const refresh = () => { if (active) loadOverview().catch(() => {}); };
    refresh();
    const onChange = (e) => { if (savingRef.current) return; const r = e?.detail?.resources || []; if (r.includes("bookings") || r.includes("customers") || r.includes("*")) refresh(); };
    window.addEventListener("ntaxco:data-changed", onChange);
    // Always show the latest backend state, even when the change was made on another device.
    const tick = () => { if (!document.hidden && !savingRef.current) refresh(); };
    const timer = window.setInterval(tick, 15000);
    document.addEventListener("visibilitychange", tick);
    return () => { active = false; window.removeEventListener("ntaxco:data-changed", onChange); window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [loadOverview]);

  // Admin changes a booking's status. The UI only changes after the backend confirms; the table is then
  // re-read from GET /admin/customers/{id}/overview so it always shows the value saved in MongoDB.
  const changeBookingStatus = async (b, next) => {
    if (!next || next === b.status || savingRef.current) return;
    savingRef.current = b.booking_id;
    setSavingBooking(b.booking_id);
    setStatusErrors((prev) => { const { [b.booking_id]: _drop, ...rest } = prev; return rest; });
    let saved;
    try {
      const { data } = await api.put(`/bookings/${encodeURIComponent(b.booking_id)}`, { status: next });
      saved = data?.data?.status || next;
    } catch (err) {
      // Not saved: the dropdown is controlled by the saved value, so it still shows the previous status.
      const msg = describeApiError(err, "Unable to update booking status");
      setStatusErrors((prev) => ({ ...prev, [b.booking_id]: msg }));
      toast.error(`Booking ${b.booking_id}: ${msg}`);
      loadOverview().catch(() => {});
      savingRef.current = null;
      setSavingBooking(null);
      return;
    }
    try {
      await loadOverview();                       // booking history + service cards + customer workflow status
      loadBilling();                              // a cancelled booking is no longer billable
      await loadBookings({ silent: true });       // related service/module lists
      if (onRefresh) await onRefresh();           // customer list / counters
      toast.success(`Booking ${b.booking_id} is now ${saved}`);
    } catch (err) {
      toast.warning(`Booking ${b.booking_id} was saved as ${saved}, but the view could not be refreshed: ${describeApiError(err, "refresh failed")}`);
    } finally {
      savingRef.current = null;
      setSavingBooking(null);
      notifyDataChanged(["bookings", "customers", "gst", "itr", "tds", "roc", "invoices"], "customer-booking-history");
    }
  };

  const [activeTab, setActiveTab] = useState(initialTab);
  const [docType, setDocType] = useState(DOC_TYPES[0]);
  const [docName, setDocName] = useState("");
  const [docDescription, setDocDescription] = useState("");
  const [uploading, setUploading] = useState(false);
  const [paymentModal, setPaymentModal] = useState(false);
  const [editingPayment, setEditingPayment] = useState(null);
  const [savingPayment, setSavingPayment] = useState(false);
  const [deletingPayment, setDeletingPayment] = useState(null);
  const [paymentForm, setPaymentForm] = useState({
    term_name: "Term 1", amount: "", payment_date: new Date().toISOString().slice(0, 10),
    payment_method: "UPI", transaction_reference: "", notes: "", status: "Paid",
  });
  const [serviceFilter, setServiceFilter] = useState("All Services");

  // Latest customer record from the backend (overview endpoint); falls back to the row we were given.
  const live = overview?.customer || customer;

  const serviceRows = useMemo(() => {
    const values = [];
    // Real booking records have priority. Prefer the overview (same MongoDB read as Booking History) and
    // fall back to the bookings list until the overview has loaded.
    const full = new Map(bookings.map((b) => [b.id, b]));
    const source = overview?.bookings
      ? overview.bookings.map((h) => ({ ...(full.get(h.booking_id) || {}), id: h.booking_id, service: h.service_name, bucket: h.service_bucket, status: h.status, booking_date: h.booking_date }))
      : bookings;
    // The category row only stands in for a service when there is no booking for it yet; its status is the
    // customer's workflow status (never the account status), so it cannot contradict booking history.
    const hasBookingForCategory = source.some((b) => String(b.service || "").toLowerCase().includes(String(customer.service_type || "").toLowerCase()));
    if (customer.service_type && !hasBookingForCategory) values.push({
      id: `customer-${customer.id}-service`,
      name: customer.service_type, bucket: bucketOf(customer.service_type),
      status: live.filing_status || live.status,
      assigned_employee: customer.assigned_employee,
      start_date: customer.registration_date,
      due_date: null,
      progress: null,
      source: "Customer assignment",
    });
    source.forEach((b) => values.push({
      id: b.id, name: b.service || b.service_id || "Booked service", bucket: b.bucket || bucketOf(b.service),
      status: b.status, assigned_employee: b.assigned_employee || b.assigned_agent,
      start_date: b.booking_date || b.created_at, due_date: b.due_date,
      progress: b.progress, source: "Booking",
    }));
    return values;
  }, [customer, bookings, overview, live]);

  const visibleServices = useMemo(() => serviceFilter === "All Services"
    ? serviceRows
    : serviceRows.filter((s) => (s.bucket || bucketOf(s.name)) === serviceFilter), [serviceRows, serviceFilter]);

  const onDocFile = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    setUploading(true);
    let okCount = 0;
    try {
      for (const f of files) {
        const mime = resolveMime(f);
        const allowed = [...ALLOWED_DOC_MIMES, "application/msword"];
        if (!allowed.includes(mime)) { toast.error(`${f.name}: supported types are PDF, JPG, JPEG, PNG, WEBP, DOC, DOCX, XLSX.`); continue; }
        if (f.size > MAX_DOC_BYTES) { toast.error(`${f.name}: file is too large. Please choose a file under 5MB.`); continue; }
        try {
          const raw = await readFileAsDataUrl(f);
          const file_data = `data:${mime};base64,${raw.slice(raw.indexOf(",") + 1)}`;
          await api.post("/documents", {
            customer_id: customer.id,
            doc_type: docType,
            title: (files.length === 1 && docName.trim()) || docType,
            description: docDescription.trim(),
            file_name: f.name,
            file_size: f.size,
            file_data,
          });
          okCount += 1;
        } catch (err) {
          toast.error(`Unable to upload ${f.name}. ${describeApiError(err, "Please try again.")}`);
        }
      }
      if (okCount) {
        setDocName(""); setDocDescription("");
        toast.success(okCount === 1 ? "Document uploaded" : `${okCount} documents uploaded`);
        await loadDocuments();
        notifyDataChanged(["documents"], "customer-detail-documents");
      }
    } finally { setUploading(false); }
  };

  const openPayment = (payment = null) => {
    if (payment) {
      setEditingPayment(payment);
      setPaymentForm({
        term_name: payment.term_name || payment.term || "Term 1",
        amount: String(payment.amount ?? payment.total ?? ""),
        payment_date: String(payment.payment_date || payment.created_at || "").slice(0, 10) || new Date().toISOString().slice(0, 10),
        payment_method: payment.payment_method || "UPI",
        transaction_reference: payment.transaction_reference || payment.reference_no || "",
        notes: payment.notes || "",
        status: payment.status === "Completed" ? "Paid" : (payment.status || "Paid"),
      });
    } else {
      setEditingPayment(null);
      setPaymentForm({
        term_name: `Term ${Math.max(1, payments.length + 1)}`, amount: "",
        payment_date: new Date().toISOString().slice(0, 10), payment_method: "UPI",
        transaction_reference: "", notes: "", status: "Paid",
      });
    }
    setPaymentModal(true);
  };

  const savePayment = async () => {
    if (savingPayment) return;
    const amount = Number(paymentForm.amount);
    if (!paymentForm.term_name.trim()) return toast.error("Payment term is required.");
    if (!Number.isFinite(amount) || amount <= 0) return toast.error("Payment amount must be greater than zero.");
    if (!paymentForm.payment_date) return toast.error("Payment date is required.");
    const currentOutstanding = Number(totals.outstanding ?? 0);
    // The cap protects invoiced/booked amounts; a customer billed only through payment terms can add further terms.
    const hasInvoicedBilling = Number(billing?.invoice_billed ?? 0) > 0;
    const oldReceived = editingPayment && ["Paid", "Completed", "Partial"].includes(String(editingPayment.status))
      ? Number(editingPayment.amount ?? editingPayment.total ?? 0) : 0;
    if (hasInvoicedBilling && ["Paid", "Partial"].includes(paymentForm.status) && amount > currentOutstanding + oldReceived + 0.009) {
      return toast.error("Payment amount cannot exceed the outstanding balance.");
    }
    setSavingPayment(true);
    try {
      const body = {
        customer_id: customer.id,
        term_name: paymentForm.term_name.trim(),
        amount, total: amount,
        payment_date: paymentForm.payment_date,
        payment_method: paymentForm.payment_method,
        transaction_reference: paymentForm.transaction_reference.trim(),
        reference_no: paymentForm.transaction_reference.trim(),
        notes: paymentForm.notes.trim(),
        status: paymentForm.status,
      };
      if (editingPayment) await updatePayment(editingPayment.id, body);
      else await createPayment(body);
      await Promise.all([loadPayments(), loadBilling()]);
      setPaymentModal(false);
      setEditingPayment(null);
      notifyDataChanged(["payments", "customers", "invoices", "bookings"], "customer-payment");
      toast.success(editingPayment ? "Payment updated" : "Payment recorded");
    } catch (err) {
      toast.error(describeApiError(err, editingPayment ? "Unable to update payment" : "Unable to add payment"));
    } finally { setSavingPayment(false); }
  };

  const confirmDeletePayment = async () => {
    if (!deletingPayment || savingPayment) return;
    setSavingPayment(true);
    try {
      await removePayment(deletingPayment.id);
      await Promise.all([loadPayments(), loadBilling()]);
      notifyDataChanged(["payments", "customers", "invoices", "bookings"], "customer-payment-delete");
      setDeletingPayment(null);
      toast.success("Payment deleted");
    } catch (err) {
      toast.error(describeApiError(err, "Unable to delete payment"));
    } finally { setSavingPayment(false); }
  };

  const previewDocument = async (doc) => {
    try {
      const { data } = await api.get(`/documents/${encodeURIComponent(doc.id)}`);
      const full = data?.data;
      if (!full?.file_data) throw new Error("File data missing");
      // Browsers block top-level navigation to data: URLs, so open the file through a Blob URL instead.
      const [meta, b64] = String(full.file_data).split(",");
      const mime = (/data:([^;]+)/.exec(meta) || [])[1] || "application/octet-stream";
      const bin = atob(b64 || "");
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
      const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
      const win = window.open(url, "_blank", "noopener,noreferrer");
      if (!win) toast.error("Your browser blocked the pop-up. Allow pop-ups for this site to view the file.");
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to preview document"));
    }
  };

  const downloadDocument = async (doc) => {
    try {
      const { data } = await api.get(`/documents/${encodeURIComponent(doc.id)}`);
      const full = data?.data;
      if (!full?.file_data) throw new Error("File data missing");
      const a = document.createElement("a");
      a.href = full.file_data;
      a.download = full.file_name || `${doc.doc_type || "document"}`;
      a.click();
    } catch (err) {
      toast.error(describeApiError(err, "Unable to download document"));
    }
  };

  const [deletingDoc, setDeletingDoc] = useState(null);
  const [docBusy, setDocBusy] = useState(false);
  const confirmDeleteDocument = async () => {
    if (!deletingDoc || docBusy) return;
    setDocBusy(true);
    try { await removeDocument(deletingDoc.id); setDeletingDoc(null); } catch { /* existing hook shows the backend error */ }
    finally { setDocBusy(false); }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 flex-1">
          <div className="rounded-lg border border-zinc-200 bg-white p-3"><div className="text-[10px] uppercase font-bold text-zinc-500">Total Bill</div><div className="mt-1 text-lg font-bold text-slate-900">{money(totals.billed)}</div></div>
          <div className="rounded-lg border border-emerald-100 bg-emerald-50/40 p-3"><div className="text-[10px] uppercase font-bold text-emerald-700">Total Paid</div><div className="mt-1 text-lg font-bold text-emerald-700">{money(totals.paid)}</div></div>
          <div className="rounded-lg border border-red-100 bg-red-50/40 p-3"><div className="text-[10px] uppercase font-bold text-red-600">Outstanding</div><div className="mt-1 text-lg font-bold text-red-600">{money(totals.outstanding)}</div></div>
        </div>
        {onEdit && <Button variant="outline" className="border-zinc-300" onClick={onEdit}><Pencil className="h-4 w-4 mr-1.5" />Edit</Button>}
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="w-full justify-start flex-wrap h-auto bg-transparent border-b border-zinc-200 rounded-none gap-1">
          <TabsTrigger value="services" className="rounded-none border-b-2 border-transparent data-[state=active]:border-blue-600 data-[state=active]:text-blue-700">◉&nbsp; Services</TabsTrigger>
          <TabsTrigger value="payments" className="rounded-none border-b-2 border-transparent data-[state=active]:border-blue-600 data-[state=active]:text-blue-700">▣&nbsp; Payments</TabsTrigger>
          <TabsTrigger value="details" className="rounded-none border-b-2 border-transparent data-[state=active]:border-blue-600 data-[state=active]:text-blue-700">◌&nbsp; Customer Details</TabsTrigger>
          <TabsTrigger value="type" className="rounded-none border-b-2 border-transparent data-[state=active]:border-blue-600 data-[state=active]:text-blue-700">⊞&nbsp; Type</TabsTrigger>
          <TabsTrigger value="status" className="rounded-none border-b-2 border-transparent data-[state=active]:border-blue-600 data-[state=active]:text-blue-700">✓&nbsp; Status</TabsTrigger>
        </TabsList>

        <TabsContent value="services" className="space-y-4 pt-4">
          <div className="flex flex-wrap gap-2">
            {["All Services", "GST", "Income Tax", "TDS", "ROC", "Accounting", "Others"].map((name) => (
              <button key={name} type="button" onClick={() => setServiceFilter(name)} className={`rounded-full border px-4 py-2 text-xs font-semibold transition ${serviceFilter === name ? "border-blue-600 bg-blue-600 text-white" : "border-zinc-200 bg-white text-zinc-600 hover:border-blue-200"}`}>{name}</button>
            ))}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {visibleServices.map((service) => (
              <div key={service.id} className="rounded-xl border border-zinc-200 bg-white p-4 hover:shadow-sm transition-shadow">
                <div className="flex items-start gap-3">
                  <div className="h-10 w-10 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0"><ServiceIcon name={service.name} /></div>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-slate-900 truncate">{service.name}</div>
                    <div className="text-xs text-zinc-500 mt-1">{service.source} · Consultant: {service.assigned_employee || "Unassigned"}</div>
                  </div>
                  <StatusBadge value={service.source === "Booking" ? (service.status || "Pending") : (service.status || live.filing_status || live.status)} />
                </div>
                <div className="grid grid-cols-2 gap-2 mt-4 text-[11px]">
                  <div><span className="text-zinc-400">Start</span><div className="font-medium text-zinc-700">{service.start_date || "—"}</div></div>
                  <div><span className="text-zinc-400">Due</span><div className="font-medium text-zinc-700">{service.due_date || "—"}</div></div>
                </div>
              </div>
            ))}
            {!visibleServices.length && <div className="col-span-full rounded-xl border border-dashed border-zinc-200 p-8 text-center text-sm text-zinc-500">No services linked to this customer yet.</div>}
          </div>

          <SectionCard title="Booking History" id="customer-booking-history">
            {overviewError && <p className="text-sm text-red-600">{overviewError}</p>}
            {!overview && !overviewError && <p className="text-sm text-zinc-500">Loading bookings…</p>}
            {overview && (
              <div className="overflow-x-auto" data-testid="customer-booking-history">
                <table className="min-w-[760px] w-full text-xs">
                  <thead className="bg-slate-50 border-b"><tr>{["Booking ID","Service","Category","Booking Date","Time","Status","Fee","Notes"].map((h) => <th key={h} className="px-3 py-2 text-left font-bold text-zinc-500">{h}</th>)}</tr></thead>
                  <tbody>
                    {overview.bookings.map((b) => (
                      <tr key={b.booking_id} className="border-b border-zinc-100">
                        <td className="px-3 py-2 font-medium">{b.booking_id}</td>
                        <td className="px-3 py-2">{b.service_name || "—"}</td>
                        <td className="px-3 py-2">{b.service_category || "—"}</td>
                        <td className="px-3 py-2">{formatDate(b.booking_date)}</td>
                        <td className="px-3 py-2">{formatTime(b.booking_date)}</td>
                        <td className="px-3 py-2 min-w-[150px]">
                          <div className="flex items-center gap-2">
                            <Select value={b.status || "Pending"} onValueChange={(v) => changeBookingStatus(b, v)} disabled={savingBooking === b.booking_id}>
                              <SelectTrigger className="h-8 w-[130px] border-zinc-300 text-xs" data-testid={`booking-status-${b.booking_id}`} aria-label={`Status for booking ${b.booking_id}`}><SelectValue /></SelectTrigger>
                              <SelectContent className="bg-white">
                                {(ADMIN_BOOKING_STATUSES.includes(b.status) || !b.status ? ADMIN_BOOKING_STATUSES : [...ADMIN_BOOKING_STATUSES, b.status]).map((st) => <SelectItem key={st} value={st}>{st}</SelectItem>)}
                              </SelectContent>
                            </Select>
                            {savingBooking === b.booking_id && <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" />}
                          </div>
                          {statusErrors[b.booking_id] && <div className="mt-1 text-[11px] text-red-600 max-w-[220px] whitespace-normal" role="alert">{statusErrors[b.booking_id]}</div>}
                        </td>
                        <td className="px-3 py-2">{b.amount != null && b.amount !== "" ? inr(b.amount) : "—"}</td>
                        <td className="px-3 py-2 max-w-[220px] truncate" title={b.notes || ""}>{b.notes || "—"}</td>
                      </tr>
                    ))}
                    {!overview.bookings.length && <tr><td colSpan="8" className="p-6 text-center text-zinc-500">No bookings made by this customer yet.</td></tr>}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>

          <SectionCard title="Recent Bookings">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                ["New Bookings", bookings.filter((b) => String(b.status || "").toLowerCase() === "new").length, CalendarDays, "bg-blue-50 text-blue-600"],
                ["In Progress", bookings.filter((b) => ["In Progress","Running","Processing"].includes(b.status)).length, Loader2, "bg-amber-50 text-amber-600"],
                ["Confirmed", bookings.filter((b) => String(b.status || "") === "Confirmed").length, CheckCircle2, "bg-emerald-50 text-emerald-600"],
                ["Completed", bookings.filter((b) => String(b.status || "") === "Completed").length, CheckCircle2, "bg-violet-50 text-violet-600"],
              ].map(([label, value, Icon, cls]) => <div key={label} className={`rounded-lg border p-3 ${cls}`}><Icon className="h-4 w-4" /><div className="text-[11px] font-semibold mt-2">{label}</div><div className="text-xl font-bold">{value}</div></div>)}
            </div>
            <div className="mt-3 flex justify-end"><Button variant="outline" size="sm" className="border-zinc-300" onClick={() => toast.info("Open the existing Bookings module to manage all bookings.")}>View All</Button></div>
          </SectionCard>
        </TabsContent>

        <TabsContent value="payments" className="space-y-4 pt-4">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[
              ["Total Bill", totals.billed, ReceiptText, "text-slate-900", "bg-blue-50 text-blue-600"],
              ["Total Paid", totals.paid, CheckCircle2, "text-emerald-700", "bg-emerald-50 text-emerald-600"],
              ["Outstanding", totals.outstanding, WalletCards, "text-red-600", "bg-red-50 text-red-600"],
              ["Pending Payments", totals.pendingPayments, Clock3, "text-amber-700", "bg-amber-50 text-amber-600"],
            ].map(([label, value, Icon, valueCls, iconCls]) => <div key={label} className="rounded-xl border border-zinc-200 p-4 bg-white"><div className={`h-8 w-8 rounded-lg flex items-center justify-center ${iconCls}`}><Icon className="h-4 w-4" /></div><div className="text-[11px] text-zinc-500 mt-3">{label}</div><div className={`text-lg font-bold mt-1 ${valueCls}`}>{money(value)}</div></div>)}
          </div>
          <SectionCard title="Payment Terms & History">
            <div className="flex justify-end mb-3">
              <Button className="bg-blue-600 hover:bg-blue-700 text-white" onClick={() => openPayment()} disabled={savingPayment}>+ Add Payment</Button>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-[900px] w-full text-xs">
                <thead className="bg-slate-50 border-b"><tr>{["Term","Amount","Date","Payment Method","Reference ID","Status","Actions"].map((h) => <th key={h} className="px-3 py-3 text-left font-bold text-zinc-500">{h}</th>)}</tr></thead>
                <tbody>
                  {payments.map((p) => {
                    const amount = Number(p.total ?? p.amount ?? 0);
                    return <tr key={p.id} className="border-b border-zinc-100">
                      <td className="px-3 py-3 font-medium">{p.term_name || p.term || p.payment_id || p.id}</td>
                      <td className="px-3 py-3">{inr(amount)}</td>
                      <td className="px-3 py-3">{p.payment_date || p.created_at || "—"}</td>
                      <td className="px-3 py-3">{p.payment_method || "—"}</td>
                      <td className="px-3 py-3">{p.transaction_reference || p.reference_no || "—"}</td>
                      <td className="px-3 py-3"><StatusBadge value={p.status === "Completed" ? "Paid" : p.status} /></td>
                      <td className="px-3 py-3"><div className="flex items-center gap-1">
                        <Button variant="ghost" size="sm" onClick={() => openPayment(p)}><Pencil className="h-3.5 w-3.5 mr-1" />Edit</Button>
                        <Button variant="ghost" size="sm" onClick={() => setDeletingPayment(p)}><Trash2 className="h-3.5 w-3.5 mr-1 text-red-500" />Delete</Button>
                      </div></td>
                    </tr>;
                  })}
                  {!payments.length && <tr><td colSpan="7" className="p-8 text-center text-zinc-500">No payments recorded for this customer.</td></tr>}
                </tbody>
              </table>
            </div>
          </SectionCard>
        </TabsContent>

        <TabsContent value="details" className="space-y-4 pt-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <SectionCard title="Basic Information">
              <Row label="Customer ID" value={customer.cust_id} /><Row label="Business Name" value={customer.business_name} />
              <Row label="Customer Type" value={customer.business_type} /><Row label="Registration Date" value={customer.registration_date} /><Row label="Account Status" value={<StatusBadge value={live.status} />} />
            </SectionCard>
            <SectionCard title="Tax Information">
              <Row label="GSTIN" value={customer.gst_number} /><Row label="PAN" value={customer.pan} /><Row label="TAN" value={customer.tan} /><Row label="CIN" value={customer.cin} />
              <Row label="GST Registration Type" value={customer.gst_registration_type} /><Row label="Taxpayer Type" value={customer.taxpayer_type} />
            </SectionCard>
            <SectionCard title="Address">
              <Row label="State" value={customer.state} /><Row label="City" value={customer.city} /><Row label="Address" value={customer.address} /><Row label="Pincode" value={customer.pincode} />
              <Row label="Registered Address" value={customer.registered_address} /><Row label="Communication Address" value={customer.communication_address} />
            </SectionCard>
            <SectionCard title="Contact">
              <Row label="Primary Contact" value={customer.owner} /><Row label="Designation" value={customer.designation} /><Row label="Mobile" value={customer.mobile} />
              <Row label="Alternate Mobile" value={customer.alternate_mobile} /><Row label="Email" value={customer.email} /><Row label="Alternate Email" value={customer.alternate_email} /><Row label="Website" value={customer.website} />
            </SectionCard>
          </div>
          <SectionCard title="Customer Projects & Activity" className="scroll-mt-6" id="customer-projects-section">
            <RelationshipPanel entity="customer" recordId={customer.id} />
          </SectionCard>
          <div className="flex justify-end gap-2"><Button variant="outline" onClick={onEdit}><Pencil className="h-4 w-4 mr-1.5" />Edit Customer</Button></div>
        </TabsContent>

        <TabsContent value="type" className="space-y-4 pt-4">
          <SectionCard title="Customer Classification">
            <div className="flex flex-wrap gap-2">
              {["GST Customer","TDS Customer","Income Tax Customer","ROC Customer","Accounting Customer","Other"].map((label) => {
                const active = String(customer.service_type || "").toLowerCase() === label.replace(" Customer","").toLowerCase();
                return <span key={label} className={`rounded-full border px-4 py-2 text-xs font-semibold ${active ? "border-blue-600 bg-blue-600 text-white" : "border-zinc-200 bg-white text-zinc-600"}`}>{label}</span>;
              })}
            </div>
            <p className="text-xs text-zinc-500 mt-3">Classification shown here reflects the existing customer service fields. The backend currently exposes a primary service category rather than a separate multi-classification field.</p>
          </SectionCard>
        </TabsContent>

        <TabsContent value="status" className="space-y-4 pt-4">
          <SectionCard title="Current Status">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div><div className="text-xs text-zinc-500 mb-1">Customer Status</div><StatusBadge value={live.filing_status || live.status} /></div>
              <div><div className="text-xs text-zinc-500 mb-1">Account Status</div><StatusBadge value={live.status} /></div>
              <div><div className="text-xs text-zinc-500 mb-1">Payment Frequency</div><span className="text-sm font-semibold">{customer.payment_frequency || "—"}</span></div>
            </div>
          </SectionCard>
          <SectionCard title="Status History">
            <div className="rounded-lg border border-dashed border-zinc-200 p-5 text-sm text-zinc-500">
              Current customer records expose the current status but do not expose a dedicated status-history collection in the existing customer response. No fabricated history is shown.
            </div>
          </SectionCard>
        </TabsContent>
      </Tabs>

      <SectionCard title="Customer Documents" className="scroll-mt-6" id="customer-documents-section">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-2 items-end">
          <div><Label className="text-xs text-zinc-500">Document Name</Label><Input value={docName} onChange={(e) => setDocName(e.target.value)} placeholder="PAN Card" className="mt-1" /></div>
          <div><Label className="text-xs text-zinc-500">Document Type</Label><Select value={docType} onValueChange={setDocType}><SelectTrigger className="mt-1 border-zinc-300"><SelectValue /></SelectTrigger><SelectContent className="bg-white">{["Identity","Tax","GST","Banking","Invoice","Payment Receipt","Agreement","Other"].map((d) => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent></Select></div>
          <div><Label className="text-xs text-zinc-500">Description</Label><Input value={docDescription} onChange={(e) => setDocDescription(e.target.value)} placeholder="Optional description" className="mt-1" /></div>
          <label className="inline-flex h-10 items-center justify-center gap-1.5 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm font-medium cursor-pointer hover:bg-zinc-50">
            <input type="file" multiple accept={[...ALLOWED_DOC_MIMES, "application/msword"].join(",")} className="hidden" onChange={onDocFile} disabled={uploading} />
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} Add Document
          </label>
        </div>
        {documents.length ? <div className="mt-4 overflow-x-auto"><table className="min-w-[760px] w-full text-xs"><thead className="bg-slate-50"><tr>{["Document","Type","File","Uploaded Date","Uploaded By","Actions"].map((h) => <th key={h} className="px-3 py-2 text-left text-zinc-500">{h}</th>)}</tr></thead><tbody>{documents.map((d) => <tr key={d.id} className="border-t border-zinc-100"><td className="px-3 py-2 font-medium"><div className="flex items-center gap-2"><DocThumb doc={d} /><span>{d.title || d.doc_type || d.file_name}</span></div></td><td className="px-3 py-2">{d.doc_type || "Other"}</td><td className="px-3 py-2"><div>{d.file_name || "—"}</div><div className="text-[10px] text-zinc-400">{(d.file_name || "").split(".").pop().toUpperCase()}{d.file_size ? ` · ${formatBytes(d.file_size)}` : ""}</div></td><td className="px-3 py-2">{d.uploaded_date || d.created_at || "—"}</td><td className="px-3 py-2">{d.uploaded_by || "—"}</td><td className="px-3 py-2"><div className="flex gap-1"><Button variant="ghost" size="sm" onClick={() => previewDocument(d)}><FileText className="h-4 w-4 mr-1" />View</Button><Button variant="ghost" size="sm" onClick={() => downloadDocument(d)}><Download className="h-4 w-4 mr-1" />Download</Button><Button variant="ghost" size="icon" aria-label={`Delete ${d.file_name || "document"}`} onClick={() => setDeletingDoc(d)}><Trash2 className="h-4 w-4 text-red-500" /></Button></div></td></tr>)}</tbody></table></div> : <p className="mt-4 text-sm text-zinc-500">No documents uploaded for this customer.</p>}
      </SectionCard>

      <AlertDialog open={!!deletingDoc} onOpenChange={(open) => { if (!open && !docBusy) setDeletingDoc(null); }}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete document?</AlertDialogTitle>
            <AlertDialogDescription>{deletingDoc?.file_name || "This document"} will be permanently removed from this customer. The customer and their other documents are not affected.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={docBusy}>Cancel</AlertDialogCancel>
            <AlertDialogAction className="bg-red-600 hover:bg-red-700 text-white" disabled={docBusy} onClick={(e) => { e.preventDefault(); confirmDeleteDocument(); }}>{docBusy ? "Deleting..." : "Delete"}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={paymentModal} onOpenChange={(open) => { if (!savingPayment) setPaymentModal(open); }}>
        <DialogContent className="bg-white max-w-lg">
          <DialogHeader><DialogTitle>{editingPayment ? "Edit Payment" : "Add Payment"}</DialogTitle></DialogHeader>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 py-2">
            <div><Label>Payment Term</Label><Input className="mt-1" value={paymentForm.term_name} onChange={(e) => setPaymentForm((f) => ({ ...f, term_name: e.target.value }))} placeholder="Term 1 / Custom Term" /></div>
            <div><Label>Amount</Label><Input className="mt-1" type="number" min="0.01" step="0.01" value={paymentForm.amount} onChange={(e) => setPaymentForm((f) => ({ ...f, amount: e.target.value }))} placeholder="10000" /></div>
            <div><Label>Payment Date</Label><Input className="mt-1" type="date" value={paymentForm.payment_date} onChange={(e) => setPaymentForm((f) => ({ ...f, payment_date: e.target.value }))} /></div>
            <div><Label>Payment Method</Label><Select value={paymentForm.payment_method} onValueChange={(v) => setPaymentForm((f) => ({ ...f, payment_method: v }))}><SelectTrigger className="mt-1"><SelectValue /></SelectTrigger><SelectContent className="bg-white">{["Cash","UPI","Bank Transfer","Card","Cheque","Online Payment","Other"].map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent></Select></div>
            <div><Label>Transaction / Reference ID</Label><Input className="mt-1" value={paymentForm.transaction_reference} onChange={(e) => setPaymentForm((f) => ({ ...f, transaction_reference: e.target.value }))} /></div>
            <div><Label>Status</Label><Select value={paymentForm.status} onValueChange={(v) => setPaymentForm((f) => ({ ...f, status: v }))}><SelectTrigger className="mt-1"><SelectValue /></SelectTrigger><SelectContent className="bg-white">{["Paid","Pending","Partial"].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent></Select></div>
            <div className="sm:col-span-2"><Label>Notes</Label><Textarea className="mt-1" value={paymentForm.notes} onChange={(e) => setPaymentForm((f) => ({ ...f, notes: e.target.value }))} placeholder="Optional notes" /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setPaymentModal(false)} disabled={savingPayment}>Cancel</Button><Button onClick={savePayment} disabled={savingPayment}>{savingPayment ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : null}{editingPayment ? "Update Payment" : "Save Payment"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deletingPayment} onOpenChange={(open) => !open && setDeletingPayment(null)}>
        <DialogContent className="bg-white max-w-md">
          <DialogHeader><DialogTitle>Delete payment?</DialogTitle></DialogHeader>
          <p className="text-sm text-zinc-600">Are you sure you want to delete this payment? This will update the customer's Total Paid and Outstanding balance.</p>
          <DialogFooter><Button variant="outline" onClick={() => setDeletingPayment(null)} disabled={savingPayment}>Cancel</Button><Button className="bg-red-600 hover:bg-red-700 text-white" onClick={confirmDeletePayment} disabled={savingPayment}>{savingPayment ? "Deleting..." : "Delete Payment"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
