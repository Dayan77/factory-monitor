/**
 * One-line explanation of each KPI (tooltips) + its section in the user manual (docs/manual/indicadores.md, published
 * on the public site under /docs). Definitions follow docs/kpis.md (ISO 22400 / ISO 14224).
 */
export type KpiId =
  | "oee" | "availability" | "performance" | "quality" | "teep" | "micro_stops" | "cadence" | "pareto" | "coverage"
  | "ao" | "mtbf" | "mcbf" | "mttr" | "mttf" | "failure_rate" | "pmp" | "compliance" | "backlog" | "life" | "rul"
  | "predictive_alert" | "condition_rul" | "weibull";

interface KpiDoc { name: string; text: string; anchor: string }

export const KPI_DOCS: Record<KpiId, KpiDoc> = {
  oee: { name: "OEE", anchor: "oee", text: "Eficiência global: Disponibilidade × Desempenho × Qualidade sobre o tempo planejado (ISO 22400). Soma tempos e peças, nunca faz média de porcentagens." },
  availability: { name: "Disponibilidade", anchor: "disponibilidade", text: "Tempo rodando ÷ tempo planejado para produzir. Paradas planejadas (refeição, setup programado) não contam contra." },
  performance: { name: "Desempenho", anchor: "desempenho", text: "Peças produzidas × tempo de ciclo ideal ÷ tempo rodando. Mostra perdas de velocidade e micro-paradas." },
  quality: { name: "Qualidade", anchor: "qualidade", text: "Peças boas ÷ peças produzidas." },
  teep: { name: "TEEP", anchor: "teep", text: "OEE × carga: quanto do calendário inteiro (24 h × 7 dias) virou peça boa." },
  micro_stops: { name: "Micro-paradas", anchor: "micro-paradas", text: "Paradas curtas demais para justificar (abaixo do limite configurado). Entram como perda de desempenho." },
  cadence: { name: "Cadência", anchor: "cadencia", text: "Peças por hora: realizada (sobre o tempo planejado) e rodando (sobre o tempo em operação)." },
  pareto: { name: "Pareto de perdas", anchor: "pareto", text: "Perdas ordenadas por tempo perdido, com o acumulado: ataque primeiro o que soma mais." },
  coverage: { name: "Cobertura de dados", anchor: "cobertura", text: "Parte do período com dados recebidos da máquina. Abaixo de 95 % o número vem com aviso." },
  ao: { name: "Disponibilidade operacional / inerente", anchor: "disponibilidade-operacional", text: "Inerente (Ai) = MTBF ÷ (MTBF + MTTR): só falhas e reparos. Operacional (Ao) inclui também esperas e logística." },
  mtbf: { name: "MTBF", anchor: "mtbf", text: "Tempo médio entre falhas: horas de operação ÷ número de falhas (ISO 14224). Maior é melhor." },
  mcbf: { name: "MCBF", anchor: "mcbf", text: "Ciclos médios entre falhas: ciclos executados ÷ falhas. Para equipamentos que se desgastam por ciclo." },
  mttr: { name: "MTTR", anchor: "mttr", text: "Tempo médio para restabelecer: da falha até a máquina voltar a rodar, incluindo espera pelo técnico. Menor é melhor." },
  mttf: { name: "MTTF", anchor: "mttf", text: "Vida média até a falha de componentes trocados (não reparáveis)." },
  failure_rate: { name: "Taxa de falha", anchor: "taxa-de-falha", text: "Falhas por hora de operação (o inverso do MTBF)." },
  pmp: { name: "PMP", anchor: "pmp", text: "Percentual de manutenção planejada: horas de OS preventivas e preditivas ÷ horas totais de manutenção." },
  compliance: { name: "Cumprimento da programação", anchor: "cumprimento", text: "OS programadas para a semana que foram concluídas na semana (encaixes e substituídas ficam fora)." },
  backlog: { name: "Backlog", anchor: "backlog", text: "Horas de trabalho pendentes ÷ capacidade semanal da equipe = semanas de serviço acumulado." },
  life: { name: "% de vida consumida", anchor: "vida", text: "Uso acumulado do componente ÷ vida nominal (ciclos, horas ou partidas: o que vier primeiro)." },
  rul: { name: "Vida remanescente (RUL)", anchor: "rul", text: "Uso que ainda resta até a vida nominal e a data projetada pelo ritmo de uso recente." },
  predictive_alert: { name: "Alerta preditivo", anchor: "alerta-preditivo", text: "Sinal da máquina saiu do comportamento normal; a severidade diz quão perto está do limite." },
  condition_rul: { name: "RUL de condição", anchor: "rul-condicao", text: "Tempo estimado até o sinal atingir o limite de alarme, pela tendência atual." },
  weibull: { name: "Weibull", anchor: "weibull", text: "Distribuição da vida de componentes por fabricante e modelo: forma (β) diz se a falha é precoce, aleatória ou por desgaste." },
};

const SITE = (import.meta.env.VITE_SITE_URL ?? "https://factoryconnect.tech").replace(/\/+$/, "");

/** Link to the manual section of a KPI on the public site (docs section). */
export function kpiManualUrl(id: KpiId): string {
  return `${SITE}/docs/indicadores#${KPI_DOCS[id].anchor}`;
}
