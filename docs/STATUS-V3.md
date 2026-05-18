# Status da v3.0 — Sistema de Gestão de Pátio Sambaíba

> **Última atualização:** 16/05/2026
> **Branch:** `v3.0-dev`
> **Autor:** Alisson Martins · ADS Anhanguera

Documento consolidado do progresso da v3.0. **A v2 continua em produção sem modificação.**

---

## 1. Estrutura geral do repositório

```
Gestao-Patio-Sambaiba/
├── frontend/        ← V2 código-fonte (NÃO MEXER — em produção)
├── v2/              ← V2 deploy GitHub Pages (NÃO MEXER)
├── index.html       ← V2 raiz (NÃO MEXER)
│
├── database/        ← V3 — banco PostgreSQL (✅ concluído)
├── backend/         ← V3 — API FastAPI (✅ concluído)
├── docs/            ← documentação (este arquivo está aqui)
└── frontend-v3/     ← V3 — frontend novo (🔄 em desenvolvimento — Fases 5.1, 5.2, 5.3 concluídas)
```

**Regra:** todo desenvolvimento novo da v3 fica em pastas separadas. A v2 não é tocada.

---

## 2. Fase 1–3: Banco de dados ✅

**Local:** `database/`

| Item | Estado |
|---|---|
| Especificação | `01-especificacao-banco-v3.md` |
| DER lógico | `02-der-logico-v3.html` (A3 paisagem, 12 tabelas) |
| Dicionário | `03-dicionario-de-dados.xlsx` (18 abas) |
| Migrations SQL | `migrations/001` a `006` |
| Seeds | `seeds/01-filas` a `06-usuario-admin` |

**12 tabelas no banco `gestao_patio_sambaiba`:**

- **Operacionais (8):** usuario, motorista, onibus, fila, alocacao_patio, escala, alerta, ficha_manutencao
- **Catálogos (3):** linha, tipo_defeito, permissao
- **Sistema (1):** importacao_escala

**Decisões fechadas:**
- Apenas Garagem 3, sem tabela `garagem`
- UUID em todas as PKs
- Filas + posições especiais + manutenção em tabela única (`fila` com coluna `tipo`)
- Histórico completo (`alocacao_patio.ativa`)
- Soft delete em escala/alerta/ficha_manutencao
- Auditoria completa nas tabelas operacionais
- Sincronização híbrida (offline-first)
- Setor derivado do prefixo da frota (1xxx → E2, 2xxx → AR2) via coluna gerada no banco

**Banco já rodando localmente:**
- PostgreSQL 17 instalado, banco populado com seeds
- Admin: RE=`ADMIN001`, senha=`57402232` (local)

---

## 3. Fase 4: Backend FastAPI ✅

**Local:** `backend/`

### Stack

- Python 3.14 / 3.11+
- FastAPI 0.115+ · SQLAlchemy 2.0+ · Pydantic 2 · psycopg 3
- JWT (python-jose) + bcrypt
- openpyxl (importação Excel)

### Estrutura

```
backend/
├── pyproject.toml
├── .env.example / .env (com DATABASE_URL e SECRET_KEY)
├── README.md
├── app/
│   ├── main.py                    ← entrypoint FastAPI
│   ├── core/
│   │   ├── config.py              ← Pydantic Settings
│   │   ├── database.py            ← engine + sessão
│   │   ├── deps.py                ← CurrentUser, AdminUser
│   │   ├── exception_handlers.py  ← respostas padronizadas
│   │   ├── security.py            ← bcrypt + JWT
│   │   └── utils.py               ← paginação + auditoria
│   ├── models/   (12 modelos + 10 ENUMs + mixins)
│   ├── schemas/  (35+ schemas Pydantic + auth + patio + importacao)
│   ├── routers/  (14 routers, ~34 endpoints)
│   └── services/
│       └── importacao_excel.py    ← parser de planilha
├── scripts/
│   └── set_password.py            ← utilitário trocar senha
└── tests/
```

### Sub-fases concluídas

