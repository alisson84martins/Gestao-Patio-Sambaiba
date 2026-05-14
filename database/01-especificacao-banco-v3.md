# Especificação do Banco de Dados — Sistema de Gestão de Pátio Sambaíba v3.0

> **Branch:** `v3.0-dev`
> **Data de fechamento:** 02/05/2026
> **Autor:** Alisson Martins
> **SGBD:** PostgreSQL 15+
> **Nome do banco:** `gestao_patio_sambaiba`
> **Estado:** Especificação fechada — pronto para o DER lógico e DDL

Este documento consolida todas as decisões de modelagem do banco de dados da v3.0, alinhadas por Alisson em sessão de 02/05/2026.

---

## 1. Visão geral

A v3.0 introduz um backend real (FastAPI + PostgreSQL) que substitui o `localStorage` da v2. O sistema continua **offline-first** com sincronização (modelo híbrido), pois o pátio da Garagem 3 só tem cobertura de dados móveis, sujeita a falhas.

O banco foi modelado para:
- Cobrir 100% do que a v2 faz hoje (alocação, escala, alertas, manutenção)
- Suportar histórico completo (sem necessidade de "limpar" entre dias)
- Ter pontos de extensão para futura integração com sistemas da empresa
- Ser cirúrgico: 12 tabelas, sem gordura

---

## 2. Decisões finais de escopo

| # | Tema | Decisão |
|---|---|---|
| 1 | **Multi-garagem** | Apenas Garagem 3. Sem tabela `garagem`. |
| 2 | **Histórico de pátio** | Histórico completo. Coluna `ativa` marca a posição atual. |
| 3 | **Filas + posições especiais + manutenção** | Tabela única `fila` com coluna `tipo` (NUMERICA / ESPECIAL / MANUTENCAO). |
| 4 | **Escala** | Importação Excel + edição manual. Auditoria por origem. |
| 5 | **Integração Nimer** | Fora do escopo. Mantém `codigo_externo` em `onibus` e `motorista` para futuro. |
| 6 | **Chave primária** | UUID em todas as tabelas (necessário pelo modelo offline-first). |
| 7 | **Auditoria** | Completa em tabelas operacionais; só `criado_em` em catálogos. |
| 8 | **Soft delete** | Em `escala`, `alerta`, `ficha_manutencao`. Demais usam flag específica (`ativo`, `ativa`, `status`). |
| 9 | **Motorista** | Tabela separada de `usuario`. FK `usuario.motorista_id` opcional. |
| 10 | **Linhas** | Catálogo fechado. Cadastro pelo admin; alteração manual. |
| 11 | **Sincronização** | Híbrido — operação local em IndexedDB, sincronização periódica via API. |

---

## 3. Regras de negócio consolidadas

### 3.1 Frota e setores

- Frota identificada por **número de 4 dígitos**, único.
- **Setor é derivado do prefixo da frota:**
  - Frota começando com `1` → setor **E2**
  - Frota começando com `2` → setor **AR2**
- Status do ônibus: `ATIVO`, `MANUTENCAO`, `INATIVO`, `RESERVA`.

### 3.2 Pátio

- **33 filas numéricas** (1 a 33)
- **6 posições especiais** atuais: Coqueiro, Laje, Lavador, Bomba, Elétricos, Fundão
- **Setor de manutenção** como tipo de fila (extensível para sub-setores: Mecânica, Lataria, etc.)
- Posições novas (ex: Coqueirinho) são adicionadas via `INSERT` na tabela `fila`
- Constraint: **um veículo só pode ter uma alocação `ativa = true`** (UNIQUE parcial)
- Posição dentro da fila é sequencial (`posicao` int) — FIFO

### 3.3 Escala

- Cada registro pertence a uma `data` específica — escalas de dias diferentes coexistem
- Tipos detectados na importação: `MANOBRA`, `PLANTAO_E2`, `PLANTAO_AR2`
- Origem: `IMPORTACAO_EXCEL` ou `MANUAL`
- Reimportar a mesma data substitui (com confirmação) — registros antigos viram soft delete
- Validação cruzada: linha E2 só em frota E2; linha AR2 só em frota AR2

