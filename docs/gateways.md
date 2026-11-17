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
| `POST /api/gateways/{id}/revoke` | admin | invalida o token **e a credencial MQTT** (`mqtt_password_hash` apagado); o servidor passa a **descartar as leituras das tags do gateway** (log `mqtt.rejected_revoked_gateway`). A resposta traz `mqtt_action`: com a sincronização ligada (`mqtt_managed = true`) diz que a credencial saiu do broker sozinha (≤ ~10 s); sem ela, remover a linha do usuário do `gateways.conf` e recarregar o broker (`make mqtt-gateway-remove GATEWAY=<usuário> && make mqtt-reload`). Um novo cadastro reativa |
| `PUT /api/gateways/{id}/tags` `{tags:[{tag_id, source?, key?, unit_id?, register_type?, data_type_plc?, word_order?, signal_role?}]}` | admin | substitui o conjunto de tags do gateway (só tags de máquinas da planta do gateway) |
| `GET /api/gateways/{id}/config-preview` | admin | o mapa que o dispositivo receberia + avisos |
| `GET /api/gateways/{id}/mqtt` | admin | `{username, topic_prefix, acl, gateways_conf_line, revoked, managed}` (+ `action` quando revogado). `managed: true` = credencial sincronizada automaticamente com o broker (a linha manual não é necessária) |

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
- Dispositivos: `opcua` (endpoint `opc.tcp://…`, sem credencial na URL — use `${VAR}` expandido no gateway),
  `modbus` (`host`, `timestamps` = `gateway`) e `ueapd` (abaixo). Nenhum outro protocolo é emitido (o edge recusa o
  mapa inteiro diante de um protocolo desconhecido). Tags `mqtt` não são lidas por gateway; tags `http` só com um
  dispositivo `ueapd` no gateway — sem ele ficam fora do mapa, com aviso.

### Fonte `ueapd` (box ueaPowerDriver)

```json
{"id": "ueapd-m1", "protocol": "ueapd", "base_url": "http://192.168.1.15:8003", "line": "hipot",
 "poll_interval_s": 2, "timeout_s": 5, "timestamps": "gateway", "max_clock_skew_s": 2, "backfill": "none",
 "default_fault_code": 301, "fault_codes": {"ERRO_TRANSFERENCIA": 101}}
```

| Campo | Regra (servidor = `edge/src/plant_map.py`) |
|---|---|
| `id`, `protocol` | obrigatórios; `protocol = "ueapd"` |
| `base_url` | obrigatório: `http(s)://host[:porta]`, `/` final opcional, sem usuário/senha, sem caminho/consulta; `${VAR}` / `${VAR:-padrão}` permitido (o padrão é validado; `${VAR}` sozinho é expandido no gateway) |
| `line` | texto opcional (`hipot`, `ats2`): linha esperada do box |
| `poll_interval_s` / `timeout_s` | 0,2–300 (padrão 2) / 0,5–120 (padrão 5) |
| `timestamps` | `gateway` (padrão) ou `source` (instante gravado pelo box) |
| `max_clock_skew_s` | número > 0 ou `off` (padrão 2) |
| `backfill` | `none` (padrão), `latest_day`, `all` |
| `default_fault_code` / `fault_codes` | inteiro > 0 (padrão 301) / `{tag de falha do CLP: inteiro > 0}` |

Tag com `protocol = http` atribuída a um dispositivo `ueapd`: o mapa leva `source = <id do dispositivo>` e
`address = <seletor>` (texto), sem campos Modbus (`unit_id`, `register_type`, ... → 422). Seletor e papel precisam casar:

| Seletor | `semantic_role` |
|---|---|
| `state:run` / `state:fault` | `run_state` / `fault_state` |
| `state:cadence` | `analog` ou `speed` |
| `passes:good` / `passes:scrap` / `passes:total` | `part_counter_good` / `_scrap` / `_total` |
| `cycles:<estágio>[:P<n>]` | `cycle_counter` (estágio `[A-Za-z0-9_]+`, posto P1–P99) |
| `measure:<estágio>:<chave>[.<subchave>]` | `analog`, `current` ou `speed` |

O servidor replica a regra do edge em `server/src/services/onboarding/edge_rules.py` (`parse_ueapd_selector`); o
teste `server/tests/test_ueapd_rules.py` roda o mesmo vetor de seletores/papéis no servidor e no edge e exige o mesmo
resultado e a mesma mensagem. O CSV de tags (Implantação) aceita linhas com `fonte` = dispositivo ueapd. Na
atribuição (`PUT /tags`) a `key` já gravada na tag (modelo de linha, CSV) é mantida quando não vem outra: o edge
guarda o estado dos contadores por id do dispositivo + `key`.

## Credenciais MQTT (broker de produção)

