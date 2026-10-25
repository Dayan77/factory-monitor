import { describe, expect, it } from "vitest";
import { demoAllowsRequest, isDemoBlockedPath, isDemoUser, tokenIsDemo } from "./demo";

function jwt(payload: object): string {
  const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

describe("demo mode (sprint6 §5)", () => {
  it("recognises the demo_viewer role", () => {
    expect(isDemoUser({ role: "demo_viewer" })).toBe(true);
    expect(isDemoUser({ role: "viewer", roles: ["demo_viewer"] })).toBe(true);
    expect(isDemoUser({ role: "admin" })).toBe(false);
    expect(isDemoUser(null)).toBe(false);
  });
  it("only reads, except the AI assistant questions", () => {
    expect(demoAllowsRequest("get", "/api/oee")).toBe(true);
    expect(demoAllowsRequest("post", "/api/downtime/")).toBe(false);
    expect(demoAllowsRequest("patch", "/api/downtime/1")).toBe(false);
    expect(demoAllowsRequest("delete", "/api/report-schedules/1")).toBe(false);
    expect(demoAllowsRequest("post", "/api/ai/chat")).toBe(true);
    expect(demoAllowsRequest("post", "http://x/api/ai/chat/stream?x=1")).toBe(true);
    expect(demoAllowsRequest("post", "/api/reports/generate")).toBe(false);
  });
  it("hides admin, users, gateways, settings and cameras", () => {
    for (const p of ["/admin", "/settings/gateways", "/settings/production", "/cameras", "/onboarding/3"]) {
      expect(isDemoBlockedPath(p)).toBe(true);
    }
    for (const p of ["/dashboard", "/factory", "/oee", "/maintenance/kpis", "/ai"]) {
      expect(isDemoBlockedPath(p)).toBe(false);
    }
  });
  it("reads the role claim of the token", () => {
    expect(tokenIsDemo(jwt({ role: "demo_viewer", sub: "x" }))).toBe(true);
    expect(tokenIsDemo(jwt({ role: "admin" }))).toBe(false);
    expect(tokenIsDemo("garbage")).toBe(false);
    expect(tokenIsDemo(null)).toBe(false);
  });
});
