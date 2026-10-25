# FactoryConnect — Sprint 6: Raio-X da fábrica e UX (Fase 5)

Especificação de domínio e de produto para o Sprint 6. Itens do roadmap cobertos: Fase 5 "Mapa/layout da planta com
status ao vivo", "Navegação drill-down Fábrica → Linha → Máquina → Componente", "App responsivo/PWA para chão de
fábrica (tablet do operador)", e os itens do site "Tour/demo interativa" (tenant DEMO) e cadastro self-service
(assistente de implantação). Relatórios exportáveis e agendados estão em [`docs/reports.md`](reports.md).

Os blocos JSON `s6-*` no fim são lidos por `docs/scenarios/test_sprint6_spec.py` (estados e precedência contra o
modelo do servidor; CSV de tags contra o parser do próprio edge; layout de exemplo; passos do assistente).
Normativo: **DEVE** / **NÃO DEVE**; **PODE** = opcional.

Já existe (não refazer): `FactoryPage` → `LinePage` → `MachinePage` → `PositionDrawer` (drill-down sem mapa),
`RaioXPage` (`/dashboard`, `GET /api/overview`), `TvPage` (`/tv/lines/:lineId`), PWA do técnico em `/m`
(`web/public/manifest.webmanifest`, `id: /m/`), WebSocket `/ws/sensors/{tenant_id}` com `machine_status`,
gateways com cadastro por código (docs/gateways.md).

---

## 1. Mapa da planta (layout) com status ao vivo

### 1.1 Como o layout é definido

O layout é a **planta baixa do cliente (imagem enviada) + marcadores posicionados** sobre ela. Nada é desenhado
"de mentira": sem imagem enviada, a tela usa o **layout automático** (§1.5), identificado como tal.

Modelo (migration Alembic):

- `plant_layouts(id, tenant_id, plant_id, version, status: draft|published|archived, image_asset_id,
  image_width_px, image_height_px, scale_m_per_px (opcional), created_by, published_by, published_at)`.
  Uma planta tem **um** layout `published` por vez; editar cria um `draft` (versão + 1); publicar arquiva o anterior.
- `layout_markers(id, tenant_id, layout_id, kind: machine|line_label|area_label, machine_id (kind=machine),
  line_id (kind=line_label), label (area_label), x, y, rotation_deg, size: s|m|l)`.
- **Coordenadas normalizadas**: `x, y ∈ [0, 1]` como fração da largura/altura da imagem, origem no canto
  **superior esquerdo**, referentes ao **centro** do marcador. Assim o marcador continua no lugar em qualquer zoom
  ou tela. `rotation_deg ∈ [0, 360)`.
- Imagem: PNG, JPEG ou SVG (SVG sanitizado: sem `<script>`, `on*`, `<foreignObject>`, referências externas), até
  **10 MB**, lado maior até 8 000 px; armazenada no volume de arquivos do tenant (nunca em banco), servida com
  `Cache-Control` e URL que exige o JWT do tenant. Trocar a imagem por outra com proporção diferente (> 2 %) mantém
  os marcadores mas marca o layout "revisar posições" até alguém publicar de novo.
- Regras: cada máquina aparece **no máximo uma vez** por layout (409 ao duplicar); marcador de máquina de outra
  planta → 422; máquina sem marcador aparece numa bandeja "não posicionadas" ao lado do mapa (nunca some).
- Quem edita: `admin` e `manager`. Quem vê: todos os papéis do tenant. Toda publicação é auditada.

Editor (desktop): enviar imagem → arrastar máquinas da bandeja para o mapa → ajustar tamanho/rotação →
pré-visualizar com status ao vivo → publicar. Atalhos: desfazer/refazer, alinhar, grade de 1 %.

### 1.2 Estado mostrado em cada marcador (precedência)

O marcador mostra **um** estado visual, escolhido pela primeira regra verdadeira (bloco `s6-status-precedence`):

