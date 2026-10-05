# Cenários de ouro (golden scenarios)

Entradas exatas e resultados esperados que o QA usa para **provar os números de ponta a ponta** no Sprint 2
(uso e produção) e no Sprint 3 (OEE e confiabilidade). Cada valor esperado sai das fórmulas de
[`docs/domain.md`](../domain.md) e [`docs/kpis.md`](../kpis.md), incluindo a seção **"Esclarecimentos Sprint 2"**
(códigos C1–C24 citados abaixo). A conta está escrita aqui, passo a passo.

| Arquivo | Cenário | Sprint | O que prova |
|---|---|---|---|
| [`scenario_a_usage.json`](scenario_a_usage.json) | A — Uso | 2 | contador de 16 bits com rollover, reset de CLP, leitura corrompida, duplicata e fora de ordem → `cycles_total`; `run_state` de motor com quedas < 1 s (debounce) e buraco de dados → `hours_total`, `starts_total`; `life_consumed_pct` e `projected_end_date` |
| [`scenario_b_production.json`](scenario_b_production.json) | B — Produção | 2 | contadores de peças boas/refugo da máquina de saída num turno que cruza a meia-noite → boas/refugo/total, cadência por hora no fuso da planta, agrupamento por dia |
| [`scenario_c_oee_shift.json`](scenario_c_oee_shift.json) | C — OEE (prévia) | 3 | turno de 8 h com pausas, falhas com código, micro-paradas abaixo/acima do limiar → A, P, Q, OEE, MTBF, MTTR, Ao, Pareto de perdas |
| [`scenario_d_line_multiday.json`](scenario_d_line_multiday.json) | D — Linha, vários turnos e dias | 3 | linha com gargalo, mix de 2 produtos com 1 setup, turno que cruza a meia-noite (e entra no feriado), feriado, turno de um dia só cancelado, turno extra, DDS reclassificado `PLN-02`, buraco de dados → OEE por turno/dia/período, TEEP, `data_coverage`, Pareto |
| [`scenario_e_maintenance.json`](scenario_e_maintenance.json) | E — Manutenção | 4 | planos por ciclos/horas/partidas/calendário (o que vier primeiro), ciclo de vida das OS, troca de componente zerando o plano, falhas por posição → % do intervalo, próxima data/uso, PMP, cumprimento da programação, backlog, MTBF/MCBF/MTTR por posição/tipo/fabricante, MTTF por fabricante/modelo |
| [`test_golden_scenarios.py`](test_golden_scenarios.py) | — | — | oráculo independente que recalcula todo `expected` a partir das entradas + conformidade com `server/src/services/usage/core.py` |
| [`test_scenario_d.py`](test_scenario_d.py) | — | — | oráculo do D + conformidade com o motor de OEE (`server/src/services/oee/`: calendário → janelas → componentes → `kpi.oee`/`kpi.pareto`); divergências conhecidas ficam `xfail` com o código do esclarecimento |
| [`test_scenario_e.py`](test_scenario_e.py) | — | — | oráculo do E (o módulo de manutenção é do Sprint 4; os testes da API dele carregam o mesmo JSON) |

```bash
.venv/bin/python -m pytest docs/scenarios -q      # o oráculo não depende do servidor; a conformidade é pulada se o server não importar
```

## Convenções

- Planta **Planta Manaus**, fuso `America/Manaus` = **UTC−4 fixo** (o Amazonas não tem horário de verão).
  Local = UTC − 4 h. No JSON todo `ts` é UTC (`Z`); o campo `local` é só para leitura.
- Nomes de ativos iguais aos da planta virtual (`edge/virtual_plant/layout.py`).
- Frações (`life_consumed_pct`, A, P, Q, OEE) vão de 0 a 1, sem arredondamento (C16). Tolerância dos testes: `1e-6`.
- Cada leitura de contador traz `expect_delta` e, quando for o caso, `expect_event` / `expect_ignored`: o QA
  confere leitura por leitura, não só o total.
- `wrong_answers` lista os números que saem de interpretações erradas (comuns). O número certo nunca coincide
  com eles, então um teste que passa não pode estar "certo por acaso".
- Configuração usada: `machine_offline_timeout_s = 60`, `run_state_debounce_s = 1,0` (padrões de `server/src/config.py`).

---

## Cenário A — Uso

### A1. Cilindro A1: contador de ciclos de 16 bits no CLP

Linha de Montagem › Estação de Fixação (`ideal_cycle_time_s = 40`) › posição **Cilindro A1**
(`pneumatic_cylinder`, `cycles_per_part = 1`). Tag `cycle_counter`, `counter_max = 65 535`,
`counter_tag_id` da posição, sem `max_rate_per_s` próprio.

`max_rate = 2 × cycles_per_part / ideal_cycle_time_s = 2 × 1 / 40 = 0,05 ciclo/s` (C4).
Limite de plausibilidade para leituras a cada 10 min: `0,05 × 600 × 1,5 + 10 = 55`.

Leituras de 2026-10-05 (local; UTC = +4 h):

| # | local | valor | regra (domain.md §3.3 + esclarecimentos) | delta |
|---|---|---|---|---|
| 1 | 06:00 | 65 470 | primeira leitura = linha de base (C1) | 0 |
| 2 | 06:10 | 65 485 | regra 1: 65 485 − 65 470 | 15 |
| 3 | 06:20 | 65 499 | regra 1 | 14 |
| 4 | 06:30 | 65 513 | regra 1 | 14 |
| 5 | 06:40 | 65 528 | regra 1 | 15 |
| 6 | 06:50 | 7 | regra 2, **rollover**: 65 528 ≥ 0,9 × 65 535 = 58 981,5 → (65 535 − 65 528) + 7 + 1 (C5) | 15 |
| 7 | 07:00 | 22 | regra 1 | 15 |
| 8 | 07:00 | 22 | regra 5, **duplicata** (mesmo ts e valor, reentrega QoS 1) | 0 |
| 9 | 07:10 | 36 | regra 1 | 14 |
| 10 | 07:20 | 32 804 | regra 4, **glitch**: bit 15 invertido (36 + 32 768); delta 32 768 > 55 → descartado; `prev` continua 36 @ 07:10 (C2) | 0 |
| 11 | 07:30 | 65 | regra 1 contra o último aceito (36 @ 07:10): Δt = 1 200 s, limite 0,05 × 1 200 × 1,5 + 10 = 100; 65 − 36 | 29 |
| 12 | 07:25 | 50 | **fora de ordem** (ts < 07:30 já aceito) → ignorada (C6) | 0 |
| 13 | 07:40 | 79 | regra 1 | 14 |
| 14 | 07:50 | 80 | regra 1 (máquina parando) | 1 |
| 15 | 08:00 | 80 | sem ciclos; ts novo, então **não** é duplicata | 0 |
| 16 | 08:10 | 93 | regra 1 | 13 |
| 17 | 08:20 | 9 | regra 3, **reset** do CLP: 93 < 58 981,5 → delta = 9 | 9 |
| 18 | 08:30 | 24 | regra 1 | 15 |
| 19 | 08:40 | 38 | regra 1 | 14 |

