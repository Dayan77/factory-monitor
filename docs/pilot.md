# FactoryConnect — Plano do primeiro piloto em fábrica real

Roteiro prático para o dono do projeto conduzir, junto com o cliente, o primeiro piloto do FactoryConnect numa
linha de produção real (roadmap, Fase 6: "Piloto real em uma linha de cliente, com relatório de resultados").
Pode ser enviado ao cliente como está; as partes marcadas **[interno]** são só para a equipe FactoryConnect.

Resumo em uma frase: **uma linha, 10 semanas, coleta automática a partir do CLP, uma semana de linha de base sem
mudar nada, seis semanas de uso com rotina diária, e um relatório final que compara "antes" e "depois" com as
mesmas definições (ISO 22400 / ISO 14224).**

---

## 1. O que o piloto precisa provar

| # | Hipótese | Como sabemos que foi provada |
|---|---|---|
| H1 | Conseguimos coletar os dados da linha **sem mexer no programa do CLP** e sem risco para a produção | cobertura de dados ≥ 95 % do tempo programado (`data_coverage`, kpis.md §0) em todas as semanas a partir da 2ª; zero paradas causadas pelo gateway |
| H2 | Os números são **confiáveis** | contagem de peças com erro ≤ 1 % contra contagem manual; ≥ 95 % das paradas ≥ 2 min detectadas automaticamente (conferidas com o diário de bordo do operador na semana de base) |
| H3 | A equipe **usa** no dia a dia | ≥ 90 % do tempo de parada justificado (motivo informado) até o fim de cada turno; relatório diário aberto ≥ 4 dias por semana pelo supervisor; reunião semanal usando a revisão de OEE (reports.md §1.3) |
| H4 | A visibilidade gera **ganho mensurável** | ver §8: ganho de OEE e/ou redução da maior perda frente à linha de base, medidos do mesmo jeito |

Fora do escopo do piloto: integração com ERP/CMMS, várias linhas, manutenção preditiva com sensores novos (pode
entrar como extensão se a linha tiver motores/cilindros críticos e o cliente quiser — §4.3).

---

## 2. Escolha da linha

Pontuar cada candidata de 0 a 2 em cada critério e escolher a de maior total (empate: a de maior interesse do
patrocinador). Linha com **0** em qualquer critério eliminatório (E) fica fora.

| Critério | 0 | 1 | 2 |
|---|---|---|---|
| (E) Acesso ao CLP | sem acesso / CLP sem rede | Modbus TCP ou rede com restrição | OPC UA nativo (ex.: CLPs recentes com servidor OPC UA) ou Modbus TCP liberado |
| (E) Linha estável | vai passar por reforma/troca nos próximos 3 meses | mudanças menores previstas | sem mudanças previstas |
| Máquina de saída clara | não há ponto único de contagem | contagem possível com sensor extra | contador de peças já existe no CLP |
| Sinais disponíveis | só contagem | contagem + rodando/parado | contagem + rodando/parado + falha com código |
| Turnos | 1 turno | 2 turnos | 3 turnos (mais dados em menos semanas) |
| Dor conhecida | ninguém reclama | perdas percebidas, sem número | perda crônica reconhecida (paradas, setup, micro-paradas) |
| Registros atuais | nenhum | planilha de produção | apontamento de paradas/OEE manual (permite comparar "antes") |
| Campeão local | ninguém | supervisor interessado | supervisor + manutenção comprometidos, com tempo reservado |
| Componentes por ciclo (bônus manutenção) | — | alguns cilindros/motores | cilindros, válvulas, motores críticos com histórico de falha |

---

## 3. Pré-requisitos

### 3.1 Do cliente

- **Patrocinador** (gerente de produção ou de planta) com poder de decisão e 30 min por semana.
- **Pontos focais**: supervisor de produção da linha, líder de manutenção, automação (quem conhece o CLP), TI.
- Documentação do CLP: modelo/firmware, lista de variáveis ou programa para leitura, endereços Modbus ou acesso ao
  servidor OPC UA; quem pode criar um usuário OPC UA **somente leitura**.
- Calendário de turnos e pausas; lista de produtos com **tempo de ciclo ideal** (PRI) por máquina; motivos de
  parada que já usam (para mapear no catálogo padrão `EQP-*`, `SET-*`, `MAT-*`, `PLN-*`…).
