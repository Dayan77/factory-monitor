---
title: "Técnico de manutenção (celular)"
description: "O app do técnico: minhas OS de hoje, executar uma preventiva, trocar componente, atender um chamado emergencial, registrar a falha (ISO 14224), concluir uma OS preditiva e apontar horas."
ordem: 6
papel: technician
---

# Técnico de manutenção — app no celular

O app do técnico abre em `/m` no celular (instale com "Adicionar à tela inicial"). Foi pensado para uma mão e luva:
botões grandes, poucos campos obrigatórios e leitura do **QR code** da etiqueta da posição ou do componente.

## Como começar o turno — Minhas OS de hoje

A lista mostra as OS atribuídas a você para hoje e as vencidas, nesta ordem: **emergencial → vencida → hoje**. Cada
cartão diz a máquina, a posição, o tipo, as horas estimadas, se a OS **para a máquina** e se a produção já
**liberou a janela**.

Você recebe notificação (push) de: OS nova atribuída, OS reprogramada e **chamado emergencial** na sua área.

## Como executar uma preventiva

1. **Iniciar**: leia o QR da posição — o app confere se você está na máquina certa. Se a OS para a máquina, o app
   mostra se a produção liberou.
2. **Checklist** item a item: ok / não ok / foto / medida.
   - Item "não ok" → **abrir corretiva programada** (vai para o planejador aprovar) ou **registrar falha encontrada**
     (falha sem parada da produção: conta no MTBF, **não** no MTTR).
3. **Pausar** quando faltar peça ou liberação: escolha o motivo e informe as **horas restantes**. O tempo em espera
   não conta como hora trabalhada, e as horas restantes alimentam o backlog do planejador.
4. **Trocar componente**, se houver (veja abaixo).
5. **Concluir**: confira as horas trabalhadas (vêm preenchidas pelo tempo em execução, você pode editar), escreva a
   observação e anexe fotos. O plano da OS zera na conclusão e o app mostra o próximo vencimento.

<a id="troca"></a>
## Como trocar um componente

1. Leia o QR (ou o número de série) do componente que **sai** — o app mostra o que está instalado na posição.
2. Escolha o **motivo da remoção**:
   - **falha** — o componente quebrou ou perdeu a função (inclusive defeito confirmado numa preditiva);
   - **troca preventiva** — trocado antes de falhar, por plano ou por decisão;
   - **melhoria** (upgrade) — trocado por outro modelo;
   - **outro**.
3. Leia ou digite fabricante, modelo e série do componente que **entra** (da lista do almoxarifado). O novo começa
   com uso zero.

O motivo da remoção é o dado mais importante da troca: é ele que separa **falha** de **suspensão** na análise de vida
por fabricante/modelo ([Weibull](indicadores.md#weibull)) e no [MTTF](indicadores.md#mttf). Marcar "falha" num
componente trocado preventivamente faz a marca parecer pior do que é; marcar "troca preventiva" num componente que
quebrou faz parecer melhor.

**Componente reparado** (rebobinado, revisado) que volta para a máquina: cadastre-o como **componente novo** na troca
(com o mesmo número de série e uma observação). O sistema não reinstala um componente já removido — a vida dele
recomeça do zero, como "tão bom quanto novo".

<a id="emergencial"></a>
## Como atender um chamado emergencial

1. A notificação chega com som: "Robô de Solda — falha 251 há 3 min". Um toque abre a parada, com o **registro de
   falha rascunho** já criado pela máquina.
2. **Assumir**: cria (ou assume) a OS corretiva emergencial e vai direto para **em execução**. O operador vê "técnico
   a caminho" no tablet.
3. Ao terminar o reparo, informe:
   - a **posição** e o **componente** que falharam (o mais baixo que você souber);
   - o **modo de falha** (lista curta por tipo de componente — cilindro: vazamento externo `ELP`, vazamento interno
     `INL`, desvio de parâmetro/curso lento `PDE`, quebra `BRD`…);
   - a **severidade**: **crítica** (perdeu a função de vez) ou **degradada** (função parcial);
   - se souber, o **mecanismo** e a **causa** — se não, deixe "6.4 desconhecido" / "5.5 desconhecida" para o
     planejador completar.
4. **Não digite a hora em que voltou**: o restabelecimento é medido pela máquina (quando volta a rodar). Em máquina
   sem sinal de "rodando", o supervisor confirma a liberação.
5. Troca de componente e conclusão como na preventiva.

**Falha durante a pausa ou fora do turno**: o chamado chega na hora, mesmo sem produção. Se a falha some sozinha em
menos de 2 min (alarme de porta aberta durante a limpeza, por exemplo), ela não vira chamado nem falha.

## Como concluir uma OS preditiva

OS preditiva nasce de um alerta de condição (curso lento, corrente alta, vibração…). Ao concluir, o app pede o
**achado** (obrigatório):

- **Defeito confirmado** — registre a falha como **incipiente** (começo de degradação, ainda funcionava) ou
  **degradada**, com o modo de falha. O método de detecção é preenchido como monitoramento contínuo. Se trocou o
  componente, motivo da remoção **falha**.
- **Sem defeito** — o alerta fecha como falso alarme. Isso não é "erro" seu: é o dado que o planejador usa para
  ajustar as regras.

Se você trocou o componente ou fez uma corretiva/preditiva, deixe marcado **reiniciar a referência** (padrão): o
sistema vai reaprender o "normal" do componente novo. Para uma simples inspeção, desmarque.

## Como fechar o dia — Apontamentos de hoje

A lista mostra o que você iniciou, concluiu e pausou, com as horas. OS ainda em execução pedem **pausar com horas
restantes** ou **continuar no próximo turno** (passa para o colega com nota).

## Erros comuns

| Erro | Consequência | Como evitar |
|---|---|---|
| Motivo de remoção errado (falha × troca preventiva) | análise de vida por marca/modelo errada; compras erradas | escolher com o componente na mão |
| Severidade "crítica" para tudo | MTBF pior do que é; prioridades erradas | "degradada" quando a máquina continuou produzindo com perda |
| Pausar sem informar horas restantes | backlog subestimado | sempre informar ao pausar |
| Deixar a OS "em execução" no fim do turno | horas infladas e OS presa no seu nome | pausar ou passar para o colega |
| Registrar falha de material/setup como falha do equipamento | MTBF/MTTR contaminados | falta de material, setup e falta de ar não são falha do equipamento |
| Reinstalar componente reparado com o cadastro antigo | (o app não deixa) | cadastrar como componente novo |

## Perguntas frequentes

**Achei um defeito na preventiva, mas a máquina não parou. É falha?**
Se a função foi perdida (total ou parcial), sim: **registrar falha encontrada**. Ela conta no MTBF, mas não no MTTR
(não houve tempo de produção perdido). Se ainda funciona normalmente, abra uma corretiva programada.

**Por que não posso digitar a hora em que a máquina voltou?**
Porque ela é medida pelo sinal da máquina, e é esse tempo que a produção sentiu. Sem sinal, o supervisor confirma.

**A OS diz "aguardando liberação da produção". Posso começar?**
Só depois que a produção liberar a janela. Começar antes transforma a parada em perda não planejada.

**O app mostrou "próximo vencimento" muito longe depois que concluí.**
O vencimento por uso é projetado pelo ritmo dos últimos 14 dias. Se a linha mudar de ritmo, a data muda.
