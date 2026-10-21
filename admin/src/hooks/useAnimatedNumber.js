import { useEffect, useRef, useState } from "react";

const prefersReducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Smoothly tweens a REAL numeric value when it changes. The first value renders
 * immediately (no count-up on page load); only later backend changes animate.
 * Non-numeric values (formatted strings) are returned untouched.
 */
export function useAnimatedNumber(value, duration = 450) {
  const numeric = typeof value === "number" && Number.isFinite(value);
  const [display, setDisplay] = useState(numeric ? value : 0);
  const shown = useRef(numeric ? value : 0);
  const frame = useRef(null);

  useEffect(() => {
    if (!numeric) return undefined;
    const from = shown.current;
    if (from === value || prefersReducedMotion()) {
      shown.current = value;
      setDisplay(value);
      return undefined;
    }
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const current = Math.round(from + (value - from) * eased);
      shown.current = current;
      setDisplay(current);
      if (t < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => frame.current && cancelAnimationFrame(frame.current);
  }, [value, numeric, duration]);

  return numeric ? display : value;
}
