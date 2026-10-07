import { describe, expect, it } from "vitest";
import type { TreePlant } from "@/services/types";
import sprint6Doc from "../../../docs/sprint6.md?raw";
import {
  buildTree, canOpenStep, csvField, detectDelimiter, isIanaZone, selectionToCsv, shiftTemplate, shiftsOverlap, suggestKey,
  suggestRole, UF_TIMEZONE, validateStep1, validateStep2, validateStep3,
} from "./onboarding";

function block<T>(name: string): T {
  const m = sprint6Doc.match(new RegExp("```json " + name + "\\n([\\s\\S]*?)```"));
  if (!m) throw new Error(name);
  return JSON.parse(m[1]) as T;
}

describe("step 1 — company, plant and timezone", () => {
  it("requires a confirmed IANA timezone and a name", () => {
    const e = validateStep1({ name: "", address: "", uf: "AM", timezone: "Manaus", confirmed: false }, false);
    expect(Object.keys(e).sort()).toEqual(["confirmed", "name", "timezone"]);
    expect(validateStep1({ name: "Planta Manaus", address: "", uf: "AM", timezone: "America/Manaus", confirmed: true }, false)).toEqual({});
  });
  it("an existing plant does not need the name again", () => {
    expect(validateStep1({ name: "", address: "", uf: "", timezone: "America/Sao_Paulo", confirmed: true }, true)).toEqual({});
  });
  it("suggests the timezone by state", () => {
    expect(UF_TIMEZONE.AM).toBe("America/Manaus");
    expect(UF_TIMEZONE.SP).toBe("America/Sao_Paulo");
    for (const tz of Object.values(UF_TIMEZONE)) expect(isIanaZone(tz)).toBe(true);
    expect(isIanaZone("GMT-3 Brasília")).toBe(false);
  });
});

describe("step 2 — lines and machines", () => {
  const plant = (lines: TreePlant["lines"]): TreePlant => ({ id: "p", name: "P", timezone: "America/Manaus", lines });
  const m = (id: string, name: string) => ({ id, name, status: "running" as const, last_seen_at: null, positions_count: 0 });
  const ss = { running: 0, stopped: 0, error: 0, unknown: 0 };
  it("every line needs an output machine", () => {
    expect(validateStep2(plant([{ id: "l", name: "Montagem", machines: [m("a", "A")], status_summary: ss }]))).toEqual(["linha Montagem: defina a máquina de saída"]);
    expect(validateStep2(plant([{ id: "l", name: "Montagem", output_machine_id: "a", machines: [m("a", "A")], status_summary: ss }]))).toEqual([]);
  });
  it("machine names are unique per plant (accents and case ignored)", () => {
    const p = plant([
      { id: "l1", name: "L1", output_machine_id: "a", machines: [m("a", "Estação de Fixação")], status_summary: ss },
      { id: "l2", name: "L2", output_machine_id: "b", machines: [m("b", "estacao de fixacao")], status_summary: ss },
    ]);
    expect(validateStep2(p)[0]).toMatch(/repetidos/);
  });
  it("no lines → error", () => expect(validateStep2(plant([]))).toHaveLength(1));
});

describe("step 3 — shifts", () => {
  it("the templates never overlap (D9)", () => {
    for (const n of [1, 2, 3] as const) expect(validateStep3(shiftTemplate(n, "p"), false)).toEqual([]);
  });
  it("detects overlap, also across midnight", () => {
    const c = { name: "C", start_local: "22:00", end_local: "06:00", weekdays: [0] };
    expect(shiftsOverlap(c, { name: "A", start_local: "05:00", end_local: "13:00", weekdays: [1] })).toBe(true);
    expect(shiftsOverlap(c, { name: "A", start_local: "06:00", end_local: "14:00", weekdays: [1] })).toBe(false);
    const sun = { name: "S", start_local: "22:00", end_local: "06:00", weekdays: [6] };
    expect(shiftsOverlap(sun, { name: "M", start_local: "05:00", end_local: "07:00", weekdays: [0] })).toBe(true);
  });
  it("needs an active shift or an explicit 24 h operation (D6)", () => {
    expect(validateStep3([], false)[0]).toMatch(/24 h/);
    expect(validateStep3([], true)).toEqual([]);
    expect(validateStep3([{ name: "A", start_local: "06:00", end_local: "14:00", weekdays: [0], is_active: false }], false)).toHaveLength(1);
  });
});

