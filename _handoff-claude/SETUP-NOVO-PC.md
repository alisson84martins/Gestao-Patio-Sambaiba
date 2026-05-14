# 🖥️ SETUP NOVO PC — Sistema de Gestão de Pátio Sambaíba V3

> Passo a passo para preparar o novo computador. Como o repositório está no OneDrive, ele já vai estar sincronizado quando logar com a mesma conta. O que falta é instalar as ferramentas e recriar o banco local.

## ✅ Pré-requisitos a instalar

Baixe e instale na seguinte ordem:

### 1. Git for Windows
- https://git-scm.com/download/win
- Instalação padrão (next, next, next)

### 2. Python 3.11+ (recomendado: 3.12 ou 3.13)
- https://www.python.org/downloads/
- ⚠️ **MARCAR "Add Python to PATH"** durante a instalação
- Verificar no PowerShell: `python --version`

### 3. PostgreSQL 17
- https://www.postgresql.org/download/windows/
- Use o instalador EnterpriseDB
- **Anote a senha do usuário `postgres`** que você definir — vai precisar
- Aceite tudo padrão (porta 5432, locale padrão)
- Inclua **pgAdmin 4** na instalação (vem junto)

### 4. VS Code
- https://code.visualstudio.com/
- Após instalar, abra e instale as extensões:
  - **Python** (Microsoft)
  - **REST Client** (Huachao Mao)
  - **PostgreSQL** (Chris Kolkman) — opcional, ajuda a explorar o banco
  - **Pylance** (Microsoft)
  - **Ruff** (Astral Software) — formatter Python

### 5. (Opcional) GitHub Desktop
- https://desktop.github.com/
- Mais simples que linha de comando para sync com GitHub

## 📂 Confirmar OneDrive sincronizou o projeto

1. Abra o Explorador de Arquivos
2. Vá em `C:\Users\<SEU_USUARIO>\OneDrive\Documentos\Projetos_dev\`
3. A pasta `Gestao-Patio-Sambaiba` deve estar lá com todos os subdiretórios

Se não estiver:
- Verifique se logou no OneDrive com a conta correta (alisson84martins@gmail.com)
- Aguarde a sincronização inicial (pode demorar dependendo da internet)
- Garante que a pasta `Documentos` está marcada para sincronizar

## 🗄️ Recriar o banco PostgreSQL local

### 1. Liberar execução de scripts no PowerShell (uma vez só)

Abra o PowerShell e rode:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
Responda **S** quando perguntar.

### 2. Criar o banco no pgAdmin

1. Abra o **pgAdmin 4**
2. Servers → PostgreSQL → entre com a senha
3. Clique direito em **Databases** → **Create** → **Database**
4. Nome: `gestao_patio_sambaiba`
5. Encoding: `UTF8`, Template: `template0`
6. **Save**

### 3. Rodar as migrations (na ordem 002 → 007)

No pgAdmin, com `gestao_patio_sambaiba` selecionado:
1. Botão direito → **Query Tool**
2. **File → Open** → navegue até `backend/../database/migrations/002-extensions-enums.sql`
3. **F5** para executar
4. Repita para `003-tables-core.sql`, `004-tables-apoio.sql`, `005-constraints-indexes.sql`, `006-functions-triggers.sql`

### 4. Rodar os seeds (na ordem 01 → 06)

Mesmo processo, abrindo cada arquivo em `database/seeds/`:
1. `01-filas-numericas.sql`
2. `02-posicoes-especiais.sql`
3. `03-manutencao.sql`
4. `04-tipos-defeito.sql`
5. `05-linhas-exemplo.sql`
6. `06-usuario-admin.sql`

### 5. Validar que está tudo lá

No Query Tool:
```sql
SELECT 'filas_num' AS item, COUNT(*) FROM fila WHERE tipo='NUMERICA'
UNION ALL SELECT 'especiais', COUNT(*) FROM fila WHERE tipo='ESPECIAL'
UNION ALL SELECT 'manutencao', COUNT(*) FROM fila WHERE tipo='MANUTENCAO'
UNION ALL SELECT 'linhas', COUNT(*) FROM linha
UNION ALL SELECT 'tipos_defeito', COUNT(*) FROM tipo_defeito
UNION ALL SELECT 'admin', COUNT(*) FROM usuario WHERE perfil='ADMIN';
```

Esperado: 33 filas numéricas, 6 especiais, 1 manutenção, 8 linhas, 29 tipos defeito, 1 admin.

## 🐍 Configurar e subir o backend

### 1. Abrir terminal na pasta backend

```powershell
cd "C:\Users\<SEU_USUARIO>\OneDrive\Documentos\Projetos_dev\Gestao-Patio-Sambaiba\backend"
```

### 2. Criar ambiente virtual

```powershell
python -m venv .venv
```

### 3. Ativar o venv

```powershell
.\.venv\Scripts\Activate.ps1
```

O prompt deve mudar para `(.venv) PS C:\...\backend>`

### 4. Instalar dependências

```powershell
pip install --upgrade pip
pip install -e .
```

Aguarde uns 2-3 minutos. Vai instalar 60+ pacotes.

### 5. Configurar o arquivo .env

```powershell
copy .env.example .env
notepad .env
```

No Notepad, ajuste **2 linhas**:

**DATABASE_URL** (substitua `SUA_SENHA` pela senha do PostgreSQL):
```env
DATABASE_URL=postgresql+psycopg://postgres:SUA_SENHA@localhost:5432/gestao_patio_sambaiba
```

**SECRET_KEY** (gere uma nova com o comando abaixo):
```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```
Cole a string longa que apareceu no campo `SECRET_KEY=`.

Salve com `Ctrl+S` e feche.

### 6. Definir senha do admin

```powershell
python scripts/set_password.py ADMIN001 57402232
```

(ou outra senha que preferir — anote em algum lugar)

### 7. Subir a API

```powershell
fastapi dev app/main.py
```

Espere aparecer:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### 8. Testar no navegador

Abra: http://127.0.0.1:8000/docs

Deve aparecer o Swagger UI com 34 endpoints listados.

### 9. Fazer login

1. Clique no botão **Authorize** (canto superior direito)
2. Preencha:
   - **username:** `ADMIN001`
   - **password:** `57402232` (ou a que você definiu)
   - Demais campos vazios
3. Clique **Authorize** → **Close**
4. Vá em `GET /auth/me` → **Try it out** → **Execute**
5. Se aparecer seus dados em **Server response** com status 200, está tudo OK!

## 🧪 Testar API direto no VS Code

1. Abra o VS Code na pasta do projeto
2. Abra o arquivo `backend/requests.http`
3. Acima de cada bloco aparece "Send Request" — clique para executar
4. Comece pelo `### 3. Login` (captura o token automaticamente)
5. Depois pode executar qualquer outro endpoint

