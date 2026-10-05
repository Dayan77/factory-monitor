# edge — gateway, planta virtual e provisionamento

Visão geral do projeto em `CLAUDE.md`. Este arquivo cobre o que o edge faz para os KPIs da Fase 2 (OEE).

## Planta virtual (`python -m edge.virtual_plant`)

Modelo determinístico (`edge/virtual_plant/model.py`): mesma `--seed` + mesmo tempo simulado ⇒ mesmos números,
qualquer que seja `--speed`. Caminhos de OEE exercitados:

| caminho | como aparece nos sinais | padrão |
|---|---|---|
| calendário | 3 turnos 06–14, 14–22, 22–06; refeição 1 h + 2 cafés de 10 min (`PLN-01`); fora de turno/pausa tudo para | `--calendar standard` (`none` = 24 h) |
| produto / ciclo ideal | tag `product_code` = `plc_code` do produto; ciclo ideal por (máquina, produto) | torno EIXO-20 45 s / EIXO-32 52 s; centro FLG-A 90 s / FLG-B 150 s com 2 peças/ciclo; célula SUP-100 40 s / SUP-200 44 s |
| setup `SET-01` | 1×/turno: run_state 0, fault 0, `product_code` muda no início | torno +60 min (15 min), centro +75 min (20 min), célula +105 min (12 min) |
| perda de velocidade | ciclo real = ideal × (1 + x % + U(0, 2 %)) | `--speed-loss-pct 5` |
| micro-paradas | run_state 0, fault 0, 15–110 s (< 120 s), taxa por hora em operação | 3/h torno, 2/h centro, 4/h célula (`--micro-stops-per-h`) |
| falhas | `fault_state` = código; centena → categoria → motivo | 1xx mecânica `EQP-01`, 2xx elétrica `EQP-02`, 3xx automação `EQP-04`, 4xx pneumática `EQP-03`, 5xx ferramental `EQP-05` |
| starved / blocked | célula fixação → robô → transportador: a montante de quem parou fica `blocked`, a jusante `starved`; buffer de 15 min do centro → célula: centro parado mais que isso ⇒ célula inteira `starved` (`MAT-01`) | — |
| refugo | por peça | torno 2 %, centro 1,5 %, célula 1,5 % |

`GET :8091/state` traz a verdade de campo para o QA (`truth.events`: falhas, micro-paradas, setups, starved,
blocked por máquina, com início/duração em segundos simulados). As tags de produto/starved/blocked vão ao servidor
como `semantic_role: analog` com o papel de borda em `signal_role` (o servidor ainda não tem esses papéis).

## Playback de cenário (QA, caminho real CLP → gateway → MQTT → servidor)

```bash
# 1) mapa com o calendário do cenário e provisionamento (tenant/planta de QA)
python -m edge.virtual_plant --write-map /tmp/plant.c.json --scenario docs/scenarios/scenario_c_oee_shift.json
python -m edge.src.provision --map /tmp/plant.c.json --out /tmp/plant.c.local.json --speed 60
# 2) planta virtual: espera o gateway assinar e reproduz 8 h em 8 min
python -m edge.virtual_plant --scenario docs/scenarios/scenario_c_oee_shift.json --speed 60
# 3) gateway com o relógio do cenário (deslocado N dias inteiros para o passado; hora local preservada)
PLANT_MAP_PATH=/tmp/plant.c.local.json VIRTUAL_PLANT_MAX_CLOCK_SKEW_S=off python -m edge.src.gateway
```

Pelo compose (profile `sim`, `docs/scenarios` montado em `/scenarios`; o `provision-sim` gera o mapa com o
calendário do cenário):
```bash
VP_SCENARIO=/scenarios/scenario_c_oee_shift.json VIRTUAL_PLANT_MAX_CLOCK_SKEW_S=off VP_SPEED=60 \
  docker compose --env-file .env -f infra/docker-compose.yml --profile sim up -d
```
O servidor re-detecta sozinho leituras que chegam depois do checkpoint do detector (playback, buffer do
gateway — docs/kpis.md D12): não é preciso segurar o detector nem apagar `downtime_detector_states`.

