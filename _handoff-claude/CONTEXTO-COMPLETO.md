# 📚 CONTEXTO COMPLETO — Sistema de Gestão de Pátio Sambaíba V3.0

> Documento autossuficiente. Contém TODAS as decisões, regras e estado do projeto até **06/05/2026**.

---

## 1. Identificação

- **Projeto:** Sistema de Gestão de Pátio Sambaíba
- **Empresa:** Sambaíba Transportes Urbanos (transporte público — São Paulo)
- **Garagem:** Garagem 3
- **Autor:** Alisson Martins (coordenador de tráfego + ADS Anhanguera)
- **Email:** alisson84martins@gmail.com
- **Repositório:** https://github.com/alisson84martins/Gestao-Patio-Sambaiba
- **V2 em produção:** https://alisson84martins.github.io/Gestao-Patio-Sambaiba/v2/
- **Branch ativa:** `v3.0-dev`

## 2. O que o sistema faz

Coordena a alocação de ônibus no pátio da garagem antes da saída para a rua. Operadores vão alocando os ônibus em filas conforme chegam, e o coordenador imprime/visualiza essa distribuição para garantir que cada ônibus saia na linha e horário corretos.

### Fluxo operacional resumido

1. Coordenador importa a escala do dia (planilha Excel) com lista de `ônibus + linha + horário`
2. Operadores vão chegando no pátio e alocam os ônibus em filas
3. Sistema mostra em tempo real onde está cada ônibus
4. Quando próximo do horário de saída, o coordenador imprime a folha de partidas
5. Veículos com problemas são marcados como PRESO (na rua) ou em manutenção
6. Coordenador remaneja escalas se ônibus escalado está em manutenção

## 3. Regras de negócio (CRÍTICAS — não esquecer)

### 3.1 Frota e setores
- Frota tem **4 dígitos** (1234, 5678, etc.)
- Setor é **derivado do prefixo:**
  - Frota começando com `1` → setor **E2**
  - Frota começando com `2` → setor **AR2**
- Status do ônibus: `ATIVO`, `MANUTENCAO`, `INATIVO`, `RESERVA`

### 3.2 Pátio
- **33 filas numéricas** (1 a 33)
- **6 posições especiais:** Coqueiro, Laje, Lavador, Bomba, Elétricos, Fundão
- **Setor de Manutenção** como tipo de fila (extensível para Mecânica/Lataria/etc.)
- Posições novas (ex: Coqueirinho) são adicionadas via INSERT na tabela `fila`
- **Constraint crítica:** um veículo só pode ter UMA alocação `ativa = true` (UNIQUE parcial)
- Posição dentro da fila é sequencial (FIFO)

### 3.3 Escala
- Cada registro pertence a uma **data específica** — escalas de dias diferentes coexistem
- Tipos detectados na importação: `MANOBRA`, `PLANTAO_E2`, `PLANTAO_AR2`
- Origem: `IMPORTACAO_EXCEL` ou `MANUAL`
- Reimportar a mesma data substitui (com confirmação) — registros antigos viram soft delete
- **Validação cruzada:** linha E2 só em frota E2; linha AR2 só em frota AR2 (trigger no banco)

### 3.4 Alertas
- Tipos: `PRESO` (veículo retido na rua) e `AMOSTRAL` (SPTRANS)
- **Prioridade na impressão:** PRESO > AMOSTRAL > normal
- UNIQUE parcial impede duplicidade de alerta ativo do mesmo tipo no mesmo ônibus

### 3.5 Manutenção — separação de conceitos
| Conceito | Tabela | Pergunta que responde |
|---|---|---|
| **ONDE** o ônibus está | `alocacao_patio` (fila tipo MANUTENCAO) | Posição física |
| **O QUE** está sendo feito | `ficha_manutencao` | Defeito, mecânico, status |
| **PARA ONDE** ele vai | `escala` | Linha e horário previstos |

Os 3 coexistem. Cruzamento alimenta tela de remanejamento.

### 3.6 Usuários e perfis
- Perfis: `ADMIN`, `COORDENADOR`, `OPERADOR_PATIO`, `MOTORISTA`, `MECANICO`
- Login via **RE** (registro de funcionário) + senha (bcrypt)
- Permissões granulares por usuário × recurso × pode_ler/pode_escrever
- Motorista loga apenas para CONSULTAR (digita frota → vê posição, horário, linha)

## 4. Arquitetura V3

### 4.1 Banco de dados
- **SGBD:** PostgreSQL 15+ (atualmente PostgreSQL 17 instalado no PC)
- **Nome do banco:** `gestao_patio_sambaiba`
- **12 tabelas:** 8 operacionais + 3 catálogos + 1 sistema
- **Chave primária:** UUID em todas (offline-first híbrido)
- **Sem tabela `garagem`** (decisão: só Garagem 3)