| # | Status | Descrição |
|---|---|---|
| 4.1 | ✅ | Setup + endpoint `/health` (12 tabelas conferidas) |
| 4.2 | ✅ | 12 modelos SQLAlchemy + 35+ schemas Pydantic |
| 4.3 | ✅ | Autenticação JWT (login JSON + form OAuth2 + me + bcrypt) |
| 4.4 | ✅ | CRUD de catálogos (6 routers, ~22 endpoints) |
| 4.5 | ✅ | Endpoints operacionais (5 routers, ~15 endpoints) |
| 4.6 | ✅ | Importação Excel (upload, parser, reverter) |
| 4.7 | ✅ | Exception handlers padronizados + docs Swagger |

### 34 endpoints REST disponíveis

**Sistema:** `GET /` · `GET /health`

**Autenticação:** `POST /auth/login` (JSON) · `POST /auth/token` (form OAuth2 — usado pelo Authorize do Swagger) · `GET /auth/me`

**Catálogos:** `/onibus` · `/motoristas` · `/linhas` · `/tipos-defeito` · `/filas` · `/usuarios`

**Operacionais:** `/alocacoes` · `/escalas` · `/alertas` · `/manutencao`

**Pátio (visão consolidada):**
- `GET /patio` — query master das 5 tabelas (alimenta tela e impressão)
- `GET /patio/onibus/{frota}` — onde está o ônibus X
- `GET /patio/remanejamento` — ônibus em manutenção COM escala hoje

**Importação Excel:** `POST /importacoes/escala` (upload) · `GET /importacoes` · `GET /importacoes/{id}` · `POST /importacoes/{id}/reverter`

### Permissões

| Recurso | Quem pode escrever |
|---|---|
| onibus, motoristas, alocacoes, escalas, alertas, manutencao, importacao | ADMIN, COORDENADOR |
| linhas, tipos-defeito, filas, usuarios | Apenas ADMIN |
| Login + ler tudo | Qualquer usuário ativo |

### Validações estruturais

- ✅ Trigger PostgreSQL valida cruzamento E2/AR2 na escala
- ✅ UNIQUE parcial impede 2 alocações ativas para o mesmo ônibus
- ✅ UNIQUE parcial impede PRESO/AMOSTRAL duplicado ativo
- ✅ Trigger gera `setor` automaticamente do prefixo da frota
- ✅ Trigger preenche `concluida_em` quando ficha é fechada
- ✅ Audit fields (criado_por, atualizado_por) automáticos
- ✅ Soft delete em escala/alerta/ficha_manutencao
- ✅ Senha bcrypt 12 rounds, JWT HS256 (8h)

### Como rodar localmente

```powershell
cd "C:\Users\aliss\OneDrive\Documentos\Projetos_dev\Gestao-Patio-Sambaiba\backend"
.\.venv\Scripts\Activate.ps1
fastapi dev app/main.py
```

API: `http://127.0.0.1:8000` · Swagger: `http://127.0.0.1:8000/docs`

---

## 4. Fase 5: Frontend v3 🔄 EM DESENVOLVIMENTO

**Local:** `frontend-v3/`

### Decisões arquiteturais (fechadas)

| # | Decisão | Escolha |
|---|---|---|
| 1 | Stack | Vanilla JS + ES Modules (sem build step) |
| 2 | Estilização | CSS puro com variáveis (`--surface`, `--accent`, etc.) — porta literal dos tokens da V2 |
| 3 | API client | `assets/js/api.js` centraliza fetch + JWT no localStorage + tratamento de 401 |
| 4 | Auto-detect ambiente | `assets/js/config.js` resolve API_BASE_URL conforme hostname (local vs prod) |
| 5 | Dev | Live Server no VS Code |
| 6 | Prod | GitHub Pages subpath `/v3/` (deploy futuro) |
| 7 | Princípio orientador | V3 é V2 fiel + backend — UX da V2 portada literal, mudanças anti-V2 só com decisão explícita |

### Sub-fases concluídas

| # | Status | Commit | Descrição |
|---|---|---|---|
| 5.1 | ✅ | `9c646ae` | Tela de login + fluxo JWT (login, validação de token, redirect, logout) |
| 5.2 | ✅ | `f981b9c` | Visualização do pátio em tempo real com polling 5s (stats bar + grid de filas) |
| 5.2.1 | ✅ | `f9395bc` | Tipo `ESPECIAL_REMOTA` no banco + 4 posições novas (Noturno, Reservados, Coqueirinho, Ilha) |
| 5.3 | ✅ | `2b1747c`, `0a819cd`, `8accf55`, `43a6c4a` | Alocação rápida: GET /patio com `alocacao_id`, POST /alocacoes/bloco atômico, painel "Registrar Filas" com modo bloco Ida/Volta, modal de mover/remover ônibus alocado |

