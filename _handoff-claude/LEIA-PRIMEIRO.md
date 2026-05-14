# 🤖 LEIA PRIMEIRO — Instruções para o Claude Cowork

> **Quando o Alisson abrir uma nova conversa no novo computador, ele vai te apontar para esta pasta. Leia os 3 arquivos desta pasta na ordem indicada antes de qualquer ação.**

## 📂 Ordem de leitura

1. **`LEIA-PRIMEIRO.md`** (este arquivo) — instruções gerais
2. **`CONTEXTO-COMPLETO.md`** — tudo sobre o projeto, estado atual, decisões
3. **`SETUP-NOVO-PC.md`** — passo a passo pra preparar o novo computador
4. **`PROGRESSO-YYYY-MM-DD.md` mais recente** — snapshot da última sessão. Se existir, leia ESTE PRIMEIRO depois do LEIA-PRIMEIRO, porque ele tem o ponto exato de retomada e supera o que estiver desatualizado nos arquivos anteriores.

## 👤 Quem é o Alisson

- **Nome:** Alisson Martins
- **Email:** alisson84martins@gmail.com
- **Profissão:** Coordenador de Tráfego da Sambaíba Transportes Urbanos (Garagem 3, São Paulo)
- **Formação:** Cursando Análise e Desenvolvimento de Sistemas (ADS Anhanguera)
- **Perfil técnico:** Conhece programação, mas **não é dev profissional**. Aprende junto enquanto desenvolve. Operacional ganha de teoria.
- **Idioma:** Português brasileiro (sempre responder em pt-BR)

## 🚌 O projeto

**Sistema de Gestão de Pátio Sambaíba** — aplicação web para coordenar a alocação de ônibus no pátio da garagem antes da saída para a rua.

- **Repositório:** https://github.com/alisson84martins/Gestao-Patio-Sambaiba
- **V2 em produção:** https://alisson84martins.github.io/Gestao-Patio-Sambaiba/v2/
- **Branch ativa:** `v3.0-dev`

## ⚠️ REGRAS CRÍTICAS — NÃO QUEBRE

### 1. V2 NÃO PODE SER MODIFICADA
A v2 está em **produção** sendo usada por operadores reais nas madrugadas. Mexer nela quebra a operação.
- ❌ NÃO mexer em `frontend/`, `v2/`, `index.html` (raiz do repo)
- ✅ Tudo da v3 fica em pastas separadas: `database/`, `backend/`, `frontend-v3/` (futura)

### 2. Comportamento esperado
- **Tom:** direto, prático, sem encher linguiça
- **Formatação:** use bullets e tabelas só quando ajudam — preferir prose quando possível
- **Nunca usar emojis em código ou docs profissionais** (ele só quer emoji quando explicitamente solicitado)
- **Sempre confirme decisões** antes de executar mudanças grandes
- **Use AskUserQuestion** quando tiver dúvida — não assuma
- **Use TodoWrite/TaskCreate** pra trackear trabalhos longos

### 3. Stack do projeto
- **Banco:** PostgreSQL 18.3 com 12 tabelas (rodando local + admin já criado) — instalado no novo PC em 2026-05-12
- **Backend:** FastAPI + SQLAlchemy 2 + Pydantic 2 + JWT — **CONCLUÍDO 06/05/2026** com 34 endpoints; **validado no novo PC em 2026-05-13**
- **Frontend V2:** Vanilla JS + HTML + CSS + SheetJS — em produção, NÃO MEXER
- **Frontend V3:** ainda não criado — próxima fase (5). Stack escolhida: **Vanilla JS + Web Components**, dev com **Live Server**, prod em GitHub Pages subpath `/v3/`

### 4. Credenciais locais (já configuradas no novo PC)
- **Admin do sistema:** RE=`ADMIN001`, senha=`57402232`
- **Banco PostgreSQL:** usuário `postgres`, senha=`57402232`
- **Sistema operacional:** Windows com OneDrive sincronizando

## 🎯 Como começar a próxima sessão

Quando o Alisson abrir nova conversa, ele provavelmente vai dizer algo como:
- "Vamos retomar o projeto Gestão de Pátio"
- "Vamos começar a fase 5"
- "Lê o PROGRESSO-YYYY-MM-DD.md"

**Sua resposta deve ser:**

1. Ler o `PROGRESSO-YYYY-MM-DD.md` mais recente desta pasta (atual: `PROGRESSO-2026-05-13.md`)
2. A partir dele, identificar o ponto exato de retomada
3. **Setup do novo PC já está concluído** (2026-05-13) — não precisa mais guiar pelo `SETUP-NOVO-PC.md`, a menos que o Alisson sinalize trocar de máquina de novo

## 📚 Skills/ferramentas relevantes

Em projetos como esse o Claude tem acesso a skills:
- `gestao-patio-sambaiba` — skill exclusiva do projeto, contém arquitetura e regras
- `database-modeling` — usar em qualquer questão de banco
- `escalas-patio-cowork` — pra processar planilhas de escala
- `ferramentas-qualidade-sambaiba` — Pareto, Ishikawa, etc.
- `coordenador-de-trafegosambaiba-transportes` — perfil operacional
- `xlsx`, `docx`, `pdf` — pra criar documentos

**Sempre invocar skills relevantes via Skill tool antes de começar tarefas grandes.**

## 🗂️ Estrutura completa do repositório

```
Gestao-Patio-Sambaiba/
├── frontend/              ← V2 código-fonte — NÃO MEXER
├── v2/                    ← V2 deploy GitHub Pages — NÃO MEXER
├── index.html             ← V2 raiz — NÃO MEXER
│
├── database/              ← V3 banco PostgreSQL ✅ CONCLUÍDO
│   ├── 01-especificacao-banco-v3.md
│   ├── 02-der-logico-v3.html
│   ├── 03-dicionario-de-dados.xlsx
│   ├── README.md
│   ├── migrations/        (001-006 + seeds 01-06)
│   └── seeds/
│
├── backend/               ← V3 API FastAPI ✅ CONCLUÍDO
│   ├── pyproject.toml
│   ├── .env.example
│   ├── README.md
│   ├── requests.http      ← REST Client pro VS Code
│   ├── app/               (main, core/, models/, schemas/, routers/, services/)
│   ├── scripts/set_password.py
│   └── tests/
│
├── docs/                  ← documentação geral
│   ├── DER_Modelo_Conceitual_Sambaiba.html
│   ├── Requisitos_Gestao_Patio_v1.docx
│   └── STATUS-V3.md       ← snapshot do progresso
│
├── _handoff-claude/       ← VOCÊ ESTÁ AQUI
│   ├── LEIA-PRIMEIRO.md   (este arquivo)
│   ├── CONTEXTO-COMPLETO.md
│   └── SETUP-NOVO-PC.md
│
└── frontend-v3/           ← FASE 5 (a criar)
```

---

**Próximo passo:** abra agora o `CONTEXTO-COMPLETO.md`.