O cadastro gera o usuário `gw-<8 hex>-<4 hex>` e uma senha (entregue só ao dispositivo). O servidor guarda o hash no
formato do `mosquitto_passwd` (`$7$101$<sal>$<hash>`, PBKDF2-SHA512) e `GET /api/gateways/{id}/mqtt` devolve a linha
pronta para o `gateways.conf` do broker de produção (`infra/mosquitto/prod`, formato `<usuário> <tenant_uuid> <hash>`),
cujo ACL gerado é `topic readwrite factory/<tenant_uuid>/#` — o mesmo prefixo `topic_prefix` devolvido ao gateway.
Verificado com o Mosquitto 2.0.22: a senha do cadastro conecta e publica; senha errada → *Not authorized*.
### Sincronização automática com o broker (arquivo do app)

Com `MQTT_APP_GATEWAYS_FILE` definido (produção: `/app/data/mqtt/gateways.app.conf`, volume `mqtt-app` montado
somente leitura no broker), o servidor escreve sozinho as credenciais dos gateways
(`server/src/services/mqtt_app_file.py`):

- formato do `gateways.conf`: cabeçalho `# GERADO pelo fm_server (MQTT_APP_GATEWAYS_FILE) — não edite; ...` e uma
  linha `<mqtt_username> <tenant_id> <mqtt_password_hash>` por gateway, ordenadas por usuário. Entram todos os
  gateways (todos os tenants — é um arquivo do broker; cada linha vira `topic readwrite factory/<tenant_id>/#`, o
  isolamento é pelo ACL) com usuário e hash não nulos **de tenants ativos** (tenant suspenso sai do arquivo);
- linhas que o broker recusaria (usuário fora de `^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$`, tenant não UUID, hash fora de
  `$6$`/`$7$`, usuário repetido ou reservado) não são escritas (aviso no log);
- escrita atômica (temporário no mesmo diretório + `os.replace`, modo 0644) e só quando o conteúdo muda;
- quando: depois do commit do cadastro, da revogação e da remoção de gateway, da suspensão/reativação/exclusão de
  tenant, na partida do servidor e a cada `MQTT_APP_SYNC_S` (padrão 60 s). Falha de escrita = log
  `mqtt_app_file.write_failed`, nunca derruba a requisição (a sincronização periódica tenta de novo);
- o broker confere o arquivo a cada ~5 s e recarrega: o gateway cadastrado conecta em segundos; o **revogado** tem
  novas conexões recusadas **e a sessão aberta derrubada** em ≤ ~10 s, sem comando no broker
  (`mqtt_action` e `managed` dizem isso à tela).

Vazio (dev, testes) = desligado: a inclusão/remoção da linha no `gateways.conf` volta a ser passo do operador
(`make mqtt-gateway-add/remove` + `make mqtt-reload`), e a resposta de `revoke` diz qual linha remover. Em qualquer
caso o servidor descarta as leituras das tags do gateway revogado. Limitação: depois de **remover** o gateway as tags
perdem a atribuição e esse filtro deixa de valer — por isso a remoção exige revogar antes.

## Modelos de linha (Implantação, passo 2)

`GET /api/onboarding/line-templates` (admin) →
`[{key, name, description, line_name, machines: [nomes], devices: [{id, label, default_base_url}], tag_count}]`.
Hoje: `ueapd-led-driver` — "Linha ueaPowerDriver (M1 Hipot + M2 ATS2)", dados em
`server/src/asset_templates/ueapd_led_driver.json`, derivado de `edge/config/ueapd.example.json` (teste de paridade em
`server/tests/test_line_templates.py`: máquinas, posições, tags, seletores, mapas de falha e fontes).

`POST /api/onboarding/line-templates/{key}/apply` (admin)
`{plant_id, line_name?, gateway_id?, devices?: {"ueapd-m1": {"base_url": "..."}, ...}}` →
`{template, line_id, line_name, line_created, machines: [{id, name, created}], tags_created, tags_updated,
tags_unchanged, gateway: {id, devices, tags_assigned, config_version} | null, warnings: []}`.

- Idempotente, casa por nome como o `edge/src/provision.py`: linha na planta (sem acento/caixa), máquina na linha,
  posição na máquina, tag na máquina. Aplicar de novo não duplica; o que for estrutural volta ao modelo (papel,
  seletor, unidade e posição das tags; contador de cada posição; máquina de saída da linha; códigos do
  `fault_code_map` do modelo); modelo, série e tempo de ciclo ideal da máquina só são preenchidos se vazios.
- Tags: `protocol = http`, `address` = seletor, `tags.edge.key` = chave do exemplo do edge (`ledd.m1.parts_good`, ...),
  estável entre aplicações.
- Com `gateway_id` (mesmo tenant **e** mesma planta): os dispositivos `ueapd-m1`/`ueapd-m2` são mesclados por id em
  `gateway.devices` (parâmetros do modelo; `base_url` do corpo, senão a já gravada, senão a padrão; id já usado por
  outro protocolo → 422) e as 18 tags são atribuídas ao gateway (`source` = id do dispositivo); `config_version` sobe
  se o mapa mudou (mesmo hash de `PUT /tags`).
- Erros: planta/gateway de outro tenant → 404; gateway de outra planta, URL inválida, dispositivo fora do modelo →
  422; máquina do modelo já existente em outra linha da planta → 409; estouro de `tenant.max_machines` → 422
  ("Limite de máquinas do plano ..."). Erro = nada gravado.

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