| # | Estado visual | Condição (fonte) | Cor + ícone + texto |
|---|---|---|---|
| 1 | `offline` | `Machine.status = unknown` ou sem leitura há > `machine_offline_timeout_s` (`last_seen_at`) | cinza tracejado, ícone de nuvem cortada, "sem dados há 12 min" |
| 2 | `failure` | `FailureRecord` aberto (sem `restored_at`) na máquina **ou** `Machine.status = error` | vermelho, chave inglesa, "falha há 8 min" + modo/código |
| 3 | `maintenance` | `Machine.status = maintenance` ou OS `in_progress` que para a máquina | azul, capacete, "manutenção — OS-123" |
| 4 | `planned_stop` | fora do tempo produtivo: pausa do turno, `planned_stop` do calendário, parada `PLN-*` aberta | cinza claro, relógio, "pausa (refeição) até 12:30" |
| 5 | `off_shift` | nenhuma instância de turno agora (D6/D9) | cinza escuro, lua, "fora do turno" |
| 6 | `stopped` | `DowntimeEvent` aberto não planejado (ou `Machine.status = stopped` no tempo produtivo) | âmbar, pausa, "parada há 6 min — sem motivo" / motivo |
| 7 | `running` | `Machine.status = running` | verde, play, cadência ao vivo (§1.9 de kpis.md) |

- `failure` vem antes de `planned_stop`/`off_shift` por causa de kpis S5-1: a falha na pausa é real e o técnico
  precisa vê-la no mapa.
- Selos adicionais (não mudam a cor): alerta preditivo aberto (severidade de predictive.md P-8), `data_coverage`
  < 0,95 no turno, peças fora do calendário (S3-7).
- **Nunca só cor**: ícone e texto curto em todo estado (daltonismo, TV a distância). Contraste AA nos dois temas.

### 1.3 Ao vivo: atualização e frescor

- Fonte: WebSocket do tenant (`machine_status`, eventos de parada, falhas) + carga inicial de `GET /api/overview`.
- Mudança de estado no servidor → marcador atualizado em **≤ 2 s** (p95) com a conexão ativa.
- WebSocket caído > 10 s → reconexão com recuo exponencial e, enquanto isso, **polling de 15 s**; caído > 30 s →
  faixa "ao vivo pausado — última atualização HH:MM:SS" e marcadores com opacidade reduzida. Nunca mostrar estado
  velho como se fosse ao vivo (regra do projeto: sem dado falso sem aviso).
- Relógio da tela = relógio do servidor (offset calculado na carga); durações ("há 6 min") contam no cliente.

### 1.4 O que o marcador mostra e para onde leva

- Tamanho `s`: ícone de estado + nome curto. `m`/`l`: nome, estado e duração, OEE do turno atual, cadência ao vivo.
- Rótulo de linha (`line_label`): nome da linha, OEE do turno atual da **máquina de saída** (§1.6 de kpis.md) e o
  estado da máquina de saída.
- Clique/toque no marcador → painel lateral com resumo (estado, parada aberta, falha aberta, alerta preditivo, OEE
  do turno, últimas 3 paradas) e botão "abrir máquina" (drill-down §2). Na TV (`/tv/plants/:plantId`), sem clique,
  com rotação automática dos destaques.
- Desempenho: 200 marcadores a 60 fps em pan/zoom num notebook comum; primeira pintura do mapa ≤ 2,5 s em 4G.

### 1.5 Layout automático (sem planta baixa)

Grade: uma faixa por linha (ordem de criação), máquinas na ordem do fluxo da linha (máquina de saída por último),
mesmas regras de estado. Faixa no topo: "Layout automático — envie a planta baixa para ver o mapa real".

---

## 2. Drill-down Fábrica → Linha → Máquina → Componente

### 2.1 Níveis e KPIs

| Nível | Rota | KPIs mostrados (sempre do mesmo período) | Filhos |
|---|---|---|---|
| Fábrica (planta) | `/factory` (mapa) e `/dashboard` (Raio-X) | OEE/A/P/Q da planta (soma das linhas, §1.6), peças boas/refugo, Pareto top-5, falhas abertas, alertas preditivos, gateways | linhas |
| Linha | `/factory/lines/:lineId` | OEE da linha pela máquina de saída, cadência, Pareto por máquina e motivo, paradas abertas, gargalo (máquina com mais ADOT) | máquinas |
| Máquina | `/factory/machines/:machineId` | OEE da máquina, linha do tempo de estados, paradas, micro-paradas, MTBF/MTTR (janela S4-8), OS abertas, falhas | posições/componentes |
| Componente (posição) | painel da posição | % de vida (§4.1), RUL (§4.2) e RUL de condição (predictive P-6), MCBF/MTBF da posição, histórico de trocas, condição (features) | — |

