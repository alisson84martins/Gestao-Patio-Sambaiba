# Progresso da Sessão — 2026-05-12

> Snapshot de onde paramos no setup do novo PC. Continuar daqui amanhã.

## Objetivo da sessão

Executar o que faltava para o **teste em VS Code** com o `requests.http` — destrancando o caminho para começar a Fase 5 (Frontend V3).

## Tarefa zero (feita antes do setup)

- **Criado o arquivo `backend/requests.http`** que estava referenciado no handoff mas não existia. Coleção REST Client com 21 blocos: login JSON e OAuth2, /auth/me, todos os catálogos, pátio consolidado, operacionais e importação. Já pronto pra usar quando o backend subir.

## Fases concluídas hoje

### Fase 1 — Python 3.14.5 instalado
- Baixado do site oficial python.org, "Add to PATH" marcado
- `python --version` → `Python 3.14.5`

### Fase 2 — PostgreSQL 18.3 + pgAdmin 4 instalados
- Instalador EDB (versão 18.3, era a única disponível — handoff falava em 17 mas 18 é compatível)
- **Senha do superusuário `postgres`:** `57402232` (mesma do admin do sistema, pra simplificar)
- pgAdmin abre e conecta no servidor PostgreSQL 18 sem erro

### Fase 3 — `.venv` reconstruída
- Apagada a `.venv` antiga (que apontava pra um Python 3.14 de outro PC via OneDrive — daí o erro `did not find executable...Python314\python.exe`)
- Recriada com `python -m venv .venv`
- `pip install -e .` rodou **sem erros**. 60+ pacotes instalados, incluindo `bcrypt==4.0.1` (que era o risco no Python 3.14 — deu certo)
- `.env` já estava preenchido: `DATABASE_URL` aponta pra `postgres:57402232@localhost:5432/gestao_patio_sambaiba` e `SECRET_KEY` gerada. **Nada a mexer.**

## Fase 4 — PAUSADO AQUI

Banco criado, Query Tool aberto e conectado. **Faltam:** rodar as migrations e seeds.

### O que já foi feito
- Database `gestao_patio_sambaiba` criado no pgAdmin (UTF8, template0, owner postgres)
- Query Tool aberto conectado em `gestao_patio_sambaiba/postgres@PostgreSQL 18`

### Onde retomar amanhã

**Passo 4.4 — Rodar as 5 migrations EM ORDEM** (pgAdmin → Query Tool → File → Open → F5):

Pasta: `C:\Users\aliss\OneDrive\Documentos\Projetos_dev\Gestao-Patio-Sambaiba\database\migrations\`

1. `002-extensions-enums.sql`
2. `003-tables-core.sql`
3. `004-tables-apoio.sql`
4. `005-constraints-indexes.sql`
5. `006-functions-triggers.sql`

**Passo 4.5 — Rodar os 6 seeds EM ORDEM:**

Pasta: `C:\Users\aliss\OneDrive\Documentos\Projetos_dev\Gestao-Patio-Sambaiba\database\seeds\`

1. `01-filas-numericas.sql`
2. `02-posicoes-especiais.sql`
3. `03-manutencao.sql`
4. `04-tipos-defeito.sql`
5. `05-linhas-exemplo.sql`
6. `06-usuario-admin.sql`

**Passo 4.6 — Validar com este SQL no Query Tool:**

```sql
SELECT 'filas_num' AS item, COUNT(*) FROM fila WHERE tipo='NUMERICA'
UNION ALL SELECT 'especiais', COUNT(*) FROM fila WHERE tipo='ESPECIAL'
UNION ALL SELECT 'manutencao', COUNT(*) FROM fila WHERE tipo='MANUTENCAO'
UNION ALL SELECT 'linhas', COUNT(*) FROM linha
UNION ALL SELECT 'tipos_defeito', COUNT(*) FROM tipo_defeito
UNION ALL SELECT 'admin', COUNT(*) FROM usuario WHERE perfil='ADMIN';
```

Esperado: 33 / 6 / 1 / 8 / 29 / 1.

## Fase 5 — Subir API e testar no VS Code (pendente)

Depois da Fase 4 dar 6/6 na validação:

1. No PowerShell, na pasta `backend`:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   python scripts/set_password.py ADMIN001 57402232
   fastapi dev app/main.py
   ```
2. Esperar `Uvicorn running on http://127.0.0.1:8000`
3. Abrir o navegador em `http://127.0.0.1:8000/health` → deve mostrar `status: "ok", database: "connected", tabelas_publicas: 12`
4. No VS Code, abrir `backend/requests.http`
5. **Confirmar que a extensão REST Client (Huachao Mao) está instalada** — se não, instalar via Ctrl+Shift+X
6. Clicar em **Send Request** acima do bloco `### 3. Login JSON` (captura o token automaticamente)
7. Clicar em **Send Request** acima do `### 4. Quem sou eu` → deve voltar dados com `perfil=ADMIN`
8. Clicar em **Send Request** acima do `### 5. Listar filas` → deve voltar 40 itens

Quando os 4 blocos passarem (health, login, me, filas), **a Fase 4 do backend está oficialmente validada no novo PC** e a gente pode partir pra Fase 5 do projeto (Frontend V3).

## Coisas a lembrar amanhã

- Pra subir a API, o PowerShell precisa estar na pasta `backend` E com a `.venv` ativada. Sinal de ativação: o prompt começa com `(.venv)`.
- A senha do `postgres` e do `ADMIN001` é a mesma: `57402232`.
- Se o `python --version` ou `psql` não funcionar no PowerShell, é porque tem que **fechar e reabrir** o PowerShell depois de instalar Python/PostgreSQL.
- Cuidado: **não** copiar arquivos JS com `Copy-Item` do Windows (adiciona null bytes). Não é o caso agora, mas vai ser na Fase 5.
- A V2 em `frontend/`, `v2/` e `index.html` está em produção. **Nada toca nela.**

## Estado dos arquivos importantes

| Arquivo | Estado |
|---|---|
| `backend/.venv/` | Recriada hoje, funcionando |
| `backend/.env` | Preenchido e correto, não mexer |
| `backend/requests.http` | Criado hoje |
| `backend/pyproject.toml` | Original, funcionando |
| PostgreSQL 18.3 | Instalado, senha `postgres`=`57402232` |
| Database `gestao_patio_sambaiba` | Criada, vazia, aguardando migrations |

---

**Para retomar amanhã, diga ao Claude:**

> "Vamos continuar de onde paramos ontem. Lê o PROGRESSO-2026-05-12.md."

O Claude vai te guiar do passo 4.4 em diante.
