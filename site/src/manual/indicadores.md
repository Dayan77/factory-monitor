---
title: "Indicadores em linguagem simples"
description: "O que cada número do FactoryConnect quer dizer, como é calculado, um exemplo e os erros de leitura mais comuns."
ordem: 2
papel: todos
---

# Indicadores em linguagem simples

Cada indicador abaixo tem: **o que é** (em uma frase), **como é calculado** (a fórmula, com a referência à
definição oficial em `docs/kpis.md`, que segue as normas ISO 22400 e ISO 14224), **um exemplo** com
números e **como não se enganar**. As referências do tipo "kpis.md §1.5" apontam para a seção da especificação
técnica, onde estão os casos de borda completos.

Convenções que valem para todos:

- Percentuais aparecem com uma casa decimal (`75,4 %`). O sistema calcula com o número completo e só arredonda
  na tela.
- **"—" não é zero.** Quando um indicador não pode ser calculado, a tela mostra "—" e o motivo (veja
  [Quando aparece "—"](/docs#quando-aparece-um-traco)).
- **Nada é média de porcentagens.** O OEE do dia não é a média dos OEEs dos turnos; o da fábrica não é a média
  das linhas. O sistema soma os tempos e as peças e refaz a conta (kpis.md §1.6). Por isso os números "fecham".

---

## Eficiência da produção

<a id="tempos"></a>
### Os tempos do turno (a base de tudo)

O turno é dividido assim (kpis.md §1.1, norma ISO 22400):

| Tempo | O que é | Exemplo (turno de 8 h) |
|---|---|---|
| **Tempo programado** (POT) | a soma dos turnos cadastrados no calendário | 06:00–14:00 = 480 min |
| **Paradas planejadas** (PDOT) | refeição, café, reunião, preventiva **programada no calendário**, sem demanda | refeição 30 min |
| **Tempo planejado para produzir** (PBT) | programado − planejadas − tempo sem dados | 450 min |
| **Paradas não planejadas** (ADOT) | falhas, setup, falta de material, falta de operador… (≥ 2 min) | 60 min |
| **Tempo rodando** (APT) | planejado para produzir − não planejadas | 390 min |

Duas regras que surpreendem quem chega:

- **Setup é parada não planejada** (perde disponibilidade), mesmo sendo esperado. Ele aparece separado no Pareto
  com o código `SET-*`, para você enxergar quanto custa.
- **Preventiva só é planejada se estava no calendário antes de o turno começar.** Feita sem estar no calendário,
  ela é `MNT-02` (não planejada).

<a id="oee"></a>
### OEE — eficiência global do equipamento

**O que é**: de todo o tempo que a máquina deveria estar produzindo, quanto virou peça boa na velocidade ideal.

**Como é calculado** (kpis.md §1.5): `OEE = Disponibilidade × Desempenho × Qualidade`, que é o mesmo que
`tempo ideal das peças boas ÷ tempo planejado para produzir`.

**Exemplo** (o "E1" da especificação): prensa, turno de 8 h, refeição de 30 min, 60 min de paradas, 700 peças
das quais 679 boas, tempo de ciclo ideal de 30 s.

- Disponibilidade = 390 / 450 = **86,7 %**
- Desempenho = (700 × 0,5 min) / 390 = **89,7 %**
- Qualidade = 679 / 700 = **97,0 %**
- **OEE = 75,4 %** (339,5 min de peças boas em 450 min planejados)

**Como não se enganar**:

- Um OEE de 100 % não existe na prática; classe mundial é em torno de 85 %. Compare a máquina com ela mesma ao
  longo das semanas.
- Turno sem produção mas com tempo planejado tem **OEE 0 %** (zero real: a máquina não gerou valor) e Qualidade "—"
  (não há peça para avaliar).
- O OEE de dois turnos juntos **não** é a média: turno A 75,4 % e turno B 92,5 % dão **83,7 %** no dia, não 84,0 %,
  porque o turno B tinha menos tempo planejado (kpis.md §1.6, exemplo E2).

<a id="disponibilidade"></a>
### Disponibilidade (A)

**O que é**: a fração do tempo planejado em que a máquina **estava rodando**.
**Fórmula** (kpis.md §1.2): `tempo rodando ÷ tempo planejado para produzir`.
**Exemplo**: 390 / 450 = 86,7 %.
**Como não se enganar**: micro-paradas (menos de 2 min) **não** reduzem a disponibilidade — elas entram no
desempenho. Parada que atravessa a troca de turno é dividida entre os dois turnos.

<a id="desempenho"></a>
### Desempenho (P)

**O que é**: enquanto rodava, a máquina produziu na velocidade ideal?
**Fórmula** (kpis.md §1.3): `Σ (tempo de ciclo ideal × peças produzidas) ÷ tempo rodando`.
**Exemplo**: 700 peças × 30 s = 350 min de trabalho ideal em 390 min rodando = 89,7 %.
**Como não se enganar**:

- Desempenho **acima de 100 %** não é cortado: aparece com o aviso **"tempo de ciclo ideal suspeito"**. Quase
  sempre o tempo de ciclo cadastrado está mais lento que o real, ou o contador conta em dobro. Corrija o cadastro
  (máquina × produto) em vez de ignorar o aviso.
- O tempo de ciclo ideal é o **menor ciclo sustentável** de projeto, não a média histórica. Usar a média deixa
  o desempenho sempre perto de 100 % e esconde a perda de velocidade.
- Produto sem tempo de ciclo ideal cadastrado → Desempenho, Qualidade e OEE ficam "—" ("produto sem tempo de
  ciclo ideal").

<a id="qualidade"></a>
### Qualidade (Q)

**O que é**: das peças produzidas, quantas saíram boas de primeira.
**Fórmula** (kpis.md §1.4): `peças boas ÷ peças produzidas` (com mais de um produto, cada peça pesa pelo seu tempo
de ciclo ideal).
**Exemplo**: 679 / 700 = 97,0 %.
**Como não se enganar**: peça retrabalhada conta como **não boa** (é a qualidade de primeira passagem).

<a id="teep"></a>
### TEEP e carregamento

**O que é**: o OEE sobre as **24 horas** do dia, não só sobre o turno. Mostra quanto da capacidade instalada
está sendo usada.
**Fórmula** (kpis.md §1.7): `Carregamento = tempo planejado ÷ tempo calendário`; `TEEP = OEE × Carregamento`.
**Exemplo** (E3): dia com 870 min planejados em 1 440 min → carregamento 60,4 %; TEEP = 83,7 % × 60,4 % = **50,5 %**.
**Como não se enganar**: num feriado o OEE é "—" (não havia tempo planejado), mas o TEEP é **0 %** — o dia entra
na média do mês como um dia em que a máquina não gerou valor.

<a id="micro-paradas"></a>
### Micro-paradas

**O que é**: interrupções curtas, de 1 s até menos de **2 min** (padrão; o administrador pode ajustar de 30 s a
5 min por máquina). Não pedem motivo e são perda de **desempenho**.
**Regra** (kpis.md §1.8): duração **≥ 2 min exatos** é parada (pede motivo); menos que isso é micro-parada.
Paradas separadas por menos de 10 s de funcionamento são juntadas numa só antes de aplicar o limite.
**Indicadores**: número de micro-paradas por hora rodando e minutos perdidos por turno.
**Como não se enganar**: 30 micro-paradas de 1 min não aparecem em "Paradas", mas somam 30 min no Pareto. Uma
máquina "que nunca para" e tem desempenho baixo costuma ter muitas micro-paradas.

<a id="cadencia"></a>
### Cadência (peças por hora)

| Variante | Fórmula (kpis.md §1.9) | Para que serve |
|---|---|---|
| Realizada | peças ÷ horas planejadas para produzir | "quantas peças por hora a linha entregou" |
| Em operação | peças ÷ horas rodando | velocidade quando está rodando |
| Ideal | 3 600 ÷ tempo de ciclo ideal (s) | linha de referência do gráfico |

**Exemplo** (E5): 700 peças em 7,5 h planejadas = **93,3 peças/h**; em 6,5 h rodando = **107,7 peças/h**; ideal
3 600 / 30 = **120 peças/h**.
**Como não se enganar**: na linha, a cadência é a da **máquina de saída** (a última), não a soma das máquinas.
No mapa da planta, o número ao lado do marcador é a cadência realizada do **turno até agora**.

<a id="pareto"></a>
### Pareto de perdas

**O que é**: todas as perdas do período convertidas em **minutos**, da maior para a menor, com a porcentagem e o
acumulado. Responde "onde perdemos mais tempo?".
**Como é montado** (kpis.md §1.10): cada motivo de parada não planejada (minutos), micro-paradas, **perda de
velocidade** (`tempo rodando − tempo ideal produzido − micro-paradas`) e **perda de qualidade** (tempo ideal das
peças refugadas). A soma das perdas é o tempo planejado menos o tempo das peças boas.

**Exemplo** (E6, o turno do E1 com 18 min de micro-paradas): total 110,5 min.

| # | Perda | min | % | acumulado |
|---|---|---|---|---|
| 1 | Falha pneumática `EQP-03` | 25 | 22,6 % | 22,6 % |
| 2 | Perda de velocidade | 22 | 19,9 % | 42,5 % |
| 3 | Setup `SET-01` | 20 | 18,1 % | 60,6 % |
| 4 | Micro-paradas | 18 | 16,3 % | 76,9 % |
| 5 | Falta de material `MAT-01` | 15 | 13,6 % | 90,5 % |
| 6 | Qualidade (refugo) | 10,5 | 9,5 % | 100,0 % |

**Como ler**: ataque as primeiras barras até uns 80 % do acumulado. Clique numa barra para ver a lista das
paradas daquele motivo. Paradas **sem motivo** (`UNK-01`) aparecem como uma barra própria — se ela for grande, o
Pareto não está dizendo a verdade sobre as causas: primeiro justifique as paradas.
**Como não se enganar**: com desempenho acima de 100 % a perda de velocidade aparece como 0 (e o aviso de tempo de
ciclo suspeito acompanha). Num período sem tempo planejado (feriado) não há Pareto.

<a id="cobertura"></a>
### Cobertura de dados

**O que é**: a fração do tempo programado em que o sistema recebeu dados da máquina (ou em que há uma parada
declarada). Abaixo de **95 %** aparece a faixa **"dados incompletos"** (kpis.md §0, decisão D5).
**Por que importa**: o tempo sem dados sai do tempo planejado — o sistema nunca supõe que a máquina rodou. Se as
peças contadas depois do buraco entram, o desempenho pode parecer melhor do que foi. Quando você sabe o que
aconteceu no buraco (queda de energia, por exemplo), **declare a parada** — ela passa a contar como parada não
planejada e a cobertura volta a 100 %.

<a id="disponibilidade-operacional"></a>
### Disponibilidade operacional (Ao) e inerente (Ai)

**O que é**: a disponibilidade do **equipamento** para a produção, olhando só o que é culpa dele.
**Fórmulas** (kpis.md §2.6):

- `Ao = (tempo requerido − indisponibilidade do equipamento) ÷ tempo requerido`. Tempo requerido = programado −
  refeições, reuniões, sem demanda e testes. Indisponibilidade = falhas `EQP-*` (inclusive a espera por peça ou
  técnico) + manutenção `PLN-03` e `MNT-02`.
- `Ai = MTBF ÷ (MTBF + MTTR)` — a disponibilidade "de projeto" do equipamento.

**Exemplo** (R6): 600 h requeridas, 12 h de falhas e preventiva → **Ao = 98,0 %**; com MTBF 180 h e MTTR 1 h,
**Ai = 99,4 %**.
**Como não se enganar**: Ao **não** é a disponibilidade do OEE. Setup, falta de material e falta de operador
derrubam a disponibilidade do OEE mas não a Ao (não são culpa do equipamento).

---

## Confiabilidade

Todos estes indicadores contam só falhas **críticas** e **degradadas** (a máquina perdeu a função, total ou
parcialmente). Falhas **incipientes** (início de degradação, ainda funciona) aparecem à parte. A janela padrão da
tela Confiabilidade é de **12 meses** (kpis.md §2, esclarecimento S4-8).

<a id="mtbf"></a>
### MTBF — tempo médio entre falhas

**O que é**: em média, quantas horas **rodando** a máquina aguenta entre uma falha e outra.
**Fórmula** (kpis.md §2.1): `horas de operação ÷ número de falhas`. Horas de operação = só o tempo com a máquina
rodando (não o tempo de turno).
**Exemplo** (R1): 720 h rodando no mês, 4 falhas → **MTBF = 180 h**.
**Como não se enganar**:

- Sem falhas no período o MTBF é "—" e a tela mostra "> 720 h sem falhas" — não é infinito.
- MTBF de uma semana com uma falha não diz nada. Olhe sempre o **n** (número de falhas) ao lado; use 3 a 12 meses.
- Para o grupo (todas as prensas, uma marca de motor), o sistema soma as horas e as falhas de todos — nunca tira a
  média dos MTBFs.

<a id="mcbf"></a>
### MCBF — ciclos médios entre falhas

**O que é**: o MTBF contado em **ciclos** em vez de horas. É o indicador certo para o que se desgasta por
movimento: cilindros pneumáticos, válvulas, garras, robôs.
**Fórmula** (kpis.md §2.2): `ciclos no período ÷ número de falhas`.
**Exemplo** (R2): cilindro A1 com 2 400 000 ciclos e 3 falhas → **MCBF = 800 000 ciclos**.
**Como não se enganar**: sem sensor de ciclo próprio, os ciclos são **estimados** por peças × ciclos por peça (a
garra que pega e solta duas vezes por peça faz 2 ciclos por peça) e a posição mostra o selo "estimado".

<a id="mttr"></a>
### MTTR — tempo médio para restabelecer

**O que é**: quanto tempo, em média, a produção fica sem a máquina quando ela quebra — da falha até voltar a
produzir, **incluindo** detectar, esperar o técnico, esperar a peça e consertar.
**Fórmula** (kpis.md §2.3): `Σ (restabelecida − falhou) ÷ número de falhas restabelecidas`.
**Exemplo** (R3): falhas de 30, 45, 90 e 75 min → **MTTR = 60 min**.
**Como não se enganar**:

- Uma falha que começou na pausa do café e só foi resolvida depois conta o tempo **inteiro**, desde que a falha
  apareceu (é o que a produção sentiu).
- Falha achada numa inspeção sem ter parado a produção entra no MTBF, mas **não** no MTTR. Falha ainda aberta
  fica fora até fechar.
- MTTR alto pode ser falta de peça ou de técnico, não reparo lento: confira na OS o tempo em espera.

<a id="mttf"></a>
### MTTF — vida média até a falha (peças trocadas)

**O que é**: para itens que se trocam em vez de consertar (rolamento, vedação, correia, válvula), a idade média na
falha, por fabricante e modelo.
**Fórmula** (kpis.md §2.4): média do uso desde a instalação dos componentes **removidos por falha**. Os removidos
sem falhar (troca preventiva) e os que ainda estão instalados são **suspensões**: não entram na média, mas são
mostrados ao lado.
**Exemplo** (R4): rolamentos falharam com 8 200, 9 600 e 10 400 h; dois foram trocados preventivamente com 7 000 e
9 000 h → **MTTF = 9 400 h**, "3 falhas, 2 suspensões".
**Como não se enganar**: com muitas trocas preventivas, a média simples **subestima** a vida (os itens trocados
"durariam mais"). Para decidir, use a [análise de Weibull](#weibull), que aproveita as suspensões. Com menos de 3
falhas aparece o aviso "amostra pequena".

<a id="taxa-de-falha"></a>
### Taxa de falha

**O que é**: o inverso do MTBF — falhas por hora de operação (a tela mostra por milhão de horas).
**Fórmula** (kpis.md §2.5): `falhas ÷ horas de operação`.
**Exemplo** (R5): 4 falhas em 720 h = 0,0056 falha/h = **5 555,6 por milhão de horas**.
**Como não se enganar**: aqui, sem falhas, a taxa é **0** (zero é a resposta certa: nenhuma falha).

---

## Gestão da manutenção

<a id="pmp"></a>
### PMP — percentual de manutenção planejada

**O que é**: das horas trabalhadas pela manutenção, quantas foram **planejadas** (e não "apagando incêndio").
**Fórmula** (kpis.md §3.1): `horas de OS planejadas ÷ horas totais de OS concluídas no período`. Planejadas =
preventiva + preditiva + melhoria + corretiva **programada**. Não planejada = corretiva **emergencial**.
**Exemplo** (M1): preventiva 90 h, preditiva 30 h, melhoria 10 h, corretiva programada 5 h, emergencial 25 h →
**PMP = 135 / 160 = 84,4 %**. Referência: ≥ 85 % (classe mundial ≥ 90 %).
**Como não se enganar**: OS preditiva que não achou defeito **continua planejada** (o custo do "alarme falso"
aparece à parte, em horas). Só contam OS **concluídas** no período — horas de uma OS ainda em execução não entram.

<a id="cumprimento"></a>
### Cumprimento da programação

**O que é**: das OS programadas para a semana (ou mês), quantas foram feitas **no dia programado**.
**Fórmula** (kpis.md §3.2): `OS programadas concluídas no prazo ÷ OS programadas`. A programação **congela na
segunda-feira 00:00**: OS programada depois disso é "encaixe" e não entra na conta. Cancelada porque outra OS a
substituiu (`superseded`) sai da conta; cancelada por qualquer outro motivo conta como **não feita**.
**Exemplo** (M2): 40 programadas, 34 no prazo, 3 atrasadas, 3 não feitas → **85,0 %**. Referência: ≥ 90 %.
**Como não se enganar**: concluir com um dia de atraso conta como **não cumprida** (tolerância padrão de 0 dia;
o administrador pode mudar).

<a id="backlog"></a>
### Backlog (semanas)

**O que é**: quantas semanas a equipe levaria para zerar o trabalho aprovado que está na fila.
**Fórmula** (kpis.md §3.3): `horas restantes das OS aprovadas, programadas, em espera e em execução ÷ capacidade
semanal` (técnicos × horas por semana − ausências previstas).
**Exemplo** (M3): 260 h em aberto, capacidade 130 h/semana → **2,0 semanas**.
**Como ler**: 2–4 semanas é saudável; menos de 2, a equipe pode estar ociosa (ou o planejamento está fraco); mais
de 4, a equipe não dá conta. Rascunhos gerados pelos planos e pedidos ainda não aprovados aparecem à parte e **não**
contam.

---

## Uso e vida de componentes

<a id="vida"></a>
### % de vida consumida

**O que é**: quanto da vida nominal (de catálogo) o componente já usou.
**Fórmula** (kpis.md §4.1): `uso desde a instalação ÷ vida nominal`. Com mais de uma medida (horas **e** partidas),
vale a **maior**.
**Exemplos**: cilindro de 5 000 000 ciclos com 3 600 000 → **72 %** (U1). Motor de 30 000 h e 200 000 partidas,
usado 12 000 h (40 %) e 130 000 partidas (65 %) → **65 % por partidas** (U2).
**Alertas**: ≥ 80 % atenção; ≥ 100 % vida nominal atingida (o número passa de 100 % e não é cortado). Componente
sem vida nominal cadastrada mostra "—" (cadastre o valor do datasheet).

<a id="rul"></a>
### Vida remanescente (RUL) e data projetada

**O que é**: em quantos dias o componente chega ao fim da vida nominal, no ritmo de uso atual.
**Fórmula** (kpis.md §4.2): `(vida nominal − uso) ÷ ritmo`, arredondado para cima, onde o ritmo é o uso dos
**últimos 14 dias corridos ÷ 14** (fins de semana parados entram na média). Com mais de uma medida, vale a que
chega primeiro.
**Exemplo** (U3): faltam 1 400 000 ciclos; 280 000 ciclos nos últimos 14 dias = 20 000/dia → **70 dias, 14/12/2026**
(contando de 05/10/2026).
**Como não se enganar**:

- Se a linha mudar de ritmo (mais um turno), a data muda junto — ela é uma projeção, não uma promessa.
- Componente instalado há menos de 14 dias: o ritmo usa os dias desde a instalação (mínimo 3) e a tela marca
  **"baixa confiança"**.
- Nos **primeiros 14 dias depois de conectar** uma máquina ao FactoryConnect, o ritmo dos **planos de
  manutenção** (e de componentes cadastrados com uma data de instalação antiga) ainda não tem 14 dias de dados e a
  data projetada sai **mais distante** do que o real. Nesse período, confira o contador da máquina antes de adiar
  uma troca (veja [Primeiros 14 dias](/docs/administrador#primeiros-14-dias)).
- Sem uso nos últimos 14 dias não há projeção ("sem uso recente").

<a id="plano"></a>
### Plano de manutenção: "o que vier primeiro"

**O que é**: um plano pode vencer por ciclos, horas, partidas **ou** dias — o que vier primeiro.
**Regras** (domain.md §5.1, S4-3): para cada gatilho, `consumido = uso desde a última manutenção ÷ intervalo`; o
estado do plano é o do **maior** consumido: **ok** (< 80 %), **vence em breve** (≥ 80 %, o sistema cria o rascunho
da OS), **vencido** (≥ 100 %), **atrasado** (≥ 100 % + tolerância, padrão 10 %). A **data** de vencimento é a mais
cedo entre os gatilhos.
**Exemplo** (U4): plano de 2 000 000 ciclos ou 180 dias; desde a última troca, 1 700 000 ciclos (85 %) e 60 dias
(33 %), ritmo de 20 000 ciclos/dia → **vence em 15 dias (20/10/2026) por ciclos**, estado "vence em breve".
**Como não se enganar**: o gatilho que define o **estado** e o que define a **data** podem ser diferentes
("82 % (partidas) · vence em 40 dias por horas"). Os dois estão certos.

---

## Manutenção preditiva

<a id="alerta-preditivo"></a>
### Alerta preditivo e severidade

**O que é**: o sistema compara cada componente monitorado com **ele mesmo** logo depois da instalação ou da
manutenção (a "referência", ou linha de base) e avisa quando um sinal — tempo de curso, corrente, vibração,
temperatura — se afasta de forma consistente (predictive.md P-2 a P-8).

| Severidade | Rótulo | De onde vem | O que fazer |
|---|---|---|---|
| `watch` | Atenção | só um detector estatístico | acompanhar; não gera OS |
| `warning` | Alerta | regra de engenharia (ex.: "> 110 % da referência") ou falha projetada em ≤ 30 dias | avaliar e converter em OS preditiva |
| `critical` | Crítico | regra crítica ou falha possível em ≤ 7 dias (cenário pessimista) | agir já: técnico de plantão é avisado |

**Como ler o texto do alerta**: "Cilindro B3 — tempo de curso 206,5 ms (referência 200,0 ± 2,0 ms desde 14/01):
+3,25 σ. EWMA 1,34 > 1,00; CUSUM 6,0 > 5,0. Falha projetada em 26 dias (17–50) ao atingir 240 ms." Quer dizer: o
curso ficou 6,5 ms mais lento que a referência, isso é 3,25 vezes a variação normal; dois testes estatísticos
confirmam que não é um pico isolado; no ritmo atual de desgaste e de uso, o curso chega ao limite de falha de 240 ms
em 26 dias, provavelmente entre 17 e 50.
**Como não se enganar**: enquanto a referência está sendo formada a tela mostra **"aprendendo (12/20 janelas)"**,
não um número. Um alerta só sobe de severidade sozinho, nunca desce.

<a id="rul-condicao"></a>
### RUL de condição (pelo sinal)

**O que é**: a vida remanescente estimada pela **tendência do sinal** até o limite de falha, e não pela vida de
catálogo (predictive.md P-6).
**Fórmula**: `(limite de falha − valor ajustado agora) ÷ inclinação`, convertido em dias pelo ritmo de uso. A faixa
(pessimista–otimista) combina a incerteza da tendência e a do ritmo de uso.
**Exemplo** (cenário F1): limite 240 ms, valor ajustado 219,73 ms, subindo 0,94 ms a cada 10 000 ciclos → restam
215 806 ciclos; a 20 000 ciclos/dia → **11 dias (faixa 8–16)**.
**Como não se enganar**: sem tendência significativa a RUL de condição é "—" ("sem tendência significativa") —
isso é uma boa notícia, não falta de dado. A tela de componentes usa a data **mais cedo** entre a RUL nominal e a de
condição.

<a id="avaliacao-preditiva"></a>
### A preditiva está funcionando? (antecedência, precisão, falsos alarmes)

Na tela Preditiva → Desempenho (predictive.md P-10):

- **Antecedência**: quanto tempo antes da falha o alerta abriu. Meta: mediana ≥ 7 dias em "Alerta".
- **Precisão**: dos alertas decididos, quantos eram falha real (ou defeito confirmado). Meta: ≥ 70 % em "Alerta".
- **Falsos alarmes por posição-ano**: meta ≤ 1 em "Alerta". Em "Atenção" mais alarmes são aceitáveis (não geram OS).
- **Detecção**: das falhas em posições monitoradas, quantas tiveram alerta com ≥ 24 h de antecedência. Meta ≥ 80 %.

---

## Vida por fabricante e modelo (Weibull)

<a id="weibull"></a>
### Como ler a análise de Weibull

O relatório "Confiabilidade e vida por fabricante/modelo" ajusta uma **distribuição de Weibull** à vida dos
componentes de cada fabricante/modelo (reports.md §2). A vantagem sobre o MTTF simples é usar também os
componentes que **ainda não falharam** (trocados preventivamente ou em serviço): eles dizem "durou pelo menos
isto".

| Número | O que significa | Como usar |
|---|---|---|
| **β (forma)** | o padrão das falhas | β < 1: falhas precoces; β ≈ 1: aleatórias; β > 1: desgaste |
| **η (vida característica)** | idade em que 63,2 % das unidades terão falhado | comparar marcas/modelos |
| **MTTF Weibull** | vida média estimada | substitui o MTTF simples quando há ajuste |
| **B10** | idade em que 10 % terão falhado | referência para o intervalo de troca preventiva |
| **B10 / vida nominal** | a vida real comparada com o catálogo | < 0,8 → "vida real abaixo da nominal" |
| **IC 90 %** | a faixa onde o valor verdadeiro está com 90 % de confiança | quanto mais falhas, mais estreita |

O padrão é decidido pelo **intervalo de confiança** de β, e o relatório imprime a recomendação:

- **IC inteiro abaixo de 1 — falhas precoces**: revisar montagem, qualidade do lote e partida. Troca preventiva
  **não** ajuda.
- **IC contém 1 — falhas aleatórias**: trocar por tempo não reduz falhas; prefira monitoramento de condição
  (preditiva).
- **IC inteiro acima de 1 — desgaste**: troca preventiva faz sentido; o B10 é uma referência de intervalo.

**Exemplo** (caso `weibull_cylinders`): 10 cilindros do mesmo modelo, 6 falhas e 4 suspensões. β = **5,20**
(IC 3,17–8,53, desgaste), η = **5 592 653 ciclos**, MTTF Weibull **5 146 536 ciclos** (o simples dava 4 850 000
porque ignorava as suspensões), **B10 = 3 627 573 ciclos** — 0,73 da vida nominal de 5 000 000 → "vida real abaixo
da nominal". Um cilindro em serviço com 5 000 000 ciclos, a 20 000 ciclos/dia, tem **36,1 %** de chance de falhar
nos próximos 30 dias; um com 2 000 000 ciclos, 1,4 %.

**Papel de Weibull**: os pontos são as falhas (com ranks ajustados que levam em conta as suspensões); a reta é o
ajuste. Pontos muito longe da reta indicam duas causas de falha misturadas (por exemplo, falha de montagem e
desgaste) — vale separar por modo de falha.

**Como não se enganar**:

- Com **menos de 3 falhas** não há ajuste ("falhas insuficientes"); o relatório mostra só o MTTF simples.
- Com **3 a 9 falhas** aparece "amostra pequena": com poucas falhas o β tende a sair alto. Exemplo: os rolamentos
  do R4 dão β = 14,3 com só 3 falhas — não conclua "desgaste muito concentrado" com essa amostra.
- Uma marca só é chamada de "melhor" quando os intervalos de η **não se sobrepõem**; caso contrário o texto é "sem
  diferença demonstrada com os dados atuais".
- Componente reparado e reinstalado entra como uma unidade nova ("tão bom quanto novo"); por isso o técnico
  cadastra o item reparado como componente novo na troca.