### 2.2 Regras

1. **Contexto do período é herdado**: `?period=current_shift|today|yesterday|last_7d|custom&start=&stop=` vai junto
   em todo link de drill-down e no botão voltar. "Hoje/ontem" = dia produtivo (C19, S3-8), nunca 00:00–00:00.
2. **Somas fecham, razões não**: minutos de perda, peças e nº de falhas do pai = soma dos filhos onde a grandeza é
   aditiva (Pareto da planta = Σ Pareto das linhas; peças da linha = peças da máquina de saída). OEE/A/P/Q do pai
   **nunca** é média dos filhos (kpis.md §1.6) — a UI não mostra "média" em lugar nenhum.
3. **Cada número é clicável para a sua causa**: barra do Pareto → lista de paradas filtrada por motivo e período;
   "falhas abertas" → Falhas filtradas; selo preditivo → alerta; "sem motivo" → Paradas "não justificadas".
4. **Breadcrumb** sempre visível (Planta › Linha › Máquina › Posição) e links profundos estáveis (podem ir no
   relatório por e-mail, reports.md §3.1).
5. **Estado vazio explicado**: KPI indefinido mostra o `reason` em português ("sem tempo planejado neste período",
   "produto sem tempo de ciclo ideal") e o atalho para corrigir (cadastrar turno, cadastrar PRI).
6. **Isolamento**: id de linha/máquina de outro tenant → 404 na API e "não encontrado" na UI (sem vazar existência).
7. Desempenho: troca de nível ≤ 1 s com cache do React Query (dados do pai reaproveitados enquanto o filho carrega,
   com esqueleto — nunca números de outro escopo).

---

## 3. PWA do operador (tablet de chão de fábrica)

### 3.1 Contexto e dispositivo

Tablet 10" Android com Chrome, fixo junto à máquina/linha (suporte de parede), uso com luva, ruído, luz forte e
Wi-Fi industrial instável. Um tablet atende **uma linha** (ou uma máquina). O operador não tem e-mail corporativo.

### 3.2 Requisitos

- **Instalável** como app separado do PWA do técnico: manifest próprio `id: /op/`, `start_url: /op`,
  `orientation: landscape`, ícones próprios. Service worker com cache só do **shell** (HTML/JS/CSS/ícones);
  dados nunca vêm do cache sem aviso.
- **Vínculo do dispositivo**: o supervisor vincula o tablet a uma linha ("cadastrar este tablet", código de 8
  caracteres válido 10 min, igual ao modelo do gateway); o dispositivo recebe um token de dispositivo com escopo
  da linha. Revogável na tela de administração.
- **Login do operador por PIN** (4–6 dígitos, por usuário, só válido em dispositivo vinculado; 5 erros → bloqueio
  de 5 min) ou crachá (leitor de código de barras/QR como teclado). Sessão termina no fim do turno ou após 15 min
  sem uso; troca de operador em 2 toques.
- **Telas** (máximo 3 toques para qualquer ação):
  1. *Minha linha agora*: estado de cada máquina (mesma precedência de §1.2), parada aberta com cronômetro,
     produção do turno vs. meta (se cadastrada — reports.md §1.1), OEE do turno.
  2. *Justificar parada*: lista das paradas sem motivo do turno; os **8 motivos mais usados** daquela máquina como
     botões grandes + "outros" (árvore de motivos); confirmar motivo sugerido `EQP-*` com um toque (D2).
  3. *Declarar parada manual* (início/fim, motivo) — sujeito às regras de S3-4 (`PLN-03` → sugestão `MNT-02`).
  4. *Trocar produto/ordem* (S3-3: a ordem nova abre no início do setup).
  5. *Chamar manutenção*: abre solicitação (`open`) ligada à parada aberta; mostra "técnico a caminho" quando a OS
     for assumida.
  6. *Refugo manual* só para máquinas sem `part_counter_scrap` (o contador do CLP é a verdade quando existe).
- **Ergonomia**: alvos de toque ≥ 48 × 48 px (≥ 64 px nas ações principais), fonte base ≥ 18 px, alto contraste,
  sem gestos finos (sem arrastar, sem duplo toque), confirmação com desfazer de 5 s em vez de diálogo.
