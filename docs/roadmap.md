# FactoryConnect — Roadmap

Objetivo: ser referência em Indústria 4.0 — conectar equipamentos e linhas de produção,
coletar os dados fundamentais e entregar o **raio-x da fábrica** para times de produção e
manutenção: eficiência (OEE), perdas, confiabilidade (MTBF/MCBF/MTTR), uso de cada
componente e planejamento de manutenção.

Cada fase só é considerada concluída quando **todos** os critérios de aceite passam em CI
e na planta virtual (`edge` com CLPs simulados). Status: ⬜ pendente · 🟨 em andamento / parcial · ✅ concluído.
Ao lado de cada item entregue, o sprint em que entrou (S1 … S7; versões no [CHANGELOG](../CHANGELOG.md)).

> **Situação em 07/10/2026 (Sprint 7, versão 0.7.0).** As fases 0 a 5 estão entregues e validadas na planta
> virtual e nos cenários de ouro. A Fase 6 está entregue no software (deploy, segurança, escala, release), mas
> **ainda não rodou em produção nem em fábrica real**: o go-live depende do dono do projeto
> ([go_live.md](go_live.md)) e o piloto em cliente ([pilot.md](pilot.md)) não começou. Nenhum número deste
> repositório vem de cliente real. O que falta está em [Pendências](#pendências-honestas) no fim.
> Itens marcados S7 foram entregues neste sprint e passam pela QA e pela revisão do próprio sprint antes do merge.

---

## Fase 0 — Fundação (pré-requisito de tudo) ✅
Sem testes e sem deploy confiável, nada é "referência".

- [x] ✅ S1 — Senha do VPS removida do repo; deploy por chave SSH, usuário não-root
  - [ ] 🟨 **Rotação** da senha antiga e conferência do SSH só por chave: tarefa do dono no VPS
        ([go_live.md §1](go_live.md)) — a senha continua no histórico do git (commit `cc1d1e7`) e deve ser tratada
        como pública
- [x] ✅ S1 — `docker compose` de dev sobe com um comando (`make dev`)
- [x] ✅ S1 — Testes: `pytest` (server + edge), `vitest` (web), Playwright e2e (login → dashboard → alertas)
- [x] ✅ S1 — CI no GitHub Actions: lint + typecheck + testes + build das imagens em todo PR
- [x] ✅ S1 — Zerar os erros de TypeScript existentes; remover fallback silencioso para mocks no frontend
- [x] ✅ S1 — Status da máquina derivado dos dados; dashboard mostra nome do sensor (não UUID)
- [x] ✅ S1 — Imagens com versão fixa (`mediamtx`, etc.)

## Site institucional — porta de entrada 🟨
Site público em `factoryconnect.tech` (app em `app.factoryconnect.tech`), `site/` (Astro estático).

- [x] ✅ S2 — Cases fictícios ("Metalúrgica Fonseca", "Plásticos Norte", "Alimentos BomDia") removidos; teste
      impede que voltem (`web/src/pages/LandingPage.test.tsx`). Só entram cases reais com autorização do cliente
- [x] ✅ S2 — Formulário de contato que entrega o lead: `/api/public/leads` grava no banco e envia por e-mail, com
      consentimento LGPD, honeypot e limite por IP
- [x] ✅ S2 — Site em `factoryconnect.tech`, app em `app.factoryconnect.tech` (Traefik roteia os dois; rotas antigas
      redirecionam). DNS: tarefa do go-live ([go_live.md §2](go_live.md))
- [x] ✅ S2 — Páginas: início, solução, para manutenção, para produção, como funciona, segurança, planos, sobre,
      contato, política de privacidade
- [x] ✅ S6 — **Tour/demo interativa**: tenant demo somente-leitura alimentado pela planta virtual, sessão por visitante
- [x] ✅ S2/S7 — Cadastro self-service e login levando ao app; **manual do usuário público** em `/docs` do site
      (por papel, com busca — S7). Referência da API: OpenAPI em `/api/docs`
  - [ ] ⬜ Guia público do gateway e da API de integração escrito para cliente (hoje: [gateways.md](gateways.md) e o
        OpenAPI, técnicos)
- [x] ✅ S2 — SEO técnico (sitemap, meta/OG)
  - [ ] ⬜ Lighthouse ≥ 95 **do site** medido e registrado (o app tem medição própria no S7, ver Fase 5)
  - [ ] ⬜ Analytics respeitando LGPD (não implementado — o site não coleta nada hoje)
- [x] ✅ S2 — Mesma identidade visual do design system do app
  - [ ] ⬜ **Inglês** (site, app e manual estão só em português)

## Fase 1 — Modelo de ativos e coleta de uso ✅
- [x] ✅ S2 — Hierarquia **Planta → Linha → Máquina → Componente** (cilindro, motor, robô/eixo, válvula, garra, rolamento…)
- [x] ✅ S2 — Componente com tipo, fabricante, modelo, série, data de instalação e **vida nominal** (ciclos / horas / partidas)
- [x] ✅ S2 — Papel semântico da tag: `analog`, `cycle_counter`, `cycle_pulse`, `run_state`, `part_counter_good`,
      `part_counter_scrap`, `fault_state`, `stroke_time`
- [x] ✅ S2 — Acumuladores por componente: total e desde a última manutenção (ciclos, horas, partidas),
      com tratamento de reset/rollover de contador do CLP (domain.md §3.3, cenário de ouro A)
- [x] ✅ S2 — Edge: leitura de contadores do CLP **e** contagem de borda local para pulsos rápidos (sem deadband em sinais digitais)
- [x] ✅ S2 — **Planta virtual**: servidor OPC UA + Modbus simulados com cilindros, motores, robô, contador de peças,
      falhas e desgaste (tempo de curso aumentando) — base dos testes de integração
- [x] ✅ S2 — Cadência da linha (peças/h) e ciclos por peça por componente

## Fase 2 — Eficiência produtiva ✅
- [x] ✅ S3 — OEE real (ISO 22400): disponibilidade × desempenho × qualidade, por máquina/linha/turno (cenários C e D)
- [x] ✅ S3 — Registro automático de paradas (`fault_state`/`run_state`) + motivo informado pelo operador
- [x] ✅ S3 — Micro-paradas, Pareto de perdas, comparação entre turnos e produtos
- [x] ✅ S3 — Turnos e calendário de produção; tempo de ciclo ideal por produto

## Fase 3 — Manutenção ✅ (com uma pendência)
- [x] ✅ S4 — Plano por componente: a cada N ciclos **ou** X horas **ou** Y dias (o que vier primeiro); % de vida consumida
- [x] ✅ S4 — Ordens de serviço preventiva / corretiva / preditiva; troca de componente zera contadores e mantém histórico
- [x] ✅ S4 — Registro de falhas com taxonomia (ISO 14224: modo, causa, mecanismo)
- [x] ✅ S4 — MTBF, **MCBF**, MTTR, disponibilidade — por componente, tipo, fabricante, máquina e linha (cenário E);
      MART, λ por ciclos e falhas fora do tempo produtivo — S7 (auditoria de KPIs)
- [x] ✅ S4 — Painel "próximas manutenções" e alertas em 80% / 100% da vida
- [x] ✅ S4 — API de integração com CMMS/ERP — webhooks assinados + REST com token de API
  - [ ] ⬜ Conectores prontos para SAP PM, TOTVS, Engeman (hoje a integração é pela API genérica; nenhum conector
        específico foi testado contra o sistema real)
- [ ] ⬜ Janela de parada `PLN-03` **por máquina** proposta ao programar OS que para a máquina
  (maintenance_workflows.md §2): o supervisor recebe o pedido de janela (`window_request`), mas a exceção por
  máquina no calendário + confirmação do supervisor não existe. Até lá a parada planejada é cadastrada à mão no
  calendário (Programação/Turnos).

## Fase 4 — Preditiva e IA ✅
- [x] ✅ S5 — Previsão da data de troca a partir do ritmo de uso e do plano de produção
- [x] ✅ S5/S5b — Detecção de desvio: tempo de curso, corrente de motor, vibração, temperatura (cenário F; leituras
      atrasadas reprocessam as janelas — S5b)
- [x] ✅ S5 — Chat de IA com acesso real aos dados (ferramentas que consultam KPIs, histórico, OS)
- [x] ✅ S5 — Relatórios de vida útil por fabricante/modelo (Weibull com censura)

## Fase 5 — Raio-x da fábrica e UX ✅ (com uma pendência)
- [x] ✅ S6 — Design system próprio (tokens, tema claro/escuro), interface limpa, moderna e fluida
  - [x] ✅ S7 — Acessibilidade: varredura axe em 184 combinações tela × tema × viewport sem violações
        (`web/e2e/a11y.verify.mjs`); medição Lighthouse do build de produção (`web/e2e/lighthouse.verify.mjs`)
- [x] ✅ S6 — Navegação drill-down Fábrica → Linha → Máquina → Componente, com KPIs em cada nível
- [x] ✅ S6 — Mapa/layout da planta com status ao vivo (cadência ao vivo pelas leituras do contador — S7;
      ressalva aberta A-19b em [audit_kpis.md](audit_kpis.md#verificação-sprint-7))
- [x] ✅ S6 — App responsivo/PWA para chão de fábrica (tablet do operador, celular do técnico)
  - [ ] ⬜ **Pendente (sprint6 §3.2)**: vínculo do tablet por código de 8 caracteres (10 min) com token de dispositivo
        preso à linha e revogável; entrada do operador por PIN ou crachá com bloqueio após 5 erros. Hoje o tablet
        entra com e-mail e senha (manual/operador.md descreve só isso).
- [x] ✅ S6 — Relatórios exportáveis (PDF/Excel) e agendados
- [x] ✅ S6 — Assistente de implantação (CSV de ativos/tags, navegação OPC UA pelo gateway)

## Fase 6 — Produção e campo 🟨
- [x] ✅ S1/S3/S7 — Deploy automatizado (CI → VPS) com rollback, backups do Postgres/Influx com teste de
      restauração e monitoramento (Prometheus, Alertmanager por e-mail, Grafana)
  - [x] ✅ S7 — Processo de release (`VERSION`, CHANGELOG, tag → imagens no GHCR com SBOM e proveniência), deploy de
        versão específica com aprovação manual, staging no mesmo VPS
  - [ ] ⬜ **Primeiro deploy em produção** e virada de DNS — só com o dono ([go_live.md](go_live.md)); nada disso
        foi executado a partir do ambiente de desenvolvimento
- [x] ✅ S3/S5 — Segurança: MQTT com TLS + autenticação e ACL por gateway (credencial revogável)
  - [x] ✅ S7 — Auditoria (`audit_events`), JWT com rotação de chave, sessões revogáveis, MFA TOTP opcional, bloqueio
        por força bruta, matriz de autorização (todo endpoint × todo papel × outro tenant), cabeçalhos HTTP (CSP
        estrita, HSTS), pip-audit / npm audit / gitleaks / Trivy / Dependabot no CI ([security.md](security.md))
  - [x] ✅ S7 — LGPD: exportação e anonimização de dados pessoais, retenção configurável ([privacy.md](privacy.md))
  - [ ] ⬜ OPC UA com certificados e usuário (política `Basic256Sha256`, `Sign`/`SignAndEncrypt`) — hoje o gateway
        conecta ao endpoint OPC UA **sem** segurança OPC UA (`edge/src/protocols/opcua.py`). **Bloqueia** o checklist
        do piloto ([pilot.md §5](pilot.md) pede `Basic256Sha256` com certificado do gateway): implementar antes do
        piloto ou acordar com a TI do cliente, por escrito, uma exceção (rede de automação isolada, só leitura)
  - [ ] ⬜ Teste de invasão por terceiro independente
- [x] ✅ S6 — Gateway de borda empacotado (imagem multi-arquitetura para IPC/Raspberry) com cadastro por código
- [x] ✅ S7 — Endurecimento dos indicadores: todos os itens da auditoria de KPIs fechados e reverificados
      ([audit_kpis.md](audit_kpis.md)), exceto **A-19b** (cadência ao vivo do mapa sem filtro de leitura
      corrompida — só exibição)
- [x] ✅ S7 — Detectores robustos depois de quedas longas (catch-up por checkpoint, resultado idêntico ao contínuo)
- [x] 🟨 S7 — Escala: 100 máquinas (~1 300 leituras/s) dentro das metas (ingestão p95 1,1 s; telas p95 < 200 ms)
      num host de 4 vCPU compartilhado; 200 máquinas (~2 600/s) **não** fecham as metas nesse host — dimensionamento
      de 8 vCPU recomendado, ainda não medido em hardware dedicado ([performance.md](performance.md))
- [ ] ⬜ **Piloto real** em uma linha de cliente, com relatório de resultados — plano pronto ([pilot.md](pilot.md)),
      piloto não iniciado

---

## Pendências honestas

O que **não** existe ou **não** foi provado em 07/10/2026, em ordem de importância para vender:

1. **Piloto em fábrica real** ([pilot.md](pilot.md)). Toda validação até aqui é na planta virtual, nos cenários de
   ouro e em testes. Ainda não sabemos, com dado de campo: erro da contagem de peças contra contagem manual, % de
   paradas detectadas, aderência da equipe (justificativa de paradas) e ganho de OEE.
2. **Cases de cliente**: nenhum. O site e a apresentação não podem citar resultados até haver um piloto com
   relatório e autorização por escrito.
3. **Go-live**: produção, DNS, segredos novos, rotação da senha vazada, aprovação de deploy — tarefas do dono
   ([go_live.md](go_live.md)).
4. **CLPs reais**: Modbus TCP e OPC UA foram testados contra servidores simulados (bibliotecas padrão), não contra
   Siemens / Rockwell / Schneider / WEG reais; S7, EtherNet/IP e PROFINET não são suportados diretamente (precisam de
   OPC UA ou de um conversor).
5. **OPC UA com certificados** (exigido pelo checklist de segurança do piloto), **tablet por código/PIN** (o
   piloto promete "operadores podem usar PIN sem e-mail" — hoje não podem), **janela `PLN-03` por máquina**
   (itens acima). Os dois primeiros precisam estar prontos, ou negociados com o cliente, antes do piloto.
6. **Escala** medida só em host compartilhado de 4 vCPU; repetir em hardware dedicado de 8 vCPU antes de prometer
   200+ máquinas por instância.
7. **A-19b** — filtro de leitura corrompida na cadência ao vivo do mapa.
8. **Inglês** (site, app, manual) e **analytics** com consentimento no site.
9. **Conectores específicos** de CMMS/ERP (SAP PM, TOTVS, Engeman) e teste de invasão independente.
10. Registro da marca, termos de uso / contrato de nível de serviço (SLA) e política comercial — fora do software,
    decisão do dono.
