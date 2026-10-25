# Gateways de campo — frota, cadastro e configuração remota (Sprint 5)

Implementação: `server/src/api/routes/gateways.py`, `server/src/services/gateways.py`, modelo `server/src/models/gateway.py`
(migration `e2f3a4b5c6d7`). Cliente do edge: `edge/src/remote_config.py` (contrato abaixo, verificado com o próprio
parser do edge em `server/tests/test_gateways_api.py`).

## Modelo

`gateways(tenant_id, plant_id, name, token_hash, token_prefix, enroll_code_hash, enroll_expires_at, last_seen_at,
version, status, last_heartbeat, heartbeat_s, pulse_poll_ms, devices, config_version, config_hash, mqtt_username,
mqtt_password_hash)`. Tags ganham `gateway_id` (o gateway que as lê) e `edge` (campos de borda do mapa v1: `source`,
`key`, `unit_id`, `register_type`, `data_type_plc`, `word_order`, `signal_role`). Token, código de cadastro e senha MQTT
só existem em claro na resposta que os entrega (uma vez); no banco ficam hashes.

## Estado

| Estado | Regra |
|---|---|
| `pending` | nunca cadastrado (ou revogado: `mqtt_revoked = true`) |
| `offline` | sem contato (config ou heartbeat) há mais de `max(3 × heartbeat_s, GATEWAY_OFFLINE_AFTER_S = 90 s)` |
| `degraded` | último heartbeat com `errors` ou `buffer_depth > GATEWAY_DEGRADED_BUFFER` (1000) |
| `online` | caso contrário |

Aparece em `GET /api/gateways/` e no Raio-X (`GET /api/overview` → `gateways: [{id, name, status, version,
last_seen_at, config_version}]`).

## API

Administração (JWT; tudo filtrado por `tenant_id`, gateway de outro tenant → 404):

| Rota | Papel | Uso |
|---|---|---|
| `GET /api/gateways/?plant_id=` | manager+ | frota com estado efetivo, versão, nº de tags |
| `POST /api/gateways/` `{name, plant_id, devices[], heartbeat_s, pulse_poll_ms}` | admin | cria (`pending`) |
| `GET/PATCH/DELETE /api/gateways/{id}` | manager+/admin/admin | detalhe, edição, remoção (as tags perdem a atribuição); gateway cadastrado → **409**: revogue antes |
| `POST /api/gateways/{id}/enroll-token` | admin | código de cadastro `XXXX-XXXX-XXXX`, uso único, 24 h |
| `POST /api/gateways/{id}/revoke` | admin | invalida o token **e a credencial MQTT** (`mqtt_password_hash` apagado); o servidor passa a **descartar as leituras das tags do gateway** (log `mqtt.rejected_revoked_gateway`). A resposta traz `mqtt_action`: remover a linha do usuário do `gateways.conf` e recarregar o broker (`make mqtt-gateway-remove GATEWAY=<usuário> && make mqtt-reload`) — até isso o broker ainda aceita a conexão. Um novo cadastro reativa |
| `PUT /api/gateways/{id}/tags` `{tags:[{tag_id, source?, key?, unit_id?, register_type?, data_type_plc?, word_order?, signal_role?}]}` | admin | substitui o conjunto de tags do gateway (só tags de máquinas da planta do gateway) |
| `GET /api/gateways/{id}/config-preview` | admin | o mapa que o dispositivo receberia + avisos |
| `GET /api/gateways/{id}/mqtt` | admin | usuário MQTT e a linha do `gateways.conf` do broker (revogado: `revoked: true`, sem linha, com a ação a fazer no broker) |

Dispositivo (sem JWT):

| Rota | Uso |
|---|---|
| `POST /api/gateways/enroll` `{code}` | troca o código por `{gateway_id, tenant_id, token, config_url, heartbeat_url, mqtt:{username, password, topic_prefix}}`; 10 códigos errados por IP em 10 min → 429 |
| `GET /api/gateways/{id}/config` + `Authorization: Gateway <token>` | mapa v1; `ETag` / `If-None-Match` → 304 |
| `POST /api/gateways/{id}/heartbeat` + `Authorization: Gateway <token>` `{version, uptime_s, buffer_depth, errors[], config_version}` | → `{status, config_version, config_changed}` (o edge recarrega a config quando `config_changed`) |

## Contrato da configuração (= `edge/src/remote_config.py`)

```json
{"gateway_id": "<uuid do gateway>", "revision": "7", "config_version": 7,
 "plant_map": {"version": 1, "gateway_id": "<uuid do gateway>", "tenant_id": "<uuid>", "heartbeat_s": 10, "pulse_poll_ms": 20,
   "sources": [{"id": "opcua-plc", "protocol": "opcua", "endpoint": "opc.tcp://${PLC_HOST:-localhost}:4840/fm/"},
               {"id": "modbus-plc", "protocol": "modbus", "host": "10.0.0.5", "port": 502}],
   "tags": [{"key": "linha_de_montagem.estacao_de_fixacao.ciclos_a1", "tag_id": "<uuid>", "source": "modbus-plc",
             "semantic_role": "cycle_counter", "unit": "", "address": 10, "counter_max": 65535, "unit_id": 2,
             "register_type": "holding", "data_type_plc": "uint16", "word_order": "big", "scale": 1.0, "offset": 0.0,
             "poll_interval_ms": 1000, "name": "ciclos A1", "machine_id": "<uuid>", "position_id": "<uuid>"}]}}
```

