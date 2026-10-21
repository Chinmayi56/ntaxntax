import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Plus, MoreVertical, Pencil, Trash2, ArrowLeft, ArrowRight, Search, Filter, RefreshCw, GripVertical, Loader2,
} from "lucide-react";
import api, { describeApiError } from "@/lib/api";
import PageHeader from "@/components/shared/PageHeader";
import KanbanEmptyState from "@/components/shared/KanbanEmptyState";
import { useKanbanFlash, flashClass } from "@/hooks/useKanbanFlash";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/* ------------------------------------------------------------------ *
 * Dynamic module BOARD
 *   Module = Board · List = column · Record = card · Field = card data
 * Uses the module's existing field configuration and the existing
 * /dynamic-records API; list + ordering endpoints live under
 * /dynamic-board/{module_key}.
 * ------------------------------------------------------------------ */

const emptyFrom = (fields) => Object.fromEntries(fields.map((f) => [f.key, ""]));

function fmtValue(field, value) {
  if (value === null || value === undefined || value === "") return "";
  if (field.type === "date") {
    const d = new Date(`${String(value).slice(0, 10)}T00:00:00`);
    if (!Number.isNaN(d.getTime())) return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  }
  return String(value);
}

// Mirrors the backend move logic so the UI can update optimistically.
function applyMove(cards, cardId, listId, position) {
  const moving = cards.find((c) => c.id === cardId);
  if (!moving) return cards;
  const srcList = moving.list_id;
  const byList = (id) => cards.filter((c) => c.list_id === id && c.id !== cardId)
    .sort((a, b) => (a.position || 0) - (b.position || 0));
  const dest = byList(listId);
  const idx = position === null || position === undefined ? dest.length : Math.max(0, Math.min(position, dest.length));
  dest.splice(idx, 0, { ...moving, list_id: listId });
  const positions = new Map();
  dest.forEach((c, i) => positions.set(c.id, i));
  if (srcList && srcList !== listId) {
    byList(srcList).forEach((c, i) => positions.set(c.id, i));
  }
  return cards.map((c) => {
    if (c.id === cardId) return { ...c, list_id: listId, position: positions.get(c.id) };
    return positions.has(c.id) ? { ...c, position: positions.get(c.id) } : c;
  });
}

