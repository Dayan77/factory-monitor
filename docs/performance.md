# FactoryConnect — Desempenho e escala (Sprint 7, Fase 6)

Validação de carga do roteiro (Fase 6): **5 tenants × 4 linhas × 10 máquinas × 12 sinais a 1 Hz + contador de peças**
(≈ 2 600 leituras/s) pelo caminho real — broker MQTT → ingestão → InfluxDB + Postgres → detectores no processo
worker — com clientes da API e geração de relatórios ao mesmo tempo, sobre 30 dias de histórico.

Metas: **atraso de ingestão p95 < 2 s** e **p95 < 500 ms** em `/api/overview`, `/api/oee` (30 dias),
`/api/losses/pareto` (30 dias) e `/api/maintenance-plans/status`, nessa carga; nenhuma leitura perdida.

Harness: [`e2e/scale/load.py`](../e2e/scale/load.py) (dados: [`e2e/scale/seed.py`](../e2e/scale/seed.py); resultado
da última execução: [`e2e/scale/result.json`](../e2e/scale/result.json)). Como rodar: §8.

## 1. Resultado

Ambiente de medição: contêiner de nuvem com **4 vCPU compartilhadas** — no mesmo host rodam o gerador de carga
(4 processos publicadores), o broker, o Postgres, o InfluxDB, a API, a ingestão, o worker **e o trabalho de outros
engenheiros** (testes, Vite, Chromium do Playwright, a stack de dev com o próprio simulador). O InfluxDB da stack de dev
já consumia ~1 vCPU contínua antes de qualquer teste; a carga média do host ficou entre 5 e 15 durante as medições.
Os números absolutos abaixo valem para esse host; o §1.2 dá o dimensionamento.

### 1.1 Antes × depois (mesmo harness, mesmo host)

| Medida | Antes (código do checkpoint e8442e5) | Depois — 1 300 leituras/s (5 × 2 × 10) | Depois — 2 600 leituras/s (5 × 4 × 10) |
|---|---|---|---|
| Leituras processadas durante a carga | **234/s** (de 2 421/s publicadas) | 1 221/s = tudo | 1 190–1 590/s (CPU do host esgotada, ver abaixo) |
| Leituras perdidas / dead letters | 0 / 0 (mas 10 min para drenar) | 0 / 0 | 0 / 0 (drena em ~1 min) |
| Atraso de ingestão p95 (leitura → WebSocket) | **121 s** | **1,1 s** ✅ | 83–106 s |
| `/api/overview` p95 | 2 152 ms | **176 ms** ✅ | 823–1 751 ms |
| `/api/oee` 30 d p95 | 1 408 ms | **193 ms** ✅ | 794–1 317 ms |
| `/api/losses/pareto` 30 d p95 | 1 460 ms | **180 ms** ✅ | 844–1 140 ms |
| `/api/maintenance-plans/status` p95 | 1 594 ms | **184 ms** ✅ | 690–1 231 ms |
| Relatório semanal de OEE (planta, JSON) | 57,7 s | 3,6–5,6 s | 12–21 s |
| Relatório mensal de manutenção | 93,9 s | 5,6–7,1 s | 15–33 s |
| Relatório diário de produção | 19,3 s | 1,6–2,7 s | 4,5–12 s |

Sem carga (mesmo banco de 30 dias): overview p50 132 ms / p95 284 ms sem cache, **8 ms / 19 ms** com o cache de
§6; relatórios: semanal 2,4 s, mensal 4,0 s (era 9,5 s), diário 0,8 s.

**Leitura honesta**: na carga nominal (2 600/s) **neste host** as metas não fecham — a ingestão precisa de ~0,9 vCPU
só para ela, o InfluxDB de outra ~1 vCPU, e o host já tem a CPU tomada pelo resto; tudo fica esperando CPU (inclusive
as respostas em cache: a API p95 sob carga é praticamente igual com e sem cache, porque o tempo é fila de CPU, não
cálculo). Com metade da carga (1 300/s, 100 máquinas) **todas as metas fecham** com folga, sem perda. A capacidade de
um único processo de ingestão, medida isolada (`e2e/scale` + processo real, sem API), é de **~3 000 leituras/s**
(§3) — o software atende a meta; o host de teste não tem CPU para o conjunto.

