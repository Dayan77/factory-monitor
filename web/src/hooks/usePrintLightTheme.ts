import { useEffect } from "react";

/** Prints in the light theme (ink-friendly) regardless of the screen theme, then restores it. */
export function usePrintLightTheme() {
  useEffect(() => {
    let previous: string | null = null;
    const before = () => {
      previous = document.documentElement.getAttribute("data-theme");
      document.documentElement.setAttribute("data-theme", "light");
    };
    const after = () => {
      if (previous) document.documentElement.setAttribute("data-theme", previous);
      previous = null;
    };
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);
}
