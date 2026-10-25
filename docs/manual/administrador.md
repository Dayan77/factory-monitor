---
title: "Administrador (implantação, usuários, gateways e relatórios)"
description: "Como implantar o FactoryConnect numa fábrica nova com o assistente, importar tags por CSV ou OPC UA, cadastrar usuários e tablets, cuidar dos gateways e configurar relatórios agendados por e-mail."
ordem: 8
papel: admin
---

# Administrador — implantação, usuários, gateways e relatórios

Você prepara o FactoryConnect para os outros: cadastra a planta, liga as máquinas, convida as pessoas e define
quem recebe o quê. Telas principais: **Implantação** (`/onboarding`), **Gateways**, **Configurações**, **Admin**
(usuários) e **Relatórios agendados**.

<a id="implantacao"></a>
## Como implantar uma fábrica nova (assistente de implantação)

O assistente leva de zero a **dados reais na tela** em 9 passos. O progresso fica salvo no servidor: dá para parar e
continuar depois, de qualquer computador. Cada passo valida antes de seguir.

| # | Passo | O que você faz | Para avançar |
|---|---|---|---|
| 1 | Empresa e planta | nome, endereço e **fuso horário** | confirmar o fuso ("Confirmo que a planta opera no fuso …") |
| 2 | Linhas e máquinas | formulário ou **CSV de ativos** (linha, máquina, modelo, série, máquina de saída da linha, posições e componentes com tipo, fabricante, modelo e vida nominal) | toda linha com **máquina de saída**; nomes únicos na planta |
| 3 | Turnos e calendário | modelo pronto (1, 2 ou 3 turnos, com pausas) ou personalizado; feriados | turnos sem sobreposição; ao menos 1 turno ou "operação 24 h" |
| 4 | Produtos e tempo de ciclo ideal | produtos, tempo de ciclo ideal por máquina e produto, produto padrão da máquina de saída | máquina de saída com tempo de ciclo (senão o OEE fica "—") |
| 5 | Conectar gateway | criar o gateway → **código de cadastro** → instalar o edge na fábrica | gateway **online** (primeiro heartbeat) |
| 6 | Importar tags | **CSV de tags** ou **navegação OPC UA** pelo gateway; papel, máquina e posição de cada tag | o mapa é aceito pelo gateway (prévia sem erro) |
| 7 | Motivos de parada e códigos de falha | revisar o catálogo padrão; mapear os códigos de falha do CLP para motivos `EQP-*` | catálogo com os códigos padrão |
| 8 | Usuários e papéis | convidar operadores, supervisores, planejador, técnicos; definir as linhas de cada um | recomendado: um segundo administrador |
| 9 | Verificação "pronto para produção" | checklist automático | todos os itens obrigatórios verdes por **15 min** de dados contínuos |

Dicas por passo:

- **Fuso (1)**: escolha o fuso da **fábrica**, não o do seu computador. Ele define o que é "hoje", os turnos e o
  horário dos relatórios. Manaus, por exemplo, é `America/Manaus` (uma hora atrás de Brasília).
- **Máquina de saída (2)**: é a máquina cuja contagem de peças é a produção da linha (normalmente a última). Sem ela
  não há OEE da linha.
- **Turnos (3)**: um turno que cruza a meia-noite pertence ao dia em que **começa**. Pausas (café, refeição) entram
  como paradas planejadas. Feriado num dia remove os turnos que começam nele — para não programar o turno da noite que
  **entra** no feriado, cadastre também o feriado só para aquele turno na véspera.
- **Tempo de ciclo ideal (4)**: use o **menor ciclo sustentável** de projeto ou demonstrado, não a média. Com molde de
  várias cavidades, informe as peças por ciclo.
- **Gateway (5)**: o código de cadastro tem o formato `XXXX-XXXX-XXXX`, vale 24 h e é de uso único. A tela espera o
  primeiro heartbeat ("Esperando o primeiro heartbeat… atualiza a cada 5 s").
- **Códigos de falha (7)**: sem mapa, toda falha do CLP vira `EQP-01` (falha mecânica). Mapear (por exemplo, código
  4xx → `EQP-03` pneumática) deixa o Pareto e os maus atores muito mais úteis.

### Checklist "pronto para produção" (passo 9)

