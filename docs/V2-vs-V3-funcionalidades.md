# V2 vs V3 — Inventário de funcionalidades

> **Última atualização:** 16/05/2026
> **Branch:** `v3.0-dev`
> **Autor:** Alisson Martins · ADS Anhanguera
> **Status:** documento vivo — atualizar a cada decisão de produto da V3

A V3 não é só "V2 com servidor". Quando o estado deixa de ser local (LocalStorage isolado por máquina) e passa a ser compartilhado num backend autenticado, várias funcionalidades da V2 perdem sentido, mudam de natureza ou viram função administrativa. Este documento é o inventário oficial dessas decisões — a fonte da verdade pra responder "isso vai pra V3?".

## Princípio orientador

**A V2 é a base total da V3. Só muda o que o backend (servidor + JWT + banco compartilhado) obriga.**

A V2 já está em produção sendo usada por operadores reais. Cada gesto, cada modal, cada atalho foi validado em operação. Mudar UX sem necessidade técnica significa retrabalho de treinamento e risco de rejeição operacional. Quando o doc abaixo lista uma funcionalidade como **PORTA**, isso quer dizer "porta literal — copia a UX da V2 e ajusta só o que LocalStorage → backend exige". Mudanças anti-V2 (introduzir interação nova, remover algo que já existe) só entram com **decisão explícita registrada** na seção 6.

---

## 1. Sistema de classificação

Cada funcionalidade da V2 (ou planejada) entra em uma das quatro categorias:

| Categoria | Significado |
|---|---|
| **PORTA** | Continua na V3 com a mesma natureza operacional. Pode ganhar reforço técnico (real-time, regra no banco), mas o usuário usa do mesmo jeito. |
| **MUDA** | Continua, porém com comportamento ou alcance diferentes por causa do servidor, JWT ou multi-usuário. |
| **MATA** | Sai da V3. Ou virou desnecessária com o servidor, ou foi substituída por algo melhor. |
| **NOVA** | Não existe na V2. Só passa a fazer sentido com banco compartilhado, autenticação ou multi-garagem. |

---

## 2. PORTA — funcionalidades que continuam

| Funcionalidade V2 | O que ganha na V3 | Justificativa |
|---|---|---|
| Visualização das filas | Atualização em tempo real (polling 5s; WebSocket no futuro) | Núcleo do sistema. Operador vê o pátio inteiro, agora compartilhado entre coordenadores. |
| Alocação rápida / Block Registration | Persistência server-side a cada movimento. Modo bloco com toggle Ida(→)/Volta(←) preservado integralmente | Função operacional cotidiana — não muda o gesto, só fica auditável. As setas refletem o caminhar físico do alocador (ver decisão 6.5). |
| Mover ônibus já alocado | Modal "Editar veículo" com dropdown de fila + linha + Salvar/Remover, igual V2 (`abrirEdicaoChip`). Salvar dispara `POST /alocacoes`; Remover dispara `DELETE /alocacoes/{id}` | Padrão clique-no-chip → modal já validado em operação. Drag-and-drop fica fora do MVP. |
| Plantão E2/AR2 | Regra `GENERATED ALWAYS AS` no banco impede erro de prefixo | A V2 confia no operador. A V3 trava no banco. Mais sólido, mesma UX. |
| Manutenção (4 subtipos) | Vira histórico permanente em tabela `ficha_manutencao` | Continua o mesmo formulário. Diferença é que o registro nunca se perde. |
| Botões de imprimir (`window.print()`) | Continua o mesmo `window.print()` por enquanto | Atende o caso de uso atual. Migrar pra geração de PDF só quando aparecer demanda real (relatório mensal, IPP agregado). |
| Relatórios Excel via SheetJS | Continua, mas pode pedir agregados (mês inteiro, comparativo) | A engine não muda. O que muda é o conjunto de dados disponível. (Ver também seção MUDA.) |

---

## 3. MUDA — funcionalidades que mudam de natureza

| Funcionalidade V2 | Como fica na V3 | Justificativa |
|---|---|---|
| Senha local "0000" pra ações sensíveis | Substituída por perfis JWT (5 perfis: visualizador, operador, manutenção, coordenador, admin) | Senha compartilhada não autentica ninguém. JWT diz quem fez o quê. |
| Limpeza de dados (3 níveis com senha "0000") | Vira ação **ADMIN** com JWT, registrada em auditoria | Em LocalStorage cada máquina tinha seu estado. No servidor, limpar é decisão coletiva irreversível. |
| Importar alocação (menu três pontos) | Vira **botão de emergência** no menu admin (Cenário B/C: servidor caído) com nome novo | Em condição normal, importar atropela o estado real. Só faz sentido como fallback de continuidade operacional. |
| Alertas configuráveis localmente | Registros na tabela `alerta` com autor, motivo e timestamp | Alerta é informação coletiva. Precisa ter responsável e ficar visível pra todos. |
| Relatórios Excel (escopo) | SheetJS continua, mas agora pode agregar mês inteiro, comparar garagens, gerar IPP histórico | A V2 só sabia da sessão atual. A V3 tem todo o histórico no banco. |
| Filtros e buscas | Server-side com índices PostgreSQL | Em frota pequena, busca client-side basta. Em multi-garagem com histórico, não. |