- **Offline**: ações vão para uma fila local (IndexedDB) com chave de idempotência; aparecem como "pendente de
  envio" e são enviadas na volta da rede, em ordem. Conflito (parada já justificada por outro) → o servidor vence e
  o tablet mostra o que foi gravado. Sem rede > 30 s → faixa "sem conexão desde HH:MM" e estado das máquinas
  esmaecido (não é ao vivo).
- **Permissões**: só o papel `operator` na linha do dispositivo; nunca horas/custos de manutenção, nunca outras
  linhas, nunca configuração.
- Desempenho: primeira carga ≤ 3 s em 4G (shell em cache: ≤ 1 s); ação de justificar confirmada ≤ 1 s online.

---

## 4. Assistente de implantação (onboarding de um cliente novo)

Objetivo: um cliente novo sai do zero até **dados reais na tela** sem ajuda da equipe para os casos comuns.
O assistente é retomável (o progresso fica no servidor por tenant), cada passo valida antes de seguir, e nada é
"marcado como pronto" sem a verificação do passo 9.

| # | Passo | O que o usuário faz | Validação para avançar |
|---|---|---|---|
| 1 | Empresa e planta | nome da planta, endereço, **fuso** (lista IANA; padrão sugerido pelo estado, nunca o do navegador sem confirmar) | planta criada; fuso confirmado |
| 2 | Linhas e máquinas | formulário ou **CSV de ativos** (linha, máquina, modelo, série, máquina de saída da linha, posições/componentes com tipo, fabricante, modelo, vida nominal) | toda linha com máquina de saída (`Line.output_machine_id`); nomes únicos por planta |
| 3 | Turnos e calendário | modelo pronto (1, 2 ou 3 turnos; pausas) ou personalizado; feriados | turnos sem sobreposição (D9); ao menos 1 turno ativo ou "operação 24 h" explícita (D6) |
| 4 | Produtos e tempo de ciclo ideal | produtos, PRI por (máquina, produto), produto padrão da máquina de saída | máquina de saída com PRI (senão aviso: "OEE ficará indefinido", §1.3) |
| 5 | Conectar gateway | criar gateway → **código de cadastro** (gateways.md) → instruções de instalação (imagem do edge, variáveis) → esperar o primeiro heartbeat | gateway `online` (estado de gateways.md) |
| 6 | Importar tags | **CSV de tags** (§4.1) **ou navegação OPC UA** pelo gateway (§4.2); atribuir papel semântico, máquina e posição | o mapa do gateway é aceito pelo parser do edge (`config-preview` sem erro) |
| 7 | Motivos de parada e códigos de falha | catálogo padrão (`EQP-*`, `SET-*`, `MAT-*`, `PLN-*`, …) + `fault_code_map` por máquina | catálogo com ao menos os códigos padrão; mapa de falha opcional (sem ele `EQP-01`, D1) |
| 8 | Usuários e papéis | convidar operadores, supervisores, planejador, técnicos; áreas (linhas) | ao menos 1 `admin` além de quem implantou é recomendado (aviso, não bloqueio) |
| 9 | Verificação "pronto para produção" | checklist automático: ver bloco `s6-readiness-checks` | todos os itens obrigatórios verdes por **15 min** de dados contínuos |

### 4.1 CSV de tags

UTF-8, separador `;` (padrão Excel pt-BR) ou `,` (detectado pelo cabeçalho), uma linha por tag. Colunas:

| Coluna | Obrigatória | Valores |
|---|---|---|
| `chave` | sim | identificador único no gateway (`linha.maquina.sinal`) |
| `linha`, `maquina` | sim | nomes cadastrados no passo 2 (casamento sem acento/caixa; sem casamento → erro na linha) |
| `posicao` | não | nome da posição (para ciclos/curso/corrente de componente) |
| `papel` | sim | papel semântico (domain.md §3.1): `analog`, `cycle_counter`, `cycle_pulse`, `run_state`, `part_counter_good`, `part_counter_scrap`, `part_counter_total`, `fault_state`, `stroke_time`, `speed`, `current` |
| `fonte` | sim | id da fonte do gateway (`opcua-plc`, `modbus-plc`, …) |
| `endereco` | sim | OPC UA: NodeId `ns=..;s=..`; Modbus: número do registrador (0-based) |
| `unit_id`, `tipo_registro`, `tipo_dado`, `ordem_palavras` | Modbus | `1–247`; `holding|input|coil|discrete`; `uint16|int16|uint32|int32|float32`; `big|little` |
| `contador_max` | contadores | ex. `65535` (C5) |
| `unidade`, `escala`, `offset`, `intervalo_ms` | não | `scale`/`offset` lineares; intervalo de leitura (padrão 1 000) |