**Ciclos no cenário** = 15+14+14+15+15+15+14+29+14+1+13+9+15+14 = **197**.
Eventos registrados: `counter_rollover` (06:50, delta 15), `counter_glitch` (07:20, delta 0), `counter_reset` (08:20, delta 9).

Buckets por hora (o delta vai inteiro para a hora do `ts` da leitura, C9):
06:00 → 15+14+14+15+15 = **73**; 07:00 → 15+14+29+14+1 = **73**; 08:00 → 0+13+9+15+14 = **51** (soma 197).

Acumuladores (estado semeado antes do cenário: a posição já teve um cilindro anterior que saiu com 3 600 000 ciclos):

| | semente | + cenário | esperado |
|---|---|---|---|
| `cycles_total` da posição | 7 199 803 | + 197 | **7 200 000** |
| `usage_since_install.cycles` do componente | 3 599 803 | + 197 | **3 600 000** |
| `hours_total`, `starts_total` | 0 | sem `run_state` no cenário | **0**, **0** |

Vida e projeção (kpis.md §4.1–4.2), avaliadas em `2026-10-06T03:30Z` = **2026-10-05 23:30 local** → hoje = 2026-10-05 (C17):

- Vida nominal 5 000 000 ciclos → `life_consumed_pct = 3 600 000 / 5 000 000 =` **0,72** (medida `cycles`).
- Uso nos últimos 14 dias: 280 000 ciclos → ritmo 280 000 / 14 = 20 000/dia.
- Restante 5 000 000 − 3 600 000 = 1 400 000 → 1 400 000 / 20 000 = 70 dias (exato, `ceil(70) = 70`, C18)
  → `projected_end_date =` **2026-12-14**.
- Sem semente: `cycles_total = 197`, `life_consumed_pct = 197 / 5 000 000 = 3,94e-5`.

Respostas erradas que o teste pega: rollover sem o `+1` → 196; glitch aceito (32 768) e o 65 tratado como
reset (65) → 33 001; "hoje" em UTC (2026-10-06) → 2026-12-15.

### A2. Motor do transportador: `run_state` com quedas curtas e buraco de dados

Linha de Montagem › Transportador de Saída › posição **Motor do transportador** (`electric_motor`), tag
`run_state` própria da posição. Heartbeat a cada 30 s com o valor corrente (o edge usa 10 s por padrão; o
resultado não muda, ver teste `test_a2_invariant_to_heartbeat_period`). O JSON tem as 216 leituras;
abaixo só as mudanças:

| local (2026-10-05) | valor | efeito |
|---|---|---|
| 06:00:00 | 0 | linha de base (C1/C12): sem partida |
| 06:05:00 | 1 | **partida 1** |
| 06:20:00,000 → 06:20:00,400 | 0 → 1 | queda de 0,4 s < 1 s → sem partida (debounce) |
| 06:40:00,000 → 06:40:00,900 | 0 → 1 | queda de 0,9 s → sem partida |
| 06:50:00 → 06:50:01 | 0 → 1 | queda de **exatamente 1,0 s**: "< 1 s" é falso → **partida 2** (C10) |
| 07:00:00 → 07:10:00 | 0 → 1 | parada de 10 min → **partida 3** |
| 07:30:00 | 1 | último heartbeat antes do **buraco**: nada chega até 07:45:00 |
| 07:45:00 | 1 | dados voltam com 1: **não** é partida (estado no buraco é desconhecido, C11) |
| 07:50:00,000 → 07:50:00,500 | 0 → 1 | queda de 0,5 s → sem partida |
| 08:00:00 | 0 | motor desligado (fim) |

Intervalos com `run_state = 1` (domain.md §3.4; as quedas debounced **não** contam como operação, C10):

| de | até | s |
|---|---|---|
| 06:05:00 | 06:20:00 | 900 |
| 06:20:00,4 | 06:40:00 | 1 199,6 |
| 06:40:00,9 | 06:50:00 | 599,1 |
| 06:50:01 | 07:00:00 | 599 |
| 07:10:00 | 07:30:00 | 1 200 |
| 07:30:00 | 07:31:00 | 60 (buraco: credita só até `last_seen + 60 s`, C11) |
| 07:45:00 | 07:50:00 | 300 |
| 07:50:00,5 | 08:00:00 | 599,5 |
| **total** | | **5 457,2 s = 1,515889 h** |

`starts = 3`; quedas filtradas pelo debounce = 3.
Por hora UTC: 10:00Z (06:00 local) = 900 + 1 199,6 + 599,1 + 599 = **3 297,7 s** e 2 partidas;
11:00Z (07:00 local) = 1 200 + 60 + 300 + 599,5 = **2 159,5 s** e 1 partida.

Acumuladores com semente (posição e componente iguais): horas 11 998,484111 + 1,515889 = **12 000 h**;
partidas 129 997 + 3 = **130 000**.

Vida nominal 30 000 h **e** 200 000 partidas (kpis.md §4.1): 12 000 / 30 000 = 0,40; 130 000 / 200 000 = 0,65
→ `life_consumed_pct =` **0,65**, medida determinante **`starts`**.

Projeção (hoje = 2026-10-05; uso nos últimos 14 dias: 168 h e 21 000 partidas):
- horas: ritmo 168 / 14 = 12 h/dia; restante 18 000 h → 1 500 dias;
- partidas: ritmo 21 000 / 14 = 1 500/dia; restante 70 000 → 46,67 → `ceil` = **47 dias**;
- RUL = medida com a data mais cedo = `starts` → `projected_end_date =` 2026-10-05 + 47 = **2026-11-21**.

A3: o mesmo componente **sem vida nominal** → `life_consumed_pct = null` (`no_nominal_life`), `projected_end_date = null`.

Respostas erradas: sem debounce → 6 partidas; debounce com "≤ 1 s" → 2; operação extrapolada sobre o buraco
→ 6 297,2 s (+840 s); partida contada após o buraco → 4.

---

## Cenário B — Produção (turno que cruza a meia-noite)

Linha de Montagem, máquina de saída **Transportador de Saída** (`ideal_cycle_time_s = 40` → ideal 90 peças/h).
Tags `part_counter_good` e `part_counter_scrap` de 32 bits (`counter_max = 4 294 967 295`), sem
`part_counter_total` → **total = boas + refugo** (C15). Limite de plausibilidade a cada 15 min:
`2/40 × 900 × 1,5 + 10 = 77,5` (o maior delta do cenário é 22).

Turno C: **2026-10-05 18:00 → 2026-10-06 02:00 local** (22:00Z → 06:00Z), dia produtivo **2026-10-05**.
Pausas planejadas: refeição 22:00–22:30 (`PLN-01`), 5S 01:45–02:00 (`PLN-02`). Atraso de partida
18:00–18:05 (`OPR-01`, não planejada). Leituras nos minutos :07, :22, :37 e :52; linha de base às 17:52
(boas 1 204 330, refugo 25 117; linha parada desde 17:30). Às 20:07 a leitura de boas chega duas vezes
(QoS 1) → a segunda é duplicata (delta 0).