export default function DynamicBoard({ config, breadcrumb }) {
  const moduleKey = config.key;
  const fields = useMemo(() => (Array.isArray(config.fields) ? config.fields : []), [config.fields]);
  const base = `/dynamic-board/${encodeURIComponent(moduleKey)}`;
  const recBase = `/dynamic-records/${encodeURIComponent(moduleKey)}`;

  const [lists, setLists] = useState([]);
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [filter, setFilter] = useState({ fieldKey: "", value: "" });

  // dialogs
  const [listDialog, setListDialog] = useState(null);      // {mode:'create'|'edit', list?}
  const [listForm, setListForm] = useState({ name: "", description: "" });
  const [deleteList, setDeleteList] = useState(null);      // list
  const [moveTo, setMoveTo] = useState("");
  const [cardDialog, setCardDialog] = useState(null);      // {mode:'create'|'view'|'edit', card?, listId?}
  const [cardForm, setCardForm] = useState({});
  const [cardListId, setCardListId] = useState("");
  const [deleteCard, setDeleteCard] = useState(null);
  const [saving, setSaving] = useState(false);

  // drag state
  const [dragCardId, setDragCardId] = useState(null);
  const [dragListId, setDragListId] = useState(null);
  const [hover, setHover] = useState(null);                // {listId, beforeId}
  const [listHover, setListHover] = useState(null);        // list id that a dragged list would be placed before
  const dragRef = useRef({ cardId: null, listId: null });
  const touchRef = useRef(null);
  const boardRef = useRef(null);
  const cardsRef = useRef(cards);
  cardsRef.current = cards;

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const { data } = await api.get(base);
      setLists(data?.data?.lists || []);
      setCards(data?.data?.cards || []);
    } catch (e) {
      toast.error(describeApiError(e, "Unable to load the board"));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [base]);

  useEffect(() => { load(); }, [load]);

  const titleField = fields[0];
  const summaryFields = fields.slice(1, 4);
  const selectFields = fields.filter((f) => f.type === "select");

  const visible = useCallback((c) => {
    if (filter.fieldKey && filter.value && String(c[filter.fieldKey] ?? "") !== filter.value) return false;
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return fields.some((f) => String(c[f.key] ?? "").toLowerCase().includes(q)) || String(c.id).toLowerCase().includes(q);
  }, [fields, filter, search]);

  const cardListKey = useCallback((c) => c.list_id, []);
  const flash = useKanbanFlash(cards, cardListKey, !loading);
  const cardsFor = useCallback((listId) =>
    cards.filter((c) => c.list_id === listId).sort((a, b) => (a.position || 0) - (b.position || 0)), [cards]);

  /* ---------------------------- lists ---------------------------- */
  const openListDialog = (mode, list) => {
    setListForm({ name: list?.name || "", description: list?.description || "" });
    setListDialog({ mode, list });
  };

  const saveList = async () => {
    const name = listForm.name.trim();
    if (!name) return toast.error("List name is required");
    setSaving(true);
    try {
      if (listDialog.mode === "edit") {
        await api.put(`${base}/lists/${listDialog.list.id}`, { name, description: listForm.description });
        toast.success("List updated");
      } else {
        await api.post(`${base}/lists`, { name, description: listForm.description });
        toast.success("List created");
      }
      setListDialog(null);
      await load(true);
      if (listDialog.mode === "create") {
        requestAnimationFrame(() => boardRef.current?.scrollTo({ left: boardRef.current.scrollWidth, behavior: "smooth" }));
      }
    } catch (e) {
      toast.error(describeApiError(e, "Unable to save list"));
    } finally { setSaving(false); }
  };

  const persistListOrder = async (orderedIds, previous) => {
    setLists((ls) => orderedIds.map((id, i) => ({ ...ls.find((l) => l.id === id), position: i })));
    try {
      await api.put(`${base}/lists-order`, { list_ids: orderedIds });
    } catch (e) {
      setLists(previous);
      toast.error(describeApiError(e, "Unable to reorder lists — restored the previous order"));
    }
  };

  const shiftList = (list, dir) => {
    const ids = lists.map((l) => l.id);
    const i = ids.indexOf(list.id);
    const j = i + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    persistListOrder(ids, lists);
  };

  const requestDeleteList = (list) => {
    setMoveTo(lists.find((l) => l.id !== list.id)?.id || "");
    setDeleteList(list);
  };

  const confirmDeleteList = async () => {
    const list = deleteList;
    const count = cardsFor(list.id).length;
    try {
      await api.delete(`${base}/lists/${list.id}`, { params: count ? { move_to: moveTo } : undefined });
      toast.success(count ? `List deleted — ${count} card(s) moved` : "List deleted");
      setDeleteList(null);
      await load(true);
    } catch (e) {
      toast.error(describeApiError(e, "Unable to delete list"));
    }
  };

  /* ---------------------------- cards ---------------------------- */
  const openCreate = (listId) => {
    if (fields.length === 0) return toast.error("Configure at least one field for this module in Settings first.");
    setCardForm(emptyFrom(fields));
    setCardListId(listId);
    setCardDialog({ mode: "create", listId });
  };
  const openView = (card) => { setCardListId(card.list_id); setCardDialog({ mode: "view", card }); };
  const openEdit = (card) => {
    const f = emptyFrom(fields);
    fields.forEach((fl) => { f[fl.key] = card[fl.key] ?? ""; });
    setCardForm(f);
    setCardListId(card.list_id);
    setCardDialog({ mode: "edit", card });
  };

  const saveCard = async () => {
    for (const f of fields) {
      if (f.required && !String(cardForm[f.key] ?? "").trim()) return toast.error(`${f.label} is required`);
    }
    setSaving(true);
    try {
      if (cardDialog.mode === "create") {
        await api.post(recBase, { ...cardForm, list_id: cardListId });
        toast.success("Card created");
      } else {
        const card = cardDialog.card;
        await api.put(`${recBase}/${card.id}`, cardForm);
        if (cardListId && cardListId !== card.list_id) {
          await api.put(`${base}/cards/${card.id}/move`, { list_id: cardListId });
        }
        toast.success("Card updated");
      }
      setCardDialog(null);
      await load(true);
    } catch (e) {
      toast.error(describeApiError(e, "Unable to save card"));
    } finally { setSaving(false); }
  };

  const confirmDeleteCard = async () => {
    try {
      await api.delete(`${recBase}/${deleteCard.id}`);
      toast.success("Card deleted");
      setDeleteCard(null);
      setCardDialog(null);
      await load(true);
    } catch (e) {
      toast.error(describeApiError(e, "Unable to delete card"));
    }
  };

  /* ------------------------ drag and drop ------------------------ */
  // The card dropped *before* which other card? Works from the pointer's Y.
  const beforeIdAt = (listId, clientY, excludeId) => {
    const col = boardRef.current?.querySelector(`[data-cards-of="${CSS.escape(listId)}"]`);
    if (!col) return null;
    const els = [...col.querySelectorAll("[data-card-id]")].filter((el) => el.dataset.cardId !== excludeId);
    for (const el of els) {
      const r = el.getBoundingClientRect();
      if (clientY < r.top + r.height / 2) return el.dataset.cardId;
    }
    return null;
  };

  const dropCard = async (cardId, listId, beforeId) => {
    const prev = cardsRef.current;
    const moving = prev.find((c) => c.id === cardId);
    if (!moving) return;
    const dest = prev.filter((c) => c.list_id === listId && c.id !== cardId).sort((a, b) => (a.position || 0) - (b.position || 0));
    let position;
    if (beforeId) position = dest.findIndex((c) => c.id === beforeId);
    else {
      const lastVisible = [...dest].reverse().find(visible);
      position = lastVisible ? dest.findIndex((c) => c.id === lastVisible.id) + 1 : dest.length;
    }
    if (position < 0) position = dest.length;
    const currentIdx = prev.filter((c) => c.list_id === listId).sort((a, b) => (a.position || 0) - (b.position || 0)).findIndex((c) => c.id === cardId);
    if (moving.list_id === listId && currentIdx === position) return; // nothing changed
    setCards(applyMove(prev, cardId, listId, position));
    try {
      await api.put(`${base}/cards/${encodeURIComponent(cardId)}/move`, { list_id: listId, position });
    } catch (e) {
      setCards(prev); // restore the card to where it was
      toast.error(describeApiError(e, "Couldn't save the move — the card was returned to its previous list"));
    }
  };

  const clearDrag = () => { dragRef.current = { cardId: null, listId: null }; setDragCardId(null); setDragListId(null); setHover(null); setListHover(null); };

  // --- native (mouse / pen) card drag
  const onCardDragStart = (e, card) => {
    dragRef.current = { cardId: card.id, listId: null };
    setDragCardId(card.id);
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", card.id); } catch { /* some browsers */ }
  };
  const onListBodyDragOver = (e, listId) => {
    if (!dragRef.current.cardId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const beforeId = beforeIdAt(listId, e.clientY, dragRef.current.cardId);
    setHover((h) => (h?.listId === listId && h?.beforeId === beforeId ? h : { listId, beforeId }));
  };
  const onListBodyDrop = (e, listId) => {
    const cardId = dragRef.current.cardId;
    if (!cardId) return;
    e.preventDefault();
    const beforeId = beforeIdAt(listId, e.clientY, cardId);
    clearDrag();
    dropCard(cardId, listId, beforeId);
  };

  // --- native list drag (grab the header grip)
  const onListDragStart = (e, list) => {
    dragRef.current = { cardId: null, listId: list.id };
    setDragListId(list.id);
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", list.id); } catch { /* ignore */ }
  };
  const listBeforeAt = (clientX, excludeId) => {
    const els = [...(boardRef.current?.querySelectorAll("[data-list-id]") || [])].filter((el) => el.dataset.listId !== excludeId);
    for (const el of els) {
      const r = el.getBoundingClientRect();
      if (clientX < r.left + r.width / 2) return el.dataset.listId;
    }
    return null;
  };
  const onBoardDragOver = (e) => {
    const lid = dragRef.current.listId;
    if (!lid) return;
    e.preventDefault();
    setListHover(listBeforeAt(e.clientX, lid) || "__end__");
    // auto-scroll near the edges
    const el = boardRef.current; if (!el) return;
    const r = el.getBoundingClientRect();
    if (e.clientX < r.left + 60) el.scrollLeft -= 20; else if (e.clientX > r.right - 60) el.scrollLeft += 20;
  };
  const onBoardDrop = (e) => {
    const lid = dragRef.current.listId;
    if (!lid) return;
    e.preventDefault();
    const before = listBeforeAt(e.clientX, lid);
    clearDrag();
    const ids = lists.map((l) => l.id).filter((i) => i !== lid);
    const at = before ? ids.indexOf(before) : ids.length;
    ids.splice(at, 0, lid);
    if (ids.join() !== lists.map((l) => l.id).join()) persistListOrder(ids, lists);
  };

  // --- touch fallback (HTML5 drag events don't fire on touch screens): long-press a card, then drag
  const onCardTouchStart = (e, card) => {
    if (e.touches.length !== 1) return;
    const t = e.touches[0];
    const st = { cardId: card.id, active: false, startX: t.clientX, startY: t.clientY, x: t.clientX, y: t.clientY, ghost: null, timer: null };
    st.timer = setTimeout(() => {
      st.active = true;
      setDragCardId(card.id);
      const src = e.target.closest("[data-card-id]");
      if (src) {
        const g = src.cloneNode(true);
        const r = src.getBoundingClientRect();
        Object.assign(g.style, { position: "fixed", left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, opacity: "0.85", pointerEvents: "none", zIndex: 9999, transform: "rotate(2deg)" });
        document.body.appendChild(g);
        st.ghost = g; st.gx = st.x - r.left; st.gy = st.y - r.top;
      }
      if (navigator.vibrate) navigator.vibrate(15);
    }, 280);
    touchRef.current = st;
  };

  useEffect(() => {
    const move = (e) => {
      const st = touchRef.current; if (!st) return;
      const t = e.touches[0];
      if (!st.active) {
        if (Math.hypot(t.clientX - st.startX, t.clientY - st.startY) > 8) { clearTimeout(st.timer); touchRef.current = null; }
        return;
      }
      e.preventDefault();
      st.x = t.clientX; st.y = t.clientY;
      if (st.ghost) { st.ghost.style.left = `${t.clientX - st.gx}px`; st.ghost.style.top = `${t.clientY - st.gy}px`; }
      const el = document.elementFromPoint(t.clientX, t.clientY)?.closest("[data-list-id]");
      if (el) {
        const listId = el.dataset.listId;
        const beforeId = beforeIdAt(listId, t.clientY, st.cardId);
        setHover((h) => (h?.listId === listId && h?.beforeId === beforeId ? h : { listId, beforeId }));
      }
      const b = boardRef.current;
      if (b) { const r = b.getBoundingClientRect(); if (t.clientX < r.left + 40) b.scrollLeft -= 14; else if (t.clientX > r.right - 40) b.scrollLeft += 14; }
    };
    const end = () => {
      const st = touchRef.current; if (!st) return;
      clearTimeout(st.timer);
      st.ghost?.remove();
      touchRef.current = null;
      if (!st.active) return;
      const el = document.elementFromPoint(st.x, st.y)?.closest("[data-list-id]");
      const cardId = st.cardId;
      const listId = el?.dataset.listId;
      const beforeId = listId ? beforeIdAt(listId, st.y, cardId) : null;
      clearDrag();
      if (listId) dropCard(cardId, listId, beforeId);
    };
    document.addEventListener("touchmove", move, { passive: false });
    document.addEventListener("touchend", end);
    document.addEventListener("touchcancel", end);
    return () => {
      document.removeEventListener("touchmove", move);
      document.removeEventListener("touchend", end);
      document.removeEventListener("touchcancel", end);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lists, cards, search, filter]);

  /* ----------------------------- UI ------------------------------ */
  const renderInput = (f) => {
    const v = cardForm[f.key] ?? "";
    const set = (val) => setCardForm((s) => ({ ...s, [f.key]: val }));
    if (f.type === "textarea") return <Textarea className="mt-1.5 border-zinc-300" rows={3} value={v} onChange={(e) => set(e.target.value)} data-testid={`board-field-${f.key}`} />;
    if (f.type === "select") {
      return (
        <Select value={String(v)} onValueChange={set}>
          <SelectTrigger className="mt-1.5 border-zinc-300" data-testid={`board-field-${f.key}`}><SelectValue placeholder={`Select ${f.label}`} /></SelectTrigger>
          <SelectContent className="bg-white max-h-64">
            {(f.options || []).map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
          </SelectContent>
        </Select>
      );
    }
    return <Input className="mt-1.5 border-zinc-300" type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"} value={v} onChange={(e) => set(e.target.value)} data-testid={`board-field-${f.key}`} />;
  };

  const listNameOf = (id) => lists.find((l) => l.id === id)?.name || "—";
  const filterActive = !!(filter.fieldKey && filter.value);
  const totalVisible = cards.filter(visible).length;

  return (
    <div data-testid="dynamic-board">
      <PageHeader
        title={config.name}
        breadcrumb={breadcrumb}
        subtitle={config.description || `Drag cards between lists to manage ${config.name.toLowerCase()} workflow.`}
        actions={<>
          <Button variant="outline" className="border-zinc-300" onClick={() => load()} disabled={loading} data-testid="board-refresh">
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />Refresh
          </Button>
          <Button className="bg-brand text-zinc-900 hover:bg-brand-hover font-semibold" onClick={() => openListDialog("create")} data-testid="board-add-list">
            <Plus className="h-4 w-4 mr-1.5" />Add List
          </Button>
        </>}
      />

      {/* search + filter */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative w-full sm:w-72">
          <Search className="h-4 w-4 text-zinc-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <Input className="pl-9 border-zinc-300 bg-white" placeholder="Search cards" value={search} onChange={(e) => setSearch(e.target.value)} data-testid="board-search" />
        </div>
        <Button variant="outline" className={`border-zinc-300 ${filterActive ? "bg-amber-50 border-amber-300" : ""}`} onClick={() => setFilterOpen((o) => !o)} data-testid="board-filter-btn">
          <Filter className="h-4 w-4 mr-1.5" />Filter{filterActive ? " (1)" : ""}
        </Button>
        {(search || filterActive) && (
          <>
            <span className="text-xs text-zinc-500">{totalVisible} of {cards.length} cards</span>
            <Button variant="ghost" size="sm" onClick={() => { setSearch(""); setFilter({ fieldKey: "", value: "" }); }}>Clear</Button>
          </>
        )}
      </div>
      {filterOpen && (
        <div className="mb-4 bg-white border border-zinc-200 rounded-xl p-3 flex flex-wrap items-end gap-3" data-testid="board-filter-panel">
          {selectFields.length === 0 ? (
            <p className="text-sm text-zinc-500">Filters use Dropdown fields. Add a Dropdown field to this module in Settings to filter by it.</p>
          ) : (
            <>
              <div className="w-52">
                <Label className="text-xs text-zinc-500">Field</Label>
                <Select value={filter.fieldKey} onValueChange={(v) => setFilter({ fieldKey: v, value: "" })}>
                  <SelectTrigger className="mt-1 border-zinc-300"><SelectValue placeholder="Choose field" /></SelectTrigger>
                  <SelectContent className="bg-white">{selectFields.map((f) => <SelectItem key={f.key} value={f.key}>{f.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="w-52">
                <Label className="text-xs text-zinc-500">Value</Label>
                <Select value={filter.value} onValueChange={(v) => setFilter((s) => ({ ...s, value: v }))} disabled={!filter.fieldKey}>
                  <SelectTrigger className="mt-1 border-zinc-300"><SelectValue placeholder="Choose value" /></SelectTrigger>
                  <SelectContent className="bg-white">
                    {(selectFields.find((f) => f.key === filter.fieldKey)?.options || []).map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setFilter({ fieldKey: "", value: "" })}>Reset</Button>
            </>
          )}
        </div>
      )}

      {fields.length === 0 && (
        <div className="mb-4 text-sm rounded-lg border border-amber-200 bg-amber-50 text-amber-800 px-3 py-2">
          This module has no fields yet. Add fields in Settings → Modules / Dynamic Configuration (Edit) to define the data shown on each card.
        </div>
      )}

      {/* board */}
      {loading ? (
        <div className="flex items-center justify-center py-24 text-zinc-400"><Loader2 className="h-6 w-6 animate-spin mr-2" />Loading board…</div>
      ) : (
        <div
          ref={boardRef}
          className="flex gap-4 overflow-x-auto pb-4 items-start snap-x snap-proximity"
          style={{ minHeight: "60vh" }}
          onDragOver={onBoardDragOver}
          onDrop={onBoardDrop}
          data-testid="board-scroller"
        >
          {lists.map((list, li) => {
            const listCards = cardsFor(list.id);
            const shown = listCards.filter(visible);
            const isDropTarget = hover?.listId === list.id && !!dragCardId;
            return (
              <div key={list.id} className="flex items-start gap-4 shrink-0">
                {listHover === list.id && dragListId && <div className="w-1 self-stretch rounded bg-amber-400" />}
                <section
                  data-list-id={list.id}
                  className={`w-[290px] max-w-[85vw] shrink-0 snap-start rounded-xl border bg-zinc-100/90 flex flex-col max-h-[calc(100vh-290px)] min-h-[160px] transition-[box-shadow,border-color] duration-200 ${
                    isDropTarget ? "border-amber-400 ring-2 ring-amber-200" : "border-zinc-200"} ${dragListId === list.id ? "opacity-50" : ""}`}
                  onDragOver={(e) => onListBodyDragOver(e, list.id)}
                  onDrop={(e) => onListBodyDrop(e, list.id)}
                  data-testid={`board-list-${list.id}`}
                >
                  <header className="flex items-center gap-1 px-2 pt-2.5 pb-1.5">
                    <span
                      draggable
                      onDragStart={(e) => onListDragStart(e, list)}
                      onDragEnd={clearDrag}
                      className="cursor-grab active:cursor-grabbing p-1 text-zinc-400 hover:text-zinc-600"
                      title="Drag to reorder list"
                    ><GripVertical className="h-4 w-4" /></span>
                    <div className="min-w-0 flex-1">
                      <h3 className="font-semibold text-sm text-zinc-900 truncate" title={list.description || list.name}>{list.name}</h3>
                      <p className="text-xs text-zinc-500">{listCards.length} {listCards.length === 1 ? "Card" : "Cards"}</p>
                    </div>
                    <DropdownMenu modal={false}>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="h-8 w-8" data-testid={`list-menu-${list.id}`}><MoreVertical className="h-4 w-4 text-zinc-500" /></Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="bg-white">
                        <DropdownMenuItem onSelect={() => openListDialog("edit", list)}><Pencil className="h-4 w-4 mr-2" />Edit / Rename</DropdownMenuItem>
                        <DropdownMenuItem disabled={li === 0} onSelect={() => shiftList(list, -1)}><ArrowLeft className="h-4 w-4 mr-2" />Move Left</DropdownMenuItem>
                        <DropdownMenuItem disabled={li === lists.length - 1} onSelect={() => shiftList(list, 1)}><ArrowRight className="h-4 w-4 mr-2" />Move Right</DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem className="text-red-600" disabled={lists.length <= 1} onSelect={() => requestDeleteList(list)}><Trash2 className="h-4 w-4 mr-2" />Delete</DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </header>

                  <div className="flex-1 overflow-y-auto px-2 pb-1 space-y-2 min-h-[40px]" data-cards-of={list.id}>
                    {shown.map((card) => (
                      <div key={card.id}>
                        {isDropTarget && hover.beforeId === card.id && dragCardId !== card.id && <div className="h-1.5 rounded bg-amber-400 mb-2" />}
                        <article
                          data-card-id={card.id}
                          draggable
                          onDragStart={(e) => onCardDragStart(e, card)}
                          onDragEnd={clearDrag}
                          onTouchStart={(e) => onCardTouchStart(e, card)}
                          onClick={() => openView(card)}
                          className={`bg-white rounded-lg border border-zinc-200 p-3 shadow-sm cursor-grab active:cursor-grabbing hover:border-zinc-300 kanban-card ${dragCardId === card.id ? "kanban-card-dragging" : flashClass(flash[String(card.id)])}`}
                          data-testid={`board-card-${card.id}`}
                        >
                          <p className="font-semibold text-sm text-zinc-900 break-words">{titleField ? (fmtValue(titleField, card[titleField.key]) || "Untitled") : card.id}</p>
                          {summaryFields.map((f) => {
                            const v = fmtValue(f, card[f.key]);
                            if (!v) return null;
                            return <p key={f.key} className="text-xs text-zinc-600 mt-0.5 truncate" title={`${f.label}: ${v}`}>{v}</p>;
                          })}
                        </article>
                      </div>
                    ))}
                    {isDropTarget && !hover.beforeId && shown.length > 0 && <div className="h-1.5 rounded bg-amber-400" />}
                    {shown.length === 0 && (listCards.length
                      ? <p className="text-xs text-zinc-400 text-center py-4">No cards match</p>
                      : <KanbanEmptyState label="No cards in this list" active={isDropTarget} />)}
                  </div>

                  <button
                    type="button"
                    onClick={() => openCreate(list.id)}
                    className="m-2 mt-1 flex items-center gap-1.5 text-sm text-zinc-600 hover:bg-zinc-200/70 rounded-lg px-2 py-2 text-left"
                    data-testid={`board-add-card-${list.id}`}
                  ><Plus className="h-4 w-4" />Add Card</button>
                </section>
              </div>
            );
          })}
          {listHover === "__end__" && dragListId && <div className="w-1 self-stretch rounded bg-amber-400" />}

          <button
            type="button"
            onClick={() => openListDialog("create")}
            className="w-[290px] max-w-[85vw] shrink-0 rounded-xl border-2 border-dashed border-zinc-300 text-zinc-500 hover:bg-zinc-50 hover:border-zinc-400 py-3 px-4 flex items-center gap-2 text-sm font-medium"
          ><Plus className="h-4 w-4" />Add another list</button>
        </div>
      )}

      {/* list create/edit */}
      <Dialog open={!!listDialog} onOpenChange={(o) => !o && setListDialog(null)}>
        <DialogContent className="bg-white sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{listDialog?.mode === "edit" ? "Edit List" : "Create New List"}</DialogTitle>
            <DialogDescription>Lists are the columns of this board, e.g. “New”, “In Progress”, “Completed”.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>List Name</Label>
              <Input className="mt-1.5 border-zinc-300" autoFocus value={listForm.name} onChange={(e) => setListForm({ ...listForm, name: e.target.value })}
                onKeyDown={(e) => e.key === "Enter" && saveList()} placeholder="e.g. Documents Pending" data-testid="list-name-input" />
            </div>
            <div>
              <Label>Description</Label>
              <Textarea className="mt-1.5 border-zinc-300" rows={3} value={listForm.description} onChange={(e) => setListForm({ ...listForm, description: e.target.value })} placeholder="Optional" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setListDialog(null)}>Cancel</Button>
            <Button className="bg-brand text-zinc-900 hover:bg-brand-hover font-semibold" onClick={saveList} disabled={saving} data-testid="list-save">
              {listDialog?.mode === "edit" ? "Save List" : "Create List"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* list delete */}
      <AlertDialog open={!!deleteList} onOpenChange={(o) => !o && setDeleteList(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete list “{deleteList?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteList && cardsFor(deleteList.id).length > 0
                ? `This list has ${cardsFor(deleteList.id).length} card(s). No cards will be deleted — choose the list they should move to.`
                : "This list is empty and will be removed from the board."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteList && cardsFor(deleteList.id).length > 0 && (
            <Select value={moveTo} onValueChange={setMoveTo}>
              <SelectTrigger className="border-zinc-300" data-testid="delete-list-moveto"><SelectValue placeholder="Move cards to…" /></SelectTrigger>
              <SelectContent className="bg-white">
                {lists.filter((l) => l.id !== deleteList.id).map((l) => <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>)}
              </SelectContent>
            </Select>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction className="bg-red-600 hover:bg-red-700 text-white" onClick={confirmDeleteList}
              disabled={!!deleteList && cardsFor(deleteList.id).length > 0 && !moveTo} data-testid="confirm-delete-list">Delete List</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* card create / view / edit */}
      <Dialog open={!!cardDialog} onOpenChange={(o) => !o && setCardDialog(null)}>
        <DialogContent className="bg-white sm:max-w-xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {cardDialog?.mode === "create" ? `Create ${config.name} Card` : cardDialog?.mode === "edit" ? `Edit ${config.name} Card` : `${config.name} Details`}
            </DialogTitle>
            <DialogDescription>
              {cardDialog?.mode === "view" ? `Record ${cardDialog.card?.id}` : "Fill in the fields configured for this module."}
            </DialogDescription>
          </DialogHeader>

          {cardDialog?.mode === "view" ? (
            <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
              {fields.map((f) => (
                <div key={f.key} className={f.type === "textarea" ? "sm:col-span-2" : ""}>
                  <dt className="text-xs text-zinc-500">{f.label}</dt>
                  <dd className="text-zinc-900 whitespace-pre-wrap break-words">{fmtValue(f, cardDialog.card[f.key]) || "—"}</dd>
                </div>
              ))}
              <div className="sm:col-span-2 border-t border-zinc-100 pt-3">
                <dt className="text-xs text-zinc-500">Current List</dt>
                <dd className="font-medium text-zinc-900">{listNameOf(cardDialog.card.list_id)}</dd>
              </div>
            </dl>
          ) : (
            <div className="grid sm:grid-cols-2 gap-4">
              {fields.map((f) => (
                <div key={f.key} className={f.type === "textarea" ? "sm:col-span-2" : ""}>
                  <Label>{f.label}{f.required && <span className="text-red-500"> *</span>}</Label>
                  {renderInput(f)}
                </div>
              ))}
              <div className="sm:col-span-2">
                <Label>List</Label>
                <Select value={cardListId} onValueChange={setCardListId}>
                  <SelectTrigger className="mt-1.5 border-zinc-300" data-testid="card-list-select"><SelectValue /></SelectTrigger>
                  <SelectContent className="bg-white">{lists.map((l) => <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
          )}

          <DialogFooter className="gap-2">
            {cardDialog?.mode === "view" ? (
              <>
                <Button variant="outline" className="text-red-600 border-red-200" onClick={() => setDeleteCard(cardDialog.card)} data-testid="card-delete"><Trash2 className="h-4 w-4 mr-1.5" />Delete</Button>
                <Button variant="outline" onClick={() => setCardDialog(null)}>Close</Button>
                <Button className="bg-brand text-zinc-900 hover:bg-brand-hover font-semibold" onClick={() => openEdit(cardDialog.card)} data-testid="card-edit"><Pencil className="h-4 w-4 mr-1.5" />Edit</Button>
              </>
            ) : (
              <>
                <Button variant="outline" onClick={() => setCardDialog(null)}>Cancel</Button>
                <Button className="bg-brand text-zinc-900 hover:bg-brand-hover font-semibold" onClick={saveCard} disabled={saving} data-testid="card-save">
                  {cardDialog?.mode === "create" ? "Create Card" : "Save Changes"}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteCard} onOpenChange={(o) => !o && setDeleteCard(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this card?</AlertDialogTitle>
            <AlertDialogDescription>This permanently deletes the record {deleteCard?.id}. This can't be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction className="bg-red-600 hover:bg-red-700 text-white" onClick={confirmDeleteCard} data-testid="confirm-delete-card">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
