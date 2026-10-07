# FactoryConnect — Manutenção preditiva (especificação para o Sprint 5, Fase 4)

Regras **normativas** (DEVE) do módulo preditivo: o que é medido, contra o que é comparado, quando vira alerta, como
o alerta vira ordem de serviço preditiva, como se projeta a vida remanescente pela condição e como se prova que tudo
funciona. Cada regra tem um código **P-n** citado pelos testes. O cenário de ouro **F**
([`scenarios/scenario_f_predictive.json`](scenarios/README.md#cenário-f--preditiva-sprint-5)) exercita cada regra e
[`scenarios/test_scenario_f.py`](scenarios/test_scenario_f.py) é o oráculo.

Base: [`domain.md`](domain.md) §2 (tipos e modos de falha), §3.1 (papéis `stroke_time`, `current`, `analog`), §5.1
(gatilho de **condição**), §5.2 (OS `predictive`), §5.5 (ISO 14224) e os esclarecimentos S4-1…S4-12;
[`kpis.md`](kpis.md) §3.1 (PMP), §4.2 e C17/C18 (ritmo de uso e arredondamento de dias);
[`maintenance_workflows.md`](maintenance_workflows.md) (papéis e telas). O assistente de IA que consulta estes dados
está em [`ai_assistant.md`](ai_assistant.md).

Princípios (valem para todo o documento):

1. **Explicável e determinístico.** Nenhum modelo caixa-preta no Sprint 5. Todo alerta diz, em português, *qual
   sinal*, *quanto mudou em relação a quê*, *qual limite foi cruzado* e *quando a falha é projetada*. Mesmos dados ⇒
   mesmos alertas (o histórico de alertas é reconstruível, como os acumuladores de domain.md §3.5).
2. **Comparar o componente com ele mesmo.** A referência é a linha de base da **posição** medida logo depois da
   instalação/manutenção (P-2), não um valor de catálogo nem a média da frota.
3. **Preditiva ≠ alarme em tempo real.** O módulo de alertas existente (`alert_rules`: valor instantâneo da tag
   `> limite`) continua cuidando do que exige reação em segundos (sobrecorrente, temperatura de desarme). A preditiva
   trabalha com **características agregadas por janela** (P-1) e detecta degradação lenta, de dias a semanas.
4. **Nunca mostrar dado falso.** Sem linha de base → "aprendendo (12/20 janelas)", não um z-score. Sem tendência
   significativa → RUL `null` com `reason`, não um número. Mesmas convenções de `null`/`reason` de kpis.md §0.

---

## P-1 — Características de condição (features) e janelas

O servidor transforma as leituras brutas (Influx) em **uma característica por janela** e por (posição, sinal). Os
detectores (P-3, P-4) só enxergam essa série — curta, auditável e guardada no Postgres (`condition_features`).

| Sinal (`feature`) | Papel da tag (domain.md §3.1) | Janela padrão | Agregado | Direção ruim | Unidade da tendência (P-5) |
|---|---|---|---|---|---|
| `stroke_time` | `stroke_time` (ms por ciclo; um por sentido se houver avanço/retorno) | **bloco de 1 000 ciclos** da posição | **mediana** dos tempos de curso do bloco | `up` | ms por ciclo (mostrado por 10 000 ciclos) |
| `current` | `current` (A) | **8 h** de calendário | **média ponderada no tempo** só com `run_state = 1` | `up` (PODE `both`: queda = carga desacoplada/correia) | A por hora de operação |
| `vibration` | `analog` com `unit = mm/s` (velocidade RMS) | 8 h | média ponderada no tempo, só rodando | `up` | mm/s por hora de operação |
| `temperature` | `analog` com `unit = °C` | 8 h | média ponderada no tempo, só rodando; se a posição tem tag de **ambiente**, usa `ΔT = T − T_amb` | `up` | °C por hora de operação |

Regras (DEVE):

- **Só rodando.** Sinais analógicos usam apenas os intervalos com `run_state = 1` da posição (ou da máquina, C13).
  Corrente de motor parado não é "corrente baixa": é ausência de dado.
- **Média ponderada no tempo** (amostra-e-segura): as tags analógicas chegam com deadband, então a média aritmética
  das amostras seria enviesada para os trechos que variam. Cada valor vale até a próxima leitura, limitado a
  `machine_offline_timeout_s` (mesma regra de buraco de dados de domain.md §3.4).
- **Janela válida**: `stroke_time` — leituras em ≥ 80 % dos ciclos do bloco; analógicos — tempo rodando ≥ 25 % da
  janela **e** cobertura de dados ≥ 80 % do tempo rodando. Janela inválida (feriado, máquina parada, gateway fora)
  **não gera ponto**: não conta, não zera e não quebra sequência de nenhuma regra (P-3) nem avança EWMA/CUSUM
  (cenário F2, dia 47 = feriado).
- A janela por **ciclos** acompanha o desgaste (um cilindro parado não envelhece); a janela por **tempo** acompanha
  sinais térmicos/elétricos. O índice da janela, o fim (UTC) e o uso acumulado da posição no fim da janela
  (`u`: ciclos desde a instalação para `stroke_time`, horas de operação para os demais) são guardados com o ponto.
- O tamanho da janela é configurável por (posição, sinal). Mudar o tamanho **reinicia a linha de base** (P-2).
- No cenário F as janelas são maiores (10 000 ciclos; 24 h) para caber no JSON; as regras são as mesmas.

---

## P-2 — Linha de base (referência por posição)

**Quando (re)começa** — eventos de reinício da linha de base de uma (posição, sinal):

| Evento | Aquecimento | Observação |
|---|---|---|
| componente instalado na posição (troca, S4-2) | sim | o novo componente tem a sua própria referência |
| OS concluída na posição com `resets_baseline = true` | sim | padrão `true` para OS com troca de componente e para `corrective`/`predictive`; `false` para as demais (inspeção não muda a condição). O técnico confirma ao concluir. |
| `rebaseline` manual (planejador/gerente) ou fechamento de alerta com resolução `rebaseline` (P-8) | **não** | "novo normal" aceito (ex.: mudança de produto/carga); motivo obrigatório, fica na trilha de auditoria |
| mudança de configuração do sinal (janela, agregado, tag) | sim | — |

**Como se calcula** (DEVE):

1. Descartar as primeiras `warmup_windows` janelas válidas (amaciamento da vedação, aquecimento do motor).
   Padrão: 5 blocos para `stroke_time`; 1 janela (8 h) para analógicos.
2. Coletar as próximas **`N = 20` janelas válidas** (mínimo; e no mínimo 24 h de calendário entre a primeira e a
   última, para pegar mais de um turno). Enquanto não houver N, o estado é `learning` e a UI mostra o progresso.
3. `m0 = mediana(x)`, `MAD = mediana(|x − m0|)`, **`σ = max(1,4826 · MAD, σ_piso)`**, com
   `σ_piso = max(0,01 · |m0|, resolução do sensor)`.
   - 1,4826 · MAD é o desvio-padrão robusto (igual ao desvio-padrão para dados normais, mas um pico não o infla).
   - O **piso** existe porque a mediana de 1 000 ciclos é tão estável (ruído por ciclo de 1,5 ms → ~0,06 ms na
     mediana) que, sem piso, qualquer desgaste normal de 0,1 ms viraria "6 σ". O piso diz: *mudança menor que 1 % da
     referência não interessa*. (F1: `1,4826 · 1,0 = 1,48 ms` < piso `2,0 ms` → `σ = 2,0`; F2:
     `1,4826 · 0,1 = 0,148 A` > piso `0,12 A` → `σ = 0,148`. Os dois ramos estão no cenário.)
4. A linha de base fica **congelada** até o próximo evento de reinício. Ela não "acompanha" a deriva — é isso que
   permite detectar a deriva. Cada linha de base é um registro (`condition_baselines`: posição, sinal, `m0`, `MAD`,
   `σ`, janelas de/até, `established_at`, motivo, usuário) e nunca é apagada.
5. Antes da linha de base, só as regras de **limite absoluto** (P-3 `threshold`) avaliam. Detectores relativos
   (EWMA, CUSUM, `% da base`, taxa, RUL por condição) começam na janela seguinte a `established_at`, com EWMA e CUSUM
   em 0.

---

## P-3 — Regras de condição (configuradas pelo usuário)

Uma regra pertence a uma (posição, sinal) e tem `type`, `value`, `sustained_for` e `severity` (`watch`, `warning`,
`critical`). Tipos:

| `type` | Condição na janela i | Exemplo (texto da UI) |
|---|---|---|
| `threshold` | `x_i > value` (direção `up`; `<` para `down`) | "tempo de curso > 230 ms" |
| `pct_of_baseline` | `x_i > value · m0` | "tempo de curso > 110 % da referência (220,0 ms)" |
| `rate_of_change` | inclinação `b` da reta de P-5 sobre as últimas `K = 10` janelas válidas `> value` | "subindo mais de 0,5 ms a cada 10 000 ciclos" |

- **`sustained_for`** (padrão 2): a condição precisa valer em `n` janelas **válidas consecutivas**. Também pode ser
  dado como duração (`"16h"`), convertida em `ceil(duração / janela)`. Janelas inválidas são ignoradas (P-1).
- **Desarme**: a regra desarma depois de `sustained_for` janelas válidas consecutivas sem a condição. O desarme não
  fecha alerta sozinho (P-8).
- **Unidades da taxa**: por uso (ms por ciclo; A por hora de operação), nunca por hora de relógio — parada de fim de
  semana não pode parecer "melhora". A UI converte para "por dia" com o ritmo de P-7.
- Padrões sugeridos ao ligar a preditiva numa posição (o usuário edita): `stroke_time` → `pct_of_baseline 1,10`
  `warning`; `current` → `threshold` = corrente nominal de placa `critical` e `pct_of_baseline 1,15` `warning`;
  `vibration` → `threshold` nos limites de zona da ISO 20816 da classe da máquina (B/C `warning`, C/D `critical`;
  ver "Pontos em aberto"); `temperature` → `threshold` do limite do mancal/classe de isolação `critical`.

---

## P-4 — Detectores estatísticos de deriva e anomalia

Entrada comum: `z_i = clip((x_i − m0) / σ, −4, +4)` (para direção `down`, usa-se `−z`). O **recorte em ±4** impede
que um único pico (ex.: queda momentânea de ar comprimido) dispare os acumuladores: ele aparece como anomalia
pontual, não como deriva (F1, bloco 25: `z` bruto = 6, recortado 4; EWMA 0,74 < 1; CUSUM 3,5 < 5).

| Detector | Fórmula | Parâmetros padrão | Dispara (`watch`) quando | Detecta |
|---|---|---|---|---|
| **EWMA** | `E_i = λ·z_i + (1 − λ)·E_{i−1}`, `E = 0` ao estabelecer a base | `λ = 0,2`, `L = 3` | `E_i > L·√(λ/(2 − λ)) = 3·√(0,2/1,8) = 1,0` | deslocamento sustentado ≳ 1 σ |
| **CUSUM** (unilateral) | `S_i = max(0, S_{i−1} + z_i − k)`, `S = 0` ao estabelecer a base | `k = 0,5`, `h = 5` | `S_i > h` | deriva lenta e persistente (acumula pequenos excessos) |
| **z robusto móvel** | `r_i = (x_i − mediana(janela)) / max(1,4826·MAD(janela), σ_piso)` sobre as **20 janelas válidas anteriores** (desde o último reinício, sem a atual) | limite 3,5 | ponto é **anomalia** se `r_i > 3,5`; alerta `watch` se ≥ **3 anomalias nas últimas 10** janelas | saltos e falhas intermitentes (válvula agarrando); **não** vê deriva lenta, porque a janela anda junto |

- Limites assintóticos (fixos), não os limites variáveis do início da carta EWMA: mais simples de explicar e um pouco
  menos sensíveis nas primeiras janelas.
- Taxa de falso alarme esperada com ruído normal independente: ARL₀ ≈ 500 janelas para EWMA(0,2; 3) e ≈ 465 para
  CUSUM(0,5; 5) (valores de tabela de controle estatístico de processo). Com janelas de 8 h isso é ~1 alarme `watch`
  por sinal a cada 5–6 meses no pior caso; o piso de σ reduz isso na prática. Por isso **detector estatístico sozinho
  gera só `watch`** (resumo diário, sem OS). Quem gera OS são as regras de engenharia (P-3) e a RUL (P-6).
- Os detectores rodam a cada janela fechada, em ordem de índice; o estado (`E`, `S`, sequências das regras) é uma
  dobra sobre a série — recalcular desde a linha de base dá o mesmo resultado (P-12).

---

## P-5 — Tendência (inclinação) pelo uso

Reta de mínimos quadrados `x = a + b·u` sobre as últimas **`K = 10` janelas válidas** desde o último reinício
(pode incluir janelas da linha de base), onde `u` é o uso da posição no fim da janela (ciclos ou horas de operação).

- `b = Σ(u − ū)(x − x̄) / Σ(u − ū)²`; `SE(b) = √( Σ resíduo² / (K − 2) / Σ(u − ū)² )`.
- **Tendência significativa** ⇔ `b > 0` (direção ruim) **e** `b / SE(b) > 2`. Sem significância → RUL por condição
  `null` com `reason = no_significant_trend` (F2, dia 50).
- Menos de K janelas desde o reinício → `reason = insufficient_data`.
- `x̂ = a + b·u_atual` (valor ajustado agora) é o que se compara com o limite de falha — não o último ponto cru.

---

## P-6 — Vida remanescente pela condição (RUL de condição) e faixa de incerteza

Limite de falha funcional `L_f` por (posição, sinal), configurado: `stroke_time` → padrão **1,20 · m0** (domain.md
§5.1: "`stroke_time` > 120 % da referência"; preferir o *timeout* de curso do CLP quando conhecido); `current` →
corrente nominal de placa; `vibration` → limite C/D da zona; `temperature` → limite do mancal/isolação.

```
restante_uso      = (L_f − x̂) / b                         ciclos ou horas de operação
restante_uso_low  = (L_f − x̂) / (b + 2·SE)                pessimista (degrada mais rápido)
restante_uso_high = (L_f − x̂) / (b − 2·SE)                otimista (b − 2·SE > 0 pela regra de significância)
dias       = ceil(restante_uso / ritmo − ε)                C18
dias_low   = floor(restante_uso_low / ritmo_alto + ε)      arredonda para o lado seguro
dias_high  = ceil(restante_uso_high / ritmo_baixo − ε)
data = hoje + dias (fuso da planta, C17)                   idem data_low, data_high
```

- `x̂ ≥ L_f` → `rul = 0`, `dias = 0`, e a UI diz "limite de falha atingido".
- A faixa combina as **duas incertezas**: a da tendência de degradação (±2·SE na inclinação) e a do ritmo de uso
  (P-7, ritmo baixo/alto). `dias_high` grande é normal no começo de uma deriva (F2, dia 58: 80 dias, faixa 39–528);
  a UI mostra "> 1 ano" acima de 365.
- **Gatilhos de severidade pela RUL** (detector `RUL`): `dias ≤ 30` → `warning`; `dias_low ≤ 7` → `critical`.
- **Combinação com a vida nominal** (kpis.md §4.2, S4-12): a API devolve `rul_nominal` (pelo uso e vida de catálogo)
  e `rul_condition` (este), e `rul_effective` = o de **menor data**, com `rul_source = nominal | condition`. A tela
  de componentes ordena por `rul_effective`. Sem RUL de condição, vale a nominal (como hoje).
- F1 (cilindro, `L_f = 240 ms`), bloco 50: `b = 9,394·10⁻⁵ ms/ciclo` (0,94 ms a cada 10 000 ciclos),
  `SE = 1,492·10⁻⁵`, `x̂ = 219,73 ms` → restante 215 806 ciclos (faixa 163 774 – 316 295); ritmo 20 000/dia →
  **11 dias, 09/02/2027 (faixa 06/02 – 14/02)**. A falha real (planta virtual) foi em **08/02/2027**: dentro da faixa.

---

## P-7 — Ritmo de uso e plano de produção

- **Ritmo** (como kpis.md §4.2/C17): uso da posição nas 14 × 24 h que terminam no instante da avaliação ÷ 14.
  **Faixa do ritmo**: as duas metades de 7 dias; `ritmo_baixo = min`, `ritmo_alto = max` (fins de semana e feriados
  entram — F2, dia 58: 14 dias com um feriado → 18,57 h/dia, faixa 17,14 – 20,0).
- **Plano de produção** (roadmap Fase 4, "previsão a partir do plano de produção"): se a máquina tem plano diário
  (`peças planejadas` por dia; uso planejado = peças × `cycles_per_part`, ou horas planejadas para sinais por hora),
  a data é o **primeiro dia em que o uso planejado acumulado ≥ restante_uso**; depois do horizonte do plano continua
  o ritmo de 14 dias. Vale para a RUL de condição **e** para a RUL nominal/planos (substitui o ritmo histórico
  quando houver plano, como kpis.md §4.2 já previa). A resposta diz `rate_source = history_14d | production_plan`.
- Exemplo (no JSON, `production_plan_example`): F1 no bloco 50 (sexta 29/01/2027), restante 215 806 ciclos, plano
  de 30 000 ciclos por dia útil e 0 no fim de semana → acumulado 30 000 (seg 01/02) … 210 000 (ter 09/02),
  240 000 (qua 10/02) ≥ 215 806 → **10/02/2027**; pelo ritmo histórico seria 09/02.

---

## P-8 — Alerta preditivo: severidade, unicidade e ciclo de vida

**Um alerta por (posição, sinal)** enquanto não estiver fechado. Novos disparos na mesma (posição, sinal) viram
**evidência** do alerta aberto (`triggers`: detector/regra, primeira e última janela, valor, limite), não alertas
novos. O alerta guarda `opened_at` (primeiro disparo) e o instante de cada **escalonamento** de severidade.

| Severidade | Rótulo | Vem de | Efeito |
|---|---|---|---|
| `watch` | Atenção | só detector estatístico (EWMA, CUSUM, z móvel) | resumo diário do planejador; não sugere OS |
| `warning` | Alerta | regra de engenharia `warning` (P-3) ou RUL ≤ 30 dias | sino + e-mail (planejador, gerente); **sugere OS preditiva**; plano de condição gera rascunho (P-9) |
| `critical` | Crítico | regra `critical` ou `dias_low` da RUL ≤ 7 | push para planejador e técnico de plantão da área (mesmo canal do chamado, maintenance_workflows.md §5) |

- Severidade só **sobe** automaticamente (nunca desce sozinha); cada subida notifica.
- **Texto explicável** (DEVE, gerado pelo servidor, em português):
  "Cilindro B3 — tempo de curso 206,5 ms (referência 200,0 ± 2,0 ms desde 14/01): +3,25 σ. EWMA 1,34 > 1,00;
  CUSUM 6,0 > 5,0. Falha projetada em 26 dias (17–50) ao atingir 240 ms."

**Estados** (transição inválida → **422** com as permitidas, como S4-5; toda transição grava quem/quando/comentário):

```
open ──► acknowledged ──► converted ──► closed
  │            │              │  ▲
  │            │              └──┼──► acknowledged   (OS cancelada por motivo ≠ superseded)
  └────────────┴──────────────────► closed            (resolução obrigatória)
```

| Transição | Quem | Regras |
|---|---|---|
| `open → acknowledged` | planejador, gerente, técnico | "vi e vou avaliar" |
| `open/acknowledged → converted` | planejador, gerente (ou automático pelo plano de condição, P-9) | cria **uma** OS `predictive` ligada (`source_alert_id`); alerta guarda `work_order_id` |
| `converted → closed` | automático | ao concluir a OS: `finding = defect_confirmed` → resolução `confirmed`; `no_defect` → `false_alarm` |
| `converted → acknowledged` | automático | OS cancelada com motivo ≠ `superseded` (o alerta continua sem ação) |
| `open/acknowledged/converted → closed` | automático | falha `critical`/`degraded` registrada na posição enquanto o alerta está aberto → resolução `failed_before_action` (a OS preditiva não iniciada é cancelada `superseded` pela corretiva) |
| `closed (failed_before_action)` → corrigido | automático | o fechamento segue o registro de falha **atual**: `failed_at` corrigido move o fechamento para o novo instante; falha removida da posição (ou rebaixada) reabre o alerta no estado anterior. O evento antigo fica no histórico com origem `failure_void`. Um alerta que a dobra deixa de reproduzir (aberto sobre o fechamento errado) e que só o motor e falhas tocaram é fechado `duplicate` (P-12) |
| `open/acknowledged → closed` | planejador, gerente | resoluções `false_alarm`, `rebaseline` (reinicia a linha de base sem aquecimento, P-2), `duplicate`, `known_cause` (ex.: teste de engenharia) |
| `open → closed` | automático | `auto_cleared`: nenhum disparo ativo por 6 janelas válidas seguidas **e** ninguém reconheceu |

- `closed` é final. Depois de `false_alarm`, a mesma (posição, sinal) fica **silenciada por 7 dias** para a mesma
  severidade (nova abertura só se a severidade for maior); depois de `rebaseline` os detectores voltam a `learning`.
- Permissões de leitura: `maintenance_planner`, `manager`, `technician`, `production_supervisor` (+ admin), filtrado
  por tenant; o operador vê só o selo "condição: atenção/alerta" na máquina. Configurar sinais/regras/limites e
  `rebaseline`: planejador, gerente, admin.

---

## P-9 — Do alerta à OS preditiva; plano de condição; PMP

**Conversão** (`POST /api/predictive/alerts/{id}/convert`): cria `WorkOrder` com `type = predictive`, estado
`approved` (quem converte é quem aprova), posição/máquina do alerta, título e descrição gerados com o texto
explicável e o link do gráfico, `due_by = data_low` da RUL (sem RUL: hoje + 7 dias), `estimated_hours` do plano de
condição da posição (se houver) ou informado. A partir daí a OS segue o ciclo normal (S4-5).

**Plano de condição** (o gatilho "condição (Fase 4)" de domain.md §5.1): `MaintenancePlan` com
`condition_trigger = {feature, min_severity: warning}`, `wo_type = predictive`. Quando um alerta da posição atinge
`min_severity`:
- o servidor cria **uma** OS `draft` do plano (invariante S4-4: no máximo uma OS do plano antes de `completed`) e o
  alerta passa a `converted`;
- o estado do plano vira **pelo menos `due`**, com `due_trigger = condition` e `next_due_date = data_low` da RUL
  (ou hoje). Os outros gatilhos (ciclos, horas, dias) continuam valendo — "o que vier primeiro";
- concluir a OS zera o plano (§5.4) e, com `resets_baseline`, reinicia a linha de base.

**Conclusão de OS preditiva** com `source_alert_id`: campo **`finding` obrigatório** — `defect_confirmed` (exige um
`FailureRecord` com `severity = incipient` ou `degraded`, `detection_method = continuous_condition_monitoring`, modo
ISO 14224 da tabela de P-10) ou `no_defect`. Falha incipiente não entra no `n` do MTBF (domain.md §5.5).

**PMP** (kpis.md §3.1, sem mudar a fórmula): OS `predictive` contam como **planejadas**, inclusive as que não
acharam defeito (foram planejadas; o custo do falso alarme aparece à parte em `predictive_no_defect_hours`). OS
preditiva cancelada `superseded` não tem horas e não entra; a corretiva emergencial que a substituiu entra como não
planejada. Exemplo (JSON `pmp_with_predictive`): preventiva 60 h + preditivas 6 + 4 + 2 h + corretiva programada 3 h
= 75 h planejadas; emergencial 5 h → **PMP = 75 / 80 = 0,9375**; `predictive_no_defect_hours = 2,0`. Errado: tirar a
preditiva sem defeito (73/78 = 0,935897) ou contar OS ainda em execução (76,5/81,5 = 0,938650).
Cumprimento da programação: OS preditivas com `scheduled_for` entram como qualquer outra (S4-6).

---

## P-10 — Como medir se a preditiva funciona

Verdade de campo: falha real (`FailureRecord` `critical`/`degraded`) na mesma posição com modo compatível com o sinal:

| Sinal | Modos ISO 14224 compatíveis (domain.md §5.5) |
|---|---|
| `stroke_time` | `PDE` (curso lento), `INL`, `ELP`, `DOP`¹ |
| `current` | `OHE`, `PDE`, `BRD`, `FTS` |
| `vibration` | `VIB`, `NOI`, `BRD` |
| `temperature` | `OHE`, `BRD` |

¹ ver "Revisão ISO 14224 (Sprint 5)" em domain.md, item 8.

| Métrica | Definição | Alvo inicial (calibrar no piloto) |
|---|---|---|
| **Antecedência** (lead time) | `failed_at − opened_at` do alerta; também por severidade (`failed_at − instante em que virou warning/critical`) | mediana ≥ 7 dias em `warning`; ≥ `min_useful_lead` = 24 h para contar como detectada |
| **Verdadeiro positivo** | falha compatível em `(opened_at, opened_at + H]`, `H = 30 dias`; ou OS do alerta com `defect_confirmed` | — |
| **Excluído** | alerta fechado `duplicate` (repete a detecção de outro alerta — inclusive o substituído pelo recálculo quando a data de uma falha é corrigida) nunca conta, nem como VP; `known_cause` sem falha em H não conta | — |
| **Falso alarme** | alerta sem falha compatível em H e sem defeito confirmado (`false_alarm`, `rebaseline`, `no_defect`, `auto_cleared`) | ≤ 1 por posição-ano em `≥ warning` |
| **Precisão** | VP / (VP + FA), por severidade mínima | ≥ 0,7 em `≥ warning` |
| **Detecção** (recall) | falhas com alerta aberto ≥ 24 h antes ÷ falhas compatíveis em posições monitoradas | ≥ 0,8 |
| **Taxa de falso alarme** | FA ÷ tempo monitorado × 365 (posição-ano). Tempo monitorado = com linha de base estabelecida, até a falha ou o fim dos dados, **sem** os períodos de reaprendizado | ver acima |
| **Cobertura da faixa de RUL** | fração dos pontos de controle em que a data real da falha está em `[data_low, data_high]` | ≥ 0,8 |

Cenário F (2 posições): F1 abre alerta 17,5 dias antes da falha (`warning` = 420 h; `critical` 264 h); a regra de
`% da base` sozinha avisaria com 216 h e o limite absoluto de 230 ms só com **96 h** — é a diferença entre programar
a troca e apagar incêndio. F2 abre `watch` e é falso alarme (mudança de carga). Precisão 0,5 em `≥ watch` e 1,0 em
`≥ warning`; tempo monitorado 24,5 + 49 = 73,5 dias → **4,97 falsos alarmes por posição-ano em `watch`** e 0 em
`warning`. (Um `watch` com essa taxa é aceitável porque não gera OS; um `warning` não seria.)

`GET /api/predictive/evaluation?start&stop[&line_id]` devolve essas métricas para o gerente (tela "Preditiva →
Desempenho"), a partir dos alertas e falhas reais do tenant.

---

## P-11 — A planta virtual como verdade de campo

No campo a preditiva *impede* a falha (a OS troca a vedação), então a antecedência real nunca é observada. A planta
virtual (`edge/virtual_plant/`) roda **até a falha**, com seed, e é a régua do Sprint 5. Requisitos para o
`edge-engineer`:

1. **Perfis de degradação por posição** (determinísticos com a seed): saudável até `onset`, depois deriva linear
   (ou acelerada) do sinal — hoje só existe o A2 com +20 ms/1 000 golpes desde o início (≈ +40 ms/dia, rápido
   demais para medir antecedência; manter como "demo rápida" e acrescentar um perfil lento).
2. **Falha funcional**: quando o valor nominal atinge `L_f` o CLP simulado entra em falha (`fault_state` 4xx
   pneumática → `EQP-03`, modo `PDE`), e a verdade registra `failure`.
3. **Deriva sem falha**: corrente do motor do transportador sobe com a mudança de produto (F2) — o caso de falso
   alarme.
4. **Verdade exportada** em `snapshot()["truth"]["degradations"]`: `[{position, feature, onset_s, failure_s|null,
   profile}]`, para o avaliador calcular as métricas de P-10 contra os alertas da API.
5. Os sinais analógicos usados pela preditiva entram no **contrato de determinismo** (hoje rotação/corrente usam
   gerador à parte e ficam fora).
6. Execução acelerada (`--speed`) para 60 dias simulados em minutos.

---

## P-12 — Determinismo, armazenamento, API e telas

- **Pipeline**: job por janela fechada (e recuperação de atraso): Influx → feature (P-1) → Postgres
  `condition_features(tenant_id, position_id, feature, i, end_at, x, n, valid, phase)` → detectores → alertas.
  `POST /api/predictive/recompute` (admin) recalcula desde a última linha de base e DEVE reproduzir os mesmos
  alertas (mesmos instantes e evidências); o teste usa o cenário F.
- **Retomada dos blocos de ciclos** (correção Sprint 5): o cursor é o instante da leitura do contador que fechou o
  último bloco (+ ciclos que ela ultrapassou, `carry`). Essa leitura pertence ao bloco anterior: na retomada ela é a
  **referência** (`prev_value`) e não é contada de novo — senão cada bloco retomado fecha um ciclo antes (bloco i
  tem de ser exatamente os ciclos (i−1)·N+1 … i·N). O valor de referência é a última leitura até o cursor
  (lookback de 366 dias); um contador **sem nenhuma leitura anterior** usa a sua 1ª leitura como referência (ela
  não conta ciclo — não há como saber quantos ciclos ela representa).
- **Custo por tick** (Sprint 5b): o bloco inteiro é resolvido **dentro do Influx** — enquanto está aberto, só
  agregados (1ª/última leitura, nº de quedas do contador, instante e valor da leitura que atinge o alvo do bloco);
  quando fecha, `count()` e mediana exata (`quantile(q: 0.5, method: "exact_mean")` = `statistics.median`) dos
  tempos de curso em (início, fim]. Nenhuma linha bruta de bloco é lida pelo servidor (antes: ~80 mil linhas por
  monitor analisadas no laço de eventos, 74 % do laço no py-spy do QA). Janelas de tempo ainda leem as leituras da
  janela (média ponderada no tempo), mas o CSV é analisado numa *thread* e a conta pesada também.
- **Motor fora do processo da API** (Sprint 5b): `PREDICTIVE_WORKER=process` (padrão) — o app inicia e supervisiona
  `python -m src.worker`, um processo só do motor preditivo (CPU e GIL próprios); `external` — o app não inicia
  (rodar `python -m src.worker` como serviço próprio, ex. `worker` no compose, mesma imagem do server); `inline` —
  tarefa no laço do app (só testes/depuração). As mensagens WebSocket do motor chegam aos navegadores pelo app via
  `NOTIFY fm_ws` no Postgres (`websocket/relay.py`). `/process` e `/recompute` continuam na API (sob demanda).
- **Dados atrasados** (buffer *store-and-forward* do gateway, backlog da ingestão) — Sprint 5b, substitui a regra
  anterior ("janelas válidas não são reescritas"):
  * a ingestão grava por tag (`ingest_watermarks`) o maior `ts` gravado e o intervalo das leituras que chegaram
    mais de `PREDICTIVE_GRACE_S` depois do próprio `ts`; o motor transfere essas marcas para `predictive_dirty` (por
    monitor afetado: janelas de tempo — qualquer tag da máquina; blocos — a tag de curso ou o contador da posição);
  * janelas de tempo: toda janela do segmento que o intervalo atrasado pode mudar (uma leitura vale até o limite de
    buraco de dados) é **reextraída, válida ou não**; as horas de operação corrigem o `u` das seguintes;
  * blocos de ciclos: os blocos do segmento que terminam em/depois da leitura atrasada são **refeitos** — o cursor
    volta para o fim do bloco anterior (`condition_features.carry` guarda os ciclos excedentes de cada bloco) e a
    extração os corta de novo, exatamente como uma extração completa (enquanto não termina, a dobra não roda, para
    não fechar alertas de blocos ainda não refeitos);
  * em seguida os detectores são redobrados (EWMA, CUSUM, regras, RUL) e os alertas reconciliados: um alerta que a
    dobra deixa de reproduzir e que só o motor tocou é fechado `duplicate`; um em que alguém agiu é reportado
    (`mismatch`). Determinístico: incremental com qualquer ponto de corte e dados atrasados == extração completa
    (`server/tests/test_predictive_cycle_cutpoints.py`, propriedade com sementes aleatórias);
  * as marcas ficam no Postgres (sobrevivem a reinício e funcionam com o motor em outro processo).
    `POST /api/predictive/recompute` com `reextract_all = true` reextrai todo o segmento do Influx e informa
    `windows_reextracted` (blocos/janelas diferentes do armazenado) e `same` (alertas, inclusive instante, motivo e
    origem do fechamento — `closed_by`). Leva minutos num monitor longo (F1, 70 blocos: 94–352 s): usar
    `background = true` (corpo ou `?background=true`) → `202 {job_id}` e consultar
    `GET /api/predictive/recompute/jobs/{job_id}` (`running`/`done`/`failed`, `result` = o mesmo corpo do modo
    síncrono; só o admin do tenant vê o job). Um recálculo preditivo por tenant por vez (`409` com o `job_id`).
  * Uso e paradas: o processamento de uso é por leitura, idempotente e ignora leitura fora de ordem (o contador é
    cumulativo, nada se perde); o detector de paradas já retrocede (`downtime_detector_states.late_from`).
- **Falhas e alertas** (Sprint 5b): registrar ou editar uma falha (`failed_at`, severidade, posição) reavalia na
  mesma requisição o fechamento `failed_before_action` dos alertas das posições envolvidas (antiga e nova) e redobra
  os detectores; uma alteração feita por fora da API é encontrada pelo `/recompute` (`same = false`).
- **Ingestão MQTT sem perda e com contrapressão** (Sprint 5b, QA F1 a 500×: janela 45 com n = 9 999, leitura de
  `stroke_time` faltando no Influx): pipeline limitado — filas FIFO por (tenant, tag) (ordem por tag preservada),
  *pool* fixo de workers (`MQTT_MAX_CONCURRENCY`), escrita no Influx em lote com repetição do lote inteiro em falha
  transitória, no máximo `MQTT_MAX_INFLIGHT` leituras dentro do servidor. O servidor assina com QoS 1 numa **sessão
  persistente** (`clean_session = false`, `MQTT_CLIENT_ID` estável, padrão `fm-server-<POSTGRES_DB>`) e só confirma
  (PUBACK, na ordem de recebimento **de cada tag** — uma leitura presa de um tenant não segura as confirmações dos
  outros) a leitura **concluída** (gravada, ou em dead letter): cheio, o broker para na
  janela `max_inflight_messages` e guarda o resto (`max_queued_messages`/`max_queued_bytes`), também durante um
  reinício do servidor; o que foi recebido e não concluído é reentregue (processamento idempotente). Falha
  transitória (timeout, Postgres/Influx fora) é repetida por até `MQTT_TRANSIENT_RETRY_S`; depois disso — ou erro
  não transitório — a leitura vai para `ingest_dead_letters` (métrica `fm_ingest_dead_letters_total`, log
  `ingest.dead_letter`; reprocessar com `python -m src.services.ingest.dead_letter --replay`, que ao terminar grava
  as marcas de dado atrasado das leituras reprocessadas — o motor reextrai as janelas/blocos). Só tag inexistente /
  gateway revogado / payload inválido é recusado (`fm_mqtt_messages_total{result}`). `fm_ingest_dropped_total`
  tem de ser 0. Prova: `e2e/sprint5b/ingest_load.py` (100 mil leituras do F1 publicadas na velocidade máxima ==
  contagem no Influx == replay lento, blocos == F1, latência ao vivo com o motor rodando).
- **Capacidade de ingestão** (medida no `e2e/sprint5b/ingest_load.py`, 1 processo, Postgres + Influx locais numa
  máquina compartilhada de 4 núcleos, broker com `max_inflight_messages 100`): 100 001 leituras do F1 (1 tag de
  curso + 1 contador) publicadas a ~3 400/s foram gravadas em ~220 s (**~450 leituras/s**, sem perda, fila do
  servidor ≤ 100 = janela do broker); as leituras de uma tag que se acumulam na fila são processadas juntas (uma
  transação de uso por lote, `usage.processor.process_readings`). `server/tests/test_ingestion_throughput.py`
  (8 tags de curso + 2 contadores, pool NullPool dos testes): ~270 leituras/s (era ~20–115/s com uma transação por
  leitura). Com o motor preditivo extraindo janelas pesadas (5 dias de corrente a 1 Hz) e leituras atrasadas
  sujando janelas a cada 3 s, a latência ao vivo (publicação → WebSocket) ficou em p95 = 8 ms (processo próprio do
  motor) e 39 ms (motor no laço da API, `inline`). **F1 completo pelo MQTT** (`FULL_F1=1`: 70 blocos, 1 400 001
  leituras publicadas a ~3 200/s): tudo gravado (~440/s), 70 blocos exatos (n = 10 000, mediana, fim = instalação
  + i·12 h), linha de base, traço dos detectores, alerta (janela 35, escalonamentos, antecedência 420/264 h) e os 4
  pontos de RUL iguais ao JSON; `recompute` com `reextract_all` idêntico; nenhuma perda no servidor nem no broker.
- **Tudo por tenant** (`tenant_id` em toda tabela e consulta) e teste de isolamento em todo endpoint.
- **API proposta** (o `backend-engineer` pode ajustar nomes, não semântica):

| Método e rota | Uso |
|---|---|
| `GET /api/predictive/monitors?plant_id&line_id&machine_id&position_id` | por (posição, sinal): estado (`learning` com progresso, `monitoring`, `disabled`), linha de base, último `x`, `z`, EWMA, CUSUM, RUL de condição, alerta aberto |
| `POST/PATCH /api/predictive/monitors`, `/api/predictive/rules` | configurar sinal, janela, `L_f`, regras |
| `POST /api/predictive/monitors/{id}/rebaseline` | reinício manual (motivo obrigatório) |
| `GET /api/predictive/features?position_id&feature&start&stop` | série para o gráfico (com fase, limites, EWMA, CUSUM) |
| `GET /api/predictive/alerts?state&severity&line_id&position_id` · `GET …/{id}` | lista e detalhe com evidências e texto explicável |
| `POST /api/predictive/alerts/{id}/transition` · `POST …/{id}/convert` | ciclo de vida (P-8) e conversão em OS (P-9) |
| `GET /api/predictive/evaluation?start&stop&line_id` | métricas de P-10 |

- **Telas**: `/maintenance/predictive` (lista de alertas por severidade; detalhe com gráfico do sinal, faixa
  `m0 ± 3σ`, limites, EWMA/CUSUM, projeção da RUL com faixa e botão "Gerar OS preditiva"); aba **Condição** na
  gaveta da posição (Sprint 3) com o mesmo gráfico; no celular, a OS preditiva mostra o gráfico e o texto do alerta.
  Pergunta de toda manhã (kpis.md §5) nova: *"O que está degradando e quando vai falhar?"* → Preditiva.

---

## Configuração padrão (bloco lido pelos testes)

```json predictive-defaults
{
  "baseline_min_windows": 20,
  "baseline_min_span_h": 24,
  "warmup_windows": {"stroke_time": 5, "current": 1, "vibration": 1, "temperature": 1},
  "window": {"stroke_time": {"cycles": 1000}, "current": {"hours": 8}, "vibration": {"hours": 8}, "temperature": {"hours": 8}},
  "sigma_floor_frac": 0.01,
  "z_clip": 4.0,
  "ewma_lambda": 0.2,
  "ewma_L": 3.0,
  "cusum_k": 0.5,
  "cusum_h": 5.0,
  "rolling_window": 20,
  "rolling_z_limit": 3.5,
  "anomaly_min": 3,
  "anomaly_of": 10,
  "trend_windows": 10,
  "trend_t_min": 2.0,
  "rul_warning_days": 30,
  "rul_critical_days": 7,
  "sustained_for": 2,
  "auto_close_after_windows": 6,
  "false_alarm_snooze_days": 7,
  "evaluation_horizon_days": 30,
  "min_useful_lead_h": 24,
  "stroke_time_failure_limit_of_baseline": 1.2
}
```

O cenário F usa estes valores, exceto janela e aquecimento (maiores/menores para caber no JSON) — o teste
`test_scenario_f.py::test_detectors_match_documented_defaults` confere.

## Pontos em aberto (decisão antes ou durante o Sprint 5)

1. **Contexto de produto na linha de base** (ex.: corrente depende do produto): o Sprint 5 tem uma base por
   (posição, sinal); o F2 mostra o falso alarme que isso causa e a saída (`rebaseline`). Base por
   (posição, sinal, produto) fica para depois, se o piloto mostrar muitos `rebaseline`.
2. **Zonas de vibração**: os limites da ISO 20816-3 (antiga 10816-3) dependem do grupo da máquina (potência,
   fundação rígida/flexível); valores típicos citados (1,4 / 2,8 / 4,5 mm/s para máquinas médias em base rígida)
   **precisam ser conferidos na norma** antes de virarem padrão. Até lá: sem padrão, o usuário informa.
3. **Alvos de P-10** são iniciais; calibrar com a planta virtual e o piloto.
4. **Alerta aberto quando o componente é trocado** (QA Sprint 5): a instalação reinicia a linha de base (P-2), mas
   o alerta aberto pelo componente substituído continua `open` (é carregado para o novo segmento). P-8 não diz o que
   fazer com ele — decisão do `reliability-engineer` (ex.: fechar com uma resolução própria, o que exige migration
   da restrição `ck_predictive_alerts_resolution`). Até lá o planejador fecha à mão.
5. **Weibull com censura** para MTTF (kpis.md §2.4 promete para a Fase 4): fica para o relatório de vida por
   fabricante/modelo; não é pré-requisito da preditiva.