### 1.2 Dimensionamento recomendado (produção)

| Carga | vCPU | Observação |
|---|---|---|
| até ~1 300 leituras/s (≈ 100 máquinas com 13 sinais a 1 Hz) | 4 | medido acima (com o host compartilhado) |
| ~2 600 leituras/s (200 máquinas) | **8** | 1 processo de ingestão (≈ 1 vCPU), InfluxDB ≈ 1–1,5, Postgres ≈ 0,5, API ≈ 1, worker ≈ 0,5; folga para picos de descarga de buffer |
| > 3 000 leituras/s | 8+ e `MQTT_INGEST_SHARDS=2` | cada shard recebe todas as mensagens e processa a sua parte das tags (crc32), então só compensa com CPU sobrando (com 4 vCPU 2 shards foram **piores**: 1 230/s contra 1 585/s) |

O broker de produção (`infra/mosquitto/prod/mosquitto.conf`) usa `max_inflight_messages 100`; a ingestão confirma
cada leitura só quando ela está gravada, então a janela limita a vazão a ≈ 100 / latência de processamento (100 /
0,05 s = 2 000/s no melhor caso). Para ≥ 2 000 leituras/s use 1 000–2 000 (o harness usa 2 000,
`e2e/scale/mosquitto.conf`) — item para o devops (não alterado aqui).

## 2. Detectores depois de longas quedas (achado do QA)

**Problema**: depois de ~40 h sem dados o checkpoint do detector de paradas não avançava — cada passada re-derivava o
buraco inteiro e estourava `DOWNTIME_MACHINE_TIMEOUT_S` (30 s); o laço era serial sobre todos os tenants, então uma
máquina travada atrasava todo mundo.

| Motor | O que mudou | Prova |
|---|---|---|
| Paradas (`oee/detector.py`) | atraso processado em **fatias** de `DOWNTIME_SLICE_READINGS` (20 000) leituras por tag, cada fatia avança e grava o checkpoint; uma máquina passa fatias por até `DOWNTIME_CATCHUP_BUDGET_S` (20 s) por ciclo e cede a vez; máquinas em paralelo com `DETECTOR_CONCURRENCY` (4), intercaladas por tenant; roda no processo worker | `test_detector_catchup.py`: 72 h de buffer descarregado de uma vez → alcança em fatias, resultado **igual** ao de uma máquina detectada ao vivo, e o tenant B é detectado em todo ciclo com a latência dele |
| Paradas — host lento (Sprint 7b) | uma fatia que estoura `DOWNTIME_MACHINE_TIMEOUT_S` é desfeita (checkpoint parado); repetir a **mesma** fatia estouraria para sempre num host lento/contendido (a medição de carga de 2 600/s registrou `downtime.detector.timeout` em série). Agora a fatia **daquela máquina** cai pela metade a cada timeout (mínimo 200 leituras por tag) e dobra de volta a cada passada boa até `DOWNTIME_SLICE_READINGS` — o checkpoint sempre avança; o log `downtime.detector.timeout` mostra `next_slice_readings` | `test_slice_shrinks_after_timeout_so_a_slow_host_still_catches_up` (passada > 600 leituras "leva 5 s", timeout 1 s → alcança, resultado = uma passada sem limite da máquina gêmea), `test_slice_feedback_halves_on_timeout_and_grows_back` |
| Paradas — consultas | máquinas em dia de um tenant (checkpoint a menos de `DOWNTIME_PREFETCH_WINDOW_S` = 10 min) compartilham **uma** consulta Flux por ciclo (`TenantPrefetch`), em vez de uma por máquina (200 máquinas a cada 10 s eram 20 consultas/s no InfluxDB) | `test_tenant_prefetch_equals_one_query_per_machine` |
| Preditiva (`predictive/engine.py`) | cada execução extrai janelas/blocos por no máximo `PREDICTIVE_EXTRACT_BUDGET_S` (60 s) e grava; a próxima continua do cursor (antes, uma execução mais longa que `PREDICTIVE_MONITOR_TIMEOUT_S` era desfeita e recomeçava para sempre); monitores em paralelo limitado, por tenant | `test_backlog_engines.py::test_predictive_backlog_…`: 72 janelas em execuções limitadas = uma execução única |
| Manutenção (`maintenance/engine.py`) | chamados emergenciais, escalonamentos e OS atrasadas rodam e fazem commit **antes** do recálculo dos planos; tenants em paralelo limitado com timeout por tenant; o recálculo de vida dos componentes usa um número fixo de consultas (§4) | `test_maintenance_urgent_stages_survive_a_hanging_plan_stage`, `test_maintenance_slow_tenant_does_not_delay_the_others` |
| Uso (contadores, horas) | já era por leitura dentro da ingestão (sem passada sobre o atraso); agora em lotes por tenant (§3) — um buffer de 72 h é só mais leituras na fila, com contrapressão | `test_ingest_scale.py::test_batched_turns_equal_reading_by_reading` |
| Agregados do InfluxDB | leituras atrasadas além da janela da task de 1 min são re-agregadas pela ingestão (§5) | `test_late_readings_are_backfilled_into_the_aggregates` |