### 3.4 Alertas

- Tipos: `PRESO` (veículo retido na rua) e `AMOSTRAL` (SPTRANS)
- **Prioridade na impressão:** PRESO > AMOSTRAL > normal
- Não pode haver duplicidade de `PRESO` ativo para o mesmo ônibus (constraint)
- Cadastro automático de ônibus se a frota digitada não existir

### 3.5 Manutenção — separação de conceitos

| Conceito | Tabela | Pergunta que responde |
|---|---|---|
| **ONDE** o ônibus está | `alocacao_patio` (apontando pra `fila` tipo MANUTENCAO) | Posição física no pátio |
| **O QUE** está sendo feito | `ficha_manutencao` | Defeito, mecânico, status do serviço |
| **PARA ONDE** ele vai | `escala` | Linha e horário previstos |

Os três coexistem. Um ônibus pode estar fisicamente em manutenção, com ficha aberta, e ainda estar escalado para uma linha — esse cruzamento alimenta a tela de remanejamento do coordenador.

### 3.6 Usuários, perfis e permissões

- Perfis: `COORDENADOR`, `OPERADOR_PATIO`, `MOTORISTA`, `MECANICO`, `ADMIN`
- Login via `RE` (registro de funcionário) + senha (hash bcrypt/argon2)
- Permissões granulares: `usuario × recurso × pode_ler / pode_escrever`
- Motorista loga apenas para consulta (digita frota → vê posição, horário, linha)

---

## 4. Lista definitiva de tabelas

### 🟥 Núcleo operacional (8 tabelas)

Auditoria completa: `criado_em`, `criado_por`, `atualizado_em`, `atualizado_por`.

| # | Tabela | Função | Soft delete |
|---|---|---|---|
| 1 | `usuario` | Quem loga no sistema. FK opcional para `motorista` quando aplicável. | Não — usa `ativo` |
| 2 | `motorista` | Cadastro operacional. Pode existir sem virar usuário. | Não — usa `status` |
| 3 | `onibus` | Frota da Garagem 3. `numero_frota` UNIQUE, `codigo_externo` opcional. | Não — usa `status` |
| 4 | `fila` | 33 numéricas + posições especiais + manutenção (tipo enum). | Não — usa `ativa` |
| 5 | `alocacao_patio` | Histórico de movimentações. Constraint UNIQUE em `(onibus_id) WHERE ativa = true`. | Não — usa `ativa` |
| 6 | `escala` | Escala diária. Importação + manual. Filtro por `data`. | **Sim** |
| 7 | `alerta` | PRESO e AMOSTRAL. UNIQUE em `(onibus_id, tipo) WHERE resolvido = false`. | **Sim** |
| 8 | `ficha_manutencao` | Defeitos abertos e concluídos. Conceito separado de localização física. | **Sim** |

### 🟨 Catálogos fechados (3 tabelas)

Pouca movimentação. Cadastro pelo admin. Apenas `criado_em`.

| # | Tabela | Função |
|---|---|---|
| 9 | `linha` | Catálogo fechado E2 e AR2 com setor amarrado (valida cruzamento) |
| 10 | `tipo_defeito` | Categorias (mecânica, elétrica, freios, ar, lataria…) |
| 11 | `permissao` | Matriz: usuário × recurso × pode_ler / pode_escrever |

### 🟦 Sistema (1 tabela)

| # | Tabela | Função |
|---|---|---|
| 12 | `importacao_escala` | Histórico de uploads (quem, quando, total registros, sucesso/erro, arquivo). Permite reverter import errado. |

**Total: 12 tabelas.**

---

## 5. Filtros e queries-chave

### 5.1 Renderizar o pátio agora

