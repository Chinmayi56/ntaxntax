import { Inbox } from "lucide-react";

/**
 * Empty-state shown inside a Kanban column that has ZERO real records.
 * It is purely presentational: the parent column <section> keeps its own
 * onDragOver/onDrop handlers, so an empty column stays a valid drop target
 * (drag events from this element bubble up to the column).
 *
 * `active` highlights the state while a card is dragged over the column.
 */
export default function KanbanEmptyState({ label = "No records in this stage", hint = "Drag a card here to move it to this stage.", icon: Icon = Inbox, active = false, testId = "kanban-empty-state" }) {
  return (
    <div
      className={`kanban-empty-enter flex flex-col items-center justify-center text-center rounded-lg border border-dashed px-4 py-10 transition-colors duration-200 ${active ? "border-amber-400 bg-amber-50/60" : "border-zinc-200 bg-white/50"}`}
      data-testid={testId}
      role="status"
    >
      <div className={`h-11 w-11 rounded-full flex items-center justify-center mb-3 transition-colors duration-200 ${active ? "bg-amber-100" : "bg-zinc-100"}`}>
        <Icon className={`h-5 w-5 ${active ? "text-amber-600" : "text-zinc-400"}`} aria-hidden="true" />
      </div>
      <p className="text-sm font-medium text-zinc-600">{label}</p>
      {hint && <p className="mt-1 text-xs text-zinc-400 max-w-[200px]">{hint}</p>}
    </div>
  );
}