**Atraso por máquina** (`oee/lag.py`, processo da API a cada 30 s): `fm_detector_lag_seconds{detector,tenant_id,entity_id}`
(paradas: última leitura vista − checkpoint; preditiva: janelas fechadas ainda não extraídas),
`fm_detector_lag_max_seconds{detector}` e `fm_detector_lagging{detector}` (acima de `DETECTOR_LAG_ALERT_S` = 15 min) →
alerta Prometheus `FmDetectorLagHigh` e log `detector.lag_high` / `detector.lag_recovered`.

O processo worker roda com prioridade de CPU menor (`WORKER_NICE` = 5): sob pressão, API e ingestão vão primeiro;
os detectores alcançam depois (limitados, sem perder nada).

## 3. Ingestão MQTT

Diagnóstico (perfis `py-spy` do processo de ingestão sob a carga):

1. **Recepção, não processamento**: o iterador de mensagens do aiomqtt cria uma task + `asyncio.wait` **por
   mensagem** (≥ 2 voltas do event loop cada); com o pipeline ocupado (voltas de alguns ms) o assinante tirava
   ~250–700 mensagens/s da fila, por mais rápido que fosse o resto, e o broker acumulava o atraso (o sintoma do QA:
   60–120 s de atraso). Agora as mensagens já enfileiradas são consumidas direto (`_drain_messages`), e o socket é
   lido até esvaziar a cada evento (`_burst_reader`, até 2 000 pacotes; o leitor do aiomqtt lia 1 pacote por volta).
2. **Uma transação por leitura**: run_state, fault_state e contador de cada máquina (3 × 200/s) abriam uma transação
   cada (~8 idas ao banco). Agora um **turno do pipeline atende até `MQTT_KEYS_PER_TURN` (128) tags** com leituras
   esperando; as leituras de uso de um tenant vão numa transação (`usage.processor.process_many`: um lock do tenant,
   um SELECT … FOR UPDATE dos estados em ordem de id, um upsert somado por tabela com `unnest` — SQL constante, sem
   recompilar por número de linhas — e uma derivação de status por máquina); `last_seen_at` das máquinas do lote num
   comando (`mark_seen_many`); as leituras do lote num único pedido ao InfluxDB (`write_many`, linha de protocolo
   montada à mão — byte a byte a da biblioteca). A ordem por tag e a confirmação (PUBACK) por tag continuam; os tenants
   de um turno rodam em paralelo (um tenant travado não segura o outro); falha não transitória de um lote cai para
   uma transação por tag (isola e manda para dead letter só a leitura ruim).
3. **Derivação de status sem lock**: a derivação de status da máquina (`derive_machine_status`) lê sem travar a linha
   e só trava/grava quando o status muda (quase nunca: a linha `machines` era o ponto quente).
4. **Saltos de event loop por leitura**: tag em cache e regras de alerta em cache são resolvidas sem `await`
   (cada `wait_for` custava uma volta do loop por leitura, e as leituras de um lote são preparadas em sequência).
