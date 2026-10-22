# FactoryConnect — Relatórios exportáveis e agendados (especificação para o Sprint 6, Fase 5)

Item do roadmap: Fase 5 "Relatórios exportáveis (PDF/Excel) e agendados" e Fase 4 "Relatórios de vida útil por
fabricante/modelo" (promessa de kpis.md §2.4: **Weibull com censura**). Este documento é a fonte da verdade do
catálogo, do conteúdo de cada relatório, do método de estimação de vida, dos formatos, do agendamento e da
distribuição por e-mail. Os números dos exemplos são casos de teste: os blocos JSON `report-*` no fim são lidos por
`docs/scenarios/test_reports_spec.py`, que recalcula tudo com uma implementação independente.

Normativo: **DEVE** / **NÃO DEVE**; **PODE** = opcional.

## 0. Princípios (valem para todos os relatórios)

1. **Mesmos números das telas.** Um relatório é uma composição de respostas da API já existentes (OEE, Pareto,
   paradas, confiabilidade, gestão da manutenção) para o mesmo escopo e período — nunca uma segunda implementação
   de KPI. Teste de aceite: para cada número do relatório há a chamada da API que o devolve igual (tolerância 1e-6).
2. **Sem dado falso.** Indicador indefinido sai como "—" com o motivo (`reason`, kpis.md §0), nunca 0. Período com
   `data_coverage < 0,95` leva a faixa "dados incompletos (cobertura X %)" no topo da página e na linha afetada;
   `ideal_cycle_time_suspect`, `product_unknown`, `counts_prorated`, `calendar_default`, `estimated` e
   `low_confidence` aparecem como notas de rodapé numeradas na linha onde ocorrem.
3. **Período = dia produtivo / turno do calendário** (C19, S3-8), no **fuso da planta**. "Ontem" é o dia produtivo
   anterior, não 00:00–24:00 UTC. Semana = segunda a domingo (dias produtivos). Mês = dias produtivos do mês civil.
4. **Instantâneo reprodutível.** Cada geração grava `report_runs` (tenant, tipo, escopo, período, parâmetros,
   `generated_at`, `data_until` = último dado considerado, versão do gerador, hash do arquivo). Gerar de novo o
   mesmo período depois de dados atrasados (D12) ou de reclassificação de paradas cria uma **nova versão**; a
   anterior continua disponível e o rodapé diz "versão 2 — substitui a de 06/10 06:30".
5. **Multi-tenant e papéis.** Toda consulta filtra por `tenant_id`; o relatório só contém o que o destinatário pode
   ver na tela equivalente (mesma capacidade de `RequireCapability`). Um relatório agendado é gerado com as
   permissões do **destinatário** (um operador não recebe horas/custos da manutenção, maintenance_workflows.md §1).
6. **Unidades e arredondamento** como S3-5: frações viram percentuais com 1 casa só na apresentação; no Excel as
   células guardam a fração bruta com formato `0.0%`.

---

## 1. Catálogo

| Código | Relatório | Pergunta que responde | Escopo | Período | Formatos | Agendamento padrão |
|---|---|---|---|---|---|---|
| `daily_production` | Relatório diário de produção | "Como foi ontem? Batemos a meta? Onde perdemos?" (kpis.md §5 nº 1–3) | planta (uma seção por linha) | dia produtivo | PDF, XLSX | todo dia, 06:30 local |
| `shift` | Relatório de turno | "O que aconteceu no meu turno, o que fica para o próximo?" | linha | instância de turno | PDF | ao fim de cada turno + 15 min |
| `weekly_oee` | Revisão semanal de OEE e perdas | "Estamos melhorando? Quais 3 perdas atacar esta semana?" (nº 2, 4) | planta / linha | semana (seg–dom) | PDF, XLSX | segunda, 07:00 local |
| `monthly_maintenance` | Relatório mensal de gestão da manutenção | "Estamos mais preventivos? A equipe dá conta? Quem são os maus atores?" (nº 9, 10) | planta | mês | PDF, XLSX | dia 1, 07:00 local |
| `reliability_life` | Confiabilidade e vida por fabricante/modelo (Weibull) | "Qual marca/modelo dura mais? A vida nominal é real? Quando trocar?" | tenant (todas as plantas) ou planta; por tipo de componente | todo o histórico (S4-11) até a data de corte | PDF, XLSX | trimestral (1º dia do trimestre), 07:00 local; sob demanda |

Todos podem ser gerados **sob demanda** (botão "Exportar" na tela correspondente, mesmo escopo e período da tela).

