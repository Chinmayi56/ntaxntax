import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Save, Settings2, CalendarCheck, Search } from "lucide-react";
import api, { describeApiError } from "@/lib/api";

const STATUSES = ["Pending", "Under Review", "Contacted", "In Progress", "Completed", "Rejected", "Cancelled"];
const money = (s) => s.price_mode === "fixed" && s.base_price != null ? `₹${Number(s.base_price).toLocaleString("en-IN")}` : (s.pricing_label || "Get a Quote");

export default function PrimeServicesManagement() {
  const [tab, setTab] = useState("services");
  const [services, setServices] = useState([]);
  const [bookings, setBookings] = useState([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [serviceFilter, setServiceFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState("");
  const [editing, setEditing] = useState(null);
  const [notes, setNotes] = useState({});
  const knownGuestBookings = useRef(null);

  const loadServices = useCallback(async () => {
    const r = await api.get("/prime-services"); setServices(r.data || []);
  }, []);
  const loadBookings = useCallback(async () => {
    const r = await api.get("/admin/prime-service-bookings", { params: { page: 1, page_size: 100, q: query || undefined, status: statusFilter || undefined, service_type: serviceFilter || undefined } });
    setBookings(r.data.items || []); setTotal(r.data.total || 0);
  }, [query, statusFilter, serviceFilter]);
  const refresh = useCallback(async () => {
    setLoading(true);
    try { await Promise.all([loadServices(), loadBookings()]); }
    catch (e) { toast.error(describeApiError(e, "Could not load Prime Services data.")); }
    finally { setLoading(false); }
  }, [loadServices, loadBookings]);
  useEffect(() => { refresh(); }, [refresh]);

  // Notify administrators in this page when new guest bookings arrive.
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const r = await api.get("/admin/prime-service-bookings", { params: { page: 1, page_size: 100, q: query || undefined, status: statusFilter || undefined, service_type: serviceFilter || undefined } });
        if (!active) return;
        const guestBookings = (r.data.items || []).filter(b => b.booking_source === "guest");
        const current = new Set(guestBookings.map(b => b.id));
        if (knownGuestBookings.current !== null) {
          const added = guestBookings.filter(b => !knownGuestBookings.current.has(b.id));
          added.forEach(b => toast.info(`New Prime booking: ${b.booking_reference} · ${b.service_type}`, { description: `${b.customer_name} · ${b.mobile_number}` }));
        }
        knownGuestBookings.current = current;
        setTotal(r.data.total || 0);
        setBookings(old => {
          // Preserve the active search/filter results, but refresh the list when no filters are set.
          if (query || statusFilter || serviceFilter) return old;
          return r.data.items || [];
        });
      } catch (_) { /* normal refresh action surfaces errors */ }
    };
    const timer = setInterval(poll, 30000);
    return () => { active = false; clearInterval(timer); };
  }, [query, statusFilter, serviceFilter]);

  const saveService = async (service) => {
    setSaving(service.id);
    try {
      const payload = { description: service.description, base_price: service.price_mode === "fixed" ? (service.base_price === "" ? null : Number(service.base_price)) : null,
        pricing_label: service.pricing_label || (service.price_mode === "quote" ? "Get a Quote" : ""), price_mode: service.price_mode,
        active: !!service.active, available: !!service.available,
        estimated_duration_value: service.estimated_duration_value === "" ? null : (service.estimated_duration_value == null ? null : Number(service.estimated_duration_value)),
        estimated_duration_unit: service.estimated_duration_unit || "", customer_completion_label: (service.customer_completion_label || "").trim(),
        completion_estimate_notes: (service.completion_estimate_notes || "").trim(), booking_instructions: service.booking_instructions || "" };
      const r = await api.patch(`/admin/prime-services/${service.id}`, payload);
      setServices(old => old.map(x => x.id === service.id ? r.data : x)); setEditing(null); toast.success(`${service.service_type} settings saved.`);
    } catch (e) { toast.error(describeApiError(e, "Unable to save service settings.")); }
    finally { setSaving(""); }
  };
  const updateBooking = async (booking, payload) => {
    setSaving(booking.id);
    try { await api.patch(`/admin/prime-service-bookings/${booking.id}`, payload); await loadBookings(); toast.success("Booking updated."); }
    catch (e) { toast.error(describeApiError(e, "Unable to update booking.")); }
    finally { setSaving(""); }
  };
  const setServiceField = (id, key, value) => setServices(old => old.map(s => s.id === id ? { ...s, [key]: value } : s));

  return <div className="p-4 md:p-8 space-y-6">
    <div className="flex flex-wrap justify-between items-center gap-3"><div><p className="text-xs font-bold uppercase tracking-widest text-amber-700">NTAXCO Admin</p><h1 className="text-2xl md:text-3xl font-black text-slate-900 mt-1">Prime Services Management</h1><p className="text-sm text-slate-500 mt-1">Manage customer-visible pricing, estimated completion times, availability and submitted requests.</p></div><button onClick={refresh} className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold"><RefreshCw className="h-4 w-4"/>Refresh</button></div>
    <div className="flex gap-2 border-b"><button onClick={()=>setTab("services")} className={`px-4 py-3 font-semibold text-sm ${tab === "services" ? "border-b-2 border-amber-500 text-slate-900" : "text-slate-500"}`}><Settings2 className="inline h-4 w-4 mr-2"/>Services & Pricing</button><button onClick={()=>setTab("bookings")} className={`px-4 py-3 font-semibold text-sm ${tab === "bookings" ? "border-b-2 border-amber-500 text-slate-900" : "text-slate-500"}`}><CalendarCheck className="inline h-4 w-4 mr-2"/>Bookings ({total})</button></div>
    {loading ? <div className="rounded-xl border p-10 text-center text-slate-500">Loading Prime Services…</div> : tab === "services" ? <div className="grid xl:grid-cols-2 gap-5">{services.map(s => <section key={s.id} className="rounded-xl border bg-white p-5 shadow-sm space-y-4"><div className="flex justify-between items-start gap-3"><div><h2 className="font-bold text-lg">{s.service_type}</h2><p className="text-sm text-slate-500">Current customer display: <strong className="text-slate-900">{money(s)}</strong></p></div><span className={`text-xs rounded-full px-2.5 py-1 ${s.active && s.available ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>{s.active && s.available ? "Available" : "Unavailable"}</span></div>
      {editing === s.id ? <div className="space-y-3"><label className="block text-sm font-medium">Description<textarea value={s.description || ""} onChange={e=>setServiceField(s.id,"description",e.target.value)} className="mt-1 w-full border rounded-lg p-2" rows={2}/></label><div className="grid sm:grid-cols-2 gap-3"><label className="text-sm font-medium">Pricing mode<select value={s.price_mode || "quote"} onChange={e=>setServiceField(s.id,"price_mode",e.target.value)} className="mt-1 w-full border rounded-lg p-2"><option value="quote">Get a Quote</option><option value="fixed">Fixed price</option></select></label><label className="text-sm font-medium">Base price (INR)<input type="number" min="0" step="0.01" disabled={s.price_mode !== "fixed"} value={s.base_price ?? ""} onChange={e=>setServiceField(s.id,"base_price",e.target.value)} className="mt-1 w-full border rounded-lg p-2 disabled:bg-slate-100" placeholder="e.g. 999"/></label></div><label className="block text-sm font-medium">Customer price label<input value={s.pricing_label || ""} onChange={e=>setServiceField(s.id,"pricing_label",e.target.value)} className="mt-1 w-full border rounded-lg p-2" placeholder="Get a Quote / Starting from ₹999"/></label><div className="rounded-lg border border-blue-100 bg-blue-50/50 p-3 space-y-3"><h3 className="font-semibold text-sm">Estimated Service Completion Time</h3><div className="grid sm:grid-cols-2 gap-3"><label className="text-sm font-medium">Estimated duration<input type="number" min="0.01" max="3650" step="0.01" value={s.estimated_duration_value ?? ""} onChange={e=>setServiceField(s.id,"estimated_duration_value",e.target.value)} className="mt-1 w-full border rounded-lg p-2" placeholder="Leave blank until configured"/></label><label className="text-sm font-medium">Duration unit<select value={s.estimated_duration_unit || ""} onChange={e=>setServiceField(s.id,"estimated_duration_unit",e.target.value)} className="mt-1 w-full border rounded-lg p-2"><option value="">Not configured</option><option value="hours">Hours</option><option value="days">Days</option><option value="working_days">Working Days</option></select></label></div><label className="block text-sm font-medium">Customer-facing completion estimate (optional)<input maxLength={180} value={s.customer_completion_label || ""} onChange={e=>setServiceField(s.id,"customer_completion_label",e.target.value)} className="mt-1 w-full border rounded-lg p-2" placeholder="Automatically generated when blank"/></label><label className="block text-sm font-medium">Completion estimate notes (optional)<textarea maxLength={600} value={s.completion_estimate_notes || ""} onChange={e=>setServiceField(s.id,"completion_estimate_notes",e.target.value)} className="mt-1 w-full border rounded-lg p-2" rows={2} placeholder="Customer-visible notes about this estimate"/></label></div><label className="flex gap-2 items-center text-sm"><input type="checkbox" checked={!!s.active} onChange={e=>setServiceField(s.id,"active",e.target.checked)}/>Service active</label><label className="flex gap-2 items-center text-sm"><input type="checkbox" checked={!!s.available} onChange={e=>setServiceField(s.id,"available",e.target.checked)}/>Accept new booking requests</label><label className="block text-sm font-medium">Booking instructions<textarea value={s.booking_instructions || ""} onChange={e=>setServiceField(s.id,"booking_instructions",e.target.value)} className="mt-1 w-full border rounded-lg p-2" rows={2}/></label><div className="flex justify-end gap-2"><button onClick={()=>{setEditing(null);loadServices()}} className="border rounded-lg px-3 py-2 text-sm">Cancel</button><button disabled={saving===s.id} onClick={()=>saveService(s)} className="bg-slate-900 text-white rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"><Save className="inline h-4 w-4 mr-1"/>Save changes</button></div></div> : <><p className="text-sm text-slate-600">{s.description}</p><p className="text-sm text-slate-500">{s.customer_completion_label || (s.estimated_duration_value && s.estimated_duration_unit ? `Estimated completion: ${s.estimated_duration_value} ${{hours:"hours",days:"days",working_days:"working days"}[s.estimated_duration_unit]}` : "Completion time not configured")}</p><button onClick={()=>setEditing(s.id)} className="rounded-lg border px-4 py-2 text-sm font-semibold hover:bg-slate-50">Edit service configuration</button></>}</section>)}</div> : <div className="rounded-xl border bg-white overflow-hidden"><div className="p-4 flex flex-col sm:flex-row gap-3"><div className="relative flex-1"><Search className="absolute left-3 top-3 h-4 w-4 text-slate-400"/><input value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>e.key==="Enter"&&loadBookings()} placeholder="Search reference, customer or mobile" className="w-full border rounded-lg pl-9 pr-3 py-2"/></div><select value={statusFilter} onChange={e=>setStatusFilter(e.target.value)} className="border rounded-lg px-3 py-2"><option value="">All statuses</option>{STATUSES.map(x=><option key={x}>{x}</option>)}</select><select value={serviceFilter} onChange={e=>setServiceFilter(e.target.value)} className="border rounded-lg px-3 py-2"><option value="">All services</option>{services.map(s=><option key={s.id} value={s.service_type}>{s.service_type}</option>)}</select><button onClick={loadBookings} className="border rounded-lg px-4 py-2 font-semibold">Search</button></div><div className="overflow-x-auto"><table className="w-full text-sm"><thead className="bg-slate-50 text-left"><tr>{["Reference / Date","Customer","Service","Price","Completion estimate","Status","Admin notes","Save"].map(h=><th key={h} className="p-3 font-semibold whitespace-nowrap">{h}</th>)}</tr></thead><tbody>{bookings.map(b=><tr key={b.id} className="border-t align-top"><td className="p-3 whitespace-nowrap"><strong>{b.booking_reference}</strong><div className="text-xs text-slate-500 mt-1">{b.created_at ? new Date(b.created_at).toLocaleString() : "—"}</div></td><td className="p-3 min-w-40">{b.customer_name} {b.booking_source === "guest" && <span className="ml-1 rounded-full bg-amber-100 text-amber-800 px-2 py-0.5 text-[10px] font-bold">GUEST</span>}<div className="text-xs text-slate-500">{b.mobile_number}</div>{b.gst_number&&<div className="text-xs text-slate-500">{b.gst_number}</div>}</td><td className="p-3 whitespace-nowrap">{b.service_type}</td><td className="p-3 whitespace-nowrap">{b.price_mode === "fixed" && b.quoted_price != null ? `₹${Number(b.quoted_price).toLocaleString("en-IN")}` : (b.pricing_label || "Quote required")}</td><td className="p-3 min-w-40">{b.customer_completion_label || "Completion time will be confirmed by the NTAXCO team."}{b.completion_estimate_notes && <div className="text-xs text-slate-500 mt-1">{b.completion_estimate_notes}</div>}</td><td className="p-3"><select value={b.status} onChange={e=>updateBooking(b,{status:e.target.value})} disabled={saving===b.id} className="border rounded-md p-2 min-w-32">{STATUSES.map(x=><option key={x}>{x}</option>)}</select></td><td className="p-3 min-w-48"><textarea rows={2} value={notes[b.id] ?? b.admin_notes ?? ""} onChange={e=>setNotes(old=>({...old,[b.id]:e.target.value}))} className="border rounded-md p-2 w-full" placeholder="Internal notes (admin only)"/><button onClick={()=>updateBooking(b,{admin_notes:notes[b.id] ?? b.admin_notes ?? ""})} className="mt-1 text-xs font-semibold underline">Save notes</button><div className="text-xs text-slate-500 mt-1">Requirements: {b.additional_requirements || "—"}</div></td><td className="p-3"><button disabled={saving===b.id} onClick={()=>updateBooking(b,{status:b.status,admin_notes:notes[b.id] ?? b.admin_notes ?? ""})} className="border rounded-md px-3 py-2 disabled:opacity-50">Save</button></td></tr>)}</tbody></table>{!bookings.length&&<div className="p-10 text-center text-slate-500">No Prime Service bookings found.</div>}</div></div>}
  </div>;
}
