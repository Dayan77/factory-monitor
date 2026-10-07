# FactoryConnect — Indicadores (KPIs)

Definição normativa de cada indicador: fórmula, unidade, granularidade, fonte de dado (papel da tag — ver
[`docs/domain.md`](domain.md) §3), casos de borda e um exemplo numérico. **Os exemplos são casos de teste**: a seção 6
traz os mesmos números em JSON (`kpi-test-cases`) para os testes do backend carregarem diretamente.

Referências: ISO 22400-2:2014 (OEE e tempos), ISO 14224:2016 (falhas), EN 15341 / SMRP (indicadores de manutenção),
SEMI E10 (estados de equipamento).

## 0. Convenções (valem para todos os indicadores)

- **Frações, não porcentagens**, na API: `0.754444` (a UI mostra `75,4 %`). Nada é arredondado antes da exibição.
  Testes comparam com tolerância absoluta `1e-6` (ou relativa `1e-6` para valores > 1).
- **Indicador indefinido → `null`** com um campo `reason` (`no_planned_time`, `no_failures`, `no_production`,
  `no_nominal_life`, `no_recent_usage`, `insufficient_data`). Nunca devolver 0 nem infinito no lugar de "indefinido".
- Toda resposta de KPI inclui `period_start`, `period_end` (UTC), `granularity` e `data_coverage`
  (fração do tempo programado com dados; < 0,95 → UI mostra aviso "dados incompletos").
- **Agregação** (máquina → linha → planta, turno → dia → mês) é sempre feita **somando os componentes**
  (tempos, contagens, falhas) e recalculando a razão. **Nunca** tirar média de porcentagens (ver §1.6).
- Granularidades suportadas: `component` (posição), `machine`, `line`, `plant`; no tempo: `shift`, `day`
  (dia produtivo), `week`, `month`, ou intervalo livre.
- Símbolos ISO 22400 usados: POT, PDOT, PBT, ADOT, APT, PRI (tempo de ciclo ideal), PQ (produzidas), GQ (boas),
  SQ (refugo), RQ (retrabalho).

---

## 1. Eficiência produtiva

### 1.1 Modelo de tempos (resumo — detalhes em domain.md §4.1)

| Símbolo | Nome | Fórmula / origem |
|---|---|---|
| POT | tempo programado | soma dos turnos do calendário no período |
| PDOT | paradas planejadas | eventos com código `PLN-*` + pausas do turno |
| PBT | tempo de produção planejado | `POT − PDOT − tempo sem dados` |
| ADOT | paradas não planejadas | soma dos `DowntimeEvent` não planejados (≥ limiar de micro-parada), inclusive setup |
| APT | tempo rodando | `PBT − ADOT` |
| PRI | tempo de ciclo ideal por peça | cadastro (máquina, produto), em s |

Fontes: `run_state`, `fault_state` (ou intervalo entre `cycle_pulse`/`cycle_counter`) para os eventos;
`part_counter_good`, `part_counter_scrap` para as contagens; calendário de turnos e motivos informados.

### 1.2 Disponibilidade (ISO 22400 *Availability*)

`A = APT / PBT` — adimensional (0–1). Granularidade: máquina, linha; turno ou maior.

Casos de borda: `PBT = 0` → `null` (`no_planned_time`). Parada que cruza turno é dividida (domain.md §4.3 regra 5).
Micro-paradas **não** reduzem A (estão dentro de APT).

### 1.3 Desempenho (ISO 22400 *Effectiveness*)

`P = Σ_i (PRI_i × PQ_i) / APT` — soma por produto i produzido no período; PQ = GQ + SQ (+ RQ, se contado à parte).

Casos de borda: `APT = 0` → `null`. `P > 1` **não é truncado**: é devolvido como está com o flag
`ideal_cycle_time_suspect = true` (o PRI cadastrado está mais lento que o real ou o contador conta em dobro); a UI mostra aviso.
Produto sem PRI cadastrado → `null` (`insufficient_data`) para P e OEE daquele período.

### 1.4 Qualidade (ISO 22400 *Quality rate*)

`Q = Σ_i (PRI_i × GQ_i) / Σ_i (PRI_i × PQ_i)` — com um só produto reduz-se a `GQ / PQ`.
(Ponderar pelo PRI garante `A × P × Q = OEE` também com mix de produtos.)
Retrabalho conta como **não boa** na primeira passagem (First Pass Yield). `PQ = 0` → `null` (`no_production`).

### 1.5 OEE

`OEE = A × P × Q = Σ_i (PRI_i × GQ_i) / PBT` — tempo produtivo líquido / tempo planejado.

**Exemplo E1 (caso `oee_single_shift`)** — Máquina "Prensa P1", turno A 06:00–14:00, produto único, PRI = 30 s.

| Grandeza | Valor |
|---|---|
| POT | 480 min |
| PDOT (refeição `PLN-01`) | 30 min → PBT = 450 min |
| ADOT: falha `EQP-03` 25 min + setup `SET-01` 20 min + falta de material `MAT-01` 15 min | 60 min → APT = 390 min |
| PQ = 700 (GQ = 679, SQ = 21) | tempo ideal = 700 × 0,5 min = 350 min |

- A = 390 / 450 = **0,866667**
- P = 350 / 390 = **0,897436**
- Q = 679 / 700 = **0,970000**
- OEE = 0,866667 × 0,897436 × 0,97 = 339,5 / 450 = **0,754444** (75,4 %)

### 1.6 Agregação: vários turnos, mix de produtos, linha

Regra: somar PBT, APT, Σ PRI·PQ, Σ PRI·GQ e recalcular. **Exemplo E2 (caso `oee_two_shifts_mix`)**:

- Turno A: o E1 (PBT 450, APT 390, Σ PRI·PQ = 350, Σ PRI·GQ = 339,5 min).
- Turno B: POT 480, refeição 30 + reunião `PLN-02` 30 → PBT 420; falha 10 min → APT 410.
  Produto X (PRI 30 s): PQ 400, GQ 390. Produto Y (PRI 40 s): PQ 300, GQ 290.
  Σ PRI·PQ = 200 + 200 = 400 min; Σ PRI·GQ = 195 + 193,3333 = 388,3333 min.
  A = 410/420 = 0,976190; P = 400/410 = **0,975610**; Q = 388,3333/400 = **0,970833**; OEE_B = 388,3333/420 = **0,924603**.
- **Dia** (A + B): PBT 870, APT 800, Σ PRI·PQ 750, Σ PRI·GQ 727,8333 →
  A = **0,919540**, P = **0,937500**, Q = **0,970444**, **OEE = 0,836590**.
  A média simples dos OEEs dos turnos daria 0,839524 — **errado** (o teste deve garantir que a API não faz isso).

OEE da **linha**: usar a máquina de saída da linha (`Line.output_machine_id`) para PQ/GQ e para APT, e o PRI da linha
(= PRI do gargalo para o produto). Paradas de máquinas intermediárias só entram se pararem a saída
(aparecem como `MAT-01 starved` na máquina de saída). OEE da planta = agregação (soma) das linhas.

### 1.7 TEEP

`Loading = PBT / tempo calendário`; `TEEP = OEE × Loading = Σ PRI·GQ / tempo calendário`.
Tempo calendário = 24 h × dias do período (1 440 min/dia). Granularidade: máquina, linha; dia ou maior.

**Exemplo E3 (caso `teep_day`)**: o dia do E2: calendário 1 440 min, PBT 870 min → Loading = **0,604167**;
TEEP = 0,836590 × 0,604167 = 727,8333 / 1 440 = **0,505440**.

### 1.8 Micro-parada

**Definição**: interrupção do fluxo com duração `d` tal que `limiar_detecção ≤ d < micro_stop_threshold_s`
(padrão **120 s**, configurável por máquina, 30–300 s). Não vira `DowntimeEvent`, não exige motivo e é perda de
**desempenho** (dentro de APT). `d ≥ micro_stop_threshold_s` → parada (evento com motivo, perda de disponibilidade).

Detecção:
- Com `run_state`: cada intervalo `run_state = 0` é uma parada de duração = intervalo.
- Sem `run_state` (só ciclos): intervalo entre ciclos consecutivos `gap > 2 × PRI` é parada, com
  `d = gap − PRI`. `PRI < gap ≤ 2 × PRI` é **perda de velocidade** de `gap − PRI` (não é parada).

**Exemplo E4 (caso `micro_stops_from_cycle_gaps`)**: PRI = 30 s, limiar 120 s, intervalos entre ciclos 31, 95, 40, 400 s:
- 31 s → perda de velocidade 1 s; 40 s → perda de velocidade 10 s (total **11 s**).
- 95 s > 60 s → parada de 65 s < 120 s → **micro-parada** (total **65 s**, 1 ocorrência).
- 400 s → parada de 370 s ≥ 120 s → **DowntimeEvent** de **370 s** (exige motivo).

KPIs derivados: nº de micro-paradas por hora de APT; tempo total de micro-paradas (min) por turno.

### 1.9 Cadência (peças/h)

| Variante | Fórmula | Uso |
|---|---|---|
| Cadência realizada | `PQ / PBT` (h) | "quantas peças por hora a linha entregou" |
| Cadência em operação | `PQ / APT` (h) | velocidade quando rodando |
| Cadência ideal | `3 600 / PRI` (s) | referência do gráfico |
| Cadência ao vivo | peças dos últimos 15 min × 4 | tela de linha (atualiza a cada leitura) |

Fonte: `part_counter_good` + `part_counter_scrap` da máquina de saída (linha). Granularidade: máquina, linha; janela livre.
Borda: PBT/APT = 0 → `null`.

**Exemplo E5 (caso `cadence`)**, dados do E1: 700 / 7,5 h = **93,333 peças/h**; 700 / 6,5 h = **107,692 peças/h**;
ideal 3 600 / 30 = **120 peças/h**.

### 1.10 Pareto de perdas (top-loss)

Todas as perdas convertidas em **minutos** dentro do PBT, de modo que `Σ perdas = PBT − Σ PRI·GQ`:

| Perda | Cálculo |
|---|---|
| cada motivo de parada não planejada (`EQP-*`, `SET-*`, `MAT-*`, …) | soma das durações dos eventos daquele código (ou categoria, no nível agregado) |
| micro-paradas | soma das durações das micro-paradas |
| perda de velocidade | `APT − Σ PRI·PQ − micro-paradas` |
| perda de qualidade | `Σ PRI × (SQ + RQ)` |

Ordenar decrescente; mostrar % de cada uma sobre o total de perdas e o acumulado. Granularidade: máquina, linha, turno ou maior.
Borda: perda de velocidade negativa (P > 1) → mostrar 0 e o flag `ideal_cycle_time_suspect`.

**Exemplo E6 (caso `loss_pareto`)**, dados do E1 com micro-paradas = 18 min:
perda de velocidade = 390 − 350 − 18 = 22; qualidade = 0,5 × 21 = 10,5; total = 450 − 339,5 = 110,5 min.

