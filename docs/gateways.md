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
| `pending` | nunca cadastrado (ou token revogado) |
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
| `GET/PATCH/DELETE /api/gateways/{id}` | manager+/admin/admin | detalhe, edição, remoção (as tags perdem a atribuição) |
| `POST /api/gateways/{id}/enroll-token` | admin | código de cadastro `XXXX-XXXX-XXXX`, uso único, 24 h |
| `POST /api/gateways/{id}/revoke` | admin | invalida o token do dispositivo |
| `PUT /api/gateways/{id}/tags` `{tags:[{tag_id, source?, key?, unit_id?, register_type?, data_type_plc?, word_order?, signal_role?}]}` | admin | substitui o conjunto de tags do gateway (só tags de máquinas da planta do gateway) |
| `GET /api/gateways/{id}/config-preview` | admin | o mapa que o dispositivo receberia + avisos |
| `GET /api/gateways/{id}/mqtt` | admin | usuário MQTT e a linha do `gateways.conf` do broker |

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
- Dispositivos: só `opcua` (endpoint `opc.tcp://…`, sem credencial na URL — use `${VAR}` expandido no gateway) e
  `modbus` (`host`, `timestamps` = `gateway`). Tags `mqtt`/`http` não são lidas por gateway (ficam fora, com aviso).

## Credenciais MQTT (broker de produção)

O cadastro gera o usuário `gw-<8 hex>-<4 hex>` e uma senha (entregue só ao dispositivo). O servidor guarda o hash no
formato do `mosquitto_passwd` (`$7$101$<sal>$<hash>`, PBKDF2-SHA512) e `GET /api/gateways/{id}/mqtt` devolve a linha
pronta para o `gateways.conf` do broker de produção (`infra/mosquitto/prod`, formato `<usuário> <tenant_uuid> <hash>`),
cujo ACL gerado é `topic readwrite factory/<tenant_uuid>/#` — o mesmo prefixo `topic_prefix` devolvido ao gateway.
Verificado com o Mosquitto 2.0.22: a senha do cadastro conecta e publica; senha errada → *Not authorized*.
A inclusão da linha no broker continua sendo um passo do operador (devops) — o servidor não escreve no broker.
