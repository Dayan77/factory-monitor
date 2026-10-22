# FactoryConnect — Assistente de IA com dados reais (especificação para o Sprint 5, Fase 4)

O assistente responde perguntas de gerentes, planejadores, supervisores e técnicos **com os dados do próprio tenant**,
buscados por **ferramentas** (function calling) que chamam a API do FactoryConnect com o token do usuário. Ele não
"sabe" nada da fábrica por conta própria: se nenhuma ferramenta trouxe o número, ele não fala o número.

Regras normativas têm código **IA-n** (viram teste). Base: KPIs em [`kpis.md`](kpis.md), domínio em
[`domain.md`](domain.md), rotinas e papéis em [`maintenance_workflows.md`](maintenance_workflows.md), preditiva em
[`predictive.md`](predictive.md). O catálogo de ferramentas e os 15 exemplos estão em blocos JSON no fim deste
arquivo e são conferidos por [`scenarios/test_ai_assistant_spec.py`](scenarios/test_ai_assistant_spec.py) (ferramenta
existente → a rota existe no servidor; todas são só leitura; papéis coerentes; exemplos usam só ferramentas e
parâmetros do catálogo).

Hoje (`server/src/api/routes/ai_chat.py`) o chat manda a pergunta ao modelo **sem dados** — o modelo inventa ou
generaliza. O Sprint 5 substitui isso pelo que está abaixo.

---

## 1. Como funciona (arquitetura)

```
web /ai ──POST /api/ai/chat {messages, context}──► server: orquestrador
                                                     │ 1. monta prompt de sistema (IA-9) + ferramentas permitidas ao papel (IA-4)
                                                     │ 2. modelo pede ferramenta(s) ──► camada de ferramentas
                                                     │        └─► requisição ASGI interna à própria API com o JWT do usuário
                                                     │ 3. resultados (aparados) voltam ao modelo; até 6 chamadas por turno
                                                     │ 4. resposta → verificador de números e citações (IA-2)
                                                     ◄── {reply, citations, tool_calls, warnings, refused, refusal_reason}
```

