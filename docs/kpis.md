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
`error` no meio (um evento com motivo sugerido `EQP-*`, ou dois eventos?).