describe("step 6 — tags", () => {
  const spec = block<{ csv: string[] }>("s6-tags-csv");
  it("detects the delimiter of the spec CSV", () => {
    expect(detectDelimiter(spec.csv.join("\n"))).toBe(";");
    expect(detectDelimiter("﻿chave,linha,maquina\n")).toBe(",");
  });
  it("quotes NodeIds with ';' (RFC 4180)", () => {
    expect(csvField("ns=2;s=Transportador.Run")).toBe('"ns=2;s=Transportador.Run"');
    expect(csvField('a"b')).toBe('"a""b"');
    expect(csvField("plain")).toBe("plain");
  });
  it("turns a browse selection into the same CSV as the spec rows", () => {
    const csv = selectionToCsv("opcua-plc", [{
      node: { node_id: "ns=2;s=Transportador.Run", display_name: "Run", data_type: "Boolean", node_class: "Variable" },
      role: "run_state", line: "Linha de Montagem", machine: "Transportador de Saída", position: "", key: "montagem.transportador.rodando", counterMax: "",
    }]);
    const [head, row] = csv.trim().split("\n");
    expect(head).toBe(spec.csv[0]);
    expect(row).toBe('montagem.transportador.rodando;Linha de Montagem;Transportador de Saída;;run_state;opcua-plc;"ns=2;s=Transportador.Run";;;;;;;;;');
  });
  it("suggests roles by type and name (only a suggestion)", () => {
    expect(suggestRole({ node_id: "ns=2;s=Transportador.Run", display_name: "Run", data_type: "Boolean" })).toBe("run_state");
    expect(suggestRole({ node_id: "x", display_name: "Rodando", data_type: "Boolean" })).toBe("run_state");
    expect(suggestRole({ node_id: "x", display_name: "Falha", data_type: "Boolean" })).toBe("fault_state");
    expect(suggestRole({ node_id: "x", display_name: "Good", data_type: "UInt32" })).toBe("part_counter_good");
    expect(suggestRole({ node_id: "x", display_name: "Refugo", data_type: "UInt32" })).toBe("part_counter_scrap");
    expect(suggestRole({ node_id: "x", display_name: "Ciclos A1", data_type: "UInt16" })).toBe("cycle_counter");
    expect(suggestRole({ node_id: "x", display_name: "Tempo de curso", data_type: "Float" })).toBe("stroke_time");
    expect(suggestRole({ node_id: "x", display_name: "Temperatura", data_type: "Float" })).toBe("analog");
  });
  it("builds the browse tree", () => {
    const t = buildTree([
      { node_id: "a", display_name: "Planta", node_class: "Object", parent_id: null },
      { node_id: "b", display_name: "Run", node_class: "Variable", parent_id: "a" },
      { node_id: "c", display_name: "Orphan", node_class: "Variable", parent_id: "zz" },
    ]);
    expect(t.map((x) => x.node.node_id)).toEqual(["c", "a"]);
    expect(t[1].children[0].node.node_id).toBe("b");
  });
  it("suggests a key linha.maquina.sinal", () => {
    expect(suggestKey("Linha de Montagem", "Transportador de Saída", { node_id: "x", display_name: "Run" })).toBe("linha_de_montagem.transportador_de_saida.run");
  });
});

describe("resumable navigation", () => {
  const steps = (states: ("pending" | "in_progress" | "done")[]) => states.map((state, i) => ({ step: i + 1, state }));
  it("opens a step only after the previous ones are done, or when it was started", () => {
    const s = steps(["done", "done", "pending", "pending", "in_progress", "pending", "pending", "pending", "pending"]);
    expect(canOpenStep(s, 3)).toBe(true);
    expect(canOpenStep(s, 4)).toBe(false);
    expect(canOpenStep(s, 5)).toBe(true);
    expect(canOpenStep(s, 1)).toBe(true);
  });
});
