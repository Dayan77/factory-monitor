# FactoryConnect 1.0 — Notas de versão

Primeira versão de disponibilidade geral do FactoryConnect: a plataforma que liga as máquinas da sua fábrica a
indicadores de eficiência, manutenção e confiabilidade calculados automaticamente e pelas normas.

> **Sobre esta versão.** A 1.0 reúne tudo o que foi construído nas versões 0.1 a 0.7 (histórico técnico completo no
> [CHANGELOG](../CHANGELOG.md)). Ela é publicada como `v1.0.0` pelo processo de release
> ([operations.md §11](operations.md)) depois que o checklist de entrada em produção ([go_live.md](go_live.md)) for
> concluído; até lá o conteúdo descrito aqui é o da versão 0.7.0. A plataforma foi validada em planta simulada e em
> cenários calculados à mão; **a primeira implantação em fábrica real (piloto) ainda vai acontecer** — veja
> "Limitações conhecidas".

---

## Destaques

- **Raio-X da fábrica**: navegue Fábrica → Linha → Máquina → Componente com os indicadores de cada nível e um
  **mapa da planta ao vivo** mostrando o que está rodando, parado, em falha, em manutenção ou em pausa.
- **OEE de verdade, sem apontamento**: paradas e micro-paradas detectadas a partir do sinal da máquina; o operador só
  informa o motivo, em poucos toques, no tablet da linha.
- **Manutenção pelo uso real**: cada componente (cilindro, motor, eixo de robô, válvula, garra, rolamento) tem os
  seus ciclos, horas e partidas contados; planos "o que vier primeiro" avisam em 80 % e 100 % da vida.
- **Confiabilidade e vida útil**: MTBF, MCBF, MTTR por componente, tipo, fabricante e máquina, e comparação de
  marcas e modelos pela análise de Weibull.
- **Preditiva**: alerta de degradação (tempo de curso, corrente, vibração, temperatura) com estimativa de quando o
  limite será atingido.
- **Relatórios** em PDF e Excel, sob demanda ou enviados por e-mail na frequência que você escolher.

## O que está incluído

### Conexão com a fábrica
- Gateway de borda para PC industrial ou Raspberry Pi, cadastrado por um código de uso único.
- Leitura **somente leitura** de CLPs e equipamentos por **Modbus TCP**, **OPC UA** e **MQTT**; nada é escrito no CLP
  e o programa da máquina não precisa mudar.
- Contagem de pulsos rápidos no próprio gateway, sem perder ciclo.
- Sem internet, o gateway guarda as leituras e reenvia quando a conexão volta; os indicadores do período são
  recalculados automaticamente e ficam iguais aos de uma conexão contínua.
- Só conexões de saída (HTTPS 443 e MQTT sobre TLS 8883): nenhuma porta aberta para dentro da fábrica.
- Assistente de implantação: importação de ativos e tags por planilha (CSV), navegação nos nós OPC UA pela tela e
  checklist "pronto para produção".

### Produção
- OEE (disponibilidade, desempenho, qualidade) e TEEP por máquina, linha, turno, dia e período — conforme a
  ISO 22400 e sempre recalculado dos tempos e peças somados (nunca média de porcentagens).
- Turnos, pausas, feriados, turnos extras e paradas planejadas no calendário de produção, no fuso da planta.
- Paradas e micro-paradas automáticas, motivos do operador, Pareto de perdas (com perda de velocidade e de
  qualidade), comparação entre turnos e produtos, tempo de ciclo ideal por produto.
- Cadência ao vivo e realizada do turno.

### Manutenção
- Planos por ciclos, horas, partidas ou dias — o que vier primeiro — e % de vida consumida.
- Ordens de serviço preventivas, corretivas e preditivas, com programação semanal, aprovação, execução pelo celular
  do técnico (com leitura de código/QR) e histórico.
- Troca de componente que zera os contadores do novo e preserva o histórico do antigo.
- Registro de falhas com a taxonomia da ISO 14224 (modo, causa, mecanismo, método de detecção).
- Indicadores de gestão: PMP (% de manutenção planejada), cumprimento da programação e backlog em semanas.
- Indicadores de confiabilidade: MTBF, MCBF, MTTR, tempo ativo de reparo (MART), taxa de falha por hora e por milhão
  de ciclos, disponibilidade inerente e operacional, MTTF, Weibull com intervalo de confiança.
- Integração com CMMS/ERP por API REST e webhooks assinados.