## 🛠️ Comandos do dia a dia (memorizar)

```powershell
# Sempre que abrir o terminal pra trabalhar:
cd "C:\Users\<SEU_USUARIO>\OneDrive\Documentos\Projetos_dev\Gestao-Patio-Sambaiba\backend"
.\.venv\Scripts\Activate.ps1
fastapi dev app/main.py
```

Para parar: `Ctrl+C` no terminal.

## ⚠️ Problemas comuns

### "ExecutionPolicy" bloqueando o `Activate.ps1`
Já resolvido no passo 1 acima (Set-ExecutionPolicy).

### "Token URL: /auth/login" no Swagger Authorize, mas dá erro 422
Pare a API com `Ctrl+C`, suba de novo com `fastapi dev app/main.py`, e dê `Ctrl+Shift+R` no navegador. O Token URL correto é `/auth/token`.

### Esqueci a senha do PostgreSQL
1. Abra o pgAdmin
2. Login/Group Roles → postgres → Properties → Definition → defina nova senha → Save
3. Atualize a senha no `.env` do backend

### `/health` retorna `database: error`
- Verifique se o PostgreSQL está rodando (Serviços do Windows → `postgresql-x64-17`)
- Confirme que a senha no `.env` está correta
- Confirme que o banco `gestao_patio_sambaiba` existe no pgAdmin

## 📦 Sincronizar com GitHub (opcional)

Se quiser commitar mudanças e enviar pro GitHub:

```powershell
cd "C:\Users\<SEU_USUARIO>\OneDrive\Documentos\Projetos_dev\Gestao-Patio-Sambaiba"
git status
git add .
git commit -m "mensagem aqui"
git push origin v3.0-dev
```

⚠️ **NUNCA usar `--force` em `main`** sem antes confirmar — main serve a V2 em produção.

## 🚀 Próximos passos depois do setup

1. Confirme que `/health` retorna `database: connected` e `tabelas_publicas: 12`
2. Confirme que login com ADMIN001 funciona
3. Avise o Claude que terminou o setup
4. Vamos começar a **fase 5** (frontend novo)

---

**Pronto! Sistema rodando no novo PC. Para retomar o desenvolvimento, abra uma nova conversa com o Claude e diga:**

> "Estou no novo PC. Setup concluído. Vamos começar a fase 5 — frontend V3."

O Claude vai ler esta pasta `_handoff-claude/` e saber exatamente onde continuar.
