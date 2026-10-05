# FactoryConnect — Especificação de domínio

Fonte da verdade do **modelo de domínio** de produção e manutenção que as Fases 1–4 implementam.
As fórmulas dos indicadores estão em [`docs/kpis.md`](kpis.md). Normas de referência:
ISO 22400-2 (KPIs de manufatura), ISO 14224 (dados de confiabilidade e manutenção),
SEMI E10 (estados de equipamento), EN 13306 (terminologia de manutenção) e práticas TPM/RCM.

Convenções usadas em todo o documento:

- **MUST / DEVE** = regra obrigatória que vira teste. **PODE** = opcional/configurável.
- Tempos são armazenados em **segundos** (inteiro ou float) e em UTC; exibição em minutos/horas no fuso da planta.
- Contadores são inteiros ≥ 0 de 64 bits no servidor (o CLP pode ter 16 ou 32 bits — ver §3.3).
- Frações (disponibilidade, % de vida etc.) trafegam na API como número entre 0 e 1 **sem arredondar**;
  a UI formata em % com 1 casa decimal.
- Toda entidade abaixo carrega `tenant_id` e toda consulta filtra por ele (regra do projeto).
- Nomes de campos em `snake_case` e em inglês (código); rótulos de UI em português.

---

## 1. Hierarquia de ativos

```
Tenant
└── Plant (planta)            fuso horário, calendário de turnos
    └── Line (linha)          máquina de saída da linha, gargalo
        └── Machine (máquina) tags, estado, paradas, OEE
            └── Position (posição funcional)   "Cilindro de fixação A1", sempre existe
                └── Component (componente físico instalado)  nº de série, vida nominal, contadores
```

| Nível | Campos mínimos | Regras |
|---|---|---|
| `Plant` | `name`, `timezone` (IANA, ex. `America/Sao_Paulo`), `address` | O fuso define dia produtivo e turnos. Um tenant pode ter N plantas. |
| `Line` | `plant_id`, `name`, `output_machine_id` (máquina cuja contagem de peças é a produção da linha), `bottleneck_machine_id` (opcional) | OEE da linha usa a contagem da máquina de saída (ver kpis.md §1.6). |
| `Machine` | `line_id`, `name`, `model`, `serial_number`, `status`, `micro_stop_threshold_s` (padrão 120), `is_line_output` (derivado) | Hoje (Fase 0) `lines.id → machines.line_id` já existe; Fase 1 adiciona `Plant` acima de `Line`. |
| `Position` (localização funcional, ISO 14224 "maintainable item location") | `machine_id`, `name`, `component_type`, `cycles_per_part` (padrão 1), `counter_tag_id` (opcional) | A posição é permanente; o que se troca é o componente. Planos de manutenção e histórico de vida são ancorados na **posição**. |
| `Component` (item físico) | `position_id` (nulo se em estoque/sucata), `component_type`, `manufacturer`, `model`, `serial_number`, `installed_at`, `removed_at`, `removal_reason`, `nominal_life` (por medida) | No máximo **um** componente instalado por posição (`removed_at IS NULL`). Componente removido nunca é apagado. |

Por que separar posição e componente: o cilindro A1 é trocado 3 vezes em 2 anos. O gerente quer
(a) a vida de **cada cilindro** (para MTTF por fabricante/modelo) e (b) o histórico da **posição A1**
(para MTBF/MCBF da aplicação e para o plano de manutenção, que continua valendo após a troca).

### 1.1 Estados da máquina

Estado instantâneo (`Machine.status`), derivado das tags quando existirem (Fase 1+), com mapeamento SEMI E10:

