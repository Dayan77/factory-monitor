import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "axios";
import { api, refreshTokens, SESSION_EXPIRED_KEY, sessionExpired } from "./api";

describe("token refresh (rotation) and session expiry", () => {
  const replace = vi.fn();
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    replace.mockReset();
    vi.stubGlobal("location", { ...window.location, replace, pathname: "/maintenance/kpis", search: "?x=1" });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("rotates once and stores the new pair", async () => {
    localStorage.setItem("refresh_token", "r0");
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { access_token: "a1", refresh_token: "r1" } });
    await expect(refreshTokens("r0")).resolves.toBe("a1");
    expect(post).toHaveBeenCalledWith("/api/auth/refresh", { refresh_token: "r0" });
    expect(localStorage.getItem("refresh_token")).toBe("r1");
  });

  it("another tab already rotated: adopts its tokens instead of reusing the old refresh token (reuse = theft)", async () => {
    localStorage.setItem("refresh_token", "r-new");
    localStorage.setItem("access_token", "a-new");
    const post = vi.spyOn(axios, "post");
    await expect(refreshTokens("r-old")).resolves.toBe("a-new");
    expect(post).not.toHaveBeenCalled();
  });

  it("concurrent 401s share one refresh, then replay the requests", async () => {
    localStorage.setItem("access_token", "a0");
    localStorage.setItem("refresh_token", "r0");
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { access_token: "a1", refresh_token: "r1" } });
    const seen: string[] = [];
    api.defaults.adapter = async (config) => {
      const auth = String(config.headers?.Authorization ?? "");
      seen.push(auth);
      if (auth === "Bearer a0") {
        const err = Object.assign(new Error("401"), { isAxiosError: true, config, response: { status: 401, data: {}, headers: {}, config, statusText: "" } });
        throw err;
      }
      return { data: { ok: true }, status: 200, statusText: "OK", headers: {}, config };
    };
    const [x, y] = await Promise.all([api.get("/api/a"), api.get("/api/b")]);
    expect(x.data).toEqual({ ok: true });
    expect(y.data).toEqual({ ok: true });
    expect(post).toHaveBeenCalledTimes(1);
    expect(seen.filter((s) => s === "Bearer a1")).toHaveLength(2);
    api.defaults.adapter = undefined;
  });

  it("expired session: clears the tokens, flags the login screen and keeps the screen to return to", () => {
    localStorage.setItem("access_token", "a");
    localStorage.setItem("refresh_token", "r");
    sessionExpired();
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(sessionStorage.getItem(SESSION_EXPIRED_KEY)).toBe("1");
    expect(replace).toHaveBeenCalledWith("/login?next=%2Fmaintenance%2Fkpis%3Fx%3D1");
  });

  it("the operator tablet goes to its own kiosk login", () => {
    vi.stubGlobal("location", { ...window.location, replace, pathname: "/op/stops", search: "" });
    sessionExpired();
    expect(replace).toHaveBeenCalledWith("/op/login");
  });
});
