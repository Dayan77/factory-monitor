---
title: "Planejador de manutenção (PCM)"
description: "O dia do PCM: o que vence e está atrasado, aprovar rascunhos, programar a semana, acompanhar OS, planos, componentes, alertas preditivos e RUL, KPIs de gestão e confiabilidade."
ordem: 5
papel: maintenance_planner
---

# Planejador de manutenção (PCM)

Você decide **o que** a manutenção faz, **quando** e **com quem**. Telas principais: **Próximas manutenções**,
**Programação**, **Ordens de serviço**, **Preditiva**, **Componentes**, **Falhas**, **Gestão**, **Confiabilidade** e
**Planos de manutenção**.

## Como ver o que vence e o que está atrasado (07:30)

**Próximas manutenções** lista os planos ordenados pelo vencimento:

| Estado | Cor | Regra |
|---|---|---|
| ok | — | menos de 80 % do intervalo |
| vence em breve (`due_soon`) | amarelo | ≥ 80 % — o sistema já criou o **rascunho** da OS |
| vencido (`due`) | laranja | ≥ 100 % |
| atrasado (`overdue`) | vermelho | ≥ 100 % + tolerância do plano (padrão 10 %) |

Cada linha mostra o **% consumido e o gatilho** que o determinou, a **data de vencimento e o gatilho que vence
primeiro**, o **próximo uso absoluto** ("vence em 45 800 h no horímetro" — é o número que o técnico compara com o
contador da máquina) e a OS do plano. Os dois gatilhos podem ser diferentes: "82 % (partidas) · vence em 40 dias por
horas" está certo ([o que vier primeiro](/docs/indicadores#plano)).

Exemplo: plano "trocar vedações do cilindro A1" a cada 2 000 000 ciclos ou 180 dias; 1 700 000 ciclos e 60 dias
desde a última troca; a linha faz 20 000 ciclos/dia → **85 %, vence em breve, em 15 dias por ciclos**.