- Registros "antes": 4–8 semanas de produção e, se houver, de paradas/OEE manual (para a comparação de §8).
- Contrato do piloto, acordo de confidencialidade e acordo de tratamento de dados (LGPD: os únicos dados pessoais
  são nomes/e-mails dos usuários).
- Ponto de rede e energia (24 VDC ou 127/220 VAC) no painel onde o gateway ficará; Wi-Fi ou rede para os tablets.

### 3.2 Da FactoryConnect [interno]

- Tenant do cliente criado em produção, com backups verificados (operations.md §4) e monitoramento ativo (§8).
- Gateway testado em bancada contra a planta virtual com o **mesmo mapa** de tags (CSV do workshop, sprint6.md §4.1).
- Broker MQTT de produção com TLS e credencial do gateway (gateways.md, operations.md §6).
- Usuários criados com papéis (supervisor, operadores com PIN, planejador/técnico), relatórios agendados
  configurados (reports.md §5.1).
- Deploy em produção só com confirmação explícita do dono do projeto (regra do projeto).

---

## 4. Lista de material

### 4.1 Obrigatório

| Item | Qtde | Especificação mínima | Observação |
|---|---|---|---|
| Gateway (PC industrial) | 1 | x86-64, 4 núcleos, 8 GB RAM, SSD industrial 128 GB, **2 portas Ethernet** (rede de automação e rede corporativa), sem ventoinha, trilho DIN, 0–50 °C | roda a imagem do edge (Docker); buffer local SQLite aguenta dias sem internet |
| Fonte e proteção | 1 | fonte 24 VDC do painel ou nobreak pequeno (≥ 15 min) | evita perder buffer em queda de energia |
| Cabo de rede blindado e conectores | conforme visita | Cat6 S/FTP | passar longe de cabos de potência |
| Tablet do operador | 1 por linha (ou por posto) | 10", Android recente, Chrome, capa robusta, suporte de parede, carregador fixo | PWA do operador (sprint6.md §3) |
| Celular do técnico | os que o cliente já tem | Android/iOS com Chrome/Safari | PWA do técnico (`/m`) |

### 4.2 Conforme o que o CLP não fornecer

| Falta no CLP | Solução | Material |
|---|---|---|
| Contador de peças da máquina de saída | sensor de presença na saída + módulo de E/S remoto | sensor fotoelétrico/indutivo, módulo de E/S remoto Modbus TCP (entradas digitais rápidas) |
| Sinal rodando/parado | contato auxiliar do motor principal ou da esteira | entrada digital no mesmo módulo |
| Código de falha | — | sem código: paradas viram `EQP-01` ou motivo do operador (D1/D2); não bloqueia o piloto |
| Ciclos de cilindro (manutenção) | sensor de fim de curso já existente → entrada digital (`cycle_pulse`) | entrada digital rápida (coil/discrete) |

### 4.3 Opcional (extensão preditiva)

Transdutor de corrente no motor crítico (entrada analógica 4–20 mA), sensor de vibração com saída 4–20 mA ou
Modbus, e uso do tempo de curso de cilindros já medido pelo CLP. Só depois da semana 4, se H1/H2 estiverem ok.

### 4.4 Opcional (gestão à vista)

TV na linha com a tela de TV (`/tv/lines/:id`) ou o mapa da planta (sprint6.md §1), mini-PC ou TV com navegador.

---

## 5. Checklist de rede e segurança com a TI do cliente

Levar impresso para a reunião com TI/automação. Referência de boas práticas: IEC 62443 (zonas e condutos).

**Arquitetura**
- [ ] Gateway com **duas interfaces**: uma na rede de automação (lê o CLP), outra na rede corporativa/DMZ (fala com
      a nuvem). **Sem roteamento** entre as duas no gateway (IP forwarding desligado).
- [ ] Gateway só faz conexões **de saída**. Nenhuma porta aberta de fora para dentro da fábrica.
- [ ] Destinos de saída liberados no firewall: `app.factoryconnect.tech` **443/TCP** (HTTPS: configuração,
      cadastro, heartbeat) e **8883/TCP** (MQTT sobre TLS). Nada mais.