| `status` | Derivação (prioridade de cima para baixo) | SEMI E10 | Conta como |
|---|---|---|---|
| `unknown` | sem leitura há mais de `machine_offline_timeout_s` | — (dado ausente) | não entra em nenhum tempo; reportado como "sem dados" |
| `maintenance` | OS em execução com `stops_machine=true` ou declarado pelo operador | Scheduled/Unscheduled downtime | parada (planejada se programada no calendário, ver §4.2) |
| `error` | `fault_state = 1` | Unscheduled downtime | parada não planejada |
| `running` | `run_state = 1` (ou ciclos chegando, se não houver `run_state`) | Productive | tempo de operação |
| `stopped` | `run_state = 0` e `fault_state = 0` | Standby | parada (motivo a informar) ou micro-parada |

Na Fase 0 o estado é derivado apenas da chegada de leituras (`server/src/services/machine_status.py`).

---

## 2. Tipos de componente, medidas de uso e vida nominal

Medidas de uso (`usage_measure`):

| Medida | Unidade | Origem |
|---|---|---|
| `cycles` | ciclos (1 ciclo = avanço + retorno, ou 1 abertura + 1 fechamento) | tag `cycle_counter` ou `cycle_pulse`; ou estimado = peças × `cycles_per_part` |
| `operating_hours` | h | integração do tempo com `run_state = 1` |
| `starts` | partidas | bordas de subida de `run_state` (0→1) |
| `distance` | km | ciclos × curso (mm) / 10⁶, ou integração de `speed` |

Tabela de tipos (`component_type`). As vidas são **exemplos típicos para pré-preencher o cadastro**;
o valor do datasheet do fabricante sempre prevalece e o usuário DEVE poder editá-lo.

| `component_type` | Medidas aplicáveis (principal em **negrito**) | Vida nominal típica (exemplo) | Modos de falha mais comuns |
|---|---|---|---|
| `pneumatic_cylinder` | **cycles**, distance | 5 000 000 – 10 000 000 ciclos, ou 3 000 – 5 000 km de curso | vazamento externo/interno (vedação), curso lento, haste empenada |
| `electric_motor` | **operating_hours**, starts | rolamentos 20 000 – 40 000 h; ≤ 6 partidas/h para motores > 30 kW | superaquecimento, vibração, falha ao partir, isolação |
| `servo_motor` | **operating_hours**, cycles (movimentos) | 20 000 – 30 000 h (rolamentos/encoder) | erro de encoder, sobrecorrente, freio |
| `robot_axis` (robô/eixo) | **operating_hours**, cycles | redutor 20 000 h a carga nominal; cabos de dress-pack 2 000 000 – 5 000 000 ciclos de torção | folga no redutor, colisão, desgaste de cabo |
| `valve` (solenoide/direcional) | **cycles** | 20 000 000 – 50 000 000 comutações (pneumática); 1 000 000 (bobina CA com alta frequência) | falha ao comutar, vazamento interno, bobina queimada |
| `gripper` (garra) | **cycles** | 5 000 000 – 10 000 000 ciclos | perda de força, folga nos dedos, vazamento |
| `bearing` (rolamento) | **operating_hours** | L10h calculado do datasheet; típico 20 000 h | desgaste, fadiga, ruído, superaquecimento |
| `belt` (correia) | **operating_hours**, distance | 8 000 – 25 000 h | desgaste, alongamento (perda de tensão), ruptura |
| `pump` (bomba) | **operating_hours**, starts | selo mecânico 8 000 – 20 000 h | vazamento no selo, cavitação, vibração |
| `spindle` (eixo-árvore) | **operating_hours** | rolamentos 10 000 – 20 000 h | vibração, temperatura, perda de precisão |
| `contactor` / `other` | starts (contator), qualquer | contator: 1 000 000 manobras (vida elétrica AC-3) | — |

Regras:

- Um componente PODE ter vida nominal em mais de uma medida (ex.: motor com 30 000 h **e** 200 000 partidas).
  O % de vida consumida reportado é o **maior** entre as medidas (kpis.md §4.1).
