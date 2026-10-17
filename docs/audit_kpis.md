# FactoryConnect — Auditoria independente dos KPIs (Sprint 6)

Auditoria do código que calcula os indicadores contra a especificação normativa
([`kpis.md`](kpis.md), [`domain.md`](domain.md), [`predictive.md`](predictive.md), [`reports.md`](reports.md) §2).
Feita pelo papel `reliability-engineer`, por leitura do código e execução das funções puras do servidor.
É insumo para o `code-reviewer` e para a sprint final de endurecimento.

- **Escopo lido**: `server/src/services/oee/` (kpi, engine, service, detection, calendar), `server/src/services/usage/`
  (core, queries, topology), `server/src/services/maintenance/` (core, kpis, service), `server/src/services/predictive/`
  (core, evaluation, defaults) e `server/src/services/reports/` (weibull, life). Versão: a árvore de trabalho do
  branch do Sprint 6 em 07/10/2026.
- **Parte executável**: [`docs/scenarios/test_audit_kpis.py`](scenarios/test_audit_kpis.py) (roda com
  `make test-golden`). (1) Passa **todos** os casos de `kpi-test-cases` (kpis.md §6) pelas funções do próprio
  servidor; (2) reproduz cada contraexemplo deste documento: o teste `…_code_today` fixa o número que o código dá hoje
  e o teste `…_spec` (marcado `xfail(strict=True)`) afirma o que a especificação exige. Quando um item for corrigido,
  os dois falham de propósito — atualizar o item aqui ("corrigido") e trocar os testes.
- **Fora do escopo**: telas (formatação S3-5 é coberta pelos testes do `web/`), detector incremental com dados
  atrasados (D12, coberto por `server/tests/test_detector_late_rewind.py`), máquina de estados das OS.

## Severidade

| Nível | Critério |
|---|---|
| **Alta** | número errado num indicador principal em uso normal, ou que muda uma decisão (programar ou não uma manutenção) |
| **Média** | número errado ou campo obrigatório (DEVE) ausente num caso de borda plausível; indefinido mostrado como número |
| **Baixa** | caso raro, diferença de arredondamento, rotulagem ou contagem auxiliar; não muda o KPI principal |
| **Info** | lacuna da especificação, decisão de implementação não documentada ou cobertura de teste |

## Resumo

| # | Item | Severidade | Onde | Teste |
|---|---|---|---|---|
| A-1 | ✅ **corrigido** — Ao ignorava a preventiva `PLN-03` programada no calendário (não entrava em D_eq) | **Alta** | `server/src/services/oee/engine.py:components` | `test_A1_*` |
| A-2 | ✅ **corrigido (Sprint 7, S7-2)** — Ritmo da RUL nominal dividia pelos dias desde `installed_at`, não pelos dias com dados | Média (lacuna de spec) | `server/src/services/usage/queries.py:component_usage_detail` | `test_A2_rul_rate_uses_the_days_with_data_spec`, `server/tests/test_audit_s7.py::test_A2_nominal_rul_uses_days_with_data` |
| A-3 | ✅ **corrigido** — Ritmo dos planos sempre ÷ 14, sem a regra "< 14 dias → dias disponíveis + `low_confidence`" | **Alta** | `server/src/services/maintenance/service.py:evaluate_plans` | `test_A3_*` |
| A-4 | ✅ **corrigido (S7-1)** — MTBF = 0 h e Ai = 0 quando há falhas mas tempo de operação 0 | Média | `server/src/services/oee/kpi.py:reliability`, `server/src/services/maintenance/core.py:reliability_row` | `test_A4_mtbf_zero_without_operating_time_spec`, `test_audit_s7.py::test_A6_A20_*` |
| A-5 | ✅ **corrigido** — Pareto com PBT = 0 devolvia 3 linhas de 0 min com `pct = null` | Média | `server/src/services/oee/kpi.py:pareto` | `test_A5_pareto_without_planned_time_spec` |
| A-6 | ✅ **corrigido (S7-10)** — `failures_outside_production` ausente das respostas de confiabilidade; relatório usava *proxy* | Média | `server/src/services/oee/service.py:reliability_for`, `server/src/services/maintenance/kpis.py:reliability`, `server/src/services/reports/builders.py:build_monthly_maintenance` | `test_A6_failures_outside_production_in_reliability_spec`, `test_audit_s7.py::test_A6_A20_reliability_apis_outside_production_and_mart` |
| A-7 | ✅ **corrigido (S7-11)** — `estimated` fixo em `false` com ciclos estimados | Média | `server/src/services/maintenance/kpis.py:reliability` | `test_audit_s7.py::test_A7_estimated_cycles_flag_on_reliability_rows` |
| A-8 | ✅ **corrigido (S7-12)** — λc (falhas por 10⁶ ciclos) não era devolvido | Baixa | `server/src/services/maintenance/core.py:reliability_row` | `test_A8_failure_rate_per_million_cycles_spec` |
| A-9 | ✅ **corrigido (S7-6)** — MTTF simples somava falhas com idade 0 | Baixa | `server/src/services/maintenance/core.py:mttf_row` | `test_A9_mttf_excludes_zero_age_failure_spec` |
| A-10 | ✅ **corrigido (S7-5)** — Ritmo da RUL de condição rateava a hora corrente (ao vivo ≠ recálculo) e usava janela diferente da nominal | Baixa | `server/src/services/predictive/core.py:make_rate_fn` | `test_A10_condition_rate_live_equals_recompute_spec` |
| A-11 | ✅ **corrigido (S7-8)** — OS concluída depois do fim do período contava como "atrasada" | Baixa | `server/src/services/maintenance/core.py:schedule_compliance` | `test_A11_schedule_compliance_completed_after_period_spec` |
| A-12 | ✅ **corrigido (S7-7)** — Falha no instante exato da troca dependia da ordem da consulta | Baixa | `server/src/services/maintenance/kpis.py:attribute_component` | `test_A12_failure_at_replacement_instant_spec` |
| A-13 | ✅ **corrigido (S7-4)** — PRI da linha: produto sem PRI no gargalo usava o PRI da saída sem aviso | Info | `server/src/services/oee/service.py:load_units` | `test_A13_line_pri_fallback_flagged_spec` |
| A-14 | ✅ **corrigido (S7-9)** — Ao: falha sobreposta a parada planejada declarada descontada duas vezes | Baixa | `server/src/services/oee/engine.py:components` | `test_A14_ao_overlap_counted_twice_spec`, `test_A14_ao_partial_overlap_spec` |
| A-15 | ✅ **corrigido (S7-3)** — Desempenho `null` sem `reason` quando APT = 0 e há produção | Baixa | `server/src/services/oee/kpi.py:oee` | `test_A15_performance_null_without_reason_spec` |
| A-16 | ✅ **corrigido (S7-15)** — Horas de operação da hora corrente usavam o relógio de parede | Baixa | `server/src/services/oee/engine.py:operating_seconds` | `test_audit_s7.py::test_A16_operating_seconds_uses_the_given_now` |
| A-17 | ✅ **resolvido** — `test_kpi_spec_cases.py` conferia os casos contra uma reimplementação | Info | `server/tests/test_kpi_spec_cases.py` | `test_conformance` |
| A-18 | ✅ **corrigido (S7-2)** — Risco condicional aceitava ritmo de componente instalado há < 3 dias sem `low_confidence` | Baixa | `server/src/services/reports/life.py:reliability_life` | `test_audit_s7.py::test_A18_conditional_risk_pace_needs_three_days` |
| A-19 | ✅ **corrigido (S7-13)** — Mapa prometia cadência ao vivo e mostrava PQ/PBT do turno / estimativa por buckets | Média | `server/src/services/layout/service.py:shift_kpis` | `test_audit_s7.py::test_A19_live_cadence_from_raw_counter_readings`, `::test_A19_map_marks_cadence_source` |
| A-20 | ✅ **corrigido (S7-14)** — MART não era calculado | Baixa | `server/src/services/maintenance/core.py:reliability_row`, `server/src/services/oee/kpi.py:reliability` | `test_A20_mart_spec`, `test_audit_s7.py::test_A6_A20_*` |

