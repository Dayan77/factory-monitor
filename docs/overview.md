# FactoryConnect — Visão geral do produto

Documento executivo para o dono do projeto e para conversas com potenciais clientes. Situação em **07/10/2026**,
versão **0.7.0** (candidata à 1.0 — [notas de versão](release_notes_v1.md)). Tudo o que está aqui foi construído e
testado; o que **ainda não foi provado em fábrica real** está dito explicitamente na §8. O FactoryConnect **ainda
não tem cliente em produção nem case publicado**: nenhum número deste documento vem de cliente.

---

## 1. O problema

Na maior parte das fábricas de médio porte, as perguntas de toda manhã — *como foi o turno, onde perdemos tempo, o
que está parado agora, o que vence esta semana, o que está degradando* — são respondidas com apontamento em papel,
planilha montada no fim do mês e a memória do supervisor. Isso tem três consequências conhecidas:

- **O OEE apontado à mão é otimista**: micro-paradas, perda de velocidade e paradas curtas não aparecem no papel.
- **Manutenção por calendário**, não por uso: troca-se cedo o que roda pouco e tarde o que roda muito, porque
  ninguém sabe quantos ciclos um cilindro ou uma garra já fez.
- **Indicadores que não se comparam**: cada planta calcula OEE e MTBF do seu jeito, então a reunião discute a conta
  e não a perda.

## 2. O que o FactoryConnect faz

Liga as máquinas (CLPs, sensores, contadores) a telas e relatórios que respondem a essas perguntas, com os números
calculados **automaticamente a partir do sinal da máquina** e **pelas definições das normas**.

| Área | O que mede | Exemplo de pergunta respondida |
|---|---|---|
| **Eficiência** | OEE (disponibilidade × desempenho × qualidade), TEEP, cadência, paradas e micro-paradas detectadas automaticamente, Pareto de perdas, comparação entre turnos e produtos | "Onde perdemos mais tempo ontem?" |
| **Uso de componentes** | ciclos, horas e partidas de cada componente (cilindro, motor, eixo de robô, válvula, garra, rolamento), % de vida consumida, data prevista de troca | "Quais componentes estão perto do fim da vida?" |
| **Manutenção** | planos "o que vier primeiro" (ciclos / horas / dias), ordens de serviço preventiva / corretiva / preditiva, falhas com taxonomia, PMP, cumprimento da programação, backlog | "O que vence esta semana? Estamos mais preventivos ou apagando incêndio?" |
| **Confiabilidade** | MTBF, MCBF (ciclos entre falhas), MTTR, MART, taxa de falha, disponibilidade inerente e operacional, MTTF e Weibull por fabricante/modelo | "Quem são os maus atores? A marca A dura mais que a B?" |
| **Preditiva** | desvio de tempo de curso, corrente, vibração e temperatura contra a linha de base de cada componente; tendência e vida restante estimada pela condição | "O que está degradando e quando deve falhar?" |
| **Ao vivo** | mapa da planta com o estado de cada máquina, avisos por e-mail/notificação | "O que está parado agora?" |
| **Assistente de IA** (opcional) | perguntas em linguagem natural respondidas por ferramentas que consultam os dados reais; todo número da resposta é conferido contra a origem antes de aparecer | "Qual linha teve o pior OEE na semana e por quê?" |

Relatórios em PDF/Excel (turno, diário, semanal de OEE, mensal de manutenção, vida útil), sob demanda ou agendados
por e-mail. Integração com CMMS/ERP por API REST e webhooks assinados.

## 3. Como conecta à fábrica

```
 CLP / sensores ──(Modbus TCP · OPC UA · MQTT, só leitura)──► gateway FactoryConnect ──(HTTPS 443 + MQTT/TLS 8883, só saída)──► nuvem
                                                               IPC x86 ou Raspberry Pi
```

- **Gateway de borda** instalado na fábrica (imagem pronta para IPC industrial ou Raspberry Pi, amd64/arm64). Lê os
  CLPs **somente leitura** — o FactoryConnect nunca escreve no CLP e não exige mudar o programa da máquina.
- **Protocolos**: Modbus TCP, OPC UA e MQTT. CLPs que só falam outros protocolos (S7, EtherNet/IP, PROFINET)
  precisam de servidor OPC UA ou conversor.
- **Pulsos rápidos** (cilindros, contadores de ciclo) são contados no próprio gateway, sem perder ciclo; sinais
  analógicos usam banda morta para não inundar a rede.
- **Sem internet, nada se perde**: o gateway guarda tudo num buffer local e reenvia em ordem quando a conexão volta;
  a nuvem reprocessa os períodos afetados e chega ao mesmo resultado que teria com a conexão contínua.
- **Só conexões de saída**: nenhuma porta aberta de fora para dentro da fábrica. Cada gateway tem credencial
  própria, revogável na hora, e só publica nos tópicos do seu cliente.
