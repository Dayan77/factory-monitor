# FactoryConnect — Rotinas de manutenção (especificação para o Sprint 4)

Como três pessoas usam o sistema **todos os dias**: o **planejador de manutenção** (PCM), o **técnico de
manutenção** (no celular) e o **supervisor de produção**. Para cada momento do dia: o que a pessoa quer saber, em
que tela, o que ela informa e que notificação recebe ou dispara. O Sprint 4 implementa exatamente isto.

Fontes normativas: regras em [`domain.md` §5](domain.md#5-manutenção) e
[Esclarecimentos de manutenção (Sprint 4)](domain.md#esclarecimentos-de-manutenção-sprint-4) (S4-1 … S4-12);
fórmulas em [`kpis.md` §2–§4](kpis.md#2-confiabilidade); números de exemplo do
[cenário de ouro E](scenarios/README.md#cenário-e--manutenção-sprint-4) (Planta Manaus, hoje = **ter 01/12/2026**).
Tudo é por tenant: listas, notificações e KPIs só mostram dados do tenant do usuário.

---

## 1. Pessoas, papéis e permissões

| Pessoa | Papel no sistema (proposta Sprint 4) | Pode | Não pode |
|---|---|---|---|
| Planejador (PCM) | `maintenance_planner` | criar/editar planos; aprovar, programar, atribuir, reprogramar e cancelar OS (com motivo); ver KPIs de gestão | apontar horas em nome do técnico |
| Técnico | `technician` | ver as OS atribuídas a ele e as da sua equipe; iniciar, pausar, concluir; apontar horas e restante; registrar troca de componente e dados de falha; abrir OS corretiva emergencial | aprovar/programar/cancelar OS; editar planos |
| Supervisor de produção | `production_supervisor` | abrir solicitação de OS (`open`) a partir de uma parada; liberar a máquina (confirmar restabelecimento); fechar (`completed → closed`) a OS da sua área; negociar janelas de preventiva (paradas `PLN-03` no calendário) | programar OS; mudar planos |
| Gerente de manutenção | `manager` (existente) | tudo do planejador + metas (PMP, cumprimento, capacidade) | — |
| Operador | `operator` (existente) | justificar paradas (Sprint 3); abrir solicitação simples ("defeito encontrado") | ver custos/horas da manutenção |

Os papéis novos somam-se a `operator`/`manager`/`admin` existentes (um usuário pode ter mais de um). Toda ação
de estado de OS grava quem e quando (trilha de auditoria) e é validada pela máquina de estados (S4-5): transição
inválida → 422 com as transições permitidas, e a UI só oferece botões válidos para o estado atual.

---

## 2. Planejador de manutenção (desktop) — o dia

### 07:30 — "O que vence e o que está atrasado?" → tela **Próximas manutenções**

- **Vê**: lista de planos ordenada por `days_to_due` com estado (`overdue` vermelho, `due` laranja, `due_soon`
  amarelo), `consumed` e o gatilho que o determinou, **data de vencimento** e o gatilho que vence primeiro
  (S4-3), próximo uso absoluto ("vence em 45 800 h no horímetro"), e a OS do plano (rascunho/programada/…).
  Filtros: planta, linha, máquina, estado, período (esta semana, 30 dias).
- **Exemplo (E)**: PL6 filtro de ar `overdue` (371 %); PL4 óleo do redutor `due` 103 % com OS-110 programada para
  28/11 e **vencida**; PL2 vedações A2 vence 12/12 **por calendário** (antes dos ciclos); PL3 rolamentos 82 % por
  partidas, mas vence 10/01/2027 por horas.
- **Ação**: abrir o rascunho gerado automaticamente (S4-4) → revisar checklist/horas → **Aprovar** (`draft → open →
  approved` em um clique "aprovar rascunho") ou **Descartar** (cancelar com motivo; se o plano continua ≥ 80 %, o
  sistema cria outro rascunho — o planejador não "perde" um vencimento por engano).
- **Notificações recebidas desde ontem** (sino + e-mail resumo das 07:00):
  - plano cruzou 80 % (rascunho criado), 100 % (`due`) ou a tolerância (`overdue`);
  - componente cruzou 80 % / 100 % da vida nominal (S4-12);
  - OS vencida (`scheduled_for < hoje` e não concluída);
  - OS pausada `on_hold` (motivo: peça, liberação da produção);
  - falha nova registrada (corretiva emergencial aberta durante a noite).

### 08:00 — "A equipe dá conta?" → tela **Backlog e programação**

- **Vê**: backlog em horas restantes e semanas (S4-7) — E: **18,0 h / 40 h = 0,45 semana** em 5 OS, 3 vencidas
  (13,5 h); faixa de referência 2–4 semanas; rascunhos e pedidos ainda não aprovados aparecem à parte.
- **Ação — programar a semana seguinte** (rotina de quinta/sexta): arrastar OS `approved` para dias da semana e
  técnicos; o quadro mostra a capacidade do dia (técnicos × horas − ausências) e as **janelas de parada** acordadas
  com a produção (paradas `PLN-03` do calendário). Programar = `approved → scheduled` com `scheduled_for` e
  `assignee_id`. Na **segunda 00:00** a semana congela (S4-6): o que for programado depois entra como "encaixe"
  e não conta no cumprimento — a UI marca o encaixe com um selo.
- **Ação — OS que precisa parar a máquina**: ao programar, o sistema propõe criar a parada planejada `PLN-03` no
  calendário de produção daquela máquina/turno (domain.md §4.1); sem ela, a parada vira `MNT-02` (não planejada)
  no OEE (kpis.md S3-4). O supervisor de produção é notificado e pode aceitar ou pedir outra janela.

### Durante o dia — acompanhar a execução → tela **Ordens de serviço** (quadro por estado)

- Colunas `aprovadas · programadas · em execução · em espera · concluídas (aguardando fechamento)`; cartão com
  máquina/posição, técnico, horas apontadas/estimadas, atraso.
- **Peça chegou** para uma OS `on_hold`: reprograma (`on_hold → scheduled`) ou devolve ao técnico
  (`on_hold → in_progress`).
- **Conflito com a produção** (a linha não pode parar): cancela com `production_conflict` (conta como não feita
  no cumprimento) ou reprograma; cancelar com `superseded` só quando outra OS substitui esta (sai do denominador).

### 16:30 — revisar o que foi concluído → tela **OS concluídas hoje**

- Conferir horas apontadas, peças trocadas e os dados ISO 14224 das falhas (modo, mecanismo, causa) que o técnico
  deixou como "6.4 desconhecido / 5.5 desconhecida" — completar após análise.
- Conferir que a troca de componente zerou o plano certo (ex.: E, OS-103 zerou PL1, não PL5/PL6 — S4-2).

### Semanal (segunda) e mensal → tela **Gestão da manutenção**

- PMP (E, novembro: **68,4 %**), cumprimento da programação (**50,0 %**, com a lista de excluídas e o motivo),
  backlog em semanas, OS vencidas, Ao das máquinas críticas. Cada número abre a lista de OS que o formou.
- Mensal: tela **Confiabilidade** — maus atores por MTBF/MCBF/MTTR por posição, tipo e fabricante na janela de
  12 meses (E: Cilindro A1 MCBF 2 000 000 ciclos, Festo 2 820 000 × SMC 3 540 000) e MTTF por modelo com
  `failures`/`suspensions` ao lado (S4-8 … S4-11). Daqui saem OS de **melhoria** (eliminar causa raiz).

### Cadastro (eventual) → tela **Planos de manutenção**

- Criar plano em uma posição (ou máquina): nome, checklist, horas estimadas, tipo (`preventive`/`predictive`),
  um ou mais gatilhos (ciclos/horas/partidas/dias), `reset_on_replacement`, data/uso da última execução
  (para planos que já existiam no papel). A tela mostra na hora o `consumed` e a data projetada com os dados reais.

---

## 3. Técnico de manutenção (celular) — o dia

Tela pensada para uma mão, luva e chão de fábrica: botões grandes, alto contraste, poucos campos obrigatórios,
leitura de QR code da posição/componente (etiqueta na máquina com o `id` da posição). Aplicação web responsiva
(PWA) no mesmo app; login persistente no aparelho da equipe.

### Início do turno → **Minhas OS de hoje**

- **Vê**: OS atribuídas a ele para hoje e vencidas, em ordem de prioridade (emergencial > vencida > hoje), cada uma
  com máquina, posição, tipo, horas estimadas, se precisa parar a máquina e se a janela foi confirmada pela produção.
- **Notificação** (push): OS nova atribuída; OS reprogramada; **chamado emergencial** na área dele.

### Executar uma preventiva (ex.: E, OS-114 vedações do Cilindro A2 em 03/12)

1. **Iniciar** (`scheduled → in_progress`): ler o QR da posição confere que está na máquina certa. Se a OS para a
   máquina, o app mostra se a produção liberou.
2. **Checklist** do plano item a item (ok / não ok / foto / medida). Item "não ok" → botão "abrir corretiva
   programada" (vira `open` para o planejador aprovar) ou "registrar falha encontrada" (falha sem parada,
   `detection_method = periodic_maintenance`, conta no MTBF e não no MTTR — S4-10).
3. **Pausar** (`in_progress → on_hold`) com motivo (aguardando peça / liberação / outro) e **horas restantes**
   estimadas (alimentam o backlog, S4-7). O tempo em espera não conta como horas trabalhadas.
4. **Trocar componente** (se houver): ler o QR/serial do componente que sai (o app mostra o atual da posição) →
   motivo da remoção (`failure`, `preventive_replacement`, `upgrade`, `other`) → ler/digitar fabricante, modelo e
   série do novo (lista do almoxarifado/cadastro). Efeito ao concluir: componente antigo removido com a idade na
   remoção, novo instalado com 0, planos da posição com `reset_on_replacement` zerados (domain.md §5.4, S4-2).
5. **Concluir** (`in_progress → completed`): horas trabalhadas (pré-preenchidas pelo tempo em execução, editáveis),
   observação, fotos. O plano da OS zera na conclusão; o sistema mostra a próxima data/uso.

### Chamado emergencial (falha que parou a produção)

- **Notificação** (push + som) quando uma parada `EQP-*` abre na área dele ou o supervisor chama: "Robô de Solda —
  falha 251 há 3 min". Um toque abre a parada com o `FailureRecord` rascunho já criado pelo motor (Decisão D3 do
  Sprint 3).
- **Assumir**: cria/assume a OS corretiva emergencial e vai direto para `in_progress` (único atalho
  `open → in_progress`, S4-5).
- **Ao terminar o reparo**: informa posição/componente que falhou, modo de falha ISO 14224 (lista curta por tipo de
  componente: cilindro → `ELP`, `INL`, `PDE`, `BRD`…), severidade (`critical`/`degraded`) e, se souber, mecanismo e
  causa (podem ficar `6.4`/`5.5` para o planejador completar). `restored_at` **não** é digitado: é o fim da parada
  medido pela máquina (volta a `running`); se a máquina não tem sinal, o supervisor confirma a liberação.
- Troca de componente e conclusão como na preventiva.

### Fim do turno → **Apontamentos de hoje**

- Lista do que ele iniciou/concluiu/pausou com as horas; OS ainda `in_progress` pedem "pausar com horas restantes"
  ou "continuar no próximo turno" (passa para o colega com nota).

---

## 4. Supervisor de produção — o dia

### Início do turno → **Relatório de turno** e **Paradas** (Sprint 3) + painel "manutenção na minha área"

- **Vê**: como foi o turno anterior (OEE, paradas, não justificadas); **manutenção programada hoje** na sua linha
  (OS com parada de máquina e a janela `PLN-03` do calendário); falhas abertas; máquinas com OS `on_hold` aguardando
  liberação.
- **Notificação**: pedido de janela de parada para preventiva (aceitar / propor outro horário); OS concluída na sua
  área aguardando fechamento.

### Durante o turno

- **Parada por falha**: a parada `EQP-*` aparece em Paradas; o supervisor (ou o operador) toca **"Chamar
  manutenção"** → cria a solicitação/OS emergencial ligada à parada e à falha rascunho e notifica os técnicos de
  plantão da área (sem plantão na linha, escala para planejamento/gestão e admins). A resposta informa quem foi
  avisado (`emergency_call.notified_count`, `on_call_technicians`, `warning`) e a tela nunca mostra "Manutenção
  chamada" quando nenhum técnico de plantão foi notificado: aviso "ninguém de plantão nesta linha" (ou alerta
  "ninguém foi notificado — avise por rádio/telefone"). A parada continua contando no OEE como `EQP-*` (disponibilidade) até a máquina voltar.
- **Defeito sem parada** (vazamento de ar, ruído): **"Solicitar manutenção"** a partir da máquina/posição (QR) com
  foto → OS `open` para o planejador; não é falha enquanto não houver perda de função (domain.md §5.5).
- **Liberação**: quando o técnico conclui uma OS que parou a máquina, o supervisor confirma "máquina liberada e
  produzindo" — sem sinal de `run_state`, essa confirmação é o `restored_at` da falha.
- **Preventiva que não foi feita** na janela combinada: a parada `PLN-03` do calendário que não teve OS executada
  aparece no relatório do turno como "janela de manutenção não usada".

### Fim do turno

- **Fechar OS** concluídas da sua área (`completed → closed`) depois de verificar o serviço (limpeza, proteção
  recolocada, primeira peça boa). Pode **devolver** com observação em vez de fechar — vira nova OS ligada à anterior
  (OS concluída não reabre, S4-5).

---

## 5. Notificações (resumo)

| Evento | Quem recebe | Canal | Quando |
|---|---|---|---|
| Plano ≥ 80 % (rascunho criado) | planejador | sino + resumo diário 07:00 | no recálculo (horário) |
| Plano `due` / `overdue` | planejador, gerente | sino + e-mail | na mudança de estado |
| Componente ≥ 80 % / ≥ 100 % da vida | planejador | sino + resumo diário | na mudança |
| OS atribuída / reprogramada | técnico | push | na hora |
| Parada `EQP-*` aberta / "chamar manutenção" | técnicos de plantão da área, supervisor; **sem técnico de plantão na linha**: planejador/gestor da área e admins (fallback) | push com som | na hora (após o limiar de parada) |
| Falha ainda aberta há > 2 × MTTR da posição | planejador, supervisor | push | escalonamento |
| OS vencida | planejador, técnico atribuído | sino + resumo diário | 00:00 local do dia seguinte a `scheduled_for` |
| OS `on_hold` | planejador | sino | na hora |
| Pedido de janela de parada | supervisor da linha | sino + push | ao programar OS que para a máquina |
| OS concluída aguardando fechamento | supervisor da área | sino | na conclusão |

Regras: notificações respeitam o tenant e a área (linha) do usuário; agrupar repetidas (não mandar 50 pushes de
plano 80 % de uma vez — resumo); cada notificação abre a tela que responde a ela; o usuário pode silenciar canais,
exceto chamado emergencial do plantão.

---

## 6. Telas do Sprint 4 (entregáveis)

| Tela | Rota proposta | Quem | Responde |
|---|---|---|---|
| Próximas manutenções | `/maintenance/upcoming` | planejador | pergunta 7 (kpis.md §5) |
| Componentes por % de vida | `/maintenance/components` | planejador | pergunta 8 |
| Ordens de serviço (quadro + lista + detalhe) | `/maintenance/work-orders` | planejador, supervisor | execução |
| Programação da semana | `/maintenance/schedule` | planejador | capacidade × backlog |
| Minhas OS (celular) | `/m/work-orders` | técnico | o que fazer hoje |
| Execução da OS (checklist, pausa, troca, conclusão) | `/m/work-orders/:id` | técnico | — |
| Falhas | `/maintenance/failures` | planejador, supervisor | pergunta 6 |
| Planos de manutenção (cadastro) | `/settings/maintenance-plans` | planejador | — |
| Gestão da manutenção (PMP, cumprimento, backlog) | `/maintenance/kpis` | gerente, planejador | pergunta 10 |
| Confiabilidade (MTBF/MCBF/MTTR/MTTF) | `/maintenance/reliability` | gerente, planejador | pergunta 9 |

Telas no celular DEVEM funcionar a 360 px de largura sem rolagem horizontal; ações principais acessíveis com o
polegar. Frontend nunca mostra dado falso sem aviso: KPI `null` aparece como "—" com o motivo (`no_failures`,
`no_recent_usage`, `insufficient_data`).

---

## 7. Critérios de aceite (verificáveis no cenário E e na planta virtual)

1. Semeando o cenário E, **Próximas manutenções** mostra os seis planos com estado, `consumed`, gatilho, data e
   próximo uso iguais a `expected.plans` (PL3: 82 % partidas, vence 10/01/2027 por horas).
2. Concluir pelo celular uma OS com troca de componente (reproduzir OS-103) zera PL1 e não PL5/PL6; o componente
   antigo aparece no histórico com a idade na remoção e o novo com 0.
3. Cancelar o rascunho de um plano ≥ 80 % cria outro rascunho (S4-4); nunca há dois.
4. Transições inválidas da lista `rejected_transitions` respondem 422 e a UI não oferece o botão.
5. **Gestão da manutenção** para novembro mostra PMP 0,684211, cumprimento 0,500000 (com OS-115 e OS-121
   excluídas e o motivo) e backlog 18,0 h / 0,45 semana com 3 OS vencidas.
6. **Confiabilidade** (12 meses) mostra os valores de `reliability_by_position`, `_by_component_type`,
   `_by_manufacturer` e `mttf_by_manufacturer_model`, com `n`, `restored`, `incipient`, `suspensions`.
7. Na planta virtual: forçar uma falha no robô → push "chamado emergencial" ao técnico de plantão; assumir no celular
   (`open → in_progress`), concluir com modo de falha; a falha tem `restored_at` = fim da parada e entra no MTTR.
8. Toda consulta e notificação é isolada por tenant (teste de isolamento por endpoint).
