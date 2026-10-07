import { useEffect, useRef, useState } from "react";

/**
 * A horizontally scrollable box (wide table on a phone) must be reachable by keyboard to be scrolled (WCAG 2.1.1,
 * axe "scrollable-region-focusable"): it becomes a tab stop only while its content actually overflows.
 */
export function useScrollFocusable<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [overflows, setOverflows] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setOverflows(el.scrollWidth > el.clientWidth + 1);
    check();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(check);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, []);
  return { ref, overflows };
}
