import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  AlertCircle, CalendarDays, CheckCircle2, ChevronDown, Clock3, Filter,
  GripVertical, MessageSquare, Plus, RefreshCw, Search, Trash2, UserRound,
  X, ArrowRight, CircleDot, Pencil, Loader2, List, CalendarDays as CalendarIcon,
  BarChart3, BriefcaseBusiness, CheckSquare, Paperclip, Download, Eye,
  Copy, MoreHorizontal, ChevronLeft, ChevronRight, UploadCloud, Building2,
  Briefcase, CalendarClock, Flag, CircleCheck
} from "lucide-react";
import api, { describeApiError } from "@/lib/api";
import PageHeader from "@/components/shared/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { KpiCard } from "@/components/shared/KpiCard";
import { KanbanListDialog } from "@/components/shared/ModuleKanban";
import KanbanEmptyState from "@/components/shared/KanbanEmptyState";
import { useKanbanFlash, flashClass } from "@/hooks/useKanbanFlash";

export const TASK_STATUSES = ["TO DO", "IN PROGRESS", "REVIEW", "COMPLETED"];
export const TASK_PRIORITIES = ["Low", "Medium", "High", "Urgent"];
const TASK_STATUS_RANK = Object.fromEntries(TASK_STATUSES.map((status, index) => [status, index]));

const EMPTY_OPTIONS = {
  customers: [],
  services: [],
  employees: [],
  bookings: [],
};

const statusMeta = {
  "TO DO": {
    icon: CircleDot,
    tone: "text-zinc-600",
    header: "bg-zinc-100",
  },
  "IN PROGRESS": {
    icon: Clock3,
    tone: "text-blue-700",
    header: "bg-blue-50",
  },
  REVIEW: {
    icon: AlertCircle,
    tone: "text-amber-700",
    header: "bg-amber-50",
  },
  COMPLETED: {
    icon: CheckCircle2,
    tone: "text-emerald-700",
    header: "bg-emerald-50",
  },
};

function canonicalTaskId(task) {
  const id = task?.id ?? task?.task_id ?? task?._id;
  return id == null ? "" : String(id).trim();
}

function taskDue(task) {
  if (!task?.due_date) return null;

  const raw = String(task.due_date).slice(0, 10);
  const due = new Date(`${raw}T00:00:00`);

  if (Number.isNaN(due.getTime())) return null;

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const diff = Math.round((due - today) / 86400000);

  if (task.status !== "COMPLETED" && diff < 0) {
    return {
      label: "Overdue",
      cls: "text-red-600 bg-red-50",
    };
  }

  if (diff === 0) {
    return {
      label: "Due today",
      cls: "text-amber-700 bg-amber-50",
    };
  }

  if (diff === 1) {
    return {
      label: "Due tomorrow",
      cls: "text-amber-700 bg-amber-50",
    };
  }

  return {
    label: raw,
    cls: "text-zinc-600 bg-zinc-50",
  };
}

function priorityClass(priority) {
  return (
    {
      Urgent: "border-red-200 bg-red-50 text-red-700",
      High: "border-orange-200 bg-orange-50 text-orange-700",
      Medium: "border-amber-200 bg-amber-50 text-amber-700",
      Low: "border-zinc-200 bg-zinc-50 text-zinc-600",
    }[priority] || "border-zinc-200 bg-zinc-50 text-zinc-600"
  );
}


