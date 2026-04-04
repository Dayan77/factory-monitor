/**
 * Mock data for frontend development without a running backend.
 * Replace with real API calls once the server is ready.
 */

import type { Machine, Line, AlertEvent, OeeSnapshot, SensorReading } from "./types";

export const MOCK_LINES: Line[] = [
  { id: "l1", name: "Linha 1 — Montagem", location: "Pavilhão A", is_active: true, machine_count: 4, running_count: 4, oee: 87 },
  { id: "l2", name: "Linha 2 — Pintura",  location: "Pavilhão A", is_active: true, machine_count: 3, running_count: 2, oee: 61 },
  { id: "l3", name: "Linha 3 — Embalagem",location: "Pavilhão B", is_active: true, machine_count: 2, running_count: 2, oee: 79 },
  { id: "l4", name: "Linha 4 — Teste",    location: "Pavilhão B", is_active: false,machine_count: 2, running_count: 0, oee: 0 },
];

export const MOCK_MACHINES: Machine[] = [
  { id: "m1", name: "Torno CNC-01",     model: "Mazak QT-200", status: "running",     line_id: "l1", line_name: "Linha 1", oee: 91, availability: 97, performance: 94, quality: 99, updated_at: new Date().toISOString() },
  { id: "m2", name: "Prensa HP-200",    model: "Schuler S200",  status: "running",     line_id: "l1", line_name: "Linha 1", oee: 83, availability: 90, performance: 93, quality: 99, updated_at: new Date().toISOString() },
  { id: "m3", name: "Robô Soldagem R1", model: "KUKA KR 210",   status: "running",     line_id: "l1", line_name: "Linha 1", oee: 88, availability: 95, performance: 93, quality: 100, updated_at: new Date().toISOString() },
  { id: "m4", name: "Esteira Conv-01",  model: "Intralox 400",  status: "running",     line_id: "l1", line_name: "Linha 1", oee: 86, availability: 99, performance: 87, quality: 100, updated_at: new Date().toISOString() },
  { id: "m5", name: "Cabine Pintura A", model: "Dürr EcoRP",    status: "stopped",     line_id: "l2", line_name: "Linha 2", oee: 0,  availability: 0,  performance: 0,  quality: 0,   updated_at: new Date().toISOString() },
  { id: "m6", name: "Forno Cura F-01",  model: "Thermcraft",    status: "error",       line_id: "l2", line_name: "Linha 2", oee: 0,  availability: 0,  performance: 0,  quality: 0,   updated_at: new Date().toISOString() },
  { id: "m7", name: "Compressor AR-02", model: "Atlas Copco",   status: "running",     line_id: "l2", line_name: "Linha 2", oee: 72, availability: 85, performance: 88, quality: 97,  updated_at: new Date().toISOString() },
  { id: "m8", name: "Empacotadora E-1", model: "Bosch PVA",     status: "running",     line_id: "l3", line_name: "Linha 3", oee: 81, availability: 93, performance: 87, quality: 100, updated_at: new Date().toISOString() },
  { id: "m9", name: "Paletizador P-01", model: "Fanuc M-410",   status: "maintenance", line_id: "l3", line_name: "Linha 3", oee: 0,  availability: 0,  performance: 0,  quality: 0,   updated_at: new Date().toISOString() },
];

export const MOCK_ALERTS: AlertEvent[] = [
  { id: "a1", rule_name: "Temp. Forno Alta",   machine_name: "Forno Cura F-01",  severity: "critical", message: "Temperatura atingiu 312°C (limite: 280°C)", triggered_at: new Date(Date.now() - 8 * 60000).toISOString() },
  { id: "a2", rule_name: "Cabine Pintura Off", machine_name: "Cabine Pintura A", severity: "warning",  message: "Máquina parada há mais de 15 minutos",       triggered_at: new Date(Date.now() - 22 * 60000).toISOString() },
  { id: "a3", rule_name: "Pressão Baixa",      machine_name: "Compressor AR-02", severity: "warning",  message: "Pressão em 5.2 bar (mínimo: 6.0 bar)",       triggered_at: new Date(Date.now() - 45 * 60000).toISOString() },
  { id: "a4", rule_name: "Manutenção Prev.",   machine_name: "Paletizador P-01", severity: "info",     message: "Manutenção preventiva agendada (2500h)",      triggered_at: new Date(Date.now() - 2 * 3600000).toISOString(), resolved_at: new Date(Date.now() - 3600000).toISOString() },
];

/** Generate a 24h OEE trend with slight variation */
export function generateOeeTrend(): OeeSnapshot[] {
  const now = Date.now();
  return Array.from({ length: 24 }, (_, i) => {
    const base = 72 + Math.sin(i / 3) * 10;
    const oee = Math.min(100, Math.max(0, base + (Math.random() - 0.5) * 8));
    return {
      oee: +oee.toFixed(1),
      availability: +(oee + 5 + Math.random() * 3).toFixed(1),
      performance: +(oee + 2 + Math.random() * 4).toFixed(1),
      quality: +(95 + Math.random() * 4).toFixed(1),
      timestamp: new Date(now - (23 - i) * 3600000).toISOString(),
    };
  });
}

/** Generate rolling sensor readings for a tag */
export function generateSensorHistory(baseValue: number, unit: string, points = 60): SensorReading[] {
  const now = Date.now();
  let v = baseValue;
  return Array.from({ length: points }, (_, i) => {
    v += (Math.random() - 0.5) * baseValue * 0.03;
    v = Math.max(0, v);
    return {
      tag_id: "t1",
      tag_name: "Temperatura",
      value: +v.toFixed(2),
      unit,
      timestamp: new Date(now - (points - 1 - i) * 10000).toISOString(),
    };
  });
}
