import { describe, expect, it } from "vitest";
import { parseBlocks, parseInline, plainAnswer, safeHref, suggestionsFor, toolLabel } from "./aiFormat";

describe("inline markdown", () => {
  it("bold, citations and internal links", () => {
    const r = parseInline("OEE de **71,4 %** [1]. Onde ver: [OEE](/oee?scope=line&id=x)");
    expect(r).toEqual([
      { kind: "text", text: "OEE de " },
      { kind: "bold", children: [{ kind: "text", text: "71,4 %" }] },
      { kind: "text", text: " " },
      { kind: "cite", n: 1 },
      { kind: "text", text: ". Onde ver: " },
      { kind: "link", text: "OEE", href: "/oee?scope=line&id=x", internal: true },
    ]);
  });
  it("never renders javascript:/data: links", () => {
    expect(parseInline("[x](javascript:alert(1))")[0]).toMatchObject({ kind: "text" });
    expect(safeHref("//evil.com")).toBeNull();
    expect(safeHref("https://factoryconnect.tech/a")).toEqual({ href: "https://factoryconnect.tech/a", internal: false });
  });
});

describe("blocks", () => {
  it("lists, tables and paragraphs", () => {
    const b = parseBlocks("Resposta direta.\n\n- A: 1 [1]\n- B: 2 [1]\n\n| Turno | OEE |\n|---|---|\n| A | 70,0 % [1] |\n| B | 65,0 % [1] |\nOnde ver: [OEE](/oee)");
    expect(b.map((x) => x.kind)).toEqual(["p", "ul", "table", "p"]);
    const t = b[2];
    if (t.kind !== "table") throw new Error();
    expect(t.rows).toHaveLength(2);
    expect(t.rows[0][1]).toContainEqual({ kind: "cite", n: 1 });
  });
});

describe("copy text", () => {
  it("keeps markers and lists the sources with absolute links", () => {
    const t = plainAnswer("OEE **71,4 %** [1]. Onde ver: [OEE](/oee)", [{ id: 1, label: "OEE · Linha", url: "/oee?x=1" }], "https://app");
    expect(t).toBe("OEE 71,4 % [1]. Onde ver: OEE (https://app/oee)\n\nFontes:\n[1] OEE · Linha — https://app/oee?x=1");
  });
});

describe("suggestions and steps", () => {
  it("per role", () => {
    expect(suggestionsFor(["technician"])[0]).toBe("Quais são as minhas OS de hoje?");
    expect(suggestionsFor(["maintenance_planner"])).toContain("Algum componente está degradando? Quando vai falhar?");
    expect(suggestionsFor(["operator"])).toEqual(["O que está parado agora?"]);
    expect(suggestionsFor(["manager", "maintenance_planner"]).length).toBeLessThanOrEqual(6);
  });
  it("tool labels in Portuguese", () => {
    expect(toolLabel("get_oee")).toBe("Consultando o OEE");
    expect(toolLabel("novo")).toBe("Consultando novo");
  });
});