---

## 4. MATA — funcionalidades que saem

| Funcionalidade V2 | Por quê sai | Substituto na V3 |
|---|---|---|
| Exportar/importar JSON de fila individual | Estado já compartilhado em tempo real entre todas as máquinas | Não há substituto — a operação ficou desnecessária. |
| Backup manual de dados | Vira rotina automática server-side (dump diário do PostgreSQL) | Backup automatizado fora do app. |
| Refresh manual da tela | Polling 5s já cobre | Botão sumiu da UI. |
| Senha "0000" como mecanismo de autorização | Não autentica, é "porteiro de papel" | JWT + perfil de usuário. |

---

## 5. NOVA — só existem na V3

| Funcionalidade | Habilitada por | Quando entra |
|---|---|---|
| Auditoria completa (quem alocou, quando, quem moveu) | Tabelas operacionais com colunas `criado_por`, `criado_em`, `alterado_por`, `alterado_em` | Já no MVP — usado em toda escrita do backend. |
| 5 perfis de usuário (visualizador, operador, manutenção, coordenador, admin) | JWT + tabela `usuario` + tabela `permissao` | MVP. |
| Dashboard pra gerência fora da garagem | Read-only de qualquer perfil ≥ coordenador | Pós-Fase 5 (visualização e alocação prontas). |
| Multi-garagem (Garagem 1, 2, 4 sem código novo) | Coluna `garagem_id` em todas as tabelas operacionais | Decisão de schema já preparada. Ativação quando outra garagem pedir. |
| Histórico de longo prazo (IPP janeiro vs julho) | Soft delete + tabela `escala` + `alocacao_patio.ativa` | Disponível desde o MVP, ferramenta de leitura vem depois. |
| Integração com API Nimer (sistema interno Sambaíba) | API FastAPI + token interno | Fase posterior, depende de acesso ao Nimer. |
| Notificações push (alertas críticos) | Service Worker + endpoint backend | Pós-PWA (ver seção 6). |
| API pública pra outros sistemas | FastAPI já expõe, basta documentar e proteger | Quando aparecer integração concreta. |

---

## 6. Decisões específicas já tomadas

Lista das decisões fechadas em sessões de planejamento. Cada item tem justificativa pra evitar revisão circular.

### 6.1 Botões de imprimir — **PORTA**
Mantidos como `window.print()`. A pergunta "deveria virar PDF?" só volta à mesa quando aparecer caso de uso concreto (ex: relatório mensal de IPP, exportação pra gerência). Hoje, imprimir do navegador atende.

### 6.2 Importar alocação — **MUDA** (vira emergência)
Não é mais ação cotidiana. Migra pro menu admin como **botão de emergência**, ativado nos cenários:
- **Cenário B:** servidor inacessível, operador precisa rodar contingência local e depois sincronizar.
- **Cenário C:** banco corrompido / restauração de backup com perda parcial de estado.

Recebe nome novo na UI (sugestão: "Restaurar alocação de emergência") pra deixar claro que não é fluxo normal.

### 6.3 Modo offline real (PWA) — **NOVA**, mas adiada pra Fase 7
Não vale over-engineering agora. A V3 nasce online com polling. PWA + Service Worker entra quando a operação real exigir (ex: queda de internet recorrente na garagem).

### 6.4 Inventário V2 vs V3 — este documento
Decisão meta: registrar em doc próprio, fora do `STATUS-V3.md`, porque vira asset de produto e ajuda em decisão de escopo das próximas fases. Documento vivo, atualizado por sessão.

### 6.5 Modo bloco com setas Ida/Volta — **PORTA literal**
Item obrigatório do MVP da Fase 5.3. Funciona assim na V2 (`v2/js/app.js` linhas 1031-1036):

- **Ida (→):** caminhando frente → fundo. Cada prefixo digitado vai pra próxima posição livre (`push`). Equivalente a `lista.length + 1`.
- **Volta (←):** caminhando fundo → frente. Cada prefixo digitado vai pra **posição 1** (`unshift`) e o sistema **recalcula todas as posições** sequencialmente.

