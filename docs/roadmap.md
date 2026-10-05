# FactoryConnect — Roadmap

Objetivo: ser referência em Indústria 4.0 — conectar equipamentos e linhas de produção,
coletar os dados fundamentais e entregar o **raio-x da fábrica** para times de produção e
manutenção: eficiência (OEE), perdas, confiabilidade (MTBF/MCBF/MTTR), uso de cada
componente e planejamento de manutenção.

Cada fase só é considerada concluída quando **todos** os critérios de aceite passam em CI
e na planta virtual (`edge` com CLPs simulados). Status: ⬜ pendente · 🟨 em andamento · ✅ concluído.

---

## Fase 0 — Fundação (pré-requisito de tudo) ⬜
Sem testes e sem deploy confiável, nada é "referência".

- [ ] Senha do VPS removida do repo e **rotacionada**; deploy por chave SSH, usuário não-root
- [ ] `docker compose` de dev sobe com um comando (corrigir `--env-file`, nginx `/api`, conflito de deps edge/server)
- [ ] Testes: `pytest` (server + edge), `vitest` (web), Playwright e2e (login → dashboard → alertas)
- [ ] CI no GitHub Actions: lint + typecheck + testes + build das imagens em todo PR
- [ ] Zerar os erros de TypeScript existentes; remover fallback silencioso para mocks no frontend
- [ ] Status da máquina derivado dos dados; dashboard mostra nome do sensor (não UUID)
- [ ] Imagens com versão fixa (`mediamtx`, etc.)

## Fase 1 — Modelo de ativos e coleta de uso ⬜
- [ ] Hierarquia **Planta → Linha → Máquina → Componente** (cilindro, motor, robô/eixo, válvula, garra, rolamento…)
- [ ] Componente com tipo, fabricante, modelo, série, data de instalação e **vida nominal** (ciclos / horas / partidas)
- [ ] Papel semântico da tag: `analog`, `cycle_counter`, `cycle_pulse`, `run_state`, `part_counter_good`,
      `part_counter_scrap`, `fault_state`, `stroke_time`
- [ ] Acumuladores por componente: total e desde a última manutenção (ciclos, horas, partidas),
      com tratamento de reset/rollover de contador do CLP
- [ ] Edge: leitura de contadores do CLP **e** contagem de borda local para pulsos rápidos (sem deadband em sinais digitais)
- [ ] **Planta virtual**: servidor OPC UA + Modbus simulados com cilindros, motores, robô, contador de peças,
      falhas e desgaste (tempo de curso aumentando) — base dos testes de integração
- [ ] Cadência da linha (peças/h) e ciclos por peça por componente

## Fase 2 — Eficiência produtiva ⬜
- [ ] OEE real (ISO 22400): disponibilidade × desempenho × qualidade, por máquina/linha/turno
- [ ] Registro automático de paradas (`fault_state`/`run_state`) + motivo informado pelo operador
- [ ] Micro-paradas, Pareto de perdas, comparação entre turnos e produtos
- [ ] Turnos e calendário de produção; tempo de ciclo ideal por produto

## Fase 3 — Manutenção ⬜
- [ ] Plano por componente: a cada N ciclos **ou** X horas **ou** Y dias (o que vier primeiro); % de vida consumida
- [ ] Ordens de serviço preventiva / corretiva / preditiva; troca de componente zera contadores e mantém histórico
- [ ] Registro de falhas com taxonomia (ISO 14224: modo, causa, mecanismo)
- [ ] MTBF, **MCBF**, MTTR, disponibilidade — por componente, tipo, fabricante, máquina e linha
- [ ] Painel "próximas manutenções" e alertas em 80% / 100% da vida
- [ ] API de integração com CMMS/ERP (SAP PM, TOTVS, Engeman) — webhooks + REST

## Fase 4 — Preditiva e IA ⬜
- [ ] Previsão da data de troca a partir do ritmo de uso e do plano de produção
- [ ] Detecção de desvio: tempo de curso, corrente de motor, vibração, temperatura
- [ ] Chat de IA com acesso real aos dados (ferramentas que consultam KPIs, histórico, OS)
- [ ] Relatórios de vida útil por fabricante/modelo

## Fase 5 — Raio-x da fábrica e UX ⬜
- [ ] Design system próprio (tokens, tema claro/escuro), interface limpa, moderna e fluida
- [ ] Navegação drill-down Fábrica → Linha → Máquina → Componente, com KPIs em cada nível
- [ ] Mapa/layout da planta com status ao vivo
- [ ] App responsivo/PWA para chão de fábrica (tablet do operador, celular do técnico)
- [ ] Relatórios exportáveis (PDF/Excel) e agendados

## Fase 6 — Produção e campo ⬜
- [ ] Deploy automatizado (CI → VPS) com rollback, backups do Postgres/Influx e monitoramento
- [ ] Segurança: MQTT com TLS + autenticação por gateway, OPC UA com certificados, auditoria
- [ ] Gateway de borda empacotado (imagem para IPC/Raspberry) com provisionamento pela nuvem
- [ ] **Piloto real** em uma linha de cliente, com relatório de resultados
