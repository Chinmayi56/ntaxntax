import { useEffect, useState } from "react";
import api, { describeApiError } from "@/lib/api";
import { toast } from "sonner";
import { useCrud } from "@/hooks/useCrud";
import PageHeader from "@/components/shared/PageHeader";
import DataTable from "@/components/shared/DataTable";
import { KpiCard } from "@/components/shared/KpiCard";
import { StatusBadge, PriorityBadge } from "@/components/shared/StatusBadge";
import { Button } from "@/components/ui/button";
import BookServiceModal from "@/components/shared/BookServiceModal";
import ServiceTrackingModal from "@/components/shared/ServiceTrackingModal";
import { CalendarCheck, CheckCircle2, Clock, Loader2, Plus, ListChecks } from "lucide-react";

// "My Services": every service the logged-in customer has booked, with its
// live status/payment/consultant pulled straight from MongoDB via GET
// /api/bookings (server-side scoped to this customer's customer_id) — no
// MongoDB data, so it survives refresh and re-login.
export default function CustomerBookings() {
  const { rows, loading } = useCrud("bookings");
  const [open, setOpen] = useState(false);
  const [track, setTrack] = useState(null);
  const [primeBookings, setPrimeBookings] = useState([]);
  useEffect(() => { api.get("/prime-service-bookings/my").then(r => setPrimeBookings(r.data || [])).catch(e => toast.error(describeApiError(e, "Could not load Prime Service bookings."))); }, []);

  const sorted = [...rows].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));

  const columns = [
    { key: "booking_no", label: "Booking" },
    { key: "service", label: "Service", render: (r) => <div><div>{r.service || r.service_name || "Service"}</div>{(r.booking_type || r.service_tier) && <span className={`text-xs font-semibold ${String(r.booking_type || r.service_tier).toLowerCase().includes("prime") ? "text-amber-700" : "text-zinc-500"}`}>{r.booking_type || r.service_tier}</span>}</div> },
    { key: "assigned_agent", label: "Consultant" },
    { key: "booking_date", label: "Booked" },
    { key: "due_date", label: "Due" },
    { key: "priority", label: "Priority", render: (r) => <PriorityBadge value={r.priority} /> },
    { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
    { key: "payment_status", label: "Payment", render: (r) => <StatusBadge value={r.payment_status} /> },
    { key: "confirmed_amount", label: "Confirmed amount", render: (r) => r.price_snapshot != null ? `₹${Number(r.price_snapshot).toLocaleString("en-IN")}` : (r.estimated_fee != null ? `₹${Number(r.estimated_fee).toLocaleString("en-IN")}` : "—") },
    { key: "processing_snapshot", label: "Estimated processing", render: (r) => r.processing_time_snapshot || r.processing_time || "—" },
    {
      key: "actions", label: "Actions", render: (r) => (
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="outline" className="border-zinc-300 h-8" onClick={() => setTrack(r.id)} data-testid={`track-${r.id}`}><ListChecks className="h-3.5 w-3.5 mr-1" />Track</Button>
        </div>
      ),
    },
  ];

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <PageHeader
        title="My Services"
        breadcrumb={["Customer", "My Services"]}
        subtitle="The complete lifecycle and status of every service you've booked."
        actions={<Button className="bg-royal text-white hover:bg-royal-hover font-semibold" onClick={() => setOpen(true)} data-testid="new-booking-btn"><Plus className="h-4 w-4 mr-1.5" />Book a Service</Button>}
      />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <KpiCard title="Total Services" value={rows.length} icon={CalendarCheck} loading={loading} testId="bk-total" />
        <KpiCard title="Completed" value={rows.filter((r) => r.status === "Completed").length} icon={CheckCircle2} loading={loading} testId="bk-done" />
        <KpiCard title="In Progress" value={rows.filter((r) => ["Running", "Confirmed", "Processing"].includes(r.status)).length} icon={Loader2} loading={loading} testId="bk-running" />
        <KpiCard title="Pending" value={rows.filter((r) => r.status === "Pending").length} icon={Clock} loading={loading} testId="bk-pending" />
      </div>
      <DataTable title="My Services" columns={columns} rows={sorted} loading={loading} pageSize={8} testId="customer-bookings-table" />
      <section className="mt-8 rounded-xl border border-slate-200 bg-white overflow-hidden"><div className="p-5 border-b"><h2 className="font-bold text-lg">Prime Service Requests</h2><p className="text-sm text-slate-500 mt-1">Status updates for your GST, TDS, ROC, Income Tax and Payroll requests.</p></div><div className="overflow-x-auto"><table className="w-full text-sm"><thead className="bg-slate-50 text-left"><tr>{["Reference","Service","Booked on","Status","Pricing"].map(x=><th key={x} className="p-3 font-semibold">{x}</th>)}</tr></thead><tbody>{primeBookings.map(b=><tr key={b.id} className="border-t"><td className="p-3 font-semibold">{b.booking_reference}</td><td className="p-3">{b.service_type}</td><td className="p-3">{b.created_at ? new Date(b.created_at).toLocaleDateString() : "—"}</td><td className="p-3"><span className="rounded-full bg-amber-50 text-amber-800 px-2.5 py-1 text-xs font-semibold">{b.status}</span></td><td className="p-3">{b.quoted_price != null ? `₹${Number(b.quoted_price).toLocaleString("en-IN")}` : (b.pricing_label || "Quotation required")}</td></tr>)}</tbody></table>{!primeBookings.length&&<p className="p-6 text-sm text-slate-500">No Prime Service requests yet.</p>}</div></section>
      <BookServiceModal open={open} onOpenChange={setOpen} />
      <ServiceTrackingModal bookingId={track} open={!!track} onOpenChange={(o) => !o && setTrack(null)} />
    </div>
  );
}