```sql
SELECT 
    f.tipo, f.numero, f.nome,
    o.numero_frota, a.posicao,
    e.linha, e.horario_saida,
    al.tipo AS tipo_alerta,
    fm.status AS status_ficha
FROM fila f
LEFT JOIN alocacao_patio a ON a.fila_id = f.id AND a.ativa = true
LEFT JOIN onibus o ON o.id = a.onibus_id
LEFT JOIN escala e ON e.onibus_id = o.id AND e.data = CURRENT_DATE
LEFT JOIN alerta al ON al.onibus_id = o.id AND al.resolvido = false
LEFT JOIN ficha_manutencao fm ON fm.onibus_id = o.id 
    AND fm.status IN ('ABERTA','EM_ANDAMENTO')
WHERE f.ativa = true
ORDER BY f.tipo, f.numero, a.posicao;
```

Cobre filas, especiais e manutenção numa só query. Alimenta tela e impressão.

### 5.2 Onde está o ônibus 1234?

```sql
SELECT f.nome, a.posicao, a.alocado_em
FROM onibus o
JOIN alocacao_patio a ON a.onibus_id = o.id AND a.ativa = true
JOIN fila f ON f.id = a.fila_id
WHERE o.numero_frota = 1234;
```

### 5.3 Tela de remanejamento (ônibus em manutenção com escala hoje)

```sql
SELECT 
    o.numero_frota, e.linha, e.horario_saida,
    fm.tipo_defeito, fm.status, fm.aberta_em
FROM onibus o
JOIN alocacao_patio a ON a.onibus_id = o.id AND a.ativa = true
JOIN fila f ON f.id = a.fila_id AND f.tipo = 'MANUTENCAO'
JOIN escala e ON e.onibus_id = o.id AND e.data = CURRENT_DATE
LEFT JOIN ficha_manutencao fm ON fm.onibus_id = o.id 
    AND fm.status IN ('ABERTA','EM_ANDAMENTO')
ORDER BY e.horario_saida;
```

### 5.4 Escala do dia

```sql
SELECT * FROM escala 
WHERE data = CURRENT_DATE AND deletado_em IS NULL
ORDER BY horario_saida;
```

---

## 6. Índices críticos

Aplicar desde o DDL inicial:

| Tabela | Índice | Justificativa |
|---|---|---|
| `onibus` | `numero_frota` UNIQUE | Operação mais comum do sistema |
| `onibus` | `codigo_externo` | Integração futura (Nimer) |
| `motorista` | `re` UNIQUE | Login + busca |
| `alocacao_patio` | `(onibus_id) WHERE ativa = true` | "Onde está o ônibus X?" instantâneo |
| `alocacao_patio` | `(fila_id, posicao) WHERE ativa = true` | Renderizar fila |
| `escala` | `(data, onibus_id) WHERE deletado_em IS NULL` | Escala do dia |
| `alerta` | `(onibus_id, tipo) WHERE resolvido = false` | Alertas ativos + constraint anti-duplicidade |
| `ficha_manutencao` | `(onibus_id, status) WHERE deletado_em IS NULL` | Manutenções abertas |
| `linha` | `codigo` UNIQUE | Validação na importação |

---

## 7. Tipos enumerados (ENUMs do PostgreSQL)

Pra evitar tabelas extras de domínio com pouca movimentação:

| ENUM | Valores |
|---|---|
| `setor_enum` | `E2`, `AR2` |
| `status_onibus_enum` | `ATIVO`, `MANUTENCAO`, `INATIVO`, `RESERVA` |
| `status_motorista_enum` | `ATIVO`, `AFASTADO`, `FERIAS`, `DESLIGADO` |
| `perfil_usuario_enum` | `ADMIN`, `COORDENADOR`, `OPERADOR_PATIO`, `MOTORISTA`, `MECANICO` |
| `tipo_fila_enum` | `NUMERICA`, `ESPECIAL`, `MANUTENCAO` |
| `tipo_alerta_enum` | `PRESO`, `AMOSTRAL` |
| `status_ficha_enum` | `ABERTA`, `EM_ANDAMENTO`, `CONCLUIDA`, `CANCELADA` |
| `tipo_escala_enum` | `MANOBRA`, `PLANTAO_E2`, `PLANTAO_AR2` |
| `origem_escala_enum` | `IMPORTACAO_EXCEL`, `MANUAL` |
| `status_importacao_enum` | `SUCESSO`, `ERRO`, `PARCIAL` |