Reflete a rotina física do alocador (marca duas filas indo, volta marcando mais duas). Sem essa lógica, a V3 não substitui a V2 em operação real.

UI obrigatória: input fila + input prefixo + input linha, toggle Ida/Volta com indicador visual ativo, barra de status ("Fila X → · N carros · próxima pos. K"), botão Marcar, botão Desfazer último, histórico inline da fila atual em ordem reversa (último marcado no topo) com edição inline.

### 6.6 Persistência otimista a cada operação — **PORTA equivalente**
A V2 chama `save()` no LocalStorage a cada confirmação (linha 1062). O equivalente fiel na V3 é POST no backend a cada operação. Sem botão "Salvar alterações", sem confirmação intermediária. Conflito multi-usuário fica por conta do trigger no banco (last-write-wins desativa alocação anterior automaticamente).

### 6.7 E2/AR2 livre na alocação de pátio — **MUDA (relaxa)**
A V2 permite alocar qualquer ônibus em qualquer fila do pátio. A V3 mantém isso. A regra de cruzamento E2/AR2 vale só na **escala** (linha × ônibus), travada por trigger no banco (`fn_valida_setor_escala`). No pátio, o setor aparece só como badge descritivo no chip.

### 6.8 Sem autocomplete na fila do modo bloco — **EXCEÇÃO à fidelidade da V2**
A V2 tem autocomplete no input de fila do modo bloco (`v2/js/app.js` linha 1126 em diante, função `acFiltrar`). **Na V3, esse autocomplete sai**. Justificativa do Alisson em 2026-05-16: "atrapalha um pouco na hora de digitar". Operador digita o número da fila direto e Enter — sem dropdown filtrando.

Validação de fila inexistente continua: se digitar fila que não existe, retorna erro claro.

### 6.9 Limpeza de campos após cada Marcar — **EXCEÇÃO à fidelidade da V2**
A V2 limpa só o campo prefixo (`bloco-carro`) após confirmar e dá foco nele de novo (linhas 1059-1060). **Na V3, o comportamento expande:** após cada Marcar bem-sucedido, **prefixo e linha ficam vazios**, o input de fila **se mantém preenchido** (operador segue marcando vários carros na mesma fila), e o foco volta pro input de prefixo.

Justificativa: regra explícita do Alisson em 2026-05-16 — "após cada alocação os campos de alocação rápida ficam vazios para digitar". Mantém o input de fila preenchido porque zerar a fila a cada Marcar quebra o fluxo de marcar a fila inteira em sequência (interpretação a confirmar).

### 6.10 Drag-and-drop pra mover chip — **NÃO entra no MVP**
A V2 não tem. Mover ônibus na V3 segue o padrão V2: clique no chip → modal → escolhe fila destino. Drag-and-drop fica como melhoria opcional pós-MVP, sem prioridade.

---

## 7. Decisões pendentes

Lista das funcionalidades cuja decisão ainda não foi tomada. Cada uma vira um item desta seção até virar uma linha em PORTA / MUDA / MATA.

| Funcionalidade V2 | Pergunta em aberto | Quando decidir |
|---|---|---|
| Importação de escala (xlsx/csv) | Continua manual por operador ou vira upload centralizado pelo coordenador? | Antes de iniciar a Fase 6 (escalas). |
| Limpeza de fila individual | Operador pode limpar uma fila ou só admin? E qual o critério (turno encerrou? manual?) | Junto com Fase 5.3 (alocação rápida). |
| Concorrência multi-usuário na mesma fila | ~~Last-write-wins? Lock otimista com timestamp? Bloqueio visual quando outro operador está editando?~~ **Decidido em 16/05/2026:** last-write-wins pelo trigger do banco. Polling 5s mostra estado real. Sem código especial. | — |
| Cor / categorização visual de chips | Mantém esquema da V2 ou redesenha com base nos perfis? | Fase 5.4 (refino de UI). |

---

## 8. Histórico de revisões

| Data | Revisão | Autor |
|---|---|---|
| 16/05/2026 | Criação do documento com decisões consolidadas das sessões de 05 a 15/05 | Alisson Martins |
| 16/05/2026 | Princípio orientador "V2 como base fiel" e decisões 6.5–6.10 da Fase 5.3 (alocação rápida) | Alisson Martins |

---

## 9. Como usar este documento

- **Antes de começar uma fase:** abrir aqui, conferir se as funcionalidades que a fase toca já têm decisão. Se tiver pendente, decidir antes de codar.
- **Em revisão de PR / commit:** se a mudança altera comportamento de uma funcionalidade listada, atualizar a justificativa.
- **Em apresentação do projeto (portfólio, faculdade, entrevista):** este doc é o argumento de que a V3 foi pensada, não só "reescrita".