No playback a planta re-escreve as tags da máquina do cenário a cada `--scenario-heartbeat-s` (10 s do cenário,
mesmo valor e timestamp novo), para o servidor não ver "sem dados" nas paradas. A planta imprime o intervalo de timestamps (`device_start → device_end`), que é a janela a consultar no servidor
(`GET /api/oee?scope=machine&id=…&start=…&stop=…`). Cada transição de `run_state`/`fault_state` sai com o
instante exato do cenário; as peças (`production.produced`) são distribuídas uniformemente pelo tempo rodando.
Modbus não tem timestamp: playback fiel só de máquina OPC UA (o Cenário C é o Torno CNC) ou em `--speed 1`.

## TLS até o broker de produção

`MQTT_TLS=true` + `MQTT_BROKER_PORT=8883` (e `MQTT_CA_CERT=/caminho/ca.pem` se a CA não for pública; vazio = CAs
do sistema). Certificado e nome do host são sempre verificados; CA inválida ou ausente → o gateway não sobe (nunca
cai para texto puro). Credenciais: `make mqtt-gateway-add` no VPS (docs/operations.md §6.2).

## Política de timestamp do gateway (um relógio por dispositivo)

Configurada por fonte no mapa (`sources[].timestamps`, `sources[].max_clock_skew_s`); detalhes em
`edge/src/clock.py`.

| política | mudança com timestamp do CLP | leitura sem timestamp (poll, re-envio de heartbeat) |
|---|---|---|
| `gateway` (padrão) | hora de recebimento no gateway | hora de recebimento no gateway |
| `source` (CLP sincronizado por NTP) | SourceTimestamp do CLP | relógio do CLP estimado (agora + desvio medido) |

- Desvio (SourceTimestamp − recebimento) > `max_clock_skew_s` (padrão 2 s): em `gateway` só avisa
  (`edge_gateway.clock_skew`, no máximo 1×/10 min por dispositivo); em `source` o dispositivo é rebaixado para
  `gateway` até reiniciar (`edge_gateway.clock_untrusted`, erro) — troca de relógio no máximo uma vez, nunca
  leitura a leitura. Atraso de entrega só deixa o SourceTimestamp mais velho, então a estimativa é o maior desvio
  das mudanças dos últimos 120 s; "atrasado" só rebaixa se persistir por ≥ 30 s (rajada de notificações
  enfileiradas depois de um blip não rebaixa); "adiantado" rebaixa na hora.
- A **notificação inicial** de cada item OPC UA (ao assinar) traz a hora da última *mudança* do valor (pode ser de
  horas atrás) e não conta como desvio; em `source` sai com esse timestamp.
- `max_clock_skew_s: off` com `source`: relógio do dispositivo sem checagem e sem re-envio sintetizado (playback).
- Sempre: timestamp estritamente crescente por tag (+1 ms quando preciso). O heartbeat do gateway publica
  `clocks` com a política efetiva e o último desvio de cada fonte.
- Modbus não traz timestamp: sempre `gateway`.
- O silêncio do deadband (re-envio de valor parado a cada `DEADBAND_MAX_SILENCE_S`) é medido no relógio da
  própria leitura — o mesmo do timestamp publicado.
- `sources[].device_heartbeat: true` (OPC UA): o dispositivo re-carimba valores parados; a assinatura usa
  `DataChangeTrigger.StatusValueTimestamp` e recebe esses "estou vivo" no relógio do dispositivo. É assim que o
  playback de cenário (relógio deslocado, sem re-envio sintetizado) mantém as paradas longas cobertas — o servidor
  considera "sem dados" depois de 60 s sem leitura. Não ligar para servidores OPC UA que re-carimbam a cada scan.
- A planta virtual declara `source` + `device_heartbeat` para o OPC UA (relógio do host). Assinaturas OPC UA usam
  fila (64 por item) para não perder transições entre dois publishes; playback acima de ~1800× pode transbordá-la.

## Provisionamento (`python -m edge.src.provision`)

Idempotente (procura antes de criar; diverge → PATCH). Além de planta/linhas/máquinas/posições/componentes/tags,
cria turnos com pausas (`/api/shifts/`), produtos (`/api/products/`), ciclo ideal por (máquina, produto)
(`/api/machines/{id}/products`) e o `fault_code_map` da máquina (quando o servidor expõe o campo). Servidor sem
os endpoints do Sprint 3 → aviso e segue.
