---
title: "Gerente (produção e manutenção)"
description: "Como o gerente usa o FactoryConnect: Raio-X da fábrica, OEE e Pareto, comparação de turnos e produtos, gestão da manutenção, confiabilidade, preditiva, análise de Weibull e relatórios por e-mail."
ordem: 7
papel: manager
---

# Gerente — produção e manutenção

Você olha para tendências e decisões: estamos melhorando, onde investir, a equipe dá conta, qual fornecedor dura
mais. Telas principais: **Raio-X** (`/dashboard`), **Mapa da planta**, **OEE**, **Gestão**, **Confiabilidade**,
**Preditiva** e os relatórios que chegam por e-mail.

## Como começar o dia — Raio-X da fábrica

O Raio-X mostra a fábrica inteira no período escolhido: OEE/A/P/Q da planta, peças boas e refugo, as 5 maiores perdas,
falhas abertas, alertas preditivos e o estado dos gateways. Cada número leva à sua causa: clique para descer
Fábrica → Linha → Máquina → Posição, com o mesmo período.

**Somas fecham, razões não**: os minutos de perda e as peças da fábrica são a soma das linhas (o Pareto da planta é a
soma dos Paretos das linhas). Já o OEE da fábrica **não** é a média das linhas: é recalculado com os tempos e as peças
somados. Em lugar nenhum o sistema mostra "média" de OEE.

<a id="ler-oee"></a>
## Como ler o OEE e o Pareto