---

## 8. Padrões de auditoria e sincronização

### 8.1 Auditoria padrão (tabelas operacionais)

```sql
criado_em        TIMESTAMP NOT NULL DEFAULT NOW(),
criado_por       UUID REFERENCES usuario(id),
atualizado_em    TIMESTAMP,
atualizado_por   UUID REFERENCES usuario(id),
deletado_em      TIMESTAMP NULL  -- só onde aplicável
```

### 8.2 Sincronização offline-first (todas as tabelas operacionais)

```sql
sincronizado_em  TIMESTAMP NULL,  -- quando o servidor recebeu
versao           INT NOT NULL DEFAULT 1  -- otimismo de concorrência
```

Estratégia: cliente cria registro local com UUID + `versao=1`. Ao sincronizar, servidor preenche `sincronizado_em`. Conflitos: last-write-wins por `versao`.

---

## 9. Crescimento e escalabilidade

### 9.1 Estimativa de volume

| Tabela | Por dia | 1 ano | 5 anos |
|---|---|---|---|
| `alocacao_patio` | ~500 | 180 mil | 900 mil |
| `escala` | ~500 | 180 mil | 900 mil |
| `alerta` | ~10 | 3.600 | 18 mil |
| `ficha_manutencao` | ~5 | 1.800 | 9 mil |

PostgreSQL suporta esses volumes sem performance impact com os índices definidos.

### 9.2 Pontos de extensão preparados

- `onibus.codigo_externo` e `motorista.codigo_externo` — integração Nimer/ERP futura
- UUID nas PKs — sem colisão se um dia houver fusão com outros bancos
- `fila.tipo` permite novos tipos de pátio sem schema migration
- `tipo_defeito` em tabela separada permite expansão de catálogo
- `permissao` granular permite criar perfis novos sem código

---

## 10. Roadmap de implementação

```
Banco completo (12 tabelas) → criado de uma vez no DDL inicial.
Custo: 1 sessão SQL.

Frontend/Backend implementados em ondas:

  v3.0 — MVP                 Alocação + busca rápida + escala
  v3.1                        Alertas (PRESO / AMOSTRAL)
  v3.2                        Manutenção (ficha + tipos de defeito)
  v3.3                        Relatórios e analytics (aproveita histórico)
```

Tabelas não usadas no MVP ficam vazias até a fase em que forem populadas. Não impactam performance.

---

## 11. Estrutura de pastas proposta

```
database/
├── 01-especificacao-banco-v3.md       ← este arquivo (FECHADO)
├── 02-der-logico-v3.html              ← próxima sessão
├── 03-dicionario-de-dados.xlsx        ← próxima sessão
├── migrations/
│   ├── 001-create-database.sql
│   ├── 002-enums.sql
│   ├── 003-tables-core.sql
│   ├── 004-tables-apoio.sql
│   ├── 005-constraints-indexes.sql
│   └── 006-seeds.sql
├── seeds/
│   ├── filas-numericas.sql            (33 filas)
│   ├── posicoes-especiais.sql         (6 posições)
│   ├── manutencao.sql                 (1+ posições de manutenção)
│   ├── tipos-defeito.sql
│   ├── linhas-e2.sql
│   ├── linhas-ar2.sql
│   └── perfis-permissoes.sql
└── README.md
```

---

## 12. Próximos passos

1. ✅ **Esta sessão (02/05/2026):** especificação fechada
2. ⏭️ **Próxima sessão:** desenhar o DER lógico revisado com as 12 tabelas, ENUMs e relacionamentos
3. ⏭️ **Sessão seguinte:** gerar scripts DDL completos (PostgreSQL 15+)
4. ⏭️ **Sessão seguinte:** dicionário de dados completo em planilha
5. ⏭️ **Sessão seguinte:** integração com FastAPI (modelos SQLAlchemy)

---

*Especificação fechada em 02/05/2026 — Sambaíba Transportes Urbanos · Garagem 3*
*Versão: v3.0 — Alisson Martins · ADS Anhanguera*
