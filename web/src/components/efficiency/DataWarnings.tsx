import type { ReactNode } from "react";
import { formatInt, formatPct } from "@/utils/format";

interface Props {
  dataCoverage?: number | null;
  productUnknown?: boolean;
  calendarDefault?: boolean;
  idealCycleSuspect?: boolean;
  reason?: string | null;
  /** S3-7: parts made outside the calendar (total). */
  producedOutside?: number | null;
  /** D7: counts of an hour only partly inside the period/shift were split pro rata. */
  countsProrated?: boolean;
}

/** Coverage below this shows the "dados incompletos" warning. */
export const COVERAGE_WARN = 0.95;

function Warn({ title, children }: { title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3 rounded-lg border border-warn-solid/40 bg-warn-solid/10 px-3 py-2.5">
      <svg viewBox="0 0 20 20" fill="currentColor" className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden="true">
        <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 6a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 6zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2z" clipRule="evenodd" />
      </svg>
      <div className="min-w-0">
        <p className="text-caption font-semibold text-fg">{title}</p>
        <p className="text-caption text-fg-muted">{children}</p>
      </div>
    </li>
  );
}

/** Explicit caveats on a KPI: never present a number as more reliable than it is. */
export default function DataWarnings({
  dataCoverage, productUnknown, calendarDefault, idealCycleSuspect, reason, producedOutside, countsProrated,
}: Props) {
  const items: ReactNode[] = [];
  if (reason === "no_planned_time") {
    items.push(<Warn key="pbt" title="Sem tempo planejado no período">Não há turno programado (PBT = 0), então o OEE não é calculado.</Warn>);
  }
  if (reason === "insufficient_data") {
    items.push(<Warn key="ins" title="Dados insuficientes">Sem leituras suficientes das máquinas no período para calcular o OEE — nenhum valor é estimado.</Warn>);
  }
  if (dataCoverage != null && dataCoverage < COVERAGE_WARN) {
    items.push(
      <Warn key="cov" title={`Cobertura de dados: ${formatPct(dataCoverage)}`}>
        Parte do tempo programado ficou sem leitura das máquinas; esse tempo sai do cálculo e os indicadores podem não representar o período inteiro.
      </Warn>,
    );
  }
  if (productUnknown) {
    items.push(
      <Warn key="prod" title="Produto não informado">
        Houve produção sem ordem/produto ativo — o desempenho usa o tempo de ciclo ideal do produto padrão da máquina. Use “Trocar produto” no tablet do operador.
      </Warn>,
    );
  }
  if (idealCycleSuspect) {
    items.push(
      <Warn key="pri" title="Tempo de ciclo ideal suspeito">
        O desempenho passou de 100%: o tempo de ciclo ideal cadastrado provavelmente está maior que o real. Revise em Configurações → Produtos.
      </Warn>,
    );
  }
  if (producedOutside != null && producedOutside > 0) {
    items.push(
      <Warn key="out" title={`Produção fora do calendário: ${formatInt(producedOutside)} peças`}>
        Peças contadas fora de qualquer turno não entram no desempenho, na qualidade nem no OEE (entrariam sem o tempo correspondente). Cadastrar turno extra?
      </Warn>,
    );
  }
  if (countsProrated) {
    items.push(
      <Warn key="prorata" title="Contagem proporcional">
        As peças são registradas por hora cheia; o período (ou o turno) começa ou termina no meio de uma hora, então parte da contagem dessa hora foi estimada proporcionalmente.
      </Warn>,
    );
  }
  if (calendarDefault) {
    items.push(
      <Warn key="cal" title="Calendário padrão">
        Nenhum turno cadastrado para a planta: o cálculo considera 24 h programadas. Cadastre os turnos em Configurações.
      </Warn>,
    );
  }
  if (!items.length) return null;
  return <ul aria-label="Avisos sobre os dados" className="space-y-2">{items}</ul>;
}