- Medida sem vida nominal cadastrada → % de vida = `null` (nunca 0).
- `cycles_per_part` (na posição): quantos ciclos o componente faz por peça produzida. Ex.: a garra que pega e
  solta duas vezes por peça tem `cycles_per_part = 2`. Usado só quando a posição **não** tem tag de ciclo própria
  (uso estimado, marcado `estimated=true` na API e na UI).
  - Exemplo: 700 peças no turno, garra com `cycles_per_part = 2` → **1 400 ciclos estimados** da garra.

---

## 3. Tags: papel semântico e coleta

### 3.1 Papéis (`Tag.semantic_role`)

| `semantic_role` | Tipo de dado | Significado | Coleta preferida | Usado em |
|---|---|---|---|---|
| `analog` | float | grandeza contínua sem semântica especial (temperatura, pressão, vibração) | polling com deadband | tendências, regras de alerta, Fase 4 |
| `cycle_counter` | int (monotônico) | contador de ciclos acumulado mantido **no CLP** | polling do registrador; **sem deadband** | ciclos da posição, MCBF, % vida |
| `cycle_pulse` | bool | 1 pulso = 1 ciclo (ex. sensor de fim de curso) | **contagem de borda no edge** (§3.2) | idem `cycle_counter`; detecção de micro-paradas por intervalo entre ciclos |
| `run_state` | bool | 1 = máquina/componente em operação | por mudança (report-by-exception) + heartbeat | horas de operação, partidas, disponibilidade |
| `part_counter_good` | int (monotônico) | peças boas acumuladas | igual a `cycle_counter` | qualidade, desempenho, cadência |
| `part_counter_scrap` | int (monotônico) | peças refugadas acumuladas | igual a `cycle_counter` | qualidade |
| `fault_state` | bool ou int (código de falha; 0 = sem falha) | máquina em falha | por mudança + heartbeat | registro automático de parada, MTBF, MTTR |
| `stroke_time` | float (ms) | tempo de curso medido por ciclo | por ciclo, sem deadband | desgaste (Fase 4), alerta de desvio |
| `speed` | float (unid./min ou rpm) | velocidade instantânea | polling com deadband | desempenho, distância |
| `current` | float (A) | corrente de motor | polling com deadband (ou RMS por janela) | sobrecarga, Fase 4 |

Uma tag tem exatamente um papel; `analog` é o padrão. Tags `cycle_*`, `run_state` e `stroke_time`
DEVEM estar ligadas a uma `Position` ou `Machine`; `part_counter_*` a uma `Machine`.
Se a máquina não tem `part_counter_good` mas tem `part_counter_total` (contador de peças total), o servidor PODE aceitar
um papel `part_counter_total` e calcular boas = total − refugo.

### 3.2 Contador do CLP vs. contagem de borda no edge

| | `cycle_counter` (CLP) | `cycle_pulse` (edge) |
|---|---|---|
| Quem conta | programa do CLP (registrador inteiro) | gateway edge, lendo o bit em alta frequência |
| Taxa de amostragem | qualquer (o contador acumula) — ex. 1 s | DEVE ser ≥ 2× a frequência máxima de pulso (pulso de 50 ms → amostrar a ≤ 25 ms) ou usar entrada de contador rápido/subscription OPC UA com fila |
| Perda de conexão | nenhum ciclo perdido: o delta é recuperado na reconexão | ciclos durante a queda do edge são perdidos; queda só da nuvem é coberta pelo buffer SQLite |
| Deadband | **proibido** (seria perda de contagem) | **proibido** para sinais digitais |
| O que o edge publica | o valor bruto do contador | contador acumulado próprio do edge (persistido no SQLite, monotônico) — o servidor trata igual a `cycle_counter` |

Regra: **sempre preferir o contador do CLP** quando existir. O edge publica contadores como acumulado (nunca "delta"),
para que mensagens perdidas ou repetidas não alterem o total.

### 3.3 Reset e rollover de contador (DEVE)

O servidor calcula o incremento `delta` entre a leitura anterior aceita `prev` e a nova `cur`:

