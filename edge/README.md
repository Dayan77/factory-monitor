# edge — gateway, planta virtual e provisionamento

Visão geral do projeto em `CLAUDE.md`. Este arquivo cobre o que o edge faz para os KPIs da Fase 2 (OEE), a
manutenção (Sprint 4), a preditiva e a frota de gateways (Sprint 5 — [instalação na fábrica](#instalação-na-fábrica)).

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

## Demonstração pública (`--demo`, Sprint 6)

`python -m edge.virtual_plant --demo --state-file /data/demo_state.json` — roteiro do tenant DEMO
(docs/sprint6.md §5, `edge/virtual_plant/demo.py`): tempo real no fuso `America/Sao_Paulo` (3 × 8 h todos os
dias), ≈ 3–5 falhas com código por turno (`DEMO_FAULT_MTBF_SCALE`), 1 setup por turno, micro-paradas e o Cilindro B3
com 12 golpes/ciclo (`DEMO_B3_STROKES_PER_CYCLE`) até o alerta preditivo em ≈ 2,5 dias; `POST /replace
{"position": "montagem.fixacao.b3"}` (demo-bot do servidor) reinicia o desgaste. `GET :8091/health` → 503 se o tempo
simulado parar ou o relógio sair do fuso. Produção: profile `demo` do `infra/docker-compose.prod.yml`
(docs/operations.md §10). Nunca é o padrão.

## Navegação OPC UA pelo gateway (comandos puxados, Sprint 6)

O heartbeat HTTP pode trazer `commands: [{id, type: "opcua_browse", source_id, root_node_id?, max_depth ≤ 6,
max_nodes ≤ 5000}]`; o gateway navega só a fonte do SEU mapa (credenciais locais: `opc.tcp://${OPC_USER}:${OPC_PASS}@…`)
e responde `POST /api/gateways/{id}/commands/{cmd}/result {nodes, truncated}` com o próprio token — só estrutura
(NodeClass, nomes, DataType, AccessLevel), nunca Value nem credencial; tempo total `GATEWAY_BROWSE_TIMEOUT_S`
(60 s). Detalhes em `edge/src/commands.py`.

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

Padrão inalterado: mapa do arquivo `PLANT_MAP_PATH`. Com `GATEWAY_CONFIG_URL` (URL base do servidor) — ou depois do
cadastro na frota (abaixo) — o gateway busca `GET {url}/api/gateways/{GATEWAY_ID}/config` com
`Authorization: Gateway <token>` (`If-None-Match` com o ETag da cópia local), valida o mapa e grava a cópia em
`GATEWAY_CONFIG_CACHE` (atômica, 0600, sem o token, com o `config_version`). Sem servidor, 401/403, 5xx ou mapa
inválido → cópia local; sem cópia → `PLANT_MAP_PATH`; nada disso → não sobe. Resposta:
`{"gateway_id": "...", "revision": "7", "config_version": 7, "plant_map": {mapa v1}}` (contrato em docs/gateways.md).

## Gateway na frota (Sprint 5): cadastro, heartbeat e recarga a quente

| Peça | Arquivo | O que faz |
|---|---|---|
| cadastro | `edge/src/enrollment.py` | 1ª partida com `ENROLL_CODE` + `SERVER_URL`: `POST /api/gateways/enroll {code}` → `{gateway_id, tenant_id, token, mqtt{username, password}}` gravado em `GATEWAY_CREDENTIALS_PATH` com **0600** (diretório 0700, escrita atômica). Partidas seguintes usam o arquivo; o código é de uso único. Código inválido/expirado (401) → sai com código 3 (não adianta reiniciar); servidor fora do ar → tenta de novo (5 s … 60 s). `SERVER_URL` precisa ser `https://` (http só localhost ou `ALLOW_INSECURE_HTTP=true`). Token e senha nunca vão para o log. |
| configuração | `edge/src/remote_config.py` | como acima, com o token do cadastro |
| heartbeat HTTP | `edge/src/fleet.py` | a cada `GATEWAY_HEARTBEAT_S` (60 s): `POST /api/gateways/{id}/heartbeat {version, uptime_s, buffer_depth, errors[], config_version}`. `errors`: fontes desconectadas, broker MQTT fora, falhas de heartbeat/configuração desde o último heartbeat entregue. Resposta `config_changed` → busca a config e aplica. Por garantia a config também é consultada a cada `GATEWAY_CONFIG_POLL_S` (300 s; 304 quando nada mudou). |
| recarga a quente | `EdgeGateway.apply_plant_map` | para os drivers do mapa antigo, carrega o novo e sobe os drivers — **sem** reiniciar o processo: buffer SQLite, conexão MQTT, contadores de borda (`cycle_pulse`, SQLite por `key`), deadband, timestamps monotônicos e relógios dos dispositivos continuam. Mapa novo inválido → fica o atual (erro no próximo heartbeat). |
| MQTT | `edge/src/protocols/mqtt_publisher.py` | usuário/senha por gateway: `MQTT_USERNAME`/`MQTT_PASSWORD` do ambiente, senão os do cadastro. TLS: `MQTT_TLS=true`, `MQTT_CA_CERT` (PEM; vazio = CAs do sistema), TLS mútuo opcional `MQTT_CLIENT_CERT` + `MQTT_CLIENT_KEY`; `MQTT_BROKER_PORT` vazio = 8883 com TLS / 1883 sem. Certificado e nome do host sempre verificados, TLS ≥ 1.2; arquivo ausente → o gateway não sobe (código 2). |

Compatibilidade: sem `SERVER_URL`/`ENROLL_CODE`/`GATEWAY_CREDENTIALS_PATH` nada disso roda — `PLANT_MAP_PATH` (mapa em
arquivo) e `GATEWAY_CONFIG_URL` + `GATEWAY_TOKEN` (Sprint 4) funcionam como antes. O mapa aceita o endereço Modbus no
formato do servidor (`"unit=2;hr=10:uint16"`) além do inteiro.

### Instalação na fábrica

**Hardware**: PC industrial/IPC x86-64 ou Raspberry Pi 4/5 com SO **64 bits** (Raspberry Pi OS 64, Ubuntu Server
arm64), 1 GB de RAM, 2 GB livres (buffer offline: ~1 KB por leitura). Relógio sincronizado por NTP (timestamps UTC
na origem). Duas placas de rede são o arranjo recomendado: uma na rede OT dos CLPs, outra na rede corporativa/Internet.

**Rede e firewall** — o gateway **só abre conexões de saída**; não há porta de entrada a liberar:

| Destino | Porta | Protocolo | Para quê |
|---|---|---|---|
| CLPs (rede OT) | 502/TCP | Modbus TCP | leitura de registradores/bits (polling) |
| CLPs / servidores OPC UA (rede OT) | 4840/TCP (ou a do servidor) | OPC UA binário | assinatura de tags |
| servidor FactoryConnect | 443/TCP | HTTPS | cadastro, configuração, heartbeat |
| broker MQTT | 8883/TCP | MQTT sobre TLS | envio das leituras e heartbeat MQTT |
| servidores NTP | 123/UDP | NTP | relógio do host |
| (opcional) registro de imagens | 443/TCP | HTTPS | `docker pull` |

Regras sugeridas no firewall da fábrica: a rede OT **não** roteia para a Internet; só o host do gateway fala com os
CLPs (502/4840) e só ele sai para 443/8883 nos endereços do FactoryConnect. Proxy HTTP corporativo: o gateway usa
`HTTPS_PROXY`/`NO_PROXY` do ambiente para o HTTPS; o MQTT/TLS precisa de saída direta na 8883 (ou um broker na DMZ).

**Cadastro** (no servidor, perfil admin): Gateways → novo gateway (planta, dispositivos: endereço OPC UA / IP Modbus)
→ atribuir as tags → "Gerar código" (`XXXX-XXXX-XXXX`, uso único, 24 h). No gateway:

*Docker* (`infra/gateway/`; imagem multi-arch `linux/amd64` + `linux/arm64`):
```bash
cd infra/gateway && cp gateway.env.example gateway.env   # SERVER_URL, ENROLL_CODE, broker MQTT/TLS
docker compose up -d && docker compose logs -f gateway  # "enrollment.done" → "edge_gateway.config"
```
Construir/publicar a imagem das duas arquiteturas: `docker buildx build --platform linux/amd64,linux/arm64
--build-arg EDGE_VERSION=0.6.0 -t <registro>/factoryconnect-edge:0.6.0 --push edge/` (todas as dependências têm wheel
manylinux para as duas arquiteturas: o build não compila nada). O container roda sem root, com sistema de arquivos
somente leitura, sem capabilities, e o estado em `/data` (volume `gateway-data`, 0700). Saúde: `HEALTH_FILE` tocado
a cada heartbeat (HEALTHCHECK da imagem).

*systemd* (sem Docker):
```bash
sudo edge/deploy/install.sh --server https://app.factoryconnect.tech --code ABCD-EFGH-JKLM --mqtt-host <broker>
journalctl -u factoryconnect-gateway -f
```
Instala em `/opt/factoryconnect` (venv só com wheels binários), configuração em `/etc/factoryconnect/gateway.env`
(0640), estado em `/var/lib/factoryconnect` (0700) e a unidade `edge/deploy/systemd/factoryconnect-gateway.service`
(usuário sem privilégios, `ProtectSystem=strict`, sem reinício em loop para código de cadastro inválido).
Rodar de novo atualiza o código e preserva configuração e estado; `--uninstall` remove serviço e código.

Depois do cadastro o `ENROLL_CODE` pode sair da configuração. Trocar o host: cadastre de novo (novo código) ou leve o
arquivo de credencial junto. Token revogado no servidor: o heartbeat passa a dar 401 (aparece nos logs e o gateway
fica `offline` na frota) — apague o arquivo de credencial e cadastre com um código novo.

Verificação manual (rede de testes): `python -m edge.src.enrollment --server https://... --code ...` cadastra sem subir
o gateway e mostra a credencial mascarada.

## Preditiva — o que o edge entrega (docs/predictive.md P-1)

As características por janela (mediana do bloco de ciclos, médias ponderadas só rodando) são calculadas **no
servidor**. O edge garante as leituras certas:

- `stroke_time` = **uma leitura por golpe**, mesmo com o mesmo valor do golpe anterior (o servidor exige leitura em
  ≥ 80 % dos ciclos do bloco). OPC UA: cada notificação é um golpe (a notificação inicial ao assinar e o re-envio de
  heartbeat não contam). Modbus: o tempo de curso é publicado quando o **contador da mesma posição** avança (polling
  mais rápido que o ciclo); sem contador, só quando o valor muda.
- Vibração/corrente RMS: quando o sensor já entrega o RMS (caso da planta virtual e de sensores IO-Link de vibração),
  a tag é um `analog`/`current` comum; `deadband_pct` por tag (ex.: 0,1 %) evita viés na média ponderada. Quando o
  sensor entrega amostras, `"aggregate": {"window_s": 1, "stat": "rms" | "peak" | "mean" | "max" | "min"}` publica um
  valor por janela (instante = fim da janela).

**Planta ao vivo**: posição nova **Cilindro B3** (Estação de Fixação, contador de 32 bits + tempo de curso) com perfil
lento — saudável por `--b3-onset-strokes` golpes (30 000), depois `--b3-drift-ms-per-kstroke` (0,5 ms / 1 000 golpes)
até 120 % do curso novo (240 ms) → falha 447 (`EQP-03`), ~110 000 golpes; e **Motor B** (Transportador de Saída:
`run`, corrente RMS, vibração RMS e pico) com corrente que depende do produto (SUP-100 12,0 A, SUP-200 12,5 A — deriva
sem falha). Sinais do Motor B são determinísticos (seed + tag + bloco de 10 s). Tags novas acrescentadas no fim de cada
máquina (endereços antigos inalterados); reprovisionar cria as posições/tags.

**Cenário F** (`edge/virtual_plant/predictive.py`):
```bash
python -m edge.virtual_plant --write-map /tmp/plant.f.json --scenario docs/scenarios/scenario_f_predictive.json  # [--scenario-cases F1]
python -m edge.src.provision --map /tmp/plant.f.json --out /tmp/plant.f.local.json --speed 500
python -m edge.virtual_plant --scenario docs/scenarios/scenario_f_predictive.json --speed 500   # [--scenario-cases F1]
PLANT_MAP_PATH=/tmp/plant.f.local.json VIRTUAL_PLANT_MAX_CLOCK_SKEW_S=off python -m edge.src.gateway
python -m edge.virtual_plant.control truth          # verdade de campo (GET 127.0.0.1:7070/truth)
```
- F1: 700 000 golpes (um a cada 4,32 s do cenário); a mediana de cada bloco de 10 000 golpes é exatamente o `x` do
  JSON; contador `b3.cycles` fecha o bloco i em `installed_at + i·12 h`; falha 447 no fim do bloco 70 e o cilindro para.
- F2: corrente do Motor B constante = `x_i` enquanto roda (02:00–22:00 local) → média ponderada só rodando = `x_i`;
  dia 47 parado.
- O mapa gerado com `--scenario` leva a **data de instalação** do Cilindro B3 / Motor B no relógio do playback (o
  provisionamento instala com ela: é o início da linha de base, P-2). Gere o mapa e rode a planta com os mesmos
  `--scenario-cases`/`--scenario-shift-days` (ou na mesma semana) e num tenant em que esses componentes ainda não
  estejam instalados (o provisionamento avisa quando a data instalada diverge).
- Relógio do dispositivo deslocado um número inteiro de **semanas** para o passado (hora local e dia da semana
  preservados). As máquinas do cenário (Estação de Fixação, Transportador) são servidas em **OPC UA** neste modo (o
  mapa gerado com `--scenario` já aponta para lá; o pulso `a1.eos` continua Modbus).
- **Servidor de QA com `SOURCE_TS_MAX_AGE_S` maior que a idade do playback** (hoje: F1 ≈ 36 dias, F1+F2 ≈ 92 dias;
  a planta imprime o valor necessário ao subir): o servidor troca timestamps com mais de 30 dias (padrão) pela hora
  de chegada (`mqtt.insane_source_ts`), o que desmontaria os blocos e as datas do cenário.
- Vazão: ≤ 25 golpes por passo de 100 ms (fila de 64 notificações por item OPC UA) → a planta e o gateway aguentam
  até ~860× (medido: 500× = ~264 mensagens MQTT/s sem buffer). **O limite prático é a ingestão do servidor**: no
  stack de dev ~80 leituras/s; acima disso o broker descarta mensagens da fila do assinante. Para o F1 pelo caminho
  completo use `--speed 150 --scenario-heartbeat-s 0` (≈ 70 leituras/s; 35 dias ≈ 5 h 40 min); só F2
  (`--scenario-cases F2`, poucas leituras) roda a 3 000×+.
- `GET /truth` (API de controle, também no playback): por caso o perfil, início da degradação e **falha real** no
  relógio do dispositivo (`failure_at`, o que o servidor recebe) e no do JSON, o estado verdadeiro agora (valor
  nominal, fração da degradação, vida remanescente real em golpes/dias) e o `expected.evaluation` do JSON — para
  conferir `GET /api/predictive/evaluation`. Na planta ao vivo, `/truth` traz `degradations` (vedações, rolamentos,
  B3, Motor B) e `failures` (todas as falhas por desgaste).

No compose (`fm_virtual_plant`), a API de controle fica no localhost do container:
`docker exec fm_virtual_plant python -m virtual_plant.control status`. Para expô-la numa rede de testes:
`VP_CONTROL_HOST=0.0.0.0` + `VP_CONTROL_TOKEN=<segredo>` (o cliente lê `VP_CONTROL_TOKEN` e `VP_CONTROL_URL`).