Regras: a importação é uma **prévia** (nada é gravado até confirmar) com o resultado por linha (`ok`, `erro: …`,
`já existe — será atualizada`); reimportar o mesmo arquivo é idempotente (chave = `chave` no gateway); os erros
usam as mesmas mensagens do parser do edge (`edge/src/plant_map.py`), porque é ele que vai ler o mapa.
`cycle_pulse` só em `coil`/`discrete` Modbus. Exemplo (testado): bloco `s6-tags-csv`.

### 4.2 Navegação OPC UA pelo gateway

O servidor **não** abre conexão para dentro da fábrica: o gateway só faz chamadas de saída (gateways.md). Por isso a
navegação é um **comando puxado**:

1. A UI pede `POST /api/gateways/{id}/browse {source_id, root_node_id?, max_depth ≤ 6, max_nodes ≤ 5000}` → 202
   com `request_id`.
2. A resposta do próximo heartbeat do gateway traz `commands: [{id, type: "opcua_browse", …}]`.
3. O gateway navega só o `endpoint` daquela fonte (já configurado, credenciais ficam no gateway via `${VAR}`) e envia
   `POST /api/gateways/{id}/commands/{id}/result {nodes: [{node_id, browse_name, display_name, node_class,
   data_type, access_level, parent_id}], truncated}` com o token do gateway.
4. A UI mostra a árvore; o usuário marca variáveis, atribui papel/máquina/posição e confirma (vira o mesmo conjunto
   de tags do CSV). Sugestão de papel por tipo/nome é só sugestão (ex.: Boolean com "run"/"rodando" → `run_state`).
- Comando expira em 5 min sem resposta ("gateway não respondeu — está online?"). Só `admin` do tenant.
  Nenhum valor de processo nem credencial é gravado no resultado.

---

## 5. Tenant DEMO somente-leitura ("Ver demonstração")

- **O que é**: um tenant real `demo` em produção, alimentado **continuamente** pela planta virtual
  (`edge/virtual_plant`) por um gateway próprio (credenciais MQTT próprias, ACL só no prefixo do tenant demo).
  Não há dado fabricado no banco: tudo vem do pipeline normal (MQTT → Influx/Postgres → detectores → KPIs).
- **Entrada**: botão "Ver demonstração" no site → `POST /api/public/demo-session` (sem cadastro; rate limit por IP,
  ex. 10/h) → JWT de **2 h** com papel `demo_viewer` no tenant demo → redireciona para `/dashboard`.
- **Somente leitura**: `demo_viewer` só passa em rotas `GET`; qualquer escrita → **403** com mensagem "Modo
  demonstração: alterações desativadas". A UI esconde/desabilita botões de ação com o mesmo texto (dica).
  Sem acesso a: administração, usuários (nomes/e-mails), gateways (tokens, códigos, MQTT), configurações, câmeras.
  WebSocket do tenant demo permitido (é o "ao vivo").
- **Faixa permanente** em toda tela: "Demonstração — dados de uma planta simulada (planta virtual FactoryConnect)".
  É a aplicação da regra "sem dado falso sem aviso": o dado é simulado e isso está sempre dito.
- **Roteiro da planta virtual** para a demo ser interessante a qualquer hora: falhas com código algumas vezes por
  turno, um setup por turno, micro-paradas, desgaste de um cilindro que gera alerta preditivo e RUL (cenário F) e
  reinício do desgaste quando a "troca" é executada pelo roteiro (OS gerada e concluída por um usuário de sistema
  `demo-bot`, auditado). Turnos 3 × 8 h todos os dias, no fuso `America/Sao_Paulo`.
- **Assistente de IA** na demo: habilitado com cota (ex. 10 perguntas por sessão) e as mesmas ferramentas
  somente-leitura (ai_assistant.md); sem cota disponível → mensagem clara, nunca resposta inventada.
- **Privacidade e SEO**: nenhum dado pessoal no tenant demo (usuários fictícios com nomes genéricos); páginas do
  app com `noindex`; analytics só de evento "abriu demo" respeitando LGPD.