**Situação após o Sprint 7**: todos os itens fechados. As decisões de especificação que faltavam (A-2, A-10, A-13,
A-15, A-19, A-20 etc.) estão em [`kpis.md`](kpis.md) "Decisões Sprint 7" (S7-1 … S7-16); os testes `…_code_today`
foram removidos e os `…_spec` passam sem `xfail` (`docs/scenarios/test_audit_kpis.py`); os casos que precisam de
banco/InfluxDB estão em `server/tests/test_audit_s7.py`.

Nenhum item crítico (fórmula central errada no caso normal): **todas** as fórmulas de OEE/A/P/Q/TEEP, micro-paradas,
cadência, Pareto, MTBF/MCBF/MTTR/MTTF, PMP, cumprimento, backlog, % de vida, RUL, "o que vier primeiro", contadores,
preditiva e Weibull conferem com os exemplos numéricos da spec (seção "Inventário"). As divergências estão em
**como as entradas são montadas** (A-1, A-2, A-3, A-14) e em **casos de borda** (A-4, A-5, A-15).

---

## Divergências e casos de borda

### A-1 — Ao sem a preventiva programada no calendário · **Alta** · ✅ corrigido

> **Corrigido** (Sprint 6, antes do merge): `engine.components` soma a D_eq a união das pausas de calendário com
> código de equipamento (`PLN-03`/`MNT-02`) ∩ POT com os eventos `EQP-*`/`PLN-03`/`MNT-02` (sem contar duas vezes).
> Contraexemplo agora dá Ao = 0,822222; `test_A1_ao_calendar_preventive_spec` passa sem `xfail`.

- **Spec**: kpis.md §2.6 — `D_eq` = falhas `EQP-*` + manutenção `PLN-03` e `MNT-02`; `T_req` = POT − `PLN-01/02/04/05`.
  Exemplo R6: preventiva de 6 h **está** em D_eq (Ao = 0,98). S3-4: `PLN-03` só existe se estiver no calendário antes
  do turno — ou seja, o caso normal de `PLN-03` é a exceção de calendário `planned_stop`.
- **Código**: `server/src/services/oee/engine.py:components` soma em `ao_equipment_s` só os **eventos** `EQP-*`/`PLN-03`/`MNT-02`
  interceptados com `sched` (POT menos as pausas). A exceção `planned_stop PLN-03` do calendário vira pausa
  (`server/src/services/oee/calendar.py:Calendar.day_instances` → `Break(reason_code="PLN-03")`), sai de `sched` e por isso
  **nunca** entra em D_eq; também não sai de T_req (correto). Um evento `PLN-03` declarado sobre a janela do calendário
  também cai fora de `sched`.
- **Contraexemplo** (`test_A1_*`): turno A 06:00–14:00, refeição `PLN-01` 30 min, preventiva `planned_stop PLN-03`
  08:00–09:00, falha `EQP-03` 10:00–10:20.
  Spec: T_req = 480 − 30 = 450 min; D_eq = 20 + 60 = 80 → **Ao = 370/450 = 0,822222**.
  Código: D_eq = 20 → **Ao = 430/450 = 0,955556** (+13,3 pp).
- **Impacto**: Ao aparece no relatório mensal de manutenção e na tela Gestão; toda planta que programa preventiva
  do jeito certo (no calendário) tem Ao superestimado. O OEE não é afetado (a pausa é PDOT, correto).
- **Correção sugerida**: em `components`, somar a `ao_equipment_s` a interseção com o POT das pausas cujo
  `reason_code` é `PLN-03` (e de eventos `PLN-03`/`MNT-02` sobre elas, sem contar duas vezes — união de intervalos).
  O teste unitário atual (`test_oee_core.py`) só passa `ao_equipment_s` pronto para `availability_operational`.

### A-2 — Ritmo da RUL nominal com data de instalação antiga · Média (lacuna da especificação) · ✅ corrigido

> **Corrigido** (Sprint 7, kpis.md S7-2): `server/src/services/usage/queries.py:component_usage_detail` usa
> `server/src/services/usage/core.py:rate_window` com `installed_at` e o início dos dados (`server/src/services/usage/queries.py:component_data_since`);
> a mesma regra na RUL de condição (`make_rate_fn(data_since=…)`) e no risco condicional (A-18). Contraexemplo: 70 dias,
> 14/12/2026, `low_confidence`.

- **Spec**: kpis.md §4.2 — "menos de 14 dias de **histórico** → usar os dias disponíveis (mínimo 3) e
  `low_confidence = true`". O esclarecimento C17 concretiza "histórico" como dias desde `installed_at`.
- **Código**: `server/src/services/usage/queries.py:component_usage_detail` usa `history_days = agora − installed_at` e
  `server/src/services/usage/core.py:rul` divide o uso da janela por `min(14, history_days)` — exatamente C17.
- **O problema**: o CSV de ativos do assistente (`server/src/services/onboarding/assets_csv.py`) grava
  `installed_at = agora`, então nesse caminho a regra funciona. Mas o cadastro de componente pela API/tela aceita
  `installed_at` no passado (o natural para um cilindro que está na máquina há um ano, e para manter o histórico de
  trocas certo). Aí os dados começam no dia em que o gateway entra, e nos primeiros 14 dias o uso da janela cobre só
  os dias com dados e é dividido por 14.
- **Contraexemplo** (`test_A2_*`): cilindro U3 (vida 5 000 000, usado 3 600 000), instalado há 400 dias, gateway
  online há 5 dias com 20 000 ciclos/dia → janela = 100 000 ciclos.
  Código: ritmo 100 000 / 14 = 7 142,9/dia → **196 dias, 19/04/2027, `low_confidence = false`**.
  Com os 5 dias de dados: 20 000/dia → **70 dias, 14/12/2026, `low_confidence = true`**. A RUL sai 2,8× mais longa,
  sem aviso.
