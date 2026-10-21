import { useEffect, useRef, useState } from "react";

/**
 * Detects REAL changes in a Kanban data set so cards can animate once:
 *   - a record that was not there before        -> "new"
 *   - a record whose column changed             -> "moved"
 *   - a record that moved into a COMPLETED stage-> "completed"
 *
 * The first loaded data set only seeds the baseline, so a page load or refresh
 * never animates every card. Nothing here fabricates data; it only observes it.
 *
 * items    : full (unfiltered) record array, so search filtering is not mistaken for a change
 * columnOf : (item) => column key
 * ready    : false while the first load is in flight
 */
export function useKanbanFlash(items, columnOf, ready = true) {
  const known = useRef(null);
  const columnOfRef = useRef(columnOf);
  const timers = useRef([]);
  const [flash, setFlash] = useState({});
  columnOfRef.current = columnOf;

  useEffect(() => {
    if (!ready || !Array.isArray(items)) return;
    const next = new Map(items.map((item) => [String(item.id), String(columnOfRef.current(item))]));
    if (known.current === null) {
      known.current = next;
      return;
    }
    const changes = {};
    next.forEach((column, id) => {
      if (!known.current.has(id)) changes[id] = "new";
      else if (known.current.get(id) !== column) changes[id] = /complete/i.test(column) ? "completed" : "moved";
    });
    known.current = next;
    const ids = Object.keys(changes);
    if (!ids.length) return;
    setFlash((current) => ({ ...current, ...changes }));
    const timer = setTimeout(() => {
      setFlash((current) => {
        const copy = { ...current };
        ids.forEach((id) => delete copy[id]);
        return copy;
      });
    }, 1600);
    timers.current.push(timer);
  }, [items, ready]);

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  return flash;
}

/** CSS class for a card given its flash kind (see the motion section in index.css). */
export const flashClass = (kind) =>
  kind === "completed" ? "kanban-card-complete" : kind === "new" ? "kanban-card-new" : kind === "moved" ? "kanban-card-moved" : "";
