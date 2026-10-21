import { useCallback, useEffect, useMemo, useState } from "react";
import { Search, Save, Loader2 } from "lucide-react";
import PageHeader from "@/components/shared/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { StatusBadge } from "@/components/shared/StatusBadge";
import api, { describeApiError } from "@/lib/api";
import { toast } from "sonner";

const CONFIG = {
  gst: { title: "GST Workspace", collection: "gst", label: "GST" },
  "income-tax": { title: "Income Tax Workspace", collection: "itr", label: "Income Tax" },
  tds: { title: "TDS Workspace", collection: "tds", label: "TDS" },
  roc: { title: "ROC Workspace", collection: "roc", label: "ROC" },
};

const STATUSES = ["Pending", "Running", "Completed"];

export default function EmployeeCompliance({ kind }) {
  const cfg = CONFIG[kind] || CONFIG.gst;
  const [rows, setRows] = useState([]);
  const [customers, setCustomers] = useState({});
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, c] = await Promise.all([
        api.get(`/${cfg.collection}`, { params: { page: 1, page_size: 500 } }),
        api.get("/customers", { params: { page: 1, page_size: 500 } }),
      ]);
      const list = Array.isArray(r.data?.data) ? r.data.data : [];
      const map = Object.fromEntries((Array.isArray(c.data?.data) ? c.data.data : []).map(x => [x.id, x]));
      setRows(list); setCustomers(map);
    } catch (e) { toast.error(describeApiError(e, `Unable to load ${cfg.label} records.`)); }
    finally { setLoading(false); }
  }, [cfg]);
  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(r => {
      const c = customers[r.customer_id] || {};
      return [c.business_name, c.name, c.owner, r.filing_period, r.financial_year, r.assessment_year, r.return_type, r.status, r.due_date, r.compliance]
        .some(v => String(v || "").toLowerCase().includes(q));
    });
  }, [rows, customers, search]);

  const save = async () => {
    if (!selected?.id) return;
    setSaving(true);
    try {
      const { data } = await api.put(`/${cfg.collection}/${selected.id}`, { status: selected.status, notes: selected.notes || "" });
      setRows(rs => rs.map(r => r.id === selected.id ? (data?.data || selected) : r));
      setSelected(data?.data || selected);
      toast.success(`${cfg.label} record updated`);
      window.dispatchEvent(new CustomEvent("ntaxco:data-changed", { detail: { resources: [cfg.collection] } }));
    } catch (e) { toast.error(describeApiError(e, `Unable to update ${cfg.label} record.`)); }
    finally { setSaving(false); }
  };

  return <div>
    <PageHeader title={cfg.title} subtitle={`Authorized ${cfg.label} compliance work from the existing ERP.`} breadcrumb={["Employee", cfg.label]} />
    <div className="bg-white border rounded-xl shadow-sm overflow-hidden">
      <div className="p-4 border-b flex gap-3"><div className="relative max-w-md w-full"><Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-400"/><Input className="pl-9" value={search} onChange={e=>setSearch(e.target.value)} placeholder={`Search ${cfg.label}, customer, period, status...`} /></div><span className="ml-auto text-sm text-zinc-500">{filtered.length} records</span></div>
      {loading ? <div className="p-8 flex gap-2 text-sm text-zinc-500"><Loader2 className="h-4 w-4 animate-spin"/>Loading...</div> : !filtered.length ? <div className="p-10 text-center text-sm text-zinc-500">No authorized {cfg.label} records found.</div> :
      <div className="overflow-x-auto"><table className="min-w-[1000px] w-full text-sm"><thead className="bg-slate-50 border-b"><tr>{["Customer","Period / FY","Type","Due Date","Assigned","Status","Action"].map(h=><th key={h} className="px-4 py-3 text-left text-xs font-semibold text-zinc-600">{h}</th>)}</tr></thead>
      <tbody>{filtered.map(r=>{const c=customers[r.customer_id]||{}; return <tr key={r.id} className="border-b hover:bg-slate-50"><td className="px-4 py-3 font-medium">{c.business_name||c.name||c.owner||r.customer_id||"—"}</td><td className="px-4 py-3">{r.filing_period||r.assessment_year||r.financial_year||r.year||"—"}</td><td className="px-4 py-3">{r.return_type||r.compliance||r.type||cfg.label}</td><td className="px-4 py-3">{r.due_date||"—"}</td><td className="px-4 py-3">{r.assigned_employee||"—"}</td><td className="px-4 py-3"><StatusBadge value={r.status}/></td><td className="px-4 py-3"><Button size="sm" variant="outline" onClick={()=>setSelected({...r})}>View / Edit</Button></td></tr>})}</tbody></table></div>}
    </div>
    <Dialog open={!!selected} onOpenChange={open=>!open&&setSelected(null)}><DialogContent className="max-w-2xl"><DialogHeader><DialogTitle>{cfg.label} Record</DialogTitle></DialogHeader>{selected&&<div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 text-sm">{Object.entries(selected).filter(([k,v])=>!["_id","status","notes"].includes(k)&&typeof v!=="object").slice(0,18).map(([k,v])=><div key={k} className="border rounded-lg p-3"><div className="text-xs text-zinc-500">{k.replaceAll("_"," ")}</div><div className="font-medium mt-1 break-words">{String(v||"—")}</div></div>)}</div>
      <div><label className="text-sm font-medium">Status</label><select className="mt-1 w-full h-10 rounded-md border px-3" value={selected.status||"Pending"} onChange={e=>setSelected(s=>({...s,status:e.target.value}))}>{STATUSES.map(x=><option key={x}>{x}</option>)}</select></div>
      <div><label className="text-sm font-medium">Notes</label><textarea className="mt-1 w-full min-h-24 rounded-md border p-3 text-sm" value={selected.notes||""} onChange={e=>setSelected(s=>({...s,notes:e.target.value}))}/></div>
      <Button onClick={save} disabled={saving}>{saving?<Loader2 className="h-4 w-4 mr-2 animate-spin"/>:<Save className="h-4 w-4 mr-2"/>}Save changes</Button>
    </div>}</DialogContent></Dialog>
  </div>;
}
