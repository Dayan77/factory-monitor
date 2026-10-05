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

## Manutenção na planta virtual (Sprint 4)

### Desgaste e falhas por uso (`edge/virtual_plant/wear.py`)

Cada componente tem uma **vida até a falha** sorteada na instalação (`random.Random(f"{seed}/{item}/{instalação}")`):
a falha acontece quando o **uso desde a instalação** chega a ela — mesmo seed ⇒ mesmas falhas no mesmo uso, qualquer
que seja `--speed`, então MTBF/MCBF do servidor são reproduzíveis. Falhas aleatórias (MTBF) continuam como antes.

| item (`id` na API) | medida | sintoma | falha |
|---|---|---|---|
| `montagem.fixacao.a1`…`a4` (vedação) | ciclos | `stroke_time` sobe com os ciclos (A2 já gasta: +20 ms/1000 golpes) | deriva ≥ 27–33 % do curso novo → 443–446 vazamento (`EQP-03`) |
| `montagem.transportador.motor`, `usinagem.centro.coolant` (rolamento) | horas de operação | corrente ×(1 + 0,3·f²); tags novas `motor.vibration` / `coolant.vibration` (mm/s) 1,1 + 6·f⁴ | 252 / 214 (`EQP-02`) |
| `montagem.robo.axis1`…`axis6` (redutor) | movimentos do eixo | — | Weibull(η 4,5 M, β 2,5) condicionada à idade → 132–137 (`EQP-01`) |
| `usinagem.torno.tool`, `usinagem.centro.tool` (ferramenta) | ciclos de corte | corrente do eixo-árvore ×(1 + 0,25·f²) | Weibull(η 800 / 380, β 4) → 523 / 524 (`EQP-05`) |

Códigos seguem a centena → categoria do layout (`FAULT_CATEGORIES`): vazamento de vedação é pneumático (4xx), folga
de redutor é mecânica (1xx). Seed 42, calendário padrão: ferramenta do centro falha em ~15 h simuladas, vedação A2 em
~18 h, ferramenta do torno em ~18 h (≈ 15–18 min reais a `--speed 60`). `--wear-repair auto` (padrão): o plantão troca
o componente após um tempo sorteado; `--wear-repair manual`: a máquina fica em falha até a API de controle agir.
`--no-wear-failures` mantém só os sintomas; `--no-faults` desliga as duas. **Atenção à aceleração:** durações são em
tempo simulado, mas o servidor mede paradas no relógio real — a `--speed 60` um reparo automático de 40 min dura 40 s
reais e vira micro-parada (< 120 s) no servidor, assim como as falhas aleatórias. Para demonstrar o ciclo de
manutenção acelerado use `--wear-repair manual` (a falha dura até a API agir) ou `force-failure`. Pelo mesmo motivo,
horas de operação no servidor não aceleram (o `run_state` é integrado pelo relógio real) e, acima de ~25×, partidas
de ciclos curtos (bomba/eixo-árvore, < 1 s real desligado) caem no debounce de `run_state` do servidor.

A verdade de campo está em `GET :8091/state` → `truth.wear` e em `GET :7070/status` → `components`: por item
`usage_since_install`, `life_to_failure`, `fails_at_usage`, `remaining`, `failures[]` (início, uso, ação) e
`replacements[]` (idade na remoção, motivo).

### API de controle (`127.0.0.1:7070`, `edge/virtual_plant/control.py`)

| ação | corpo | efeito |
|---|---|---|
| `GET /status` | — | componentes (acima) + falha ativa por máquina |
| `POST /replace` | `{"position": "montagem.fixacao.a2", "reset_counter": false, "reason": "preventive_replacement"}` | troca: desgaste zerado, nova vida; `reset_counter` zera o contador do CLP da posição; encerra a falha do componente |
| `POST /repair` | `{"machine": "montagem.robo"}` | libera a máquina; falha por desgaste sem troca = paliativo (volta a falhar logo) |
| `POST /force-failure` | `{"machine": "Robô de Solda", "code": 331, "duration_s": null}` ou `{"position": "montagem.robo.axis1"}` | falha agora (até `/repair`, ou por `duration_s` simulados); por posição = falha por desgaste do componente |

Só localhost (fora disso exige `VP_CONTROL_TOKEN` + cabeçalho `X-Control-Token`). Cliente sem dependências:

```bash
python -m edge.virtual_plant.control status
python -m edge.virtual_plant.control force-failure --machine montagem.robo --code 331   # critério de aceite 7
python -m edge.virtual_plant.control repair montagem.robo                               # máquina volta (restored_at)
python -m edge.virtual_plant.control replace montagem.fixacao.a2 --reset-counter         # troca com reset do CLP
```

Demonstração do ciclo completo (stack local, ~1 h): `make dev` (ou backend local) → `python -m edge.virtual_plant
--speed 60` → `python -m edge.src.provision --speed 60` → `python -m edge.src.provision_maintenance --speed 60
--demo-minutes 60` → gateway com `PLANT_MAP_PATH=edge/config/plant.local.json`. Os planos de calendário já nascem
`due_soon`/`due` (rascunho de OS na hora); os de uso cruzam 80 %/100 % durante a janela; forçar a falha do robô abre a
parada `EQP-04` e a falha; `/repair` encerra a parada (restabelecimento); `/replace` reproduz a troca da OS.

### Planos de manutenção (`python -m edge.src.provision_maintenance`)

Um plano por posição da Planta Manaus (e dois de máquina, por calendário), sufixo `(planta virtual)`, idempotente pelo
nome (`POST /api/maintenance-plans/`, divergência de configuração → `PATCH`; `last_done_at` só na criação).
`--profile demo` (padrão com `--speed > 1`): intervalo de uso = ritmo medido da posição × janela (`--demo-minutes` ×
`--speed` para ciclos e partidas; horas de operação não aceleram — o servidor integra o `run_state` pelo relógio real)
÷ alvo (1,5 → vence em ~40 min; 1,0 → `due_soon` em ~48 min; 0,3–0,5 → `ok`). `--profile realistic`:
intervalos de fábrica (2 000 000 ciclos / 180 dias para vedações etc.).

## Configuração remota do gateway (`edge/src/remote_config.py`)

Padrão inalterado: mapa do arquivo `PLANT_MAP_PATH`. Com `GATEWAY_CONFIG_URL` (URL base do servidor) o gateway busca
`GET {url}/api/gateways/{GATEWAY_ID}/config` com `Authorization: Gateway <GATEWAY_TOKEN>` (`If-None-Match` com o ETag
da cópia local), valida o mapa e grava a cópia em `GATEWAY_CONFIG_CACHE` (atômica, 0600, sem o token). Sem servidor,
401/403, 5xx ou mapa inválido → cópia local; sem cópia → `PLANT_MAP_PATH`; nada disso → não sobe. Resposta esperada:
`{"gateway_id": "...", "revision": "...", "plant_map": {mapa v1}}` (ou o mapa v1 direto no corpo). O endpoint do
servidor vem num sprint seguinte.

No compose (`fm_virtual_plant`), a API de controle fica no localhost do container:
`docker exec fm_virtual_plant python -m virtual_plant.control status`. Para expô-la numa rede de testes:
`VP_CONTROL_HOST=0.0.0.0` + `VP_CONTROL_TOKEN=<segredo>` (o cliente lê `VP_CONTROL_TOKEN` e `VP_CONTROL_URL`).