- **Isolamento**: token demo nunca acessa outro tenant (teste de isolamento obrigatório em todas as rotas `GET`
  listadas pelo próprio app); tenant demo não aparece em listagens de superadmin para clientes.
- **Operação**: o container da planta virtual da demo tem healthcheck; parado > 5 min → alerta para devops
  (operations.md §8) e a faixa da demo passa a dizer "planta simulada temporariamente parada".

---

## 6. Critérios de aceite (Sprint 6)

1. **Mapa**: com a planta baixa de teste enviada e 6 máquinas posicionadas, a planta virtual provoca
   falha/parada/pausa e o marcador muda para o estado da tabela §1.2 em ≤ 2 s (Playwright mede); com o servidor
   do WebSocket derrubado, a faixa "ao vivo pausado" aparece em ≤ 30 s.
2. **Precedência**: teste unitário da função de estado visual cobre as 7 regras e os casos do bloco
   `s6-status-precedence` (inclusive falha durante a pausa → `failure`, kpis S5-1).
3. **Layout**: marcador duplicado → 409; máquina de outra planta → 422; layout de outro tenant → 404; SVG com
   `<script>` rejeitado; coordenadas fora de [0, 1] → 422.
4. **Drill-down**: Playwright navega Fábrica → Linha → Máquina → Posição com `period=yesterday` preservado; a
   soma dos minutos do Pareto das linhas = Pareto da planta; nenhum OEE é média (teste de API com o caso E2).
5. **PWA do operador**: Lighthouse PWA instalável; justificar uma parada em ≤ 3 toques; offline: justificar sem
   rede, religar, a justificativa chega uma vez (idempotência); operador não acessa outra linha (403).
6. **Assistente de implantação**: um tenant novo, seguindo o assistente contra a planta virtual, chega ao passo 9
   todo verde; o CSV `s6-tags-csv` importa sem erro; um CSV com `cycle_pulse` em `holding` mostra o erro do
   parser do edge na linha certa; a navegação OPC UA lista os nós do servidor OPC UA da planta virtual.
7. **DEMO**: "Ver demonstração" abre o dashboard em ≤ 5 s; toda rota de escrita responde 403 ao `demo_viewer`
   (teste gerado a partir da lista de rotas); a faixa aparece em todas as telas; nenhum e-mail de usuário é
   exposto.
8. Toda tela nova verificada no navegador nos temas claro e escuro, em 1366 × 768, tablet 1280 × 800 e celular 390 × 844.

---

## 7. Blocos de teste

```json s6-status-precedence
{"order": ["offline", "failure", "maintenance", "planned_stop", "off_shift", "stopped", "running"],
 "machine_status_map": {"unknown": "offline", "error": "failure", "maintenance": "maintenance",
                        "stopped": "stopped", "running": "running"},
 "cases": [
   {"id": "fault_in_break", "facts": {"status": "stopped", "open_failure": true, "in_break": true}, "expected": "failure"},
   {"id": "break_no_fault", "facts": {"status": "stopped", "in_break": true}, "expected": "planned_stop"},
   {"id": "offline_beats_failure", "facts": {"status": "unknown", "open_failure": true}, "expected": "offline"},
   {"id": "off_shift_stopped", "facts": {"status": "stopped", "off_shift": true}, "expected": "off_shift"},
   {"id": "open_stop", "facts": {"status": "stopped", "open_downtime": true}, "expected": "stopped"},
   {"id": "maintenance_wo", "facts": {"status": "running", "wo_in_progress_stops_machine": true}, "expected": "maintenance"},
   {"id": "running", "facts": {"status": "running"}, "expected": "running"}
 ]}
```

```json s6-layout-example
{"plant": "Planta Manaus", "image": {"width_px": 4000, "height_px": 2500},
 "markers": [
   {"kind": "line_label", "line": "Linha de Usinagem", "x": 0.18, "y": 0.12},
   {"kind": "machine", "machine": "Torno CNC", "x": 0.12, "y": 0.30, "rotation_deg": 0, "size": "m"},
   {"kind": "machine", "machine": "Centro de Usinagem", "x": 0.28, "y": 0.30, "rotation_deg": 0, "size": "m"},
   {"kind": "line_label", "line": "Linha de Montagem", "x": 0.62, "y": 0.12},
   {"kind": "machine", "machine": "Robô de Solda", "x": 0.52, "y": 0.34, "rotation_deg": 90, "size": "l"},
   {"kind": "machine", "machine": "Estação de Fixação", "x": 0.66, "y": 0.34, "rotation_deg": 0, "size": "m"},
   {"kind": "machine", "machine": "Transportador de Saída", "x": 0.80, "y": 0.34, "rotation_deg": 0, "size": "s"},
   {"kind": "area_label", "label": "Almoxarifado", "x": 0.90, "y": 0.85}
 ]}
```

