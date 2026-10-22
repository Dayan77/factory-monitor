import { afterEach, describe, expect, it } from "vitest";
import { checkPassword, normalizeOtp, passwordStrength, PASSWORD_MIN } from "./password";
import { flatten, fold, scoreItem, searchCommands, type CommandItem } from "./commandSearch";
import { describeDevice } from "./device";
import { formatDate, formatDateTime, formatHourLabel, formatTime, setDisplayTimeZone } from "./format";
import { KPI_DOCS, kpiManualUrl } from "./kpiGlossary";
import { roleList } from "./roles";
import { safeNext } from "@/services/api";

describe("password hints (mirror of passwords.check_policy)", () => {
  it("requires the server minimum and refuses trivial sequences and personal data", () => {
    expect(PASSWORD_MIN).toBe(12);
    expect(checkPassword("curta").ok).toBe(false);
    expect(checkPassword("aaaaaaaaaaaaaaa").problem).toMatch(/previsível/);
    expect(checkPassword("123456789012345").problem).toMatch(/previsível/);
    expect(checkPassword("abcdefghijklmnop").problem).toMatch(/previsível/);
    expect(checkPassword("Mariana-Torno-2026!", ["mariana.souza@acme.com"]).problem).toMatch(/nome, e-mail/);
    expect(checkPassword("Bancada-Verde-Seguro-42", ["ana@acme.com", "Ana", "Acme"]).ok).toBe(true);
  });
  it("strength meter grows with length and variety", () => {
    expect(passwordStrength("")).toBe(0);
    expect(passwordStrength("abc")).toBe(0);
    expect(passwordStrength("Bancada-Verde-Seguro-42")).toBeGreaterThanOrEqual(3);
  });
  it("normalizes pasted codes", () => {
    expect(normalizeOtp(" 123 456 ")).toBe("123456");
    expect(normalizeOtp("abcde-fghij")).toBe("abcdefghij");
  });
});

describe("command palette search", () => {
  const items: CommandItem[] = [
    { id: "p1", kind: "page", label: "Ordens de serviço", hint: "Manutenção", to: "/maintenance/work-orders" },
    { id: "p2", kind: "page", label: "Raio-X", to: "/dashboard" },
    { id: "l1", kind: "line", label: "Linha de Usinagem", to: "/factory/lines/1" },
    { id: "m1", kind: "machine", label: "Torno CNC", hint: "Linha de Usinagem", to: "/factory/machines/1" },
    { id: "m2", kind: "machine", label: "Estação de Fixação", hint: "Linha de Montagem", to: "/factory/machines/2" },
    { id: "w1", kind: "work_order", label: "OS-0042 — Troca do rolamento", keywords: "OS-0042", to: "/maintenance/work-orders?wo=1" },
  ];
  it("ignores accents and case, needs every word", () => {
    expect(fold("Estação")).toBe("estacao");
    expect(scoreItem(items[4], "estacao fix")).not.toBeNull();
    expect(scoreItem(items[4], "estacao torno")).toBeNull();
  });
  it("empty query lists only screens; a query groups matches by kind in a fixed order", () => {
    expect(searchCommands(items, "").map((g) => g.kind)).toEqual(["page"]);
    const groups = searchCommands(items, "usinagem");
    expect(groups.map((g) => g.kind)).toEqual(["line", "machine"]);
    expect(flatten(groups).map((i) => i.id)).toEqual(["l1", "m1"]);
  });
  it("puts the group with the best match first", () => {
    const more: CommandItem[] = [...items, { id: "p3", kind: "page", label: "Relatório de turno", to: "/reports/shift" }];
    expect(searchCommands(more, "tor").map((g) => g.kind)).toEqual(["machine", "page"]);
  });
  it("finds a work order by its number and ranks prefix matches first", () => {
    expect(flatten(searchCommands(items, "os-0042"))[0].id).toBe("w1");
    expect(flatten(searchCommands(items, "torno"))[0].id).toBe("m1");
  });
});

describe("plant timezone on screen", () => {
  afterEach(() => setDisplayTimeZone(null));
  it("formats instants in the plant timezone once it is known", () => {
    const iso = "2026-10-07T03:30:00Z";
    setDisplayTimeZone("America/Manaus"); // UTC-4
    expect(formatDateTime(iso)).toBe("06/10/2026, 23:30");
    expect(formatDate(iso)).toBe("06/10/2026");
    expect(formatTime(iso)).toBe("23:30");
    expect(formatHourLabel(iso)).toBe("23h");
    setDisplayTimeZone("Asia/Tokyo"); // UTC+9
    expect(formatTime(iso, true)).toBe("12:30:00");
    expect(formatDate("2026-10-07")).toBe("07/10/2026"); // date-only strings never shift
  });
  it("ignores an invalid timezone instead of throwing", () => {
    setDisplayTimeZone("Not/AZone");
    expect(formatDateTime("2026-10-07T03:30:00Z")).not.toBe("—");
  });
});

describe("small helpers", () => {
  it("describes the device of a session", () => {
    expect(describeDevice("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36")).toBe("Chrome · Windows");
    expect(describeDevice("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1")).toBe("Safari · iOS");
    expect(describeDevice("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/129 Mobile Safari/537.36 EdgA/129 Edg/129")).toBe("Edge · Android");
    expect(describeDevice(null)).toBe("Dispositivo desconhecido");
  });
  it("KPI manual links point at an anchor of the indicators page", () => {
    for (const id of Object.keys(KPI_DOCS) as (keyof typeof KPI_DOCS)[]) {
      expect(kpiManualUrl(id)).toMatch(/^https:\/\/factoryconnect\.tech\/docs\/indicadores#[a-z-]+$/);
    }
  });
  it("role list in Portuguese", () => {
    expect(roleList({ role: "admin", roles: ["admin", "technician"] })).toBe("Administrador · Técnico de manutenção");
    expect(roleList(null)).toBe("");
  });
  it("login ?next= only accepts same-origin app paths", () => {
    expect(safeNext("/maintenance/work-orders?wo=1")).toBe("/maintenance/work-orders?wo=1");
    expect(safeNext("//evil.example")).toBeNull();
    expect(safeNext("https://evil.example")).toBeNull();
    expect(safeNext("/\\evil.example")).toBeNull();
    expect(safeNext("/login")).toBeNull();
    expect(safeNext(null)).toBeNull();
  });
});
