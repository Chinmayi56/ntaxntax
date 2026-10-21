import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Pencil, Trash2, GripVertical, RefreshCw, Search, CalendarDays, UserRound } from "lucide-react";
import api, { describeApiError } from "@/lib/api";
import { useCrud } from "@/hooks/useCrud";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { KpiCard } from "@/components/shared/KpiCard";
import KanbanEmptyState from "@/components/shared/KanbanEmptyState";
import { useKanbanFlash, flashClass } from "@/hooks/useKanbanFlash";
import { toast } from "sonner";

export const KANBAN_COLUMNS = ["TO DO", "IN PROGRESS", "REVIEW", "COMPLETED"];

const MODULE_STATUS = {
  Pending: "TO DO",
  Running: "IN PROGRESS",
  Completed: "COMPLETED",
};

const COLORS = {
  "TO DO": "bg-zinc-100 text-zinc-700",
  "IN PROGRESS": "bg-blue-50 text-blue-700",
  REVIEW: "bg-amber-50 text-amber-700",
  COMPLETED: "bg-emerald-50 text-emerald-700",
};

function recordDate(row) {
  return String(row?.due_date || row?.created_at || "").slice(0, 10);
}

function derivedColumn(row) {
  if (row?.kanban_list_id) return `custom:${row.kanban_list_id}`;
  if (KANBAN_COLUMNS.includes(row?.kanban_column)) return row.kanban_column;
  if (KANBAN_COLUMNS.includes(row?.kanban_status)) return row.kanban_status;
  return MODULE_STATUS[row?.status] || "TO DO";
}

function displayValue(row, keys) {
  for (const key of keys) {
    if (row?.[key] !== undefined && row?.[key] !== null && String(row[key]).trim()) return row[key];
  }
  return "—";
}