5. **Conexões**: o pool do processo de ingestão é dimensionado (`INGEST_DB_POOL_SIZE` = 24): conexões acima do
   `pool_size` eram fechadas e reabertas (com autenticação SCRAM a cada vez).

| Medida (1 processo de ingestão, 2 600 leituras/s publicadas, sem API) | Antes | Depois |
|---|---|---|
| Vazão sustentada | ~250/s | **2 600–3 200/s** (alcança o atraso inicial e acompanha) |
| Idas ao Postgres por leitura de uso | ~8 | ~8 por **lote** (dezenas de tags) |
| Pedidos HTTP ao InfluxDB | 1 a cada ~6 leituras | 1 a cada ~220 leituras |

Equivalência: `server/tests/test_ingest_scale.py` — o mesmo fluxo (contador de peças, contador de ciclos de posição,
run_state, fault_state, analógico) processado em lotes e leitura a leitura dá **os mesmos** totais, buckets, estados
de tag, status de máquina e pontos no InfluxDB.

## 4. Índices do Postgres (migração `c6d7e8f9a0b1`)

`usage_buckets (scope, entity_id, bucket_start)` — "desde quando há dados" (kpis.md S7-2) e janelas por entidade. A chave
primária tem `measure` entre `entity_id` e `bucket_start`; com o plano genérico das consultas preparadas o Postgres
percorria o índice do tenant inteiro:

```
-- antes (plan_cache_mode = force_generic_plan, 30 dias, 100 mil buckets por tenant)
Index Scan using ix_usage_buckets_tenant_bucket on usage_buckets (actual time=26.733..26.733 rows=0 loops=1)
  Index Cond: ((tenant_id = $1) AND (bucket_start IS NOT NULL))
  Filter: (((scope)::text = ($2)::text) AND (entity_id = $3))
  Rows Removed by Filter: 100080
Execution Time: 26.807 ms
-- depois
Index Scan using ix_usage_buckets_entity_start on usage_buckets (actual time=0.035..0.035 rows=0 loops=1)
  Index Cond: (((scope)::text = ($2)::text) AND (entity_id = $3) AND (bucket_start IS NOT NULL))
Execution Time: 0.088 ms
```

Um ciclo de `/api/maintenance-plans/status` fazia 124 dessas consultas (4,5 s de banco em 32 requisições); o relatório
mensal de manutenção caiu de 9,5 s para 4,0 s só com o índice. Criado com `CONCURRENTLY` (não bloqueia a ingestão numa
base grande).

Consultas N+1 removidas no mesmo passo: vida/RUL de todos os componentes (`usage.queries.component_usage_details`:
de ~7 consultas por componente para um número fixo — um tenant com 400 componentes fazia ~2 800 consultas a cada
ciclo de 30 s do motor de manutenção), alertas de vida (uma consulta para os já enviados), lista de falhas
(`/api/failures/`: nomes e componentes em uma consulta, não 2–3 por falha).

## 5. InfluxDB: retenção e agregados

| Bucket | Conteúdo | Retenção |
|---|---|---|
| `sensors` | leituras brutas | `INFLUX_RAW_RETENTION_DAYS` = **90 dias** |
| `sensors_1m` | agregados de 1 min (`sum`, `count`, `min`, `max`, `last`, no início da janela) | `INFLUX_1M_RETENTION_DAYS` = **2 anos** |
| `sensors_1h` | agregados horários (dos de 1 min) | **para sempre** |

`services/influx_downsampling.py`: o worker cria/atualiza buckets, retenção e duas **tasks do InfluxDB** de forma
idempotente na partida (`fm_downsample_sensors_1m` a cada minuto sobre os últimos 15 minutos completos;
`fm_downsample_sensors_1h` a cada hora sobre as últimas 3 horas completas) e completa o histórico anterior às tasks
de trás para frente, um dia por vez (cobertura registrada no próprio bucket de 1 min). Leituras que chegam depois da
janela da task (buffer do gateway descarregado horas depois) são anotadas pela ingestão e re-agregadas em até 30 s.

