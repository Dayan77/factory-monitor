import { describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { docSlug, docUrl, rewriteDocHref, rewriteDocLinks } from "../src/lib/doc-links.mjs";
import { buildSearchIndex, plainText, shortTitle } from "../src/lib/doc-text";

const root = fileURLToPath(new URL("..", import.meta.url));

describe("manual links → site URLs", () => {
  it("maps manual pages, anchors and the cover", () => {
    expect(docSlug("README.md")).toBe("");
    expect(docSlug("/x/operador.md")).toBe("operador");
    expect(docUrl("")).toBe("/docs");
    expect(rewriteDocHref("operador.md")).toBe("/docs/operador");
    expect(rewriteDocHref("./indicadores.md#oee")).toBe("/docs/indicadores#oee");
    expect(rewriteDocHref("README.md#quando-aparece-um-traco")).toBe("/docs#quando-aparece-um-traco");
  });
  it("technical docs of the repository become plain text; external links and anchors stay", () => {
    expect(rewriteDocHref("../kpis.md")).toBeNull();
    expect(rewriteDocHref("https://factoryconnect.tech")).toBeUndefined();
    expect(rewriteDocHref("#oee")).toBeUndefined();
    expect(rewriteDocLinks("ver [`docs/kpis.md`](../kpis.md) e [OEE](indicadores.md#oee); ![img](a.md)"))
      .toBe("ver `docs/kpis.md` e [OEE](/docs/indicadores#oee); ![img](a.md)");
  });
});

describe("copies in src/manual", () => {
  it("are in sync with docs/manual (run `npm run sync-docs` after editing the manual)", () => {
    expect(() => execFileSync("node", ["scripts/sync-docs.mjs", "--check"], { cwd: root, stdio: "pipe" })).not.toThrow();
  });
  it("every internal link points at an existing page and anchor", () => {
    const dir = `${root}/src/manual`;
    const files = readdirSync(dir).filter((f) => f.endsWith(".md"));
    const anchors = new Map<string, Set<string>>();
    for (const f of files) {
      const md = readFileSync(`${dir}/${f}`, "utf8");
      anchors.set(docSlug(f), new Set([...md.matchAll(/<a id="([^"]+)"><\/a>/g)].map((m) => m[1] ?? "")));
    }
    const broken: string[] = [];
    for (const f of files) {
      const md = readFileSync(`${dir}/${f}`, "utf8");
      for (const m of md.matchAll(/\]\((\/docs[^)\s]*)\)/g)) {
        const [p = "", hash] = (m[1] ?? "").split("#");
        const slug = p.replace(/^\/docs\/?/, "");
        if (!anchors.has(slug)) broken.push(`${f}: ${m[1]} (página)`);
        else if (hash && !anchors.get(slug)!.has(hash)) broken.push(`${f}: ${m[1]} (âncora)`);
      }
    }
    expect(broken).toEqual([]);
  });
});

describe("search index helpers", () => {
  it("strips markdown to searchable text", () => {
    expect(plainText('<a id="oee"></a>\n**OEE** = `A × P × Q` — [ver](x.md)\n| a | b |')).toBe("OEE = A × P × Q — ver a b");
    expect(shortTitle("Operador (tablet da linha)")).toBe("Operador");
    expect(shortTitle("Manual do usuário FactoryConnect")).toBe("Início");
  });
});

describe("search index", () => {
  it("one entry per h2/h3 section, pointing at the explicit manual anchor when there is one", () => {
    const raw = '---\ntitle: x\n---\n# Indicadores\n\nIntro.\n\n<a id="oee"></a>\n### OEE — eficiência global\n\nDisponibilidade × Desempenho.\n\n## Sem âncora\n\nTexto.';
    const idx = buildSearchIndex([{ short: "Indicadores", url: "/docs/indicadores", raw,
      headings: [{ depth: 1, slug: "indicadores", text: "Indicadores" }, { depth: 3, slug: "oee--eficiência-global", text: "OEE — eficiência global" }, { depth: 2, slug: "sem-âncora", text: "Sem âncora" }] }]);
    expect(idx.map((e) => e.u)).toEqual(["/docs/indicadores", "/docs/indicadores#oee", "/docs/indicadores#sem-âncora"]);
    expect(idx[1]).toMatchObject({ p: "Indicadores", h: "OEE — eficiência global", t: "Disponibilidade × Desempenho." });
  });
});
