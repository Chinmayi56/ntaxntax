import { useEffect, useState } from "react";
import { Search, Eye, Loader2 } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import PageHeader from "@/components/shared/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { StatusBadge } from "@/components/shared/StatusBadge";
import api, { describeApiError } from "@/lib/api";
import { toast } from "sonner";

const TABS = ["Overview", "Services", "Tasks", "Documents", "Compliance", "Activity"];

export default function EmployeeCustomers() {
  const [rows, setRows] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [workspace, setWorkspace] = useState(null);
  const [tab, setTab] = useState("Overview");
  const [error, setError] = useState("");
  const [params, setParams] = useSearchParams();

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/customers", { params: { page: 1, page_size: 500 } });
      setRows(Array.isArray(data?.data) ? data.data : []);
      setError("");
    } catch (e) {
      setError(describeApiError(e, "Unable to load assigned customers."));
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);
  useEffect(() => {
    const id = params.get("customer");
    if (!id || !rows.length) return;
    const c = rows.find((x) => x.id === id);
    if (c) openCustomer(c);
  }, [params, rows]);

  const openCustomer = async (customer) => {
    setSelected(customer);
    setTab("Overview");
    try {
      const { data } = await api.get(`/employee/customers/${customer.id}/workspace`);
      setWorkspace(data?.data || null);
    } catch (e) {
      setWorkspace(null);
      toast.error(describeApiError(e, "Unable to load customer workspace."));
    }
  };

  const filtered = rows.filter((r) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return [r.business_name, r.name, r.owner, r.mobile, r.email, r.gst_number, r.pan]
      .some((v) => String(v || "").toLowerCase().includes(q));
  });

  return <div>
    <PageHeader title="Assigned Customers" subtitle="Customers authorized for your employee workload." breadcrumb={["Employee", "Customers"]} />
    <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
      <div className="p-4 border-b flex gap-3 items-center">
        <div className="relative max-w-md w-full"><Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-400" /><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search customer, business, GSTIN, PAN..." className="pl-9" /></div>
        <span className="text-sm text-zinc-500 ml-auto">{filtered.length} customer{filtered.length === 1 ? "" : "s"}</span>
      </div>
      {error ? <div className="p-8 text-sm text-red-600">{error}</div> : loading ? <div className="p-8 flex items-center gap-2 text-sm text-zinc-500"><Loader2 className="h-4 w-4 animate-spin" />Loading customers...</div> :
      filtered.length === 0 ? <div className="p-10 text-center text-sm text-zinc-500">No assigned customers found.</div> :
      <div className="overflow-x-auto"><table className="min-w-[1000px] w-full text-sm">
        <thead className="bg-slate-50 border-b"><tr>{["Business", "Contact", "Mobile", "GSTIN", "PAN", "State", "Status", "Action"].map(h => <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-zinc-600">{h}</th>)}</tr></thead>
        <tbody>{filtered.map(c => <tr key={c.id} className="border-b hover:bg-slate-50">
          <td className="px-4 py-3 font-medium">{c.business_name || c.name || c.owner || "—"}</td><td className="px-4 py-3">{c.name || c.owner || "—"}</td>
          <td className="px-4 py-3">{c.mobile || c.phone || "—"}</td><td className="px-4 py-3">{c.gst_number || c.gstin || "—"}</td>
          <td className="px-4 py-3">{c.pan || "—"}</td><td className="px-4 py-3">{c.state || "—"}</td>
          <td className="px-4 py-3"><StatusBadge value={c.status} /></td><td className="px-4 py-3"><Button size="sm" variant="outline" onClick={() => { params.set("customer", c.id); setParams(params); openCustomer(c); }}><Eye className="h-3.5 w-3.5 mr-1" />View</Button></td>
        </tr>)}</tbody>
      </table></div>}
    </div>

    <Dialog open={!!selected} onOpenChange={(open) => { if (!open) { setSelected(null); setWorkspace(null); params.delete("customer"); setParams(params); } }}>
      <DialogContent className="max-w-5xl max-h-[90vh] overflow-y-auto">
        <DialogHeader><DialogTitle>{selected?.business_name || selected?.name || "Customer"} — Workspace</DialogTitle></DialogHeader>
        {!workspace ? <div className="py-10 text-sm text-zinc-500">Loading workspace...</div> :
        <div className="space-y-5">
          <div className="flex flex-wrap gap-2 border-b pb-3">{TABS.map(t => <Button key={t} size="sm" variant={tab === t ? "default" : "outline"} onClick={() => setTab(t)}>{t}</Button>)}</div>
          {tab === "Overview" && <InfoGrid customer={workspace.customer} />}
          {tab === "Services" && <BookingList rows={workspace.bookings || []} />}
          {tab === "Tasks" && <TaskList rows={workspace.tasks || []} />}
          {tab === "Documents" && <DocumentList rows={workspace.documents || []} />}
          {tab === "Compliance" && <ComplianceList data={workspace.compliance || {}} />}
          {tab === "Activity" && <ActivityList rows={workspace.activity || []} />}
        </div>}
      </DialogContent>
    </Dialog>
  </div>;
}

function InfoGrid({ customer }) {
  const fields = [["Business", customer?.business_name], ["Contact", customer?.name || customer?.owner], ["Mobile", customer?.mobile || customer?.phone], ["Email", customer?.email], ["Address", customer?.address], ["City", customer?.city], ["State", customer?.state], ["Pincode", customer?.pincode], ["GSTIN", customer?.gst_number || customer?.gstin], ["PAN", customer?.pan], ["Business Type", customer?.business_type], ["Status", customer?.status]];
  return <div className="grid grid-cols-1 md:grid-cols-2 gap-4">{fields.map(([k,v]) => <div key={k} className="rounded-lg border p-3"><div className="text-xs text-zinc-500">{k}</div><div className="font-medium mt-1 break-words">{v || "—"}</div></div>)}</div>;
}
function BookingList({ rows }) { return rows.length ? <div className="space-y-2">{rows.map(b => <div key={b.id} className="border rounded-lg p-3 grid md:grid-cols-5 gap-2 text-sm"><span>{b.booking_no || b.id}</span><span>{b.service || b.service_name || "—"}</span><StatusBadge value={b.status} /><span>{b.booking_date || "—"}</span><span>{b.due_date || "—"}</span></div>)}</div> : <Empty />; }
function TaskList({ rows }) { return rows.length ? <div className="space-y-2">{rows.map(t => <div key={t.id} className="border rounded-lg p-3 grid md:grid-cols-5 gap-2 text-sm"><b>{t.title}</b><span>{t.service || "—"}</span><StatusBadge value={t.status} /><span>{t.priority || "—"}</span><span>{t.due_date || "—"}</span></div>)}</div> : <Empty />; }
function DocumentList({ rows }) { return rows.length ? <div className="space-y-2">{rows.map(d => <div key={d.id} className="border rounded-lg p-3 flex justify-between gap-3 text-sm"><span className="truncate">{d.file_name || d.name}</span><span>{d.doc_type || d.file_type || "Document"}</span><span>{d.uploaded_date || "—"}</span></div>)}</div> : <Empty />; }
function ComplianceList({ data }) { const groups = [["GST", data.gst], ["Income Tax", data.itr], ["TDS", data.tds], ["ROC", data.roc]]; return <div className="space-y-4">{groups.map(([name, rows]) => <div key={name}><h3 className="font-semibold mb-2">{name}</h3>{rows?.length ? rows.map(r => <div key={r.id} className="border rounded-lg p-3 grid md:grid-cols-4 gap-2 text-sm"><span>{r.filing_period || r.financial_year || r.assessment_year || r.year || "—"}</span><span>{r.return_type || r.compliance || r.type || name}</span><StatusBadge value={r.status} /><span>{r.due_date || "—"}</span></div>) : <div className="text-sm text-zinc-500 border rounded-lg p-3">No {name} records for this customer.</div>}</div>)}</div>; }
function ActivityList({ rows }) { return rows.length ? <div className="space-y-2">{rows.map(a => <div key={a.id} className="border rounded-lg p-3 text-sm"><div className="font-medium">{a.action || a.type || "Activity"}</div><div className="text-zinc-500">{a.text || a.details?.comment || ""} · {a.user_name || a.actor_name || ""} · {a.created_at || ""}</div></div>)}</div> : <Empty />; }
function Empty() { return <div className="p-8 text-center text-sm text-zinc-500 border rounded-lg">No records available.</div>; }
