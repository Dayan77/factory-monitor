---
title: "Supervisor de produção"
description: "O turno do supervisor no FactoryConnect: relatório de turno, mapa ao vivo, OEE e Pareto, paradas sem motivo, dados incompletos, chamado à manutenção, janelas de preventiva e tablets."
ordem: 4
papel: production_supervisor
---

# Supervisor de produção

Você responde pelo turno: o que aconteceu, o que está acontecendo agora e o que fica para o próximo. As telas
principais são **Raio-X** (`/dashboard`), **Mapa da planta**, **Paradas**, **Relatório de turno** e, na
manutenção, o painel da sua área.

## Como começar o turno

1. Abra **Relatório de turno** e escolha o turno anterior da sua linha. Veja:
   - OEE, disponibilidade, desempenho e qualidade do turno ([como ler](indicadores.md#oee));
   - as paradas, com destaque para as **não justificadas** e o total de minutos delas;
   - **paradas planejadas declaradas no turno** (reuniões, falta de programação declaradas pelo operador — elas
     reduzem o tempo planejado e melhoram a disponibilidade, por isso aparecem em separado);
   - **pendências** para o seu turno: paradas abertas, OS emergenciais abertas, alertas preditivos de nível
     "Alerta" ou "Crítico" na linha.
2. Abra **Paradas** com o filtro **não justificadas** e resolva o que o turno anterior deixou (veja abaixo).
3. No painel de manutenção da sua área, confira a **manutenção programada hoje** na linha (com a janela de parada) e
   as máquinas com OS **em espera** aguardando liberação.

## Como acompanhar a linha ao vivo

- **Mapa da planta** (`/factory`): cada máquina é um marcador sobre a planta baixa, com o estado (mesmas cores e
  ícones do [tablet](operador.md)), a duração ("parada há 6 min") e, nos marcadores maiores, o OEE do turno atual e
  a cadência do turno até agora. Toque num marcador para ver o resumo (parada aberta, falha aberta, alerta preditivo,
  últimas 3 paradas) e **abrir a máquina**.
- Uma falha que começou durante a pausa aparece em **vermelho** mesmo na pausa: a máquina quebrou e o técnico precisa
  ver, ainda que o OEE não seja afetado.
- **Ao vivo pausado**: se a faixa "ao vivo pausado — última atualização HH:MM:SS" aparecer, os marcadores ficam
  esmaecidos — o que você vê pode estar velho. Recarregue a página ou verifique a rede.
- Sem planta baixa enviada, o mapa usa o **layout automático** (uma faixa por linha); isso está escrito no topo.

### Navegar sem perder o período

Fábrica → Linha → Máquina → Posição: o período escolhido ("turno atual", "hoje", "ontem", "últimos 7 dias" ou um
intervalo) vai junto em todos os links e no botão voltar. "Hoje" e "ontem" são **dias produtivos** (pelo calendário de
turnos), não 00:00–00:00. Os links podem ser copiados e enviados: quem abrir vê o mesmo período.

## Como ler o OEE e o Pareto

- O OEE da linha é calculado na **máquina de saída** da linha (a que conta as peças que saem). Paradas de máquinas
  intermediárias só pesam quando param a saída (aparecem nela como "falta de material", `MAT-01`).
- O OEE de vários turnos **não** é a média dos turnos: o sistema soma os tempos e as peças. Exemplo: turno A 75,4 %
  e turno B 92,5 % dão **83,7 %** no dia ([por quê](indicadores.md#oee)).
- No **Pareto de perdas** ([como ler](indicadores.md#pareto)) ataque as primeiras barras até ~80 % do acumulado.
  Clique numa barra para ver as paradas daquele motivo. Se "Não justificada" estiver entre as primeiras, o Pareto
  ainda não está dizendo a verdade: justifique primeiro.
- O **gargalo** da linha (máquina com mais tempo de parada) aparece na tela da linha.

<a id="nao-justificadas"></a>
## Como tratar paradas sem motivo e corrigir um motivo

1. **Paradas** → filtro **não justificadas** (motivo vazio ou `UNK-01`).
2. Abra a parada, escolha o motivo e salve. Para falhas, o motivo sugerido `EQP-*` vem do código de falha do CLP:
   confirme ou corrija.
3. Para corrigir um motivo dado pelo operador, abra a parada e troque. Toda troca fica registrada (quem e quando).

Ao fim do turno, parada sem motivo **e** sem sugestão de falha recebe `UNK-01` automaticamente. Parada com sugestão
`EQP-*` continua aguardando confirmação.

**Reclassificar para parada planejada** (`PLN-01` refeição, `PLN-02` reunião, `PLN-04` sem programação, `PLN-05`
teste): a parada sai do tempo planejado e melhora a disponibilidade. Use só quando foi de fato planejada — ela aparece
no relatório como "parada planejada declarada no turno". **Preventiva** (`PLN-03`) só é aceita se estava no
calendário; caso contrário o sistema sugere `MNT-02`.

## Como tratar "dados incompletos"

A faixa **"dados incompletos (cobertura X %)"** aparece quando menos de 95 % do turno teve dados da máquina
([o que é cobertura](indicadores.md#cobertura)). O tempo sem dados **sai** do tempo planejado: o sistema nunca supõe
que a máquina rodou nem que parou.

- Se a linha **estava parada** no buraco (queda de energia, falta de ar): declare a parada (Paradas → declarar, com
  início, fim e motivo, por exemplo `UTL-01`). Ela passa a contar como parada não planejada.
  Exemplo da especificação (cenário D): sem a declaração, o turno fica com OEE 89,1 % sobre 395 min; declarando a
  queda de utilidade, 80,9 % sobre 435 min — o número certo.
- Se a linha **rodou** e só o sinal caiu: não declare nada; as peças contadas depois do buraco entram (o contador do
  CLP não perde contagem). Avise o administrador para ver o gateway.

## Como tratar "produção fora do calendário"

Peças contadas fora de qualquer turno (teste da manutenção no feriado, sábado sem turno extra) **não** entram no OEE
— entrariam sem o tempo correspondente e inflariam o desempenho. Se foi produção de verdade, peça ao planejador de
produção/administrador para cadastrar um **turno extra** naquele dia; o OEE é recalculado.

<a id="chamar-manutencao"></a>
## Como chamar a manutenção e liberar a máquina

- **Parada por falha**: na parada (tablet ou Paradas), toque em **Chamar manutenção**. O chamado vai para os técnicos
  de plantão da área. A resposta diz **quem foi avisado**; se não houver técnico de plantão na linha, o planejamento e
  a gestão são avisados ("ninguém de plantão nesta linha — o planejamento/gestão foi avisado (N pessoas)"). Alerta
  vermelho "avise a manutenção por rádio/telefone" = ninguém da manutenção recebeu.
- **Defeito sem parada** (vazamento, ruído): **Solicitar manutenção** a partir da máquina, com foto. Vira uma
  solicitação para o planejador; não é falha enquanto a máquina não perder a função.
- **Liberação**: quando o técnico concluir uma OS que parou a máquina, confirme **"máquina liberada e produzindo"**.
  Em máquina sem sinal de "rodando", essa confirmação é o instante de restabelecimento da falha (entra no MTTR).
- Falha aberta há mais de **2 × o MTTR** da posição gera escalonamento para você e o planejador.

## Como combinar uma janela de preventiva

Quando o planejador programa uma OS que **para a máquina**, você recebe o **pedido de janela de parada**. Aceite (o
sistema cria a parada planejada `PLN-03` no calendário daquele turno) ou proponha outro horário.

Por que importa: com a janela no calendário, a preventiva é tempo planejado (não derruba o OEE). Sem ela, a mesma
preventiva vira `MNT-02`, perda de disponibilidade. Janela combinada e não usada aparece no relatório de turno como
"janela de manutenção não usada".

## Como fechar uma OS da sua área

OS concluídas pelo técnico chegam como **aguardando fechamento**. Verifique o serviço (limpeza, proteções
recolocadas, primeira peça boa) e **feche**. Se algo ficou errado, **devolva com observação**: o sistema cria uma OS
nova ligada à anterior (OS concluída não reabre).

<a id="tablet"></a>
## Como cadastrar o tablet da linha

1. No tablet novo, abra o endereço do FactoryConnect em `/op` e instale o app ("Adicionar à tela inicial").
2. Num computador, gere o **código de cadastro do tablet** para a linha (válido por 10 min).
3. Digite o código no tablet. Ele passa a atender **só aquela linha** e só aceita operadores daquela linha.
4. Tablet perdido ou trocado: peça ao administrador para **revogar** o dispositivo; o código antigo deixa de valer.

## Como fechar o turno

- Resolva as paradas sem motivo (o relatório é gerado **15 min depois do fim do turno**, justamente para isso).
- No **Relatório de turno**, escreva as **observações do supervisor** (texto livre, guardado com o relatório).
- O relatório em PDF é enviado por e-mail para você e para o supervisor do próximo turno, se o agendamento estiver
  ativo (veja [Administrador — relatórios agendados](administrador.md#relatorios)). Você pode exportar PDF/Excel a
  qualquer momento com o botão **Exportar** da tela.

## Erros comuns

| Erro | Consequência | Como evitar |
|---|---|---|
| Deixar paradas sem motivo para "depois" | o relatório sai com `UNK-01` e o Pareto aponta "não justificada" | justificar durante o turno; o relatório espera 15 min |
| Reclassificar perda como `PLN-*` para melhorar o número | OEE melhor no papel, perda escondida; aparece em destaque no relatório | `PLN-*` só para o que estava planejado |
| Ler o OEE da linha como média das máquinas | conclusões erradas sobre o gargalo | o OEE da linha é o da máquina de saída |
| Ignorar "dados incompletos" | desempenho inflado (peças contadas, tempo fora) | declarar a parada do buraco quando a linha estava parada |
| Fazer preventiva fora da janela combinada | vira `MNT-02` e derruba a disponibilidade | combinar a janela `PLN-03` antes do turno |
| Fechar OS sem verificar | retrabalho e falha repetida no MTBF | verificar e, se preciso, devolver |

## Perguntas frequentes

**O OEE de ontem mudou depois que eu justifiquei paradas. Está certo?**
Sim. Justificar não muda o tempo parado, mas reclassificar para `PLN-*` tira o tempo do planejado, e o relatório
agendado é refeito como nova versão quando dados atrasados chegam (a versão anterior continua disponível).

**Por que o desempenho passou de 100 %?**
O tempo de ciclo ideal cadastrado está mais lento que o real, ou o contador conta em dobro. O número aparece com o
aviso "tempo de ciclo ideal suspeito" — peça a correção do cadastro.

**A falha começou no café e foi resolvida depois. Como entra no OEE?**
Só a parte depois do fim do café é parada não planejada (perda de disponibilidade). O MTTR da manutenção conta o
tempo inteiro desde a falha (é o que a produção sentiu).

**Uma consulta de 15 minutos mostrou "contagem proporcional". O número está errado?**
Não: as peças são guardadas por hora, e uma janela que corta a hora recebe a parte proporcional. Turnos em hora cheia
são exatos.

**Por que "hoje" começa às 06:00 e não à meia-noite?**
"Hoje" é o dia produtivo do calendário de turnos. O turno da noite que começa às 22:00 pertence ao dia em que começa.