Deltas por leitura (boas/refugo) e o bucket que recebe cada um (hora local do `ts` da leitura, C9):

| hora local | :07 | :22 | :37 | :52 | boas | refugo | total = cadência (peças/h) |
|---|---|---|---|---|---|---|---|
| 05/10 18:00 | 3/0 (partiu 18:05) | 21/1 | 22/0 | 21/0 | 67 | 1 | **68** |
| 05/10 19:00 | 22/0 | 21/1 | 22/0 | 22/0 | 87 | 1 | **88** |
| 05/10 20:00 (= 00:00Z: meia-noite UTC) | 21/0 + duplicata 0 | 22/0 | 21/1 | 22/0 | 86 | 1 | **87** |
| 05/10 21:00 | 22/0 | 21/0 | 22/0 | 21/1 | 86 | 1 | **87** |
| 05/10 22:00 | 12/0 (21:52–22:00) | 0/0 (refeição) | 10/0 (desde 22:30) | 22/1 | 44 | 1 | **45** |
| 05/10 23:00 | 21/0 | 22/0 | 22/1 | 21/0 | 86 | 1 | **87** |
| 06/10 00:00 | 22/0 (23:52→00:07 atravessa a meia-noite local) | 21/0 | 22/0 | 22/1 | 87 | 1 | **88** |
| 06/10 01:00 | 21/0 | 22/1 | 21/0 | 12/0 (parou 01:45) | 76 | 1 | **77** |
| 06/10 02:00 | 0/0 | — | — | — | 0 | 0 | — (sem bucket) |

Totais do turno: boas 67+87+86+86+44+86+87+76 = **619**; refugo 8 × 1 = **8**; total **627**.

Agrupamento por dia (C19):

| agrupamento | 2026-10-05 | 2026-10-06 | uso |
|---|---|---|---|
| **dia produtivo** (turno pertence ao dia em que começa) | 619 / 8 / 627 | — | KPIs por turno/dia (Sprint 3, com calendário de turnos) |
| **dia calendário local** (00:00–24:00 em Manaus) | 456 / 6 / 462 (18:00–23:59) | 163 / 2 / 165 (00:00–02:00) | histórico `bucket=1d` do Sprint 2 (sem calendário de turnos) |
| ~~dia UTC~~ (errado) | 154 / 2 / 156 (corta às 20:00 local) | 465 / 6 / 471 | nunca usar |

Cadência do turno (kpis.md §1.9): POT 480 min; PDOT 30 + 15 = 45 → PBT **435 min**; ADOT 5 → APT **430 min**.
- realizada = 627 / 7,25 h = **86,482759 peças/h**;
- em operação = 627 / (430/60) h = **87,488372 peças/h**;
- ideal = 3 600 / 40 = **90 peças/h**; desempenho implícito P = 627 × 40 / (430 × 60) = **0,972093**.

---

## Cenário C — Turno de 8 h com OEE (prévia do Sprint 3)

Torno CNC, produto EIXO-20, **PRI = 45 s** (0,75 min), `micro_stop_threshold_s = 120`. Turno A
2026-10-06 06:00–14:00 local (10:00Z–18:00Z). Mapa de códigos de falha → motivo: 101 → `EQP-01`,
204 → `EQP-02`, 305 → `EQP-04`. Produção: PQ 450, GQ 441, SQ 9. O JSON traz a lista de eventos e as transições de
`run_state`/`fault_state` equivalentes (o teste garante que uma reproduz a outra).

| local | duração | tipo | classificação |
|---|---|---|---|
| 06:40:00–06:40:45 | 45 s | parada `run_state` | micro-parada |
| 07:12:00–07:30:00 | 18 min | falha 101 | `EQP-01`, falha `critical` |
| 07:50:00–07:51:00 | 60 s | falha 305 | < 120 s → **micro-parada**, sem `FailureRecord` (C22) |
| 08:30:00–08:36:00 | 6 min | parada | `MAT-01` |
| 09:00:00–09:10:00 | 10 min | café | `PLN-01` (PDOT) |
| 09:45:00–09:52:30 | 7,5 min | falha 204 | `EQP-02`, falha `degraded` |
| 10:15:00–10:16:30 | 90 s | parada | micro-parada |
| 10:50:00–10:51:59 | 119 s | parada | micro-parada (ainda < 120) |
| 11:00:00–11:30:00 | 30 min | almoço | `PLN-01` (PDOT) |
| 11:50:00–11:52:00 | **120 s** | parada sem motivo | = limiar → `DowntimeEvent`; sem motivo ao fim do turno → `UNK-01` |
| 12:05:00–12:06:16 | 76 s | parada | micro-parada |
| 12:20:00–12:44:00 | 24 min | falha 101 | `EQP-01`, falha `critical` |
| 13:00:00–13:15:00 | 15 min | setup | `SET-01` (não planejada, ISO 22400) |
| 13:30:00–13:40:00 | 10 min | café | `PLN-01` (PDOT) |
| 13:50:00–13:51:30 | 90 s | parada | micro-parada |

Tempos (kpis.md §1.1):
- POT = 480 min; PDOT = 10 + 30 + 10 = 50 → **PBT = 430 min** (dados completos, `data_coverage = 1`).
- ADOT = 18 + 7,5 + 24 (EQP) + 15 (SET) + 6 (MAT) + 2 (UNK) = **72,5 min** → **APT = 357,5 min**.
- Micro-paradas: 45 + 60 + 90 + 119 + 76 + 90 = 480 s = **8 min**, **6** ocorrências → 6 / 5,958333 h = 1,006993 por hora de APT.
- Tempo ideal produzido Σ PRI·PQ = 450 × 0,75 = **337,5 min**; líquido Σ PRI·GQ = 441 × 0,75 = **330,75 min**.

| KPI | conta | esperado |
|---|---|---|
| A | 357,5 / 430 | **0,831395** |
| P | 337,5 / 357,5 | **0,944056** |
| Q | 441 / 450 | **0,980000** |
| OEE | 0,831395 × 0,944056 × 0,98 = 330,75 / 430 | **0,769186** |
| Cadência realizada / em operação / ideal | 450 / 7,1667 h; 450 / 5,9583 h; 3 600 / 45 | 62,790698; 75,524476; 80 peças/h |
| Tempo com `run_state = 1` | 430 − 72,5 − 8 = 349,5 min (C23) | 5,825 h |
| n (falhas `critical`/`degraded`) | F 07:12, 09:45, 12:20 (a de 60 s não é falha) | **3** |
| MTBF | 5,825 h / 3 | **1,941667 h** (116,5 min) |
| MTTR | (18 + 7,5 + 24) / 3 | **16,5 min** (990 s) |
| Ao (kpis.md §2.6) | T_req = 480 − 50 = 430; D_eq = 49,5 (só EQP) → 380,5 / 430 | **0,884884** |