- [ ] DNS e **NTP** liberados (o horário do gateway é usado nos dados; relógio errado distorce paradas e turnos).
      Se houver servidor NTP interno, informar o endereço.
- [ ] Proxy corporativo? Informar se há inspeção TLS (o gateway precisa confiar no certificado do proxy) ou liberar
      os dois destinos sem inspeção.

**Acesso ao CLP (somente leitura)**
- [ ] OPC UA: usuário próprio **somente leitura**, política de segurança `Basic256Sha256` com assinatura e
      criptografia, certificado do gateway confiado no CLP. Credenciais ficam só no gateway (variáveis de ambiente),
      nunca na nuvem nem em planilha.
- [ ] Modbus TCP (não tem autenticação): firewall/ACL do switch permitindo **só o IP do gateway** na porta 502 do
      CLP/módulo de E/S. O FactoryConnect **só usa funções de leitura** (nunca escreve no CLP).
- [ ] Taxa de leitura combinada com automação (padrão 1 s por tag; contadores rápidos por pulso com 20 ms) e teste
      de carga do CLP antes de ligar em produção.

**Gateway**
- [ ] Imagem com sistema atualizado, sem contas padrão, SSH desabilitado ou só por chave, USB desabilitado,
      disco cifrado se o cliente exigir.
- [ ] Credencial única por gateway (código de cadastro de uso único, 24 h — gateways.md); revogável na hora.
- [ ] Atualizações do gateway: janela combinada; nunca durante a produção sem aviso.
- [ ] Acesso remoto para suporte: **nenhum por padrão**. Se necessário, pela VPN do cliente, com pedido e prazo.

**Dados**
- [ ] Onde ficam os dados (servidor em nuvem no Brasil/exterior — informar), retenção, backup diário com cópia
      fora do servidor (operations.md §4), exclusão ao fim do contrato.
- [ ] Dados pessoais: só nome/e-mail/papel dos usuários; operadores podem usar PIN sem e-mail.
- [ ] Contatos de incidente dos dois lados e tempo de resposta.

---

## 6. Workshop de mapeamento de tags (meio dia, na fábrica)

**Participantes**: automação do cliente, supervisor da linha, líder de manutenção, FactoryConnect (2 pessoas).

**Agenda**
1. (30 min) Caminhar a linha: fluxo das peças, máquina de saída, gargalo, onde as paradas acontecem.
2. (90 min) Para cada máquina, preencher o CSV de tags (sprint6.md §4.1): papel de cada sinal
   (`run_state`, `part_counter_good`/`scrap`/`total`, `fault_state`, `cycle_counter`/`cycle_pulse`, `stroke_time`,
   `current`…), endereço, tipo de dado, **`counter_max`** de cada contador (16 bits = 65 535; 32 bits =
   4 294 967 295), unidade.
3. (45 min) Códigos de falha do CLP → `fault_code_map` (código → motivo `EQP-*` e modo de falha), e os motivos de
   parada do cliente → catálogo.
4. (30 min) Calendário (turnos, pausas, feriados) e produtos com PRI. Conferir: PRI é o tempo **ideal** (projeto
   ou o melhor sustentado), não a média — senão o desempenho passa de 100 % ou esconde perda de velocidade.
5. (30 min) Teste no campo com o gateway ligado (ou um notebook com o simulador de leitura): **contar 20 peças à
   mão** e comparar com o contador; parar a máquina e ver `run_state` mudar; provocar (ou esperar) uma falha e ver o
   código. Anotar divergências.

**Saídas**: CSV de tags validado pelo `config-preview` do gateway, `fault_code_map`, calendário, produtos/PRI,
lista de pendências com dono e data.

---

## 7. Semana de linha de base

Objetivo: medir o "antes" **com o sistema**, sem mudar nada na operação. Sem ela, qualquer ganho do piloto é
opinião.

- Duração: **1 semana completa de produção** (todos os turnos), depois de 3–5 dias de estabilização da coleta.
- O que pode: operadores justificam as paradas no tablet (é coleta, não ação); a FactoryConnect corrige mapeamento.
- O que **não** pode: reuniões de ação sobre as perdas, mudança de método, metas novas. (Se algo urgente aparecer —
  falha grave — trata-se normalmente e anota-se no diário do piloto.)
