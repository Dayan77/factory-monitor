/**
 * Validation and helpers of the onboarding wizard (docs/sprint6.md §4). The server validates every step again when it
 * is marked `done` (PUT /api/onboarding → 422 with the errors); these rules give the same answer before the round
 * trip, so the "Concluir passo" button explains what is missing.
 */
import type { ShiftBody, TreePlant } from "@/services/types";
import type { BrowseNode } from "@/services/sprint6Types";

export const STEP_TITLES = [
  "Empresa e planta", "Linhas e máquinas", "Turnos e calendário", "Produtos e tempo de ciclo ideal", "Conectar gateway",
  "Importar tags", "Motivos de parada e códigos de falha", "Usuários e papéis", "Verificação \"pronto para produção\"",
];

// ── step 1: timezone ──────────────────────────────────────────────

/** Suggested IANA timezone by Brazilian state (UF) — a suggestion the user must confirm (never the browser's). */
export const UF_TIMEZONE: Record<string, string> = {
  AC: "America/Rio_Branco", AL: "America/Maceio", AM: "America/Manaus", AP: "America/Belem", BA: "America/Bahia",
  CE: "America/Fortaleza", DF: "America/Sao_Paulo", ES: "America/Sao_Paulo", GO: "America/Sao_Paulo",
  MA: "America/Fortaleza", MG: "America/Sao_Paulo", MS: "America/Campo_Grande", MT: "America/Cuiaba",
  PA: "America/Belem", PB: "America/Fortaleza", PE: "America/Recife", PI: "America/Fortaleza", PR: "America/Sao_Paulo",
  RJ: "America/Sao_Paulo", RN: "America/Fortaleza", RO: "America/Porto_Velho", RR: "America/Boa_Vista",
  RS: "America/Sao_Paulo", SC: "America/Sao_Paulo", SE: "America/Maceio", SP: "America/Sao_Paulo",
  TO: "America/Araguaina",
};

const FALLBACK_TZ = [
  "America/Sao_Paulo", "America/Manaus", "America/Belem", "America/Fortaleza", "America/Recife", "America/Bahia",
  "America/Cuiaba", "America/Campo_Grande", "America/Porto_Velho", "America/Boa_Vista", "America/Rio_Branco",
  "America/Araguaina", "America/Maceio", "America/Noronha", "UTC",
];

export function ianaZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  try {
    const all = intl.supportedValuesOf?.("timeZone");
    if (all && all.length) return all.includes("UTC") ? all : [...all, "UTC"];
  } catch { /* old engine */ }
  return FALLBACK_TZ;
}

