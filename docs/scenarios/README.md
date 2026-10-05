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
| [`test_golden_scenarios.py`](test_golden_scenarios.py) | — | — | oráculo independente que recalcula todo `expected` a partir das entradas + conformidade com `server/src/services/usage/core.py` |

```bash
.venv/bin/python -m pytest docs/scenarios -q      # 21 testes; o oráculo não depende do servidor
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