| Item | Obrigatório | Regra |
|---|---|---|
| Fuso confirmado | sim | planta com fuso confirmado no passo 1 |
| Máquina de saída por linha | sim | toda linha com máquina de saída |
| Calendário | sim | ao menos 1 turno ativo ou operação 24 h explícita |
| Tempo de ciclo na saída | sim | máquina de saída de cada linha com tempo de ciclo para o produto padrão |
| Gateway online | sim | todo gateway da planta online |
| Sinal de operação | sim | toda máquina com sinal de "rodando" ou contador de ciclos/peças |
| Contador de peças na saída | sim | máquina de saída com contador de peças boas (ou total) |
| Dados chegando | sim | leituras de todas as tags obrigatórias nos últimos 15 min contínuos |
| Limite do contador | não (aviso) | contadores com valor máximo informado (virada do contador de 16/32 bits) |
| Mapa de falhas | não (aviso) | máquinas com sinal de falha têm o mapa de códigos |
| Segundo administrador | não (recomendação) | mais de um administrador no tenant |

<a id="csv-tags"></a>
## Como importar tags por CSV

Arquivo em UTF-8, separado por `;` (padrão do Excel em português) ou `,` — o sistema detecta pelo cabeçalho. Uma linha
por tag:

| Coluna | Obrigatória | Valores |
|---|---|---|
| `chave` | sim | identificador único no gateway, por exemplo `linha.maquina.sinal` |
| `linha`, `maquina` | sim | nomes cadastrados no passo 2 (acentos e maiúsculas não importam) |
| `posicao` | não | posição da máquina (para ciclos, curso e corrente de componente) |
| `papel` | sim | `analog`, `cycle_counter`, `cycle_pulse`, `run_state`, `part_counter_good`, `part_counter_scrap`, `part_counter_total`, `fault_state`, `stroke_time`, `speed`, `current` |
| `fonte` | sim | id da fonte do gateway (`opcua-plc`, `modbus-plc`, …) |
| `endereco` | sim | OPC UA: NodeId `ns=2;s=…`; Modbus: número do registrador (começando em 0) |
| `unit_id`, `tipo_registro`, `tipo_dado`, `ordem_palavras` | Modbus | `1–247`; `holding`, `input`, `coil`, `discrete`; `uint16`, `int16`, `uint32`, `int32`, `float32`; `big`, `little` |
| `contador_max` | contadores | por exemplo `65535` (16 bits) |
| `unidade`, `escala`, `offset`, `intervalo_ms` | não | conversão linear e intervalo de leitura (padrão 1 000 ms) |

Exemplo:

```csv
chave;linha;maquina;posicao;papel;fonte;endereco;unit_id;tipo_registro;tipo_dado;ordem_palavras;contador_max;unidade;escala;offset;intervalo_ms
montagem.fixacao.ciclos_a1;Linha de Montagem;Estação de Fixação;Cilindro A1;cycle_counter;modbus-plc;10;2;holding;uint16;big;65535;;1;0;1000
montagem.transportador.rodando;Linha de Montagem;Transportador de Saída;;run_state;opcua-plc;"ns=2;s=Transportador.Run";;;;;;;;;500
```

Regras:

- A importação é sempre uma **prévia**: nada é gravado até você confirmar. Cada linha mostra `ok`, `erro: …` ou "já
  existe — será atualizada". Reimportar o mesmo arquivo não duplica nada.
- O NodeId do OPC UA tem `;` dentro: deixe esse campo **entre aspas** (o Excel faz isso sozinho ao salvar em CSV).
- Os erros são os mesmos do gateway, porque é ele que vai ler o mapa. Exemplo: `cycle_pulse` só pode ser `coil` ou
  `discrete` — em `holding` aparece "cycle_pulse exige register_type coil/discrete" na linha do erro.
- Sempre prefira o **contador do CLP** (`cycle_counter`) ao pulso (`cycle_pulse`): o contador não perde ciclos quando
  a rede cai.

## Como navegar no OPC UA pelo gateway

O servidor nunca abre conexão para dentro da fábrica: quem navega é o gateway, a pedido.

1. Em **Importar tags → Navegar OPC UA**, escolha a fonte e (opcional) o nó inicial e a profundidade.
2. O pedido vai no próximo heartbeat do gateway; a árvore aparece em segundos. Sem resposta em 5 min: "gateway não
   respondeu — está online?".