- **Decisão pedida ao dono do produto** (proposta do `reliability-engineer`): "dias de histórico" =
  `min(14, agora − installed_at, agora − início dos dados da posição)`, onde início dos dados = primeiro bucket de
  uso da posição (ou `data_since` do gateway). Vale para a RUL nominal, os planos (A-3), a RUL de condição (P-7) e o
  risco condicional do Weibull (A-18). Até lá o manual avisa o administrador (manual/administrador.md, "Primeiros
  14 dias").
- **Relacionado**: componente importado pelo CSV começa com uso 0 (a idade real anterior é desconhecida) — o % de
  vida fica subestimado até a primeira troca. Sugestão para o assistente: coluna opcional "uso já acumulado" no CSV de
  ativos.

### A-3 — Planos: ritmo sempre ÷ 14 · **Alta** · ✅ corrigido

> **Corrigido** (Sprint 6, antes do merge): `evaluate_plans` passa `window_days` = min(14, agora − 1º bucket de uso
> da posição/máquina) (`maintenance/usage.py:data_days`); `< 3` dias → sem projeção por uso
> (`reason = insufficient_data`); `< 14` → `low_confidence = true` em `PlanStatus` e em `/api/maintenance-plans/status`.
> Contraexemplo: 15 dias, 20/10/2026. A regra de "dias com dados" para a RUL nominal (A-2) continua pendente.

- **Spec**: kpis.md §4.2 — "Para planos, o mesmo cálculo …" com as mesmas bordas (dias disponíveis, mínimo 3,
  `low_confidence`). S4-3: `dias = ceil((intervalo − uso)/ritmo − ε)` com ritmo de 14 dias corridos.
- **Código**: `server/src/services/maintenance/service.py:evaluate_plans` busca a janela de 14 dias
  (`server/src/services/maintenance/usage.py:rate_window`) e chama `server/src/services/maintenance/core.py:compute_plan_status` sem
  `window_days` (padrão 14). A função pura aceita `window_days`, mas ninguém passa; `PlanStatus` não tem
  `low_confidence`.
- **Contraexemplo** (`test_A3_*`): o U4 (plano 2 000 000 ciclos ou 180 dias; 1 700 000 ciclos e 60 dias desde a troca;
  hoje 05/10/2026) numa posição com dados há 5 dias a 20 000/dia (100 000 na janela).
  Código: ritmo 7 142,9/dia → **vence em 42 dias, 16/11/2026**. Real: **15 dias, 20/10/2026**. O planejador vê a
  data 27 dias depois do vencimento real e não programa a troca.
- **Correção**: passar os dias de dados (mesma regra de A-2, mínimo 3) e expor `low_confidence` no plano.

### A-4 — MTBF = 0 sem tempo de operação · Média · ✅ corrigido

> **Corrigido** (S7-1): MTBF/Ai/λ `null`, `reason = insufficient_data`; MTTR 15 min mantido.

- **Spec**: kpis.md §0 — indefinido → `null` com `reason`, nunca 0 nem infinito; §2.5: tempo = 0 → λ `null`.
  MTBF = tempo/n com tempo desconhecido é indefinido (não "a máquina falha a toda hora").
- **Código**: `server/src/services/oee/kpi.py:reliability` e `server/src/services/maintenance/core.py:reliability_row`
  devolvem `mtbf_h = 0.0` (e `availability_inherent = 0.0`) quando `operating_s = 0` e `n > 0`, enquanto
  `failure_rate_per_h` sai `null` — os dois campos se contradizem.
- **Contraexemplo** (`test_A4_*`): máquina sem `run_state` nem contador de ciclos (D8), 2 falhas no período, uma de
  10 min e uma de 20 min. Código: **MTBF 0,0 h, Ai 0,0**, MTTR 15 min. Esperado: MTBF `null`
  (`reason = insufficient_data`), Ai `null`, MTTR 15 min.
- **Onde aparece**: máquina recém-cadastrada sem tag de operação; posição de máquina sem `run_state` próprio na
  tela Confiabilidade.

### A-5 — Pareto vazio quando não há tempo planejado · Média · ✅ corrigido

> **Corrigido**: `kpi.pareto` devolve `[]` com PBT = 0 (também `by=category`).

- **Spec**: kpis.md S3-1 — "Pareto com PBT = 0: **lista vazia** (sem linhas de 0 min com `pct = null`)".
- **Código**: `server/src/services/oee/kpi.py:pareto` sempre inclui as três pseudo-perdas (`micro_stops`, `speed_loss`,
  `quality_loss`); com PBT = 0 elas saem com 0 min e `pct = null`. `GET /api/losses/pareto` e o relatório de turno
  repassam a lista sem filtrar.
- **Contraexemplo** (`test_A5_*`): feriado (POT 60 min, todo PDOT) → 3 linhas `{duration_s: 0, pct: null}`;
  esperado `[]`. A UI e o relatório PDF precisam tratar "—" em vez de "sem perdas".
- **Correção**: em `pareto`, `if c.pbt_s <= 0: return []`.

### A-6 — `failures_outside_production` · Média · ✅ corrigido

> **Corrigido** (S7-10): `server/src/services/oee/engine.py:outside_production` (calendário + paradas planejadas
> declaradas) nas duas APIs; o relatório mensal lê o campo da API (proxy removido).

- **Spec**: kpis.md S5-1c item 6 — "A resposta de confiabilidade **DEVE** trazer `failures_outside_production` (n das
  falhas cujo `failed_at` cai fora do tempo produtivo — pausa ou fora do turno)". No `break_fault_shift`: 3.
- **Código**: o campo não existe em `GET /api/reliability` (`server/src/services/oee/service.py:reliability_for`) nem em
  `GET /api/maintenance/reliability` (`server/src/services/maintenance/kpis.py:reliability`). O relatório mensal
  (`server/src/services/reports/builders.py:build_monthly_maintenance`, seção "Maus atores") calcula uma coluna com esse nome
  pelo *proxy* "falha `critical`/`degraded` **sem** `downtime_event_id`".
- **Contraexemplos do proxy**: (a) F2 do `break_fault_shift` (falha às 10:05, dentro do café 10:00–10:15) tem
  evento (10:15–10:40) → o proxy **não** a conta: dá 2, a spec dá **3**; (b) falha achada em preventiva às 09:00, em
  pleno turno, `detection_method = periodic_maintenance` (sem parada, domain.md §5.5) → o proxy a conta como "fora do
  tempo produtivo".
- **Correção**: calcular pelo calendário (`productive_at(cal, failed_at)` de `oee/detection.py`) nas duas APIs e
  reutilizar no relatório.

### A-7 — Ciclos estimados apresentados como medidos · Média · ✅ corrigido

> **Corrigido** (S7-11): `estimated` pela topologia (posição sem tag de ciclo) nas linhas por posição, tipo e
> fabricante/modelo; `false` explícito por máquina.

- **Spec**: kpis.md §2.2 — ciclos estimados por peças × `cycles_per_part` → `estimated = true`; C14; domain.md §2.
- **Código**: `server/src/services/usage/topology.py:load_topology` grava ciclos estimados para posições sem tag de ciclo,
  e as rotas de posição/máquina devolvem `cycles_estimated`. Mas `server/src/services/maintenance/kpis.py:reliability`
  (linhas por posição) fixa `"estimated": False`, e as linhas por máquina/tipo/fabricante não têm o campo.
- **Contraexemplo**: garra G1 sem sensor, `cycles_per_part = 2`, 700 peças → 1 400 ciclos estimados; 1 falha →
  MCBF 1 400 ciclos exibido sem o selo "estimado" na tela Confiabilidade e no relatório mensal.

### A-8 — λ por ciclos não exposto · Baixa · ✅ corrigido

> **Corrigido** (S7-12): `failure_rate_per_million_cycles` em `reliability_row`.

- **Spec**: kpis.md §2.5 — `λc = n / ciclos × 10⁶`; caso `failure_rate`: 3 / 2 400 000 → **1,25 por 10⁶ ciclos**.
- **Código**: `server/src/services/maintenance/core.py:reliability_row` devolve só `failure_rate_per_h`. Como MCBF existe,
  λc = 10⁶ / MCBF pode ser derivado na UI, mas a spec manda o servidor calcular (S3-5: nada recalculado na UI).

### A-9 — MTTF simples com idade 0 · Baixa · ✅ corrigido

> **Corrigido** (S7-6): idade ≤ 0 excluída, `excluded_no_usage` / `mttf_excluded_no_usage`; contraexemplo 9 400 h.

- **Spec**: kpis.md §2.4 (média das idades na falha). reports.md §2.1 exclui `t ≤ 0` (`no_usage`) do Weibull.
- **Código**: `server/src/services/maintenance/core.py:mttf_row` usa `usage_since_install.get(m, 0.0)` — componente que
  falhou sem uso registrado (importado no CSV, ou removido antes de o gateway existir) entra com idade 0.
- **Contraexemplo** (`test_A9_*`): rolamentos com falha em 8 200, 9 600, 10 400 h e um quarto removido por falha sem uso
  registrado → código **7 050 h**; com a regra do Weibull (excluído, listado) **9 400 h**. O mesmo grupo mostraria
  MTTF simples 7 050 e MTTF Weibull ~9 500 no relatório de vida.