- Portões de qualidade de dados (todos verdes para a semana valer como base; senão, repetir a semana):
  `data_coverage ≥ 0,95`; erro de contagem ≤ 1 % (contagem manual de 3 lotes); ≥ 95 % das paradas ≥ 2 min do
  diário de bordo do operador aparecem no sistema; PRI cadastrado para todos os produtos produzidos
  (sem `product_unknown`); nenhum `ideal_cycle_time_suspect`.
- Comparação com o registro manual do cliente: o OEE manual costuma sair **maior** que o medido (paradas curtas e
  perdas de velocidade não são anotadas). Essa diferença é um resultado do piloto ("gap de medição"), não erro.

---

## 8. Métricas de sucesso (antes × depois)

Regras de comparação (as mesmas de kpis.md):
1. Mesmas definições nos dois períodos (ISO 22400; agregação por **soma** dos componentes — nunca média de OEE de
   turnos ou semanas, kpis.md §1.6).
2. Períodos comparáveis: PBT dentro de ±10 % e mesmo mix de produtos (senão comparar por produto).
3. "Depois" = média ponderada (por soma) das **semanas 7 e 8** (depois de a rotina estabilizar), contra a semana de
   base.

| Métrica | Definição | Antes (fonte) | Meta do piloto |
|---|---|---|---|
| Cobertura de dados | `data_coverage` (kpis.md §0) | — | ≥ 95 % todas as semanas |
| Exatidão da contagem | erro vs. contagem manual | — | ≤ 1 % |
| Tempo justificado | % do tempo de parada com motivo (D2) | diário de bordo | ≥ 90 % |
| OEE da linha | §1.5, pela máquina de saída | semana de base | **+5 pp** ou mais |
| Maior perda do Pareto | minutos/semana da perda nº 1 da base (§1.10) | semana de base | **−30 %** |
| MTTR | §2.3 (relógio, de `failed_at`) | semana de base + histórico | −15 % |
| Tempo até saber o resultado de ontem | do fim do dia produtivo até o número na mão do gerente | processo atual (planilha) | ≤ 1 h (relatório diário, reports.md) |
| Uso | dias/semana com relatório aberto; % paradas justificadas no turno | — | ≥ 4 dias; ≥ 90 % |

**Exemplo de comparação (caso `pilot_before_after`, conferido por teste)** — linha de 3 turnos, 5 dias,
PBT 6 750 min por semana nos dois períodos, PRI 30 s:

| | PBT | ADOT | APT | Σ PRI·PQ | Σ PRI·GQ | A | P | Q | OEE |
|---|---|---|---|---|---|---|---|---|---|
| Base | 6 750 | 1 350 | 5 400 | 4 590 | 4 452,3 | 0,80 | 0,85 | 0,97 | **0,659600** |
| Semanas 7–8 (por semana) | 6 750 | 945 | 5 805 | 4 992,3 | 4 867,4925 | 0,86 | 0,86 | 0,975 | **0,721110** |

Ganho = **+6,15 pp** de OEE = 415,1925 min/semana de tempo produtivo líquido = **≈ 830 peças boas a mais por
semana** (415,1925 / 0,5 min) **sem investimento em máquina**. A maior perda da base (falha pneumática `EQP-03`,
420 min) caiu para 210 min (**−50 %**). O OEE manual que o cliente reportava antes era 0,78: o "gap de medição"
de 12 pp (0,78 − 0,66) é o tamanho da perda que não aparecia na planilha.

---

## 9. Cronograma (10 semanas)