1. `cur ≥ prev` → `delta = cur − prev`.
2. `cur < prev` e a tag tem `counter_max` configurado (ex. 65 535 para 16 bits, 4 294 967 295 para 32 bits)
   e `prev ≥ 0,9 × counter_max` → **rollover**: `delta = (counter_max − prev) + cur + 1`.
3. `cur < prev` nos demais casos → **reset** (zerado no CLP, troca de CLP, reinício do edge sem persistência):
   `delta = cur` (contagens feitas desde o reset). Registrar evento `counter_reset` para auditoria.
4. **Filtro de plausibilidade**: se `delta > max_rate × Δt × 1,5 + 10` (onde `max_rate` = ciclos/s máximos da posição,
   padrão `2 / ideal_cycle_time`), descartar o delta, manter `prev` e registrar `counter_glitch`. Evita que uma leitura
   corrompida some milhões de ciclos.
5. Leituras duplicadas (mesmo timestamp e valor) são idempotentes: `delta = 0`.

Exemplos (viram teste):

| prev | cur | counter_max | Resultado |
|---|---|---|---|
| 1 000 | 1 250 | 65 535 | delta = 250 |
| 65 530 | 4 | 65 535 | rollover, delta = 10 |
| 48 000 | 120 | 65 535 | reset (48 000 < 0,9 × 65 535 = 58 981,5), delta = 120 |
| 48 000 | 120 | nulo | reset, delta = 120 |
| 1 000 | 900 000 (Δt = 1 s, max_rate = 2/s) | 65 535 | glitch (900 000 − 1 000 > 13), delta = 0 |

### 3.4 Horas de operação e partidas

- Horas: soma dos intervalos com `run_state = 1`. Um intervalo aberto é fechado na próxima mudança de estado,
  ou em `last_seen + machine_offline_timeout_s` se os dados pararem (nunca extrapolar operação sobre um buraco de dados).
- Partidas: número de bordas de subida 0→1 de `run_state`; um 0 com duração < 1 s (debounce, configurável) não gera nova partida.
- Sem `run_state`: horas PODEM ser estimadas por ciclos × `ideal_cycle_time` (marcadas `estimated=true`).

### 3.5 Acumuladores por componente/posição

Para cada (posição, medida) o servidor mantém:

| Acumulador | Zera quando |
|---|---|
| `usage_total` da posição | nunca (vida da aplicação) |
| `usage_since_install` do componente | nunca para aquele componente (é a "idade" dele); um novo componente começa em 0 |
| `usage_since_maintenance` por plano | quando uma OS daquele plano é concluída (§5.4) |

Acumuladores são derivados dos deltas (§3.3) e DEVEM ser reconstruíveis a partir do histórico (recalcular = mesmo resultado).

---

## 4. Produção

### 4.1 Tempos (ISO 22400-2)

```
Tempo calendário (24 h/dia)
├── Tempo não programado (sem turno, feriado)                         → só afeta TEEP
└── POT  Planned Operation Time = soma dos turnos do calendário
    ├── PDOT Paradas planejadas: refeição/pausa, reunião, preventiva programada, sem ordem/sem demanda, teste
    └── PBT  Planned Busy Time = POT − PDOT       ← "tempo de produção planejado", base da disponibilidade
        ├── ADOT Paradas não planejadas ≥ limiar de micro-parada: falha, setup/troca, ajuste, falta de material…
        └── APT  Actual Production Time = PBT − ADOT   ← "tempo rodando"
            ├── perda de velocidade + micro-paradas  → desempenho
            ├── PRI × refugo/retrabalho              → qualidade
            └── PRI × peças boas                      ← tempo produtivo líquido (valor)
```

- **Turno** (`Shift`): pertence à planta; `name`, `start_local`, `end_local`, dias da semana, pausas planejadas
  (lista de intervalos). Um turno que cruza a meia-noite pertence ao **dia produtivo em que começa**
  (22:00 seg → 06:00 ter = dia produtivo de segunda). Exceções de calendário (feriado, turno extra) sobrepõem o padrão.