### 1.1 `daily_production` — Relatório diário de produção

Fonte: `GET /api/oee?scope=line&group_by=day`, Pareto, `GET /api/downtime/`, `GET /api/reports/shift`.

Seções (PDF, A4 retrato, 1 página por linha + capa da planta):

1. **Capa da planta**: tabela por linha — OEE, A, P, Q do dia; PQ, GQ, SQ; cadência realizada (§1.9); variação do
   OEE vs. média das 4 últimas semanas no mesmo dia da semana (pp); meta (se cadastrada); `data_coverage`.
   OEE da planta = agregação por soma (§1.6), nunca média das linhas.
2. **Por linha**: OEE por turno (S3-8: o total do dia é a soma dos turnos); top-5 perdas do Pareto (§1.10) em
   minutos e %; paradas ≥ 10 min (início, fim, máquina, motivo efetivo D2, duração); paradas **não justificadas**
   (D2) em destaque com o total de minutos; paradas planejadas declaradas no turno (S3-4); produção fora do
   calendário (S3-7), se houver.
3. **Manutenção do dia** (só para destinatários com `maintenance.view`): falhas do dia (`FailureRecord` por
   `failed_at`, incluindo as sem parada — kpis S5-1), OS emergenciais abertas/fechadas.

Metas: hoje não há meta cadastrada no modelo. O Sprint 6 acrescenta `line_targets(tenant_id, line_id, oee,
parts_per_shift, valid_from)` (migration Alembic); sem meta, a coluna **não aparece** (não é 0 nem "—").

Exemplo (caso `daily_production_e2`): o dia do kpis.md E2 (turnos A + B, PBT 870) sai no relatório como
OEE **83,7 %** (0,836590), A **92,0 %**, P **93,8 %**, Q **97,0 %**, turnos A **75,4 %** e B **92,5 %**. O relatório
**NÃO DEVE** mostrar 84,0 % (média dos turnos, 0,839524).

### 1.2 `shift` — Relatório de turno

É o `GET /api/reports/shift` existente (Sprint 3) em PDF, mais: pendências para o próximo turno (paradas abertas,
paradas sem motivo, OS emergenciais abertas na linha, alertas preditivos `warning`/`critical` abertos na linha) e
campo de observações do supervisor (texto livre gravado no `report_run`). Enviado ao supervisor do turno que
termina e ao do próximo turno. Gerado `settle = 15 min` depois do fim do turno (dá tempo ao detector, D12, e ao
operador para justificar as últimas paradas). Exemplo (caso `shift_schedule_manaus`): Turno A de 05/10 termina
14:00 local → gerado e enviado **14:15 local = 18:15Z**.

### 1.3 `weekly_oee` — Revisão semanal de OEE e perdas

Para a reunião semanal de melhoria (TPM/kaizen). Seções:

1. Tendência: OEE, A, P, Q por semana das últimas **12 semanas** (gráfico de linhas) com a semana atual destacada.
2. Pareto da semana por motivo e por máquina (top-10) + comparação com a semana anterior (Δ minutos por motivo).
3. Comparação de turnos e de produtos (OEE e perdas, agregação §1.6).
4. Micro-paradas: nº por hora de APT e minutos (§1.8), por máquina.
5. Disciplina de apontamento: % de tempo de parada justificado (D2), paradas planejadas declaradas (S3-4).
6. "3 perdas a atacar": as 3 maiores perdas em minutos da semana, com a máquina principal de cada uma — lista
   gerada pela regra, sem texto inventado. O assistente de IA **NÃO** escreve no relatório agendado (só dados).

### 1.4 `monthly_maintenance` — Relatório mensal de gestão da manutenção

Fonte: KPIs de gestão da manutenção, confiabilidade, OS e preditiva (mesmas rotas das telas). Seções:

1. Indicadores do mês e dos 12 meses anteriores (tabela + gráfico): PMP (§3.1), cumprimento da programação (§3.2),
   backlog em semanas (§3.3, no último dia do mês), Ao (§2.6), MTBF/MTTR da planta (§2.1, §2.3, janela 12 meses S4-8).
   Referências ao lado: PMP ≥ 0,85; cumprimento ≥ 0,90; backlog 2–4 semanas.
2. Horas de OS por tipo (preventiva, preditiva, melhoria, corretiva programada, corretiva emergencial);
   `predictive_no_defect_hours` à parte (predictive.md P-9).