- **Implantação guiada**: cadastro do gateway por código de uso único, importação de ativos e tags por planilha
  (CSV), navegação nos nós OPC UA pela tela, checklist "pronto para produção".

## 4. Telas por papel

Cada usuário vê só as telas e as linhas do seu papel; uma pessoa pode ter mais de um papel.
Manual completo, por papel e por tarefa, em [manual/](manual/README.md) (também publicado em `/docs` do site).

| Papel | Onde usa | Principais telas |
|---|---|---|
| **Operador** | tablet fixo na linha | estado da máquina, justificar/declarar paradas em poucos toques (funciona sem rede e sincroniza depois), trocar produto, chamar manutenção |
| **Supervisor de produção** | computador / tablet | Raio-X da linha, mapa ao vivo, Paradas (não justificadas), Relatório de turno, liberar máquina |
| **Planejador (PCM)** | computador | Próximas manutenções, Programação da semana, Ordens de serviço, Componentes por % de vida, Preditiva |
| **Técnico de manutenção** | celular | OS atribuídas, execução com leitura de código de barras, registro de falha (modo/causa/mecanismo), troca de componente |
| **Gerente** (produção ou manutenção) | computador | Raio-X Fábrica → Linha → Máquina → Componente, OEE, Gestão da manutenção (PMP, cumprimento, backlog), Confiabilidade, relatórios |
| **Administrador** | computador | assistente de implantação, usuários e papéis, gateways, relatórios agendados, privacidade |

Toda tela tem tema claro e escuro, funciona em monitor, tablet e celular, e foi verificada com varredura automática
de acessibilidade (axe) sem violações.

## 5. Indicadores e normas

| Norma / referência | Onde se aplica |
|---|---|
| **ISO 22400** (KPIs de manufatura) | OEE e seus tempos (POT, PBT, APT…), disponibilidade, desempenho, qualidade, TEEP |
| **ISO 14224** (dados de falha e manutenção) | taxonomia de falhas (modo, causa, mecanismo, método de detecção), MTBF, MTTR, MART |
| **SEMI E10** e práticas TPM / RCM | classificação de tempos de equipamento, MCBF para pneumática e robôs, planos "o que vier primeiro", PMP |
| **IEC 62443** (zonas e condutos) | checklist de rede e segurança usado com a TI do cliente na implantação |
| **LGPD** (Lei 13.709/2018) | dados pessoais dos usuários e do formulário do site |

Princípios que valem em toda tela e relatório (definidos em [kpis.md](kpis.md) e [domain.md](domain.md)):

- **Nada é média de porcentagens**: o OEE da linha, do dia ou da fábrica é recalculado dos tempos e peças somados.
- **Indefinido aparece como "—" com o motivo** ("sem tempo planejado", "dados insuficientes"), nunca como 0 % ou 100 %.
- **Número estimado é marcado** como estimado (ex.: ciclos deduzidos das peças quando o componente não tem sensor).
- **Nenhum dado falso sem aviso**: sem dado, a tela diz que não há dado; a demonstração pública é identificada.
- **Rastreável**: cada número leva à sua causa (a barra do Pareto abre as paradas daquele motivo).
- Cada indicador tem fórmula, unidade, casos de borda e um **exemplo numérico que é teste automático**.

## 6. Segurança

- **Isolamento entre clientes** em toda consulta, com teste automático gerado da lista de rotas: todo endpoint ×
  todo papel × usuário de outro cliente.
- **Acesso**: sessões revogáveis ("sair de todos os dispositivos"), tokens de curta duração com rotação de chave,
  verificação em duas etapas (TOTP) opcional, bloqueio progressivo contra força bruta, política de senhas,
  convites e redefinição por link de uso único, trilha de auditoria de quem fez o quê.
- **Transporte**: HTTPS com HSTS e política de conteúdo estrita no app e no site; MQTT sobre TLS com usuário e
  permissão por gateway; payload opcionalmente cifrado (AES-256-GCM) desde o gateway.
- **Cadeia de suprimentos**: varredura de vulnerabilidades das dependências e das imagens, varredura de segredos no
  histórico do código e inventário de componentes (SBOM) em todo build; atualizações automáticas propostas.
- **Privacidade (LGPD)**: exportação dos dados pessoais pelo próprio usuário, anonimização, retenção configurável;
  IP nunca guardado completo. O assistente de IA não envia e-mail, telefone nem token ao provedor do modelo.
- Detalhes: [security.md](security.md), [privacy.md](privacy.md), checklist de rede: [pilot.md §5](pilot.md).

## 7. Modelo de implantação

- **SaaS multi-cliente** em `app.factoryconnect.tech`: o cliente instala só o gateway; servidor, banco, séries
  temporais, atualizações, backup e monitoramento ficam com a FactoryConnect.