- **Setup/troca de produto é parada não planejada** em ISO 22400 (reduz disponibilidade), mesmo que esperado. Ela tem
  motivo próprio (`SET-*`) para aparecer separada no Pareto.
- **Preventiva** só é parada planejada se estava programada no calendário antes do início do turno; executada
  dentro do tempo programado sem estar no calendário, é não planejada (`MNT-02`).
- Máquina `unknown` (sem dados) em tempo programado: o intervalo é reportado como **"sem dados"** e excluído de PBT,
  com indicador de cobertura de dados (`data_coverage = tempo com dados / POT`). Nunca assumir que rodou.

### 4.2 Produto, tempo de ciclo ideal e ciclos por peça

- **Produto** (`Product`): `code`, `name`. **Tempo de ciclo ideal** (`ideal_cycle_time_s`, ISO 22400 *PRI — planned run
  time per item*) é definido por **(máquina, produto)**: o menor tempo de ciclo sustentável projetado/demonstrado,
  **não** a média histórica. Para linha, por (linha, produto) = tempo de ciclo ideal do gargalo.
- **Ordem/lote em produção** (`ProductionRun`): máquina, produto, início, fim. Sem ordem informada, usa-se o produto
  padrão da máquina (e a UI avisa "produto não informado").
- `parts_per_cycle` (por máquina, produto): peças por ciclo da máquina (molde de 4 cavidades = 4). Peças = ciclos da
  máquina × `parts_per_cycle` quando não houver contador de peças.
- `cycles_per_part` (por posição): ciclos do componente por peça — ver §2.

### 4.3 Eventos de parada

`DowntimeEvent`: `machine_id`, `start`, `end` (nulo enquanto aberta), `duration_s`, `source`
(`auto_fault`, `auto_run_state`, `auto_cycle_gap`, `manual`), `reason_code` (nulo até o operador informar),
`planned` (bool, derivado do código), `failure_id` (se gerou registro de falha), `comment`.

Regras de registro automático (DEVE):

1. Abre quando `fault_state ≠ 0`, ou `run_state = 0` em tempo programado, ou (sem `run_state`) quando o intervalo desde
   o último ciclo > 2 × `ideal_cycle_time`.
2. Fecha quando a máquina volta a `running`. Paradas separadas por menos de 10 s de operação PODEM ser fundidas.
3. Duração < `micro_stop_threshold_s` (padrão **120 s**) → é **micro-parada**: não vira `DowntimeEvent`, não pede motivo,
   é acumulada como perda de desempenho (kpis.md §1.8).
4. Duração ≥ limiar → evento que **exige motivo**; sem motivo após o turno, fica `UNK-01` (não justificada).
5. Parada que cruza a fronteira de turno é dividida nos dois turnos (cada parte entra no seu turno).
6. `fault_state` tem precedência: se a falha estava ativa, o motivo sugerido é `EQP-*` (operador confirma o subtipo).

### 4.4 Taxonomia de motivos de parada (padrão; o tenant pode estender subcódigos)