### Detalhes da Fase 5.3 (concluída em 16/05/2026)

**Backend:**
- `GET /patio` passou a devolver `alocacao_id` em cada chip — habilita operações de mover/remover diretamente pela UI sem segundo round-trip.
- Endpoint atômico `POST /alocacoes/bloco` com semântica de sentido `ida` (empilha no fim) ou `volta` (insere em pos.1 empurrando os demais), resolvendo o conflito com o índice `uq_alocacao_fila_posicao_ativa`.
- Schema `AlocacaoBlocoCreate` aceita `numero_frota`, `fila` (resolve numérica ou nome especial), `linha_codigo` e `sentido`.

**Frontend:**
- Painel "Registrar Filas" portado da V2 para `patio.html`, posicionado entre stats e grid.
- Modo bloco com botões `↑` (ida, padrão), `↓` (volta), `✔` (marcar) e `↩` (desfazer último), barra de status dinâmica e histórico inline da fila atual.
- Modal de edição/remoção acionado por clique no chip do grid: dropdown com todas as filas + campo Linha + ações Salvar/Remover, consumindo `POST /alocacoes` e `DELETE /alocacoes/{id}`.
- Concorrência multi-usuário tratada como last-write-wins via trigger do banco.

**Exceções deliberadas à fidelidade V2** (documentadas em `docs/V2-vs-V3-funcionalidades.md`):
- Sem autocomplete no input de fila do modo bloco — decidido em 16/05 por atrapalhar a digitação rápida.
- Após cada Marcar bem-sucedido, limpa prefixo + linha mas mantém a fila preenchida.

### Próximas sub-fases (a discutir)

| # | Tema | Observação |
|---|---|---|
| 5.4 | Tela de remanejamento (`GET /patio/remanejamento`) | Listar ônibus em manutenção com escala hoje |
| 5.5 | Telas de alerta (PRESO/AMOSTRAL) | Consome `/alertas` |
| 5.6 | Tela de manutenção (fichas) | Consome `/manutencao` |
| 5.7 | Importação Excel de escala | Consome `/importacoes/escala` |
| 5.8 | Cadastros (ônibus, motoristas, linhas, usuários) | Apenas perfil ADMIN |

---

## 5. Fases 6 e 7: depois da v3 funcional

| # | Fase | Status |
|---|---|---|
| 6 | Sincronização offline-first (IndexedDB ↔ API) + PWA instalável | depois |
| 7 | Deploy Railway/Render (backend) + GitHub Pages (frontend) | depois |

---

## 6. Bugs conhecidos da v2 que a v3 resolve estruturalmente

- **Encoding UTF-8 na importação Excel** → resolvido com openpyxl no backend
- **Validação cruzada E2/AR2** → resolvido com trigger no banco + validação no schema
- **Manutenção sumindo da impressão (bug 1 da v2)** → resolvido com tabela `fila` unificada
- **Chip fantasma após mover (bug 2 da v2)** → resolvido com UNIQUE parcial em alocação ativa
- **Necessidade de "limpar" entre dias** → resolvido com `data` em escala (histórico coexiste)

---

## 7. Comandos de retomada (para a próxima sessão)

### Subir backend

```powershell
cd "C:\Users\aliss\OneDrive\Documentos\Projetos_dev\Gestao-Patio-Sambaiba\backend"
.\.venv\Scripts\Activate.ps1
fastapi dev app/main.py
```

### Verificar banco

```powershell
# pgAdmin ou:
psql -U postgres -d gestao_patio_sambaiba -c "SELECT COUNT(*) FROM fila"
```

### Trocar senha de qualquer usuário

```powershell
python scripts/set_password.py <RE> <nova_senha>
```

---

*Atualizado em 16/05/2026 — Fase 5.3 concluída (alocação rápida + modal mover/remover).*
*Próxima sessão: discutir escopo da Fase 5.4 (remanejamento) ou seguir lista 5.5–5.8.*