| # | Perda | min | % | acumulado |
|---|---|---|---|---|
| 1 | Falha pneumática `EQP-03` | 25 | 22,62 % | 22,62 % |
| 2 | Perda de velocidade | 22 | 19,91 % | 42,53 % |
| 3 | Setup `SET-01` | 20 | 18,10 % | 60,63 % |
| 4 | Micro-paradas | 18 | 16,29 % | 76,92 % |
| 5 | Falta de material `MAT-01` | 15 | 13,57 % | 90,50 % |
| 6 | Qualidade (refugo) | 10,5 | 9,50 % | 100 % |

---

## 2. Confiabilidade

Contagem de falhas (`n`) em todos os indicadores desta seção: `FailureRecord` com `severity ∈ {critical, degraded}`
no período (por `failed_at`), filtrados pela granularidade: posição/componente, tipo de componente, fabricante/modelo,
máquina, linha. Falhas `incipient` são reportadas em campo separado, não entram em `n`.

### 2.1 MTBF — tempo médio entre falhas (horas)

`MTBF = tempo de operação no período / n` — unidade **h**.
Tempo de operação = horas com `run_state = 1` (da posição, se tiver `run_state` próprio; senão da máquina).
Para tipo/fabricante: somar o tempo de operação de todas as posições do grupo e todas as falhas do grupo.

Bordas: `n = 0` → `null` (`no_failures`) e devolver `operating_hours` para a UI mostrar "> 720 h sem falhas";
tempo de operação estimado (sem `run_state`) → `estimated = true`. Usar janela de pelo menos 3 meses na tela de
confiabilidade (MTBF de 1 semana com 1 falha não é estatisticamente útil — mostrar `n` junto).

**Exemplo R1 (caso `mtbf`)**: máquina com 720 h de operação no mês, 4 falhas (critical/degraded) + 1 incipient →
MTBF = 720 / 4 = **180 h**. Sem falhas (caso `mtbf_no_failures`): `null`, `operating_hours = 720`.

### 2.2 MCBF — ciclos médios entre falhas

`MCBF = ciclos no período / n` — unidade **ciclos**. Fonte: `cycle_counter`/`cycle_pulse` da posição
(ou estimado por peças × `cycles_per_part`, com `estimated = true`). Indicador principal para cilindros, válvulas,
garras e robôs (desgaste por ciclo, não por hora). Bordas iguais ao MTBF.

**Exemplo R2 (caso `mcbf`)**: posição "Cilindro A1" com 2 400 000 ciclos no período e 3 falhas → MCBF = **800 000 ciclos**.

### 2.3 MTTR — tempo médio para restabelecer

`MTTR = Σ (restored_at − failed_at) / nº de falhas com restabelecimento` — unidade **min** (API em s).
Inclui detecção, espera por técnico/peça e reparo (é o que a produção sente: ISO 14224 *mean time to restore*).
Só falhas que pararam a produção (com `DowntimeEvent`) entram; falha encontrada em preventiva não entra.
Indicador auxiliar: **MART** (tempo ativo de reparo) = média de `repair_finished_at − repair_started_at`, quando apontado.
Bordas: nenhuma falha restabelecida → `null`; falha ainda aberta no fim do período → fica fora até fechar.

**Exemplo R3 (caso `mttr`)**: 4 falhas com 30, 45, 90 e 75 min → MTTR = 240 / 4 = **60 min**.

### 2.4 MTTF — tempo médio até a falha (itens não reparáveis)

Para componentes substituídos (rolamento, vedação, correia, válvula): média da **idade na falha**
(`usage_since_install` na remoção com `removal_reason = failure`) dos componentes de um grupo
(tipo, fabricante, modelo). Unidade: h, ou ciclos (**MCTF**) para itens por ciclo.
Componentes removidos sem falhar (troca preventiva) são **suspensões**: não entram na média, mas `suspensions` é
devolvido junto (a média simples subestima a vida quando há muitas suspensões; Fase 4 usará Weibull com censura).
Borda: nenhum componente falhou → `null`.

**Exemplo R4 (caso `mttf`)**: rolamentos do mesmo modelo falharam com 8 200, 9 600 e 10 400 h; dois foram trocados
preventivamente com 7 000 e 9 000 h → MTTF = 28 200 / 3 = **9 400 h**, `failures = 3`, `suspensions = 2`.

### 2.5 Taxa de falha

`λ = n / tempo de operação` (falhas/h; UI mostra por 10⁶ h) = 1/MTBF. Por ciclos: `λc = n / ciclos × 10⁶`
(falhas por milhão de ciclos). Borda: tempo/ciclos = 0 → `null`; `n = 0` → `0` (aqui zero é correto).

**Exemplo R5 (caso `failure_rate`)**: 4 / 720 h = **0,0055556 falhas/h** (5 555,6 por 10⁶ h);
3 / 2 400 000 ciclos = **1,25 falhas por 10⁶ ciclos**.

### 2.6 Disponibilidade inerente e operacional

| | Fórmula | O que mede |
|---|---|---|
| Inerente `Ai` | `MTBF / (MTBF + MTTR)` (mesma unidade) | confiabilidade + manutenibilidade do equipamento (projeto) |
| Operacional `Ao` | `(T_req − D_eq) / T_req` | disponibilidade real do equipamento para a produção |

- `T_req` (tempo requerido) = POT − paradas não relacionadas ao equipamento planejadas e de organização
  (`PLN-01`, `PLN-02`, `PLN-04`, `PLN-05`).
- `D_eq` (indisponibilidade do equipamento) = paradas por falha `EQP-*` (incluindo espera por peça/técnico) +
  manutenção `PLN-03` e `MNT-02`.
- **Não** entram em `D_eq`: setup, falta de material/operador, qualidade, utilidades — por isso Ao ≠ disponibilidade OEE.

**Exemplo R6 (casos `availability_inherent`, `availability_operational`)**:
Ai com R1 e MTTR = 1 h: 180 / 181 = **0,994475**.
Ao: POT 660 h, pausas/reuniões/sem demanda 60 h → T_req = 600 h; falhas 6 h (incl. 2 h aguardando peça) +
preventiva 6 h → D_eq = 12 h → Ao = 588 / 600 = **0,980000**.

---

## 3. Gestão da manutenção

### 3.1 PMP — Planned Maintenance Percentage

`PMP = horas de OS planejadas / horas totais de OS` — fração, sobre `labor_hours` de OS `completed`/`closed` no período
(por `completed_at`). Planejadas = `preventive` + `predictive` + `improvement` + `corrective` com `urgency = deferred`.
Não planejadas = `corrective` `emergency`. Granularidade: máquina, linha, planta; semana ou mês.
Meta de referência: ≥ 0,85 (classe mundial ≥ 0,90). Borda: total 0 → `null`.

**Exemplo M1 (caso `pmp`)**: preventiva 90 h, preditiva 30 h, melhoria 10 h, corretiva programada 5 h,
corretiva emergencial 25 h → planejadas 135 / total 160 = **0,843750**.

### 3.2 Cumprimento da programação (schedule compliance)

`SC = OS programadas no período concluídas no prazo / OS programadas no período`.
Programadas = OS com `scheduled_for` dentro do período (congelado no início da semana; OS incluídas depois não entram
no denominador). No prazo = `completed_at ≤ fim do dia de scheduled_for + tolerância` (padrão 0 dias, configurável por tenant).
Cancelada pelo planejador com motivo `superseded` → sai do denominador; qualquer outra cancelada → não cumprida.
Meta: ≥ 0,90. Borda: nenhuma programada → `null`.

**Exemplo M2 (caso `schedule_compliance`)**: 40 programadas: 34 no prazo, 3 atrasadas, 3 não feitas → **0,850000**.

### 3.3 Backlog

`Backlog (semanas) = Σ horas estimadas restantes das OS em approved/scheduled/on_hold/in_progress /
capacidade semanal disponível (h)`. Capacidade = técnicos × horas/semana do calendário da manutenção − ausências previstas.
Também devolver: nº de OS em backlog, nº e horas de OS **vencidas** (`scheduled_for < hoje` ou plano `overdue`).
Referência: 2–4 semanas saudável; < 2 equipe ociosa/planejamento fraco; > 4 equipe insuficiente. Borda: capacidade 0 → `null`.

**Exemplo M3 (caso `backlog`)**: 260 h estimadas em aberto, capacidade 130 h/semana → **2,0 semanas**.

---

## 4. Uso e vida de componentes

### 4.1 % de vida consumida

Por medida m com vida nominal `L_m`: `life_m = usage_since_install_m / L_m`; reportado `life = max_m(life_m)` e qual
medida determinou. Fonte: acumuladores (domain.md §3.5). Granularidade: componente (e lista por máquina/linha).
Alertas: ≥ 0,80 (atenção), ≥ 1,00 (vida nominal atingida). Valor > 1 **não** é truncado.
Borda: nenhuma vida nominal → `null` (`no_nominal_life`).
O mesmo cálculo sobre `usage_since_maintenance / intervalo` dá o **% do intervalo do plano** (domain.md §5.1).

**Exemplo U1 (caso `life_consumed`)**: cilindro com vida 5 000 000 ciclos e 3 600 000 ciclos desde a instalação → **0,72**.
**Exemplo U2 (caso `life_consumed_multi_measure`)**: motor 30 000 h e 200 000 partidas; usado 12 000 h (0,40) e
130 000 partidas (0,65) → **0,65**, medida determinante `starts`.

### 4.2 Vida útil remanescente (RUL) e data projetada

Para cada medida: `restante_m = L_m − usage_since_install_m`; `ritmo_m = uso nos últimos 14 dias corridos / 14`
(por dia **corrido**, de modo que fins de semana parados entram na média); `dias_m = restante_m / ritmo_m`;
`data = hoje + ceil(dias_m)` no fuso da planta. RUL = a medida com **menor** data.
Para planos, o mesmo cálculo com `intervalo − usage_since_maintenance`, e o gatilho de calendário dá
`data = última conclusão + interval_days`; vale a data mais cedo.
Bordas: ritmo = 0 → `null` (`no_recent_usage`); menos de 14 dias de histórico → usar os dias disponíveis
(mínimo 3, senão `insufficient_data`) e `low_confidence = true`; já vencido (restante ≤ 0) → `days = 0`, `overdue = true`.
Fase 4 substitui o ritmo histórico pelo plano de produção quando houver.

**Exemplo U3 (caso `rul_projection`)**: U1 em 2026-10-05; restante 1 400 000 ciclos; 280 000 ciclos nos últimos 14 dias →
ritmo 20 000/dia → **70 dias** → **2026-12-14**.
**Exemplo U4 (caso `plan_due_whichever_first`)**: plano 2 000 000 ciclos **ou** 180 dias; desde a última troca
1 700 000 ciclos e 60 dias; ritmo 20 000 ciclos/dia; hoje 2026-10-05 → ciclos: 300 000 / 20 000 = 15 dias;
calendário: 120 dias → vence em **15 dias, 2026-10-20**, gatilho `cycles`, `consumed = 0,85` (`due_soon`).