#### Lista de tabelas
**Operacionais (8):** usuario, motorista, onibus, fila, alocacao_patio, escala, alerta, ficha_manutencao
**Catálogos (3):** linha, tipo_defeito, permissao
**Sistema (1):** importacao_escala

### 4.2 Backend (FastAPI) — CONCLUÍDO
- **34 endpoints REST** em 14 routers (~3500 linhas)
- **Stack:** Python 3.11+ · FastAPI 0.115 · SQLAlchemy 2.0 · psycopg 3 · Pydantic 2 · python-jose (JWT) · passlib/bcrypt · openpyxl
- **Autenticação:** JWT HS256, token válido 8 horas
- **Senha:** bcrypt 12 rounds
- **Importação Excel:** parser openpyxl com validação, soft delete e reverter

#### Endpoints
- **Sistema:** `GET /` · `GET /health`
- **Autenticação:** `POST /auth/login` (JSON) · `POST /auth/token` (form OAuth2 — usado pelo Authorize) · `GET /auth/me`
- **Catálogos CRUD:** `/onibus` · `/motoristas` · `/linhas` · `/tipos-defeito` · `/filas` · `/usuarios`
- **Operacionais:** `/alocacoes` · `/escalas` · `/alertas` · `/manutencao`
- **Pátio (visão consolidada):**
  - `GET /patio` — query master das 5 tabelas
  - `GET /patio/onibus/{frota}` — onde está o ônibus X
  - `GET /patio/remanejamento` — ônibus em manutenção COM escala hoje
- **Importação Excel:** `POST /importacoes/escala` · `GET /importacoes` · `GET /importacoes/{id}` · `POST /importacoes/{id}/reverter`

#### Permissões
| Recurso | Quem escreve |
|---|---|
| onibus, motoristas, alocacoes, escalas, alertas, manutencao, importação | ADMIN, COORDENADOR |
| linhas, tipos-defeito, filas, usuarios | Apenas ADMIN |
| Login + leitura | Qualquer usuário ativo |

#### Validações estruturais (no banco)
- Trigger valida cruzamento E2/AR2 na escala
- UNIQUE parcial impede 2 alocações ativas pro mesmo ônibus
- UNIQUE parcial impede PRESO/AMOSTRAL duplicado ativo
- Trigger gera `setor` automaticamente do prefixo da frota
- Trigger preenche `concluida_em` ao fechar ficha
- Audit fields (criado_por, atualizado_por) automáticos
- Soft delete em escala/alerta/ficha_manutencao

### 4.3 Frontend
- **V2:** Vanilla JS + HTML + CSS + SheetJS — em `frontend/` e `v2/` — **EM PRODUÇÃO, NÃO MEXER**
- **V3:** ainda não criado, vai em `frontend-v3/` — **PRÓXIMA FASE**

## 5. Decisões fechadas (não revisitar)

| # | Tema | Decisão |
|---|---|---|
| 1 | Multi-garagem | Apenas Garagem 3. Sem tabela `garagem`. |
| 2 | Histórico de pátio | Histórico completo. Coluna `ativa` marca posição atual. |
| 3 | Filas + posições + manutenção | Tabela única `fila` com coluna `tipo` (NUMERICA/ESPECIAL/MANUTENCAO). |
| 4 | Escala | Importação Excel + edição manual. Auditoria por origem. |
| 5 | Integração Nimer | Fora do escopo. `codigo_externo` reservado em onibus/motorista para futuro. |
| 6 | Chave primária | UUID em todas as tabelas. |
| 7 | Auditoria | Completa em operacionais, só `criado_em` em catálogos. |
| 8 | Soft delete | Em escala, alerta, ficha_manutencao. Demais usam flag (`ativo`, `ativa`, `status`). |
| 9 | Motorista | Tabela separada de usuario. FK `usuario.motorista_id` opcional. |
| 10 | Linhas | Catálogo fechado. Cadastro pelo admin. |
| 11 | Sincronização | Híbrido offline-first (IndexedDB ↔ API). |

## 6. Status das fases

| # | Fase | Status |
|---|---|---|
| 1 | Especificação banco | ✅ |
| 2 | DDL (migrations + seeds) | ✅ |
| 3 | Dicionário de dados | ✅ |
| 4.1 | Backend Setup + /health | ✅ |
| 4.2 | Modelos + Schemas | ✅ |
| 4.3 | Autenticação JWT | ✅ |
| 4.4 | CRUD de catálogos | ✅ |
| 4.5 | Endpoints operacionais | ✅ |
| 4.6 | Importação Excel | ✅ |
| 4.7 | Refino + docs | ✅ |
| **5** | **Frontend V3** | ⏭️ **PRÓXIMA** |
| 6 | Sincronização offline-first | depois |
| 7 | Deploy Railway + Pages | depois |