Pareto de perdas (kpis.md §1.10), total = PBT − Σ PRI·GQ = 430 − 330,75 = **99,25 min**:
perda de velocidade = APT − Σ PRI·PQ − micro = 357,5 − 337,5 − 8 = **12**; qualidade = 0,75 × 9 = **6,75**.

| # | perda | min | % | acumulado |
|---|---|---|---|---|
| 1 | `EQP-01` falha mecânica (18 + 24) | 42 | 42,32 % | 42,32 % |
| 2 | `SET-01` setup | 15 | 15,11 % | 57,43 % |
| 3 | perda de velocidade | 12 | 12,09 % | 69,52 % |
| 4 | micro-paradas | 8 | 8,06 % | 77,58 % |
| 5 | `EQP-02` falha elétrica | 7,5 | 7,56 % | 85,14 % |
| 6 | qualidade (refugo) | 6,75 | 6,80 % | 91,94 % |
| 7 | `MAT-01` falta de material | 6 | 6,05 % | 97,98 % |
| 8 | `UNK-01` não justificada | 2 | 2,02 % | 100 % |

Por categoria: EQP 49,5 · SET 15 · velocidade 12 · micro 8 · qualidade 6,75 · MAT 6 · UNK 2.

Respostas erradas: MTBF com APT (inclui micro-paradas) → 1,986111 h; falha de 60 s contada → n = 4,
MTBF 1,45625 h; 120 s tratado como micro-parada → ADOT 70,5 e 7 micro-paradas; pausas dentro do PBT →
A = 357,5 / 480 = 0,744792.

---

## Cenário D — Linha com gargalo, vários turnos e dias (Sprint 3)

Prova a agregação **turno → dia produtivo → período** de uma **linha** e tudo o que o calendário pode fazer com o
tempo: turno que cruza a meia-noite, feriado, turno de um dia só cancelado, turno extra, parada planejada declarada
no turno, buraco de dados. Regras usadas: kpis.md §1, C19, C22–C24, Decisões D2/D4/D5/D7/D9/D10 e os
**Esclarecimentos Sprint 3** (S3-1 … S3-9).

### Ativos, produtos e PRI

Linha de Montagem: saída = **Transportador de Saída** (dele vêm paradas, APT e peças), gargalo = **Robô de Solda**
(dele vem o PRI da linha, kpis.md §1.6).

| Produto | PRI do gargalo (linha) | PRI do transportador (só `scope=machine`) |
|---|---|---|
| SUP-100 (padrão) | **48 s** = 0,8 min | 30 s |
| SUP-200 | **60 s** = 1,0 min | 30 s |

### Calendário e consulta

Turnos: A 06–14 (seg–sex, refeição 10:00–10:30), B 14–22 (seg–sex, refeição 18:00–18:30), C 22–06
(dia produtivo dom–qui, refeição 02:00–02:30). Exceções: **sáb 10/10** turno extra 06–14 com as pausas do A;
**seg 12/10** feriado (remove A, B e C do dia 12); **ter 13/10** `holiday` só do turno C (o PCP cancelou o C de terça).

Consulta: `scope=line`, `start = 2026-10-10 00:00`, `stop = 2026-10-14 00:00` local (04:00Z → 04:00Z),
`group_by=shift` e `group_by=day`. Começar no sábado evita o rabo de um turno C da véspera (sexta não tem C).

| Instância | Dia produtivo | Local | Por quê |
|---|---|---|---|
| S1 Turno A (extra) | sáb 10/10 | 10/10 06:00–14:00 | `extra_shift` com as pausas do A |
| S2 Turno C | dom 11/10 | 11/10 22:00 → **12/10 06:00** | cruza a meia-noite e termina na manhã do feriado; pertence a domingo (C19) e o feriado de segunda **não** o remove (S3-6) |
| — | seg 12/10 | — | feriado: POT = 0 |
| S3 Turno A | ter 13/10 | 13/10 06:00–14:00 | — |
| S4 Turno B | ter 13/10 | 13/10 14:00–22:00 | — (o C de terça foi cancelado) |

### Paradas da máquina de saída (≥ 120 s) e o que mais aconteceu

| Turno | Local | min | Motivo efetivo | Observação |
|---|---|---|---|---|
| S1 | 07:15–07:35 | 20 | `MAT-01` | o robô (gargalo) parou por colisão 07:13–07:33: na linha só conta a falta de peça na saída — somar a falha do robô também daria ADOT 163 |
| S1 | 11:40–11:50 | 10 | `OPR-01` | equipe reduzida no turno extra |
| S2 | dom 23:45 → seg 00:20 | 35 | `EQP-01` (falha 151) | atravessa a meia-noite **dentro** do turno: um evento, todo no S2 |
| S2 | 03:10–03:22 | 12 | `MAT-01` | — |
| S3 | 08:00–08:15 | 15 | `EQP-02` (falha 251) | — |
| S3 | 11:00–11:30 | 30 | `SET-01` | setup SUP-100 → SUP-200; a ordem do SUP-200 abre às 11:00, no início do setup (S3-3) |
| S4 | 14:00–14:15 | 15 | **`PLN-02`** | DDS fora do calendário: detectado como parada e reclassificado pelo supervisor → **PDOT** (S3-4) |
| S4 | 16:10–16:16 | 6 | `UNK-01` | ninguém justificou (D2) |
| S4 | 19:00–19:40 | 40 | — | **sem dados**: última leitura 18:59:00 + 60 s (C11); os contadores não avançaram; ninguém declarou → sai do PBT (D5) |
| S4 | 20:30–20:45 | 15 | `MAT-01` | — |
| seg 12/10 | 09:00–10:00 | — | — | feriado: a manutenção rodou a linha para testar a correia → **12 peças fora do calendário** (S3-7) |

Micro-paradas (< 120 s): S1 4 (80 + 100 + 90 + 90 s = 6 min), S2 3 (4,5 min), S3 5 (7,5 min), S4 2 (3 min).
Produção (GQ/PQ): S1 SUP-100 470/480; S2 SUP-100 441/450; S3 SUP-100 294/300 (06–11) + SUP-200 126/130 (11–14);
S4 SUP-200 352/360. O JSON traz as peças por hora (o QA publica os contadores hora a hora), as ordens e as
transições de `run_state`/`fault_state` da saída.

### Por turno (S = Σ PRI·PQ, G = Σ PRI·GQ, em min)

| | POT | PDOT | sem dados | **PBT** | ADOT | **APT** | S | G | A | P | Q | **OEE** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | 480 | 30 | 0 | 450 | 30 | 420 | 480 × 0,8 = 384 | 376 | 0,933333 | 0,914286 | 0,979167 | **0,835556** |
| S2 | 480 | 30 | 0 | 450 | 47 | 403 | 360 | 352,8 | 0,895556 | 0,893300 | 0,980000 | **0,784000** |
| S3 | 480 | 30 | 0 | 450 | 45 | 405 | 300 × 0,8 + 130 × 1 = 370 | 235,2 + 126 = 361,2 | 0,900000 | 0,913580 | 0,976216 | **0,802667** |
| S4 | 480 | 30 + 15 | 40 | **395** | 21 | 374 | 360 | 352 | 0,946835 | 0,962567 | 0,977778 | **0,891139** |