- **Correção**: excluir idade ≤ 0 também no MTTF simples e devolver `excluded_no_usage`.

### A-10 — Ritmo da RUL de condição · Baixa · ✅ corrigido

> **Corrigido** (S7-5): horas completas `[hora(t) − 14 d, hora(t))` em `make_rate_fn`, na RUL nominal e nos planos
> (`server/src/services/maintenance/usage.py:rate_window`); ao vivo = recálculo = nominal = 20 000/dia.

- **Spec**: predictive.md P-7 — ritmo "como kpis.md §4.2/C17"; C17: buckets horários com início ≥ hora cheia de
  `agora − 14 d`, ÷ 14. P-12: recalcular dá o mesmo resultado.
- **Código**: `server/src/services/predictive/core.py:make_rate_fn` rateia os buckets nas bordas pela sobreposição. Ao
  vivo, o bucket da hora corrente contém só o uso até agora, e o rateio o conta pela metade (às 10:30, metade de
  30 min de uso); no recálculo o mesmo bucket está completo. A RUL nominal (C17) conta o bucket da borda inteiro.
- **Contraexemplo** (`test_A10_*`): uso uniforme de 20 000 ciclos/dia, avaliação às 10:30: condição **19 985,12/dia**,
  nominal **20 029,76/dia** (e o recálculo dessa mesma janela dá 20 000). Diferença ≤ 0,25 %: só muda `ceil` de dias
  em casos de fronteira, mas quebra a igualdade ao vivo × recálculo de P-12 nesses casos.

### A-11 — "Atrasada" × "não feita" · Baixa · ✅ corrigido

> **Corrigido** (S7-8): concluída após o fim do período = não feita (`late = 0, not_done = 1`).

- **Spec**: domain.md S4-6 — "concluída depois = atrasada; **não concluída até o fim do período = não feita**".
- **Código**: `server/src/services/maintenance/core.py:schedule_compliance` classifica pelo `completed_at` atual, mesmo se
  posterior ao período.
- **Contraexemplo** (`test_A11_*`): OS programada para 30/09, período = setembro, concluída em 02/10, relatório gerado
  em 05/10 → código: `late = 1, not_done = 0`; spec: `late = 0, not_done = 1`. O índice (34/40 etc.) não muda, mas a
  contagem muda conforme o dia em que o relatório é gerado (fere o princípio 4 de reports.md, reprodutibilidade).

### A-12 — Falha no instante da troca · Baixa · ✅ corrigido

> **Corrigido** (S7-7): intervalo semiaberto, ordem determinística → "new".

- **Spec**: domain.md S4-2/S4-9 — os intervalos de instalação nunca se sobrepõem; a falha vai para o componente
  instalado em `failed_at`.
- **Código**: `server/src/services/maintenance/kpis.py:attribute_component` usa `installed_at ≤ failed_at ≤ removed_at`
  (fechado nas duas pontas) e devolve o primeiro da lista (ordem da consulta, não determinística).
- **Contraexemplo** (`test_A12_*`): troca concluída às 10:00 e falha registrada às 10:00 → "old" ou "new" conforme a
  ordem. Proposta: intervalo semiaberto `[installed_at, removed_at)` → "new".

### A-13 — PRI da linha sem o produto no gargalo · Info · ✅ corrigido

> **Corrigido** (S7-4): `line_pri_fallback = true` no OEE da linha/planta quando o PRI veio da saída
> (`server/src/services/oee/service.py:load_units`).

`server/src/services/oee/service.py:load_units` usa `{**PRI da saída, **PRI do gargalo}`: produto sem PRI cadastrado no
gargalo usa, sem aviso, o PRI da máquina de saída. kpis.md §1.6 diz "PRI da linha = PRI do gargalo para o produto" e
não trata a ausência. Recomendação: documentar a regra (fallback = saída) e devolver `line_pri_fallback = true`.

### A-14 — Ao: sobreposição contada duas vezes · Baixa · ✅ corrigido

> **Corrigido** (S7-9): D_eq exclui a parte sob parada planejada declarada `PLN-01/02/04/05`; Ao = 1,0.

- **Spec**: S3-4 — parada não planejada sobreposta a uma planejada perde a parte sobreposta (a planejada prevalece);
  §2.6.
- **Código**: `server/src/services/oee/engine.py:components` desconta a reunião `PLN-02` de T_req (`ao_excluded_s`) e soma a
  falha sobreposta a D_eq (`ao_equipment_s` usa o evento não planejado inteiro ∩ `sched`). O ADOT do OEE está certo.
- **Contraexemplo** (`test_A14_*`): DDS `PLN-02` declarado 10:00–10:30 sobre falha `EQP-03` 10:00–10:20 → código
  **Ao = 430/450 = 0,955556**; spec **1,0** (a falha não ocupou tempo requerido).

### A-15 — `reason` ausente com APT = 0 · Baixa · ✅ corrigido

> **Corrigido** (S7-3): `reason = no_running_time`.

`server/src/services/oee/kpi.py:oee`: PBT 60 min inteiro em ADOT e 10 peças reveladas pelo contador (S3-9) → `performance
= null`, `reason = null` (`test_A15_*`). kpis.md §0 exige `reason` em todo indefinido; sugerir `no_running_time`
(acrescentar à lista do §0) ou `insufficient_data`.

### A-16 — Relógio de parede nas horas de operação · Baixa · ✅ corrigido

> **Corrigido** (S7-15): `operating_seconds(..., now)`; `reliability_for` passa o `now` do motor.

`server/src/services/oee/engine.py:operating_seconds` limita o bucket da hora corrente com `datetime.now()`, enquanto o resto
do motor recebe `now` (e a manutenção usa `services.clock`). Com o relógio congelado (testes de cenário, demo em
modo acelerado) a hora corrente é tratada como hora cheia e rateada: às 10:30, um bucket com 1 800 s de operação
conta 900 s. Passar `now` como parâmetro.

### A-17 — Casos da spec testados contra reimplementação · Info

`server/tests/test_kpi_spec_cases.py` calcula cada caso de kpis.md §6 com fórmulas escritas no próprio teste; PMP,
cumprimento, backlog, MTTF, % de vida, RUL, plano e λ não passam por ele pelo código do servidor (só indiretamente,
pelos cenários E/F). **Resolvido** nesta auditoria: `docs/scenarios/test_audit_kpis.py::test_conformance` passa os
22 casos pelas funções do servidor (o 23º, ciclos estimados, vive na topologia de uso e é coberto por `server/tests/test_usage_core.py`) e `test_every_kpi_case_is_audited` falha se um caso novo for acrescentado sem
verificação.

### A-18 — Risco condicional com instalação muito recente · Baixa · ✅ corrigido

> **Corrigido** (S7-2): < 3 dias → `rate_per_day = null`, `p_reason = insufficient_data`; < 14 → `low_confidence`.

`server/src/services/reports/life.py:reliability_life` usa `days = min(14, desde a instalação)` sem o mínimo de 3 dias de
kpis.md §4.2 nem `low_confidence`. Um componente instalado há 2 h com a partida rápida da linha entra com o ritmo de
2 h extrapolado para 30 dias na lista "maior probabilidade de falhar". Aplicar a regra de A-2.

### A-19 — "Cadência ao vivo" no mapa · Média · ✅ corrigido

> **Corrigido** (S7-13): `server/src/services/layout/service.py:live_parts` lê as leituras brutas do contador no
> InfluxDB (`cadence_source = counter`; sem leituras → `buckets`). Contraexemplo: 60 pç/h.