function formatBytes(value) {
  const n = Number(value || 0);
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function openDataUrl(dataUrl, fileName, download = false) {
  if (!dataUrl) return;
  const a = document.createElement("a");
  a.href = dataUrl;
  a.target = download ? "_self" : "_blank";
  if (download) a.download = fileName || "document";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function Field({ label, children, className = "" }) {
  return (
    <label className={`block space-y-1.5 ${className}`}>
      <span className="text-xs font-medium text-zinc-600">{label}</span>
      {children}
    </label>
  );
}

function NativeSelect({ value, onChange, children, disabled = false }) {
  return (
    <select
      value={value ?? ""}
      onChange={onChange}
      disabled={disabled}
      className="h-9 w-full rounded-md border border-zinc-200 bg-white px-3 text-sm outline-none focus:border-amber-400 disabled:bg-zinc-50"
    >
      {children}
    </select>
  );
}

function TaskCreateDialog({
  open,
  onOpenChange,
  options,
  onCreated,
  initialBooking = null,
}) {
  const [saving, setSaving] = useState(false);

  const [form, setForm] = useState({
    title: "",
    description: "",
    priority: "Medium",
    status: "TO DO",
    employee_id: "",
    customer_id: "",
    booking_id: "",
    service_id: "",
    due_date: "",
  });

  const [localOptions, setLocalOptions] = useState(
    options || EMPTY_OPTIONS
  );

  useEffect(() => {
    setLocalOptions(options || EMPTY_OPTIONS);
  }, [options]);

  useEffect(() => {
    if (!open || (options?.employees || []).length) return;

    api
      .get("/admin/tasks/options")
      .then(({ data }) => {
        if (data?.data) setLocalOptions(data.data);
      })
      .catch(() => {});
  }, [open, options]);

  useEffect(() => {
    if (!open) return;

    const b = initialBooking;

    setForm({
      title: b ? `Follow up: ${b.booking_no || b.id}` : "",
      description: "",
      priority:
        b?.priority && TASK_PRIORITIES.includes(b.priority)
          ? b.priority
          : "Medium",
      status: "TO DO",
      employee_id: "",
      customer_id: b?.customer_id || "",
      booking_id: b?.id || "",
      service_id: b?.service_id || "",
      due_date: b?.due_date || "",
    });
  }, [open, initialBooking]);

  const set = (key, value) =>
    setForm((f) => ({
      ...f,
      [key]: value,
    }));

  const bookings = localOptions.bookings || [];
  const customers = localOptions.customers || [];
  const services = localOptions.services || [];
  const employees = localOptions.employees || [];

  const submit = async (e) => {
    e.preventDefault();

    if (!form.title.trim()) {
      return toast.error("Task title is required");
    }

    setSaving(true);

    try {
      const { data } = await api.post("/admin/tasks", form);

      toast.success("Task created");

      onCreated?.(data?.data);
      onOpenChange(false);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to create task"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Create Task</DialogTitle>
          <DialogDescription>
            Create a real NTAXCO task linked to existing ERP records.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Task title *" className="md:col-span-2">
              <Input
                value={form.title}
                onChange={(e) => set("title", e.target.value)}
                placeholder="Prepare GST registration documents"
              />
            </Field>

            <Field label="Description" className="md:col-span-2">
              <Textarea
                value={form.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder="Add task details..."
                rows={4}
              />
            </Field>

            <Field label="Priority">
              <NativeSelect
                value={form.priority}
                onChange={(e) => set("priority", e.target.value)}
              >
                {TASK_PRIORITIES.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </NativeSelect>
            </Field>

            <Field label="Status">
              <NativeSelect
                value={form.status}
                onChange={(e) => set("status", e.target.value)}
              >
                {TASK_STATUSES.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </NativeSelect>
            </Field>

            <Field label="Assigned employee">
              <NativeSelect
                value={form.employee_id}
                onChange={(e) => set("employee_id", e.target.value)}
              >
                <option value="">Unassigned</option>
                {employees.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name || e.emp_id || e.id}
                  </option>
                ))}
              </NativeSelect>
            </Field>

            <Field label="Customer">
              <NativeSelect
                value={form.customer_id}
                onChange={(e) => set("customer_id", e.target.value)}
              >
                <option value="">No customer</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.business_name || c.owner || c.name || c.id}
                  </option>
                ))}
              </NativeSelect>
            </Field>

            <Field label="Related booking">
              <NativeSelect
                value={form.booking_id}
                onChange={(e) => {
                  const b = bookings.find(
                    (x) => x.id === e.target.value
                  );

                  setForm((f) => ({
                    ...f,
                    booking_id: e.target.value,
                    customer_id: b?.customer_id || f.customer_id,
                    service_id: b?.service_id || f.service_id,
                  }));
                }}
              >
                <option value="">No booking</option>

                {bookings.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.booking_no || b.id} · {b.customer || ""}
                  </option>
                ))}
              </NativeSelect>
            </Field>

            <Field label="Related service">
              <NativeSelect
                value={form.service_id}
                onChange={(e) => set("service_id", e.target.value)}
              >
                <option value="">No service</option>

                {services.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name || s.title || s.category || s.id}
                  </option>
                ))}
              </NativeSelect>
            </Field>

            <Field label="Due date">
              <Input
                type="date"
                value={form.due_date || ""}
                onChange={(e) => set("due_date", e.target.value)}
              />
            </Field>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>

            <Button
              type="submit"
              disabled={saving}
              className="bg-amber-500 hover:bg-amber-600 text-zinc-950"
            >
              {saving ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Plus className="h-4 w-4 mr-2" />
              )}
              Create Task
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function TaskDetailsDialog({
  task,
  open,
  onOpenChange,
  options,
  onChanged,
}) {
  const [detail, setDetail] = useState(task);
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [commenting, setCommenting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({});
  const [newChecklist, setNewChecklist] = useState("");
  const [uploading, setUploading] = useState(false);
  const [duplicating, setDuplicating] = useState(false);

  useEffect(() => {
    setDetail(task);
    setComment("");
    setEditing(false);
  }, [task]);

  useEffect(() => {
    const taskId = canonicalTaskId(task);
    if (!open || !taskId) return;
    api.get(`/admin/tasks/${encodeURIComponent(taskId)}`)
      .then(({ data }) => {
        const loaded = data?.data;
        if (!loaded) {
          toast.error("Unable to load task details");
          return;
        }
        setDetail({ ...loaded, id: canonicalTaskId(loaded) || taskId });
      })
      .catch((err) => toast.error(describeApiError(err, "Unable to load task details")));
  }, [open, task]);

  if (!detail) return null;

  const employees = options.employees || [];
  const customers = options.customers || [];
  const services = options.services || [];
  const bookings = options.bookings || [];
  const due = taskDue(detail);
  const checklist = detail.checklist || [];
  const attachments = detail.attachments || [];
  const documents = detail.documents || [];

  const refreshDetail = async () => {
    const { data } = await api.get(`/admin/tasks/${detail.id}`);
    setDetail(data?.data || detail);
    onChanged?.();
  };

  const update = async (patch) => {
    setSaving(true);
    try {
      const { data } = await api.put(`/admin/tasks/${detail.id}`, patch);
      setDetail(data?.data || detail);
      onChanged?.();
      toast.success("Task updated");
    } catch (err) {
      toast.error(describeApiError(err, "Unable to update task"));
    } finally {
      setSaving(false);
    }
  };

  const startEdit = () => {
    setForm({
      title: detail.title || "",
      description: detail.description || "",
      priority: detail.priority || "Medium",
      status: detail.status || "TO DO",
      employee_id: detail.employee_id || "",
      customer_id: detail.customer_id || "",
      booking_id: detail.booking_id || "",
      service_id: detail.service_id || "",
      due_date: detail.due_date || "",
    });
    setEditing(true);
  };

  const saveEdit = async () => {
    if (saving) return;
    if (!String(form.title || "").trim()) return toast.error("Task title is required");
    setSaving(true);
    try {
      const { data } = await api.put(`/admin/tasks/${detail.id}`, {
        ...form,
        employee_id: form.employee_id || null,
        customer_id: form.customer_id || null,
        booking_id: form.booking_id || null,
        service_id: form.service_id || null,
        due_date: form.due_date || null,
      });
      setDetail(data?.data || detail);
      setEditing(false);
      onChanged?.();
      toast.success("Task updated");
    } catch (err) {
      toast.error(describeApiError(err, "Unable to update task"));
    } finally {
      setSaving(false);
    }
  };

  const addComment = async () => {
    if (!comment.trim()) return;
    setCommenting(true);
    try {
      await api.post(`/admin/tasks/${detail.id}/comment`, { comment: comment.trim() });
      await refreshDetail();
      setComment("");
      toast.success("Comment added");
    } catch (err) {
      toast.error(describeApiError(err, "Unable to add comment"));
    } finally {
      setCommenting(false);
    }
  };

  const addChecklist = async () => {
    if (!newChecklist.trim()) return;
    try {
      await api.post(`/admin/tasks/${detail.id}/checklist`, { text: newChecklist.trim() });
      setNewChecklist("");
      await refreshDetail();
    } catch (err) {
      toast.error(describeApiError(err, "Unable to add checklist item"));
    }
  };

  const toggleChecklist = async (item) => {
    try {
      await api.put(`/admin/tasks/${detail.id}/checklist/${item.id}`, { completed: !item.completed });
      await refreshDetail();
    } catch (err) {
      toast.error(describeApiError(err, "Unable to update checklist item"));
    }
  };

  const deleteChecklist = async (item) => {
    try {
      await api.delete(`/admin/tasks/${detail.id}/checklist/${item.id}`);
      await refreshDetail();
    } catch (err) {
      toast.error(describeApiError(err, "Unable to delete checklist item"));
    }
  };

  const uploadAttachment = async (file) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) return toast.error("Please upload a file under 5MB.");
    setUploading(true);
    try {
      const fileData = await readFileAsDataUrl(file);
      await api.post(`/admin/tasks/${detail.id}/attachments`, {
        file_name: file.name,
        file_type: file.type,
        mime_type: file.type || "application/octet-stream",
        file_data: fileData,
      });
      await refreshDetail();
      toast.success("Attachment uploaded");
    } catch (err) {
      toast.error(describeApiError(err, "Unable to upload attachment"));
    } finally {
      setUploading(false);
    }
  };

  const viewCustomerDocument = async (doc, download = false) => {
    try {
      const { data } = await api.get(`/documents/${doc.id}`);
      const file = data?.data || {};
      openDataUrl(file.file_data, file.file_name || doc.file_name, download);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to access document"));
    }
  };

  const viewAttachment = async (att, download = false) => {
    try {
      const { data } = await api.get(`/admin/tasks/${detail.id}/attachments/${att.id}`);
      const file = data?.data || {};
      openDataUrl(file.file_data, file.file_name || att.file_name, download);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to access attachment"));
    }
  };

  const deleteAttachment = async (att) => {
    if (!window.confirm(`Delete ${att.file_name || "this attachment"}?`)) return;
    try {
      await api.delete(`/admin/tasks/${detail.id}/attachments/${att.id}`);
      await refreshDetail();
      toast.success("Attachment deleted");
    } catch (err) {
      toast.error(describeApiError(err, "Unable to delete attachment"));
    }
  };

  const duplicateTask = async () => {
    setDuplicating(true);
    try {
      const { data } = await api.post(`/admin/tasks/${detail.id}/duplicate`, {});
      toast.success("Task duplicated");
      onChanged?.();
      setDetail(data?.data || detail);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to duplicate task"));
    } finally {
      setDuplicating(false);
    }
  };

  const deleteTask = async () => {
    if (!window.confirm("Delete this task and its activity history?")) return;
    setSaving(true);
    try {
      await api.delete(`/admin/tasks/${detail.id}`);
      toast.success("Task deleted");
      onOpenChange(false);
      onChanged?.();
    } catch (err) {
      toast.error(describeApiError(err, "Unable to delete task"));
    } finally {
      setSaving(false);
    }
  };

  const openCustomer = () => {
    if (detail.customer_id) window.location.href = `/admin/customers?open=${encodeURIComponent(detail.customer_id)}`;
  };

  const progress = checklist.length
    ? Math.round(checklist.filter((x) => x.completed).length / checklist.length * 100)
    : 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[calc(100vw-1.5rem)] max-w-6xl max-h-[94vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-start justify-between gap-3 pr-6">
            <div className="min-w-0 flex-1">
              {editing ? (
                <Input value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} maxLength={200} />
              ) : (
                <DialogTitle className="break-words">{detail.title}</DialogTitle>
              )}
              <DialogDescription className="mt-1 break-all">
                Task ID: {detail.id} · Created {detail.created_at ? new Date(detail.created_at).toLocaleString() : "—"}
              </DialogDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge className={priorityClass(detail.priority)}>{detail.priority}</Badge>
              <Button variant="outline" size="sm" onClick={duplicateTask} disabled={duplicating}>
                <Copy className="h-3.5 w-3.5 mr-1" /> Duplicate
              </Button>
              {!editing && <Button variant="outline" size="sm" onClick={startEdit}><Pencil className="h-3.5 w-3.5 mr-1" /> Edit</Button>}
            </div>
          </div>
        </DialogHeader>

        {editing ? (
          <div className="rounded-lg border p-4 bg-zinc-50 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Description" className="md:col-span-2"><Textarea rows={4} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} /></Field>
              <Field label="Status"><NativeSelect value={form.status} onChange={(e) => setForm((f) => ({ ...f, status: e.target.value }))}>{TASK_STATUSES.map((x) => <option key={x} disabled={(TASK_STATUS_RANK[x] ?? -1) < (TASK_STATUS_RANK[detail.status] ?? -1)}>{x}</option>)}</NativeSelect></Field>
              <Field label="Priority"><NativeSelect value={form.priority} onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))}>{TASK_PRIORITIES.map((x) => <option key={x}>{x}</option>)}</NativeSelect></Field>
              <Field label="Employee"><NativeSelect value={form.employee_id} onChange={(e) => setForm((f) => ({ ...f, employee_id: e.target.value }))}><option value="">Unassigned</option>{employees.map((e) => <option key={e.id} value={e.id}>{e.name || e.emp_id || e.id}</option>)}</NativeSelect></Field>
              <Field label="Customer"><NativeSelect value={form.customer_id} onChange={(e) => setForm((f) => ({ ...f, customer_id: e.target.value, booking_id: "", service_id: "" }))}><option value="">No customer</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.business_name || c.owner || c.name || c.id}</option>)}</NativeSelect></Field>
              <Field label="Booking"><NativeSelect value={form.booking_id} onChange={(e) => { const b = bookings.find((x) => x.id === e.target.value); setForm((f) => ({ ...f, booking_id: e.target.value, customer_id: b?.customer_id || f.customer_id, service_id: b?.service_id || f.service_id })); }}><option value="">No booking</option>{bookings.filter((b) => !form.customer_id || !b.customer_id || b.customer_id === form.customer_id).map((b) => <option key={b.id} value={b.id}>{b.booking_no || b.id} · {b.customer || ""}</option>)}</NativeSelect></Field>
              <Field label="Service"><NativeSelect value={form.service_id} onChange={(e) => setForm((f) => ({ ...f, service_id: e.target.value }))}><option value="">No service</option>{services.map((s) => <option key={s.id} value={s.id}>{s.name || s.title || s.category || s.id}</option>)}</NativeSelect></Field>
              <Field label="Due date"><Input type="date" value={form.due_date || ""} onChange={(e) => setForm((f) => ({ ...f, due_date: e.target.value }))} /></Field>
            </div>
            <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setEditing(false)}>Cancel</Button><Button onClick={saveEdit} disabled={saving}>{saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}Save changes</Button></div>
          </div>
        ) : null}

        <div className="grid grid-cols-1 xl:grid-cols-[1fr_330px] gap-6">
          <div className="space-y-5">
            <div className="rounded-lg border p-4 bg-zinc-50/50">
              <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-2">Description</p>
              <p className="text-sm text-zinc-700 whitespace-pre-wrap break-words">{detail.description || "No description added."}</p>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                ["Customer", detail.customer || "—"],
                ["Booking", detail.booking_no || "—"],
                ["Service", detail.service || "—"],
                ["Due", due?.label || "—"],
              ].map(([label, value]) => <div key={label} className="rounded-lg border p-3"><p className="text-xs text-zinc-500">{label}</p><p className={`text-sm font-medium mt-1 truncate ${label === "Due" && due?.label === "Overdue" ? "text-red-600" : ""}`}>{value}</p></div>)}
            </div>

            <section className="rounded-lg border p-4">
              <div className="flex items-center justify-between gap-3 mb-3">
                <h3 className="font-semibold text-sm">Customer Details</h3>
                {detail.customer_id && <Button size="sm" variant="outline" onClick={openCustomer}>View Customer</Button>}
              </div>
              {detail.customer_details ? (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
                  {[
                    ["Business", detail.customer_details.business_name || detail.customer_details.name || detail.customer_details.owner],
                    ["Mobile", detail.customer_details.mobile || detail.customer_details.phone],
                    ["Email", detail.customer_details.email],
                    ["GSTIN", detail.customer_details.gstin || detail.customer_details.gst_number],
                    ["PAN", detail.customer_details.pan],
                    ["Address", [detail.customer_details.address, detail.customer_details.city, detail.customer_details.state, detail.customer_details.pincode].filter(Boolean).join(", ")],
                    ["Customer ID", detail.customer_details.id],
                    ["Outstanding", detail.customer_details.outstanding != null ? `₹${Number(detail.customer_details.outstanding).toLocaleString("en-IN")}` : "—"],
                  ].map(([k, v]) => <div key={k}><p className="text-xs text-zinc-500">{k}</p><p className="font-medium break-words">{v || "—"}</p></div>)}
                </div>
              ) : <p className="text-sm text-zinc-500">No linked customer.</p>}
            </section>

            <section className="rounded-lg border p-4">
              <h3 className="font-semibold text-sm mb-3">Customer Documents</h3>
              {documents.length ? <div className="space-y-2">{documents.map((doc) => (
                <div key={doc.id} className="flex items-center gap-3 rounded-md border p-2.5">
                  <FileText className="h-4 w-4 text-zinc-500 shrink-0" />
                  <div className="min-w-0 flex-1"><p className="text-sm font-medium truncate">{doc.file_name || "Document"}</p><p className="text-xs text-zinc-500">{doc.mime_type || doc.file_type || "File"} · {formatBytes(doc.file_size)} · {doc.uploaded_date ? new Date(doc.uploaded_date).toLocaleDateString() : "—"}</p></div>
                  <Button size="sm" variant="ghost" onClick={() => viewCustomerDocument(doc)}><Eye className="h-4 w-4 mr-1" />View</Button>
                  <Button size="sm" variant="ghost" onClick={() => viewCustomerDocument(doc, true)}><Download className="h-4 w-4 mr-1" />Download</Button>
                </div>
              ))}</div> : <p className="text-sm text-zinc-500">No customer documents.</p>}
            </section>

            <section className="rounded-lg border p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-sm">Checklist / Subtasks</h3>
                <span className="text-xs text-zinc-500">{checklist.filter((x) => x.completed).length}/{checklist.length} completed</span>
              </div>
              {checklist.length ? <div className="space-y-2">{checklist.map((item) => (
                <div key={item.id} className="flex items-center gap-2">
                  <input type="checkbox" checked={!!item.completed} onChange={() => toggleChecklist(item)} />
                  <span className={`text-sm flex-1 ${item.completed ? "line-through text-zinc-400" : ""}`}>{item.text}</span>
                  <Button size="icon" variant="ghost" className="h-7 w-7 text-red-600" onClick={() => deleteChecklist(item)}><Trash2 className="h-3.5 w-3.5" /></Button>
                </div>
              ))}</div> : <p className="text-sm text-zinc-500">No checklist items.</p>}
              <div className="flex gap-2 mt-3"><Input value={newChecklist} onChange={(e) => setNewChecklist(e.target.value)} placeholder="Add checklist item..." onKeyDown={(e) => e.key === "Enter" && addChecklist()} /><Button onClick={addChecklist}><Plus className="h-4 w-4 mr-1" />Add</Button></div>
              {checklist.length ? <div className="mt-3 h-2 rounded-full bg-zinc-100 overflow-hidden"><div className="h-full bg-amber-500" style={{ width: `${progress}%` }} /></div> : null}
            </section>

            <section className="rounded-lg border p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-sm">Task Attachments</h3>
                <label className="cursor-pointer"><input type="file" className="hidden" onChange={(e) => uploadAttachment(e.target.files?.[0])} /><Button asChild size="sm" variant="outline" disabled={uploading}><span><UploadCloud className="h-4 w-4 mr-1" />{uploading ? "Uploading..." : "Upload"}</span></Button></label>
              </div>
              {attachments.length ? <div className="space-y-2">{attachments.map((att) => (
                <div key={att.id} className="flex items-center gap-3 rounded-md border p-2.5">
                  <Paperclip className="h-4 w-4 text-zinc-500" /><div className="min-w-0 flex-1"><p className="text-sm font-medium truncate">{att.file_name}</p><p className="text-xs text-zinc-500">{att.mime_type} · {formatBytes(att.file_size)}</p></div>
                  <Button size="sm" variant="ghost" onClick={() => viewAttachment(att)}><Eye className="h-4 w-4" /></Button><Button size="sm" variant="ghost" onClick={() => viewAttachment(att, true)}><Download className="h-4 w-4" /></Button><Button size="icon" variant="ghost" className="text-red-600" onClick={() => deleteAttachment(att)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              ))}</div> : <p className="text-sm text-zinc-500">No task attachments.</p>}
            </section>

            <section>
              <h3 className="font-semibold text-sm mb-3">Activity & History</h3>
              <div className="space-y-3 border-l-2 border-zinc-200 pl-4">
                {(detail.activity || []).length ? detail.activity.map((a) => (
                  <div key={a.id} className="relative"><span className="absolute -left-[22px] top-1 h-2.5 w-2.5 rounded-full bg-amber-400 border-2 border-white" /><p className="text-sm font-medium text-zinc-800">{a.action}</p>{a.details?.comment && <p className="text-sm text-zinc-600 mt-1">{a.details.comment}</p>}<p className="text-xs text-zinc-400 mt-1">{a.user_name || "Admin"} · {a.created_at ? new Date(a.created_at).toLocaleString() : ""}</p></div>
                )) : <p className="text-sm text-zinc-500">No activity yet.</p>}
              </div>
            </section>
            <div className="flex gap-2"><Input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a comment or note..." onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); addComment(); } }} /><Button onClick={addComment} disabled={commenting || !comment.trim()}>{commenting ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageSquare className="h-4 w-4 mr-1.5" />}Comment</Button></div>
          </div>

          <div className="space-y-4">
            <Field label="Status"><NativeSelect value={detail.status} onChange={(e) => update({ status: e.target.value })} disabled={saving}>{TASK_STATUSES.map((x) => <option key={x} disabled={(TASK_STATUS_RANK[x] ?? -1) < (TASK_STATUS_RANK[detail.status] ?? -1)}>{x}</option>)}</NativeSelect></Field>
            <Field label="Priority"><NativeSelect value={detail.priority} onChange={(e) => update({ priority: e.target.value })} disabled={saving}>{TASK_PRIORITIES.map((x) => <option key={x}>{x}</option>)}</NativeSelect></Field>
            <Field label="Assigned employee"><NativeSelect value={detail.employee_id || ""} onChange={(e) => update({ employee_id: e.target.value || null })} disabled={saving}><option value="">Unassigned</option>{employees.map((e) => <option key={e.id} value={e.id}>{e.name || e.emp_id || e.id}</option>)}</NativeSelect></Field>
            <Field label="Due date"><Input type="date" value={detail.due_date || ""} disabled={saving} onChange={(e) => update({ due_date: e.target.value || null })} /></Field>
            <div className="rounded-lg border p-3 text-xs text-zinc-500 space-y-1"><p>Created: {detail.created_at ? new Date(detail.created_at).toLocaleString() : "—"}</p><p>Updated: {detail.updated_at ? new Date(detail.updated_at).toLocaleString() : "—"}</p><p>Created by: {detail.created_by_name || "—"}</p></div>
            <div className="rounded-lg border p-3"><p className="text-xs text-zinc-500">Booking</p><p className="font-medium text-sm">{detail.booking_no || "—"}</p><p className="text-xs text-zinc-500 mt-2">Service</p><p className="font-medium text-sm">{detail.service || "—"}</p><p className="text-xs text-zinc-500 mt-2">Booking status</p><p className="font-medium text-sm">{detail.booking_status || "—"}</p></div>
            <Button variant="outline" className="w-full text-red-600 hover:text-red-700 hover:bg-red-50" onClick={deleteTask} disabled={saving}><Trash2 className="h-4 w-4 mr-2" />Delete Task</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function TaskBoard({
  initialBooking = null,
  openCreate = false,
  onCloseCreate,
}) {
  const location = useLocation();
  const [tasks, setTasks] = useState([]);
  const [options, setOptions] = useState(EMPTY_OPTIONS);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [dragging, setDragging] = useState(null);
  const [createOpen, setCreateOpen] = useState(openCreate);
  const [selected, setSelected] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);
  const [kanbanLists, setKanbanLists] = useState([]);
  const [summary, setSummary] = useState({});
  const [pagination, setPagination] = useState({ page: 1, page_size: 20, total: 0, pages: 0 });
  const [viewMode, setViewMode] = useState("kanban");
  const [calendarMode, setCalendarMode] = useState("month");
  const [calendarDate, setCalendarDate] = useState(() => new Date());
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState("desc");
  const [quickFilter, setQuickFilter] = useState("all");
  const [showAnalytics, setShowAnalytics] = useState(false);
  const [analytics, setAnalytics] = useState(null);
  const [showWorkload, setShowWorkload] = useState(false);
  const [workload, setWorkload] = useState([]);
  const [expandedWorkloadEmployee, setExpandedWorkloadEmployee] = useState(null);
  const [listDialog, setListDialog] = useState({ open: false, editing: null });
  const [summaryFilter, setSummaryFilter] = useState(null);
  const [filters, setFilters] = useState({
    search: "", status: "all", priority: "all", employee_id: "all",
    customer_id: "all", service_id: "all", due: "all",
  });
  const [overColumn, setOverColumn] = useState(null);

  useEffect(() => setCreateOpen(openCreate), [openCreate]);
  useEffect(() => { if (!createOpen) onCloseCreate?.(); }, [createOpen, onCloseCreate]);

  const loadKanbanMeta = useCallback(async () => {
    try {
      const [{ data: listData }, { data: summaryData }] = await Promise.all([
        api.get("/admin/kanban/tasks/lists"),
        api.get("/admin/tasks/summary"),
      ]);
      setKanbanLists(Array.isArray(listData?.data) ? listData.data : []);
      setSummary(summaryData?.data || {});
    } catch (err) {
      toast.error(describeApiError(err, "Unable to load Task Board summary"));
    }
  }, []);

  const loadOptions = useCallback(async () => {
    try {
      const { data } = await api.get("/admin/tasks/options");
      setOptions(data?.data || EMPTY_OPTIONS);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to load task options"));
    }
  }, []);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const params = {
        page: pagination.page,
        page_size: pagination.page_size,
        sort_by: sortBy,
        sort_order: sortOrder,
      };
      Object.entries(filters).forEach(([k, v]) => { if (v && v !== "all") params[k] = v; });
      if (quickFilter === "my") params.my_tasks = true;
      if (quickFilter === "unassigned") params.unassigned = true;
      if (quickFilter === "overdue") params.overdue = true;
      if (quickFilter === "today") params.due_today = true;
      if (quickFilter === "high") params.high_priority = true;
      const { data } = await api.get("/admin/tasks", { params });
      setTasks(Array.isArray(data?.data) ? data.data : []);
      setPagination((p) => data?.pagination ? { ...p, ...data.pagination } : p);
      setSelectedIds((ids) => ids.filter((id) => (data?.data || []).some((t) => t.id === id)));
      setLoadError("");
    } catch (err) {
      const message = describeApiError(err, "Unable to load Task Board");
      setLoadError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [filters, pagination.page, pagination.page_size, quickFilter, sortBy, sortOrder]);

  // Options are only needed for create/edit dialogs. Deferring this large lookup keeps
  // the first board render independent of customer/service/booking dropdown datasets.
  useEffect(() => { loadKanbanMeta(); }, [loadKanbanMeta]);
  useEffect(() => { if (createOpen || selected) loadOptions(); }, [createOpen, selected, loadOptions]);
  useEffect(() => {
    const timer = setTimeout(loadTasks, filters.search ? 250 : 0);
    return () => clearTimeout(timer);
  }, [loadTasks, filters.search]);

  useEffect(() => {
    const refresh = () => { if (!document.hidden) { loadTasks(); loadKanbanMeta(); } };
    const onVisibility = () => { if (document.visibilityState === "visible") refresh(); };
    window.addEventListener("ntaxco:data-changed", refresh);
    document.addEventListener("visibilitychange", onVisibility);
    // A minute-level fallback avoids hammering the task and summary endpoints.
    const timer = window.setInterval(refresh, 60000);
    return () => { window.removeEventListener("ntaxco:data-changed", refresh); document.removeEventListener("visibilitychange", onVisibility); window.clearInterval(timer); };
  }, [loadTasks, loadKanbanMeta]);

  useEffect(() => {
    const taskId = new URLSearchParams(location.search).get("task");
    if (taskId && tasks.length) {
      const found = tasks.find((t) => t.id === taskId);
      if (found) setSelected(found);
    }
  }, [location.search, tasks]);

  const columns = useMemo(() => {
    const standard = TASK_STATUSES.map((status) => ({
      id: status, status, name: status, standard: true,
      tasks: tasks.filter((t) => !t.kanban_list_id && t.status === status),
    }));
    const custom = kanbanLists.map((list) => ({
      id: `custom:${list.id}`, status: `custom:${list.id}`, name: list.name, list,
      tasks: tasks.filter((t) => t.kanban_list_id === list.id),
    }));
    return [...standard, ...custom];
  }, [tasks, kanbanLists]);

  const taskColumnKey = useCallback((t) => (t.kanban_list_id ? `custom:${t.kanban_list_id}` : t.status), []);
  const flash = useKanbanFlash(tasks, taskColumnKey, !loading);

  const refreshAll = () => {
    loadTasks(); loadKanbanMeta();
    window.dispatchEvent(new CustomEvent("ntaxco:data-changed", { detail: { resources: ["tasks"] } }));
  };

  const changeStatus = async (task, status) => {
    if (!task || task.status === status) return;
    const original = task;
    setTasks((prev) => prev.map((t) => t.id === task.id ? { ...t, status } : t));
    try {
      const { data } = await api.put(`/admin/tasks/${task.id}`, { status });
      setTasks((prev) => prev.map((t) => t.id === task.id ? (data?.data || { ...t, status }) : t));
      toast.success(`Task moved to ${status}`);
      refreshAll();
    } catch (err) {
      setTasks((prev) => prev.map((t) => t.id === task.id ? original : t));
      toast.error(describeApiError(err, "Unable to change task status"));
    } finally {
      setDragging(null);
    }
  };

  const moveToKanbanColumn = async (task, column) => {
    if (!task) return;
    if (column.standard) return changeStatus(task, column.status);
    if (task.kanban_list_id === column.list.id) return;
    try {
      const { data } = await api.post("/admin/kanban/tasks/move", { record_id: task.id, list_id: column.list.id });
      setTasks((prev) => prev.map((t) => t.id === task.id ? (data?.data || { ...t, kanban_list_id: column.list.id, kanban_list_name: column.list.name }) : t));
      await loadKanbanMeta();
      toast.success(`Task moved to ${column.name}`);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to move task"));
    } finally { setDragging(null); }
  };

  const resetFilters = () => {
    setFilters({ search: "", status: "all", priority: "all", employee_id: "all", customer_id: "all", service_id: "all", due: "all" });
    setQuickFilter("all");
    setSummaryFilter(null);
    setPagination((p) => ({ ...p, page: 1 }));
  };

  const setQuick = (key) => {
    setQuickFilter(key);
    setSummaryFilter(null);
    setPagination((p) => ({ ...p, page: 1 }));
    if (key === "all") setFilters((f) => ({ ...f, due: "all", priority: "all" }));
  };

  const openAnalytics = async () => {
    setShowAnalytics((v) => !v);
    if (!analytics) {
      try { const { data } = await api.get("/admin/tasks/analytics"); setAnalytics(data?.data || null); }
      catch (err) { toast.error(describeApiError(err, "Unable to load task analytics")); }
    }
  };

  const openWorkload = async () => {
    setShowWorkload((v) => !v);
    if (!workload.length) {
      try { const { data } = await api.get("/admin/tasks/workload"); setWorkload(Array.isArray(data?.data) ? data.data : []); }
      catch (err) { toast.error(describeApiError(err, "Unable to load employee workload")); }
    }
  };

  const toggleSelect = (id) => setSelectedIds((ids) => ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);

  const bulkAction = async (action, extra = {}) => {
    if (!selectedIds.length) return toast.error("Select at least one task");
    if (action === "delete" && !window.confirm(`Delete ${selectedIds.length} selected task(s)? This cannot be undone.`)) return;
    try {
      await api.post("/admin/tasks/bulk", { task_ids: selectedIds, action, ...extra });
      toast.success(`Bulk ${action} completed`);
      setSelectedIds([]);
      refreshAll();
    } catch (err) { toast.error(describeApiError(err, "Bulk operation failed")); }
  };

  const deleteKanbanList = async (list) => {
    if (!window.confirm(`Delete "${list.name}"? Tasks in this list will return to their status column.`)) return;
    try { await api.delete(`/admin/kanban/tasks/lists/${list.id}`); await Promise.all([loadTasks(), loadKanbanMeta()]); toast.success("List deleted"); }
    catch (err) { toast.error(describeApiError(err, "Unable to delete list")); }
  };

  const taskCard = (task) => {
    const due = taskDue(task);
    return (
      <article key={task.id} draggable onDragStart={() => setDragging(task)} onDragEnd={() => { setDragging(null); setOverColumn(null); }}
        onClick={() => setSelected(task)}
        className={`kanban-card bg-white rounded-lg border border-zinc-200 p-3 shadow-sm cursor-grab active:cursor-grabbing hover:border-zinc-300 w-full min-w-0 overflow-hidden min-h-[176px] flex flex-col ${dragging?.id === task.id ? "kanban-card-dragging" : flashClass(flash[String(task.id)])}`}>
        <div className="flex items-start gap-2 flex-1 min-h-0">
          <input type="checkbox" checked={selectedIds.includes(task.id)} onChange={(e) => { e.stopPropagation(); toggleSelect(task.id); }} onClick={(e) => e.stopPropagation()} className="mt-1 shrink-0" />
          <GripVertical className="h-4 w-4 text-zinc-300 shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1 h-full flex flex-col">
            <div className="flex items-start justify-between gap-2">
              <h3 className="min-w-0 font-medium text-sm text-zinc-900 leading-5 line-clamp-2 break-words" title={task.title}>{task.title}</h3>
              <Badge className={`shrink-0 text-[10px] ${priorityClass(task.priority)}`}><Flag aria-hidden="true" className="mr-1 inline h-3 w-3" />{task.priority}</Badge>
            </div>
            {task.description && <p className="text-xs text-zinc-500 line-clamp-2 mt-1.5">{task.description}</p>}
            <div className="flex flex-wrap gap-1.5 mt-2">
              {task.customer && <span title={`Customer: ${task.customer}`} className="inline-flex items-center gap-1 text-[11px] rounded bg-zinc-50 px-1.5 py-1 text-zinc-600 max-w-[48%] truncate"><Building2 aria-hidden="true" className="h-3 w-3 shrink-0"/><span className="truncate">{task.customer}</span></span>}
              {task.service && <span title={`Service: ${task.service}`} className="inline-flex items-center gap-1 text-[11px] rounded bg-zinc-50 px-1.5 py-1 text-zinc-600 max-w-[48%] truncate"><Briefcase aria-hidden="true" className="h-3 w-3 shrink-0"/><span className="truncate">{task.service}</span></span>}
            </div>
            {task.checklist?.length ? <span className="text-[11px] text-zinc-500 mt-2"><CheckSquare className="inline h-3.5 w-3.5 mr-1" />{task.checklist.filter((x) => x.completed).length}/{task.checklist.length}</span> : null}
            <div className="flex items-center justify-between gap-2 mt-auto pt-2 border-t border-zinc-100">
              <span className="flex min-w-0 items-center gap-1 text-[11px] text-zinc-500 truncate" title={`Assigned to: ${task.assigned_employee || "Unassigned"}`}><UserRound aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />{task.assigned_employee || "Unassigned"}</span>
              {due && <span className={`inline-flex items-center gap-1 shrink-0 text-[10px] font-medium px-1.5 py-1 rounded ${due.cls}`}><CalendarClock aria-hidden="true" className="h-3 w-3"/>{due.label}</span>}
            </div>
          </div>
        </div>
      </article>
    );
  };

  return (
    <div>
      <PageHeader title="Task Board" subtitle="Internal NTAXCO task management — linked to live customers, bookings, services and employees." breadcrumb={["Super Admin", "Task Board"]}
        actions={<div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={refreshAll} disabled={loading}><RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
          <Button variant="outline" onClick={() => setListDialog({ open: true, editing: null })}><Plus className="h-4 w-4 mr-1.5" />Add List</Button>
          <Button variant="outline" onClick={openAnalytics}><BarChart3 className="h-4 w-4 mr-1.5" />Analytics</Button>
          <Button variant="outline" onClick={openWorkload}><BriefcaseBusiness className="h-4 w-4 mr-1.5" />Workload</Button>
          <Button onClick={() => setCreateOpen(true)} className="bg-amber-500 hover:bg-amber-600 text-zinc-950"><Plus className="h-4 w-4 mr-1.5" />Create Task</Button>
        </div>}
      />

      {loadError && <div role="alert" className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"><span>{loadError}</span><Button size="sm" variant="outline" onClick={refreshAll}><RefreshCw className="mr-1 h-3.5 w-3.5"/>Retry</Button></div>}
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3 mb-5">
        {[
          ["Today's Due", summary.today_due, "today"], ["In Progress", summary.in_progress, "IN PROGRESS"], ["Review", summary.review, "REVIEW"],
          ["Completed", summary.completed, "COMPLETED"], ["Today's Tasks Completed", summary.today_completed, "today-completed"],
          ["This Month", summary.this_month, "month"], ["This Year", summary.this_year, "year"], ["Pending Tasks", summary.pending, "pending"],
        ].map(([label, value, key]) => (
          <div key={key} onClick={() => { setSummaryFilter((c) => c === key ? null : key); setPagination((p) => ({ ...p, page: 1 })); setFilters((f) => ({ ...f, status: ["IN PROGRESS", "REVIEW", "COMPLETED"].includes(key) ? key : "all" })); }} className="cursor-pointer">
            <KpiCard title={label} value={Number(value || 0)} loading={!Object.keys(summary).length} tone={key === "COMPLETED" ? "green" : key === "REVIEW" ? "amber" : key === "pending" ? "orange" : "royal"} />
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        {[["all", "All Tasks"], ["my", "My Tasks"], ["unassigned", "Unassigned"], ["overdue", "Overdue"], ["today", "Due Today"], ["high", "High Priority"]].map(([key, label]) => (
          <Button key={key} size="sm" variant={quickFilter === key ? "default" : "outline"} onClick={() => setQuick(key)}>{label}</Button>
        ))}
      </div>

      <div className="bg-white border border-zinc-200 rounded-xl p-3 mb-5">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-2">
          <div className="relative xl:col-span-2"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400" /><Input className="pl-9" placeholder="Search title, description, customer, employee, booking, service, ID..." value={filters.search} onChange={(e) => { setFilters((f) => ({ ...f, search: e.target.value })); setPagination((p) => ({ ...p, page: 1 })); }} /></div>
          <NativeSelect value={filters.status} onChange={(e) => { setFilters((f) => ({ ...f, status: e.target.value })); setPagination((p) => ({ ...p, page: 1 })); }}><option value="all">All statuses</option>{TASK_STATUSES.map((x) => <option key={x}>{x}</option>)}</NativeSelect>
          <NativeSelect value={filters.priority} onChange={(e) => { setFilters((f) => ({ ...f, priority: e.target.value })); setPagination((p) => ({ ...p, page: 1 })); }}><option value="all">All priorities</option>{TASK_PRIORITIES.map((x) => <option key={x}>{x}</option>)}</NativeSelect>
          <NativeSelect value={filters.employee_id} onChange={(e) => { setFilters((f) => ({ ...f, employee_id: e.target.value })); setPagination((p) => ({ ...p, page: 1 })); }}><option value="all">All employees</option>{options.employees.map((e) => <option key={e.id} value={e.id}>{e.name || e.emp_id}</option>)}</NativeSelect>
          <NativeSelect value={filters.customer_id} onChange={(e) => { setFilters((f) => ({ ...f, customer_id: e.target.value })); setPagination((p) => ({ ...p, page: 1 })); }}><option value="all">All customers</option>{options.customers.map((c) => <option key={c.id} value={c.id}>{c.business_name || c.owner || c.name}</option>)}</NativeSelect>
          <NativeSelect value={filters.service_id} onChange={(e) => { setFilters((f) => ({ ...f, service_id: e.target.value })); setPagination((p) => ({ ...p, page: 1 })); }}><option value="all">All services</option>{options.services.map((s) => <option key={s.id} value={s.id}>{s.name || s.title || s.category}</option>)}</NativeSelect>
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-2">
          <Filter className="h-4 w-4 text-zinc-400" />
          <NativeSelect value={filters.due} onChange={(e) => { setFilters((f) => ({ ...f, due: e.target.value })); setPagination((p) => ({ ...p, page: 1 })); }}><option value="all">All due dates</option><option value="overdue">Overdue</option><option value="today">Due today</option><option value="tomorrow">Due tomorrow</option><option value="upcoming">Upcoming</option></NativeSelect>
          <NativeSelect value={sortBy} onChange={(e) => { setSortBy(e.target.value); setPagination((p) => ({ ...p, page: 1 })); }}><option value="created_at">Sort: Created</option><option value="due_date">Sort: Due Date</option><option value="priority">Sort: Priority</option><option value="updated_at">Sort: Updated</option><option value="customer">Sort: Customer</option><option value="employee">Sort: Employee</option><option value="title">Sort: Task Name</option></NativeSelect>
          <Button size="sm" variant="outline" onClick={() => setSortOrder((v) => v === "asc" ? "desc" : "asc")}>{sortOrder === "asc" ? "Ascending" : "Descending"}</Button>
          <Button variant="ghost" size="sm" onClick={resetFilters}>Clear filters</Button>
          <div className="ml-auto flex items-center gap-1 border rounded-md p-1">
            {[["kanban", "Kanban", List], ["list", "List", List], ["calendar", "Calendar", CalendarIcon]].map(([key, label, Icon]) => <Button key={key} size="sm" variant={viewMode === key ? "default" : "ghost"} onClick={() => setViewMode(key)}><Icon className="h-4 w-4 mr-1" />{label}</Button>)}
          </div>
        </div>
        {selectedIds.length ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-amber-50 border border-amber-200 p-2">
            <span className="text-sm font-medium">{selectedIds.length} selected</span>
            <NativeSelect value="" onChange={(e) => { const v = e.target.value; if (v === "delete") bulkAction("delete"); if (v === "high") bulkAction("priority", { priority: "High" }); if (v === "urgent") bulkAction("priority", { priority: "Urgent" }); if (v === "progress") bulkAction("status", { status: "IN PROGRESS" }); if (v === "review") bulkAction("status", { status: "REVIEW" }); if (v === "completed") bulkAction("status", { status: "COMPLETED" }); if (v === "todo") bulkAction("status", { status: "TO DO" }); }}><option value="">Bulk action...</option><option value="progress">Move to In Progress</option><option value="review">Move to Review</option><option value="completed">Mark Completed</option><option value="todo">Move to To Do</option><option value="high">Set High</option><option value="urgent">Set Urgent</option><option value="delete">Delete</option></NativeSelect>
            <Button size="sm" variant="ghost" onClick={() => setSelectedIds([])}>Clear selection</Button>
          </div>
        ) : null}
      </div>

      {showAnalytics && analytics && (
        <div className="mb-5 rounded-xl border bg-white p-4">
          <div className="flex items-center justify-between mb-4"><h2 className="font-semibold">Task Analytics</h2><Button size="sm" variant="ghost" onClick={() => setShowAnalytics(false)}><X className="h-4 w-4" /></Button></div>
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
            {[
              ["Total", analytics.summary?.total], ["Completed", analytics.summary?.completed], ["In Progress", analytics.summary?.in_progress],
              ["Review", analytics.summary?.review], ["Overdue", analytics.summary?.overdue], ["Due Today", analytics.summary?.due_today],
              ["Completion %", `${analytics.summary?.completion_percentage || 0}%`], ["Avg. Completion", `${analytics.summary?.average_completion_hours || 0}h`],
            ].map(([k, v]) => <div key={k} className="rounded-lg border p-3"><p className="text-xs text-zinc-500">{k}</p><p className="text-lg font-semibold mt-1">{v}</p></div>)}
          </div>
          <div className="grid md:grid-cols-2 gap-4 mt-4"><div><h3 className="text-sm font-semibold mb-2">Status Distribution</h3>{(analytics.by_status || []).map((x) => <div key={x.name} className="flex justify-between text-sm py-1"><span>{x.name}</span><b>{x.value}</b></div>)}</div><div><h3 className="text-sm font-semibold mb-2">Priority Distribution</h3>{(analytics.by_priority || []).map((x) => <div key={x.name} className="flex justify-between text-sm py-1"><span>{x.name}</span><b>{x.value}</b></div>)}</div></div>
        </div>
      )}

      {showWorkload && (
        <div className="mb-5 rounded-xl border bg-white p-4">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="font-semibold">Employee Workload</h2>
              <p className="text-xs text-zinc-500 mt-1">Click an employee to filter the board, or open an individual task directly.</p>
            </div>
            <Button size="sm" variant="ghost" onClick={() => { setShowWorkload(false); setExpandedWorkloadEmployee(null); }}>
              <X className="h-4 w-4" />
            </Button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b text-left">
                <th className="p-2">Employee</th><th className="p-2">Total</th><th className="p-2">To Do</th><th className="p-2">In Progress</th>
                <th className="p-2">Review</th><th className="p-2">Completed</th><th className="p-2">Overdue</th><th className="p-2">High</th>
              </tr></thead>
              <tbody>
                {workload.map((w) => {
                  const expanded = expandedWorkloadEmployee === w.employee_id;
                  const validTasks = Array.isArray(w.tasks) ? w.tasks.filter((t) => canonicalTaskId(t)) : [];
                  return (
                    <Fragment key={w.employee_id}>
                      <tr className="border-b hover:bg-zinc-50 cursor-pointer" onClick={() => {
                        setExpandedWorkloadEmployee(expanded ? null : w.employee_id);
                        setFilters((f) => ({ ...f, employee_id: w.employee_id }));
                        setQuickFilter("all");
                        setPagination((p) => ({ ...p, page: 1 }));
                      }}>
                        <td className="p-2 font-medium">{w.employee}</td><td className="p-2">{w.total}</td><td className="p-2">{w.todo}</td>
                        <td className="p-2">{w.in_progress}</td><td className="p-2">{w.review}</td><td className="p-2">{w.completed}</td>
                        <td className="p-2 text-red-600">{w.overdue}</td><td className="p-2">{w.high_priority}</td>
                      </tr>
                      {expanded && (
                        <tr className="border-b bg-zinc-50/70">
                          <td colSpan={8} className="p-3">
                            {validTasks.length ? (
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                {validTasks.map((t) => {
                                  const taskId = canonicalTaskId(t);
                                  return (
                                    <button key={taskId} type="button" className="text-left rounded-lg border bg-white p-3 hover:border-zinc-400"
                                      onClick={(e) => { e.stopPropagation(); setSelected({ ...t, id: taskId }); }}>
                                      <div className="font-medium text-sm truncate">{t.title}</div>
                                      <div className="mt-1 text-xs text-zinc-500">{t.status} · {t.priority}{t.due_date ? ` · ${String(t.due_date).slice(0, 10)}` : ""}</div>
                                    </button>
                                  );
                                })}
                              </div>
                            ) : (
                              <div className="text-xs text-zinc-500 py-2">No active task records are available for this employee.</div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {viewMode === "kanban" && (
        <div className="overflow-x-auto pb-3"><div className="flex gap-4 min-w-max items-start">
          {columns.map(({ status, name, tasks: columnTasks, standard, list }) => {
            const meta = statusMeta[status] || statusMeta["TO DO"]; const Icon = meta.icon;
            return <section key={status} className={`kanban-column w-[min(285px,80vw)] shrink-0 rounded-xl border bg-zinc-50/80 min-h-[520px] ${dragging && overColumn === status ? "kanban-column-over" : "border-zinc-200"}`}
              data-testid={`task-column-${status}`} onDragOver={(e) => { e.preventDefault(); if (overColumn !== status) setOverColumn(status); }} onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOverColumn((c) => c === status ? null : c); }}
              onDrop={(e) => { e.preventDefault(); setOverColumn(null); if (dragging) moveToKanbanColumn(dragging, { status, name, standard, list }); }}>
              <div className={`rounded-t-xl px-3 py-3 flex items-center justify-between ${meta.header}`}><div className="flex items-center gap-2"><Icon className={`h-4 w-4 ${meta.tone}`} /><h2 className="text-sm font-semibold text-zinc-800">{name}</h2></div><div className="flex items-center gap-1"><span className="text-xs font-semibold rounded-full bg-white/80 px-2 py-0.5 text-zinc-500">{columnTasks.length}</span>{!standard && list && <><Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); setListDialog({ open: true, editing: { ...list, board: "tasks" } }); }}><Pencil className="h-3.5 w-3.5" /></Button><Button variant="ghost" size="icon" className="h-7 w-7 text-red-600" onClick={(e) => { e.stopPropagation(); deleteKanbanList(list); }}><Trash2 className="h-3.5 w-3.5" /></Button></>}</div></div>
              <div className="p-2 space-y-2 min-h-[470px]">{loading ? <div className="py-10 text-center text-sm text-zinc-400"><Loader2 className="h-5 w-5 animate-spin mx-auto mb-2" />Loading tasks...</div> : columnTasks.map(taskCard)}{!loading && !columnTasks.length && <KanbanEmptyState label="No tasks in this stage" active={!!dragging && overColumn === status} />}</div>
            </section>;
          })}
        </div></div>
      )}

      {viewMode === "list" && (
        <div className="bg-white border rounded-xl overflow-x-auto"><table className="w-full text-sm"><thead className="bg-zinc-50"><tr><th className="p-3 text-left">Task</th><th className="p-3 text-left">Customer</th><th className="p-3 text-left">Service</th><th className="p-3 text-left">Employee</th><th className="p-3 text-left">Priority</th><th className="p-3 text-left">Due</th><th className="p-3 text-left">Status</th></tr></thead><tbody>{tasks.map((task) => <tr key={task.id} className="border-t hover:bg-zinc-50 cursor-pointer" onClick={() => setSelected(task)}><td className="p-3 font-medium">{task.title}</td><td className="p-3">{task.customer || "—"}</td><td className="p-3">{task.service || "—"}</td><td className="p-3">{task.assigned_employee || "Unassigned"}</td><td className="p-3"><Badge className={priorityClass(task.priority)}>{task.priority}</Badge></td><td className="p-3">{task.due_date || "—"}</td><td className="p-3">{task.status}</td></tr>)}{!loading && !tasks.length && <tr><td colSpan="7" className="p-10 text-center text-zinc-500">No tasks found.</td></tr>}</tbody></table></div>
      )}

      {viewMode === "calendar" && (() => {
        const y = calendarDate.getFullYear();
        const m = calendarDate.getMonth();
        const monthDays = new Date(y, m + 1, 0).getDate();
        const firstDay = new Date(y, m, 1).getDay();
        const dayKey = (d) => `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
        const dayTasks = (key) => tasks.filter((t) => String(t.due_date || "").slice(0, 10) === key);
        const shift = (amount) => setCalendarDate((d) => {
          const n = new Date(d);
          if (calendarMode === "month") n.setMonth(n.getMonth() + amount);
          else if (calendarMode === "week") n.setDate(n.getDate() + amount * 7);
          else n.setDate(n.getDate() + amount);
          return n;
        });
        const weekStart = new Date(calendarDate);
        weekStart.setDate(weekStart.getDate() - weekStart.getDay());
        const weekDays = Array.from({ length: 7 }, (_, i) => {
          const d = new Date(weekStart); d.setDate(weekStart.getDate() + i);
          return { d, key: d.toISOString().slice(0, 10) };
        });
        const dayKeySingle = calendarDate.toISOString().slice(0, 10);
        return (
          <div className="bg-white border rounded-xl p-4">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" onClick={() => shift(-1)}><ChevronLeft className="h-4 w-4" /></Button>
                <Button size="sm" variant="outline" onClick={() => setCalendarDate(new Date())}>Today</Button>
                <Button size="sm" variant="outline" onClick={() => shift(1)}><ChevronRight className="h-4 w-4" /></Button>
                <span className="font-semibold ml-2">{calendarMode === "month" ? calendarDate.toLocaleString("en-IN", { month: "long", year: "numeric" }) : calendarMode === "day" ? calendarDate.toLocaleDateString("en-IN", { dateStyle: "full" }) : `Week of ${weekStart.toLocaleDateString("en-IN")}`}</span>
              </div>
              <div className="flex gap-1">{["month", "week", "day"].map((v) => <Button key={v} size="sm" variant={calendarMode === v ? "default" : "outline"} onClick={() => setCalendarMode(v)}>{v[0].toUpperCase() + v.slice(1)}</Button>)}</div>
            </div>
            {calendarMode === "month" && (
              <div className="grid grid-cols-7 gap-1">
                {["Sun","Mon","Tue","Wed","Thu","Fri","Sat"].map((d) => <div key={d} className="p-2 text-xs font-semibold text-zinc-500">{d}</div>)}
                {Array.from({ length: firstDay }).map((_, i) => <div key={`blank-${i}`} className="min-h-[100px] rounded-md bg-zinc-50" />)}
                {Array.from({ length: monthDays }, (_, i) => i + 1).map((d) => {
                  const items = dayTasks(dayKey(d));
                  return <div key={d} className="min-h-[100px] rounded-md border p-2"><div className="text-xs font-semibold text-zinc-600">{d}</div><div className="space-y-1 mt-1">{items.slice(0, 4).map((t) => <button key={t.id} onClick={() => setSelected(t)} className="w-full text-left text-[11px] rounded bg-zinc-50 hover:bg-zinc-100 px-1.5 py-1 truncate">{t.title}</button>)}{items.length > 4 && <span className="text-[10px] text-zinc-400">+{items.length - 4} more</span>}</div></div>;
                })}
              </div>
            )}
            {calendarMode === "week" && (
              <div className="grid grid-cols-7 gap-2">{weekDays.map(({ d, key }) => <div key={key} className="min-h-[260px] rounded-md border p-2"><p className="text-xs font-semibold text-zinc-600">{d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric" })}</p><div className="space-y-2 mt-2">{dayTasks(key).map((t) => <button key={t.id} onClick={() => setSelected(t)} className="w-full text-left rounded border p-2 text-xs hover:bg-zinc-50"><b className="block truncate">{t.title}</b><span>{t.status} · {t.assigned_employee || "Unassigned"}</span></button>)}</div></div>)}</div>
            )}
            {calendarMode === "day" && <div className="space-y-2">{dayTasks(dayKeySingle).map((t) => <button key={t.id} onClick={() => setSelected(t)} className="w-full text-left rounded-lg border p-3 hover:bg-zinc-50"><div className="flex justify-between gap-2"><span className="font-medium">{t.title}</span><Badge className={priorityClass(t.priority)}>{t.priority}</Badge></div><p className="text-xs text-zinc-500 mt-1">{t.customer || "No customer"} · {t.service || "No service"} · {t.status}</p></button>)}{!dayTasks(dayKeySingle).length && <p className="py-10 text-center text-sm text-zinc-500">No tasks due on this day.</p>}</div>}
          </div>
        );
      })()}

      <div className="flex flex-wrap items-center justify-between gap-3 mt-4 text-sm text-zinc-600">
        <span>Showing {pagination.total ? ((pagination.page - 1) * pagination.page_size + 1) : 0}–{Math.min(pagination.page * pagination.page_size, pagination.total)} of {pagination.total} tasks</span>
        <div className="flex items-center gap-2"><span>Rows</span><NativeSelect value={pagination.page_size} onChange={(e) => setPagination((p) => ({ ...p, page_size: Number(e.target.value), page: 1 }))}><option value="20">20</option><option value="50">50</option><option value="100">100</option></NativeSelect><Button size="sm" variant="outline" disabled={pagination.page <= 1} onClick={() => setPagination((p) => ({ ...p, page: p.page - 1 }))}><ChevronLeft className="h-4 w-4" /></Button><span>Page {pagination.page} of {pagination.pages || 1}</span><Button size="sm" variant="outline" disabled={pagination.page >= (pagination.pages || 1)} onClick={() => setPagination((p) => ({ ...p, page: p.page + 1 }))}><ChevronRight className="h-4 w-4" /></Button></div>
      </div>

      <TaskCreateDialog open={createOpen} onOpenChange={setCreateOpen} options={options} initialBooking={initialBooking} onCreated={refreshAll} />
      <KanbanListDialog board="tasks" open={listDialog.open} onOpenChange={(open) => setListDialog((d) => ({ ...d, open }))} editing={listDialog.editing} onSaved={() => { loadKanbanMeta(); loadTasks(); }} />
      <TaskDetailsDialog task={selected} open={!!selected} onOpenChange={(o) => !o && setSelected(null)} options={options} onChanged={refreshAll} />
    </div>
  );
}

export { TaskCreateDialog };