import { describe, expect, it } from "vitest";
import { resolveTheme, THEME_STORAGE_KEY, useThemeStore } from "./theme";

describe("theme store", () => {
  it("resolves system preference", () => {
    expect(resolveTheme("system", "light")).toBe("light");
    expect(resolveTheme("system", "dark")).toBe("dark");
    expect(resolveTheme("light", "dark")).toBe("light");
  });

  it("toggle writes <html data-theme> and persists the explicit choice", () => {
    useThemeStore.getState().setPref("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    useThemeStore.getState().toggle();
    expect(useThemeStore.getState().theme).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    useThemeStore.getState().setPref("system");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });
});