sprint6.md §1.2 (estado `running`: "cadência ao vivo (§1.9 de kpis.md)") e §1.4. kpis.md §1.9: cadência ao vivo =
peças dos últimos 15 min × 4. `server/src/services/layout/service.py:shift_kpis` devolve `cadence_per_hour =
cadence_planned_per_hour` = PQ/PBT **do turno até agora**. Contraexemplo: turno desde 06:00, 400 peças até 09:45
(100/h), e a máquina rodando a meia velocidade desde então (15 peças em 09:45–10:00) → o mapa mostra **100 pç/h**;
ao vivo seria **60 pç/h**. A UI rotula só "Cadência" (não engana sobre ser ao vivo), mas não cumpre a spec. Ou
implementar a janela de 15 min, ou corrigir a spec para "cadência do turno".

### A-20 — MART · Baixa · ✅ corrigido

> **Corrigido** (S7-14): `mart_s`/`mart_min`, `repairs_timed`, `mart_reason` nas duas APIs de confiabilidade.

kpis.md §2.3 define o MART (média de `repair_finished_at − repair_started_at`) como auxiliar "quando apontado". Os
campos existem (`FailureRecord`), `repair_started_at` é preenchido ao iniciar a OS, mas nenhuma API calcula o MART. Sem
ele não dá para separar espera (peça/técnico) de reparo — a pergunta "o MTTR é alto por falta de peça?".

---

## Inventário: cada fórmula, onde está e se confere

Legenda: ✅ confere exatamente com a spec e o exemplo numérico; ⚠️ confere na fórmula, com ressalva (item A-n);
❌ diverge. "Teste" = onde a conformidade com o exemplo da spec é verificada pelo código do servidor.

### Produção (kpis.md §1)

| Indicador | Fórmula da spec | Implementação | Situação | Teste |
|---|---|---|---|---|
| POT | soma das instâncias de turno no período | `server/src/services/oee/engine.py:components` (`md.cal.instances`) + `server/src/services/oee/calendar.py:Calendar.day_instances` | ✅ (C19, D9, D11, S3-6) | `test_oee_core.py`, cenário D |
| PDOT | pausas + `planned_stop` + eventos planejados ∩ POT, sem contar duas vezes | `server/src/services/oee/engine.py:components` (`brk`, `planned_u`) | ✅ (S3-4 união) | cenário D |
| Sem dados | POT fora de pausas/paradas sem leitura válida | `server/src/services/oee/engine.py:components` (`nodata_s`) | ✅ D5 | `test_oee_api.py` |
| PBT | `POT − PDOT − sem dados` | `server/src/services/oee/kpi.py:Components.pbt_s` | ✅ | E1 |
| ADOT | eventos não planejados ∩ tempo produtivo | `server/src/services/oee/engine.py:components` (`unplanned_u`) | ✅ (S5-1: só a parte produtiva) | `test_break_fault_s5.py` |
| APT | `PBT − ADOT` | `server/src/services/oee/kpi.py:Components.apt_s` | ✅ | E1 |
| A | `APT / PBT`; PBT = 0 → `null` (`no_planned_time` / `insufficient_data`) | `server/src/services/oee/kpi.py:oee` | ✅ S3-1 | E1, E2 |
| P | `Σ PRI·PQ / APT`, sem truncar, flag `ideal_cycle_time_suspect` | `server/src/services/oee/kpi.py:oee` | ✅ (A-15 corrigido) | E1, E2 |
| Q | `Σ PRI·GQ / Σ PRI·PQ`; PQ = 0 → `null` (`no_production`) | `server/src/services/oee/kpi.py:oee` | ✅ | E1, E2 |
| OEE | `Σ PRI·GQ / PBT` | `server/src/services/oee/kpi.py:oee` | ✅ (PBT > 0 sem produção → 0 real, Q `null`) | E1, E2 |
| Agregação | somar componentes, recalcular | `server/src/services/oee/kpi.py:total`, `server/src/services/oee/service.py:oee_for` (S3-8) | ✅ nunca média | E2 (0,836590 ≠ 0,839524) |
| PRI por produto | `ProductionRun` → produto padrão → PRI da máquina → `insufficient_data` | `server/src/services/oee/engine.py:_production`, `server/src/services/oee/engine.py:product_pri` | ✅ S3-3, D7 | cenário D |
| OEE da linha / planta | saída da linha + PRI do gargalo; planta = Σ linhas | `server/src/services/oee/service.py:resolve_scope`, `server/src/services/oee/service.py:load_units` | ✅ (A-13 corrigido) | `test_drilldown_e2.py` |
| Produção fora do calendário | fora de PQ/GQ e do TEEP | `server/src/services/oee/engine.py:_production` | ✅ S3-7 | cenário D |
| Loading / TEEP | `PBT / calendário`; `Σ PRI·GQ / calendário` | `server/src/services/oee/kpi.py:oee`, `server/src/services/oee/service.py:total_components` (D10) | ✅ (feriado: 0; `insufficient_data`: `null`) | E3 |
| `data_coverage` | (POT com dados ou parada declarada) / POT | `server/src/services/oee/kpi.py:oee`, `server/src/services/oee/engine.py:components` | ✅ D5 | `test_oee_api.py` |
| Micro-parada (run_state) | `debounce ≤ d < limiar`; `d ≥ limiar` → evento | `server/src/services/oee/detection.py:detect`, `server/src/services/oee/detector.py:detect_machine` | ✅ C22 (120 s exatos = evento), C24a (fusão < 10 s) | cenário C |
| Micro-parada (ciclos) | `gap > 2·PRI` → parada `gap − PRI`; `PRI < gap ≤ 2·PRI` → velocidade | `server/src/services/oee/detection.py:classify_cycle_gaps`, `server/src/services/oee/detection.py:detect` (modo `cycle`) | ✅ | E4 |
| Falha curta | falha < limiar → `short_faults`, sem `FailureRecord` (também fora do tempo produtivo) | `server/src/services/oee/detection.py:micro_pieces`, `server/src/services/oee/detection.py:short_offtime_fault` | ✅ C22, S5-1b | `test_break_fault_s5.py` |
| Micro-paradas por hora de APT | `n / APT(h)` | `server/src/services/oee/kpi.py:oee` | ✅ | — |
| Cadência realizada / em operação | `PQ/PBT`, `PQ/APT` | `server/src/services/oee/kpi.py:oee` | ✅ | E5 |
| Cadência ao vivo | peças dos últimos 15 min × 4 | `server/src/services/layout/service.py:shift_kpis` | ✅ (A-19 corrigido) | `test_A19_*` |
| Cadência de bucket | peças / duração do bucket | `server/src/api/routes/lines.py` (`cadence_per_hour`) | ✅ C21 | cenário B |
| Pareto de perdas | motivos + micro + velocidade (`APT − Σ PRI·PQ − micro`, ≥ 0) + qualidade (`Σ PRI·SQ`) | `server/src/services/oee/kpi.py:pareto` | ✅ (A-5 corrigido) | E6 |
| Motivo efetivo | operador → sugerido `EQP-*` → `UNK-01` | `server/src/services/oee/engine.py:effective_reason` | ✅ D2 | `test_oee_api.py` |

### Confiabilidade (kpis.md §2)