### Preditiva e assistente
- Linha de base por componente, detecção estatística de desvio e de tendência, vida restante estimada pela condição
  e pelo plano de produção.
- Assistente de IA (opcional) que responde perguntas consultando os seus dados reais; todo número da resposta é
  conferido contra a origem antes de aparecer.

### Para cada pessoa da equipe
- Telas por papel — operador (tablet), supervisor, planejador, técnico (celular), gerente e administrador — com tema
  claro e escuro, em monitor, tablet e celular.
- Manual do usuário por papel e por tarefa, com busca, em `factoryconnect.tech/docs`.
- Notificações no app e por e-mail, com preferências por usuário.

### Segurança e privacidade
- Dados de cada cliente isolados em toda consulta, com teste automático de isolamento em todos os módulos.
- Verificação em duas etapas (TOTP) opcional, sessões que você pode encerrar ("sair de todos os dispositivos"),
  bloqueio contra tentativas de senha, convites e redefinição de senha por link de uso único, trilha de auditoria.
- HTTPS com políticas de segurança estritas no navegador; MQTT com TLS e credencial por gateway; cifragem opcional
  das leituras desde o gateway.
- LGPD: o usuário exporta os próprios dados; o administrador anonimiza contas e define a retenção.

### Operação do serviço
- Backups diários com teste automático de restauração, monitoramento com alertas, ambiente de pré-produção para
  validar cada versão antes de chegar até você e volta automática para a versão anterior se uma atualização falhar.

## Requisitos

| Item | Requisito |
|---|---|
| Navegador | versão atual de Chrome, Edge, Firefox ou Safari |
| Tablet do operador | navegador atual; instalável como aplicativo (PWA) |
| Gateway | PC industrial x86-64 ou Raspberry Pi com sistema de 64 bits (arm64) e Docker; duas interfaces de rede recomendadas (automação e corporativa) |
| Rede | saída liberada para `app.factoryconnect.tech` nas portas 443 e 8883; DNS e NTP |
| CLP | Modbus TCP, OPC UA ou MQTT; outros protocolos por servidor OPC UA ou conversor |

## Limitações conhecidas

Preferimos dizer antes:

- **Piloto em andamento de planejamento.** Os números foram validados em planta simulada e em cenários calculados à
  mão, não ainda em uma fábrica de cliente. A primeira implantação é conduzida como piloto, com uma semana de linha
  de base e relatório de resultados ([pilot.md](pilot.md)).
- **OPC UA sem certificado.** O gateway ainda não usa a política de segurança OPC UA com certificado
  (`Basic256Sha256`). Até isso chegar, a conexão OPC UA deve ficar numa rede de automação isolada, combinada com a
  sua TI.
- **Entrada do operador por e-mail e senha.** Entrada por PIN ou crachá e vínculo do tablet à linha por código
  ainda não estão disponíveis.
- **Parada planejada para manutenção por máquina**: ao programar uma OS que para a máquina, o supervisor recebe o
  pedido, mas a parada ainda é lançada à mão no calendário de produção.
- **Atraso da leitura ao vivo com 200 máquinas**: na carga nominal completa (200 máquinas, ~2 600 leituras/s) o
  atraso entre a leitura e a tela ficou em p95 8,4 s no nosso ambiente de teste de 4 vCPU (meta: 2 s; mediana
  0,7 s). Nenhuma leitura se perde e os indicadores calculados não são afetados. A confirmação no servidor de
  produção (8 vCPU) é um item do go-live ([performance.md §1.0](performance.md)).
- **Integração CMMS/ERP** pela API genérica; não há conector pronto para SAP PM, TOTVS ou Engeman.
- **Idioma**: português. Inglês está planejado.
- **Primeiros 14 dias** depois de conectar uma máquina: as previsões de troca usam menos histórico e aparecem
  marcadas como de baixa confiança.

## Atualizando a partir de versões anteriores

Não há ação do cliente: a atualização é feita pela FactoryConnect. Depois da atualização:
- Todos os usuários entram de novo uma vez (as sessões passaram a ser controladas pelo servidor).
- As sessões agora duram 15 minutos com renovação automática enquanto o app está aberto.
- Gateways instalados antes da credencial por gateway recebem da equipe FactoryConnect usuário e senha MQTT
  próprios (e, quando habilitado, o endereço TLS 8883); não é preciso trocar o equipamento.

## Suporte

Fale com a equipe FactoryConnect pelo contato do site. Para incidentes de segurança, os contatos e prazos são os
acordados no contrato e no checklist de implantação.
