import { describe, expect, it, vi } from "vitest";
import { validateLead, toPayload, submitLead, type LeadInput } from "../src/lib/lead";

const valid: LeadInput = {
  name: "Ana Souza",
  email: "ana@empresa.com.br",
  company: "Empresa X",
  phone: "(11) 91234-5678",
  message: "Queremos medir paradas de 4 prensas.",
  consent: true,
  website: "",
};

describe("validateLead", () => {
  it("accepts a complete valid lead", () => {
    expect(validateLead(valid)).toEqual({});
  });

  it("accepts optional company/phone empty", () => {
    expect(validateLead({ ...valid, company: "", phone: "" })).toEqual({});
  });

  it("requires name, email, message and consent", () => {
    const e = validateLead({ ...valid, name: " ", email: "", message: "", consent: false });
    expect(Object.keys(e).sort()).toEqual(["consent", "email", "message", "name"]);
    expect(e.consent).toMatch(/concordar/);
  });

  it.each(["ana", "ana@", "ana@empresa", "ana @empresa.com", "@empresa.com"])("rejects invalid e-mail %s", (email) => {
    expect(validateLead({ ...valid, email }).email).toBe("E-mail inválido.");
  });

  it.each(["123", "abc-defg-hij", "+55 11 9 1234 5678 0000 00"])("rejects invalid phone %s", (phone) => {
    expect(validateLead({ ...valid, phone }).phone).toBe("Telefone inválido.");
  });

  it("accepts international phone formats", () => {
    expect(validateLead({ ...valid, phone: "+55 11 91234-5678" }).phone).toBeUndefined();
  });

  it("enforces the server-side length limits", () => {
    expect(validateLead({ ...valid, name: "a".repeat(256) }).name).toMatch(/Máximo/);
    expect(validateLead({ ...valid, message: "a".repeat(5001) }).message).toMatch(/Máximo/);
    expect(validateLead({ ...valid, company: "a".repeat(256) }).company).toMatch(/Máximo/);
  });
});

describe("toPayload", () => {
  it("trims, maps empty optionals to null and keeps the honeypot as-is", () => {
    const p = toPayload({ ...valid, name: "  Ana ", company: "  ", phone: "", website: "spam.com" }, "/contato");
    expect(p).toEqual({
      name: "Ana",
      email: "ana@empresa.com.br",
      company: null,
      phone: null,
      message: "Queremos medir paradas de 4 prensas.",
      consent: true,
      website: "spam.com",
      source: "/contato",
    });
  });

  it("matches the API contract keys", () => {
    expect(Object.keys(toPayload(valid, "/")).sort()).toEqual(
      ["company", "consent", "email", "message", "name", "phone", "source", "website"],
    );
  });
});

describe("submitLead", () => {
  const payload = toPayload(valid, "/contato");
  const res = (status: number) => ({ ok: status >= 200 && status < 300, status, json: async () => ({}) });

  it("POSTs JSON to the endpoint without credentials", async () => {
    const f = vi.fn(async () => res(202));
    const r = await submitLead("https://api.test/api/public/leads", payload, f);
    expect(r).toEqual({ ok: true });
    expect(f).toHaveBeenCalledOnce();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.test/api/public/leads");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("omit");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });

  it.each([
    [422, "validation"],
    [429, "rate_limit"],
    [500, "server"],
    [503, "server"],
  ] as const)("maps HTTP %i to %s", async (status, kind) => {
    const r = await submitLead("u", payload, async () => res(status));
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.kind).toBe(kind);
      expect(r.message.length).toBeGreaterThan(10);
    }
  });

  it("maps network failures", async () => {
    const r = await submitLead("u", payload, async () => { throw new TypeError("Failed to fetch"); });
    expect(r).toMatchObject({ ok: false, kind: "network" });
  });

  it("aborts after the timeout", async () => {
    const f = (_u: string, init: RequestInit) =>
      new Promise<never>((_, reject) => init.signal?.addEventListener("abort", () => reject(new Error("aborted"))));
    const r = await submitLead("u", payload, f, 20);
    expect(r).toMatchObject({ ok: false, kind: "network" });
  });
});