- Comece pelos três fatores: [disponibilidade](/docs/indicadores#disponibilidade) (paradas),
  [desempenho](/docs/indicadores#desempenho) (velocidade e micro-paradas) e [qualidade](/docs/indicadores#qualidade)
  (refugo). O menor dos três diz onde está o problema.
- Exemplo do dia (E2): turno A 75,4 %, turno B 92,5 %, **dia 83,7 %** (A 92,0 %, P 93,8 %, Q 97,0 %). Não é 84,0 %
  (a média), porque o turno B tinha menos tempo planejado.
- [Pareto](/docs/indicadores#pareto): ataque as primeiras barras até ~80 % do acumulado. Se "não justificada" está entre as
  maiores, a primeira ação é disciplina de apontamento (veja "% de tempo de parada justificado" no relatório semanal).
- [TEEP](/docs/indicadores#teep) responde outra pergunta: quanto da capacidade de 24 h × 7 está sendo usada — útil para
  decidir entre um turno a mais e uma máquina nova.
- Avisos que mudam a leitura: **dados incompletos**, **tempo de ciclo ideal suspeito** (desempenho > 100 %),
  **produto não informado** ([lista completa](/docs#avisos)).

## Como comparar turnos, produtos e semanas

- **OEE → agrupar por turno** (ou por produto): cada ponto é a soma das instâncias daquele turno no período, nunca a
  média. Um turno muito pior que os outros costuma ter causa própria (setup concentrado, falta de operador).
- O **relatório semanal de OEE e perdas** (segunda, 07:00) traz as 12 últimas semanas, o Pareto da semana contra a
  anterior, micro-paradas por máquina, a disciplina de apontamento e as **3 perdas a atacar** (as 3 maiores em minutos,
  com a máquina principal de cada uma — gerado pela regra, sem texto inventado).

## Como ler a gestão da manutenção

**Gestão** (e o relatório mensal de manutenção, dia 1 às 07:00):

| Indicador | Referência | O que a resposta diz |
|---|---|---|
| [PMP](/docs/indicadores#pmp) | ≥ 85 % (classe mundial ≥ 90 %) | estamos planejando ou apagando incêndio? |
| [Cumprimento da programação](/docs/indicadores#cumprimento) | ≥ 90 % | o planejado é executado? |
| [Backlog](/docs/indicadores#backlog) | 2–4 semanas | a equipe dá conta? (< 2: ociosa; > 4: insuficiente) |
| [Ao](/docs/indicadores#disponibilidade-operacional) | depende da máquina | quanto do tempo requerido o equipamento esteve disponível |
| [MTBF](/docs/indicadores#mtbf) / [MTTR](/docs/indicadores#mttr) | tendência | a planta quebra menos? volta mais rápido? |

Exemplo (relatório mensal da especificação): PMP **84,4 %** (âmbar), cumprimento **85,0 %** (âmbar), backlog **2,0
semanas** (verde), MTBF **180,0 h**, MTTR **60,0 min**, Ao **98,0 %**.

Leituras combinadas que ajudam:

- PMP baixo **e** backlog alto: a equipe vive de emergência e o preventivo acumula — reforçar a equipe ou reduzir a
  causa das falhas (melhorias).
- Cumprimento baixo com backlog baixo: problema de programação ou de liberação da produção, não de capacidade. Veja a
  lista de OS "não feitas" e o motivo do cancelamento (conflito com a produção?).
- MTTR alto: abra as falhas e veja o tempo em espera (peça, técnico) antes de concluir que o reparo é lento.

## Como achar os maus atores

**Confiabilidade** (12 meses): ordene por número de falhas ou por minutos de parada `EQP-*`. Compare posições do
mesmo tipo, máquinas e fabricantes. Para cilindros, garras, válvulas e robôs use o **MCBF** (ciclos), não o MTBF.
Olhe sempre o **n**: MTBF com 1 falha não é estatística. O relatório mensal traz os top-10 com o modo de falha ISO 14224
dominante e, em coluna própria, as falhas que aconteceram sem tempo produtivo (na pausa ou fora do turno).

<a id="weibull"></a>
## Como ler o Weibull (vida por fabricante e modelo)

O relatório **Confiabilidade e vida por fabricante/modelo** (trimestral, ou sob demanda) responde: *qual marca/modelo
dura mais? a vida de catálogo é real? quando trocar?*

1. Para cada tipo de componente, uma linha por fabricante/modelo: componentes (N), falhas (r), suspensões, MTTF
   simples e **β, η, MTTF Weibull, B10** com intervalo de confiança de 90 %.
2. **β** diz o padrão ([detalhes](/docs/indicadores#weibull)):
   - **desgaste** (β > 1): troca preventiva faz sentido; use o **B10** como referência de intervalo;
   - **aleatório** (β ≈ 1): trocar por tempo não reduz falhas — invista em preditiva;
   - **falhas precoces** (β < 1): o problema é montagem, lote ou partida; troca preventiva **piora**.
3. **B10 / vida nominal < 0,8** → "vida real abaixo da nominal": cobre o fornecedor ou revise a aplicação.
4. **Comparar marcas**: só há diferença "significativa" quando os intervalos de η não se sobrepõem. Senão, "sem
   diferença demonstrada com os dados atuais" — não troque de fornecedor com base nisso.
5. A seção **"maior probabilidade de falhar nos próximos 30 dias"** lista os componentes em serviço por risco —
   boa pauta para a reunião com o PCM.

Exemplo: cilindros com β = 5,20 (desgaste), B10 = 3 627 573 ciclos, 0,73 da vida nominal; o cilindro com 5 000 000
ciclos tem 36,1 % de chance de falhar em 30 dias.

Cuidado com amostras pequenas: com 3 a 9 falhas aparece "amostra pequena" (o β tende a sair alto); com menos de 3 não
há ajuste.

## Como avaliar se a preditiva funciona

**Preditiva → Desempenho**: antecedência (meta: mediana ≥ 7 dias em "Alerta"), precisão (≥ 70 %), falsos alarmes por
posição-ano (≤ 1 em "Alerta"), detecção (≥ 80 % das falhas com alerta ≥ 24 h antes) e a cobertura da faixa de RUL
([definições](/docs/indicadores#avaliacao-preditiva)). No exemplo da especificação, o alerta abriu 17,5 dias antes da
falha; um limite fixo simples teria avisado só 4 dias antes — é a diferença entre programar a troca e apagar incêndio.

As horas de OS preditivas **sem defeito** aparecem à parte na Gestão: é o custo dos falsos alarmes. Elas não pioram o
PMP.

## Como receber os relatórios

Por padrão o gerente recebe: produção diária (06:30), OEE semanal (segunda 07:00), manutenção mensal (dia 1, 07:00) e
vida por fabricante/modelo (trimestral). Cada e-mail traz 3 a 5 números principais, o aviso de dados incompletos se
houver, o PDF (e o Excel, se marcado) e o link para a tela. Os números do relatório são **os mesmos das telas** (o
relatório é montado com as mesmas consultas). Peça ao administrador para mudar horários e destinatários.

O **Assistente IA** responde perguntas sobre esses mesmos dados ("qual linha perdeu mais com setup na semana?")
usando as mesmas consultas das telas, e mostra de onde tirou cada número.

## Erros comuns

| Erro | Consequência | Como evitar |
|---|---|---|
| Fazer média de OEE de linhas ou turnos numa planilha | número diferente do sistema, conclusões erradas | exportar o Excel com os tempos e somar os componentes, ou usar o agrupamento da tela |
| Comparar OEE entre fábricas com tempos de ciclo cadastrados de jeitos diferentes | comparação injusta | padronizar o tempo de ciclo ideal (menor sustentável, não média) |
| Cobrar PMP alto sem olhar o cumprimento | planejamento no papel | olhar PMP, cumprimento e backlog juntos |
| Trocar de fornecedor por MTTF simples | decisão enviesada por suspensões | usar η e B10 com intervalo de confiança |
| Aumentar a preventiva onde β ≈ 1 | custo sem redução de falhas | preditiva para falhas aleatórias |

## Perguntas frequentes

**Por que o OEE do relatório diário é diferente da tela que abri às 08:00?**
O relatório usa o **dia produtivo fechado** (até o fim do último turno, + 30 min). A tela às 08:00 pode estar mostrando
"hoje", que acabou de começar. Com o mesmo período, os números são idênticos.

**O relatório do mês chegou como "versão 2". O que mudou?**
Dados atrasados ou paradas reclassificadas depois do envio. Se algum indicador principal mudou mais de 0,5 ponto
percentual, você recebe a versão revisada; a anterior continua disponível.

**Posso ver o OEE de uma linha sem máquina de saída cadastrada?**
Não: o sistema não sabe quais peças contam como produção da linha. Aparece "linha sem máquina de saída".

**O que é o modo demonstração?**
Um ambiente com uma planta **simulada** (faixa permanente no topo). Serve para conhecer o produto; nada é dado real e
nada pode ser alterado.