- S3 com mix: Q = 361,2 / 370 = 0,976216 (ponderado pelo PRI, §1.4); GQ/PQ = 420/430 = 0,976744 seria o erro.
- S4: `data_coverage` = (480 − 40) / 480 = **0,916667** < 0,95 → a UI mostra "dados incompletos".
  Se o supervisor declarar `UTL-01` 19:00–19:40 (variante no JSON): o buraco vira ADOT e coberto (D5) →
  PBT 435, ADOT 61, A = 0,859770, OEE = 352 / 435 = 0,809195, cobertura 1.
- Ao (§2.6): S1 1; S2 (450 − 35) / 450 = 0,922222; S3 0,966667; S4: T_req = 480 − 30 − 15 (`PLN-02`) − 40 = 395, Ao = 1.
- Perda de velocidade = APT − S − micro: S1 30; S2 38,5; S3 27,5; S4 11.

### Por dia produtivo (tempo calendário 1 440 min/dia)

| Dia | PBT | APT | G | A | P | Q | OEE | Loading | TEEP |
|---|---|---|---|---|---|---|---|---|---|
| sáb 10/10 | 450 | 420 | 376 | 0,933333 | 0,914286 | 0,979167 | 0,835556 | 0,312500 | 0,261111 |
| dom 11/10 | 450 | 403 | 352,8 | 0,895556 | 0,893300 | 0,980000 | 0,784000 | 0,312500 | 0,245000 |
| seg 12/10 (feriado) | 0 | 0 | 0 | `null` | `null` | `null` | **`null`** (`no_planned_time`) | **0** | **0** (S3-1) |
| ter 13/10 | 845 | 779 | 713,2 | 0,921893 | 0,937099 | 0,976986 | 0,844024 | 0,586806 | 0,495278 |

Produção por dia produtivo: 480 · **450** (o C de domingo inteiro, inclusive 00:00–06:00 de segunda) · 0 · 790.
Por dia calendário local (errado com calendário de turnos) daria 480 · 117 · 345 (inclui as 12 do teste) · 790.

### Período (4 dias produtivos, 5 760 min de calendário)

POT 1 920; PDOT 135; sem dados 40 → **PBT 1 745**; ADOT 143 → **APT 1 602**; PQ 1 720, GQ 1 683;
S = 1 474; G = 1 442; micro-paradas 14 = 21 min.

| KPI | conta | esperado |
|---|---|---|
| A | 1 602 / 1 745 | **0,918052** |
| P | 1 474 / 1 602 | **0,920100** |
| Q | 1 442 / 1 474 | **0,978290** |
| OEE | 1 442 / 1 745 | **0,826361** |
| Loading | 1 745 / 5 760 | 0,302951 |
| TEEP | 1 442 / 5 760 | **0,250347** |
| `data_coverage` | 1 880 / 1 920 | 0,979167 |
| Ao | (1 745 − 50) / 1 745 | 0,971347 |

O total é a **soma dos componentes** dos dias (S3-8); as 12 peças do feriado ficam fora (S3-7) e são devolvidas
em `produced_outside_schedule`.

Pareto (total = PBT − G = 1 745 − 1 442 = **303 min**; velocidade = 1 602 − 1 474 − 21 = 107; qualidade = 1 474 − 1 442 = 32):

| # | perda | min | % | acumulado |
|---|---|---|---|---|
| 1 | perda de velocidade | 107 | 35,31 % | 35,31 % |
| 2 | `MAT-01` falta de material | 47 | 15,51 % | 50,83 % |
| 3 | `EQP-01` falha mecânica | 35 | 11,55 % | 62,38 % |
| 4 | qualidade (refugo) | 32 | 10,56 % | 72,94 % |
| 5 | `SET-01` setup | 30 | 9,90 % | 82,84 % |
| 6 | micro-paradas | 21 | 6,93 % | 89,77 % |
| 7 | `EQP-02` falha elétrica | 15 | 4,95 % | 94,72 % |
| 8 | `OPR-01` falta de operador | 10 | 3,30 % | 98,02 % |
| 9 | `UNK-01` não justificada | 6 | 1,98 % | 100 % |

Por categoria: velocidade 107 · EQP 50 · MAT 47 · qualidade 32 · SET 30 · micro 21 · OPR 10 · UNK 6.
O `PLN-02` e o buraco de dados não são perdas (estão fora do PBT).

Respostas erradas: média dos OEEs dos turnos 0,828340; média dos dias 0,821193; PRI da máquina de saída
(30 s) → P 0,536829 e OEE 0,482235 (é o valor certo para `scope=machine` do transportador); somar a falha do robô
→ ADOT 163; buraco tratado como operação → S4 PBT 435 e A 0,951724; peças do feriado no OEE → 0,831862;
TEEP do feriado `null`.

Conformidade com o motor (`test_scenario_d.py`): janelas de turno e de dia, soma do período, Pareto por código e
por categoria e `scope=machine` conferem com o servidor. Duas divergências ficam `xfail` até o backend corrigir:
TEEP do feriado (o motor devolve `null`, S3-1) e o total sem `group_by`, que soma as peças fora do calendário (S3-7/S3-8).

---

## Cenário E — Manutenção (Sprint 4)

Avaliado em **2026-12-01 08:00 local** (hoje = 01/12). Mês de referência: **novembro/2026**. Confiabilidade em
janela de **12 meses** (01/12/2025 → 01/12/2026). Regras: domain.md §5, kpis.md §2–§4 e os **Esclarecimentos de
manutenção (Sprint 4)** S4-1 … S4-12 em domain.md. Ritmo = uso dos últimos 14 dias / 14.
Como planejador, técnico e supervisor chegam a estes números no dia a dia:
[`docs/maintenance_workflows.md`](../maintenance_workflows.md).

### Planos (o que vier primeiro)

| Plano | Âncora | Intervalos | Desde a última manutenção | % por gatilho | `consumed` / estado | Vence em (gatilho) | Próximo uso |
|---|---|---|---|---|---|---|---|
| PL1 vedações A1 | Cilindro A1 | 2 000 000 ciclos / 180 d | 340 000 ciclos, 19 d (zerado pela troca do cilindro em 12/11, S4-2) | 0,17 / 0,105556 | 0,17 `ok` | 1 660 000 / 18 000 = 92,2 → 93 d → **2027-03-04** (ciclos) | 9 200 000 ciclos |
| PL2 vedações A2 | Cilindro A2 | 2 000 000 ciclos / 180 d | 1 780 000, 169 d | 0,89 / 0,938889 | 0,938889 `due_soon` | calendário 15/06 + 180 = **2026-12-12** (11 d) antes dos ciclos (13 d) | 9 320 000 ciclos |
| PL3 rolamentos | Motor do transportador | 4 000 h / 30 000 partidas / 365 d | 3 200 h, 24 600, 225 d | 0,80 / **0,82** / 0,616438 | 0,82 `due_soon` (gatilho de maior %: partidas) | horas 800 / 20 = **40 d → 2027-01-10**; partidas 45 d → 2027-01-15 | 45 800 h ou 320 000 partidas |
| PL4 óleo do redutor | Eixo 1 | 5 000 h / 365 d | 5 150 h, 346 d | **1,03** / 0,947945 | 1,03 `due` (< 1,10) | vencido: 0 d, **2026-12-01** (`past_due`) | 38 000 h |
| PL5 NR-12 | Estação de Fixação | 30 d | 28 d | 0,933333 | `due_soon` | 03/11 + 30 = **2026-12-03** | — |
| PL6 filtro de ar | Estação de Fixação | 7 d | 26 d | 3,714286 | `overdue` (≥ 1,10) | vencido: **2026-12-01** | — |

