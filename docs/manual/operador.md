---
title: "Operador (tablet da linha)"
description: "Como entrar no tablet, ver a linha agora, justificar e declarar paradas, trocar o produto, chamar a manutenção e trabalhar sem rede."
ordem: 3
papel: operator
---

# Operador — tablet da linha

O tablet fica fixo na linha (ou na máquina) e mostra só o que você precisa durante o turno. Ele foi feito para uso
com luva, com barulho e luz forte: botões grandes, poucas telas, **no máximo 3 toques** para qualquer ação, e
"desfazer" em vez de caixas de confirmação.

O que você **não** vê no tablet: outras linhas, horas e custos da manutenção, configurações. Isso é de propósito.

## Como entrar

1. Na tela inicial do tablet, digite (ou leia com o leitor de crachá) o seu **usuário ou crachá** e a sua senha/PIN.
2. Se aparecer "Este tablet fica em qual linha?", o tablet ainda não foi vinculado: chame o supervisor (ele faz isso
   uma vez, veja [Supervisor — como cadastrar o tablet da linha](supervisor.md#tablet)).
3. Na troca de turno ou de operador, toque em **Trocar operador**: o próximo entra em 2 toques, sem perder nada que
   estava pendente. A sessão também termina sozinha no fim do turno ou depois de 15 min sem uso.

Errou o PIN 5 vezes? O acesso fica bloqueado por 5 minutos. Esqueceu o PIN: o supervisor ou o administrador gera
outro.

## Como ver a linha agora

A primeira tela mostra cada máquina da linha com **cor, ícone e texto** (nunca só a cor):

| Estado | Como aparece | O que significa |
|---|---|---|
| Rodando | verde, ▶ | produzindo |
| Parada | âmbar, ⏸, "parada há 6 min — sem motivo" | parou e ninguém disse por quê |
| Falha | vermelho, chave inglesa, "falha há 8 min" | o CLP indicou falha; a manutenção já pode ter sido chamada |
| Manutenção | azul, capacete, "manutenção — OS-123" | há um técnico trabalhando nela |
| Pausa planejada | cinza claro, relógio, "pausa (refeição) até 12:30" | café, refeição, reunião |
| Fora do turno | cinza escuro, lua | não há turno agora |
| Sem dados | cinza tracejado, nuvem cortada, "sem dados há 12 min" | o tablet não está recebendo informação dessa máquina |

Também aparecem a **parada aberta com cronômetro**, a produção do turno (e a meta, se cadastrada) e o **OEE do
turno** (o que é OEE: [Indicadores — OEE](indicadores.md#oee)).

Se a faixa **"sem conexão desde HH:MM"** aparecer, o tablet perdeu a rede: os estados ficam esmaecidos porque não são
mais ao vivo. Você pode continuar justificando paradas (veja [Sem rede](#sem-rede)).

<a id="justificar"></a>
## Como justificar uma parada

Toda parada de **2 minutos ou mais** precisa de um motivo. Sem motivo, ela fica como "não justificada" (`UNK-01`) e
aparece no relatório do supervisor — e o Pareto de perdas não consegue mostrar a causa real.

1. Toque em **Paradas sem motivo**. A lista mostra as paradas do turno que ainda não têm motivo.
2. Toque na parada. Aparecem os **motivos mais usados** daquela máquina como botões grandes.
3. Toque no motivo. Pronto: a parada some da lista. Errou? Toque em **Desfazer** (5 s).

Se o motivo não estiver entre os mais usados, toque em **Outros…** e escolha na lista completa (agrupada:
falhas `EQP`, setup `SET`, material `MAT`, operador `OPR`, qualidade `QLT`, utilidades `UTL`…).

**Falha já sugerida**: quando o CLP indicou falha, a parada já vem com um motivo sugerido do tipo `EQP-*` (por
exemplo, "Falha pneumática"). Se estiver certo, **confirme com um toque**; se não, escolha o motivo certo. Ao trocar
uma falha por outro motivo (por exemplo, era falta de ar comprimido, `UTL-01`), o registro de falha da máquina é
desfeito — por isso escolha com cuidado.

Micro-paradas (menos de 2 min) **não** pedem motivo: elas entram na conta do desempenho automaticamente.

## Como declarar uma parada que o sistema não viu

Use quando a máquina parou mas não há sinal automático (máquina sem sensor de "rodando") ou quando o sistema ficou
sem dados (queda de rede, de energia).

1. Toque em **Declarar parada**.
2. **1. Máquina** → **2. Quando começou** → **3. Motivo**.
3. Se a máquina já voltou, toque em **Já voltou (fim agora)**; se ainda está parada, **Ainda parada** (você encerra
   depois).

Regras que o tablet aplica:

- **Preventiva** (`PLN-03`) só vale se estava no **calendário** antes do turno. Se não estava, o tablet recusa e sugere
  **"Manutenção não programada" (`MNT-02`)** — é a regra do OEE: preventiva fora do calendário é perda.
- Reunião, treinamento, falta de programação (`PLN-*`) declarados durante o turno saem do tempo planejado e ficam
  registrados com o seu nome no relatório do supervisor ("paradas planejadas declaradas no turno"). Use só quando for
  verdade.

## Como trocar o produto (ou a ordem)

O desempenho e o OEE dependem do **tempo de ciclo ideal do produto**. Se o sistema não sabe o que está sendo
produzido, ele usa o produto padrão da máquina e avisa "produto não informado".

1. Toque em **Trocar produto / ordem** **no início do setup** — não no fim.
2. Escolha o produto (e a ordem, se houver).

Por que no início: o setup pertence à ordem que ele prepara. Trocar no fim do setup deixa o tempo de setup na ordem
anterior e reparte as peças da hora entre os produtos errados.

Se aparecer "Nenhum produto vinculado a esta máquina", peça ao gestor para cadastrar o produto e o tempo de ciclo.

<a id="chamar-manutencao"></a>
## Como chamar a manutenção

- **Com a máquina parada**: na parada aberta, toque em **Chamar manutenção**. O chamado vai para os técnicos de
  plantão da sua área, ligado à parada. O tablet mostra **"técnico a caminho"** quando alguém assume.
- **Sem parada** (vazamento, barulho estranho, peça solta): **Chamar manutenção (sem parada aberta)** abre uma
  solicitação para o planejador avaliar.

Se aparecer o aviso "ninguém de plantão nesta linha", o planejamento foi avisado — mas, se for urgente, **avise
também por rádio ou telefone**. Um alerta vermelho "avise a manutenção por rádio/telefone" quer dizer que ninguém da
manutenção recebeu o chamado.

## Como lançar refugo manual

Só aparece para máquinas **sem contador automático de refugo**. Onde existe o contador do CLP, ele é a verdade e o
lançamento manual não é oferecido (para não contar duas vezes).

<a id="sem-rede"></a>
## Sem rede

O Wi-Fi da fábrica cai? Continue trabalhando:

- Justificativas, paradas declaradas e trocas de produto ficam guardadas no tablet como **"pendente de envio"** (veja
  **Pendentes de envio**).
- Quando a rede volta, o tablet envia tudo **na ordem**, uma vez só.
- Se outra pessoa justificou a mesma parada antes, vale o que chegou primeiro no servidor e o tablet mostra o que foi
  gravado ("Esta parada já foi justificada").

## Erros comuns

| Erro | Consequência | Como evitar |
|---|---|---|
| Justificar tudo como "Outros" ou deixar sem motivo | o Pareto mostra "não justificada" como maior perda e ninguém sabe o que atacar | escolha o motivo mais próximo; se faltar um motivo, peça ao supervisor para criar |
| Marcar uma quebra como "pausa" ou "reunião" (`PLN-*`) | a perda some do OEE e a manutenção não fica sabendo | `PLN-*` só para o que estava planejado |
| Trocar o produto depois do setup | setup e peças vão para o produto errado | trocar no início do setup |
| Declarar preventiva sem estar no calendário | o tablet recusa; quem insistir com outro código distorce o OEE | use `MNT-02` ou peça para o planejador agendar no calendário |
| Lançar refugo manual onde há contador | (não é oferecido) | — |
| Desligar o tablet com itens "pendente de envio" | eles só saem quando o tablet volta à rede | deixe o tablet ligado na tomada |

## Perguntas frequentes

**A parada tem 1 min e 50 s. Preciso justificar?**
Não. Abaixo de 2 min é micro-parada: não pede motivo e entra no desempenho. A partir de 2 min exatos, pede.

**A máquina parou no café e voltou depois. Como justifico?**
Só a parte depois do fim da pausa aparece como parada a justificar; o tempo dentro do café já é pausa planejada.

**Por que o OEE do turno no tablet é diferente do que o supervisor viu ontem no relatório?**
O tablet mostra o turno **atual**, até agora. O relatório mostra o turno fechado. Paradas justificadas depois também
podem mudar o relatório do turno (ele é gerado 15 min depois do fim do turno, para dar tempo de justificar).

**Justifiquei errado. Dá para corrigir?**
Durante 5 s, **Desfazer**. Depois disso, peça ao supervisor: ele corrige na tela Paradas.

**O tablet diz "sem dados há 12 min" para uma máquina que está rodando.**
O sinal daquela máquina não está chegando (cabo, CLP, gateway). Avise o supervisor; o tempo sem dados não conta
como produção nem como parada.
