---
title: "Manual do usuário FactoryConnect"
description: "Por onde começar: qual parte do manual é a sua, como ler os números e o que significam os avisos."
ordem: 1
papel: todos
---

# Manual do usuário FactoryConnect

O FactoryConnect liga as máquinas da sua fábrica (CLPs, sensores, contadores) a telas que respondem às perguntas
de todo dia: *como foi o turno, onde perdemos tempo, o que está parado agora, o que vence esta semana, o que está
degradando*. Este manual é organizado **por papel** e **por tarefa**: procure a sua função e, dentro dela, a
tarefa ("como justificar uma parada", "como programar a semana").

## Qual é a sua parte

| Você é… | Usa principalmente | Leia |
|---|---|---|
| **Operador** de máquina ou linha | o tablet fixo na linha | [Operador (tablet)](operador.md) |
| **Supervisor de produção** | Raio-X, mapa, Paradas, Relatório de turno | [Supervisor de produção](supervisor.md) |
| **Planejador de manutenção (PCM)** | Próximas manutenções, Programação, Ordens de serviço, Preditiva | [Planejador / PCM](planejador.md) |
| **Técnico de manutenção** | o app do técnico no celular | [Técnico (celular)](tecnico.md) |
| **Gerente** (produção ou manutenção) | Raio-X, OEE, Gestão, Confiabilidade, relatórios | [Gerente](gerente.md) |
| **Administrador** do sistema | Implantação, usuários, tablets, gateways, relatórios agendados | [Administrador](administrador.md) |

Todos: [Indicadores em linguagem simples](indicadores.md) explica cada número (OEE, MTBF, PMP, RUL, Weibull…) com
a fórmula, um exemplo e os erros de leitura mais comuns.

Uma pessoa pode ter mais de um papel (o gerente de manutenção também planeja, por exemplo). O sistema mostra só as
telas e os botões que o seu papel pode usar; se faltar alguma coisa, peça ao administrador.

## As perguntas de toda manhã e onde estão as respostas

| Pergunta | Tela | Quem |
|---|---|---|
| Como foi ontem e a noite? Batemos a meta? | Raio-X → Linha (resumo por turno), Relatório de turno | supervisor, gerente |
| Onde perdemos mais tempo? | Linha → Pareto de perdas | supervisor, gerente |
| Quais paradas ainda estão sem motivo? | Paradas → filtro "não justificadas" | operador, supervisor |
| Algum turno ou produto está muito pior? | OEE → comparação de turnos/produtos | gerente |
| O que está parado agora? | Mapa da planta (ao vivo) | todos |
| O que quebrou desde ontem e já voltou? | Falhas (últimas 24 h) | planejador, gerente |
| O que vence esta semana / está atrasado? | Próximas manutenções | planejador |
| Quais componentes estão perto do fim da vida? | Componentes (ordenado por % de vida) | planejador |
| Quem são os equipamentos que mais falham? | Confiabilidade | planejador, gerente |
| Estamos mais preventivos ou apagando incêndio? | Gestão da manutenção | gerente |
| O que está degradando e quando vai falhar? | Preditiva | planejador, gerente |

## Como ler os números

- **Percentuais** com uma casa decimal (`75,4 %`), durações em minutos (`25 min`) ou horas (`180,0 h`).
- **Dia produtivo**: "ontem" e "hoje" seguem o calendário de turnos da planta, no fuso da planta. O turno da noite
  que começa às 22:00 de segunda e termina às 06:00 de terça pertence à **segunda-feira**.
- **Nada é média de porcentagens**: o OEE da linha, do dia ou da fábrica é recalculado a partir dos tempos e das
  peças somados. Os minutos de perda e as peças somam de baixo para cima (fábrica = soma das linhas); as
  porcentagens não.
- **Cada número leva à sua causa**: clique numa barra do Pareto para ver as paradas daquele motivo, em "falhas
  abertas" para ver a lista de falhas, num selo preditivo para ver o alerta. O período escolhido vai junto.

<a id="quando-aparece-um-traco"></a>
## Quando aparece "—"

"—" quer dizer **"não dá para calcular"**, nunca zero. O motivo aparece ao lado:

| Motivo na tela | Código | O que fazer |
|---|---|---|
| sem tempo planejado neste período | `no_planned_time` | normal em feriado/fim de semana sem turno; se não for, cadastre o turno |
| dados insuficientes | `insufficient_data` | a máquina não mandou dados no período, ou o produto não tem tempo de ciclo ideal: confira o gateway e o cadastro de produtos |
| sem produção | `no_production` | havia tempo planejado e nenhuma peça (a qualidade não se aplica) |
| linha sem máquina de saída | `no_output_machine` | o administrador indica qual máquina conta as peças da linha |
| sem falhas no período | `no_failures` | boa notícia; o MTBF mostra as horas sem falha |
| sem vida nominal cadastrada | `no_nominal_life` | cadastre a vida do datasheet do componente |
| sem uso recente | `no_recent_usage` | o componente não trabalhou nos últimos 14 dias: não há como projetar |
| falhas insuficientes | `insufficient_failures` | Weibull precisa de pelo menos 3 falhas no grupo |
| sem tendência significativa | `no_significant_trend` | a condição não está piorando de forma consistente |

<a id="avisos"></a>
## Os avisos que acompanham os números

| Aviso | Quer dizer | O que fazer |
|---|---|---|
| **dados incompletos (cobertura X %)** | menos de 95 % do tempo programado com dados | declarar a parada do período sem dados, se souber o que houve; chamar o administrador se o gateway caiu |
| **tempo de ciclo ideal suspeito** | desempenho acima de 100 % | revisar o tempo de ciclo cadastrado (máquina × produto) ou o contador |
| **produto não informado em parte do período** | não havia ordem de produção aberta; usou-se o produto padrão | trocar o produto no tablet no início de cada setup |
| **contagem proporcional** | a janela consultada corta uma hora no meio e as peças daquela hora foram repartidas | normal em consultas de 15 min; turnos em hora cheia são exatos |
| **produção fora do calendário** | peças contadas fora de qualquer turno | cadastrar um turno extra, se foi produção de verdade |
| **calendário padrão 24 h** | a planta ainda não tem turnos cadastrados | cadastrar os turnos (passo 3 da implantação) |
| **estimado** | horas ou ciclos calculados (ex.: ciclos = peças × ciclos por peça) e não medidos | instalar sensor de ciclo se a posição for crítica |
| **baixa confiança** | projeção com menos de 14 dias de histórico, ou poucas falhas | usar com cautela; o número melhora com o tempo |
| **ao vivo pausado — última atualização HH:MM:SS** | a conexão com o servidor caiu há mais de 30 s | o mapa não está mais ao vivo; aguarde a reconexão ou recarregue |
| **Demonstração — dados de uma planta simulada** | você está no modo demonstração | nada do que você vê é de uma fábrica real; alterações estão desativadas |

## Mais informações

- A definição técnica de cada indicador, com todos os casos de borda: [`docs/kpis.md`](../kpis.md).
- Regras de domínio (paradas, falhas ISO 14224, manutenção): [`docs/domain.md`](../domain.md).
- Manutenção preditiva: [`docs/predictive.md`](../predictive.md). Relatórios: [`docs/reports.md`](../reports.md).