```json s6-tags-csv
{"sources": [{"id": "opcua-plc", "protocol": "opcua", "endpoint": "opc.tcp://${PLC_HOST:-localhost}:4840/fm/"},
             {"id": "modbus-plc", "protocol": "modbus", "host": "10.0.0.5", "port": 502}],
 "csv": [
  "chave;linha;maquina;posicao;papel;fonte;endereco;unit_id;tipo_registro;tipo_dado;ordem_palavras;contador_max;unidade;escala;offset;intervalo_ms",
  "montagem.fixacao.ciclos_a1;Linha de Montagem;Estação de Fixação;Cilindro A1;cycle_counter;modbus-plc;10;2;holding;uint16;big;65535;;1;0;1000",
  "montagem.fixacao.curso_a1;Linha de Montagem;Estação de Fixação;Cilindro A1;stroke_time;modbus-plc;20;2;holding;float32;big;;ms;1;0;1000",
  "montagem.fixacao.pulso_b3;Linha de Montagem;Estação de Fixação;Cilindro B3;cycle_pulse;modbus-plc;5;2;discrete;;;;;;;",
  "montagem.transportador.rodando;Linha de Montagem;Transportador de Saída;;run_state;opcua-plc;\"ns=2;s=Transportador.Run\";;;;;;;;;500",
  "montagem.transportador.boas;Linha de Montagem;Transportador de Saída;;part_counter_good;opcua-plc;\"ns=2;s=Transportador.Good\";;;;;4294967295;;;;1000",
  "montagem.transportador.falha;Linha de Montagem;Transportador de Saída;;fault_state;opcua-plc;\"ns=2;s=Transportador.Fault\";;;;;;;;;500"
 ],
 "invalid_csv_row": "montagem.fixacao.pulso_errado;Linha de Montagem;Estação de Fixação;Cilindro B3;cycle_pulse;modbus-plc;6;2;holding;uint16;big;;;;;",
 "invalid_error_contains": "cycle_pulse exige register_type coil/discrete"}
```

Nota do CSV: o NodeId OPC UA contém `;` (`ns=2;s=…`), por isso esse campo vem **entre aspas** (CSV padrão,
RFC 4180) e o importador DEVE usar um leitor de CSV de verdade (aspas, aspas duplicadas, BOM do Excel), nunca
`split(';')`. O teste lê o exemplo com `csv.reader` e passa o resultado pelo parser do edge.

```json s6-readiness-checks
[
  {"id": "timezone_confirmed", "required": true, "rule": "planta com fuso IANA confirmado no passo 1"},
  {"id": "output_machine_per_line", "required": true, "rule": "toda linha com output_machine_id"},
  {"id": "calendar", "required": true, "rule": "ao menos 1 turno ativo ou operação 24 h explícita (D6)"},
  {"id": "ideal_cycle_time_output", "required": true, "rule": "máquina de saída de cada linha com PRI para o produto padrão"},
  {"id": "gateway_online", "required": true, "rule": "todo gateway da planta online (gateways.md)"},
  {"id": "run_state_or_cycles", "required": true, "rule": "toda máquina com run_state ou contador de ciclos/peças (D8)"},
  {"id": "part_counter_output", "required": true, "rule": "máquina de saída com part_counter_good ou part_counter_total"},
  {"id": "data_flowing", "required": true, "rule": "leituras de todas as tags obrigatórias nos últimos 15 min contínuos"},
  {"id": "counter_max_set", "required": false, "rule": "contadores com counter_max (rollover C5) — aviso se ausente"},
  {"id": "fault_code_map", "required": false, "rule": "máquinas com fault_state têm fault_code_map — aviso se ausente"},
  {"id": "second_admin", "required": false, "rule": "mais de um admin no tenant — recomendação"}
]
```