| Semana | Atividade | Responsável (R) / aprovação (A) | Entrega |
|---|---|---|---|
| −2 | Reunião de abertura, escolha da linha (§2), checklist de TI (§5), contrato/NDA/LGPD | FactoryConnect (R), patrocinador (A) | linha escolhida, checklist assinado |
| −1 | Workshop de tags (§6), compra/separação de material (§4), teste do gateway em bancada | FactoryConnect + automação (R) | CSV validado, gateway pronto |
| 0 | Instalação (gateway, sensores extras, tablet), cadastro do gateway, usuários; treinamento de 30 min por turno | FactoryConnect + manutenção (R) | dados chegando; `data_coverage` medido |
| 1 | Estabilização: corrigir mapeamento, PRI, motivos; conferir contagem | FactoryConnect (R) | portões de §7 verdes |
| 2 | **Semana de linha de base** (sem ação) | supervisor (R) | relatório da base |
| 3 | Apresentação da base: Pareto, gap de medição, escolha das 3 perdas a atacar | patrocinador (A) | plano de ação |
| 4–8 | Rotina: relatório diário 06:30, justificativa de paradas no turno, reunião semanal de 30 min com a revisão de OEE, ações nas perdas; manutenção usa chamados e OS | supervisor + manutenção (R) | ações registradas, semanas 7–8 medidas |
| 9 | Relatório de resultados (§11) e decisão: expandir, ajustar ou encerrar | FactoryConnect (R), patrocinador (A) | relatório final, proposta comercial |

---

## 10. Riscos e mitigação

| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| TI não libera saída para a nuvem a tempo | média | atrasa tudo | checklist (§5) na semana −2; alternativa: roteador 4G dedicado só para o gateway (com aprovação da TI) |
| CLP não tem OPC UA nem Modbus liberado | média | sem coleta | módulo de E/S remoto com sensores próprios (§4.2) — independe do CLP |
| Leitura sobrecarrega o CLP | baixa | risco à produção | só leitura, taxa combinada, teste de carga antes, possibilidade de desligar o gateway sem efeito na linha |
| Contagem diverge da real | média | perda de confiança | contagem manual na semana 1; corrigir `counter_max`, rollover (C5) ou ponto de contagem |
| Operadores não justificam paradas | alta | Pareto com "sem motivo" | motivos em botões grandes (≤ 3 toques), meta por turno no relatório, supervisor cobra no início do turno |
| PRI errado (desempenho > 100 % ou perda escondida) | média | OEE sem sentido | conferir com engenharia no workshop; o sistema avisa `ideal_cycle_time_suspect` |
| Relógio do gateway errado | baixa | paradas/turnos deslocados | NTP obrigatório (§5); alerta de desvio de relógio do edge |
| Mudança de produto/rotina durante a base | média | base inválida | registrar no diário do piloto; repetir a semana se necessário |
| Falta de tempo do campeão local | média | uso baixo | 30 min/semana combinados com o patrocinador desde a abertura |
| Expectativa de "IA resolve tudo" | média | frustração | deixar claro: o piloto entrega medição confiável e rotina; preditiva e IA são extensões |

---

## 11. Relatório de resultados (modelo)

1. Resumo executivo (1 página): hipóteses H1–H4 provadas ou não, com o número de cada uma.
2. Linha e período, o que foi instalado, disponibilidade da coleta por semana.
3. Base × depois (tabela de §8), Pareto da base e das semanas 7–8, gap de medição.
4. Ações realizadas e o efeito de cada uma (quando atribuível).
5. Manutenção: falhas, MTTR, chamados atendidos, componentes com vida medida.
6. Uso: acesso por papel, % de paradas justificadas por turno.
7. Lições aprendidas, pendências e proposta de expansão (outras linhas, preditiva, integrações).

Critério de encerramento com sucesso: H1, H2 e H3 provadas e pelo menos uma meta de H4 atingida. Caso contrário,
o relatório diz o porquê e o que mudaria — sem maquiar números.

```json pilot-before-after
{"ideal_cycle_time_s": 30,
 "baseline": {"pbt_min": 6750, "adot_min": 1350, "ideal_produced_min": 4590, "ideal_good_min": 4452.3,
              "top_loss": {"code": "EQP-03", "min": 420}},
 "after": {"pbt_min": 6750, "adot_min": 945, "ideal_produced_min": 4992.3, "ideal_good_min": 4867.4925,
           "top_loss": {"code": "EQP-03", "min": 210}},
 "manual_oee_before": 0.78,
 "expected": {"baseline": {"availability": 0.8, "performance": 0.85, "quality": 0.97, "oee": 0.6596},
              "after": {"availability": 0.86, "performance": 0.86, "quality": 0.975, "oee": 0.72111},
              "gain_pp": 6.151, "extra_good_parts_per_week": 830.385, "top_loss_reduction": 0.5,
              "measurement_gap_pp": 12.04, "meets_oee_target": true, "meets_top_loss_target": true}}
```