3. Maus atores: top-10 máquinas/posições por nº de falhas e por minutos de parada `EQP-*`, com o modo de falha ISO
   14224 dominante (pergunta 9; domain.md "Revisão ISO 14224" item 12). Falhas sem tempo produtivo (kpis S5-1)
   contadas em coluna própria (`failures_outside_production`, kpis S5-1c item 6).
4. Preditiva: alertas abertos/fechados no mês por desfecho, precisão e antecedência (predictive.md P-10).
5. Componentes ≥ 80 % de vida e planos `overdue` no fim do mês.

Exemplo (caso `monthly_maintenance_m`): com os números de kpis.md M1–M3, R1, R3 e R6 o relatório mostra
PMP **84,4 %** (0,84375, abaixo da referência 85 % → marcador âmbar), cumprimento **85,0 %** (abaixo de 90 % → âmbar),
backlog **2,0 semanas** (verde), MTBF **180,0 h**, MTTR **60,0 min**, Ao **98,0 %**.

### 1.5 `reliability_life` — Confiabilidade e vida por fabricante/modelo (Weibull com censura)

Para o gerente de manutenção e engenharia/compras: comparar marcas e modelos, conferir a vida nominal do catálogo
e escolher intervalo de troca. Uma seção por **tipo de componente**; dentro dela uma linha por
**(fabricante, modelo)**. Método: §2.

Colunas por grupo: componentes (N), falhas (r), suspensões (N − r, separando removidos sem falha e em serviço),
medida (ciclos/h/partidas, a da vida nominal do tipo — S4-11), MTTF simples (kpis.md §2.4, para comparação),
β e η com IC 90 %, MTTF Weibull, **B10** com IC 90 %, vida nominal do catálogo e `B10 / vida nominal`,
interpretação de β (§2.6), avisos (`low_confidence`, `insufficient_failures`).
Gráficos: papel de Weibull (pontos de ranks ajustados + reta do MLE) por grupo; barras de B10 com IC por grupo.
Seção extra: **componentes em serviço com maior probabilidade de falhar nos próximos 30 dias** (§2.5).
Comparação entre marcas: diferença só é chamada de "significativa" quando os IC 90 % de η **não se sobrepõem**
(critério conservador); caso contrário o texto é "sem diferença demonstrada com os dados atuais".

---

## 2. Método de estimação de vida (Weibull de 2 parâmetros com censura à direita)

Referências: IEC 61649 (análise de Weibull), IEC 60050-192, ISO 14224 (dados de falha); Abernethy, *The New
Weibull Handbook* (ranks de Johnson, aproximação de Bernard).

### 2.1 Dados: unidade, idade, falha e suspensão

- **Grupo** = (tipo de componente, fabricante, modelo) — o mesmo agrupamento de S4-9/S4-11. Fabricante/modelo
  vazio = grupo "—" (mostrado, mas fora da comparação entre marcas).
- **Unidade** = um período de instalação de um componente (instalado → removido, ou instalado até a data de corte).
  Componente reparado e reinstalado começa uma **nova** unidade (hipótese "tão bom quanto novo", anotada no rodapé).
- **Idade** `t` = `usage_since_install` na medida do tipo, no fim da unidade (remoção ou data de corte).
- **Falha** (`δ = 1`): removido com `removal_reason = failure` — inclui remoção por defeito confirmado em OS
  preditiva (falha `incipient`/`degraded`, domain S5-2): é o fim de vida útil do item, ainda que antes da perda
  total; isso deixa a vida estimada levemente conservadora e o relatório informa quantas foram assim.
- **Suspensão** (`δ = 0`, censura à direita): removido por **qualquer outro motivo** (troca preventiva, melhoria,
  OS preditiva com `no_defect`, desmontagem) **e** todo componente **ainda instalado** (idade atual). É isto que
  falta no MTTF simples de §2.4: suspensões carregam informação ("durou pelo menos t").
- **Excluídas** (listadas com o motivo, fora do ajuste): `t ≤ 0` ou sem uso registrado (`no_usage`), tipo sem
  medida de vida (`no_measure`).

### 2.2 Modelo e estimador

Weibull de 2 parâmetros (sem parâmetro de localização): `F(t) = 1 − exp(−(t/η)^β)`, `R(t) = 1 − F(t)`.

Estimador: **máxima verossimilhança (MLE) com censura à direita**, sem correção de viés. Com `r = Σ δ_i` falhas e
todas as `N` unidades:

```
ln L(β, η) = Σ_{falhas} [ln β − β·ln η + (β − 1)·ln t_i]  −  Σ_{todas} (t_i / η)^β
```

β é a raiz de