**Datas de vencimento por uso** vêm do ritmo dos últimos 14 dias. Se a linha vai mudar de ritmo (turno extra,
férias coletivas), a data vai mudar junto. Em máquinas conectadas há menos de 14 dias, a data sai mais distante do que
o real: confira o contador (veja [Primeiros 14 dias](/docs/administrador#primeiros-14-dias)).

## Como aprovar ou descartar um rascunho

O plano que cruza 80 % gera **uma** OS em rascunho (com checklist e horas do plano). Abra, revise e:

- **Aprovar rascunho** (um clique: rascunho → aberta → aprovada); ou
- **Descartar** (cancelar com motivo). Se o plano continuar ≥ 80 %, o sistema cria outro rascunho na hora — você não
  "perde" um vencimento por engano. Criar outra OS à mão para o mesmo plano, com uma já aberta, é recusado.

<a id="programar"></a>
## Como programar a semana

Rotina de quinta ou sexta para a semana seguinte, na tela **Programação**:

1. Arraste as OS **aprovadas** para os dias e para os técnicos. O quadro mostra a **capacidade do dia** (técnicos ×
   horas − ausências) e as **janelas de parada** já combinadas com a produção.
2. OS que **para a máquina**: ao programar, aceite a proposta de criar a parada planejada `PLN-03` no calendário de
   produção daquele turno. O supervisor da linha recebe o pedido e aceita ou propõe outro horário. Sem a janela no
   calendário, a parada vira `MNT-02` (não planejada) e derruba o OEE da produção.
3. **Segunda-feira 00:00 a semana congela.** Tudo o que for programado depois entra como **encaixe** (com selo) e não
   conta no cumprimento da programação — nem a favor nem contra.

Para medir: [cumprimento da programação](/docs/indicadores#cumprimento) (meta ≥ 90 %) e
[backlog](/docs/indicadores#backlog) (2–4 semanas saudável).

## Como acompanhar a execução

**Ordens de serviço** mostra um quadro por estado: aprovadas · programadas · em execução · em espera · concluídas
(aguardando fechamento). O cartão traz máquina/posição, técnico, horas apontadas/estimadas e atraso.

- **Peça chegou** para uma OS em espera: reprograme (em espera → programada) ou devolva ao técnico.
- **A linha não pode parar**: cancele com **conflito com a produção** (conta como **não feita** no cumprimento) ou
  reprograme. Cancele como **substituída** (`superseded`) só quando outra OS faz o trabalho — ela sai da conta.
- OS **vencida**: programada para um dia que já passou e não concluída. Aparece na lista de vencidas e no backlog.

## Como revisar o que foi concluído (16:30)

- Confira horas apontadas, peças trocadas e os dados de falha (ISO 14224) que o técnico deixou como "6.4
  desconhecido / 5.5 desconhecida" — complete **modo, mecanismo e causa** depois da análise. É isso que permite
  agrupar os "maus atores" por modo de falha.
- Confira se a troca de componente zerou o **plano certo**: só os planos da **posição** com "zerar na troca" ligado.
  Planos da máquina (inspeção NR-12, filtro de ar) não zeram.

## Como cadastrar um plano de manutenção

**Planos de manutenção** → novo plano numa **posição** (o lugar na máquina, que continua valendo depois da troca do
componente) ou na máquina inteira:

1. Nome, checklist, horas estimadas e tipo (preventiva ou preditiva).
2. Um ou mais **gatilhos**: ciclos, horas, partidas, dias — vence o que vier primeiro. Planos de **condição** disparam
   quando um alerta preditivo da posição chega a "Alerta".
3. **Zerar na troca** (padrão ligado): a troca do componente reinicia o plano.
4. Para planos que já existiam no papel, informe a **data e o uso da última execução**. A tela mostra na hora o %
   consumido e a data projetada com os dados reais.

## Como acompanhar componentes e vida útil

**Componentes** lista os componentes instalados ordenados por **% de vida** ([o que é](/docs/indicadores#vida)) com a
**RUL** e a data projetada ([como é projetada](/docs/indicadores#rul)). ≥ 80 % = atenção; ≥ 100 % = vida nominal
atingida. Atingir a vida nominal **não** gera OS sozinho: só se houver plano. Você decide.

A RUL efetiva é a mais cedo entre a **vida de catálogo** e a **condição medida** (preditiva); a coluna diz de onde
veio.

<a id="alerta-preditivo"></a>
## Como interpretar um alerta preditivo e a RUL

Na tela **Preditiva** cada alerta tem severidade (Atenção, Alerta, Crítico — [o que significa](/docs/indicadores#alerta-preditivo)),
o texto explicável e o gráfico do sinal com a referência.

1. **Leia o texto**: qual sinal, quanto mudou em relação à referência, qual limite foi cruzado e quando a falha é
   projetada, com a faixa. Exemplo: "tempo de curso 206,5 ms (referência 200,0 ± 2,0 ms desde 14/01): +3,25 σ …
   Falha projetada em 26 dias (17–50) ao atingir 240 ms".
2. **Olhe a faixa da RUL**, não só o número do meio. Para programar, use a data **pessimista** (o começo da faixa):
   ao converter, o sistema já põe o prazo da OS nela.
3. **Decida**:
   - **Reconhecer** — "vi e vou avaliar".
   - **Converter em OS preditiva** — cria uma OS aprovada ligada ao alerta, com o texto e o link do gráfico.
   - **Fechar** com resolução: falso alarme (silencia a mesma severidade por 7 dias), nova referência (`rebaseline`:
     "este é o novo normal", por exemplo mudança de produto — exige motivo), duplicado, causa conhecida (ex.: teste de
     engenharia).
4. Ao concluir a OS preditiva, o técnico informa o **achado**: defeito confirmado (registra uma falha incipiente ou
   degradada) ou sem defeito (o alerta fecha como falso alarme). As horas de OS preditivas sem defeito aparecem à
   parte na Gestão; a OS continua contando como planejada no PMP.

**"Aprendendo (12/20 janelas)"**: o sistema ainda está formando a referência do componente (depois da instalação ou
de uma manutenção que a reinicia). Nesse período só valem os limites absolutos.

**RUL "—" por "sem tendência significativa"**: o sinal não está piorando de forma consistente. Não é falta de dado.

Se a falha chegar antes da preditiva, a corretiva emergencial substitui a preditiva (cancelada como substituída) e o
alerta fecha como "falhou antes da ação".

## Como ler os KPIs de gestão (segunda e fim do mês)

**Gestão da manutenção**: [PMP](/docs/indicadores#pmp) (meta ≥ 85 %), [cumprimento](/docs/indicadores#cumprimento)
(meta ≥ 90 %, com a lista de OS excluídas e o motivo), [backlog](/docs/indicadores#backlog) em semanas, OS vencidas e
[Ao](/docs/indicadores#disponibilidade-operacional) das máquinas críticas. Cada número abre a lista de OS que o formou.

Exemplo: PMP 84,4 % (âmbar, abaixo de 85 %), cumprimento 85,0 % (âmbar), backlog 2,0 semanas (verde).

## Como achar os maus atores (mensal)

**Confiabilidade**, janela de 12 meses: [MTBF](/docs/indicadores#mtbf), [MCBF](/docs/indicadores#mcbf) e
[MTTR](/docs/indicadores#mttr) por posição, máquina, tipo de componente e fabricante, com o **n** de falhas ao lado, e
[MTTF](/docs/indicadores#mttf) por modelo com falhas e suspensões. Daqui saem OS de **melhoria** (eliminar a causa raiz).

- Para cilindros, válvulas, garras e robôs use **MCBF** (ciclos); para motores, rolamentos e bombas, MTBF (horas).
- Para comparar marcas e escolher intervalo de troca, use o relatório **Confiabilidade e vida por fabricante/modelo**
  ([como ler o Weibull](/docs/indicadores#weibull)).

## Erros comuns

| Erro | Consequência | Como evitar |
|---|---|---|
| Programar preventiva que para a máquina sem a janela `PLN-03` | vira `MNT-02` e derruba o OEE; atrito com a produção | aceitar a proposta de janela ao programar |
| Programar depois de segunda 00:00 achando que conta | entra como encaixe (fora do cumprimento) | fechar a programação na quinta/sexta |
| Cancelar como "substituída" sem haver substituta | o cumprimento fica artificialmente alto | usar "conflito com a produção" ou "não necessária" |
| Deixar modo/causa de falha em "desconhecido" | maus atores sem modo dominante; Weibull sem separar causas | completar após a análise |
| Comparar MTBF de uma semana | n = 1, número sem significado | janela de 3–12 meses, olhar o n |
| Decidir intervalo de troca pelo MTTF simples | subestima a vida quando há muitas trocas preventivas | usar B10 do Weibull |
| Converter alerta "Atenção" em OS por rotina | horas gastas em falsos alarmes | "Atenção" é para acompanhar; converter a partir de "Alerta" |

## Perguntas frequentes

**Por que o plano está "vence em breve" se a data é daqui a 40 dias?**
O estado é definido pelo gatilho com maior % (por exemplo, partidas a 82 %); a data, pelo gatilho que vence primeiro
no ritmo atual. Os dois aparecem na linha.

**Troquei o componente e o plano da máquina não zerou.**
Só zeram os planos da **posição** com "zerar na troca" ligado. Planos da máquina inteira (filtro, inspeção) continuam.

**Cancelei o rascunho e apareceu outro.**
Correto: enquanto o plano estiver ≥ 80 %, sempre existe exatamente uma OS aberta dele. Para parar, ajuste o plano.

**O alerta subiu para "Crítico" sozinho. Ele pode voltar para "Alerta"?**
Não. A severidade só sobe automaticamente; para encerrar, feche com a resolução adequada.

**A OS preditiva não achou defeito. Isso piora o PMP?**
Não. Ela continua planejada. O custo aparece à parte ("horas preditivas sem defeito") para você calibrar as regras.
