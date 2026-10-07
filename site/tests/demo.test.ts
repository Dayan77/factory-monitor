import { describe, expect, it, vi } from "vitest";
import {
  MSG_ERROR, MSG_UNAVAILABLE, checkDemo, demoEvent, demoUrls, trackDemoOpened, trackingAllowed,
} from "../src/lib/demo";

const STATUS = "https://app.factoryconnect.tech/api/public/demo-status";
const reply = (status: number, body: unknown) =>
  vi.fn(async () => ({ ok: status >= 200 && status < 300, status, json: async () => body }));

describe("demoUrls", () => {
  it("opens the app route /demo (the app creates the session) and checks the public status", () => {
    expect(demoUrls("https://app.factoryconnect.tech/", "https://api.example.com//")).toEqual({
      app: "https://app.factoryconnect.tech/demo", status: "https://api.example.com/api/public/demo-status",
    });
  });
  it("never puts a token or session in the URL", () => {
    expect(demoUrls("https://app.x", "https://app.x").app).not.toMatch(/[?#]/);
  });
});

describe("checkDemo", () => {
  it("ok when the demo tenant is available (plant running or not)", async () => {
    const f = reply(200, { available: true, plant_running: true, banner: "Demonstração — ..." });
    expect(await checkDemo(STATUS, f)).toEqual({ kind: "ok", plantRunning: true });
    expect(f).toHaveBeenCalledWith(STATUS, expect.objectContaining({ method: "GET", credentials: "omit" }));
    expect(await checkDemo(STATUS, reply(200, { available: true, plant_running: false })))
      .toEqual({ kind: "ok", plantRunning: false });
  });
  it("unavailable: demo tenant missing, 503 or 404", async () => {
    for (const f of [reply(200, { available: false }), reply(503, {}), reply(404, {})]) {
      expect(await checkDemo(STATUS, f)).toEqual({ kind: "unavailable", message: MSG_UNAVAILABLE });
    }
  });
  it("error: network failure, 5xx, 429 or unexpected body — never throws", async () => {
    const boom = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    for (const f of [boom, reply(500, {}), reply(429, {}), reply(200, null), reply(200, { foo: 1 })]) {
      expect(await checkDemo(STATUS, f as never)).toEqual({ kind: "error", message: MSG_ERROR });
    }
  });
  it("times out instead of hanging", async () => {
    const hang = vi.fn((_u: string, init?: { signal?: AbortSignal }) => new Promise<never>((_, rej) => {
      init?.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
    }));
    expect(await checkDemo(STATUS, hang as never, 20)).toEqual({ kind: "error", message: MSG_ERROR });
  });
});

describe("tracking 'abriu demo' (LGPD)", () => {
  it("respects Do Not Track and Global Privacy Control", () => {
    expect(trackingAllowed({ doNotTrack: "1" })).toBe(false);
    expect(trackingAllowed({ globalPrivacyControl: true })).toBe(false);
    expect(trackingAllowed({ doNotTrack: null })).toBe(true);
    expect(trackingAllowed(undefined)).toBe(false);
  });
  it("event carries only the name and where the button was — no identifier", () => {
    expect(demoEvent("hero")).toEqual({ event: "abriu_demo", placement: "hero" });
    expect(demoEvent("<script>")).toEqual({ event: "abriu_demo", placement: "outro" });
  });
  it("sends one beacon to the first-party endpoint, nothing when unconfigured or opted out", () => {
    const sendBeacon = vi.fn((_url: string, _data: string | Blob) => true);
    expect(trackDemoOpened("", "hero", { sendBeacon })).toBe(false);
    expect(trackDemoOpened("https://app.x/api/public/events", "hero", { sendBeacon, doNotTrack: "1" })).toBe(false);
    expect(sendBeacon).not.toHaveBeenCalled();
    expect(trackDemoOpened("https://app.x/api/public/events", "header", { sendBeacon })).toBe(true);
    expect(sendBeacon).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(sendBeacon.mock.calls[0]?.[1]))).toEqual({ event: "abriu_demo", placement: "header" });
  });
});