| Código | Categoria | Planejada? | Perda OEE | Exemplos |
|---|---|---|---|---|
| `PLN-01` | Refeição / pausa | sim | — (fora de PBT) | almoço, café |
| `PLN-02` | Reunião / treinamento / 5S programado | sim | — | DDS |
| `PLN-03` | Manutenção preventiva programada | sim | — | preventiva no calendário |
| `PLN-04` | Sem programação / sem demanda | sim | — | falta de pedido |
| `PLN-05` | Teste / try-out / engenharia programada | sim | — | amostra de novo produto |
| `EQP-01` | Falha mecânica | não | disponibilidade | quebra, travamento |
| `EQP-02` | Falha elétrica | não | disponibilidade | motor, disjuntor, sensor |
| `EQP-03` | Falha pneumática/hidráulica | não | disponibilidade | cilindro, válvula, vazamento |
| `EQP-04` | Falha de automação/software | não | disponibilidade | CLP, robô, IHM |
| `EQP-05` | Ferramenta/molde/dispositivo | não | disponibilidade | quebra de ferramenta |
| `SET-01` | Setup / troca de produto | não | disponibilidade | troca de molde |
| `SET-02` | Ajuste / regulagem após setup | não | disponibilidade | primeiras peças |
| `MAT-01` | Falta de material / bloqueio por montante (starved) | não | disponibilidade | sem peça na entrada |
| `MAT-02` | Saída bloqueada por jusante (blocked) | não | disponibilidade | buffer cheio |
| `OPR-01` | Falta de operador | não | disponibilidade | — |
| `QLT-01` | Aguardando inspeção / problema de qualidade | não | disponibilidade | liberação de 1ª peça |
| `UTL-01` | Utilidades (energia, ar comprimido, água) | não | disponibilidade | queda de pressão de ar |
| `MNT-02` | Manutenção não programada fora de falha | não | disponibilidade | preventiva feita fora do calendário |
| `UNK-01` | Não justificada | não | disponibilidade | operador não informou |

Regra: eventos `EQP-*` com duração ≥ limiar contam como **falha** para MTBF/MTTR da máquina quando estão ligados a um
`FailureRecord` (§5.5). Por padrão o servidor cria o `FailureRecord` automaticamente (rascunho) para todo `EQP-*`;
o técnico completa modo/causa/mecanismo. `MAT-*`, `SET-*`, `OPR-*`, `UTL-*` **não** são falhas do equipamento.

---

## 5. Manutenção

### 5.1 Plano de manutenção (`MaintenancePlan`)

Ancorado em uma **posição** (ou máquina, para planos de máquina inteira). Campos: `name`, `task_list` (checklist),
`estimated_hours`, `wo_type` (`preventive` ou `predictive`), e **um ou mais gatilhos**:

| Gatilho | Campo | Medida |
|---|---|---|
| ciclos | `interval_cycles` | `usage_since_maintenance.cycles` |
| horas | `interval_hours` | `usage_since_maintenance.operating_hours` |
| partidas | `interval_starts` | `usage_since_maintenance.starts` |
| calendário | `interval_days` | dias desde a última conclusão (ou desde a criação do plano) |
| condição (Fase 4) | regra sobre tag (ex. `stroke_time` > 120% da referência) | — |

**O que vier primeiro** (DEVE): para cada gatilho calcula-se `consumed_i = uso_i / intervalo_i`; o plano está
`consumed = max_i(consumed_i)`. Estados do plano: `ok` (< 0,8), `due_soon` (≥ 0,8 → alerta e OS gerada
automaticamente em rascunho), `due` (≥ 1,0), `overdue` (≥ 1,0 + tolerância, padrão 10% do intervalo).
A data projetada é a mais cedo entre as projeções de cada gatilho (kpis.md §4.2).

Exemplo: plano "trocar vedações do cilindro A1" a cada 2 000 000 ciclos **ou** 180 dias. Desde a última troca:
1 700 000 ciclos (0,85) e 60 dias (0,333) → `consumed = 0,85`, estado `due_soon`, gatilho determinante = ciclos.

### 5.2 Tipos de ordem de serviço (`WorkOrder.type`)

| Tipo | Quando | Gera parada? | Planejada (PMP)? |
|---|---|---|---|
| `preventive` | gerada por plano (uso ou calendário) | se `stops_machine` | sim |
| `predictive` | gerada por condição/tendência (Fase 4) ou inspeção preditiva (vibração, termografia) | se `stops_machine` | sim |
| `corrective` | após falha ou defeito encontrado. `urgency = emergency` (atendimento imediato) ou `deferred` (programada para depois) | normalmente | só `deferred` |
| `improvement` | melhoria/modificação (eliminar causa raiz, kaizen) | se `stops_machine` | sim |