PL3 é a pegadinha: `consumed` usa o gatilho de **maior %** (partidas, 0,82), mas a data é a do gatilho que
**vence primeiro** (horas, 40 dias) — usar a data das partidas daria 2027-01-15 (S4-3).

### Componentes instalados: % de vida e fim projetado (kpis.md §4.1–4.2)

| Componente | Vida nominal | Uso desde a instalação | % vida (medida) | Fim projetado |
|---|---|---|---|---|
| C-A1-3 SMC CP96 (A1, instalado 12/11) | 6 000 000 ciclos | 340 000 | 0,056667 (ciclos) | 315 d → 2027-10-12 |
| C-A2-2 SMC CP96 (A2) | 6 000 000 ciclos | 3 200 000 | 0,533333 (ciclos) | 156 d → 2027-05-06 |
| M-1 WEG W22 (motor) | 30 000 h / 200 000 partidas | 21 000 h / 150 000 | **0,75 (partidas)** | partidas 417 d → 2028-01-22 |
| R-1 ABB redutor eixo 1 | 20 000 h | 17 200 h | **0,86** (`attention`) | 175 d → 2027-05-25 |

### Ordens de serviço de novembro

| OS | Tipo | Programada p/ | Programada em | Situação em 01/12 | Horas (apont./est.) | PMP | Cumprimento |
|---|---|---|---|---|---|---|---|
| 101 | preventiva (PL5) | 03/11 | 30/10 | fechada 03/11 | 2,0 | planejada | no prazo |
| 103 | corretiva emergencial (falha F4, troca C-A1-2 → C-A1-3) | — | — | fechada 12/11 | 3,0 | **não planejada** | — |
| 104 | preditiva | 10/11 | 06/11 | concluída 10/11 | 1,5 | planejada | no prazo |
| 106 | corretiva programada | 18/11 | 13/11 | concluída **19/11** | 2,5 | planejada | **atrasada** |
| 107 | melhoria | 24/11 | 20/11 | concluída 24/11 | 4,0 | planejada | no prazo |
| 108 | corretiva emergencial (F7) | — | — | concluída 20/11 | 1,0 | não planejada | — |
| 109 | corretiva emergencial (F9) | — | — | concluída 26/11 | 2,0 | não planejada | — |
| 110 | preventiva (PL4) | 28/11 | 20/11 | `scheduled` — **vencida** | 0 / 6,0 | — | não feita |
| 113 | melhoria | 16/11 | 10/11 | `on_hold` (peça) — vencida | 2,0 / 8,0 | — (não concluída) | não feita |
| 115 | preventiva | 05/11 | 30/10 | cancelada `superseded` | — | — | **fora do denominador** |
| 116 | preventiva (PL6) | 12/11 | 06/11 | cancelada (`production_conflict`) | — | — | não feita |
| 117 | preditiva | 30/11 | 26/11 | `in_progress` — vencida | 0,5 / 2,0 | — | não feita |
| 118 | preventiva | 17/11 | 12/11 | concluída 17/11 | 1,0 | planejada | no prazo |
| 120 | preditiva | 20/11 | 13/11 | concluída 20/11 | 0,5 | planejada | no prazo |
| 121 | corretiva programada | 26/11 | **25/11** (depois do congelamento de seg 23/11) | concluída 26/11 | 1,5 | planejada | **fora do denominador** |

Fora de novembro ou ainda não aprovadas: OS-099 (concluída em 30/10, fora do PMP), OS-112 (`approved`, 3,0 h),
OS-114 (`scheduled` p/ 03/12, 1,5 h), rascunhos OS-111 (PL3), OS-122 (PL6, recriada após o cancelamento da 116,
S4-4) e OS-123 (PL5).

- **PMP** (OS concluídas em novembro, por `labor_hours`): planejadas 2 + 1,5 + 2,5 + 4 + 1 + 0,5 + 1,5 = 13; total
  13 + 3 + 1 + 2 = 19 → **13 / 19 = 0,684211**. (Somar as horas das OS abertas 113 e 117 daria 0,720930.)
- **Cumprimento da programação**: denominador = 10 (101, 104, 106, 107, 110, 113, 116, 117, 118, 120); no prazo 5
  → **0,500000**. Atrasada 1 (106), não feitas 4. Contar a 121 daria 0,545455; contar a 115 daria 0,454545.
- **Backlog** (approved/scheduled/on_hold/in_progress, horas **restantes**, S4-7): 110 6,0 + 112 3,0 + 113 (8 − 2) 6,0
  + 114 1,5 + 117 (2 − 0,5) 1,5 = **18,0 h** em 5 OS; capacidade 1 técnico × 44 h − 4 h de ausência = 40 h/semana
  → **0,45 semana**. Vencidas: 110, 113, 117 = 3 OS, 13,5 h. Rascunhos não são backlog (com eles: 21,5 h).

### Confiabilidade (12 meses; n = falhas `critical`/`degraded`)

| Grupo | Horas | Ciclos | Falhas | MTBF (h) | MCBF (ciclos) | MTTR (min) |
|---|---|---|---|---|---|---|
| Cilindro A1 | 5 200 | 6 000 000 | 3 (F1, F2, F4; F3 é incipiente, F0 é anterior à janela) | **1 733,333** | **2 000 000** | (90 + 40 + 120) / 3 = **83,333** |
| Cilindro A2 | 5 200 | 6 000 000 | 1 | 5 200 | 6 000 000 | 30 |
| Motor do transportador | 6 800 | — | 2 | 3 400 | — | (150 + 45) / 2 = 97,5 |
| Eixo 1 | 5 600 | 9 000 000 | 2 (F8 achada em inspeção, sem parada) | 2 800 | 4 500 000 | **75** (só F9 tem restabelecimento, S4-10) |
| tipo `pneumatic_cylinder` | 10 400 | 12 000 000 | 4 | 2 600 | 3 000 000 | 70 |
| cilindros **Festo** | 1 400 + 3 500 + 2 400 = 7 300 | 8 460 000 | 3 | 2 433,333 | **2 820 000** | 83,333 |
| cilindros **SMC** | 300 + 2 800 = 3 100 | 3 540 000 | 1 | 3 100 | 3 540 000 | 30 |