| Indicador | Fórmula da spec | Implementação | Situação | Teste |
|---|---|---|---|---|
| n | `critical` + `degraded` por `failed_at`; `incipient` à parte | `server/src/services/oee/kpi.py:reliability`, `server/src/services/maintenance/core.py:reliability_row` | ✅ | R1 |
| Tempo de operação | `Σ run_state = 1` (C23), da posição ou herdado da máquina (C13, D8) | `server/src/services/oee/engine.py:operating_seconds`, `server/src/services/oee/engine.py:run_source`, `server/src/services/usage/core.py:run_step` | ✅ (A-16 corrigido) | cenário C (349,5 min) |
| MTBF | `tempo / n`; n = 0 → `null` + `operating_hours` | idem | ✅ (A-4 corrigido) | R1, `mtbf_no_failures` |
| MCBF | `ciclos / n` | `server/src/services/maintenance/core.py:reliability_row` | ✅ (A-7 corrigido) | R2, cenário E |
| MTTR | `Σ (restored_at − failed_at) / nº restabelecidas` (relógio, S4-10, S5-1) | `server/src/services/oee/kpi.py:reliability`, `server/src/services/maintenance/core.py:reliability_row`, `server/src/services/oee/detection.py:restoration` | ✅ (falha aberta fora; restabelecimento ausente ≠ 0) | R3, `break_fault_shift` (23,75 min) |
| MART | média `repair_finished_at − repair_started_at` | — | ✅ (A-20 corrigido) | `test_A20_mart_spec` |
| MTTF / MCTF | média de `usage_since_install` dos removidos por falha; suspensões à parte | `server/src/services/maintenance/core.py:mttf_row`, `server/src/services/maintenance/core.py:life_measure_of` | ✅ (A-9 corrigido) | R4, cenário E |
| Taxa de falha λ | `n / tempo` (n = 0 → 0; tempo = 0 → `null`) | `server/src/services/oee/kpi.py:reliability`, `server/src/services/maintenance/core.py:reliability_row` | ✅ | R5 |
| λc | `n / ciclos × 10⁶` | — | ✅ (A-8 corrigido) | R5 |
| Ai | `MTBF / (MTBF + MTTR)` | `server/src/services/oee/kpi.py:reliability` | ✅ (A-4 corrigido) | R6 |
| Ao | `(T_req − D_eq) / T_req` | `server/src/services/oee/kpi.py:availability_operational`, `server/src/services/oee/engine.py:components` | ✅ (A-1, A-14 corrigidos) | R6 (função pura), `break_fault_shift` |
| Atribuição a componente | componente instalado em `failed_at` | `server/src/services/maintenance/kpis.py:attribute_component` | ✅ (A-12 corrigido) | cenário E |
| Agregação de grupo | Σ uso ÷ Σ falhas | `server/src/services/maintenance/kpis.py:reliability` | ✅ S4-8, S4-9 | cenário E |
| `failures_outside_production` | n de falhas fora do tempo produtivo | (só no relatório, por proxy) | ✅ (A-6 corrigido) | `test_A6_*` |

### Gestão da manutenção (kpis.md §3)

| Indicador | Fórmula da spec | Implementação | Situação | Teste |
|---|---|---|---|---|
| PMP | horas planejadas / horas totais, OS concluídas no período | `server/src/services/maintenance/core.py:pmp`, `server/src/services/maintenance/core.py:is_planned_work` | ✅ (corretiva sem `urgency` conta como não planejada — Info) | M1, `pmp_with_predictive` (0,9375) |
| Cumprimento | no prazo / programadas (congelamento segunda 00:00, `superseded` fora) | `server/src/services/maintenance/core.py:schedule_compliance` | ✅ (A-11 corrigido) | M2, cenário E |
| Backlog | Σ horas restantes (approved/scheduled/on_hold/in_progress) / capacidade | `server/src/services/maintenance/core.py:backlog`, `server/src/services/maintenance/kpis.py:crew_capacity`, `server/src/services/maintenance/core.py:remaining_hours` | ✅ S4-5, S4-7 | M3, cenário E |
| `predictive_no_defect_hours` | horas de OS preditivas concluídas com `no_defect` | `server/src/services/maintenance/kpis.py:management_kpis`, `server/src/services/predictive/evaluation.py:predictive_no_defect_hours` | ✅ P-9 | cenário F |

### Uso e vida (kpis.md §4, domain.md §3)

| Indicador | Fórmula da spec | Implementação | Situação | Teste |
|---|---|---|---|---|
| Delta de contador | regras 1–5, C1–C7 (rollover módulo `max + 1`, glitch, re-baseline) | `server/src/services/usage/core.py:counter_delta`, `server/src/services/usage/core.py:counter_step`, `server/src/services/usage/core.py:resolve_max_rate` | ✅ | `counter_delta`, cenário A |
| Horas e partidas | `Σ run_state = 1`; partida = 0→1 com 0 ≥ debounce; buraco ≤ timeout | `server/src/services/usage/core.py:run_step` | ✅ C10–C12 | cenário A2 (5 457,2 s) |
| Ciclos estimados | peças totais × `cycles_per_part` | `server/src/services/usage/topology.py:load_topology` | ✅ C14 (flag: A-7) | `cycles_per_part_estimate` |
| % de vida | `max_m(uso_m / L_m)`, sem truncar; sem vida → `null` | `server/src/services/usage/core.py:life_consumed` | ✅ | U1, U2 |
| RUL nominal | `ceil((L − uso) / ritmo14)`, menor data; empate → maior % | `server/src/services/usage/core.py:rul`, `server/src/services/usage/queries.py:component_usage_detail` | ✅ (A-2 corrigido) | U3 |
| Plano "o que vier primeiro" | `consumed = max_i`; estados 0,8/1,0/1+tol; data = mais cedo | `server/src/services/maintenance/core.py:compute_plan_status`, `server/src/services/maintenance/core.py:plan_state` | ✅ (A-3 corrigido) | U4, cenário E |

### Preditiva (predictive.md)

| Indicador | Fórmula da spec | Implementação | Situação | Teste |
|---|---|---|---|---|
| Linha de base | `m0 = mediana`, `σ = max(1,4826·MAD, max(0,01·abs(m0), resolução))`, N = 20 e ≥ 24 h | `server/src/services/predictive/core.py:compute_baseline`, `server/src/services/predictive/core.py:fold_segment` | ✅ P-2 | cenário F |
| z, EWMA, CUSUM | `clip(±4)`, `λ = 0,2`, limite 1,0; `k = 0,5`, `h = 5` | `server/src/services/predictive/core.py:fold_segment`, `server/src/services/predictive/core.py:ewma_limit` | ✅ P-4 | cenário F |
| z robusto móvel | 20 janelas anteriores, > 3,5, 3 em 10 | `server/src/services/predictive/core.py:fold_segment` | ✅ | cenário F |
| Tendência | MQO nas últimas 10 janelas; significativa se `b > 0` e `b/SE > 2` | `server/src/services/predictive/core.py:ols` | ✅ P-5 | cenário F |
| RUL de condição | `(L_f − x̂)/b`, faixa `(L_f − x̂)/(b ± 2SE)`, dias com ritmo alto/baixo | `server/src/services/predictive/core.py:condition_rul` | ✅ P-6 (ritmo: A-10 corrigido) | F1 bloco 50 (11 dias, 8–16) |
| Ritmo e faixa | 14 d ÷ 14; metades de 7 d | `server/src/services/predictive/core.py:make_rate_fn` | ✅ (A-10 corrigido) | F2 dia 58 |
| Plano de produção | 1º dia com uso planejado acumulado ≥ restante | `server/src/services/predictive/core.py:project_with_plan` | ✅ P-7 | `production_plan_example` (10/02/2027) |
| Severidade | estatístico → `watch`; regra → sua; RUL ≤ 30 d → `warning`; `dias_low ≤ 7` → `critical` | `server/src/services/predictive/core.py:severity_of` | ✅ P-8 | cenário F |
| Antecedência, precisão, FA/posição-ano, detecção, cobertura da faixa | P-10 | `server/src/services/predictive/evaluation.py:evaluate` | ✅ | F (0,5 / 1,0; 4,966/ano) |

### Vida por fabricante/modelo — Weibull (reports.md §2)