function moneyOf(row) {
  for (const key of ["amount", "total", "revenue"]) {
    const raw = row?.[key];
    if (raw === undefined || raw === null || raw === "") continue;
    const n = Number(raw);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

const inrText = (n) => (Number.isFinite(Number(n)) ? `₹${Number(n).toLocaleString("en-IN")}` : "—");

function safeText(v) {
  if (v === undefined || v === null) return "—";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "—";
  if (typeof v === "object") return JSON.stringify(v);
  const t = String(v).trim();
  return !t || t === "undefined" || t === "null" || t === "NaN" ? "—" : t;
}

export function KanbanListDialog({ open, onOpenChange, editing, board, onSaved }) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setName(editing?.name || "");
  }, [open, editing]);

  const save = async () => {
    const value = name.trim();
    if (!value) return toast.error("List name is required");
    setSaving(true);
    try {
      const url = `/admin/kanban/${board}/lists${editing ? `/${editing.id}` : ""}`;
      const response = editing
        ? await api.put(url, { name: value })
        : await api.post(url, { name: value });
      toast.success(editing ? "List renamed" : "List created");
      onSaved?.(response.data?.data);
      onOpenChange(false);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to save list"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-white">
        <DialogHeader>
          <DialogTitle>{editing ? "Rename List" : "Add List"}</DialogTitle>
          <DialogDescription>Custom Kanban lists are saved in MongoDB.</DialogDescription>
        </DialogHeader>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Waiting for Client" onKeyDown={(e) => e.key === "Enter" && save()} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={save} disabled={saving} className="bg-brand text-zinc-900 hover:bg-brand-hover">
            {saving ? "Saving..." : editing ? "Rename" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ModuleKanban({ board, resource, title, customerService, subtitle, searchFields = [], rowFilter = null, onEdit = null, recordsEndpoint = null, detailFields = null, showPeople = false, showHeading = true, showToolbar = true, onOpenRecord = null, refreshKey = 0 }) {
  // showHeading / showToolbar: GST, Income Tax, TDS and ROC hide the "<Title> Kanban" heading and the
  // duplicate Refresh / Add List buttons (the page header already has Refresh + Add). Revenue keeps both.
  // onOpenRecord: when given, clicking a card opens that exact record in the module's own detail view
  // instead of the generic read-only dialog.
  // refreshKey: bumped by the page-level Refresh button so the board reloads with it.
  // recordsEndpoint: board whose cards are server-built from real records (Revenue). Otherwise the
  // generic resource list is used exactly as before.
  const crud = useCrud(resource, { fetchAll: true, enabled: !recordsEndpoint });
  const [remoteRows, setRemoteRows] = useState([]);
  const [remoteLoading, setRemoteLoading] = useState(!!recordsEndpoint);
  const loadRemote = useCallback(async (opts) => {
    if (!recordsEndpoint) return;
    const silent = opts && opts.silent === true;
    if (!silent) setRemoteLoading(true);
    try {
      const { data } = await api.get(recordsEndpoint);
      setRemoteRows(Array.isArray(data?.data) ? data.data : []);
    } catch (err) {
      if (!silent) toast.error(describeApiError(err, "Unable to load Kanban records"));
    } finally {
      if (!silent) setRemoteLoading(false);
    }
  }, [recordsEndpoint]);
  const rows = recordsEndpoint ? remoteRows : crud.rows;
  const loading = recordsEndpoint ? remoteLoading : crud.loading;
  const load = recordsEndpoint ? loadRemote : crud.load;
  const [lists, setLists] = useState([]);
  const [search, setSearch] = useState("");
  const [dragging, setDragging] = useState(null);
  const [overColumn, setOverColumn] = useState(null);
  const [dialog, setDialog] = useState({ open: false, editing: null });
  const [selected, setSelected] = useState(null);

  const loadLists = async () => {
    try {
      const { data } = await api.get(`/admin/kanban/${board}/lists`);
      setLists(Array.isArray(data?.data) ? data.data : []);
    } catch (err) {
      toast.error(describeApiError(err, "Unable to load Kanban lists"));
    }
  };

  useEffect(() => {
    loadLists();
    if (recordsEndpoint) loadRemote();
    const refresh = (event) => {
      if (event?.detail?.source === `kanban:${board}`) return;
      load({ silent: true }); loadLists();
    };
    window.addEventListener("ntaxco:data-changed", refresh);
    return () => window.removeEventListener("ntaxco:data-changed", refresh);
  }, [board, load, recordsEndpoint]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!refreshKey) return;
    load(); loadLists();
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = useMemo(() => {
    const filtered = typeof rowFilter === "function" ? rows.filter(rowFilter) : rows;
    const q = search.trim().toLowerCase();
    if (!q) return filtered;
    return filtered.filter((row) => {
      const values = searchFields.length ? searchFields.map((k) => row[k]) : Object.values(row);
      return values.some((v) => String(v ?? "").toLowerCase().includes(q));
    });
  }, [rows, search, searchFields, rowFilter]);

  const columns = useMemo(() => {
    const standard = KANBAN_COLUMNS.map((name) => ({ id: name, name, standard: true }));
    return [...standard, ...lists.map((l) => ({ id: `custom:${l.id}`, name: l.name, list: l }))];
  }, [lists]);

  // One-shot highlight for REAL new / moved / completed records (never on first load or refresh).
  const flash = useKanbanFlash(rows, derivedColumn, !loading);
  const emptyLabel = (searching) => (searching ? "No matching records" : `No ${title} records in this stage`);
  const counts = useMemo(() => ({
    todo: visible.filter((r) => derivedColumn(r) === "TO DO").length,
    inProgress: visible.filter((r) => derivedColumn(r) === "IN PROGRESS").length,
    review: visible.filter((r) => derivedColumn(r) === "REVIEW").length,
    completed: visible.filter((r) => derivedColumn(r) === "COMPLETED").length,
  }), [visible]);

  const move = async (row, column) => {
    if (!row) return;
    const isCustom = column.id.startsWith("custom:");
    const listId = isCustom ? column.id.slice(7) : null;
    if (derivedColumn(row) === column.id) return;
    try {
      await api.post(`/admin/kanban/${board}/move`, {
        record_id: row.id,
        list_id: listId,
        target: isCustom ? null : column.id,
      });
      await load();
      toast.success(`${title} moved to ${column.name}`);
      window.dispatchEvent(new CustomEvent("ntaxco:data-changed", { detail: { resources: [resource], source: `kanban:${board}`, timestamp: Date.now() } }));
    } catch (err) {
      toast.error(describeApiError(err, "Unable to move record"));
    } finally {
      setDragging(null);
    }
  };

  const deleteList = async (list) => {
    if (!window.confirm(`Delete "${list.name}"? Records in this list will return to their status column.`)) return;
    try {
      await api.delete(`/admin/kanban/${board}/lists/${list.id}`);
      await loadLists();
      await load();
      toast.success("List deleted");
    } catch (err) {
      toast.error(describeApiError(err, "Unable to delete list"));
    }
  };

  return (
    <div className="space-y-5">
      {showHeading || showToolbar ? (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {showHeading && <h2 className="text-lg font-semibold text-zinc-900">{title} Kanban</h2>}
            <p className="text-sm text-zinc-500">{subtitle || `Live ${title.toLowerCase()} records from MongoDB.`}</p>
          </div>
          {showToolbar && (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => { load(); loadLists(); }} disabled={loading}><RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
              <Button onClick={() => setDialog({ open: true, editing: null })} className="bg-brand text-zinc-900 hover:bg-brand-hover"><Plus className="h-4 w-4 mr-1.5" />Add List</Button>
            </div>
          )}
        </div>
      ) : (
        <p className="text-sm text-zinc-500">{subtitle || `Live ${title.toLowerCase()} records from MongoDB.`}</p>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard title="TO DO" value={counts.todo} loading={loading} tone="grey" />
        <KpiCard title="IN PROGRESS" value={counts.inProgress} loading={loading} tone="royal" />
        <KpiCard title="REVIEW" value={counts.review} loading={loading} tone="amber" />
        <KpiCard title="COMPLETED" value={counts.completed} loading={loading} tone="green" />
      </div>

      <div className="relative max-w-xl">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400" />
        <Input className="pl-9" placeholder={`Search ${title.toLowerCase()}...`} value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      <div className="overflow-x-auto pb-3">
        <div className="flex gap-4 min-w-max items-start">
          {columns.map((column) => {
            const columnRows = visible.filter((row) => derivedColumn(row) === column.id);
            return (
              <section key={column.id} className={`kanban-column w-[285px] max-w-[85vw] shrink-0 min-w-0 rounded-xl border bg-zinc-50/80 min-h-[440px] ${dragging && overColumn === column.id ? "kanban-column-over" : "border-zinc-200"}`} onDragOver={(e) => { e.preventDefault(); if (overColumn !== column.id) setOverColumn(column.id); }} onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOverColumn((c) => (c === column.id ? null : c)); }} onDrop={(e) => { e.preventDefault(); setOverColumn(null); move(dragging, column); }} data-testid={`kanban-column-${column.id}`}>
                <div className={`rounded-t-xl px-3 py-3 flex items-center justify-between ${COLORS[column.name] || "bg-white"}`}>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold">{column.name}</h3>
                    <span className="text-xs rounded-full bg-white/80 px-2 py-0.5">{columnRows.length}</span>
                  </div>
                  {column.list && (
                    <div className="flex gap-1">
                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setDialog({ open: true, editing: { ...column.list, board } })}><Pencil className="h-3.5 w-3.5" /></Button>
                      <Button variant="ghost" size="icon" className="h-7 w-7 text-red-600" onClick={() => deleteList(column.list)}><Trash2 className="h-3.5 w-3.5" /></Button>
                    </div>
                  )}
                </div>
                <div className="p-2 space-y-2 min-h-[390px]">
                  {columnRows.map((row) => (
                    <article key={row.id} draggable onDragStart={() => setDragging(row)} onDragEnd={() => { setDragging(null); setOverColumn(null); }} onClick={() => (onOpenRecord ? onOpenRecord(row) : setSelected(row))} className={`kanban-card w-full min-w-0 overflow-hidden bg-white rounded-lg border border-zinc-200 p-3 shadow-sm cursor-grab active:cursor-grabbing hover:border-zinc-300 ${dragging?.id === row.id ? "kanban-card-dragging" : flashClass(flash[String(row.id)])}`} data-testid={`kanban-card-${row.id}`}>
                      <div className="flex gap-2">
                        <GripVertical className="h-4 w-4 text-zinc-300 mt-0.5 shrink-0" />
                        <div className="min-w-0 flex-1">
                          <h4 className="font-medium text-sm text-zinc-900 break-words [overflow-wrap:anywhere]">{displayValue(row, ["client", "company", "customer", "customer_name", "business_name", "owner", "return_type", "form", "return_no"])}</h4>
                          <p className="text-xs text-zinc-500 mt-1 break-words [overflow-wrap:anywhere]">{displayValue(row, ["service", "return_type", "form", "return_no", "gstin", "pan"])}</p>
                          <div className="flex flex-wrap gap-1.5 mt-3">
                            <span className="text-[11px] rounded bg-zinc-50 px-1.5 py-1 text-zinc-600 max-w-full break-words">{safeText(row.payment_status || row.status || "Pending")}</span>
                            {row.priority && <span className="text-[11px] rounded bg-zinc-50 px-1.5 py-1 text-zinc-600 max-w-full break-words">{row.priority}</span>}
                            {moneyOf(row) !== null && <span className="text-[11px] rounded bg-emerald-50 px-1.5 py-1 text-emerald-700 max-w-full break-words">{inrText(moneyOf(row))}</span>}
                          </div>
                          {showPeople && <div className="mt-2 space-y-0.5 text-[11px] text-zinc-500">
                            <div className="truncate">Invoice: <span className="text-zinc-700">{displayValue(row, ["invoice_no", "invoice_id"])}</span></div>
                            <div className="truncate">Agent: <span className="text-zinc-700">{displayValue(row, ["agent_name", "agent"])}</span></div>
                          </div>}
                          <div className="flex items-center justify-between gap-2 mt-3 pt-2 border-t border-zinc-100 text-[11px] text-zinc-500">
                            <span className="flex shrink-0 items-center gap-1"><CalendarDays className="h-3.5 w-3.5" />{(showPeople ? String(row.payment_date || "").slice(0, 10) : recordDate(row)) || "No date"}</span>
                            <span className="truncate min-w-0 max-w-[115px]"><UserRound className="h-3.5 w-3.5 inline mr-1" />{displayValue(row, ["consultant", "employee", "assigned_employee", "employee_name"])}</span>
                          </div>
                          {onEdit && <div className="mt-2 pt-2 border-t border-zinc-100 flex justify-end">
                            <Button variant="outline" size="sm" className="h-7 text-xs" onClick={(e) => { e.stopPropagation(); onEdit(row); }}>Edit</Button>
                          </div>}
                        </div>
                      </div>
                    </article>
                  ))}
                  {!loading && !columnRows.length && <KanbanEmptyState label={emptyLabel(!!search.trim())} hint={search.trim() ? "Try a different search term." : undefined} active={!!dragging && overColumn === column.id} />}
                </div>
              </section>
            );
          })}
        </div>
      </div>

      <Dialog open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="bg-white max-w-xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{title} Details</DialogTitle><DialogDescription>Live record details from MongoDB.</DialogDescription></DialogHeader>
          {selected && (
            <div className="space-y-2">
              {(detailFields
                ? detailFields.map(([k, label, fmt]) => [label, fmt ? fmt(selected[k], selected) : safeText(selected[k])])
                : Object.entries(selected).filter(([k]) => k !== "_id" && !k.endsWith("_ci")).map(([k, v]) => [k.replace(/_/g, " "), safeText(v)])
              ).map(([label, value]) => (
                <div key={label} className="flex justify-between gap-4 border-b border-zinc-100 py-2">
                  <span className="text-xs text-zinc-500">{label}</span>
                  <span className="text-sm text-right max-w-[65%] break-words">{value}</span>
                </div>
              ))}
            {onEdit && selected && <DialogFooter><Button onClick={() => { setSelected(null); onEdit(selected); }}>Edit</Button></DialogFooter>}
            </div>
          )}
        </DialogContent>
      </Dialog>

      <KanbanListDialog board={board} open={dialog.open} onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} editing={dialog.editing} onSaved={() => { loadLists(); }} />
    </div>
  );
}