export function isIanaZone(tz: string): boolean {
  if (!tz || !/^[A-Za-z_]+(\/[A-Za-z0-9_+-]+)*$/.test(tz)) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export interface Step1Form {
  name: string;
  address: string;
  uf: string;
  timezone: string;
  confirmed: boolean;
}

export function validateStep1(f: Step1Form, hasPlant: boolean): Record<string, string> {
  const e: Record<string, string> = {};
  if (!hasPlant && !f.name.trim()) e.name = "Informe o nome da planta.";
  if (f.name.length > 255) e.name = "Nome muito longo.";
  if (!isIanaZone(f.timezone)) e.timezone = "Escolha um fuso da lista (ex.: America/Sao_Paulo).";
  if (!f.confirmed) e.confirmed = "Confirme o fuso da planta — os turnos e os dias produtivos dependem dele.";
  return e;
}

// ── step 2: lines and machines ────────────────────────────────────

export function norm(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").trim().toLowerCase().replace(/\s+/g, " ");
}

export function validateStep2(plant: TreePlant | null | undefined): string[] {
  const errs: string[] = [];
  if (!plant || plant.lines.length === 0) return ["cadastre ao menos uma linha com máquinas"];
  for (const l of plant.lines) {
    if (l.machines.length === 0) errs.push(`linha ${l.name}: sem máquinas`);
    else if (!l.output_machine_id) errs.push(`linha ${l.name}: defina a máquina de saída`);
  }
  const seen = new Set<string>();
  const dups = new Set<string>();
  for (const l of plant.lines) for (const m of l.machines) {
    const k = norm(m.name);
    if (seen.has(k)) dups.add(m.name);
    seen.add(k);
  }
  if (dups.size) errs.push(`nomes de máquina repetidos na planta: ${[...dups].join(", ")}`);
  return errs;
}

// ── step 3: shifts ────────────────────────────────────────────────

const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const WEEKDAYS = [0, 1, 2, 3, 4];

/** Ready-made calendars (1, 2 or 3 shifts with a meal break). */
export function shiftTemplate(n: 1 | 2 | 3, plantId: string): ShiftBody[] {
  if (n === 1) {
    return [{ plant_id: plantId, name: "Turno A", start_local: "07:00", end_local: "16:48", weekdays: WEEKDAYS,
      breaks: [{ start_local: "12:00", end_local: "13:00", reason_code: "PLN-01" }] }];
  }
  if (n === 2) {
    return [
      { plant_id: plantId, name: "Turno A", start_local: "06:00", end_local: "14:00", weekdays: [0, 1, 2, 3, 4, 5], breaks: [{ start_local: "10:00", end_local: "10:30", reason_code: "PLN-01" }] },
      { plant_id: plantId, name: "Turno B", start_local: "14:00", end_local: "22:00", weekdays: [0, 1, 2, 3, 4, 5], breaks: [{ start_local: "18:00", end_local: "18:30", reason_code: "PLN-01" }] },
    ];
  }
  return [
    { plant_id: plantId, name: "Turno A", start_local: "06:00", end_local: "14:00", weekdays: ALL_DAYS, breaks: [{ start_local: "10:00", end_local: "10:30", reason_code: "PLN-01" }] },
    { plant_id: plantId, name: "Turno B", start_local: "14:00", end_local: "22:00", weekdays: ALL_DAYS, breaks: [{ start_local: "18:00", end_local: "18:30", reason_code: "PLN-01" }] },
    { plant_id: plantId, name: "Turno C", start_local: "22:00", end_local: "06:00", weekdays: ALL_DAYS, breaks: [{ start_local: "02:00", end_local: "02:30", reason_code: "PLN-01" }] },
  ];
}

function hm(s: string): number {
  const [h, m] = s.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** Weekly minute intervals of a shift (a shift crossing midnight belongs to the day it starts on, C19). */
function intervals(s: Pick<ShiftBody, "start_local" | "end_local" | "weekdays">): [number, number][] {
  const a = hm(s.start_local);
  let b = hm(s.end_local);
  if (b <= a) b += 1440;
  const week = 7 * 1440;
  const out: [number, number][] = [];
  for (const d of s.weekdays) {
    const st = d * 1440 + a;
    const en = d * 1440 + b;
    if (en <= week) out.push([st, en]);
    else { out.push([st, week]); out.push([0, en - week]); }
  }
  return out;
}

type ShiftLike = Pick<ShiftBody, "start_local" | "end_local" | "weekdays"> & { name?: string };

export function shiftsOverlap(a: ShiftLike, b: ShiftLike): boolean {
  const ia = intervals(a);
  const ib = intervals(b);
  return ia.some(([s1, e1]) => ib.some(([s2, e2]) => s1 < e2 && s2 < e1));
}

export function validateStep3(shifts: (Pick<ShiftBody, "name" | "start_local" | "end_local" | "weekdays"> & { is_active?: boolean })[], operation24h: boolean): string[] {
  const act = shifts.filter((s) => s.is_active !== false);
  const errs: string[] = [];
  for (let i = 0; i < act.length; i += 1) {
    for (let j = i + 1; j < act.length; j += 1) {
      if (shiftsOverlap(act[i], act[j])) errs.push(`turnos sobrepostos: ${act[i].name} e ${act[j].name} (D9)`);
    }
  }
  if (act.length === 0 && !operation24h) errs.push("cadastre ao menos 1 turno ativo ou declare operação 24 h");
  return errs;
}

// ── step 6: tags (CSV §4.1 and OPC UA browse §4.2) ────────────────

export const SEMANTIC_ROLES = [
  "analog", "cycle_counter", "cycle_pulse", "run_state", "part_counter_good", "part_counter_scrap",
  "part_counter_total", "fault_state", "stroke_time", "speed", "current",
] as const;
export type SemanticRole = (typeof SEMANTIC_ROLES)[number];

export const ROLE_LABEL: Record<SemanticRole, string> = {
  analog: "Analógico", cycle_counter: "Contador de ciclos", cycle_pulse: "Pulso de ciclo", run_state: "Rodando (run)",
  part_counter_good: "Peças boas", part_counter_scrap: "Refugo", part_counter_total: "Peças total",
  fault_state: "Falha / código", stroke_time: "Tempo de curso", speed: "Velocidade", current: "Corrente",
};

/** CSV delimiter from the header line (`;` Excel pt-BR, or `,`). */
export function detectDelimiter(text: string): ";" | "," {
  const head = text.replace(/^\uFEFF/, "").split(/\r?\n/, 1)[0] ?? "";
  return (head.match(/;/g)?.length ?? 0) >= (head.match(/,/g)?.length ?? 0) ? ";" : ",";
}

/** RFC 4180 field (quotes when it has the delimiter, a quote or a line break — NodeIds have `;`). */
export function csvField(v: string | number | null | undefined, delim = ";"): string {
  const s = v == null ? "" : String(v);
  return s.includes(delim) || s.includes('"') || /[\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export const TAG_CSV_HEADER = ["chave", "linha", "maquina", "posicao", "papel", "fonte", "endereco", "unit_id", "tipo_registro",
  "tipo_dado", "ordem_palavras", "contador_max", "unidade", "escala", "offset", "intervalo_ms"];

export interface BrowseSelection {
  node: BrowseNode;
  role: SemanticRole;
  line: string;
  machine: string;
  position: string;
  key: string;
  counterMax: string;
}

/** The tag CSV of the browse selection (same import path as an uploaded file). */
export function selectionToCsv(sourceId: string, rows: BrowseSelection[]): string {
  const lines = [TAG_CSV_HEADER.join(";")];
  for (const r of rows) {
    const vals = [r.key, r.line, r.machine, r.position, r.role, sourceId, r.node.node_id, "", "", "", "",
      isCounter(r.role) ? r.counterMax : "", "", "", "", ""];
    lines.push(vals.map((v) => csvField(v)).join(";"));
  }
  return lines.join("\n") + "\n";
}

export function isCounter(role: string): boolean {
  return role === "cycle_counter" || role.startsWith("part_counter");
}

const VARIABLE = /variable/i;

/** Role suggestion from the node's data type and name (§4.2: only a suggestion). */
export function suggestRole(n: Pick<BrowseNode, "data_type" | "display_name" | "browse_name" | "node_id">): SemanticRole {
  const name = norm(`${n.display_name ?? ""} ${n.browse_name ?? ""} ${n.node_id}`);
  const t = (n.data_type ?? "").toLowerCase();
  const bool = t.includes("bool");
  if (bool) {
    if (/\b(run|running|rodando|ligad|em ?operacao|operando)/.test(name)) return "run_state";
    if (/(fault|falha|alarm|alarme|erro|error|trip)/.test(name)) return "fault_state";
    if (/(pulse|pulso|ciclo|cycle)/.test(name)) return "cycle_pulse";
    return "run_state";
  }
  if (/(fault|falha|alarm|alarme|erro|error)/.test(name)) return "fault_state";
  if (/(scrap|refugo|rejeit|ng\b|bad)/.test(name)) return "part_counter_scrap";
  if (/(good|boas|ok\b|aprovad)/.test(name)) return "part_counter_good";
  if (/(total|pecas|parts|count|contador|produc)/.test(name) && !/(ciclo|cycle)/.test(name)) return "part_counter_total";
  if (/(ciclo|cycle|stroke ?count)/.test(name)) return /(time|tempo|curso)/.test(name) ? "stroke_time" : "cycle_counter";
  if (/(curso|stroke)/.test(name)) return "stroke_time";
  if (/(speed|velocidade|rpm|vel\b)/.test(name)) return "speed";
  if (/(current|corrente|amp)/.test(name)) return "current";
  return "analog";
}

/** Only readable variables can become tags. */
export function isVariable(n: Pick<BrowseNode, "node_class">): boolean {
  return VARIABLE.test(n.node_class ?? "");
}

export interface TreeNode {
  node: BrowseNode;
  children: TreeNode[];
}

/** Flat browse result → tree (parent_id); nodes whose parent is missing become roots. */
export function buildTree(nodes: BrowseNode[]): TreeNode[] {
  const by = new Map<string, TreeNode>();
  for (const n of nodes) by.set(n.node_id, { node: n, children: [] });
  const roots: TreeNode[] = [];
  for (const t of by.values()) {
    const p = t.node.parent_id ? by.get(t.node.parent_id) : undefined;
    if (p && p !== t) p.children.push(t);
    else roots.push(t);
  }
  const sort = (xs: TreeNode[]) => {
    xs.sort((a, b) => (a.node.display_name ?? a.node.node_id).localeCompare(b.node.display_name ?? b.node.node_id, "pt-BR"));
    xs.forEach((x) => sort(x.children));
  };
  sort(roots);
  return roots;
}

/** Tag key suggestion `linha.maquina.sinal` (unique in the gateway). */
export function suggestKey(line: string, machine: string, n: Pick<BrowseNode, "display_name" | "browse_name" | "node_id">): string {
  const slug = (s: string) => norm(s).replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const sig = slug(n.display_name ?? n.browse_name ?? n.node_id.split(/[;=.]/).pop() ?? "sinal") || "sinal";
  return [slug(line) || "linha", slug(machine) || "maquina", sig].join(".");
}

// ── navigation ────────────────────────────────────────────────────

export type StepStateLike = { step: number; state: "pending" | "in_progress" | "done" };

/** A step is reachable when every previous step is done, or it was already started (resumable wizard). */
export function canOpenStep(steps: StepStateLike[], n: number): boolean {
  if (n <= 1) return true;
  const s = steps.find((x) => x.step === n);
  if (s && s.state !== "pending") return true;
  return steps.filter((x) => x.step < n).every((x) => x.state === "done");
}