Por fabricante, a falha vai para o **componente instalado** no instante da falha e o uso é o de cada componente
dentro da janela (S4-9). **MTTF/MCTF** por fabricante/modelo (todo o histórico, idade na remoção):
Festo DSBC-50-100 = (2 600 000 + 4 060 000 + 3 140 000) / 3 = **3 266 666,7 ciclos**, 3 falhas e 2 suspensões
(5 900 000 e 4 000 000 — trocas preventivas, não entram na média; incluí-las daria 3 940 000);
SMC CP96 `null` (`no_failures`, 2 em serviço); WEG W22 = **23 800 h** (1 falha); ABB `null`.

O oráculo também valida cada histórico de OS contra o diagrama de estados (domain.md §5.3) e rejeita as transições
de `rejected_transitions` (422).

---

## Como o QA roda de ponta a ponta

1. **Unidade/serviço** (já automatizado): `pytest docs/scenarios` — oráculo + `core.py` do servidor.
2. **Ponta a ponta (MQTT → processador → API)**, Cenários A e B:
   - Criar planta (fuso `America/Manaus`), linha, máquinas, posições, componentes e tags com os campos do JSON
     (`counter_max`, `ideal_cycle_time_s`, `cycles_per_part`, `counter_tag_id`, vida nominal).
   - **Deslocar o tempo**: o servidor troca por `received_at` todo `ts` no futuro ou mais velho que
     `source_ts_max_age_s` (30 dias). Subtraia **um número inteiro de dias** N de todo `ts`, de `evaluate_at`,
     `installed_at` e das datas esperadas (`today_plant`, `projected_end_date`, chaves dos buckets). Como Manaus não
     tem horário de verão, horas locais e todos os números ficam iguais.
   - Semear `usage_totals` com o bloco `seed` (ou conferir só os deltas: `cycles_delta`, `hours_delta`, `starts_delta`).
   - Publicar as leituras **na ordem do JSON** (a ordem importa: a 12ª leitura do A1 é propositalmente atrasada) em
     `factory/{tenant_id}/sensors/{tag_id}`, cada uma com o `ts` deslocado.
   - Conferir: `GET /api/positions/{id}` (cycles/hours/starts total), `GET /api/components/{id}`
     (`life_consumed_pct`, medida), `GET /api/usage/history?bucket=1h` e `bucket=1d` (buckets da tabela),
     eventos `counter_rollover`/`counter_glitch`/`counter_reset`, e `POST /api/usage/recompute` → mesmos números
     (domain.md §3.5).
   - `projected_end_date` pela API usa o relógio real e a janela real de 14 dias: conferir no nível de serviço
     (`component_usage_detail(now=...)`) ou semear buckets dos últimos 14 dias e recalcular pela mesma fórmula.
3. **Planta virtual**: não é determinística em relação a estes números (é estocástica com seed); serve para
   o teste de fumaça, não para o ouro.
4. **Cenário C**: entra como teste de aceite do motor de OEE/paradas do Sprint 3 (alimentar `signals` + motivos do
   operador + contagens; comparar com `expected`).
5. **Cenário D** (ponta a ponta, mesmo caminho do C): criar o calendário (`shifts` + `exceptions`), os produtos e o
   PRI por (máquina, produto) do JSON, a linha com `output_machine` e `bottleneck_machine`; publicar as
   `signals_output_machine.transitions` com heartbeat a cada 10 s **exceto** entre `last_reading_utc` e o fim de
   cada `data_gaps`, e os contadores de peças com os totais acumulados de `production_by_hour` (uma leitura no fim de
   cada hora); abrir as `production_runs`; rodar o detector; justificar as paradas com `operator_reason_code`
   (o 16:10 fica sem motivo → `UNK-01`; o 14:00 é reclassificado `PLN-02`). Conferir `GET /api/oee?scope=line`
   com `group_by=shift` e `group_by=day` (pontos = `expected.shifts`/`expected.days`, total = `expected.period`),
   `GET /api/losses/pareto?scope=line` e `by=category`, e `GET /api/reports/shift` de cada turno. Deslocar o tempo
   como nos cenários A/B (dias inteiros, inclusive as datas das `exceptions`; o dia da semana importa para o calendário, então deslocar
   em múltiplos de 7 dias).
6. **Cenário E**: teste de aceite do módulo de manutenção do Sprint 4 — semear posições, componentes (com o uso
   desde a instalação), falhas, planos (com `last_reset`) e OS (reproduzindo `history` pela API de transições);
   conferir `expected` pelos endpoints de planos, componentes, OS, PMP/cumprimento/backlog e confiabilidade.

---

## Cenário F — Preditiva (Sprint 5)

Arquivos: [`scenario_f_predictive.json`](scenario_f_predictive.json) (entradas + `expected`) e o oráculo
[`test_scenario_f.py`](test_scenario_f.py); a consistência do catálogo do assistente de IA fica em
[`test_ai_assistant_spec.py`](test_ai_assistant_spec.py). Regras: [`docs/predictive.md`](../predictive.md) P-1…P-12.
Parâmetros dos detectores = o bloco `predictive-defaults` de predictive.md (o teste confere), exceto janela e
aquecimento, maiores aqui para caber no JSON (`detector_overrides_vs_defaults`).

| Parâmetro | Valor | Conta |
|---|---|---|
| EWMA | λ = 0,2, L = 3 | limite = 3 · √(0,2 / 1,8) = 3 · 0,3333 = **1,0** |
| CUSUM | k = 0,5, h = 5 | `S = max(0, S + z − 0,5)`, dispara em `S > 5` |
| z de entrada | recortado em ±4 | um pico vale no máximo 0,2 · 4 = 0,8 no EWMA e 4 − 0,5 = 3,5 no CUSUM — sozinho não dispara |
| z robusto móvel | 20 janelas anteriores, limite 3,5; alerta com 3 em 10 | — |
| Tendência / RUL | K = 10 janelas, `b/SE > 2`, faixa `b ± 2·SE` | RUL `warning` se ≤ 30 dias, `critical` se `dias_low` ≤ 7 |

Ruído determinístico (período 10, índice `i mod 10`): `[0, +1, −1, +0,5, −0,5, +1,5, −1,5, 0, +2, −2]` (F1, ms) e o
mesmo × 0,1 (F2, A). Em 20 janelas consecutivas cada valor aparece 2 vezes → **mediana 0 e MAD 1,0** (F1) / 0,1 (F2).

### F1 — Cilindro B3: tempo de curso sobe até a falha

Cilindro novo em **04/01/2027 06:00** local (10:00Z), 20 000 ciclos/dia contínuos; janela = 10 000 ciclos
(**12 h**); `x_i = 200 + ruído_i + 1,0 · max(0, i − 30)` ms; bloco 25 = **212,0** (queda momentânea de ar).
Falha (planta virtual: tempo de curso nominal = 240 ms = 1,2 · m0) no fim do bloco 70 = **08/02/2027 10:00Z**.