## 7. Bugs conhecidos da V2 que a V3 já resolve estruturalmente

- Encoding UTF-8 importação Excel → openpyxl no backend
- Validação cruzada E2/AR2 → trigger no banco
- Manutenção sumindo da impressão (bug 1 da V2) → fila unificada com tipo
- Chip fantasma (bug 2 da V2) → UNIQUE parcial em alocação ativa
- Necessidade de "limpar" entre dias → coluna `data` em escala (histórico coexiste)

## 8. Lições aprendidas (não repetir erros)

- **NUNCA usar Copy-Item do Windows** para copiar JS — adiciona null bytes que causam SyntaxError. Usar Python/bash.
- **NUNCA fazer git checkout** para trocar de branch durante deploy — sobrescreve arquivos. Usar `git push origin HEAD:main --force` direto.
- **CSS Grid não funciona em @media print** se o elemento estava `display:none`. Usar `<table>` com inline styles.
- **Sempre verificar com `node --check`** antes de commit em arquivos JS.
- **Set-ExecutionPolicy** precisa rodar uma vez no PowerShell pra ativar venv: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

## 9. Credenciais (LOCAIS — apenas dev)

- **Admin do sistema:** RE=`ADMIN001`, senha=`57402232`
- **PostgreSQL:** usuário `postgres`, senha definida pelo Alisson localmente (não está nesse arquivo por segurança)
- **SECRET_KEY do JWT:** gerada com `python -c "import secrets; print(secrets.token_urlsafe(64))"` e salva no `.env` do backend
- **No novo PC:** essas credenciais precisam ser regeradas (instruções em `SETUP-NOVO-PC.md`)

## 10. Próxima fase (5) — Frontend V3

### Local previsto
`frontend-v3/` (pasta nova, sem tocar `frontend/` ou `v2/`)

### Decisões pendentes (a tomar com o Alisson)

1. **Nome da pasta:** `frontend-v3/` (sugestão)
2. **Stack:** Vanilla JS + Web Components (recomendado, mantém continuidade) ou Vue 3 sem build / React+Vite
3. **Hospedagem dev:** servidor local
4. **Hospedagem prod:** GitHub Pages (subpath) ou Cloudflare Pages

### Escopo planejado

1. Tela de login (consome `POST /auth/login`)
2. API client com fetch + token JWT no localStorage
3. Tela do pátio (consome `GET /patio`)
4. Tela de remanejamento (`GET /patio/remanejamento`)
5. Modais de alocação, alerta, manutenção
6. IndexedDB pra cache offline
7. Service Worker pra PWA
8. Manifest pra "Adicionar à tela inicial" no celular

### Recomendação inicial

Manter **vanilla JS + Web Components** porque:
- Alisson já domina vanilla (vez da V2)
- Sem build step, deploy igual ao GitHub Pages
- Web Components dão componentização sem framework
- Mais fácil de manter sozinho

## 11. Comandos críticos

### Subir backend (todo dia)
```powershell
cd "C:\Users\<USUARIO>\OneDrive\Documentos\Projetos_dev\Gestao-Patio-Sambaiba\backend"
.\.venv\Scripts\Activate.ps1
fastapi dev app/main.py
```

### Acessar Swagger
http://127.0.0.1:8000/docs

### Trocar senha de qualquer usuário
```powershell
python scripts/set_password.py <RE> <nova_senha>
```

### Resetar banco (se quiser começar do zero)
1. No pgAdmin, drop database `gestao_patio_sambaiba`
2. Create database `gestao_patio_sambaiba`
3. Conectar nele e rodar `database/migrations/002` até `006` em sequência
4. Rodar `database/seeds/01` até `06` em sequência
5. `python backend/scripts/set_password.py ADMIN001 nova_senha`

## 12. Documentos relacionados (no projeto)

- `database/01-especificacao-banco-v3.md` — especificação completa do banco
- `database/02-der-logico-v3.html` — DER em HTML A3 paisagem
- `database/03-dicionario-de-dados.xlsx` — 18 abas com toda a documentação
- `backend/README.md` — documentação do backend
- `backend/requests.http` — coleção REST Client pra testar API no VS Code
- `docs/STATUS-V3.md` — snapshot de progresso
- `docs/Requisitos_Gestao_Patio_v1.docx` — requisitos da V1

---

**Fim do contexto. Próximo arquivo: `SETUP-NOVO-PC.md`.**