3. Marque as variáveis, atribua papel, máquina e posição e confirme. A sugestão de papel (por exemplo, booleano com
   "run" no nome → `run_state`) é só sugestão: confira.

Nenhum valor de processo nem credencial é gravado nessa navegação. As credenciais do servidor OPC UA ficam no próprio
gateway (variáveis de ambiente).

## Como cuidar dos gateways

**Gateways** mostra a frota com o estado:

| Estado | Quer dizer |
|---|---|
| pendente | ainda não cadastrado (ou revogado) |
| online | conversando normalmente |
| degradado | o último heartbeat trouxe erros ou o gateway tem muitas leituras guardadas esperando envio |
| offline | sem contato há mais de 90 s (ou 3 heartbeats) |

- **Gateway offline**: a fábrica continua produzindo e o gateway guarda as leituras localmente; quando volta, envia
  tudo em ordem. As telas mostram "dados incompletos" até lá.
- **Revogar** (gateway roubado, trocado, descartado): invalida o token e a credencial MQTT; o servidor passa a
  descartar as leituras dele. A remoção da credencial no broker é um passo do time de operação — a tela mostra qual.
  Só dá para **remover** um gateway depois de revogar.
- **Mudar o mapa de tags**: o gateway recebe a nova configuração sozinho no próximo heartbeat.

<a id="usuarios"></a>
## Como cadastrar usuários, papéis e tablets

| Papel | Para quem | Vê / faz |
|---|---|---|
| Operador | quem opera a máquina | tablet da linha: estado, justificar/declarar paradas, trocar produto, chamar manutenção |
| Supervisor de produção | líder do turno | produção da sua área, paradas, relatório de turno, liberar máquina, fechar OS da área |
| Planejador de manutenção | PCM | planos, programação, OS, preditiva, componentes, KPIs de manutenção |
| Técnico | manutenção | app do técnico: OS atribuídas, execução, falhas, trocas |
| Gestor | gerente de produção/manutenção | tudo de produção e manutenção, metas |
| Administrador | TI/implantação | configuração, usuários, gateways, relatórios agendados |

- Uma pessoa pode ter mais de um papel. Defina as **linhas** (áreas) de cada usuário: elas filtram telas,
  notificações e relatórios.
- **Técnicos de plantão** por linha recebem os chamados emergenciais; sem plantão na linha, o chamado vai para
  planejamento e gestão.