### 5.3 Estados da OS

```
draft ──► open ──► approved ──► scheduled ──► in_progress ──► completed ──► closed
                                    ▲              │   ▲
                                    │              ▼   │
                                    └──────── on_hold (aguardando peça/liberação)
qualquer estado antes de completed ──► cancelled (motivo obrigatório)
```

| Estado | Significado | Timestamps |
|---|---|---|
| `draft` | gerada automaticamente (plano em 80%) ou rascunho | `created_at` |
| `open` | solicitação registrada | `requested_at` |
| `approved` | aprovada pelo planejador | `approved_at` |
| `scheduled` | com data/hora e responsável | `scheduled_for`, `assignee_id` |
| `in_progress` | técnico iniciou | `started_at` (primeiro início) |
| `on_hold` | pausada; o tempo em espera **não** conta como horas trabalhadas | `hold_reason` |
| `completed` | serviço executado; horas apontadas; peças trocadas registradas | `completed_at`, `labor_hours` |
| `closed` | verificada/encerrada pelo supervisor | `closed_at` |
| `cancelled` | não será executada | `cancelled_at`, `cancel_reason` |

Transições fora deste diagrama DEVEM ser rejeitadas pela API (422). Uma OS corretiva de emergência PODE ir de `open`
direto a `in_progress`.

### 5.4 Execução e troca de componente (DEVE)

Ao concluir (`completed`) uma OS de um plano:
- `usage_since_maintenance` de **todos os gatilhos daquele plano** zera, e a data de referência do calendário passa a
  ser `completed_at`. Os demais planos da posição não são afetados.

Ao registrar **troca de componente** em uma OS (qualquer tipo):
1. O componente antigo recebe `removed_at = completed_at`, `position_id = null`, `removal_reason`
   (`failure`, `preventive_replacement`, `upgrade`, `other`) e **mantém** seus contadores finais (`usage_since_install`),
   que são a idade na remoção (base do MTTF por fabricante/modelo).
2. O novo componente é instalado com `installed_at = completed_at` e contadores em 0.
3. `usage_total` da posição continua acumulando (não zera).
4. Os planos da posição que têm `reset_on_replacement = true` (padrão) zeram `usage_since_maintenance`.
5. Nada é apagado: histórico de leituras, OS e falhas do componente antigo permanece consultável.
6. Se o contador físico (CLP) também for zerado na troca, o reset é tratado pela regra §3.3 (não gera ciclos negativos).

### 5.5 Registro de falha (`FailureRecord`, ISO 14224)

Uma falha é a **perda da capacidade de executar a função requerida** (não inclui paradas por falta de material,
setup etc.). Campos:

| Campo | Valores (códigos ISO 14224, com rótulo PT) | Obrigatório |
|---|---|---|
| `machine_id`, `position_id`, `component_id` | o item que falhou (o mais baixo conhecido) | máquina sim; posição/componente quando conhecido |
| `failed_at` | início da perda de função (= início da parada, se houve) | sim |
| `restored_at` | função restabelecida (máquina volta a produzir) | sim, ao fechar |
| `detected_at`, `repair_started_at`, `repair_finished_at` | para separar tempo de detecção, espera e reparo ativo | não |
| `severity` | `critical` (perda total imediata da função), `degraded` (função parcial), `incipient` (início de degradação, ainda funciona) | sim |
| `failure_mode` | modo — **como** se manifestou. Comuns a todos: `BRD` quebra, `FTS` falha ao partir, `STP` falha ao parar, `UST` parada espúria, `ELP` vazamento externo, `INL` vazamento interno, `OHE` superaquecimento, `VIB` vibração, `NOI` ruído, `PDE` desvio de parâmetro (ex. curso lento), `ERO` saída errática, `LOO` perda de saída, `STD` deficiência estrutural, `AIR` leitura anormal de instrumento, `SER` problemas menores, `OTH` outro, `UNK` desconhecido. Válvulas: `FTO` falha ao abrir, `FTC` falha ao fechar. | sim |
| `failure_mechanism` | mecanismo — processo físico. 1 Mecânico (1.1 vazamento, 1.2 vibração, 1.3 folga/desalinhamento, 1.4 deformação, 1.5 afrouxamento, 1.6 travamento); 2 Material (2.1 cavitação, 2.2 corrosão, 2.3 erosão, 2.4 desgaste, 2.5 ruptura, 2.6 fadiga, 2.7 superaquecimento, 2.8 explosão/estouro); 3 Instrumento (3.1 falha de controle, 3.2 sem sinal, 3.3 sinal falho, 3.4 fora de ajuste, 3.5 erro de software, 3.6 falha de causa comum); 4 Elétrico (4.1 curto-circuito, 4.2 circuito aberto, 4.3 sem energia, 4.4 energia com defeito, 4.5 falha de aterramento/isolação); 5 Influência externa (5.1 bloqueio/entupimento, 5.2 contaminação, 5.3 outra externa); 6 Diversos (6.1 sem causa encontrada, 6.2 causas combinadas, 6.3 outro, 6.4 desconhecido) | sim (pode ser 6.4 até a análise) |
| `failure_cause` | causa raiz — **por que**. 1 Projeto (1.1 capacidade inadequada, 1.2 material inadequado); 2 Fabricação/instalação (2.1 erro de fabricação, 2.2 erro de instalação); 3 Operação/manutenção (3.1 serviço fora do projeto, 3.2 erro de operação, 3.3 erro de manutenção, 3.4 desgaste e envelhecimento esperados); 4 Gestão (4.1 erro de documentação, 4.2 erro de gestão); 5 Diversos (5.1 sem causa encontrada, 5.2 causa comum, 5.3 causa combinada, 5.4 outro, 5.5 desconhecido) | sim (pode ser 5.5) |
| `detection_method` | `periodic_maintenance`, `functional_test`, `inspection`, `periodic_condition_monitoring`, `continuous_condition_monitoring` (alarme do FactoryConnect), `production_interference` (descoberta porque parou a produção), `casual_observation`, `corrective_maintenance`, `on_demand`, `other` | sim |
| `downtime_event_id`, `work_order_id` | vínculos | quando houver |
| `usage_at_failure` | snapshot de `usage_since_install` e `usage_total` (ciclos, horas, partidas) no `failed_at` | preenchido pelo servidor |

Regras:
- Uma falha ↔ no máximo uma OS corretiva principal; uma parada `EQP-*` ↔ no máximo uma falha.
- Só falhas `critical` e `degraded` entram em MTBF/MCBF/MTTR (padrão); `incipient` é contada à parte (kpis.md §2).
- Falha encontrada em preventiva sem ter parado a produção: `detection_method = periodic_maintenance`, sem parada
  associada; conta para MTBF do componente, **não** para MTTR da máquina (não houve tempo de restabelecimento em produção).

---

## 6. Glossário rápido

| Termo | Definição curta |
|---|---|
| Parada planejada | prevista no calendário antes do turno; fora do PBT |
| Parada não planejada | qualquer interrupção ≥ limiar dentro do PBT, inclusive setup |
| Micro-parada | interrupção < `micro_stop_threshold_s` (padrão 120 s); perda de desempenho, sem motivo |
| Preventiva | baseada em tempo/uso, independente da condição |
| Preditiva | baseada em condição medida/tendência |
| Corretiva | após falha ou defeito |
| MTBF vs MCBF | MTBF em **horas de operação**; MCBF em **ciclos** — use MCBF para pneumática, garras, robôs |
| Posição | lugar funcional permanente na máquina |
| Componente | item físico com nº de série instalado numa posição |