---

## 5. Perguntas de toda manhã → tela / KPI

| # | Quem | Pergunta | Tela | KPI / dado |
|---|---|---|---|---|
| 1 | Produção | Como foi ontem e a noite? Batemos a meta? | Fábrica → Linha (resumo por turno) | OEE, A/P/Q por turno (§1.5–1.6), PQ vs. meta, cadência realizada (§1.9) |
| 2 | Produção | Onde perdemos mais tempo? | Linha → Pareto de perdas | Pareto top-loss (§1.10), por turno e por máquina |
| 3 | Produção | Quais paradas ainda estão sem motivo? | Paradas (filtro "não justificadas") | `DowntimeEvent` com `UNK-01` / sem motivo; % de tempo justificado |
| 4 | Produção | Algum turno ou produto está muito pior que os outros? | Comparação de turnos/produtos | OEE e perdas por turno e por produto (agregação §1.6) |
| 5 | Produção | Qual a situação agora? O que está parado? | Mapa/Linha ao vivo | `Machine.status`, parada aberta e duração, cadência ao vivo |
| 6 | Manutenção | O que quebrou desde ontem e já voltou a funcionar? | Falhas (últimas 24 h) | `FailureRecord` com duração, MTTR do período (§2.3) |
| 7 | Manutenção | O que vence esta semana / o que está atrasado? | Próximas manutenções | planos `due_soon`/`due`/`overdue`, data projetada (§4.2), OS vencidas (§3.3) |
| 8 | Manutenção | Quais componentes estão perto do fim da vida? | Componentes (ordenado por % vida) | % vida ≥ 80 % (§4.1), RUL (§4.2) |
| 9 | Manutenção | Quais são os maus atores (equipamentos que mais falham)? | Confiabilidade | MTBF/MCBF/MTTR por máquina, tipo, fabricante (§2.1–2.4); falhas por modo ISO 14224 |
| 10 | Manutenção | Estamos mais preventivos ou apagando incêndio? A equipe dá conta? | Gestão da manutenção | PMP (§3.1), cumprimento da programação (§3.2), backlog em semanas (§3.3), Ao (§2.6) |

---

## 6. Casos de teste canônicos

