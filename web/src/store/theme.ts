import { create } from "zustand";

/**
 * Theme preference. "system" follows prefers-color-scheme. The resolved theme is written to
 * <html data-theme>, which switches every CSS variable in src/styles/tokens.css.
 * public/theme-init.js applies the same logic before first paint (same storage key).
 */
export type ThemePref = "light" | "dark" | "system";
export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "fm-theme";

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function systemTheme(): Theme {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolveTheme(pref: ThemePref, system: Theme = systemTheme()): Theme {
  return pref === "system" ? system : pref;
}

function apply(theme: Theme) {
  if (typeof document !== "undefined") document.documentElement.setAttribute("data-theme", theme);
}

interface ThemeState {
  pref: ThemePref;
  theme: Theme;
  setPref: (p: ThemePref) => void;
  /** Flips between light and dark (stores an explicit preference). */
  toggle: () => void;
  /** Re-evaluates "system" (called when the OS preference changes). */
  syncSystem: () => void;
}

export const useThemeStore = create<ThemeState>((set, get) => {
  const pref = readPref();
  const theme = resolveTheme(pref);
  apply(theme);
  return {
    pref,
    theme,
    setPref: (p) => {
      try {
        if (p === "system") localStorage.removeItem(THEME_STORAGE_KEY);
        else localStorage.setItem(THEME_STORAGE_KEY, p);
      } catch {
        /* storage blocked: preference lasts for this session only */
      }
      const t = resolveTheme(p);
      apply(t);
      set({ pref: p, theme: t });
    },
    toggle: () => get().setPref(get().theme === "dark" ? "light" : "dark"),
    syncSystem: () => {
      if (get().pref !== "system") return;
      const t = systemTheme();
      apply(t);
      set({ theme: t });
    },
  };
});

let listening = false;
/** Subscribes once to OS theme changes (called from main.tsx). */
export function watchSystemTheme() {
  if (listening || typeof window === "undefined" || !window.matchMedia) return;
  listening = true;
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => useThemeStore.getState().syncSystem());
}