Leitura: `influx.query_history` (gráficos `/api/sensors/history`) usa os agregados para a parte coberta de intervalos
≥ 6 h com janela de minutos inteiros (horária se a janela for de horas inteiras) e o bruto para o resto (antes da
cobertura e os últimos minutos ainda não agregados) — nenhum gráfico fica sem dados por uma task atrasada. A média de
cada janela vem de Σsum/Σcount, **exata** (igual à do bruto: `test_influx_downsampling.py`). Os KPIs não usam os
agregados (kpis.md S7-18). Com retenção, `/api/usage/recompute?apply=true` é recusado (409 `beyond_raw_retention`)
quando o uso do tenant é mais antigo que o bruto guardado — recalcular apagaria esse uso.

`INFLUX_DOWNSAMPLING=false` desliga tudo; `INFLUX_MANAGE_RETENTION=false` não mexe na retenção do bucket bruto.

## 6. Cache das telas de painel

`services/response_cache.py`, `API_CACHE_TTL_S` = 10 s: `/api/overview`, `/api/oee`, `/api/losses/pareto` e
`/api/maintenance-plans/status` guardam a resposta por **usuário** e consulta (RBAC, escopo de linhas do operador e
isolamento de tenant são exatamente os da requisição que calculou); pedidos iguais simultâneos compartilham um cálculo;
qualquer gravação do tenant feita pela API invalida as entradas dele na hora (kpis.md S7-19). Métrica
`fm_api_cache_requests_total{endpoint,result=hit|miss|shared}`. Testes: `test_response_cache.py`.

Ajuda quem consulta a mesma coisa repetidamente (painéis de TV, várias abas, presets de período); não ajuda quando a
CPU do host é o gargalo (§1.1).

## 7. Relatórios

O relatório é composição de respostas da própria API (reports.md §0.1). As respostas independentes agora são buscadas
em paralelo (`ApiSource.prefetch`, até 4 por vez; mesmos parâmetros, mesma resposta, nunca duas vezes) e as
consultas por trás delas ficaram mais baratas (§4). O tempo de cada chamada interna aparece em `api_calls[].ms` do
documento JSON. Para relatórios grandes em horário de carga use `mode=job` (202 + download quando pronto) ou o
agendamento (o motor gera fora da requisição).

## 8. Como rodar

```bash
.venv/bin/python e2e/scale/load.py                                # 5×4×10, 30 min, metas → exit 0
DURATION_S=180 .venv/bin/python e2e/scale/load.py                 # 3 min
LINES=2 DURATION_S=180 .venv/bin/python e2e/scale/load.py         # metade da carga (1 300/s)
API_CACHE_TTL_S=0 DURATION_S=180 .venv/bin/python e2e/scale/load.py   # API sem cache
API_CONC=0 REPORTS=0 DURATION_S=120 .venv/bin/python e2e/scale/load.py # só ingestão
```

O harness sobe o próprio broker (porta 8162), a própria API (8160, com ingestão e worker como processos filhos), um
banco próprio (`fm_scale`) e um bucket próprio no InfluxDB (`fm_scale_8160` + agregados), e apaga tudo no fim
(`KEEP_DB=1` mantém). Fala com o Postgres e o InfluxDB pelo IP da rede docker (`DIRECT=1`, como o servidor em
produção): pelo `docker-proxy` de localhost cada ida ao banco levava 0,33 ms contra 0,07 ms direto (medido), e o
próprio `docker-proxy` chegava a ~25 % de uma vCPU sob a carga.

Saída: `e2e/scale/result.json` (configuração, carga média do host no início/fim, amostras a cada 10 s, latências e
metas).

## 9. Próximos passos

- Ingestão: hoje cada leitura vira uma mensagem WebSocket por cliente do tenant (520/s por painel aberto nesta carga)
  e passa pelo relay da API mesmo sem nenhum cliente conectado; enviar só para tenants com telas abertas e em lotes
  reduz CPU na ingestão, na API e no navegador (muda o contrato do WebSocket — frontend junto).
- Relatórios: gerar fora do processo da API (processo próprio) para que um relatório grande nunca dispute CPU com
  as requisições interativas.