```
g(β) = Σ_{todas} t_i^β·ln t_i / Σ_{todas} t_i^β  −  1/β  −  (1/r)·Σ_{falhas} ln t_i  = 0
```

e então `η = ( Σ_{todas} t_i^β / r )^(1/β)`.

Algoritmo (determinístico, sem dependência de biblioteca estatística):
1. Escalar as idades por `s = max(t_i)` antes de elevar a β (evita overflow; η é reescalado no fim — β não muda).
2. `g` é crescente em β; resolver por **bissecção** em `[0,05; 50]` até `|β_hi − β_lo| < 1e-10` (no máximo 200
   iterações). Sem troca de sinal no intervalo → `null` com `reason = degenerate` (p. ex. todas as falhas com a
   mesma idade e nenhuma suspensão maior).
3. Mínimo de dados: **`r ≥ 3`**, senão `null` com `reason = insufficient_failures` (o relatório mostra só N, r e o
   MTTF simples). `3 ≤ r < 10` → resultado com `low_confidence = true` (aviso "amostra pequena: com poucas falhas
   o β estimado tende a sair alto").

### 2.3 Intervalos de confiança (matriz de Fisher, 90 % bilateral)

Informação observada = −Hessiana de ln L no MLE, com `z_i = (t_i/η)^β` e `l_i = ln(t_i/η)` (todas as unidades):

```
∂²lnL/∂β²  = −r/β² − Σ z_i·l_i²
∂²lnL/∂η²  = r·β/η² − (β/η²)·Σ z_i − (β²/η²)·Σ z_i
∂²lnL/∂β∂η = −r/η + (1/η)·Σ z_i + (β/η)·Σ z_i·l_i
```

`Cov = (−H)⁻¹` → `Var(β)`, `Var(η)`, `Cov(β, η)`. Limites com transformação log (sempre positivos),
`z = 1,644854`:

- `β_lo, β_hi = β · exp(∓ z·√Var(β) / β)`; `η_lo, η_hi = η · exp(∓ z·√Var(η) / η)`.
- B10: `B10 = η·(−ln 0,9)^(1/β)`; com `k = ln(−ln 0,9)`,
  `Var(ln B10) = Var(η)/η² + (k/β²)²·Var(β) − 2·(k/β²)·Cov(β, η)/η`; `B10_lo, B10_hi = B10 · exp(∓ z·√Var(ln B10))`.

### 2.4 Grandezas derivadas

- `MTTF_W = η · Γ(1 + 1/β)` (vida média) — substitui o MTTF simples quando há ajuste; o simples continua na
  tabela para comparação.
- `B10` = idade em que 10 % das unidades terão falhado (base para intervalo de troca preventiva).
- `B10 / vida nominal` (vida do catálogo, `nominal_life` do componente): < 0,8 → "vida real abaixo da nominal".

### 2.5 Probabilidade condicional de falha de um componente em serviço

Para um componente instalado com idade `a` e ritmo `u` por dia (kpis.md §4.2, 14 dias corridos), nos próximos
`D = 30` dias: `P = 1 − R(a + u·D) / R(a)`. A lista é ordenada por `P` decrescente; `u = 0` → `null`
(`no_recent_usage`).

### 2.6 Interpretação de β (texto fixo no relatório)

| β (IC 90 %) | Padrão (`pattern`) | Recomendação impressa |
|---|---|---|
| IC inteiro < 1 | `infant_mortality` | "Falhas precoces: revisar montagem, qualidade do lote e partida. Troca preventiva **não** ajuda." |
| IC contém 1 | `random` | "Falhas aleatórias: troca por tempo não reduz falhas; preferir monitoramento de condição (preditiva)." |
| IC inteiro > 1 | `wear_out` | "Desgaste: troca preventiva faz sentido; B10 é uma referência de intervalo." |

### 2.7 Papel de Weibull (só visual)

Ordenar todas as unidades por idade (empate: falha antes de suspensão). Para cada **falha**, com `RR` = número de
unidades com idade ≥ a dela (rank reverso) e `j_prev` o rank ajustado anterior (0 no início):
`j = j_prev + (N + 1 − j_prev) / (1 + RR)` (rank ajustado de Johnson), `F = (j − 0,3) / (N + 0,4)` (Bernard).
Eixos: `x = ln t`, `y = ln(−ln(1 − F))`; a reta desenhada é a do **MLE** (não uma regressão nos pontos).

### 2.8 Exemplo trabalhado (caso `weibull_cylinders`) — dados de teste

Cilindros pneumáticos do mesmo fabricante e modelo (dados fictícios para teste), medida **ciclos**, N = 10:

| Unidade | Idade (ciclos) | Situação | δ |
|---|---|---|---|
| C-01 | 3 100 000 | removido, falha (vazamento `ELP`) | 1 |
| C-02 | 3 900 000 | removido, falha | 1 |
| C-03 | 4 400 000 | removido, falha | 1 |
| C-04 | 5 200 000 | removido, falha | 1 |
| C-05 | 5 800 000 | removido, falha | 1 |
| C-06 | 6 700 000 | removido, falha | 1 |
| C-07 | 4 000 000 | removido em troca preventiva | 0 |
| C-08 | 4 500 000 | removido em troca preventiva | 0 |
| C-09 | 2 000 000 | **em serviço** | 0 |
| C-10 | 5 000 000 | **em serviço** | 0 |

Resultado (r = 6 → `low_confidence = true`):

| Grandeza | Valor |
|---|---|
| MTTF simples (§2.4: média das 6 falhas) | 4 850 000 ciclos (ignora as 4 suspensões → subestima) |
| β (IC 90 %) | **5,198474** (3,168572 – 8,528802) → IC inteiro > 1: desgaste |
| η (IC 90 %) | **5 592 653** (4 913 430 – 6 365 772) ciclos |
| √Var(β), √Var(η) | 1,564687; 440 249 |
| MTTF Weibull | **5 146 536** ciclos |
| B10 (IC 90 %) | **3 627 573** (2 802 888 – 4 694 903) ciclos |
| Com vida nominal 5 000 000 | B10 / nominal = 0,725515 → "vida real abaixo da nominal" |
| C-10 (5 000 000, ritmo 20 000/dia, 30 dias) | P = 1 − R(5,6 M)/R(5,0 M) = **0,361250** |
| C-09 (2 000 000, mesmo ritmo) | P = **0,013788** |

Ranks ajustados (papel de Weibull): 3,1 M → j 1,1, F 0,076923; 3,9 M → 2,2, 0,182692; 4,4 M → 3,457143, 0,303571;
5,2 M → 5,342857, 0,484890; 5,8 M → 7,228571, 0,666209; 6,7 M → 9,114286, 0,847527.

Caso `weibull_bearings_r4` (o R4 de kpis.md: falhas 8 200, 9 600, 10 400 h; suspensões 7 000, 9 000 h):
β = **14,282747**, η = **9 899,27 h**, MTTF_W = **9 544,34 h** (o simples dá 9 400 h), B10 = **8 456,23 h**,
`low_confidence = true` (r = 3: β tão alto é efeito de amostra pequena — o aviso é obrigatório).
Caso `weibull_insufficient`: r = 2 → `null`, `reason = insufficient_failures`, MTTF simples 2 800 000.

---

## 3. Formatos

### 3.1 PDF

- A4 (retrato; paisagem para `weekly_oee` e `reliability_life`), fonte da identidade visual, gráficos vetoriais.
- Cabeçalho: logo do tenant (se houver), nome do relatório, escopo (planta/linha), período em hora local
  ("Dia produtivo 05/10/2026 (06:00 05/10 – 06:00 06/10, America/Manaus)").
- Rodapé de toda página: `gerado em <local> · dados até <data_until local> · versão N · página x/y` e o link para a
  tela equivalente no app.
- Faixas de aviso (princípio 2) no topo da página afetada. Página final "Definições" com a fórmula curta de cada
  KPI usado e a referência (kpis.md §).
- Geração no servidor com bibliotecas Python puras (sugestão: ReportLab para PDF e openpyxl para XLSX — instaláveis
  por pip, sem pacotes de sistema; a imagem do server não precisa de `apt`). Tamanho alvo < 2 MB.

### 3.2 Excel (XLSX)

- Uma aba **"Resumo"** igual à primeira página do PDF; uma aba por tabela de dados (`OEE_por_turno`,
  `Pareto`, `Paradas`, `Falhas`, `OS`, `Weibull_grupos`, `Weibull_unidades`), uma aba **"Leia-me"** com as
  definições e avisos.
- Dados **brutos**: frações como número com formato `0.0%`; tempos em **minutos** (número) com o nome da coluna
  dizendo a unidade (`duracao_min`); timestamps em duas colunas (`inicio_local` como data/hora do Excel no fuso da
  planta e `inicio_utc` ISO 8601). Sem fórmulas (os valores são os da API) e sem células mescladas nas abas de dados
  (filtros e tabelas dinâmicas funcionam).
- `Weibull_unidades` traz cada unidade (componente, idade, δ, motivo de remoção, data) para o cliente refazer a conta.
- Indefinido = célula **vazia** + coluna `motivo` (`no_failures` etc.) — nunca 0.

---

## 4. Agendamento

Modelo novo (migration Alembic): `report_schedules(id, tenant_id, report_type, scope_type, scope_id, params,
deliver_at_local "HH:MM", weekday (weekly), month_day (monthly, 1–28), enabled, created_by, recipients[])` e
`report_runs` (princípio 4). O motor roda a cada minuto (como o motor da manutenção) e é idempotente por
(schedule, período): nunca envia duas vezes o mesmo período.

### 4.1 Quando um período está pronto

- `ready_at` = fim do período **pelo calendário** + `settle`:
  - fim do período = fim da **última instância de turno** cujo dia produtivo pertence ao período (o turno C que
    começa 22:00 do último dia termina às 06:00 do dia seguinte, C19); sem turnos (D6) = 24:00 local do último dia;
  - `settle` = 15 min para `shift`, 30 min para os demais (detector D12, justificativas de última hora).
- `send_at` = `ready_at` para `shift`; para os demais, `max(ready_at, D às deliver_at_local)`, onde **D** é o
  primeiro dia civil local depois do último dia do período (diário 05/10 → 06/10; semana 28/09–04/10 → segunda 05/10;
  mês de setembro → 01/10). Ou seja: nunca antes de os dados fecharem, e no horário escolhido quando possível.
- Fusos: tudo em `ZoneInfo(plant.timezone)`; num fuso com horário de verão, `deliver_at_local` inexistente
  (pulo) → primeiro minuto válido depois; ambíguo (repetido) → a primeira ocorrência.
- `reliability_life` trimestral: período = histórico até 00:00 local do 1º dia do trimestre; `ready_at` = esse
  instante + 30 min.

### 4.2 Falhas e atraso

- Geração falhou → até 3 novas tentativas (5, 15 e 60 min); depois, notificação ao admin do tenant e linha
  `failed` em `report_runs`. Nada é enviado parcialmente.
- Servidor fora do ar no horário → ao voltar, envia os períodos pendentes dos **últimos 7 dias** (mais antigos ficam
  só para download), cada um uma vez.
- Dados atrasados depois do envio **não** reenviam automaticamente (exceto o mensal: se uma nova versão mudar
  algum KPI da seção 1 em mais de 0,5 pp ou 1 %, o responsável recebe "versão revisada" — fechamento do mês).

---

## 5. Distribuição por e-mail

- Canal: o `mailer` existente (`server/src/services/mailer.py`: SendGrid ou SMTP), estendido com **anexo**. Sem
  canal configurado → o relatório é gerado e fica para download; o admin vê o aviso "e-mail não configurado"
  (nunca falha silenciosa).
- Corpo do e-mail (texto + HTML simples): 3–5 números principais do relatório (ex.: OEE do dia por linha e a maior
  perda), o aviso de dados incompletos se houver, e o link para o app. O anexo é o PDF (e o XLSX, se marcado).
  Anexos acima de 10 MB → só link (URL assinada, expira em 7 dias, exige login do tenant).
- Destinatários: **usuários do tenant** (por papel e área/linhas do usuário, `User.line_ids`) e, só se o admin
  habilitar `reports_external_recipients`, e-mails externos cadastrados com nome e finalidade (LGPD: registro da
  base legal, descadastro com um clique, link "não quero mais receber" em todo e-mail).
- Assunto padronizado: `[FactoryConnect] <Relatório> — <escopo> — <período>` (ex.: `[FactoryConnect] Produção
  diária — Planta Manaus — 05/10/2026`).
- Registro: cada envio grava destinatário, horário, id do provedor e resultado (bounce → desativa o destinatário
  externo após 3 bounces e avisa o admin). Nenhum dado de produção vai para log.

### 5.1 Quem recebe o quê (padrão ao criar o tenant; o admin ajusta)

| Relatório | Recebe por padrão (papel) | Filtro de área | Conteúdo restrito |
|---|---|---|---|
| `daily_production` | `manager`, `production_supervisor`, `admin` | linhas do usuário | seção "Manutenção do dia" só com `maintenance.view` |
| `shift` | `production_supervisor` (turno que termina e próximo) | linha do relatório | — |
| `weekly_oee` | `manager`, `production_supervisor` | linhas do usuário | — |
| `monthly_maintenance` | `manager`, `maintenance_planner`, `admin` | planta | horas/custos não vão para quem não tem `maintenance.kpis` |
| `reliability_life` | `manager`, `maintenance_planner` | tenant | — |

`operator` e `technician` **não** recebem relatórios por e-mail por padrão (o operador usa o tablet; o técnico, o
celular); podem baixá-los no app se tiverem acesso à tela.

---

## 6. Critérios de aceite (Sprint 6)

1. Cada relatório do catálogo gera PDF e (quando listado) XLSX para o tenant da planta virtual; cada número
   confere com a chamada da API equivalente (teste automático por relatório).
2. Os casos `daily_production_e2`, `monthly_maintenance_m` e `weibull_*` deste documento passam no backend
   (o gerador de Weibull carrega o bloco `report-weibull-cases` e confere com tolerância relativa 1e-6).
3. Agendamento: os casos `report-schedule-cases` dão os mesmos `ready_at`/`send_at` no motor; nenhum período é
   enviado duas vezes (motor rodando duas vezes no mesmo minuto) e nenhum antes do `ready_at`.
4. Isolamento: um agendamento do tenant A nunca contém dados nem destinatários do tenant B (teste de isolamento);
   o relatório respeita a capacidade do destinatário (operador não recebe horas de OS).
5. Dado indefinido aparece como "—"/célula vazia com motivo; cobertura < 95 % mostra a faixa; nunca 0 no lugar.
6. E-mail: com um servidor SMTP de teste em memória o anexo chega, o assunto segue o padrão, o link de descadastro
   funciona; sem canal configurado o admin vê o aviso.
7. UI: botão "Exportar PDF/Excel" nas telas OEE, Paradas, Relatório de turno, Gestão da manutenção e Confiabilidade;
   tela "Relatórios agendados" (criar, pausar, enviar agora, histórico de versões) verificada no navegador (Playwright).

---

## 7. Blocos de teste

```json report-catalog
[
  {"code": "daily_production", "formats": ["pdf", "xlsx"], "period": "productive_day", "settle_min": 30,
   "deliver_at_local": "06:30", "recipients": ["manager", "production_supervisor", "admin"]},
  {"code": "shift", "formats": ["pdf"], "period": "shift_instance", "settle_min": 15,
   "deliver_at_local": null, "recipients": ["production_supervisor"]},
  {"code": "weekly_oee", "formats": ["pdf", "xlsx"], "period": "week", "settle_min": 30,
   "deliver_at_local": "07:00", "weekday": 0, "recipients": ["manager", "production_supervisor"]},
  {"code": "monthly_maintenance", "formats": ["pdf", "xlsx"], "period": "month", "settle_min": 30,
   "deliver_at_local": "07:00", "month_day": 1, "recipients": ["manager", "maintenance_planner", "admin"]},
  {"code": "reliability_life", "formats": ["pdf", "xlsx"], "period": "history_to_quarter_start", "settle_min": 30,
   "deliver_at_local": "07:00", "recipients": ["manager", "maintenance_planner"]}
]
```

```json report-kpi-cases
[
  {"id": "daily_production_e2", "from_kpi_case": "oee_two_shifts_mix",
   "expected_display": {"oee": "83,7 %", "availability": "92,0 %", "performance": "93,8 %", "quality": "97,0 %",
                        "shift_oee": ["75,4 %", "92,5 %"]},
   "must_not_display": {"oee": "84,0 %"}},
  {"id": "monthly_maintenance_m",
   "from_kpi_cases": {"pmp": "pmp", "schedule_compliance": "schedule_compliance", "backlog": "backlog",
                      "mtbf": "mtbf", "mttr": "mttr", "ao": "availability_operational"},
   "expected_display": {"pmp": "84,4 %", "schedule_compliance": "85,0 %", "backlog": "2,0 semanas",
                        "mtbf": "180,0 h", "mttr": "60,0 min", "ao": "98,0 %"},
   "flags": {"pmp": "amber", "schedule_compliance": "amber", "backlog": "green"}}
]
```

`send_at` e `ready_at` em UTC. Turnos `[início, fim]` locais; fim ≤ início = cruza a meia-noite (dia produtivo de
início); `weekdays` 0 = segunda (D9).

```json report-schedule-cases
[
  {"id": "daily_abc_manaus", "report": "daily_production", "tz": "America/Manaus", "period": ["2026-10-05", "2026-10-05"],
   "shifts": [["06:00", "14:00"], ["14:00", "22:00"], ["22:00", "06:00"]], "weekdays": [0, 1, 2, 3, 4, 5, 6],
   "deliver_at_local": "06:00", "settle_min": 30,
   "expected": {"ready_at": "2026-10-06T10:30:00Z", "send_at": "2026-10-06T10:30:00Z"}},
  {"id": "daily_no_calendar_manaus", "report": "daily_production", "tz": "America/Manaus",
   "period": ["2026-10-05", "2026-10-05"], "shifts": [], "weekdays": [], "deliver_at_local": "07:00", "settle_min": 30,
   "expected": {"ready_at": "2026-10-06T04:30:00Z", "send_at": "2026-10-06T11:00:00Z"}},
  {"id": "shift_schedule_manaus", "report": "shift", "tz": "America/Manaus", "shift_instance": ["2026-10-05", "06:00", "14:00"],
   "settle_min": 15, "expected": {"ready_at": "2026-10-05T18:15:00Z", "send_at": "2026-10-05T18:15:00Z"}},
  {"id": "weekly_ab_weekdays_manaus", "report": "weekly_oee", "tz": "America/Manaus", "period": ["2026-09-28", "2026-10-04"],
   "shifts": [["06:00", "14:00"], ["14:00", "22:00"]], "weekdays": [0, 1, 2, 3, 4],
   "deliver_at_local": "07:00", "settle_min": 30,
   "expected": {"ready_at": "2026-10-03T02:30:00Z", "send_at": "2026-10-05T11:00:00Z"}},
  {"id": "monthly_abc_sao_paulo", "report": "monthly_maintenance", "tz": "America/Sao_Paulo", "period": ["2026-09-01", "2026-09-30"],
   "shifts": [["06:00", "14:00"], ["14:00", "22:00"], ["22:00", "06:00"]], "weekdays": [0, 1, 2, 3, 4, 5, 6],
   "deliver_at_local": "07:00", "settle_min": 30,
   "expected": {"ready_at": "2026-10-01T09:30:00Z", "send_at": "2026-10-01T10:00:00Z"}},
  {"id": "daily_late_ready_manaus", "report": "daily_production", "tz": "America/Manaus", "period": ["2026-10-05", "2026-10-05"],
   "shifts": [["06:00", "14:00"], ["14:00", "22:00"], ["22:00", "06:00"]], "weekdays": [0, 1, 2, 3, 4, 5, 6],
   "deliver_at_local": "05:00", "settle_min": 30,
   "expected": {"ready_at": "2026-10-06T10:30:00Z", "send_at": "2026-10-06T10:30:00Z"}}
]
```

Idades em ciclos (cilindros) ou horas (rolamentos). `z = 1,644854` (90 % bilateral). Tolerância relativa 1e-6
(valores com 2 casas: absoluta 0,01).

```json report-weibull-cases
[
  {"id": "weibull_cylinders", "measure": "cycles",
   "failures": [3100000, 3900000, 4400000, 5200000, 5800000, 6700000],
   "suspensions": [4000000, 4500000, 2000000, 5000000],
   "nominal_life": 5000000,
   "conditional": [{"age": 5000000, "rate_per_day": 20000, "days": 30, "p_fail": 0.361250},
                   {"age": 2000000, "rate_per_day": 20000, "days": 30, "p_fail": 0.013788}],
   "expected": {"n": 10, "r": 6, "beta": 5.198474, "eta": 5592653.48, "se_beta": 1.564687, "se_eta": 440249.43,
                "beta_lo": 3.168572, "beta_hi": 8.528802, "eta_lo": 4913429.81, "eta_hi": 6365771.82,
                "mttf": 5146536.11, "b10": 3627573.03, "b10_lo": 2802887.75, "b10_hi": 4694903.01,
                "b10_over_nominal": 0.725515, "simple_mttf": 4850000, "low_confidence": true, "pattern": "wear_out",
                "johnson_ranks": [1.1, 2.2, 3.457143, 5.342857, 7.228571, 9.114286],
                "bernard_f": [0.076923, 0.182692, 0.303571, 0.484890, 0.666209, 0.847527]}},
  {"id": "weibull_bearings_r4", "measure": "hours",
   "failures": [8200, 9600, 10400], "suspensions": [7000, 9000],
   "expected": {"n": 5, "r": 3, "beta": 14.282747, "eta": 9899.272975, "mttf": 9544.339352, "b10": 8456.22517,
                "simple_mttf": 9400, "low_confidence": true, "pattern": "wear_out"}},
  {"id": "weibull_insufficient", "measure": "cycles",
   "failures": [2500000, 3100000], "suspensions": [1000000, 4000000],
   "expected": {"n": 4, "r": 2, "beta": null, "reason": "insufficient_failures", "simple_mttf": 2800000}}
]
```