| Indicador | Fórmula da spec | Implementação | Situação | Teste |
|---|---|---|---|---|
| β, η (MLE com censura) | raiz de `g(β)` por bissecção em [0,05; 50]; `η = (Σ t^β / r)^(1/β)`; r < 3 → `insufficient_failures` | `server/src/services/reports/weibull.py:mle`, `server/src/services/reports/weibull.py:g_score` | ✅ | `weibull_cylinders`, `weibull_bearings_r4`, `weibull_insufficient` |
| IC 90 % (Fisher, log) | Hessiana de §2.3 | `server/src/services/reports/weibull.py:fisher_cov`, `server/src/services/reports/weibull.py:analyze` | ✅ | `weibull_cylinders` |
| MTTF_W, B10, B10/nominal | `η·Γ(1 + 1/β)`; `η·(−ln 0,9)^(1/β)` | `server/src/services/reports/weibull.py:analyze` | ✅ | idem |
| Probabilidade condicional | `1 − R(a + u·D)/R(a)` | `server/src/services/reports/weibull.py:conditional_failure` | ✅ (ritmo: A-18 corrigido) | 0,361250; 0,013788 |
| Papel de Weibull | ranks de Johnson + Bernard | `server/src/services/reports/weibull.py:plot_points` | ✅ | idem |
| Padrão de β | IC < 1, contém 1, > 1 | `server/src/services/reports/weibull.py:pattern` | ✅ | idem |
| Comparação entre marcas | IC de η sem sobreposição | `server/src/services/reports/weibull.py:eta_ci_overlap`, `server/src/services/reports/life.py:compare_groups` | ✅ | `test_reports_core.py` |
| Unidades e censura | falha = removido por `failure`; demais e instalados = suspensão; `t ≤ 0` excluído | `server/src/services/reports/life.py:reliability_life` | ✅ (componente reparado não pode ser reinstalado: a API recusa, 409 — vira um componente novo, coerente com "tão bom quanto novo") | `test_reports_api.py` |

---

## Recomendações para a sprint de endurecimento (ordem sugerida)

1. **A-1** (Ao com preventiva do calendário) e **A-3** (ritmo dos planos) — números errados em uso normal; correção
   local, com os testes `…_spec` prontos.
2. **A-2** — levar a proposta de "dias de histórico = dias com dados" ao dono do produto e aplicar a mesma regra em
   RUL nominal, planos (A-3), RUL de condição e Weibull (A-18). Junto com A-3, afeta todo cliente nos primeiros 14 dias.
3. **A-4, A-5, A-15** — indefinidos com `null` + `reason` (uma linha cada).
4. **A-6, A-7, A-19** — campos e flags que a spec exige e as telas/relatórios precisam para não mostrar dado sem aviso.
5. Demais (Baixa/Info) junto com a revisão de código do módulo.

---

## Verificação Sprint 7

Reverificação independente (papel `reliability-engineer`, 07/10/2026) das correções do Sprint 7 (A-2, A-4 … A-20) e
das correções do Sprint 6 (A-1, A-3, A-5), sobre a árvore de trabalho do branch do Sprint 7 (checkpoint `e8442e5` +
alterações em andamento). Método: (1) leitura do código citado em cada item contra a decisão normativa de
[`kpis.md`](kpis.md) "Decisões Sprint 7"; (2) execução dos testes das correções; (3) **sondas novas**, que tentam
quebrar cada correção com combinações que os testes das correções não cobrem —
[`docs/scenarios/test_audit_verification_s7.py`](scenarios/test_audit_verification_s7.py).

Execuções (todas verdes; o `xfail` de A-19b foi retirado quando o item foi corrigido no Sprint 7b):

| Comando | Resultado |
|---|---|
| `.venv/bin/python -m pytest -q docs/scenarios` | todos passam (265 após a correção de A-19b no Sprint 7b, incluindo as sondas abaixo); 0 xfailed |
| `cd server && FM_TEST_DB=fm_test_docs ../.venv/bin/python -m pytest -q tests/test_audit_s7.py tests/test_kpi_spec_cases.py tests/test_detector_late_rewind.py` | 36 passed |
| `cd server && FM_TEST_DB=fm_test_docs ../.venv/bin/python -m pytest -q tests/test_detector_catchup.py tests/test_backlog_engines.py tests/test_break_fault_s5.py tests/test_reports_core.py` | 34 passed (S7-16/S7-17: quedas longas, resultado idêntico ao contínuo) |

### Resultado por item

| # | Veredito | Evidência (código lido · teste) |
|---|---|---|
| A-1 | ✅ confirmado | `engine.components`: `eq_brk` (pausas `PLN-03`/`MNT-02` ∩ POT) unido aos eventos de equipamento. Sondas V1 (calendário + falha sobreposta → D_eq 90 min, não 120) e V2 (mesmo caso com evento declarado) → Ao = 0,8 |
| A-2 | ✅ confirmado | `usage/core.rate_window` com `installed_at` e início dos dados; U3 com instalação antiga → 70 dias, `low_confidence`; V9 (2,5 dias com 60 h de instalação) |
| A-3 | ✅ confirmado | `evaluate_plans` passa `window_days`; `< 3` dias → `insufficient_data` |
| A-4 | ✅ confirmado | V6: MTBF, Ai e λ ficam `null` **juntos** (não há mais campo contraditório); n = 0 com tempo → λ = 0 |
| A-5 | ✅ confirmado | `kpi.pareto` → `[]` com PBT = 0, também `by=category` |
| A-6 | ✅ confirmado | `engine.outside_production` (calendário + `DowntimeEvent` planejado) nas duas APIs; proxy removido do relatório; `test_audit_s7.py::test_A6_A20_*` |
| A-7 | ✅ confirmado | `estimated` pela topologia nas linhas por posição/tipo/fabricante; `test_A7_*` |
| A-8 | ✅ confirmado | λc = 1,25 por 10⁶ ciclos (R5); ciclos sem fonte → `null`; n = 0 → 0 (V6) |
| A-9 | ✅ confirmado | 9 400 h, `excluded_no_usage = 1`; V7: todos com idade 0 → `null` + `insufficient_data` |
| A-10 | ✅ confirmado | `make_rate_fn` em horas completas: ao vivo = recálculo = nominal = 20 000/dia |
| A-11 | ✅ confirmado | concluída após o período → `not_done`; concluída dentro do período após o prazo → `late` |
| A-12 | ✅ confirmado (com observação O-2) | intervalo semiaberto, ordenação determinística → "new" |
| A-13 | ✅ confirmado | `line_pri_fallback` no OEE da linha/planta |
| A-14 | ✅ confirmado | parcial 440/450; V3: falha atravessando a refeição do **calendário** → só a parte fora da pausa em D_eq; V4: `planned_stop` sem código cai em `PLN-04` (fora de T_req, não D_eq) |
| A-15 | ✅ confirmado | `reason = no_running_time` |
| A-16 | ✅ confirmado | `operating_seconds(..., now)`; `test_A16_*` |
| A-17 | ✅ confirmado | `test_conformance` passa os casos de §6 pelas funções do servidor |
| A-18 | ✅ confirmado | `< 3` dias → `rate_per_day = null`, `p_reason = insufficient_data`; `< 14` → `low_confidence` |
| A-19 | ✅ confirmado (após A-19b) | a cadência ao vivo vem das leituras brutas (60 pç/h no contraexemplo); o filtro de plausibilidade entrou no Sprint 7b (A-19b, abaixo) |
| A-20 | ✅ confirmado | `mart_min` 20 min com 2 de 3 reparos apontados; nenhum apontado → `mart_reason = no_repair_times` |

V5 (falha aberta, cálculo às 13:30 → Ao = 390/420) confirma que T_req e D_eq param no `now` do motor.

### A-19b — Cadência ao vivo do mapa sem o filtro de plausibilidade · Média · ✅ corrigido (Sprint 7b)