- **Operadores** entram no tablet (`/op`) com **e-mail e senha** — crie um usuário para cada operador e atribua a
  linha dele (ele só vê e só justifica paradas dessa linha). Entrada por PIN/crachá com bloqueio após erros e o
  vínculo do tablet por código **ainda não existem** (pendentes no roadmap; veja [Supervisor](supervisor.md#tablet)).

<a id="relatorios"></a>
## Como configurar os relatórios agendados

**Relatórios agendados** → **Novo agendamento**:

| Relatório | Escopo | Período | Formatos | Padrão |
|---|---|---|---|---|
| Produção diária | planta (uma página por linha) | dia produtivo | PDF, Excel | todo dia 06:30 |
| Relatório de turno | linha | turno | PDF | fim de cada turno + 15 min |
| OEE semanal e perdas | planta ou linha | semana (seg–dom) | PDF, Excel | segunda 07:00 |
| Gestão mensal da manutenção | planta | mês | PDF, Excel | dia 1, 07:00 |
| Confiabilidade e vida por fabricante/modelo (Weibull) | empresa ou planta | todo o histórico | PDF, Excel | trimestral, 07:00 |

Como o horário funciona: o relatório **nunca sai antes de os dados fecharem** — fim do último turno do período
+ 30 min (15 min no relatório de turno) — e sai no horário escolhido quando possível. Exemplo: em Manaus, com
turnos A/B/C (o C termina 06:00), o diário de 05/10 marcado para 06:00 sai às **06:30** de 06/10. Sem turnos
cadastrados, o dia fecha à meia-noite.

- **Destinatários** são usuários do tenant por papel e área. Cada um recebe o que pode ver na tela: quem não tem acesso
  a horas/custos de manutenção recebe a versão sem essas seções; operadores e técnicos não recebem relatórios por
  e-mail por padrão.
- **E-mails externos** (cliente, matriz) só se você habilitar destinatários externos — com nome e finalidade
  cadastrados (LGPD), e todo e-mail traz o link "não quero mais receber". Três devoluções (bounce) desativam o
  destinatário externo.
- **Enviar agora** gera e envia o período atual; **Histórico** mostra as versões e as entregas. Gerar de novo depois de
  dados atrasados cria a **versão 2** (a anterior continua disponível).
- **E-mail não configurado**: os relatórios continuam sendo gerados e ficam para download; a tela avisa. Anexos acima de
  10 MB vão como link (expira em 7 dias e exige login).

<a id="primeiros-14-dias"></a>
## Primeiros 14 dias depois de conectar

Alguns números precisam de histórico para ficarem bons. Avise as equipes:

- **Datas projetadas** (vencimento de planos por uso, fim de vida de componentes, RUL da preditiva) usam o ritmo dos
  **últimos 14 dias**. Componentes instalados há menos de 14 dias usam os dias desde a instalação e aparecem com
  "baixa confiança". Mas os **planos** (e componentes cadastrados com data de instalação antiga) dividem o uso por
  14 desde o primeiro dia: nos primeiros 14 dias depois de conectar uma máquina, a data sai **mais distante** do que o
  real. Exemplo: um plano que vence de verdade em 15 dias pode aparecer com 42 dias depois de só 5 dias de dados.
  Nesse período, o planejador deve conferir o contador da máquina (o "próximo uso absoluto" do plano) antes de
  adiar uma troca.
- **Componentes cadastrados no CSV de ativos** começam com uso zero no FactoryConnect, mesmo que estejam na máquina há
  anos. Para planos que já existiam no papel, informe a **data e o uso da última execução** ao cadastrar o plano.
- **Preditiva**: cada componente monitorado precisa de pelo menos 20 janelas de dados para formar a referência
  ("aprendendo (12/20 janelas)"). Até lá só os limites absolutos avisam.
- **Confiabilidade** (MTBF, MCBF) e **Weibull** só ficam úteis com meses de dados e algumas falhas registradas.

## Erros comuns

| Erro | Consequência | Como evitar |
|---|---|---|
| Fuso do navegador em vez do da fábrica | "hoje", turnos e relatórios deslocados | confirmar o fuso no passo 1 |
| Linha sem máquina de saída | sem OEE da linha | indicar a máquina que conta as peças da linha |
| Tempo de ciclo ideal = média histórica | desempenho sempre ~100 %, perda de velocidade invisível | menor ciclo sustentável |
| Contador sem `contador_max` | virada do contador lida como "reset" | informar 65535 (16 bits) ou 4294967295 (32 bits) |
| `cycle_pulse` onde existe contador no CLP | ciclos perdidos quando o gateway cai | preferir `cycle_counter` |
| Sem mapa de códigos de falha | toda falha vira `EQP-01` | mapear os códigos do CLP |
| Um único administrador | empresa travada se ele sair | ter um segundo administrador |
| Turno extra não cadastrado | produção "fora do calendário", fora do OEE | cadastrar o turno extra |

## Perguntas frequentes

**O que é o modo demonstração ("Ver demonstração")?**
Um tenant separado, alimentado por uma **planta virtual** (simulada) 24 h por dia. Quem entra vê a faixa
"Demonstração — dados de uma planta simulada" em todas as telas e não consegue alterar nada. Nenhum dado de cliente
real aparece lá, e o acesso de demonstração nunca enxerga outro tenant.

**Posso apagar um gateway?**
Só depois de revogá-lo. As tags perdem a atribuição ao gateway.

**O relatório de turno de ontem não foi enviado.**
Veja **Histórico** do agendamento: falhas de geração são tentadas de novo (5, 15 e 60 min) e depois aparecem como
falha, com aviso ao administrador. Se o servidor ficou fora do ar, ao voltar ele envia os períodos pendentes dos
últimos 7 dias, uma vez cada.

**Um usuário saiu da empresa.**
Desative o usuário (o histórico de quem fez o quê continua). Se ele tinha um tablet vinculado ou era o único
administrador, revogue o tablet e promova outro administrador antes.