- O edge roda com `GATEWAY_ID = <uuid do gateway>` (devolvido pelo cadastro), `GATEWAY_TOKEN = <token>`,
  `GATEWAY_CONFIG_URL = https://app.factoryconnect.tech`.
- `config_version` sobe sempre que o mapa efetivo muda (hash do conteúdo: tags, posições, dispositivos), seja quem for
  que mudou; o `ETag` é `"<config_version>-<hash>"`.
- Endereço Modbus: o `Tag.address` pode vir no formato gravado pelo provisionamento/seed (`unit=2;hr=10:uint16`,
  `unit=1;di=1` — `hr|ir|di|co` → `holding|input|discrete|coil`, tipo opcional) ou como registrador inteiro (`10`);
  o mapa sempre leva `address` inteiro + `unit_id`/`register_type`/`data_type_plc`. Campos informados na atribuição
  (`tags.edge`) têm precedência sobre os do endereço; só eles ficam gravados em `tags.edge`.
- Dispositivos: só `opcua` (endpoint `opc.tcp://…`, sem credencial na URL — use `${VAR}` expandido no gateway) e
  `modbus` (`host`, `timestamps` = `gateway`). Tags `mqtt`/`http` não são lidas por gateway (ficam fora, com aviso).

## Credenciais MQTT (broker de produção)

O cadastro gera o usuário `gw-<8 hex>-<4 hex>` e uma senha (entregue só ao dispositivo). O servidor guarda o hash no
formato do `mosquitto_passwd` (`$7$101$<sal>$<hash>`, PBKDF2-SHA512) e `GET /api/gateways/{id}/mqtt` devolve a linha
pronta para o `gateways.conf` do broker de produção (`infra/mosquitto/prod`, formato `<usuário> <tenant_uuid> <hash>`),
cujo ACL gerado é `topic readwrite factory/<tenant_uuid>/#` — o mesmo prefixo `topic_prefix` devolvido ao gateway.
Verificado com o Mosquitto 2.0.22: a senha do cadastro conecta e publica; senha errada → *Not authorized*.
A inclusão da linha no broker continua sendo um passo do operador (devops) — o servidor não escreve no broker. Pelo
mesmo motivo, ao **revogar** a remoção da linha também é manual (a resposta de `revoke` e a tela dizem qual); enquanto
isso o servidor descarta as leituras das tags do gateway revogado. Limitação: depois de **remover** o gateway as tags
perdem a atribuição e esse filtro deixa de valer — por isso a remoção exige revogar antes.

## Store-and-forward sob carga (Sprint 5b)

Leitura → fila em memória limitada (`OUTBOX_MAX`, 20 000; cheia, o driver espera — contrapressão, memória
limitada) → uma tarefa de envio:

- broker no ar e buffer vazio: publica direto, `PUBLISH_WINDOW` (200) publicações QoS 1 em voo (em ordem no fio);
- broker fora: grava no SQLite em lote (`SPILL_BATCH` = 1000 linhas por transação, no máximo `SPILL_MAX_AGE_S` = 1 s
  só em memória), WAL + `synchronous=NORMAL`;
- broker de volta: esvazia **primeiro** o buffer (do mais antigo, em janelas); as leituras novas esperam na memória
  e entram atrás do buffer — ordem FIFO por tag mantida, e o esvaziamento é mais rápido que a chegada.
- parada (SIGTERM): os drivers entregam o que já receberam, a memória vai para o SQLite, e a parada tem limite de
  30 s.

OPC UA: fila no servidor por item `OPCUA_QUEUE_SIZE` (4096 — antes 64, que estourava após ~0,55 s de pausa a 116
cursos/s) e fila local `OPCUA_MAX_PENDING` (50 000) servida por uma única tarefa, em ordem; cheia, a leitura é
descartada e contada (`opcua.readings_dropped` no log, com o total). `LOG_LEVEL` (padrão `info`; `debug` registra
cada leitura enviada).

Medido (`aiomqtt` real → proxy TCP → Mosquitto, 4 tags): a 500 leituras/s, queda de 45 s (24,7 mil linhas no
buffer) esvaziada em ~20 s com as leituras novas chegando; a 1500/s, queda de 30 s (52 mil linhas) esvaziada em
~55 s; nenhuma leitura perdida, ordem por tag mantida, memória ≤ 90 MB (antes: 4,4 GB e nenhum `buffer.flushed`
em 54 min a ~230/s).