- **Spec**: kpis.md S7-13 — cadência ao vivo pelas leituras brutas com "regras de delta de domain.md §3.3"; a regra 4
  (filtro de plausibilidade, DEVE) descarta um delta `> max_rate × Δt × 1,5 + 10`, mantém `prev` e registra
  `counter_glitch`.
- **Código**: `server/src/services/layout/service.py:live_parts` chama `counter_delta(a, b, cmax)` **sem** `dt_s` nem
  `max_rate_per_s`, então só as regras 1–3 valem; também não mantém o `prev` aceito depois de um glitch. O
  pipeline de ingestão (`server/src/services/usage/processor.py`, `counter_step` com `max_rate`) filtra o mesmo glitch,
  portanto PQ/GQ do turno, OEE e uso **não** são afetados — só o número ao vivo do mapa.
- **Contraexemplo** (`test_A19b_*`): 1 peça/min nos últimos 15 min (leituras 1 000 … 1 015, contador de 16 bits) com
  **uma** leitura corrompida de 60 000 no minuto 7; PRI 30 s → limite de 16 peças por minuto. O salto vira +58 994 e a
  volta a 1 008 é lida como rollover (60 000 ≥ 0,9 × 65 535): +6 544. Código: **65 551 peças → 262 204 pç/h** no
  marcador, com `cadence_source = counter` (sem aviso), por 15 min. Spec: glitch descartado → **15 peças → 60 pç/h**.
- **Impacto**: o mapa é a tela "o que está acontecendo agora" e fica na TV da fábrica; um número absurdo desacredita o
  produto no piloto (pilot.md H2). Leitura corrompida isolada é rara, mas acontece com CLP reiniciando, troca de
  palavra de 32 bits lida em duas metades (Modbus) ou gateway com registrador errado.
- **Correção (Sprint 7b)**: `live_parts` passa cada leitura por `usage/core.counter_step` — a mesma função do
  pipeline de ingestão —, com `dt_s` entre leituras e `max_rate` resolvido por `part_counter_tags` com
  `resolve_max_rate` (tag, senão `2 / PRI` da máquina, senão `usage_default_max_rate_per_s`). O glitch contribui 0
  e mantém o `prev` aceito; um salto real confirmado pela leitura seguinte faz re-baseline como no pipeline (o salto
  nunca é contado). Contraexemplo agora dá **15 peças → 60 pç/h**.
- **Testes**: `docs/scenarios/test_audit_verification_s7.py::test_A19b_live_cadence_glitch_spec` (sem `xfail`;
  `…_code_today` removido), `…::test_A19b_live_cadence_real_jump_rebaselines` e, com Postgres + InfluxDB reais,
  `server/tests/test_audit_s7.py::test_A19b_live_cadence_discards_counter_glitch` (inclui isolamento de tenant).

### Observações (não mudam KPI; sem ação obrigatória)

- **O-1 — Viés da leitura de base da cadência ao vivo.** `live_parts` usa como base a última leitura até o início da
  janela (busca até 10 min antes). Com gateway que publica por mudança, está certo (o contador não mudou entre a base e
  o início da janela). Com leitura periódica, as peças entre a base e o início da janela entram na conta: viés de no
  máximo um intervalo de leitura (ex.: leitura a cada 15 s a 60 pç/h → ≤ 0,25 peça → ≤ 1 pç/h). Aceitável; documentar
  se algum cliente usar intervalo de leitura longo para contadores.
- **O-2 — Falha no instante exato de uma remoção sem sucessor.** Com o intervalo semiaberto (S7-7), uma falha
  registrada exatamente no instante em que o componente foi removido, sem componente novo instalado na posição, fica
  sem componente (sonda V8) — continua contando para a posição e a máquina, mas não para o MTTF/MCBF do componente
  removido. No fluxo normal (troca = remoção + instalação no mesmo instante, ou falha registrada antes da remoção) não
  acontece. Se ocorrer no piloto, a correção é na entrada (OS de troca grava `component_id` na falha), não na fórmula.
- **O-3 — Sem leituras do contador na janela.** Com a base presente e nenhuma leitura nos últimos 15 min a cadência
  ao vivo é 0 pç/h (`cadence_source = counter`); com gateway por mudança isso é verdade (nada produzido). Gateway sem
  contato já tem precedência no marcador (`offline`, sprint6.md §1.2), então o 0 não aparece como "rodando".

**Conclusão**: dos 20 itens, **19 confirmados corrigidos** e **1 (A-19) corrigido com ressalva**, que gerou o item
aberto **A-19b** (Média, só a exibição ao vivo do mapa; nenhum KPI calculado é afetado). As fórmulas de OEE, Ao,
MTBF/MCBF/MTTR/MART, λ, MTTF, PMP, cumprimento, backlog, RUL e planos conferem com a especificação e com as sondas.

**Atualização Sprint 7b**: A-19b corrigido (ver item acima, testes citados). Com isso **os 20 itens e o item de
verificação A-19b estão corrigidos**; nenhum item da auditoria permanece aberto. Cenários dourados A–F inalterados
(`make test-golden`).

### Sprint 7b — achados da rodada de capturas na planta virtual (fora da lista A-*)

Três defeitos de **exibição** encontrados pelo dono do projeto na planta virtual ao vivo; nenhum muda fórmula, mas os
três mostravam ao usuário um número/estado que a especificação não sustenta. Decisões em [`kpis.md`](kpis.md)
"Decisões Sprint 7" (S7-21 … S7-23).

| # | Achado | Decisão | Estado · teste |
|---|---|---|---|
| B-1 | Linha do tempo de estados pintava períodos **sem dados** como verde "sem parada registrada" | S7-22: `GET /api/downtime/no-data` (complemento dos intervalos com dados, o mesmo "sem dados" do OEE); a tela pinta "sem dados" e avisa se a consulta falhar | ✅ corrigido · `server/tests/test_sprint7b_bugs.py::test_no_data_intervals_are_explicit`, `::test_no_data_tenant_isolation`; `web/src/utils/efficiency.test.ts` ("paints the gaps without data…"), `web/src/pages/efficiency/TimelinePage.test.tsx` |
| B-2 | Janela padrão da confiabilidade terminava hoje 00:00 local: a falha de hoje não aparecia | S7-21: `[00:00 local de 12 meses atrás, agora)` (emenda S4-8); cenário E inalterado com a janela explícita | ✅ corrigido · API: `test_sprint7b_bugs.py::test_reliability_default_window_includes_todays_failure`, `test_maintenance_scenario_e.py` (padrão termina em `evaluate_at`). Tela corrigida: `ReliabilityPage.reliabilityWindow` envia só `start` (rótulo "… – agora") · `web/src/pages/maintenance/maintenancePages.test.tsx` (a requisição não leva `stop`). Conferido no navegador com a planta viva em 2026-10-16 (`e2e/sprint7b/screenshot_bugs.verify.mjs`: a requisição leva só `start`; rótulo "16/10/2025 – agora") |
| B-3 | Falha do seed com `restored_at` vazio aparecia como falha ao vivo "há 40 d" no mapa/tablet/Raio-X | S7-23: aberta > `FAILURE_STALE_OPEN_H` (72 h) → selo `stale_failure` + "falha aberta há muito tempo — verificar", estado pelas demais regras; `incipient` nunca põe a máquina em falha; `open_stale` em `/api/failures/`; seed restabelece as falhas históricas (e corrige bancos já semeados) | ✅ corrigido · `test_sprint7b_bugs.py::test_stale_open_failure_is_flagged_not_live`, `::test_long_real_failure_with_open_linked_stop_stays_failure`, `::test_old_incipient_record_is_never_open_stale`, `test_seed_demo_plant.py::test_seed_demo_maintenance`. Um só predicado para mapa e API (`services/maintenance/stale.py`): a idade sozinha não rebaixa uma falha |
