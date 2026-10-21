import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { ClipboardList, CheckCircle2, Clock3, AlertCircle, MessageSquare, Eye, Download } from "lucide-react";
import PageHeader from "@/components/shared/PageHeader";
import { KpiCard } from "@/components/shared/KpiCard";
import { StatusBadge, PriorityBadge } from "@/components/shared/StatusBadge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import api, { describeApiError } from "@/lib/api";
import { toast } from "sonner";

const STATUSES = ["TO DO", "IN PROGRESS", "REVIEW", "COMPLETED"];

const STATUS_RANK = Object.fromEntries(STATUSES.map((status, index) => [status, index]));

export default function Tasks() {
  const location = useLocation();
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [priorityFilter, setPriorityFilter] = useState("all");

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/employee/tasks");
      setTasks(Array.isArray(data?.data) ? data.data : []);
    } catch (e) {
      toast.error(describeApiError(e, "Unable to load your tasks."));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const onChange = (e) => {
      if ((e?.detail?.resources || []).includes("tasks")) load();
    };
    window.addEventListener("ntaxco:data-changed", onChange);
    // Admin updates happen in a separate browser/session, so the Employee
    // portal also refreshes from MongoDB periodically without a page reload.
    const timer = window.setInterval(() => {
      if (!document.hidden) load();
    }, 10000);
    return () => {
      window.removeEventListener("ntaxco:data-changed", onChange);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const taskId = new URLSearchParams(location.search).get("task");
    if (!taskId || !tasks.length) return;
    const found = tasks.find((task) => task.id === taskId);
    if (found) setSelected(found);
  }, [location.search, tasks]);

  const filteredTasks = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tasks.filter((task) => {
      const text = [task.title, task.customer, task.service, task.booking_no, task.priority, task.status].join(" ").toLowerCase();
      return (!q || text.includes(q))
        && (statusFilter === "all" || String(task.status || "").toUpperCase() === statusFilter)
        && (priorityFilter === "all" || String(task.priority || "").toLowerCase() === priorityFilter.toLowerCase());
    });
  }, [tasks, search, statusFilter, priorityFilter]);

  const cards = [
    { title: "Total Tasks", value: tasks.length, icon: ClipboardList },
    { title: "To Do", value: tasks.filter((t) => t.status === "TO DO").length, icon: Clock3 },
    { title: "In Progress", value: tasks.filter((t) => t.status === "IN PROGRESS").length, icon: AlertCircle },
    { title: "Completed", value: tasks.filter((t) => t.status === "COMPLETED").length, icon: CheckCircle2 },
  ];

  const updateStatus = async (task, status) => {
    const currentRank = STATUS_RANK[task?.status] ?? -1;
    const nextRank = STATUS_RANK[status] ?? -1;
    if (nextRank < currentRank) {
      toast.error(`Task status can only move forward from ${task.status}.`);
      return;
    }
    setSaving(true);
    try {
      const { data } = await api.put(`/employee/tasks/${task.id}`, { status });
      setTasks((rows) => rows.map((r) => r.id === task.id ? (data?.data || { ...r, status }) : r));
      setSelected((current) => current?.id === task.id ? (data?.data || { ...current, status }) : current);
      toast.success("Task status updated");
      window.dispatchEvent(new CustomEvent("ntaxco:data-changed", { detail: { resources: ["tasks"] } }));
    } catch (e) {
      toast.error(describeApiError(e, "Unable to update task status."));
    } finally {
      setSaving(false);
    }
  };

  const openDocument = async (doc, download = false) => {
    try {
      const { data } = await api.get(`/documents/${doc.id}`);
      const file = data?.data;
      if (!file?.file_data) throw new Error("Document data is unavailable.");
      if (download) { const a = document.createElement("a"); a.href = file.file_data; a.download = file.file_name || "document"; a.click(); return; }
      const win = window.open("", "_blank");
      if (!win) throw new Error("Please allow pop-ups to preview the document.");
      if (String(file.mime_type || "").startsWith("image/") || file.mime_type === "application/pdf") win.document.write(`<title>${file.file_name || "Document"}</title><iframe src="${file.file_data}" style="width:100%;height:100vh;border:0"></iframe>`);
      else { win.close(); const a = document.createElement("a"); a.href = file.file_data; a.download = file.file_name || "document"; a.click(); }
    } catch (e) { toast.error(describeApiError(e, "Unable to open document.")); }
  };

  const addComment = async () => {
    if (!selected?.id || !comment.trim()) return;
    setSaving(true);
    try {
      await api.post(`/employee/tasks/${selected.id}/comment`, { comment: comment.trim() });
      const refreshed = await api.get(`/employee/tasks/${selected.id}`);
      const fresh = refreshed.data?.data;
      setTasks((rows) => rows.map((r) => r.id === selected.id ? fresh : r));
      setSelected(fresh);
      setComment("");
      toast.success("Comment added");
    } catch (e) {
      toast.error(describeApiError(e, "Unable to add comment."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <PageHeader title="My Tasks" subtitle="Tasks assigned to you from the Admin Task Board." breadcrumb={["Employee", "Tasks"]} />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {cards.map((card, i) => <KpiCard key={i} {...card} loading={loading} testId={`employee-task-kpi-${i}`} />)}
      </div>
      <div className="bg-white border border-zinc-200 rounded-xl shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-zinc-100 flex flex-wrap gap-2 items-center">
          <span className="text-sm font-semibold mr-auto">Assigned Tasks</span>
          <input className="h-9 w-56 rounded-md border px-3 text-sm" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search task/customer..." />
          <select className="h-9 rounded-md border px-3 text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="all">All statuses</option>{STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="h-9 rounded-md border px-3 text-sm" value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
            <option value="all">All priorities</option><option>High</option><option>Medium</option><option>Low</option>
          </select>
        </div>
        {loading ? (
          <div className="p-8 text-sm text-zinc-500">Loading tasks…</div>
        ) : filteredTasks.length === 0 ? (
          <div className="p-10 text-center text-sm text-zinc-500">No tasks match the current filters.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[1200px] w-full text-sm">
              <thead className="bg-slate-50 border-b border-zinc-200">
                <tr>{["Task", "Customer", "Business", "Service", "Priority", "Due Date", "Status", "Action"].map((h) => <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-zinc-600">{h}</th>)}</tr>
              </thead>
              <tbody>
                {filteredTasks.map((task) => (
                  <tr key={task.id} className="border-b border-zinc-100">
                    <td className="px-4 py-3 font-medium text-zinc-900">{task.title || "—"}</td>
                    <td className="px-4 py-3">{task.customer || "—"}</td>
                    <td className="px-4 py-3">{task.customer_details?.business_name || "—"}</td>
                    <td className="px-4 py-3">{task.service || "—"}</td>
                    <td className="px-4 py-3"><PriorityBadge value={task.priority} /></td>
                    <td className="px-4 py-3">{task.due_date || "—"}</td>
                    <td className="px-4 py-3"><StatusBadge value={task.status} /></td>
                    <td className="px-4 py-3"><Button variant="outline" size="sm" onClick={() => setSelected(task)}>View</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Dialog open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{selected?.title || "Task Details"}</DialogTitle></DialogHeader>
          {selected && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div><span className="text-zinc-500">Customer Name</span><div className="font-medium">{selected.customer || "—"}</div></div>
                <div><span className="text-zinc-500">Business</span><div className="font-medium">{selected.customer_details?.business_name || selected.customer_details?.name || "—"}</div></div>
                <div><span className="text-zinc-500">GSTIN</span><div className="font-medium">{selected.customer_details?.gst_number || selected.customer_details?.gstin || "—"}</div></div>
                <div><span className="text-zinc-500">PAN</span><div className="font-medium">{selected.customer_details?.pan || "—"}</div></div>
                <div><span className="text-zinc-500">Mobile</span><div className="font-medium">{selected.customer_details?.mobile || selected.customer_details?.phone || "—"}</div></div>
                <div><span className="text-zinc-500">Email</span><div className="font-medium">{selected.customer_details?.email || "—"}</div></div>
                <div><span className="text-zinc-500">Address</span><div className="font-medium">{selected.customer_details?.address || "—"}</div></div>
                <div><span className="text-zinc-500">State / City</span><div className="font-medium">{[selected.customer_details?.state, selected.customer_details?.city].filter(Boolean).join(" / ") || "—"}</div></div>
                <div><span className="text-zinc-500">Pincode</span><div className="font-medium">{selected.customer_details?.pincode || "—"}</div></div>
                <div><span className="text-zinc-500">Outstanding</span><div className="font-medium">{selected.customer_details?.outstanding ?? "—"}</div></div>
                <div><span className="text-zinc-500">Task ID</span><div className="font-medium">{selected.id || "—"}</div></div>
                <div><span className="text-zinc-500">Service</span><div className="font-medium">{selected.service || "—"}</div></div>
                <div><span className="text-zinc-500">Booking</span><div className="font-medium">{selected.booking_no || selected.booking_id || "—"}</div></div>
                <div><span className="text-zinc-500">Status</span><div className="font-medium">{selected.status || "—"}</div></div>
                <div><span className="text-zinc-500">Priority</span><div className="font-medium">{selected.priority || "—"}</div></div>
                <div><span className="text-zinc-500">Assigned Employee</span><div className="font-medium">{selected.assigned_employee || "—"}</div></div>
                <div><span className="text-zinc-500">Due date</span><div className="font-medium">{selected.due_date || "—"}</div></div>
              </div>
              <div className="border rounded-lg p-3">
                <div className="font-semibold text-sm mb-2">Customer Documents</div>
                {selected.documents?.length ? <div className="space-y-2">{selected.documents.map((doc) => <div key={doc.id} className="flex items-center justify-between gap-2 text-sm"><span className="truncate">{doc.file_name}</span><span className="flex gap-1"><Button size="sm" variant="outline" onClick={() => openDocument(doc)}><Eye className="h-3.5 w-3.5 mr-1"/>View</Button><Button size="sm" variant="outline" onClick={() => openDocument(doc, true)}><Download className="h-3.5 w-3.5 mr-1"/>Download</Button></span></div>)}</div> : <div className="text-sm text-zinc-500">No documents uploaded for this booking.</div>}
              </div>
              <div><div className="text-xs font-medium text-zinc-500 mb-1">Description</div><div className="text-sm whitespace-pre-wrap">{selected.description || "No description provided."}</div></div>
              <div className="border rounded-lg p-3">
                <div className="font-semibold text-sm mb-2">Activity / History</div>
                {selected.activity?.length ? <div className="space-y-2 max-h-40 overflow-y-auto">{selected.activity.map((a) => <div key={a.id} className="text-sm border-b last:border-0 pb-2"><div className="font-medium">{a.action || a.type || "Update"}</div><div className="text-zinc-500">{a.text || ""} · {a.actor_name || a.user_name || ""} · {a.created_at || ""}</div></div>)}</div> : <div className="text-sm text-zinc-500">No activity recorded yet.</div>}
              </div>
              <div>
                <div className="text-xs font-medium text-zinc-500 mb-1">Update status</div>
                <div className="flex flex-wrap gap-2">
                  {STATUSES.map((status) => {
                    const disabledBackward = (STATUS_RANK[status] ?? -1) < (STATUS_RANK[selected.status] ?? -1);
                    return <Button key={status} variant={selected.status === status ? "default" : "outline"} size="sm" disabled={saving || disabledBackward} onClick={() => updateStatus(selected, status)}>{status}</Button>;
                  })}
                </div>
              </div>
              <div>
                <div className="flex items-center gap-2 text-xs font-medium text-zinc-500 mb-1"><MessageSquare className="h-3.5 w-3.5" /> Add activity/comment</div>
                <Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a work update..." />
              </div>
            </div>
          )}
          <DialogFooter><Button variant="outline" onClick={() => setSelected(null)}>Close</Button><Button onClick={addComment} disabled={saving || !comment.trim()}>Add Comment</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