- **Infraestrutura**: contêineres em servidor dedicado (VPS) com HTTPS automático, ambiente de pré-produção
  (staging) separado, backups diários com teste automático de restauração, alertas por e-mail (ingestão atrasada,
  gateway sem contato, disco, certificado, backup antigo).
- **Versões**: cada versão é publicada com número, notas e imagens rastreáveis; implantação em produção só com
  aprovação manual do dono e com volta automática para a versão anterior em caso de falha.
- **Dimensionamento** (medido, [performance.md](performance.md)): um servidor de 4 vCPU atendeu ~100 máquinas
  (≈ 1 300 leituras/s) dentro das metas de latência; para ~200 máquinas recomendamos 8 vCPU (ainda não medido em
  hardware dedicado).
- Instalação dedicada (nuvem ou servidor do próprio cliente) é tecnicamente possível com os mesmos contêineres, mas
  **não** foi empacotada nem testada como oferta.

## 8. O que foi validado — e como

| Como | O que prova |
|---|---|
| **Cenários de ouro A–F** ([scenarios/](scenarios/README.md)) | entradas exatas e resultados esperados calculados à mão, conta por conta: uso de componentes com rollover/reset/leitura corrompida (A), produção num turno que cruza a meia-noite (B), OEE de um turno (C), linha com gargalo, mix de produtos, feriado e turno extra (D), manutenção e confiabilidade (E), preditiva (F). Um oráculo independente recalcula tudo, e o servidor tem que dar o mesmo número com tolerância de 10⁻⁶ |
| **Planta virtual** (`edge/virtual_plant/`) | CLPs simulados (OPC UA + Modbus) com cilindros, motores, robô, contador de peças, falhas e desgaste progressivo, lidos pelo **mesmo gateway** e pelo mesmo caminho de dados da produção. Base dos testes de ponta a ponta e da demonstração pública |
| **Auditoria independente dos KPIs** ([audit_kpis.md](audit_kpis.md)) | cada fórmula do código conferida contra a especificação; 20 divergências encontradas no Sprint 6, todas corrigidas e **reverificadas** no Sprint 7 com contraexemplos novos; 1 ressalva aberta (A-19b, só a exibição da cadência ao vivo no mapa) |
| **Testes automáticos** | servidor, gateway, web, scripts e infraestrutura em todo push; navegador real (Playwright) em cada tela nova, temas claro/escuro, monitor/tablet/celular |
| **Quedas longas** | 72 h de buffer do gateway descarregadas de uma vez dão os mesmos eventos, falhas e micro-paradas que o processamento contínuo |
| **Carga** | 5 clientes × até 4 linhas × 10 máquinas com 30 dias de histórico, pelo caminho real (broker → ingestão → bancos → detectores) com telas e relatórios ao mesmo tempo, sem perder leitura |

## 9. O que ainda precisa de um piloto real

Nada disso pode ser prometido a um cliente antes do piloto ([pilot.md](pilot.md) — uma linha, 10 semanas, semana de
linha de base, relatório "antes × depois" com as mesmas definições):

- **Conexão com CLPs reais** de fabricantes diferentes, na rede do cliente, sem afetar a produção (testado só contra
  servidores simulados).
- **Precisão em campo**: erro da contagem de peças contra contagem manual (meta ≤ 1 %) e % das paradas ≥ 2 min
  detectadas automaticamente (meta ≥ 95 %).
- **Adoção**: se operadores justificam as paradas e se supervisores e gerentes usam as telas na rotina.
- **Ganho**: qualquer melhoria de OEE ou redução de perda. **Não existe hoje nenhum resultado de ganho**.
- **Preditiva com desgaste real**: os algoritmos foram validados com desgaste simulado; a taxa de alarmes falsos e
  a antecedência em equipamento real só se conhecem no campo.
- **Operação em produção**: o go-live ([go_live.md](go_live.md)) ainda não foi executado.

Antes do piloto também é preciso fechar duas lacunas do produto que o checklist do piloto pressupõe: **OPC UA com
certificado** (política `Basic256Sha256`) no gateway e **entrada do operador por PIN** no tablet (hoje é e-mail e
senha) — ou combiná-las por escrito com a TI do cliente. Lista completa de pendências: [roadmap.md](roadmap.md#pendências-honestas).

## 10. Como mostrar o produto hoje

- **Demonstração pública** ("Ver demonstração" no site): fábrica fictícia, identificada como demonstração,
  alimentada pela planta virtual em tempo real, somente leitura.
- **Material técnico** para a TI do cliente: [architecture.md](architecture.md), [security.md](security.md),
  [pilot.md](pilot.md) §5.
- **O que dizer**: "medimos pelas normas, automaticamente, a partir do sinal da máquina; está validado em planta
  simulada com cenários calculados à mão; procuramos a primeira linha piloto". **O que não dizer**: resultados de
  clientes, percentuais de ganho, compatibilidade testada com um CLP específico.