Os testes do backend DEVEM carregar o bloco abaixo (procurar a cerca ` ```json kpi-test-cases `) e verificar cada `expected`
com tolerância `1e-6` (relativa para valores > 1). Unidades: tempos em minutos salvo indicação no nome do campo.
Ao escrever esta especificação, cada caso foi recalculado a partir de `input` por uma implementação independente
das fórmulas acima (todos conferem; os valores com 6 casas são arredondamentos, erro ≤ 5e-7). O teste do backend que
carregar este bloco passa a ser a verificação permanente.

```json kpi-test-cases
[
  {"id": "oee_single_shift", "kpi": "oee",
   "input": {"pot_min": 480, "planned_downtime_min": 30, "unplanned_downtime_min": [25, 20, 15],
             "products": [{"ideal_cycle_time_s": 30, "produced": 700, "good": 679}]},
   "expected": {"pbt_min": 450, "apt_min": 390, "availability": 0.866667, "performance": 0.897436,
                "quality": 0.97, "oee": 0.754444}},

  {"id": "oee_two_shifts_mix", "kpi": "oee_aggregate",
   "input": {"shifts": [
      {"pot_min": 480, "planned_downtime_min": 30, "unplanned_downtime_min": [25, 20, 15],
       "products": [{"ideal_cycle_time_s": 30, "produced": 700, "good": 679}]},
      {"pot_min": 480, "planned_downtime_min": 60, "unplanned_downtime_min": [10],
       "products": [{"ideal_cycle_time_s": 30, "produced": 400, "good": 390},
                    {"ideal_cycle_time_s": 40, "produced": 300, "good": 290}]}]},
   "expected": {"shift_oee": [0.754444, 0.924603], "shift_b_performance": 0.975610, "shift_b_quality": 0.970833,
                "pbt_min": 870, "apt_min": 800, "availability": 0.919540, "performance": 0.9375,
                "quality": 0.970444, "oee": 0.836590, "wrong_mean_of_shift_oee": 0.839524}},

  {"id": "teep_day", "kpi": "teep",
   "input": {"calendar_min": 1440, "pbt_min": 870, "net_productive_min": 727.833333},
   "expected": {"loading": 0.604167, "teep": 0.505440}},

  {"id": "micro_stops_from_cycle_gaps", "kpi": "micro_stops",
   "input": {"ideal_cycle_time_s": 30, "micro_stop_threshold_s": 120, "cycle_gaps_s": [31, 95, 40, 400]},
   "expected": {"speed_loss_s": 11, "micro_stop_count": 1, "micro_stop_s": 65, "downtime_events_s": [370]}},

  {"id": "cadence", "kpi": "cadence",
   "input": {"produced": 700, "pbt_min": 450, "apt_min": 390, "ideal_cycle_time_s": 30},
   "expected": {"parts_per_hour_planned": 93.333333, "parts_per_hour_running": 107.692308, "parts_per_hour_ideal": 120}},

  {"id": "loss_pareto", "kpi": "loss_pareto",
   "input": {"pbt_min": 450, "apt_min": 390, "ideal_time_produced_min": 350, "ideal_cycle_time_s": 30, "scrap": 21,
             "downtime_by_reason_min": {"EQP-03": 25, "SET-01": 20, "MAT-01": 15}, "micro_stop_min": 18},
   "expected": {"total_loss_min": 110.5,
                "items": [["EQP-03", 25, 0.226244, 0.226244], ["speed_loss", 22, 0.199095, 0.425339],
                          ["SET-01", 20, 0.180995, 0.606335], ["micro_stops", 18, 0.162896, 0.769231],
                          ["MAT-01", 15, 0.135747, 0.904977], ["quality_loss", 10.5, 0.095023, 1.0]]}},

  {"id": "mtbf", "kpi": "mtbf",
   "input": {"operating_hours": 720, "failures": [{"severity": "critical"}, {"severity": "degraded"},
             {"severity": "critical"}, {"severity": "degraded"}, {"severity": "incipient"}]},
   "expected": {"n": 4, "mtbf_h": 180, "incipient": 1}},

  {"id": "mtbf_no_failures", "kpi": "mtbf",
   "input": {"operating_hours": 720, "failures": []},
   "expected": {"n": 0, "mtbf_h": null, "reason": "no_failures", "operating_hours": 720}},

  {"id": "mcbf", "kpi": "mcbf",
   "input": {"cycles": 2400000, "failures": 3},
   "expected": {"mcbf_cycles": 800000}},

  {"id": "mttr", "kpi": "mttr",
   "input": {"restore_durations_min": [30, 45, 90, 75]},
   "expected": {"mttr_min": 60}},

  {"id": "mttf", "kpi": "mttf",
   "input": {"failed_ages_h": [8200, 9600, 10400], "suspended_ages_h": [7000, 9000]},
   "expected": {"mttf_h": 9400, "failures": 3, "suspensions": 2}},

  {"id": "failure_rate", "kpi": "failure_rate",
   "input": {"failures": 4, "operating_hours": 720, "cycle_failures": 3, "cycles": 2400000},
   "expected": {"per_hour": 0.0055556, "per_million_hours": 5555.556, "per_million_cycles": 1.25}},

  {"id": "availability_inherent", "kpi": "availability_inherent",
   "input": {"mtbf_h": 180, "mttr_h": 1},
   "expected": {"ai": 0.994475}},

  {"id": "availability_operational", "kpi": "availability_operational",
   "input": {"pot_h": 660, "excluded_h": 60, "failure_downtime_h": 6, "maintenance_downtime_h": 6},
   "expected": {"required_h": 600, "ao": 0.98}},

  {"id": "pmp", "kpi": "pmp",
   "input": {"labor_hours": {"preventive": 90, "predictive": 30, "improvement": 10,
                             "corrective_deferred": 5, "corrective_emergency": 25}},
   "expected": {"planned_h": 135, "total_h": 160, "pmp": 0.84375}},

  {"id": "schedule_compliance", "kpi": "schedule_compliance",
   "input": {"scheduled": 40, "on_time": 34, "late": 3, "not_done": 3},
   "expected": {"compliance": 0.85}},

  {"id": "backlog", "kpi": "backlog",
   "input": {"open_estimated_hours": 260, "weekly_capacity_hours": 130},
   "expected": {"weeks": 2.0}},

  {"id": "life_consumed", "kpi": "life_consumed",
   "input": {"nominal": {"cycles": 5000000}, "usage_since_install": {"cycles": 3600000}},
   "expected": {"life": 0.72, "measure": "cycles"}},

  {"id": "life_consumed_multi_measure", "kpi": "life_consumed",
   "input": {"nominal": {"operating_hours": 30000, "starts": 200000},
             "usage_since_install": {"operating_hours": 12000, "starts": 130000}},
   "expected": {"life": 0.65, "measure": "starts"}},

  {"id": "rul_projection", "kpi": "rul",
   "input": {"today": "2026-10-05", "nominal": {"cycles": 5000000}, "usage_since_install": {"cycles": 3600000},
             "usage_last_14_days": {"cycles": 280000}},
   "expected": {"remaining": 1400000, "rate_per_day": 20000, "days": 70, "date": "2026-12-14", "measure": "cycles"}},

  {"id": "plan_due_whichever_first", "kpi": "plan_due",
   "input": {"today": "2026-10-05", "interval": {"cycles": 2000000, "days": 180},
             "since_maintenance": {"cycles": 1700000, "days": 60}, "rate_per_day": {"cycles": 20000}},
   "expected": {"consumed": 0.85, "state": "due_soon", "trigger": "cycles", "days": 15, "date": "2026-10-20"}},

  {"id": "counter_delta", "kpi": "counter_delta",
   "input": {"cases": [
      {"prev": 1000, "cur": 1250, "counter_max": 65535},
      {"prev": 65530, "cur": 4, "counter_max": 65535},
      {"prev": 48000, "cur": 120, "counter_max": 65535},
      {"prev": 48000, "cur": 120, "counter_max": null},
      {"prev": 1000, "cur": 900000, "counter_max": 65535, "dt_s": 1, "max_rate_per_s": 2}]},
   "expected": {"deltas": [250, 10, 120, 120, 0], "events": [null, "rollover", "counter_reset", "counter_reset", "counter_glitch"]}},

  {"id": "cycles_per_part_estimate", "kpi": "estimated_cycles",
   "input": {"parts": 700, "cycles_per_part": 2},
   "expected": {"cycles": 1400, "estimated": true}}
]
```

---

## Esclarecimentos Sprint 2

Revisão de domain.md §3 (tags, contadores, horas/partidas) e §4 (produção) procurando pontos em que dois engenheiros
chegariam a números diferentes. Cada item abaixo é **normativo** (DEVE) e tem prioridade sobre um texto ambíguo
acima ou em domain.md. Os cenários de ouro em [`docs/scenarios/`](scenarios/README.md) exercitam cada um
(o código entre parênteses indica onde).

### Contadores (domain.md §3.3)

- **C1 — Primeira leitura é linha de base.** A primeira leitura aceita de uma tag de contador (inclusive após
  recriar a tag ou perder o estado) tem `delta = 0`: o que foi contado antes é desconhecido. Vale também para
  `run_state` (C12). (A1 #1, B 17:52)
- **C2 — `prev` é a última leitura *aceita*.** Leitura descartada como glitch não muda `prev` **nem** o seu
  timestamp; o Δt da regra 4 é sempre `ts_atual − ts(prev)`. (A1 #10–#11: Δt = 1 200 s, não 600 s)
- **C3 — A regra 4 vem depois das regras 1–3.** O filtro de plausibilidade é aplicado ao delta já calculado, seja
  incremento normal, rollover ou reset. Um reset cujo `cur` exceda o limite também é glitch.
- **C4 — `max_rate` da posição** = `max_rate_per_s` da tag, se cadastrado; senão `2 × cycles_per_part /
  ideal_cycle_time_s` da máquina; senão o padrão do servidor (`usage_default_max_rate_per_s`, 10/s). Para
  contadores de peças, `cycles_per_part = 1`. Unidade: contagens/s; o limite é `max_rate × Δt × 1,5 + 10` contagens.
  (A1: 2 × 1 / 40 = 0,05/s)
- **C5 — Rollover usa módulo `counter_max + 1`.** `delta = (counter_max − prev) + cur + 1`; o exemplo
  65 530 → 4 = 10 confirma (usar `counter_max` como módulo dá 9). O limiar `0,9 × counter_max` é comparado com `prev`
  com `≥`. (A1 #6)
- **C6 — Duplicata, mesmo instante e fora de ordem.** (a) Mesmo `ts` e mesmo valor → duplicata, `delta = 0`
  (regra 5). (b) Mesmo `ts` e valor diferente → processada normalmente com Δt = 0 (o limite vira 10).
  (c) `ts` anterior ao da última leitura aceita → **ignorada** no processamento incremental (o contador é
  acumulado: a leitura seguinte já traz as contagens). Mesmo valor com `ts` novo **não** é duplicata (delta 0, mas
  avança `prev`). (A1 #8, #12, #15; B 20:07)
- **C7 — Salto real depois de um glitch (re-baseline).** Se a leitura seguinte a um glitch for plausível em
  relação ao **valor glitchado** (`cur ≥ pendente` e `cur − pendente ≤ limite(Δt desde o pendente)`), o contador
  realmente saltou (CLP trocado, preset): passa-se a contar a partir do valor pendente, somando só
  `cur − pendente`, e registra-se `counter_rebaseline`. O salto em si **nunca** é contado. Sem isso um salto legítimo
  congelaria a contagem para sempre. Uma leitura plausível em relação a `prev` descarta o pendente. (A1 #11)
- **C8 — Reconstrução é a fonte da verdade.** `recompute` (domain.md §3.5) reprocessa o histórico ordenado por
  `ts`. Para contadores dá o mesmo total que o incremental (a soma dos deltas telescopa). Para `run_state`, leituras
  que chegaram fora de ordem podem gerar diferença; nesse caso vale o recompute.

### Atribuição no tempo, horas e partidas (domain.md §3.4–3.5)

- **C9 — Bucket de um delta de contador = hora que contém o `ts` da leitura que o revelou**, sem rateio entre
  horas. Com polling de 1 s o erro é desprezível; com leituras esparsas (cenários), a leitura das 00:07 que cobre
  23:52–00:07 vai inteira para o bucket 00:00. Horas de operação **são** rateadas pelos buckets que o intervalo
  atravessa. Partidas vão para o bucket do `ts` da borda de subida. (A1 buckets; A2 buckets; B 00:07)
- **C10 — Debounce.** A duração de um 0 é `ts(0→1) − ts(1→0)` (timestamps de origem, do edge). Partida nova só se
  essa duração for **≥** `run_state_debounce_s` (padrão 1,0 s): exatamente 1,0 s **é** partida. As horas são
  literalmente `Σ run_state = 1`: o 0 filtrado pelo debounce **não** entra como operação (diferença sub-segundo,
  mas define o número exato). (A2: 0,4/0,9/0,5 s filtrados; 1,0 s é partida; 5 457,2 s)
- **C11 — Buraco de dados.** `last_seen` = `ts` da última leitura da tag (heartbeat incluso). Intervalo aberto com
  1 é creditado até `min(próxima leitura, last_seen + machine_offline_timeout_s)` (padrão 60 s). O heartbeat do edge
  DEVE ser menor que esse timeout (padrão 10 s). Dados que voltam com 1 depois de um buraco iniciado em 1 **não**
  geram partida (estado no buraco desconhecido); voltar com 1 depois de um 0 observado gera partida normalmente.
  (A2: 07:30–07:45 credita 60 s; 07:45 não é partida)
- **C12 — Primeira leitura de `run_state`** é linha de base: não é partida, mesmo com valor 1; a operação é
  contada a partir dela.
- **C13 — Herança e precedência de fontes.** Posição sem `run_state` próprio recebe horas **e** partidas do
  `run_state` da máquina (kpis.md §2.1). Posição com `counter_tag_id` conta ciclos só por essa tag; outras tags de
  ciclo ligadas à mesma posição são apenas verificação cruzada (não somam).
- **C14 — Ciclos estimados** (posição sem tag de ciclo) = delta de peças **totais** (boas + refugo) da máquina ×
  `cycles_per_part`, marcados `estimated = true`.
- **C15 — Peças totais.** Sem `part_counter_total`: total = boas + refugo (cada delta de boas e de refugo soma no
  total). Com `part_counter_total`: total vem dele e boas = total − refugo. (B)

### Vida e projeção (kpis.md §4)

- **C16 — Unidades na API.** `life_consumed_pct` é **fração 0–1** apesar do nome (0,72, não 72). Horas trafegam em
  horas (float) e são armazenadas em segundos. Chaves de vida nominal na API: `cycles`, `hours`, `starts`.
- **C17 — "Hoje" e janela.** "Hoje" = data **no fuso da planta** no instante da avaliação (23:30 de 05/10 em
  Manaus é 03:30Z de 06/10 → hoje = 05/10). A janela do ritmo são as 14 × 24 h que terminam no instante da avaliação
  (buckets horários com início ≥ hora cheia de `agora − 14 d`), divididas por 14. Componente instalado há menos de
  14 dias: divide-se pelos dias desde `installed_at` (mínimo 3; `low_confidence = true`). (A1/A2 projeção)
- **C18 — Arredondamento de dias.** `dias = ceil(restante / ritmo)`; quociente inteiro não sobe (70 → 70;
  46,67 → 47). A RUL é a medida com **menos dias**; empate → a medida de maior % de vida.

### Produção e dia produtivo (domain.md §4.1, kpis.md §1.9)

- **C19 — Dia.** Com calendário de turnos (Sprint 3): o turno que cruza a meia-noite pertence ao dia produtivo em
  que começa. Sem calendário (Sprint 2): dia = dia calendário **local** 00:00–24:00 no fuso da planta (é o que o
  histórico `bucket=1d` mostra) e a UI não deve chamá-lo de "dia produtivo". **Nunca** agrupar por data UTC (em Manaus
  ela vira às 20:00 local). (B: 627 no dia produtivo 05/10; 462 + 165 por dia local; UTC daria 156 + 471)
- **C20 — Buckets horários** são rotulados na hora local da planta. Para fusos de hora cheia (todo o Brasil) o
  bucket horário UTC coincide com o local; a agregação diária é sempre feita no fuso da planta.
- **C21 — Cadência de um bucket** = peças (boas + refugo) no bucket / duração do bucket (h) — cadência bruta, sem
  descontar pausas. As variantes de §1.9 (`PQ/PBT`, `PQ/APT`) exigem calendário e paradas e valem por turno ou maior.

### Paradas, micro-paradas e confiabilidade (domain.md §4.3, kpis.md §1.8, §2) — para o Sprint 3

- **C22 — Limiar.** `d ≥ micro_stop_threshold_s` → `DowntimeEvent` (120 s exatos é parada). `limiar_detecção` de
  §1.8 = `run_state_debounce_s` (1 s): quedas menores não são paradas nem micro-paradas. O limiar vale para **toda**
  fonte, inclusive `fault_state`: falha com código mais curta que o limiar é micro-parada, não gera `FailureRecord`
  e não entra em `n` do MTBF/MTTR; é contada à parte como `short_faults`. (C: 119 s micro, 120 s evento; falha 305 de 60 s)
- **C23 — Tempo de operação do MTBF** = `Σ run_state = 1` (exclui micro-paradas), **não** APT. No cenário C:
  349,5 min, não 357,5 min.
- **C24 — Fronteiras.** (a) Paradas separadas por menos de 10 s de operação **DEVEM** ser fundidas (era PODE): a
  duração fundida vai do início da primeira ao fim da última e o limiar é aplicado a ela. (b) O limiar é aplicado à
  duração contígua total da parada; depois, a parte que cai dentro de uma pausa planejada conta como PDOT e o
  restante como ADOT (e a divisão por turno da regra 5 se aplica do mesmo modo). (c) O MTTR usa o tempo de relógio
  `restored_at − failed_at` inteiro, mesmo que atravesse uma pausa (é o que a produção sente); só ADOT desconta a pausa.
  (d) Parado durante pausa planejada **não** gera `DowntimeEvent`.

Em aberto para o Sprint 3 (sem decisão ainda, nenhum cenário depende disso): parada que começa como `stopped` e vira
`error` no meio (um evento com motivo sugerido `EQP-*`, ou dois eventos?) → **decidido no Sprint 3: um evento (D1 abaixo)**.

## Decisões Sprint 3

Decisões tomadas na implementação do motor de paradas/OEE (`server/src/services/oee/`). Normativas como os
esclarecimentos acima; os testes `server/tests/test_oee_*.py` e o cenário C as exercitam.

- **D1 — Parada que vira falha no meio** (pergunta em aberto acima): é **um** `DowntimeEvent` (do início da
  parada até a volta a `running`), com motivo sugerido `EQP-*` (mapa `fault_code_map` da máquina; código não
  mapeado → `EQP-01`). O `FailureRecord` rascunho tem `failed_at` = instante em que `fault_state` ficou ≠ 0 (não o
  início da parada) e `restored_at` = fim da parada; o MTTR usa esse intervalo (C24c). Uma falha com código só
  durante parte de uma parada fundida (C24a) também torna o evento inteiro `EQP-*`.
- **D2 — Motivo efetivo.** Pareto/OEE usam `reason_code` do operador; sem ele, o sugerido (`EQP-*` da falha);
  sem os dois, `UNK-01`. Ao fim do turno, evento sem motivo **e** sem sugestão recebe `reason_code = UNK-01`
  (regra 4); evento com sugestão `EQP-*` mantém `reason_code` nulo até o operador confirmar. "Não justificada" =
  `reason_code` nulo ou `UNK-01`.
- **D3 — FailureRecord automático** para toda parada cujo motivo efetivo é `EQP-*` (severidade padrão `critical`
  — a parada perdeu a função; o técnico pode rebaixar para `degraded`). Uma parada dividida por turno tem **uma**
  falha (ligada ao primeiro evento). Se o operador reclassificar para um motivo não `EQP-*`, o rascunho é apagado.
- **D4 — Partes da parada.** O limiar é aplicado à parada contígua inteira (com dados); depois ela é cortada nos
  turnos (um evento por instância de turno, `stop_start` comum) e a parte fora do tempo programado não vira evento.
  Um pedaço inteiramente dentro de pausa planejada não vira evento (C24d). Micro-paradas: a perda conta só a parte
  dentro do tempo programado e fora de pausa; a ocorrência é contada uma vez, na primeira hora/turno em que cai.
- **D5 — Sem dados.** Um buraco de dados corta a parada (o estado durante o buraco é desconhecido); a parada
  cortada só é finalizada quando os dados voltam (ou após 24 h de silêncio). Sem dados = tempo programado fora de
  pausas/paradas planejadas e fora de paradas não planejadas declaradas, sem leitura válida (C11); sai do PBT.
  `data_coverage` = (tempo do POT com dados ou com parada declarada) / POT — máquina sem nenhum dado no período
  tem cobertura 0 e A/P/Q/OEE `null` (`insufficient_data`). Parada manual declarada sobre um buraco conta como ADOT.
- **D6 — Planta sem turnos cadastrados** usa um calendário implícito de 24 h (todo dia 00:00–24:00 local) para
  detecção e OEE, sinalizado por `calendar_default = true` na resposta. Com qualquer turno cadastrado, vale só o
  calendário.
- **D7 — Contagens horárias.** Peças, horas de operação e micro-paradas ficam em buckets horários. Micro-paradas
  entram na janela que contém o início do pedaço dentro do turno. **Peças**: entra na janela a parte do bucket que
  cai dentro da janela **e** dentro das instâncias de turno (POT), proporcional à sobreposição (peças supostas
  uniformes **na parte da hora com dados** — os `MachineDataSpan` da máquina; a hora corrente até agora; hora sem
  nenhum span: pelo tempo decorrido, regra antiga) — janelas vizinhas nunca contam duas vezes e a soma das
  partes é o total. Sprint 6 (QA, tenant recém-cadastrado com 1ª leitura às 07:28): repartir pela hora inteira
  punha 35/60 das peças de 07:28–08:00 na janela até 07:35, cujo APT só tinha 7 min com dados → P 2,06 e OEE 200 %;
  pela parte coberta a janela recebe 7/32 das peças (P 0,79). *Revisão do reliability-engineer pendente.* Janelas e turnos alinhados à hora cheia são exatos; quando algum bucket foi repartido a
  resposta traz `counts_prorated = true` e a UI avisa "contagem proporcional" (ex.: turno 05:30–14:00, consulta
  09:30–09:45). Antes, um bucket só contava inteiro se o seu início caísse na janela — 09:30–09:45 dava 0 peças e
  OEE 0. A produção de um
  bucket é repartida entre as ordens (`ProductionRun`) pela sobreposição com a hora; a parte sem ordem usa o
  produto padrão da máquina (`product_unknown = true`); sem produto padrão, o `ideal_cycle_time_s` da máquina;
  produto sem PRI cadastrado → P e OEE `null` (`insufficient_data`).
- **D8 — `run_state` de posição.** Máquina sem `run_state` próprio usa o da primeira posição (por criação) que o
  tenha — para paradas, MTBF (C23) e cadência em operação (ex.: transportador cujo `run_state` é o do motor).
  Sem nenhum `run_state`, paradas vêm do intervalo entre ciclos (contador de ciclos da máquina; senão
  `part_counter_total`; senão `part_counter_good`) com o PRI do produto padrão.
- **D9 — Turnos.** `weekdays`: 0 = segunda … 6 = domingo, avaliado no dia produtivo (o do início). Turnos ativos
  da mesma planta não podem se sobrepor (409). `planned_stop` sem horário cobre o turno (com `shift_id`) ou o dia.
- **D10 — TEEP por janela.** Tempo calendário = duração da janela consultada (limitada a "agora"); com
  `group_by=day`, a parte do dia local dentro da janela. Na planta (soma de linhas) o tempo calendário também é
  somado por linha.
- **D11 — Versões de turno.** O calendário não reescreve o passado. Editar horário, dias ou pausas, ou desativar um
  turno que já vigorou em algum dia produtivo anterior, **encerra a versão** (`valid_to` = dia anterior a
  `effective_from`, padrão hoje no fuso da planta) e cria uma nova (`valid_from = effective_from`, devolvida pelo
  `PATCH`, com outro `id`); exceções do turno a partir dessa data passam para a nova versão. Mudar só o nome é no
  lugar. `DELETE` de turno com paradas registradas → 409 (desative); turno que já vigorou é encerrado (ontem) em vez
  de apagado; só um turno criado hoje sem paradas é apagado. `GET /api/shifts/` lista as versões vigentes
  (`include_history=true` inclui as encerradas). Um turno **novo** vale também para os dias anteriores (não havia
  versão antes dele). A UI avisa, antes de salvar, que os dias anteriores mantêm o horário antigo.
- **D12 — Dados atrasados no detector de paradas.** O MQTT grava leituras concorrentemente e o gateway descarrega o
  buffer depois de reconectar, então leituras chegam ao InfluxDB fora de ordem. O detector (i) só avança o checkpoint
  sobre leituras que já estavam visíveis há `downtime_settle_s` (30 s); (ii) uma leitura gravada mais de
  `downtime_settle_s` depois do próprio timestamp marca a máquina (`late_from`) e a próxima passada volta ao último
  snapshot anterior (`downtime_detector_snapshots`, um a cada 15 min, 7 dias) e re-detecta: eventos por início
  (automáticos que deixaram de existir são removidos — os que o operador já tratou ficam, com log), micro-paradas
  restauradas do snapshot (nunca contadas duas vezes) e cobertura re-derivada; (iii) dentro de uma parada longa o
  checkpoint guarda o início dela (`down_since`), sem reler a parada inteira a cada passada. Dados mais antigos que
  o horizonte são gravados mas não re-detectados (log `downtime.detector.late_beyond_horizon`).

## Esclarecimentos Sprint 3

Revisão do contrato do Sprint 3 (OEE/paradas/Pareto pela API, motor em `server/src/services/oee/`) procurando
pontos em que duas implementações — ou a API e a UI — dariam números diferentes. Normativos como C1–C24 e D1–D10;
têm prioridade sobre texto ambíguo acima. O cenário de ouro **D** ([`docs/scenarios/`](scenarios/README.md#cenário-d--linha-com-gargalo-vários-turnos-e-dias-sprint-3))
exercita cada item; o código entre parênteses diz onde. As divergências encontradas na revisão (S3-1, S3-4, S3-5,
S3-6, S3-7, S3-8) foram corrigidas; `docs/scenarios/test_scenario_d.py` não tem mais `xfail`.

### S3-1 — OEE quando PBT = 0

- A resposta é **HTTP 200** (nunca 404/422/500 para um escopo válido sem tempo planejado) com `availability`,
  `performance`, `quality` e `oee` = `null` e `reason`:
  - `no_planned_time` — `POT − PDOT = 0`: feriado, fim de semana sem turno, turno inteiro em parada planejada;
  - `insufficient_data` — havia tempo planejado, mas todo ele sem dados (`data_coverage = 0`, D5);
  - `no_output_machine` — linha sem máquina de saída cadastrada (e por isso sem POT).
- **TEEP e Loading continuam definidos** sempre que o tempo calendário > 0: `Loading = PBT / calendário = 0` e
  `TEEP = Σ PRI·GQ dentro do POT / calendário = 0` (zero real: o ativo não gerou valor naquele tempo; o dia entra
  na média de TEEP do mês). Exceção: com `reason = insufficient_data` o TEEP é `null` (não se sabe o que foi feito).
  (D: feriado 12/10 → OEE `null`, Loading 0, TEEP 0; o TEEP do período 0,250347 inclui os 1 440 min do feriado.)
- `data_coverage` com POT = 0 é `null`. Cadências e `micro_stops_per_apt_hour` com denominador 0 são `null`.
- Pareto com PBT = 0: **lista vazia** (sem linhas de 0 min com `pct = null`). Com PBT > 0, linhas com 0 min PODEM
  ser omitidas; `pct` nunca é `null` quando o total de perdas é > 0.
- Agregação: um dia/turno com PBT = 0 contribui com 0 para todas as somas (é neutro no OEE do período) e com o seu
  tempo calendário para o TEEP.

### S3-2 — Desempenho > 100 %

- `P` **não** é truncado e `ideal_cycle_time_suspect = true` quando `P > 1` (estritamente). A, Q e OEE são
  calculados normalmente a partir das somas (o OEE também não é truncado). A UI mostra o valor e o aviso
  "tempo de ciclo ideal suspeito — revisar o cadastro (máquina, produto) ou a contagem".
- Agregação: soma-se **antes** de qualquer limitação — o P do dia é `Σ S / Σ APT` com os componentes brutos,
  nunca a soma de perdas de velocidade já truncadas dos turnos.
- Pareto: a perda de velocidade (`APT − Σ PRI·PQ − micro`) negativa vira **0** e o flag acompanha a resposta; nesse
  caso `Σ perdas ≠ PBT − Σ PRI·GQ` e o total do Pareto é a soma das linhas. Nenhuma outra linha é ajustada.
- Pontos horários (`group_by=hour`) com P > 1 por arredondamento de contagem (uma peça a mais numa hora) recebem o
  mesmo flag; a UI PODE suprimir o aviso em pontos horários se o turno não tiver P > 1.

### S3-3 — Produto desconhecido

Ordem de resolução do PRI das peças de cada bucket horário (D7):
1. a(s) `ProductionRun` que se sobrepõe(m) à hora — peças repartidas pela fração da hora coberta por cada uma;
2. a fração da hora **sem** ordem vai para o produto padrão da máquina (`is_default`), com `product_unknown = true`;
3. sem produto padrão: `Machine.ideal_cycle_time_s`, também com `product_unknown = true`;
4. sem nenhum PRI: P, Q e OEE `null` com `reason = insufficient_data`; **A continua** sendo calculada (não depende
   de PRI). O TEEP também fica `null`.

Para que (2) não reparta peças de um produto para outro, a troca de produto DEVE ser contígua: **a ordem nova abre
no início do setup** (o setup pertence à ordem que ele prepara) e a ação "trocar produto" fecha a ordem anterior
no mesmo instante — nunca há buraco entre ordens consecutivas. (D: SUP-100 06:00–11:00, SUP-200 a partir de 11:00,
setup `SET-01` 11:00–11:30 dentro da ordem nova.) Quando a máquina tiver a tag `product_code` (receita do CLP), ela
PODE abrir/fechar as ordens automaticamente (Sprint 4). `product_unknown = true` na resposta faz a UI mostrar
"produto não informado em parte do período".

### S3-4 — Paradas planejadas (`PLN-*`) informadas fora das pausas

- **Antes do turno** o lugar delas é o calendário (`planned_stop` com horário, `shift_id` opcional, D9): PDOT.
- **Durante/depois do turno**, uma parada detectada (ou manual) reclassificada com `PLN-01`, `PLN-02`, `PLN-04` ou
  `PLN-05` vira `planned = true` e a sua parte dentro do tempo programado e **fora** das pausas do turno vai para
  **PDOT** (sai do PBT); a parte que coincide com uma pausa não é contada duas vezes (união de intervalos); a parte
  fora do POT é ignorada. Uma parada não planejada que se sobrepõe a uma planejada perde a parte sobreposta (a
  planejada prevalece). Ao (§2.6): `PLN-01/02/04/05` saem de T_req. (D: DDS `PLN-02` 14:00–14:15 → PDOT 45 e
  PBT 395 no S4; T_req 395.)
- **`PLN-03` (preventiva) informada sem estar no calendário antes do início do turno é `MNT-02`** (domain.md §4.1):
  a API DEVE responder 422 com a sugestão `MNT-02` quando o operador tentar `PLN-03` numa parada que não está coberta
  por um `planned_stop PLN-03` do calendário (`detail = {message, suggested_reason_code: "MNT-02"}`; vale para
  `POST /api/downtime/` e `PATCH /api/downtime/{id}`; "coberta" = a parada se sobrepõe à janela PLN-03).
- Pausas de turno e exceções `planned_stop`/`extra_shift` só aceitam motivo **planejado** existente (`PLN-*` ou
  código do tenant com `planned = true`), senão 422 — uma pausa com `EQP-*` tiraria perda real do PBT e do Pareto.
- Toda reclassificação para `PLN-*` fica auditada (`justified_by`, `justified_at`) e aparece em separado no relatório
  de turno ("paradas planejadas declaradas no turno"), porque reduz o PBT e melhora A. O tenant PODE exigir papel
  supervisor para ela (configuração; padrão: operador pode).
- Parada manual `PLN-*` sobre um buraco de dados conta como coberta (D5) e como PDOT.

### S3-5 — Arredondamento e precisão dos valores da API

- **Frações** (A, P, Q, OEE, TEEP, Loading, Ao, `data_coverage`, `pct` e `cumulative_pct` do Pareto, `life`):
  `float` IEEE-754 de 64 bits **sem arredondamento** no servidor; calculadas sempre a partir das somas em
  segundos (nunca de valores já arredondados). Nunca `NaN`/`Infinity` no JSON: indefinido é `null` + `reason`.
- **Tempos**: segundos (`*_s`) em `float`, resolução de milissegundo (timestamps de origem); minutos/horas só na UI.
  **Contagens** (`produced`, `good`, `scrap`, `micro_stop_count`, `events`, `failures`): inteiros no total do
  período/turno; repartições por produto dentro de uma hora (S3-3) PODEM ser fracionárias e só aparecem no
  detalhamento por produto.
- **Timestamps** em ISO 8601 com fuso (`Z`); datas de dia produtivo (`productive_day`) como `YYYY-MM-DD` local.
- **Testes**: tolerância absoluta `1e-6` para frações, relativa `1e-6` para valores > 1; `pct` do Pareto `5e-7`.
- **UI** (pt-BR, `Intl.NumberFormat`): percentuais com **1 casa** (`75,4 %`), arredondamento meio-para-cima do valor
  bruto; o acumulado do Pareto é calculado do bruto (a última linha mostra sempre `100,0 %`, mesmo que a soma das
  linhas arredondadas dê 99,9 %); MTBF/MTTR com 1 casa (`1 733,3 h`, `83,3 min`); MCBF e contagens inteiros com
  separador de milhar; durações ≥ 10 min sem casas, < 10 min com 1 casa. Nenhum KPI é recalculado na UI a partir de
  valores arredondados. (`formatPct` em web/src/utils/format.ts; espaço não separável antes do `%`.)

### S3-6 — Feriado e o turno que entra nele

Exceções de calendário valem para o **dia produtivo**: `holiday` em 12/10 remove os turnos que **começam** em 12/10.
O turno C que começa às 22:00 de 11/10 roda até 06:00 do feriado e é de 11/10 (C19). Para não programá-lo, o
planejador cadastra também `holiday` com `shift_id = C` em 11/10 (o mesmo recurso cancela um turno de um dia só:
no D, o C de 13/10). Um turno extra que se sobrepõe a uma instância de turno ativa do mesmo dia DEVE ser
rejeitado (409, como D9), senão o POT contaria o tempo duas vezes — inclusive contra outro turno extra do mesmo
período (`POST`/`PATCH /api/calendar-exceptions/`).

### S3-7 — Produção fora do calendário

Peças contadas fora de qualquer instância de turno (teste da manutenção no feriado, linha rodando sem turno extra
cadastrado) **não** entram em PQ/GQ do OEE nem no numerador do TEEP — entrariam sem o tempo correspondente e
inflariam P. São devolvidas em `produced_outside_schedule` (boas/refugo/total) e a UI avisa "produção fora do
calendário — cadastrar turno extra?". Continuam no histórico de produção (`/api/production`, buckets). (D: 12 peças
em 12/10 09:00–10:00.)

### S3-8 — Total de uma consulta agrupada

Com `group_by=shift|day`, o total da resposta DEVE ser a **soma dos componentes dos pontos** (mesmas instâncias de
turno), para que série e total nunca discordem. Sem `group_by`, a janela é tratada como intervalo livre: as
instâncias de turno são cortadas em `[start, stop)`, o que inclui o rabo do turno noturno da véspera quando
`start` é meia-noite — por isso as telas "ontem/hoje" usam o dia produtivo (relatório de turno ou `group_by=day`),
nunca um intervalo 00:00–00:00. O tempo calendário do TEEP agrupado por dia é 1 440 min por dia produtivo da
janela (D10); com `group_by=shift`, o tempo calendário é a janela consultada.

### S3-9 — Buraco de dados com contagem

Peças reveladas pela primeira leitura depois de um buraco **contam** (o contador do CLP é a verdade da quantidade,
C9) e o buraco continua fora do PBT (D5). Isso superestima P do período na proporção do buraco; o aviso de
`data_coverage < 0,95` é o sinal para o supervisor **declarar** o que aconteceu (parada manual → ADOT e coberto,
ou nada, se a linha rodou). No D o buraco não tem peças (a linha estava parada): sem declaração, S4 fica com
PBT 395 e OEE 0,891139; com `UTL-01` declarada, PBT 435 e OEE 0,809195 — por isso a parada sem dados deve ser
justificada no fluxo de paradas, como as demais.

---

## Indicadores da preditiva (Sprint 5)

Definições completas, casos de borda e exemplos numéricos em [`docs/predictive.md`](predictive.md) P-6 (RUL de
condição) e P-10 (avaliação), com os números do cenário F ([`scenarios/README.md`](scenarios/README.md#cenário-f--preditiva-sprint-5)).
Resumo para quem procura aqui:

| Indicador | Fórmula | Unidade | Exemplo (cenário F) |
|---|---|---|---|
| Antecedência do alerta | `failed_at − opened_at` (também por severidade) | h | F1: 420 h (`warning`), 264 h (`critical`); só com limite fixo: 96 h |
| Precisão | VP / (VP + FA), por severidade mínima | fração | 0,5 (`≥ watch`), 1,0 (`≥ warning`) |
| Falsos alarmes por posição-ano | FA / dias monitorados × 365 (sem períodos de reaprendizado) | 1/ano | 1 / 73,5 × 365 = 4,966 (`watch`) |
| RUL de condição | `(L_f − x̂) / b`, faixa `(L_f − x̂)/(b ± 2·SE)`; dias pelo ritmo de 14 dias (C17/C18) ou plano de produção | ciclos/h → dias | F1 bloco 50: 215 806 ciclos → 11 dias (8–16) |
| `predictive_no_defect_hours` | horas de OS preditivas concluídas com `finding = no_defect` | h | 2,0 (PMP continua 0,9375 — preditiva é planejada) |

Pergunta de toda manhã nº 11 (complementa §5): **Manutenção** — "O que está degradando e quando vai falhar?" →
tela Preditiva (`/maintenance/predictive`) → alertas por severidade, RUL de condição com faixa (predictive.md P-8).

## Decisões Sprint 5

- **S5-1 — Falha de equipamento que começa durante pausa planejada ou fora do turno** (achado do QA do Sprint 4:
  nada era criado até a pausa acabar). Decisão do dono do produto, normativa:
  - O `FailureRecord` rascunho (`failed_at` = instante em que `fault_state` ficou ≠ 0, `detection_method =
    production_interference`) e o **chamado emergencial** aos técnicos de plantão são criados **imediatamente**,
    independentemente do calendário — a máquina quebrou e precisa de manutenção mesmo sem tempo de produção perdido.
    Enquanto não há tempo produtivo envolvido a falha não tem `DowntimeEvent` (`downtime_event_id` nulo) e aparece
    como **aberta** em Falhas; o escalonamento (> 2 × MTTR) vale para ela também.
  - **OEE inalterado**: o tempo dentro da pausa continua PDOT; o tempo fora do turno continua fora do POT. Uma parada
    inteiramente na pausa/fora do turno continua sem evento (D4/C24d).
  - Quando o tempo produtivo recomeça com a falha ainda ativa, o `DowntimeEvent` cobre **só a parte produtiva** (começa
    no fim da pausa / no início do turno) e é ligado à **mesma** falha (uma falha por parada, D3); a OS emergencial
    aberta na pausa passa a apontar para essa parada. Só paradas com falha (`fault_state ≠ 0`) são cortadas assim; as
    demais paradas que começam numa pausa mantêm a forma do Sprint 3 (evento desde o início, parte na pausa = PDOT).
  - **MTTR** usa o tempo de relógio de `failed_at` até o restabelecimento. Restabelecimento: o fim da parada (D1) — exceto
    quando a falha **some fora do tempo produtivo** (na pausa ou fora do turno): aí `restored_at` = instante em que
    `fault_state` voltou a 0, porque ninguém religa a máquina antes do turno e essa espera não é reparo. Ex.: falha
    10:05–10:20 numa pausa 10:00–10:30 → `restored_at` 10:20, MTTR 15 min, sem evento; falha 10:05 numa pausa
    10:00–10:15 que só some às 10:40 → evento 10:15–10:40 (ADOT 25 min, PDOT da pausa 15 min), MTTR 35 min.
  - Releitura/atraso de dados (rewind) que apaga a falha remove o rascunho sem evento, salvo se já houve chamado/OS.
  - Testes: `server/tests/test_break_fault_s5.py`.

### S5-1 — Exemplo trabalhado (caso `break_fault_shift`)

Torno CNC (Linha de Usinagem, Planta Manaus), dia produtivo 05/10, **Turno A 06:00–14:00**, produto único com
PRI = 30 s, `micro_stop_threshold_s = 120`. Pausas do turno (`PLN-01`): café **10:00–10:15** e refeição
**12:00–12:30** → PDOT 45 min, PBT 435 min. Quatro falhas com código (`fault_state ≠ 0`), mapeadas para `EQP-*`:

| # | `fault_state ≠ 0` | `run_state` | Onde começa | `DowntimeEvent` (ADOT) | `failed_at` → `restored_at` | Tempo p/ restabelecer |
|---|---|---|---|---|---|---|
| F1 | 08:00–08:20 | 0 de 08:00 a 08:20 | produção | 08:00–08:20 (**20 min**) | 08:00 → 08:20 (fim da parada, D1) | 20 min |
| F2 | 10:05–10:40 | 0 de 10:00 (pausa) a 10:40 | café | **10:15**–10:40 (**25 min**; só a parte produtiva) | 10:05 → 10:40 (fim da parada) | 35 min |
| F3 | 12:10–12:20 | 0 de 12:00 a 12:30 (volta no fim da pausa) | refeição | nenhum (falha sumiu dentro da pausa) | 12:10 → **12:20** (`fault_state` = 0) | 10 min |
| F4 | 14:30–15:00 | 0 desde 14:00 (fim do turno) | fora do turno | nenhum (fora do POT) | 14:30 → **15:00** (`fault_state` = 0) | 30 min |

Produção: PQ = 760, GQ = 745 (SQ = 15). Sem micro-paradas.

- **OEE (inalterado pela decisão)**: ADOT = 20 + 25 = 45 min → APT = 390 min. Σ PRI·PQ = 380 min, Σ PRI·GQ = 372,5 min.
  A = 390/435 = **0,896552**; P = 380/390 = **0,974359**; Q = 745/760 = **0,980263**; OEE = 372,5/435 = **0,856322**.
  PDOT continua **45 min**: os 10 min de F2 dentro do café (10:05–10:15) e toda a F3 são PDOT; F4 está fora do POT.
- **Ao** (§2.6): T_req = 480 − 45 = 435 min; D_eq = paradas `EQP-*` dentro de T_req = 45 min → Ao = 390/435 = **0,896552**.
  A parte de F2 dentro do café e as falhas F3/F4 **não** entram em D_eq (não havia tempo requerido).
- **Confiabilidade do dia** (janela 05/10 00:00–24:00 local, por `failed_at`): n = **4** (todas `critical`, D3);
  tempo de operação = Σ `run_state = 1` = 390 min = 6,5 h (C23) → MTBF = 6,5/4 = **1,625 h**;
  MTTR = (20 + 35 + 10 + 30)/4 = 95/4 = **23,75 min**; `restored = 4`.
- **Chamados**: F1, F2, F3 e F4 geram o chamado emergencial no primeiro ciclo do motor depois de ficarem ativas por
  `micro_stop_threshold_s` (ver S5-1b); F3 e F4 geram OS emergencial **sem** `downtime_event_id`; a OS de F2 passa a
  apontar para o evento 10:15–10:40 quando ele abre.

Respostas erradas (o teste garante que nenhuma coincide com o certo):

| Interpretação errada | Resultado |
|---|---|
| comportamento do Sprint 4 (falha só nasce quando a pausa acaba; F3/F4 não existem; F2 com `failed_at` 10:15) | n = 2, MTTR = (20 + 25)/2 = 22,5 min, MTBF = 3,25 h |
| contar a parte de F2 dentro do café como ADOT (evento desde 10:05) | ADOT 55, A = 380/435 = **0,873563** (e Ao idem) — a pausa viraria perda de disponibilidade |
| MTTR de F2 pela duração do evento (25 min) | MTTR = (20 + 25 + 10 + 30)/4 = 21,25 min |
| `restored_at` de F4 = início do próximo turno (06:00 de 06/10) | MTTR = (20 + 35 + 10 + 930)/4 = 248,75 min |

### S5-1b — Falha curta fora do tempo produtivo (complemento normativo de S5-1)

O limiar de C22 vale também aqui, aplicado à **duração da própria falha** (não da parada, que numa pausa é longa
por definição): uma falha que começa **e termina** fora do tempo produtivo com `fault_until − fault_since <
micro_stop_threshold_s` é contada só como `short_faults`, **não** gera `FailureRecord` nem chamado. Uma falha ainda
ativa só gera o rascunho e o chamado quando completa o limiar (é a mesma espera do chamado em produção:
maintenance_workflows.md §5, "após o limiar de parada"). Motivo: em pausa/limpeza é comum um alarme de porta aberta
ou de emergência acionada por segundos; sem o limiar, cada um viraria chamado de plantão e falha no MTBF.
Exemplo (caso `break_short_fault`): pausa 10:00–10:30, `fault_state = 7` de 10:05:00 a 10:05:20 → 20 s < 120 s →
`short_faults = 1`, nenhuma falha, nenhum chamado; se durasse 10:05:00–10:07:00 (120 s, limiar inclusivo como C22)
→ 1 falha, `restored_at` 10:07, MTTR 2 min.
**Divergência encontrada** (Sprint 5, verificada contra `server/src/services/oee/detector.py`): a falha de
20 s dentro da pausa criava `FailureRecord` (o filtro de C22 olhava a duração da parada). **Corrigido** no fix do
Sprint 5 (`detection.short_offtime_fault`): sem rascunho nem chamado; a falha curta entra em `short_faults` no bucket
de micro-paradas da hora/turno da pausa (fora de turno não há bucket: não é contada). Casos em
`server/tests/test_break_fault_s5.py` (`break_short_fault`: 20 s → `short_faults = 1`, 0 falhas; 120 s → 1 falha,
MTTR 2 min; falha ainda ativa só vira rascunho ao completar o limiar).

### S5-1c — Revisão de consistência com ISO 14224 e ISO 22400

| # | Ponto | Norma | Conclusão |
|---|---|---|---|
| 1 | Falha registrada mesmo sem tempo de produção | ISO 14224 (definição de *failure*, seção 3 — confirmar número da cláusula): término da capacidade de o item desempenhar a função requerida; o registro de falha independe de o item estar em produção (há falhas em espera/*standby* e "falha ao partir") | **consistente**: o `FailureRecord` nasce do estado do equipamento, não do calendário |
| 2 | Tempo dentro da pausa = PDOT; fora do turno = fora do POT | ISO 22400-2: PDOT é tempo planejado sem produção; ADOT só existe dentro do PBT | **consistente**: o OEE mede perda do tempo planejado para produzir; a falha na pausa não tirou tempo planejado |
| 3 | Pausa que se estende por causa da falha (F2 10:15–10:40) | ISO 22400-2: parada não planejada dentro do PBT → ADOT | **consistente** (ADOT 25 min, motivo `EQP-*`) |
| 4 | MTTR desde `failed_at`, inclusive a parte na pausa | ISO 14224 / IEC 60050-192: *time to restoration* = do instante da falha até o item voltar ao estado de funcionar (inclui detecção, espera e reparo) | **consistente**; MART (§2.3) continua sendo o tempo ativo de reparo, quando apontado |
| 5 | `restored_at` = `fault_state` volta a 0 fora do tempo produtivo | IEC 60050-192: restabelecimento = item capaz de desempenhar a função; a espera pelo turno é tempo não requerido, não indisponibilidade | **consistente** como *proxy*; a UI mostra "restabelecido (falha zerada no CLP)" para diferenciar de "voltou a produzir" |
| 6 | MTBF por **tempo de operação** com falhas que ocorreram parado | ISO 14224 admite base de tempo **calendário** ou **operacional**, desde que declarada | **consistente, com ressalva**: F3/F4 entram em n mas o tempo parado não entra no denominador → λ por hora de operação fica um pouco maior (conservador). A resposta de confiabilidade DEVE trazer `failures_outside_production` (n das falhas cujo `failed_at` cai fora do tempo produtivo — pausa ou fora do turno; no exemplo F2, F3 e F4 = 3) para o analista separar |
| 7 | §2.3 "só falhas que pararam a produção (com `DowntimeEvent`) entram no MTTR" | — | **conflito de texto, resolvido**: vale S4-10 (domain.md): entram no MTTR todas as falhas `critical`/`degraded` **com `restored_at`**; F3/F4 têm `restored_at` e entram. A frase de §2.3 deve ser lida como "falha achada em inspeção sem restabelecimento não entra" |
| 8 | PMP (§3.1) | EN 15341 | a OS emergencial de F3/F4 é `corrective` `emergency` → horas não planejadas, mesmo sem perda de produção (consistente: foi trabalho não programado) |
| 9 | Escalonamento > 2 × MTTR | — | vale para falha sem evento (S5-1); F4 fora do turno escalona para o plantão, não para o supervisor de produção (não há turno) |

**Nota de nomenclatura**: "S5-1" aqui (falha na pausa) e "S5-1" em domain.md "Esclarecimentos Sprint 5"
(gatilho de condição) são itens diferentes. Citar como **kpis S5-1** e **domain S5-1**.

```json kpi-s5-cases
[
  {"id": "break_fault_shift", "kpi": "break_fault",
   "input": {"shift": ["06:00", "14:00"], "breaks": [["10:00", "10:15"], ["12:00", "12:30"]],
             "ideal_cycle_time_s": 30, "produced": 760, "good": 745, "micro_stop_threshold_s": 120,
             "faults": [{"id": "F1", "fault": ["08:00", "08:20"], "run_zero": ["08:00", "08:20"]},
                        {"id": "F2", "fault": ["10:05", "10:40"], "run_zero": ["10:00", "10:40"]},
                        {"id": "F3", "fault": ["12:10", "12:20"], "run_zero": ["12:00", "12:30"]},
                        {"id": "F4", "fault": ["14:30", "15:00"], "run_zero": ["14:00", "15:00"]}]},
   "expected": {"pot_min": 480, "pdot_min": 45, "pbt_min": 435, "adot_min": 45, "apt_min": 390,
                "events": [["F1", "08:00", "08:20"], ["F2", "10:15", "10:40"]],
                "restored": {"F1": "08:20", "F2": "10:40", "F3": "12:20", "F4": "15:00"},
                "availability": 0.896552, "performance": 0.974359, "quality": 0.980263, "oee": 0.856322,
                "ao": 0.896552, "n": 4, "operating_h": 6.5, "mtbf_h": 1.625, "mttr_min": 23.75,
                "failures_outside_production": 3},
   "wrong_answers": {"sprint4_mttr_min": 22.5, "sprint4_mtbf_h": 3.25, "break_as_adot_availability": 0.873563,
                     "mttr_by_event_min": 21.25, "mttr_restored_next_shift_min": 248.75}},
  {"id": "break_short_fault", "kpi": "break_fault",
   "input": {"shift": ["06:00", "14:00"], "breaks": [["10:00", "10:30"]], "micro_stop_threshold_s": 120,
             "faults_s": [{"id": "short", "start": "10:05:00", "end": "10:05:20"},
                          {"id": "limit", "start": "10:05:00", "end": "10:07:00"}]},
   "expected": {"short": {"failure": false, "short_fault": true},
                "limit": {"failure": true, "restored": "10:07:00", "mttr_min": 2.0}}}
]
```

## Decisões Sprint 7

Fechamento de todos os itens abertos da auditoria independente ([`audit_kpis.md`](audit_kpis.md), A-2 e A-4…A-20).
Onde a especificação tinha lacuna, a decisão abaixo é **normativa** a partir deste sprint (no espírito do §0: indefinido
→ `null` + `reason`, nada recalculado na UI, nenhum número sem aviso). Os contraexemplos da auditoria são testes:
`docs/scenarios/test_audit_kpis.py` (funções puras) e `server/tests/test_audit_s7.py` (API, banco e InfluxDB).
Os casos de §6 e os cenários de ouro A–F continuam exatos.

- **S7-1 — Falhas sem tempo de operação (A-4).** Com `n > 0` e tempo de operação 0 ou desconhecido (máquina sem
  `run_state` nem contador, D8), `MTBF`, `Ai` e `λ` são `null` com `reason = insufficient_data` (não 0 h). `MTTR`,
  `n` e `failures_outside_production` continuam calculados. `n = 0` segue `reason = no_failures`.
- **S7-2 — Dias de histórico do ritmo (A-2, A-18; vale também para planos e RUL de condição).** Dias de histórico =
  `min(14, agora − installed_at, agora − início dos dados)`, onde início dos dados = o mais antigo entre o 1º bucket de
  uso do componente, o da sua posição, o da máquina e o 1º intervalo com dados (`machine_data_spans`) da máquina.
  Menos de 3 dias → sem projeção (`reason = insufficient_data`; no risco condicional do Weibull: `p_fail = null`,
  `p_reason = insufficient_data`); menos de 14 → `low_confidence = true` (RUL nominal, RUL de condição
  `rate_window_days`, risco condicional `low_confidence`). Planos: dias com dados da âncora (A-3, Sprint 6).
  Ex. (U3 com instalação antiga): instalado há 400 dias, dados há 5 dias, 100 000 ciclos → 20 000/dia → 70 dias,
  14/12/2026, `low_confidence` (a regra antiga dava 196 dias sem aviso).
- **S7-3 — `reason = no_running_time` (A-15).** Acrescentado à lista de §0: PBT > 0 inteiramente perdido (APT = 0) com
  peças contadas → `performance = null`, `reason = no_running_time` (A = 0 real, OEE pela fórmula de §1.5).
- **S7-4 — PRI da linha sem o produto no gargalo (A-13).** Regra: PRI da linha = PRI do gargalo para o produto; produto
  sem PRI no gargalo usa o PRI da **máquina de saída** e a resposta traz `line_pri_fallback = true` (OEE e Pareto da
  linha/planta). Sem PRI em nenhuma das duas → `insufficient_data` (S3-3, inalterado).
- **S7-5 — Janela do ritmo: horas completas (A-10; emenda C17).** Para a RUL nominal, os planos, a RUL de condição
  (P-7) e o risco condicional, a janela são os buckets horários **completos** `[hora(agora) − 14 d, hora(agora))`
  (336 buckets) divididos por 14 (ou pelos dias de S7-2). A hora corrente, ainda enchendo, fica de fora: avaliar ao vivo
  e recalcular depois dão o mesmo ritmo (P-12). Ex. (uso uniforme 20 000/dia, avaliação às 10:30): 20 000/dia nos
  quatro cálculos (antes: condição 19 985,12; nominal 20 029,76). Os exemplos U3/U4 não mudam (dados por dia).
- **S7-6 — MTTF simples com idade 0 (A-9).** Componente removido por falha sem uso registrado (`t ≤ 0`: importado,
  removido antes do gateway) fica **fora** da média, como no Weibull (reports.md §2.1), e é contado em
  `excluded_no_usage` (`mttf_excluded_no_usage` na API). Todos excluídos → `null`, `reason = insufficient_data`.
  Ex.: 8 200, 9 600, 10 400 h e um com idade 0 → 9 400 h, `excluded_no_usage = 1`.
- **S7-7 — Falha no instante da troca (A-12).** Intervalo de instalação semiaberto `[installed_at, removed_at)`: a falha
  registrada no instante exato da troca é do componente **novo** (determinístico, independe da ordem da consulta).
- **S7-8 — Cumprimento: concluída depois do período (A-11).** OS programada no período e concluída **depois do fim do
  período** conta como **não feita** (S4-6); "atrasada" é só a concluída dentro do período após o prazo. O número de um
  período fechado não depende do dia em que o relatório é gerado (reports.md princípio 4). No prazo pela tolerância
  continua no prazo.
- **S7-9 — Ao: sobreposição com parada planejada declarada (A-14).** A parte de uma parada `EQP-*`/`PLN-03`/`MNT-02` sob
  uma parada planejada declarada `PLN-01/02/04/05` está fora de T_req e **não** entra em D_eq (a planejada prevalece,
  S3-4); a união das planejadas é descontada uma vez. Ex.: DDS `PLN-02` 10:00–10:30 sobre falha 10:00–10:20 → Ao = 1,0;
  falha 10:00–10:40 → D_eq = 10 min, Ao = 440/450.
- **S7-10 — `failures_outside_production` (A-6, S5-1c item 6).** n das falhas `critical`/`degraded` cujo `failed_at`
  não é tempo produtivo **pelo calendário** da planta da máquina: fora de instância de turno, dentro de pausa /
  `planned_stop` do calendário, ou dentro de um `DowntimeEvent` planejado declarado. Devolvido em `GET /api/reliability`
  e em toda linha de `GET /api/maintenance/reliability`; o relatório mensal usa esse campo (o *proxy* "sem
  `downtime_event_id`" foi removido). `break_fault_shift`: 3.
- **S7-11 — Ciclos estimados (A-7).** Linhas de confiabilidade por posição, tipo de componente e fabricante/modelo
  trazem `estimated = true` quando os ciclos de alguma posição do grupo vêm de peças × `cycles_per_part` (posição sem
  tag de ciclo própria, topologia atual — C14). Linhas por máquina: `estimated = false` (ciclos de máquina só vêm de
  contador de ciclos da máquina).
- **S7-12 — λ por ciclos (A-8).** `failure_rate_per_million_cycles = n / ciclos × 10⁶` (ciclos 0/sem fonte → `null`;
  `n = 0` com ciclos → 0). Ex. R5: 3 / 2 400 000 → 1,25.
- **S7-13 — Cadência ao vivo do mapa (A-19).** `cadence_per_hour` do mapa = peças dos **últimos 15 min** × 4 a partir das
  **leituras brutas** do contador (InfluxDB: `part_counter_total`, senão bom + refugo; regras de delta de domain.md §3.3
  a partir da última leitura até o início da janela) — `cadence_source = counter`. Sem leituras ou InfluxDB
  indisponível: estimativa pelos buckets horários (`cadence_source = buckets`, `cadence_prorated`), que a UI sinaliza.
  `cadence_shift_per_hour` continua a cadência realizada do turno (PQ/PBT). Ex.: 400 peças 06:00–09:45 e 15 peças
  09:45–10:00 → 60 pç/h (os buckets davam ~115).
- **S7-14 — MART (A-20).** `mart_s`/`mart_min` = média de `repair_finished_at − repair_started_at` das falhas contadas
  em `n` que têm os dois apontados; `repairs_timed` = quantas; nenhuma → `null`, `mart_reason = no_repair_times`.
  Nas duas APIs de confiabilidade. Separa espera (MTTR − MART) de reparo.
- **S7-15 — Relógio da hora corrente nas horas de operação (A-16).** O bucket da hora corrente é limitado pelo `now`
  de quem calcula (`services.clock`/o `now` do motor), nunca pelo relógio de parede.
- **S7-16 — Detectores depois de longas quedas (não muda fórmula).** O detector de paradas processa o atraso em
  fatias limitadas (`DOWNTIME_SLICE_READINGS` leituras por tag) que avançam o checkpoint; o resultado (eventos,
  falhas, micro-paradas, intervalos com dados) é **idêntico** ao do processamento contínuo — prova:
  `server/tests/test_detector_catchup.py` (72 h de buffer descarregado de uma vez).
- **S7-17 — Nenhum motor para por causa de atraso (não muda fórmula).** Além do detector de paradas (S7-16): a
  preditiva extrai janelas/blocos por no máximo `PREDICTIVE_EXTRACT_BUDGET_S` por execução, grava e continua do
  cursor na próxima (série idêntica à de uma execução única — `server/tests/test_backlog_engines.py`); o motor de
  manutenção roda chamados emergenciais e escalonamentos **antes** do recálculo dos planos, com commit próprio (um
  plano lento nunca atrasa um chamado); os detectores rodam por máquina/monitor/tenant com concorrência limitada no
  processo worker. As máquinas em dia de um tenant compartilham uma consulta ao InfluxDB por ciclo — mesmos eventos,
  falhas e micro-paradas que uma consulta por máquina (`test_detector_catchup.py::test_tenant_prefetch_…`).
- **S7-18 — Agregados do InfluxDB só para gráficos de séries (não muda KPI).** Bruto 90 dias, agregados de 1 min por
  2 anos e horários para sempre (`docs/performance.md` §5). Os agregados guardam soma, contagem, mín., máx. e último
  valor por janela, então a média de qualquer janela ≥ 1 min é **exata** (Σsoma/Σcontagem), igual à do bruto
  (`server/tests/test_influx_downsampling.py`). Nenhum KPI (OEE, paradas, uso, MTBF, preditiva) é calculado dos
  agregados: os detectores leem o bruto no momento em que os dados chegam e gravam o resultado no Postgres; depois de
  90 dias o bruto expira, os KPIs já calculados ficam. Recalcular uso/preditiva além de 90 dias não é possível
  (`/recompute` avisa — o bruto não existe mais).
- **S7-19 — Cache curto das telas de painel (não muda número).** `/api/overview`, `/api/oee`, `/api/losses/pareto` e
  `/api/maintenance-plans/status` guardam a resposta por `API_CACHE_TTL_S` (10 s) por usuário e consulta; qualquer
  gravação do próprio tenant neste processo invalida na hora (o usuário vê a própria justificativa/OS). Dados da
  ingestão e dos detectores aparecem em até 10 s — a mesma ordem do ciclo dos detectores (`DOWNTIME_TICK_S` = 10 s).
