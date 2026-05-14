# Backend — Sistema de Gestão de Pátio Sambaíba v3.0

API REST em FastAPI + PostgreSQL para o sistema de gestão de pátio da Garagem 3.

## Stack

- **Python:** 3.11+
- **Framework:** FastAPI 0.115+
- **ORM:** SQLAlchemy 2.0+
- **Driver:** psycopg 3 (binary)
- **Validação:** Pydantic 2
- **Auth:** JWT (python-jose) + bcrypt
- **Migrations:** Alembic (preparado, não usado ainda — DDL em `../database/migrations/`)
- **Banco:** PostgreSQL 15+ (banco `gestao_patio_sambaiba`)

## Estrutura

```
backend/
├── pyproject.toml        ← dependências
├── .env.example          ← template de configuração
├── .env                  ← suas variáveis (NÃO commitar)
├── .gitignore
├── README.md             ← este arquivo
├── app/
│   ├── __init__.py
│   ├── main.py           ← entrypoint FastAPI
│   ├── core/
│   │   ├── config.py     ← Pydantic Settings
│   │   ├── database.py   ← engine + sessão SQLAlchemy
│   │   ├── deps.py       ← get_current_user, AdminUser
│   │   ├── exception_handlers.py ← respostas padronizadas
│   │   ├── security.py   ← bcrypt + JWT
│   │   └── utils.py      ← paginação + auditoria
│   ├── models/           ← 12 modelos SQLAlchemy + 10 ENUMs
│   ├── schemas/          ← Pydantic input/output
│   ├── routers/          ← 14 routers (~34 endpoints)
│   └── services/
│       └── importacao_excel.py  ← parser de planilha
├── scripts/
│   └── set_password.py   ← utilitário pra trocar senha
└── tests/
```

## Como rodar localmente

### 1. Criar ambiente virtual

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Instalar dependências

```powershell
pip install --upgrade pip
pip install -e .
```

### 3. Configurar .env

```powershell
copy .env.example .env
notepad .env
```

Ajuste:
- `DATABASE_URL` → senha do PostgreSQL local
- `SECRET_KEY` → gere com: `python -c "import secrets; print(secrets.token_urlsafe(64))"`

### 4. Definir senha do admin (uma vez)

```powershell
python scripts/set_password.py ADMIN001 sua_senha_aqui
```

### 5. Subir a API

```powershell
fastapi dev app/main.py
```

API em `http://127.0.0.1:8000` · Swagger em `http://127.0.0.1:8000/docs`.

## Endpoints — visão geral (34 total)

### Sistema (2)
- `GET /` · `GET /health`

### Autenticação (3)
- `POST /auth/login` (JSON) · `POST /auth/token` (form OAuth2 — usado pelo Authorize) · `GET /auth/me`

### Catálogos (~22 endpoints)
- `/onibus` · `/motoristas` · `/linhas` · `/tipos-defeito` · `/filas` · `/usuarios`

### Operacionais
- `/alocacoes` (mover ônibus)
- `/escalas` (CRUD com filtro por data)
- `/alertas` (PRESO/AMOSTRAL + resolver)
- `/manutencao` (fichas)

### Pátio (visão consolidada)
- `GET /patio` — query master das 5 tabelas
- `GET /patio/onibus/{frota}` — onde está
- `GET /patio/remanejamento` — manutenção + escala hoje

### Importação Excel
- `POST /importacoes/escala` — upload .xlsx
- `GET /importacoes` · `GET /importacoes/{id}` · `POST /importacoes/{id}/reverter`

## Permissões resumidas

| Recurso | Quem pode escrever |
|---|---|
| onibus, motoristas, alocacoes, escalas, alertas, manutencao | ADMIN, COORDENADOR |
| linhas, tipos-defeito, filas, usuarios | Apenas ADMIN |
| Importar Excel | ADMIN, COORDENADOR |
| Login + ler tudo | Qualquer usuário ativo |

## Formato da planilha de importação

Linha 1 = cabeçalho (ignorada). A partir da linha 2:

| Coluna | Campo | Obrigatório | Exemplo |
|---|---|---|---|
| A | numero_frota | sim | `1234` |
| B | linha_codigo | sim | `8500-10` |
| C | horario_saida | sim | `04:30` |
| D | re_motorista | não | `MOT001` |
| E | tipo | não | `PLANTAO_E2` |

Se `tipo` vazio, usa `tipo_default` enviado no upload.

## Comandos do dia a dia

```powershell
# Ativar venv
.\.venv\Scripts\Activate.ps1

# Rodar com auto-reload
fastapi dev app/main.py

# Rodar produção
fastapi run app/main.py

# Linter
ruff check .
ruff format .

# Trocar senha de qualquer usuário
python scripts/set_password.py <RE> <nova_senha>
```

## Status do passo 4 (Backend FastAPI)

| # | Status | Descrição |
|---|---|---|
| 4.1 | ✅ | Setup + endpoint /health |
| 4.2 | ✅ | 12 modelos SQLAlchemy + 35+ schemas Pydantic |
| 4.3 | ✅ | Autenticação JWT (login + me + bcrypt) |
| 4.4 | ✅ | CRUD de catálogos (6 routers) |
| 4.5 | ✅ | Endpoints operacionais (5 routers) |
| 4.6 | ✅ | Importação Excel (upload + reverter) |
| 4.7 | ✅ | Exception handlers padronizados + docs |

**Backend completo. Próximo passo: adaptar o frontend v2 → v3 pra consumir a API.**

---

*Sambaíba Transportes Urbanos · Garagem 3 · 2026*