- **IA-1 — Ferramenta = rota da API, chamada como o usuário.** A camada de ferramentas faz uma requisição **interna
  (ASGI, sem rede)** à rota listada no catálogo, com o mesmo `Authorization: Bearer <jwt do usuário>`. Assim valem
  exatamente o mesmo RBAC (`require_any`), o mesmo filtro de `tenant_id` e as mesmas regras de técnico ("só as minhas
  OS") da tela. **Proibido** chamar serviços/consultas diretamente com uma sessão de banco "de sistema" ou chamar a
  função da rota pulando as `dependencies` (isso pularia o controle de papel).
- **Provedor do modelo**: o código atual usa o SDK da OpenAI; a especificação é neutra (ferramentas em JSON Schema).
  Escolha do provedor/modelo é do dono do projeto; chave só no `.env`. `temperature ≤ 0,2`. Sem chave: a mensagem
  atual de "módulo não configurado".
- **Limites**: até 6 chamadas de ferramenta e 20 s por turno; 50 linhas por resultado (`truncated: true` +
  `total`); 20 mensagens/min por usuário; orçamento mensal de tokens por tenant (configurável; estourou → "limite
  mensal do assistente atingido"). Variável `AI_ENABLED=false` desliga o recurso por tenant.
- **Contexto da tela**: a web envia `context = {route, plant_id, line_id, machine_id, position_id, period}` da tela
  aberta; "esta linha", "esta máquina" resolvem por ele.

### Resposta da API (compatível com o `ChatResponse {reply}` atual — só acrescenta campos)

```json
{
  "reply": "OEE da **Linha de Montagem** ontem (05/10): **71,4 %** [1] …",
  "citations": [{"id": 1, "label": "OEE · Linha de Montagem · 05/10 por turno", "url": "/oee?scope=line&id=…&start=…&stop=…&group_by=shift"}],
  "tool_calls": [{"name": "get_oee", "args": {"scope": "line", "id": "…", "period": "yesterday", "group_by": "shift"}, "status": "ok", "rows": 3, "duration_ms": 41}],
  "warnings": ["Dados incompletos no turno C (cobertura 91,0 %)."],
  "refused": false,
  "refusal_reason": null
}
```

`refusal_reason ∈ {no_permission, insufficient_data, out_of_scope, write_not_allowed, ambiguous, budget_exceeded}`.

---

## 2. Regras de resposta

- **IA-2 — Todo número vem de uma ferramenta e tem citação.** Cada valor numérico (KPI, contagem, duração, data,
  % de vida, horas) da resposta DEVE estar no resultado de uma ferramenta **deste turno** e ser seguido de `[n]`, com
  `citations[n].url` = a tela que mostra aquele número **com os mesmos filtros** (template `link` do catálogo).
  - O servidor roda um **verificador** depois do modelo: extrai os números da resposta (formato pt-BR), normaliza
    (`71,4 %` ↔ `0.7143…` com 1 casa; minutos ↔ segundos; datas locais) e confere contra os resultados. Número sem
    origem → o modelo refaz uma vez com o aviso; falhou de novo → resposta **de modelo fixo** (tabela gerada a partir
    dos resultados, sem texto livre) + aviso. Exceções: números que estão na pergunta do usuário e marcadores de lista.
  - **Contas são da ferramenta, não do modelo**: diferenças, somas, comparações de período vêm de parâmetros como
    `compare_to` ou de campos calculados pelo servidor (ex.: `delta`). O modelo não soma, não tira média e — nunca —
    faz média de porcentagens (kpis.md §0).
- **IA-3 — Dado insuficiente → dizer, não adivinhar.** KPI `null` → "indefinido" + o motivo traduzido (tabela
  abaixo). `data_coverage < 0,95` → "dados incompletos (cobertura X %)". `estimated = true` → "estimado".
  `low_confidence` → "projeção com pouco histórico". MTBF/MTTF com `failures < 3` → "amostra pequena". Pergunta sem
  ferramenta que a responda (previsão de vendas, custo em R$ sem cadastro de custo, dados de outro sistema) → recusa
  com `insufficient_data`/`out_of_scope`, dizendo **o que faltaria** e qual tela chega mais perto.

  | `reason` | Frase |
  |---|---|
  | `no_planned_time` | "não havia tempo programado no período" |
  | `no_failures` | "nenhuma falha no período — X h de operação sem falha" (quando a ferramenta devolve as horas) |
  | `no_production` | "não houve produção no período" |
  | `no_nominal_life` | "o componente não tem vida nominal cadastrada" |
  | `no_recent_usage` | "sem uso nos últimos 14 dias — não dá para projetar" |
  | `insufficient_data` | "histórico insuficiente" |
  | `no_significant_trend` | "sem tendência de degradação significativa" |

- **IA-4 — Escopo do tenant e do papel.** `tenant_id` **não é parâmetro** de nenhuma ferramenta: vem do JWT. O
  modelo só **recebe** as ferramentas que o papel do usuário pode usar (coluna `roles` do catálogo; admin/superadmin
  passam como na API); se mesmo assim uma chamada voltar 403, a resposta diz "seu perfil não tem acesso a …"
  (`no_permission`). ID de outro tenant → 404 → "não encontrei". Técnico vê só as OS dele/da equipe porque a rota já
  filtra (IA-1).
- **IA-5 — Ambiguidade → perguntar.** Nome de ativo com mais de um resultado em `resolve_asset` (ex.: "o motor") →
  pergunta curta com as opções (`refusal_reason = ambiguous`, sem números). Nunca escolher sozinho.
- **IA-6 — Período explícito e no fuso da planta.** As ferramentas aceitam `period` (`today`, `yesterday`,
  `this_week`, `last_week`, `last_7_days`, `this_month`, `last_month`, `last_12_months`) **ou** `start`/`stop`
  (ISO, UTC). As palavras são resolvidas **pelo servidor** com o calendário da planta (dia produtivo, C17/C19), não
  pelo modelo. A resposta sempre diz período e escopo ("ontem, 05/10, Linha de Montagem, 3 turnos").
- **IA-7 — Português do Brasil.** Vírgula decimal, `%` com 1 casa, durações em min/h, datas `dd/mm/aaaa` no fuso da
  planta, siglas de KPI como na UI (OEE, MTBF, MCBF, MTTR, PMP). Indicador certo para o item: **MCBF** para
  cilindros/garras/válvulas (ciclos), MTBF para motores/robôs (horas) — se perguntarem "MTBF do cilindro", responder
  com MCBF e explicar em uma frase (domain.md §6).
- **IA-8 — Forma da resposta.** (1) uma ou duas frases com a resposta direta; (2) lista ou tabela curta com os
  números e as citações; (3) "Onde ver: [tela](link)". Até ~150 palavras salvo pedido. Sem recomendações inventadas:
  só as que decorrem de regra documentada (ex.: "backlog > 4 semanas = equipe insuficiente", kpis.md §3.3).
- **IA-9 — Texto dos dados é dado, não instrução.** Resultados trazem texto digitado por usuários (comentários de
  parada, descrição de OS, notas de falha). O prompt de sistema e o orquestrador tratam esses campos como dado
  (delimitados); instruções contidas neles são ignoradas. Nenhuma ferramenta envia dado para fora.
- **IA-10 — Privacidade.** Resultados enviados ao provedor do modelo não levam e-mail, telefone nem token; nome do
  técnico atribuído é permitido (necessário para "OS do João"). O assistente **não ranqueia pessoas** por desempenho
  ("técnico mais lento") — recusa com `out_of_scope`.

---

## 3. Segurança: só leitura no Sprint 5

- **IA-11 — Nenhuma ferramenta muda dados no Sprint 5.** Todas as ferramentas do catálogo são `GET`. Pedidos de ação
  ("abra uma OS", "reconheça o alerta", "mude o plano") → recusa com `write_not_allowed` + **link de ação
  pré-preenchido** para a tela que faz aquilo (ex.: `/maintenance/work-orders?new=1&position_id=…`), onde o usuário
  confirma pelo fluxo normal.
- Para quando houver ferramentas de escrita (Sprint 6+), já fica definido: o modelo só **propõe** (cartão "Ação
  proposta" com o que será feito); a execução acontece **só** depois do clique "Confirmar" do usuário, pela rota
  normal da API com o token dele, com trilha de auditoria marcando `via = ai_assistant`. Nada de apagar, nunca.
- **Auditoria**: cada conversa grava `tenant_id`, `user_id`, mensagens, chamadas de ferramenta (nome, argumentos,
  status, nº de linhas — não o conteúdo inteiro), citações, recusas. Retenção 90 dias. Visível ao admin do tenant.

---

## 4. Catálogo de ferramentas

Convenções: `period` | `start`/`stop` conforme IA-6 em todas as ferramentas com tempo; ids são UUID obtidos por
`resolve_asset` (ou pelo contexto da tela); `limit ≤ 50`. `roles = ["*"]` = qualquer usuário autenticado do
tenant. `MAINT_READ` = `maintenance_planner`, `manager`, `technician`, `production_supervisor`; `KPI_READ` =
`maintenance_planner`, `manager`, `production_supervisor` (iguais a `server/src/api/routes/maintenance.py`).
`status = existing` → a rota já existe (o teste confere); `sprint5` → rota nova do Sprint 5 (predictive.md P-12).

| Ferramenta | O que responde | Rota(s) | Papéis | Tela citada |
|---|---|---|---|---|
| `resolve_asset` | nome falado → ids (planta, linha, máquina, posição) | `GET /api/plants/tree`, `GET /api/positions/` | todos | — (só resolve) |
| `get_machine_status` | o que está rodando/parado agora, desde quando | `GET /api/overview`, `GET /api/plants/tree` | todos | `/factory`, `/factory/machines/{id}` |
| `get_oee` | OEE e A/P/Q por máquina/linha/planta, por hora/turno/dia | `GET /api/oee` | todos | `/oee?…` |
| `get_losses_pareto` | maiores perdas (Pareto) | `GET /api/losses/pareto` | todos | `/oee?…&tab=pareto` |
| `get_downtime` | paradas (abertas, sem motivo, por máquina/linha) | `GET /api/downtime/` | todos | `/stops?…` |
| `get_shift_report` | relatório de um turno | `GET /api/reports/shift` | todos | `/reports/shift?…` |
| `get_component_life` | % de vida e RUL dos componentes instalados | `GET /api/maintenance/components` | MAINT_READ | `/maintenance/components` |
| `get_maintenance_due` | planos `due_soon`/`due`/`overdue`, data e gatilho | `GET /api/maintenance-plans/status` | MAINT_READ | `/maintenance/upcoming` |
| `get_work_orders` | OS por estado, tipo, técnico, vencidas, período | `GET /api/work-orders/` | todos, com a visibilidade da rota (técnico: as dele e da equipe; operador: só as solicitações que abriu) | `/maintenance/work-orders?wo={id}` |
| `get_maintenance_kpis` | PMP, cumprimento, backlog | `GET /api/maintenance/kpis` | KPI_READ | `/maintenance/kpis` |
| `get_reliability` | MTBF/MCBF/MTTR/MTTF por posição, máquina, tipo, fabricante, modelo | `GET /api/maintenance/reliability` | KPI_READ | `/maintenance/reliability?group_by=…` |
| `search_failures` | falhas registradas (ISO 14224) por ativo, período, modo | `GET /api/failures/` | MAINT_READ | `/maintenance/failures?f={id}` |
| `get_predictive_alerts` | alertas de condição abertos, severidade, RUL | `GET /api/predictive/alerts` (S5) | MAINT_READ | `/maintenance/predictive?alert={id}` |
| `get_condition_trend` | série do sinal, linha de base, EWMA/CUSUM, RUL de condição | `GET /api/predictive/monitors`, `GET /api/predictive/features` (S5) | MAINT_READ | `/maintenance/predictive?position_id=…` |

Extensões de API pedidas ao `backend-engineer` (pequenas, nas rotas existentes): `get_oee.compare_to =
previous_period` (servidor devolve os dois períodos e o `delta`, somando componentes — S3-8); `group_by =
shift_name` (agrega todas as instâncias do mesmo turno no período, somando componentes); `search_failures.failure_mode`
e `q` (texto livre em notas) como filtros da rota de falhas; aceitar `period` em todas as rotas com tempo (ou a
camada de ferramentas traduz para `start`/`stop`). Ao `frontend-engineer`: as telas citadas DEVEM abrir com os filtros
da URL (deep link) — sem isso a citação não leva ao mesmo número.

```json ai-tools
[
  {"name": "resolve_asset", "status": "existing", "roles": ["*"],
   "description": "Encontra planta, linha, máquina ou posição pelo nome falado pelo usuário e devolve ids. Use antes de qualquer outra ferramenta quando o usuário citar um ativo pelo nome.",
   "api": [{"method": "GET", "path": "/api/plants/tree"}, {"method": "GET", "path": "/api/positions/"}],
   "params": {"query": {"type": "string"}, "kind": {"type": "string", "enum": ["plant", "line", "machine", "position", "any"]}},
   "required": ["query"], "link": null},
  {"name": "get_machine_status", "status": "existing", "roles": ["*"],
   "description": "Estado atual (rodando, parada, falha, manutenção, sem dados) das máquinas, com a parada aberta e há quanto tempo.",
   "api": [{"method": "GET", "path": "/api/overview"}, {"method": "GET", "path": "/api/plants/tree"}],
   "params": {"plant_id": {"type": "string"}, "line_id": {"type": "string"}, "machine_id": {"type": "string"}},
   "required": [], "link": "/factory/machines/{machine_id}"},
  {"name": "get_oee", "status": "existing", "roles": ["*"],
   "description": "OEE (ISO 22400) e disponibilidade, desempenho e qualidade de máquina, linha ou planta no período, opcionalmente por hora, turno ou dia.",
   "api": [{"method": "GET", "path": "/api/oee"}],
   "params": {"scope": {"type": "string", "enum": ["machine", "line", "plant"]}, "id": {"type": "string"},
              "period": {"type": "string"}, "start": {"type": "string", "format": "date-time"}, "stop": {"type": "string", "format": "date-time"},
              "group_by": {"type": "string", "enum": ["none", "hour", "shift", "day", "shift_name"]},
              "compare_to": {"type": "string", "enum": ["none", "previous_period"]}},
   "required": ["scope", "id"], "link": "/oee?scope={scope}&id={id}&start={start}&stop={stop}&group_by={group_by}"},
  {"name": "get_losses_pareto", "status": "existing", "roles": ["*"],
   "description": "Pareto das perdas de tempo (paradas por motivo, micro-paradas, velocidade, qualidade) em minutos e % do total.",
   "api": [{"method": "GET", "path": "/api/losses/pareto"}],
   "params": {"scope": {"type": "string", "enum": ["machine", "line", "plant"]}, "id": {"type": "string"},
              "period": {"type": "string"}, "start": {"type": "string"}, "stop": {"type": "string"},
              "by": {"type": "string", "enum": ["code", "category"]}},
   "required": ["scope", "id"], "link": "/oee?scope={scope}&id={id}&start={start}&stop={stop}&tab=pareto"},
  {"name": "get_downtime", "status": "existing", "roles": ["*"],
   "description": "Lista de paradas (≥ limiar) com máquina, início, duração, motivo; filtros de abertas e sem motivo.",
   "api": [{"method": "GET", "path": "/api/downtime/"}],
   "params": {"machine_id": {"type": "string"}, "line_id": {"type": "string"}, "period": {"type": "string"},
              "start": {"type": "string"}, "stop": {"type": "string"}, "open": {"type": "boolean"},
              "unjustified": {"type": "boolean"}, "limit": {"type": "integer", "maximum": 50}},
   "required": [], "link": "/stops?line_id={line_id}&machine_id={machine_id}&start={start}&stop={stop}"},
  {"name": "get_shift_report", "status": "existing", "roles": ["*"],
   "description": "Relatório de turno de uma linha: OEE, Pareto, paradas e produção por máquina.",
   "api": [{"method": "GET", "path": "/api/reports/shift"}],
   "params": {"line_id": {"type": "string"}, "productive_day": {"type": "string", "format": "date"}, "shift_id": {"type": "string"}},
   "required": ["line_id", "productive_day"], "link": "/reports/shift?line_id={line_id}&day={productive_day}&shift_id={shift_id}"},
  {"name": "get_component_life", "status": "existing", "roles": ["maintenance_planner", "manager", "technician", "production_supervisor"],
   "description": "Componentes instalados com % de vida consumida (medida determinante), RUL em dias e data projetada, e alerta de vida.",
   "api": [{"method": "GET", "path": "/api/maintenance/components"}],
   "params": {"plant_id": {"type": "string"}, "line_id": {"type": "string"}, "min_life": {"type": "number"}},
   "required": [], "link": "/maintenance/components"},
  {"name": "get_maintenance_due", "status": "existing", "roles": ["maintenance_planner", "manager", "technician", "production_supervisor"],
   "description": "Planos de manutenção com estado (ok, due_soon, due, overdue), % do intervalo, gatilho e data de vencimento (o que vier primeiro).",
   "api": [{"method": "GET", "path": "/api/maintenance-plans/status"}],
   "params": {"plant_id": {"type": "string"}, "line_id": {"type": "string"}, "machine_id": {"type": "string"},
              "state": {"type": "array", "items": {"type": "string", "enum": ["ok", "due_soon", "due", "overdue"]}}},
   "required": [], "link": "/maintenance/upcoming"},
  {"name": "get_work_orders", "status": "existing", "roles": ["*"],
   "description": "Ordens de serviço por estado, tipo, técnico ('me' = o próprio usuário), máquina/linha, vencidas e período.",
   "api": [{"method": "GET", "path": "/api/work-orders/"}],
   "params": {"state": {"type": "array", "items": {"type": "string"}}, "type": {"type": "string", "enum": ["preventive", "predictive", "corrective", "improvement"]},
              "assignee_id": {"type": "string"}, "machine_id": {"type": "string"}, "line_id": {"type": "string"},
              "plant_id": {"type": "string"}, "overdue": {"type": "boolean"}, "from": {"type": "string", "format": "date"},
              "to": {"type": "string", "format": "date"}, "completed_from": {"type": "string"}, "completed_to": {"type": "string"},
              "limit": {"type": "integer", "maximum": 50}},
   "required": [], "link": "/maintenance/work-orders?wo={id}"},
  {"name": "get_maintenance_kpis", "status": "existing", "roles": ["maintenance_planner", "manager", "production_supervisor"],
   "description": "PMP, cumprimento da programação (com exclusões e motivo) e backlog em horas e semanas, no período.",
   "api": [{"method": "GET", "path": "/api/maintenance/kpis"}],
   "params": {"plant_id": {"type": "string"}, "period": {"type": "string"}, "start": {"type": "string"}, "stop": {"type": "string"}},
   "required": [], "link": "/maintenance/kpis?start={start}&stop={stop}"},
  {"name": "get_reliability", "status": "existing", "roles": ["maintenance_planner", "manager", "production_supervisor"],
   "description": "MTBF (h), MCBF (ciclos), MTTR (min) com n, restabelecidas e incipientes; MTTF por fabricante/modelo com falhas e suspensões. Janela padrão 12 meses.",
   "api": [{"method": "GET", "path": "/api/maintenance/reliability"}],
   "params": {"group_by": {"type": "string", "enum": ["position", "machine", "component_type", "manufacturer", "manufacturer_model"]},
              "period": {"type": "string"}, "start": {"type": "string"}, "stop": {"type": "string"},
              "plant_id": {"type": "string"}, "line_id": {"type": "string"}},
   "required": [], "link": "/maintenance/reliability?group_by={group_by}"},
  {"name": "search_failures", "status": "existing", "roles": ["maintenance_planner", "manager", "technician", "production_supervisor"],
   "description": "Falhas registradas (ISO 14224: modo, mecanismo, causa, detecção, severidade) com início, restabelecimento e OS ligada.",
   "api": [{"method": "GET", "path": "/api/failures/"}],
   "params": {"machine_id": {"type": "string"}, "line_id": {"type": "string"}, "plant_id": {"type": "string"},
              "position_id": {"type": "string"}, "component_id": {"type": "string"},
              "severity": {"type": "string", "enum": ["critical", "degraded", "incipient"]}, "open": {"type": "boolean"},
              "period": {"type": "string"}, "start": {"type": "string"}, "stop": {"type": "string"},
              "failure_mode": {"type": "string"}, "q": {"type": "string"}, "limit": {"type": "integer", "maximum": 50}},
   "required": [], "link": "/maintenance/failures?f={id}"},
  {"name": "get_predictive_alerts", "status": "sprint5", "roles": ["maintenance_planner", "manager", "technician", "production_supervisor"],
   "description": "Alertas de condição (preditiva): sinal, severidade, texto explicável, RUL de condição com faixa, estado e OS ligada.",
   "api": [{"method": "GET", "path": "/api/predictive/alerts"}],
   "params": {"state": {"type": "array", "items": {"type": "string", "enum": ["open", "acknowledged", "converted", "closed"]}},
              "severity": {"type": "string", "enum": ["watch", "warning", "critical"]},
              "line_id": {"type": "string"}, "position_id": {"type": "string"}},
   "required": [], "link": "/maintenance/predictive?alert={id}"},
  {"name": "get_condition_trend", "status": "sprint5", "roles": ["maintenance_planner", "manager", "technician", "production_supervisor"],
   "description": "Série do sinal de condição de uma posição com linha de base, limites, EWMA/CUSUM e RUL de condição.",
   "api": [{"method": "GET", "path": "/api/predictive/monitors"}, {"method": "GET", "path": "/api/predictive/features"}],
   "params": {"position_id": {"type": "string"}, "feature": {"type": "string", "enum": ["stroke_time", "current", "vibration", "temperature"]},
              "period": {"type": "string"}, "start": {"type": "string"}, "stop": {"type": "string"}},
   "required": ["position_id"], "link": "/maintenance/predictive?position_id={position_id}&feature={feature}"}
]
```

---

## 5. Quinze perguntas de exemplo (chamadas esperadas e forma da resposta)

Os exemplos usam a Planta Manaus (planta virtual e cenários D/E). "Hoje" = 01/12/2026 (cenário E) quando a pergunta
é de manutenção. Viram casos do teste do orquestrador com modelo **roteirizado** (IA-12).

| # | Quem | Pergunta | Ferramentas esperadas | Forma da resposta |
|---|---|---|---|---|
| 1 | Gerente de produção | "Como foi o OEE da Linha de Montagem ontem, por turno?" | `resolve_asset(query="Linha de Montagem", kind=line)` → `get_oee(scope=line, id, period=yesterday, group_by=shift)` | OEE do dia em destaque; tabela turno × A/P/Q/OEE com [n]; aviso se `data_coverage < 0,95`; "Onde ver" OEE |
| 2 | Gerente de produção | "Onde perdemos mais tempo na semana passada na Usinagem?" | `resolve_asset` → `get_losses_pareto(scope=line, period=last_week, by=code)` | 3 maiores perdas com min, % e % acumulado [n]; micro-paradas e velocidade aparecem como perdas, não como motivos de parada |
| 3 | Supervisor de produção | "Quais paradas de hoje ainda estão sem motivo?" | `get_downtime(line_id=<contexto>, period=today, unjustified=true)` | lista máquina · início · duração [n]; "nenhuma" se vazia; link para Paradas filtrado |
| 4 | Gerente | "O que está parado agora?" | `get_machine_status(plant_id=<contexto>)` | máquinas não `running` com estado e "parada há X min" [n]; `unknown` dito como "sem dados", nunca "parada" |
| 5 | Gerente de produção | "Este mês, o turno da noite está pior que o da manhã?" | `get_oee(scope=line, id, period=this_month, group_by=shift_name)` | OEE por turno (somando componentes, S3-8) e o `delta` do servidor [n]; qual perda explica (A, P ou Q) |
| 6 | Planejador | "O que vence esta semana e o que está atrasado?" | `get_maintenance_due(state=[due_soon,due,overdue])` + `get_work_orders(overdue=true)` | duas listas: planos (posição, % e gatilho, data e gatilho da data — "82 % (partidas) · vence em 10/01 por horas") e OS vencidas [n] |
| 7 | Planejador | "Quais componentes estão perto do fim da vida?" | `get_component_life(min_life=0.8)` | tabela componente · posição · % vida (medida) · RUL dias e data [n]; `nominal_reached` primeiro |
| 8 | Gerente de manutenção | "Qual o MTBF dos cilindros Festo comparado com os SMC nos últimos 12 meses?" | `get_reliability(group_by=manufacturer, period=last_12_months)` | explica que para cilindro o indicador é **MCBF** (IA-7); MCBF, n, MTTR por fabricante [n]; `null` = "nenhuma falha" com o uso; "amostra pequena" se n < 3 |
| 9 | Gerente de manutenção | "Estamos mais preventivos ou apagando incêndio? A equipe dá conta?" | `get_maintenance_kpis(period=last_month)` | PMP vs. meta 85 %, cumprimento vs. 90 % (com excluídas e motivo), backlog em semanas e a faixa 2–4 [n] |
| 10 | Técnico | "Quais são as minhas OS de hoje?" | `get_work_orders(assignee_id=me, from=<hoje>, to=<hoje>, state=[scheduled,in_progress,on_hold])` | lista OS · posição · estado · horas estimadas/restantes [n], link para o celular `/m/work-orders` |
| 11 | Técnico | "Quando foi a última falha do cilindro?" | `resolve_asset(query="cilindro", kind=position)` → 4 resultados → **pergunta** (IA-5); depois `search_failures(position_id, limit=1)` | primeiro: "Qual cilindro: A1, A2, A3 ou A4 (Estação de Fixação)?" sem números; depois: data, duração até restabelecer, modo/mecanismo/causa, OS [n] |
| 12 | Planejador | "Algum componente está degradando? Quando vai falhar?" | `get_predictive_alerts(state=[open,acknowledged,converted])` (+ `get_condition_trend(position_id)` do mais grave) | por alerta: o texto explicável (P-8), RUL "em X dias (Y–Z)" com [n]; "nenhum alerta aberto" se vazio |
| 13 | Gerente de produção | "Qual vai ser o OEE do mês que vem?" | nenhuma (ou `get_oee(period=this_month)` como alternativa) | recusa `insufficient_data`: não há previsão de OEE; oferece o OEE do mês até agora e a tela de OEE |
| 14 | Operador | "Qual o MTTR da manutenção e quem é o técnico mais lento?" | nenhuma (`get_reliability` não é oferecida ao operador) | recusa `no_permission` para o MTTR (perfil sem acesso) e `out_of_scope` para ranquear pessoas (IA-10) |
| 15 | Planejador | "Abra uma OS para trocar o cilindro A2 amanhã." | `resolve_asset(query="cilindro A2", kind=position)` | recusa `write_not_allowed` (IA-11) com link pré-preenchido `/maintenance/work-orders?new=1&position_id=…`; nenhum dado alterado |

```json ai-examples
[
  {"id": 1, "role": "manager", "question": "Como foi o OEE da Linha de Montagem ontem, por turno?",
   "calls": [{"name": "resolve_asset", "args": {"query": "Linha de Montagem", "kind": "line"}}, {"name": "get_oee", "args": {"scope": "line", "id": "<line_id>", "period": "yesterday", "group_by": "shift"}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 2, "role": "manager", "question": "Onde perdemos mais tempo na semana passada na Usinagem?",
   "calls": [{"name": "resolve_asset", "args": {"query": "Usinagem", "kind": "line"}}, {"name": "get_losses_pareto", "args": {"scope": "line", "id": "<line_id>", "period": "last_week", "by": "code"}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 3, "role": "production_supervisor", "question": "Quais paradas de hoje ainda estão sem motivo?",
   "calls": [{"name": "get_downtime", "args": {"line_id": "<context.line_id>", "period": "today", "unjustified": true}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 4, "role": "manager", "question": "O que está parado agora?",
   "calls": [{"name": "get_machine_status", "args": {"plant_id": "<context.plant_id>"}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 5, "role": "manager", "question": "Este mês, o turno da noite está pior que o da manhã?",
   "calls": [{"name": "get_oee", "args": {"scope": "line", "id": "<context.line_id>", "period": "this_month", "group_by": "shift_name"}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 6, "role": "maintenance_planner", "question": "O que vence esta semana e o que está atrasado?",
   "calls": [{"name": "get_maintenance_due", "args": {"state": ["due_soon", "due", "overdue"]}}, {"name": "get_work_orders", "args": {"overdue": true}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 7, "role": "maintenance_planner", "question": "Quais componentes estão perto do fim da vida?",
   "calls": [{"name": "get_component_life", "args": {"min_life": 0.8}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 8, "role": "manager", "question": "Qual o MTBF dos cilindros Festo comparado com os SMC nos últimos 12 meses?",
   "calls": [{"name": "get_reliability", "args": {"group_by": "manufacturer", "period": "last_12_months"}}],
   "answer": {"must_cite": true, "refusal_reason": null, "must_mention": ["MCBF"]}},
  {"id": 9, "role": "manager", "question": "Estamos mais preventivos ou apagando incêndio? A equipe dá conta?",
   "calls": [{"name": "get_maintenance_kpis", "args": {"period": "last_month"}}],
   "answer": {"must_cite": true, "refusal_reason": null, "must_mention": ["PMP", "backlog"]}},
  {"id": 10, "role": "technician", "question": "Quais são as minhas OS de hoje?",
   "calls": [{"name": "get_work_orders", "args": {"assignee_id": "me", "from": "<today>", "to": "<today>", "state": ["scheduled", "in_progress", "on_hold"]}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 11, "role": "technician", "question": "Quando foi a última falha do cilindro?",
   "calls": [{"name": "resolve_asset", "args": {"query": "cilindro", "kind": "position"}}],
   "follow_up": {"user": "A1", "calls": [{"name": "search_failures", "args": {"position_id": "<POS-A1>", "limit": 1}}]},
   "answer": {"must_cite": false, "refusal_reason": "ambiguous"}},
  {"id": 12, "role": "maintenance_planner", "question": "Algum componente está degradando? Quando vai falhar?",
   "calls": [{"name": "get_predictive_alerts", "args": {"state": ["open", "acknowledged", "converted"]}}, {"name": "get_condition_trend", "args": {"position_id": "<most severe>"}}],
   "answer": {"must_cite": true, "refusal_reason": null}},
  {"id": 13, "role": "manager", "question": "Qual vai ser o OEE do mês que vem?",
   "calls": [],
   "answer": {"must_cite": false, "refusal_reason": "insufficient_data"}},
  {"id": 14, "role": "operator", "question": "Qual o MTTR da manutenção e quem é o técnico mais lento?",
   "calls": [],
   "answer": {"must_cite": false, "refusal_reason": "no_permission", "forbidden_tools": ["get_reliability"]}},
  {"id": 15, "role": "maintenance_planner", "question": "Abra uma OS para trocar o cilindro A2 amanhã.",
   "calls": [{"name": "resolve_asset", "args": {"query": "cilindro A2", "kind": "position"}}],
   "answer": {"must_cite": false, "refusal_reason": "write_not_allowed"}}
]
```

---

## 6. Critérios de aceite (Sprint 5)

- **IA-12 — Teste do orquestrador sem modelo real (CI).** Um "modelo roteirizado" emite exatamente as chamadas de
  `ai-examples` e uma resposta-modelo com números tirados dos resultados; o teste verifica: as ferramentas executam
  com o JWT/papel do usuário (exemplo 14: `get_reliability` nem é oferecida ao operador; forjada → 403 tratado);
  isolamento de tenant em **toda** ferramenta (id de outro tenant → "não encontrei"); verificador de números (IA-2)
  rejeita uma resposta com número inventado e aceita a correta; citações com URL e filtros; recusas com o
  `refusal_reason` esperado; nenhuma rota não-GET é chamada.
- **IA-13 — Avaliação com modelo real (manual, antes do deploy).** As 15 perguntas na planta virtual semeada com os
  cenários D e E: 15/15 com as ferramentas certas, 0 número sem origem, 100 % das respostas em português, recusas
  corretas em 13–15. Resultado registrado no PR.
- Frontend (`/ai`): mostra as citações como links clicáveis, os avisos (`warnings`) e a recusa de forma clara;
  verificado no navegador (Playwright) com o modelo roteirizado.

---

## 7. Implementação (Sprint 5) — notas do backend

Código: `server/src/services/ai/` (`catalog.py` = cópia deste catálogo, conferida por `server/tests/test_ai_catalog.py`;
`tools.py` camada de ferramentas; `periods.py` IA-6; `verifier.py` IA-2; `privacy.py` IA-10; `providers.py`;
`assistant.py` orquestrador) e `server/src/api/routes/ai_chat.py`. Testes: `server/tests/test_ai_assistant.py`
(os 15 exemplos com modelo roteirizado, IA-12).

- **Rotas**: `POST /api/ai/chat` e `POST /api/ai/chat/stream` (SSE: `start`, `tool_call`, `tool_result`,
  `verifying`, `delta`, `done`, `error`) com `{message, conversation_id?, context?}`; `GET /api/ai/conversations`,
  `GET/DELETE /api/ai/conversations/{id}` (só as do próprio usuário); `GET /api/ai/audit` (admin do tenant);
  `GET /api/ai/status`. A resposta acrescenta ao contrato do §1: `verified`, `refusal_reasons` (todas as recusas da
  pergunta, ex. 14 = `no_permission` + `out_of_scope`), `answer_source` (`model` | `template` | `refusal` | `system`),
  `conversation_id`, `message_id`, `provider`, `model`; `citations[n] = {id, label, url}`;
  `tool_calls[n] = {name, args, status, rows, duration_ms, summary}`.
- **Streaming**: o texto só é enviado depois do verificador (IA-2) — número sem origem nunca chega à tela; durante o
  turno o stream mostra o progresso (ferramentas chamadas e seus resultados).
- **Provedor**: `AI_PROVIDER = openai | anthropic` + `AI_MODEL`; sem `AI_PROVIDER`, o da chave configurada
  (`ANTHROPIC_API_KEY` preferida). Claude: SDK oficial `anthropic`, modelo padrão `claude-opus-5-5`, tool use com
  `tool_choice` automático, `output_config.effort = low`, fallback de recusa no servidor (`fallbacks = "default"`).
  Os modelos Claude atuais não aceitam parâmetros de amostragem: `temperature` (≤ 0,2) é enviado à OpenAI e aos Claude
  antigos que o aceitam; nos atuais a estabilidade vem do esforço baixo + verificador. `AI_PROVIDER=scripted`
  (só fora de produção) liga um roteiro determinístico para verificar a tela `/ai` no navegador sem chave.
- **Recusas**: o modelo marca `[[recusa:<motivo>]]` (removido do texto); o servidor também detecta sozinho 403 de
  ferramenta (`no_permission`), ranking de pessoas (`out_of_scope`), pedido de ação (`write_not_allowed`), nome
  ambíguo sem dado depois (`ambiguous`) e orçamento (`budget_exceeded`).
- **Verificador**: uma nova tentativa com a lista dos números sem origem; falhou de novo → resposta em modelo fixo
  (`answer_source = template`) montada só com os resultados, com aviso. Referências documentadas (metas de PMP,
  cumprimento, faixa do backlog, 80 %/100 % de vida, "amostra pequena" < 3) vêm no resultado (`reference`), não da
  memória do modelo.
- **Limites**: 6 chamadas e 20 s por turno, 50 linhas por resultado, 20 mensagens/min por usuário (429),
  orçamento **diário** de tokens por tenant (`AI_DAILY_TOKEN_BUDGET`, padrão 2 000 000; decisão do Sprint 5 no lugar
  do mensal do §1), `AI_ENABLED=false` / `AI_DISABLED_TENANTS` desligam. Retenção 90 dias (conversas e auditoria).
- **Extensões de API feitas**: `GET /api/oee` aceita `group_by=shift_name` e `compare_to=previous_period`
  (`previous` + `delta` calculados pelo servidor). `period` é traduzido pela camada de ferramentas; `failure_mode` e
  `q` de `search_failures` são filtros da camada de ferramentas sobre `GET /api/failures/`.