1. **Linha de base**: bloco 1 = aquecimento; blocos 2–21 → `m0 = 200,0`, `MAD = 1,0`,
   `1,4826 · 1,0 = 1,48 < piso 1 % · 200 = 2,0` → **σ = 2,0 ms**; estabelecida no fim do bloco 21 = 14/01 22:00Z.
   `L_f = 1,2 · 200 = 240 ms`.
2. **Pico (bloco 25)**: z bruto (212 − 200)/2 = 6 → recortado **4**; EWMA = 0,2 · 4 + 0,8 · (−0,0742) = **0,741**
   < 1; CUSUM = 0 + 4 − 0,5 = **3,5** < 5; z móvel (blocos 5–24: mediana 200, MAD 1 → σ 2) = **6,0** > 3,5 →
   anomalia pontual, 1 de 10 → **sem alerta**.
3. **Bloco 35** (x = 200 + 1,5 + 5 = 206,5): z = 3,25; EWMA = 0,2 · 3,25 + 0,8 · 0,8576 = **1,336 > 1**;
   CUSUM = 3,25 + 3,25 − 0,5 = **6,0 > 5**; tendência dos blocos 26–35 significativa → RUL 26 dias (17–50) ≤ 30.
   **Alerta abre já como `warning`** em 21/01 22:00Z (EWMA + CUSUM + RUL).
4. Depois: taxa (`R-F1-ROC`, > 0,5 ms/10 000 ciclos por 2 blocos) no bloco 36; `critical` (dias_low ≤ 7) no bloco
   48; `% da base` (> 220 ms por 2 blocos) no 52; limite absoluto (> 230 ms por 2 blocos) só no **62**.

| Quem avisou | Bloco | Antecedência até a falha |
|---|---|---|
| EWMA / CUSUM / RUL ≤ 30 d (`warning`) | 35 | (70 − 35) × 12 h = **420 h (17,5 dias)** |
| taxa de variação | 36 | 408 h |
| RUL `critical` | 48 | **264 h (11 dias)** |
| 110 % da base | 52 | 216 h |
| limite fixo 230 ms | 62 | **96 h (4 dias)** |

**RUL nos pontos de controle** (P-6; ritmo 20 000 ciclos/dia; janela de tendência = 10 últimos blocos):

| Bloco | hoje | b (ms/10 000 ciclos) | x̂ (ms) | restante (ciclos) | dias (faixa) | data (faixa) | real |
|---|---|---|---|---|---|---|---|
| 40 | 24/01 | 0,9394 ± 0,1492 | 209,73 | 322 258 (244 560 – 472 315) | 17 (12–24) | 10/02 (05/02 – 17/02) | 300 000 · 15 d · 08/02 |
| 50 | 29/01 | 0,9394 ± 0,1492 | 219,73 | 215 806 (163 774 – 316 295) | 11 (8–16) | 09/02 (06/02 – 14/02) | 200 000 · 10 d · 08/02 |
| 60 | 03/02 | 0,9394 ± 0,1492 | 229,73 | 109 355 (82 989 – 160 275) | 6 (4–9) | 09/02 (07/02 – 12/02) | 100 000 · 5 d · 08/02 |
| 66 | 06/02 | 0,9394 ± 0,1492 | 235,73 | 45 484 (34 517 – 66 663) | 3 (1–4) | 09/02 (07/02 – 10/02) | 40 000 · 2 d · 08/02 |

Conta do bloco 50: `restante = (240 − 219,727) / 9,394·10⁻⁵ = 215 806`; `dias = ceil(215 806 / 20 000) =
ceil(10,79) = 11`; `dias_low = floor(163 774 / 20 000) = 8`; `dias_high = ceil(316 295 / 20 000) = 16`. A data
real (08/02) está dentro da faixa nos 4 pontos. Com **plano de produção** (P-7; 30 000/dia útil, 0 no fim de
semana, a partir de sáb 30/01) o acumulado passa 215 806 na qua **10/02** (`production_plan_example`).

### F2 — Motor B: corrente sobe sem falha (falso alarme)

Dia 1 = 04/01/2027 (troca de rolamentos; aquecimento). Janela = 1 dia local, média com a máquina rodando 20 h/dia;
`x_d = 12,0 + ruído_d + 0,02 · min(max(0, d − 30), 25)` A (sobe 0,5 A entre os dias 31 e 55 e estabiliza);
**dia 47 = feriado** (0 h rodando → janela inválida: não gera ponto, não avança EWMA/CUSUM). Nenhuma falha.

1. Base dias 2–21: `m0 = 12,0`, `MAD = 0,1`, `σ = max(0,14826, piso 0,12) = **0,14826 A**`; estabelecida em
   25/01 04:00Z.
2. **Dia 41**: EWMA > 1 → alerta **`watch`** em 14/02 04:00Z (CUSUM confirma no dia 42). Não escala: `% da base`
   (> 13,8 A) e limite (> 15 A) nunca disparam e a RUL de condição, quando existe, é longa — dia 38: 75 dias
   (43–268); dia 58: **80 dias (39–528)** com ritmo **18,57 h/dia** (14 dias com o feriado; faixa 17,14–20,0);
   dia 50: `no_significant_trend`.
3. Fim do dia 62: planejador fecha com **`rebaseline`** (carga maior do novo produto). Nova base sem aquecimento:
   dias 63–82 → `m0 = 12,5`, σ = 0,14826; dias 83–90 sem disparo.

### Avaliação (P-10) e PMP (P-9)

- VP = 1 (F1: falha 17,5 dias depois de abrir, dentro de H = 30 dias), FA = 1 (F2). Precisão **0,5** em `≥ watch`
  e **1,0** em `≥ warning`. Falhas detectadas 1/1.
- Tempo monitorado: F1 14/01 22:00Z → 08/02 10:00Z = **24,5 dias**; F2 25/01 → 04/04 = 69 dias − 20 de
  reaprendizado = **49 dias**; FA por posição-ano = 1 / 73,5 × 365 = **4,966** (`watch`) e **0** (`warning`).
- PMP com OS preditivas: (60 + 6 + 4 + 2 + 3) / (75 + 5) = **0,9375**; horas de preditiva sem defeito = 2,0.

### Como o QA roda o F de ponta a ponta

7. **Cenário F** (Sprint 5): (a) **motor de detecção** — alimentar as séries `windows` de F1 e F2 como
   características já agregadas (`condition_features`) e conferir linha de base, alerta (abertura, escalonamentos,
   evidências), RUL nos pontos de controle e `GET /api/predictive/evaluation`; `POST /api/predictive/recompute`
   DEVE reproduzir os mesmos alertas; (b) **agregação** — publicar tempos de curso por ciclo cuja mediana por bloco
   dê a série de F1 (ex.: 10 000 leituras = valor do bloco ± ruído simétrico) e conferir a característica;
   (c) **planta virtual** — perfil lento de degradação até a falha (predictive.md P-11) para medir antecedência e
   falsos alarmes reais (não é ouro: é estocástica com seed).
