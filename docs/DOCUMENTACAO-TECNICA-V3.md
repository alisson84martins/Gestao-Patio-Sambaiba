# Sistema de Gestão de Pátio Sambaíba — Documentação Técnica V3

**Versão:** 3.0.0-alpha  
**Atualizado em:** 2026-06-22  
**Autor:** Alisson Martins — Coordenador de Tráfego, Garagem 3  
**Acesso público:** https://www.gestaopatiosambaiba.com.br

---

## 1. Visão Geral

O **Sistema de Gestão de Pátio Sambaíba** é uma aplicação web full-stack criada para digitalizar e centralizar o controle operacional do pátio de ônibus da Garagem 3 da Sambaíba Transportes Urbanos.

Antes do sistema, o controle de filas era feito manualmente — anotações em papel, comunicação verbal entre operadores. Com o sistema, qualquer operador autorizado vê o estado do pátio em tempo real de qualquer dispositivo com acesso à internet.

**Problemas resolvidos:**
- Visibilidade do pátio em tempo real durante a soltura da frota
- Importação automática da planilha de escala (Excel)
- Registro de alertas operacionais (PRESO, AMOSTRAL)
- Controle de manutenção por ficha de defeito
- Sync multi-dispositivo sem conflito entre operadores

---

## 2. Infraestrutura de Produção

```
Usuário (navegador)
       │
       ▼
  Nginx (HTTPS)
  gestaopatiosambaiba.com.br
       │
       ├──── /  (frontend estático)
       │     └─ /root/Gestao-Patio-Sambaiba/frontend-v3/
       │
       └──── api.gestaopatiosambaiba.com.br
             └─ proxy → FastAPI (uvicorn :8000)
                        └─ PostgreSQL (local)
```

| Componente | Detalhe |
|---|---|
| Servidor | Hetzner VPS (IP omitido por segurança) |
| SO | Ubuntu 22.04 |
| Processo backend | systemd `gestao-patio` |
| Domínio frontend | `www.gestaopatiosambaiba.com.br` |
| Domínio API | `api.gestaopatiosambaiba.com.br` |
| Branch ativo | `v3.0-dev` |
| Deploy | `git pull` no servidor + `systemctl restart gestao-patio` (se backend mudou) |

---

## 3. Arquitetura Geral

O sistema é dividido em três camadas independentes que se comunicam por HTTP/JSON:

```
┌────────────────────────────────────────────────┐
│               FRONTEND V3                       │
│  Vanilla JS + ES Modules — sem build/bundler    │
│  7 páginas HTML + 12 módulos JS                 │
│  Polling a cada 5s (GET /patio)                 │
└────────────────────┬───────────────────────────┘
                     │ HTTPS + JWT Bearer
                     ▼
┌────────────────────────────────────────────────┐
│                BACKEND                          │
│  Python + FastAPI + SQLAlchemy 2                │
│  14 routers REST — porta 8000                   │
│  JWT HS256 — 24h de validade                    │
└────────────────────┬───────────────────────────┘
                     │ SQLAlchemy ORM
                     ▼
┌────────────────────────────────────────────────┐
│              BANCO DE DADOS                     │
│  PostgreSQL 15+                                 │
│  Banco: gestao_frota_sambaiba                   │
│  Tabelas: 13 principais + seeds pré-carregados  │
└────────────────────────────────────────────────┘
```

---

## 4. Frontend V3

### 4.1 Páginas HTML

| Arquivo | Rota | Descrição |
|---|---|---|
| `index.html` | `/` | Tela de login — autenticação por RE + senha |
| `patio.html` | `/patio.html` | Visualização do pátio em tempo real (página principal) |
| `alertas.html` | `/alertas.html` | Registro e gestão de alertas PRESO e AMOSTRAL |
| `importacao.html` | `/importacao.html` | Upload de planilha Excel para importar escala |
| `manutencao.html` | `/manutencao.html` | Abertura e consulta de fichas de manutenção |
| `remanejamento.html` | `/remanejamento.html` | Ônibus em manutenção com escala no dia |
| `cadastros.html` | `/cadastros.html` | Cadastros de apoio (acesso admin) |

### 4.2 Módulos JavaScript

Todos os módulos usam ES Modules (`type="module"`). Nenhum framework externo — JavaScript puro.

#### `config.js` — Configuração Global
Define a URL da API e constantes compartilhadas. Detecta automaticamente o ambiente:
- **Localhost** → aponta para `http://127.0.0.1:8000` (FastAPI local)
- **Produção** → aponta para `https://api.gestaopatiosambaiba.com.br`

Exporta: `API_BASE_URL`, `IS_LOCAL`, `APP_VERSION`, `POLLING_INTERVAL_MS` (5000ms), `TOKEN_KEY`, `USER_KEY`.

#### `api.js` — Cliente HTTP Centralizado
Wrapper sobre o `fetch()` nativo. **Todos os módulos usam `api.js` — nunca `fetch()` direto.**

Responsabilidades:
- Injeta `Authorization: Bearer <token>` automaticamente em toda requisição
- Trata `401` (token expirado) limpando sessão e redirecionando para o login
- Padroniza erros (`ApiError` com `status` e `message`)
- Exporta: `apiGet`, `apiPost`, `apiPut`, `apiPatch`, `apiDelete`, `checkApiHealth`

#### `auth.js` — Autenticação no Frontend
Gerencia a sessão do usuário no `localStorage`.

Funções principais:
- `login(re, senha)` — chama `POST /auth/login`, salva token e dados do usuário
- `logout()` — limpa localStorage e redireciona para login
- `requireAuth()` — redireciona para login se não houver sessão; usado no topo de cada página protegida
- `getCurrentUser()` — retorna o objeto do usuário logado (perfil, RE, nome)

#### `patio.page.js` — Tela Principal do Pátio
Módulo mais complexo do frontend. Controla a visualização das filas.

Responsabilidades:
1. Verifica autenticação (redireciona se não logado)
2. Busca `GET /patio` a cada 5 segundos e renderiza as filas
3. Atualiza barra de estatísticas: **Frota total / Alocados / Em manutenção / Presos**
4. Exibe indicador de polling (bolinha verde = ok, vermelha = erro de rede)
5. Renderiza chips de ônibus com cor conforme status e alertas
6. Controla busca de carro (disponível para todos os perfis)
7. Inicializa os módulos `alocacao.bloco.js`, `mover.chip.modal.js` e `menu.js`

**Design resiliente:** não hardcoda quantidade de filas — itera sobre o payload. Se o backend devolver novas filas (Noturno, Reservados etc.), a UI se ajusta sozinha.

#### `alocacao.bloco.js` — Modo Bloco (Marcação em Massa)
Implementa o modo de alocação em bloco, espelhando a operação física do alocador no pátio.

- **Ida (↑):** `push` — adiciona ônibus no final da fila
- **Volta (↓):** `unshift` + recalcula posições — insere no início da fila

#### `mover.chip.modal.js` — Modal de Mover Ônibus
Abre modal quando o operador clica em um chip (ônibus). Permite mover o ônibus para outra fila via `PATCH /alocacoes/{id}`.

#### `menu.js` — Menu de Contexto da Fila
Controla o menu de três pontos (⋮) por fila. Ações disponíveis:
- Ver Escala da fila
- Imprimir fila
- Exportar para Excel
- Importar escala (restrito a ADMIN/COORDENADOR)
- Limpar fila

#### `importacao.js` — Importação de Escala
Interface para upload de arquivo `.xlsx` da escala. Envia o arquivo para `POST /importacao/excel` e exibe relatório de resultado (sucessos, erros, substituições, presos criados).

#### `alertas.js` — Gestão de Alertas
Tela para registrar e resolver alertas do tipo:
- **PRESO:** ônibus retido na rua (não retornou ao pátio)
- **AMOSTRAL:** ônibus separado para fiscalização da SPTRANS

Polling automático para exibir alertas ativos em tempo real.

#### `login.page.js` — Tela de Login
Controla o formulário de login. Chama `auth.js`, exibe mensagens de erro e verifica saúde da API antes do operador tentar logar.

#### `manutencao.js`, `remanejamento.js`, `cadastros.js`
Módulos das telas de suporte. Cada um segue o mesmo padrão: `requireAuth()` → fetch da API → renderização.

---

## 5. Backend

### 5.1 Ponto de Entrada — `main.py`

Cria a instância `FastAPI`, registra todos os 14 routers, configura CORS e inicializa logging. O startup/shutdown é gerenciado por `lifespan` (padrão moderno do FastAPI).

**CORS permitido para:** `gestaopatiosambaiba.com.br`, `www.gestaopatiosambaiba.com.br`, `localhost:5500`.

### 5.2 Core

| Arquivo | Função |
|---|---|
| `config.py` | Lê variáveis de ambiente via Pydantic Settings (`.env`). Valida DATABASE_URL, SECRET_KEY, JWT config, CORS, log level |
| `database.py` | Cria engine SQLAlchemy e sessão de banco. Exporta `get_db` (dependency do FastAPI) e `Base` (classe base dos models) |
| `deps.py` | Dependências reutilizáveis do FastAPI. Principal: `CurrentUser` — extrai e valida o JWT de cada requisição e retorna o usuário logado |
| `security.py` | Funções de criptografia: `hash_password` (bcrypt 12 rounds), `verify_password`, `create_access_token`, `decode_access_token` (JWT HS256) |
| `utils.py` | `PaginationParams` (skip/limit), `set_create_audit`, `set_update_audit` — preenchem campos de auditoria automaticamente |
| `exception_handlers.py` | Handler global de erros. Retorna `{"erro": "...", "status_code": N}` em vez do `{"detail": "..."}` padrão do FastAPI |

### 5.3 Models (SQLAlchemy 2)

#### `enums.py` — Tipos Enumerados
Espelha os enums do PostgreSQL em Python:

| Enum | Valores |
|---|---|
| `SetorEnum` | `E2`, `AR2` |
| `StatusOnibusEnum` | `ATIVO`, `MANUTENCAO`, `INATIVO`, `RESERVA` |
| `StatusMotoristaEnum` | `ATIVO`, `AFASTADO`, `FERIAS`, `DESLIGADO` |
| `PerfilUsuarioEnum` | `ADMIN`, `COORDENADOR`, `OPERADOR_PATIO`, `MOTORISTA`, `MECANICO` |
| `TipoFilaEnum` | `NUMERICA`, `ESPECIAL`, `ESPECIAL_REMOTA`, `MANUTENCAO` |
| `TipoAlertaEnum` | `PRESO`, `AMOSTRAL` |
| `StatusFichaEnum` | `ABERTA`, `EM_ANDAMENTO`, `CONCLUIDA`, `CANCELADA` |
| `TipoEscalaEnum` | `MANOBRA`, `PLANTAO_E2`, `PLANTAO_AR2` |
| `OrigemEscalaEnum` | `IMPORTACAO_EXCEL`, `MANUAL` |
| `StatusImportacaoEnum` | `SUCESSO`, `ERRO`, `PARCIAL` |

#### `frota.py` — Modelos de Frota e Pátio

**`Onibus`** — Cadastro de veículos da frota.
- `numero_frota`: número único do ônibus (ex: 1234)
- `setor`: coluna **gerada automaticamente pelo banco** com base no número de frota:
  - 1000–1999 → `E2` (linha centro)
  - 2000–2999 → `AR2` (linha bairro)
- `status`: ATIVO / MANUTENCAO / INATIVO / RESERVA
- `placa`, `codigo_externo`: campos opcionais de integração

**`Fila`** — Posições do pátio.
- `tipo`: NUMERICA (filas 1–33), ESPECIAL (Coqueiro, Laje, Lavador, Bomba, Elétricos, Fundão), ESPECIAL_REMOTA, MANUTENCAO
- `numero`: número da fila (somente para tipo NUMERICA)
- `nome`: nome exibido na tela
- `ordem_exibicao`: controla a ordem de renderização no grid

**`AlocacaoPatio`** — Registro de onde cada ônibus está.
- Liga `onibus_id` → `fila_id` com `posicao` (ordem dentro da fila)
- `ativa`: `True` = alocação vigente; `False` = histórico
- `data_referencia`: data de serviço calculada pelo backend (após 20h = amanhã)
- `alocado_por`: quem fez a alocação (auditoria)

#### `pessoas.py` — Modelos de Pessoas

**`Motorista`** — Cadastro independente do usuário do sistema.
- `re`: registro do empregado (login do motorista na escala)
- `nome`, `cpf`, `status`, `codigo_externo`

**`Usuario`** — Usuários do sistema (login web).
- `re` + `senha_hash`: credenciais de login
- `perfil`: um dos 5 perfis de acesso
- `primeiro_acesso`: `True` enquanto não trocou a senha inicial (gerada com os 4 últimos dígitos do CPF)
- `motorista_id`: vínculo opcional com o cadastro de motorista

#### `operacoes.py` — Modelos Operacionais

**`Escala`** — Escala diária de ônibus.
- Liga `onibus_id` + `linha_id` + `motorista_id` + `data` + `horario_saida`
- `tipo`: MANOBRA, PLANTAO_E2 ou PLANTAO_AR2
- `origem`: IMPORTACAO_EXCEL ou MANUAL
- `importacao_id`: rastreabilidade (qual importação gerou este registro)

**`Alerta`** — Alertas operacionais.
- `tipo`: PRESO (retido na rua) ou AMOSTRAL (fiscalização SPTRANS)
- `resolvido` + `resolvido_em` + `resolvido_por`: ciclo de vida do alerta
- **Sem campo `data`** — alertas são vinculados ao ônibus, não a uma data específica

**`FichaManutencao`** — Fichas de serviço.
- Liga ônibus + motorista que reportou + mecânico responsável + tipo de defeito
- `status`: ABERTA → EM_ANDAMENTO → CONCLUIDA / CANCELADA
- `aberta_em`, `concluida_em`: rastreabilidade temporal

**`ImportacaoEscala`** — Log de cada importação de Excel.
- `arquivo_nome`, `arquivo_hash` (SHA-256), `data_escala`
- `total_registros`, `registros_sucesso`, `registros_erro`, `status`
- `erro_detalhe`: resumo dos primeiros 10 erros encontrados

#### `catalogos.py` — Catálogos de Apoio
- `Linha`: catálogo de linhas (código + nome + setor E2/AR2)
- `TipoDefeito`: categorias de defeito para fichas de manutenção
- `Garagem`: cadastro de garagens (preparação para expansão multi-garagem)

#### `mixins.py` — Comportamentos Reutilizáveis
- `AuditoriaMixin`: adiciona `criado_em`, `criado_por`, `atualizado_em`, `atualizado_por` a qualquer model
- `SoftDeleteMixin`: adiciona `deletado_em` — registros não são apagados, apenas marcados
- `SyncMixin`: adiciona `sync_id` — suporte a sincronização

### 5.4 Routers (Endpoints REST)

| Router | Prefixo | Acesso | Função |
|---|---|---|---|
| `health.py` | `/health` | Público | Verifica se a API está no ar |
| `auth.py` | `/auth` | Público/Autenticado | Login JWT, dados do usuário, troca de senha. Rate limiting: 10 tentativas/IP/60s |
| `onibus.py` | `/onibus` | Autenticado | CRUD da frota. Setor calculado automaticamente pelo prefixo |
| `motoristas.py` | `/motoristas` | Autenticado | CRUD de motoristas |
| `linhas.py` | `/linhas` | Autenticado | Catálogo de linhas E2 e AR2 |
| `tipos_defeito.py` | `/tipos-defeito` | Autenticado | Categorias de defeito |
| `filas.py` | `/filas` | Autenticado | Listagem e configuração das filas do pátio |
| `usuarios.py` | `/usuarios` | Apenas ADMIN | CRUD de usuários. Proteção: admin não pode alterar o próprio perfil/status |
| `alocacoes.py` | `/alocacoes` | Autenticado | Mover ônibus entre filas. Inclui `get_data_servico()` (lógica de data após 20h) |
| `escalas.py` | `/escalas` | Autenticado | Escala manual + limpeza por data (`DELETE /escalas`) |
| `alertas.py` | `/alertas` | Autenticado | Criar, listar, resolver alertas PRESO e AMOSTRAL |
| `manutencao.py` | `/manutencao` | Autenticado | Fichas de manutenção |
| `patio.py` | `/patio` | Autenticado | **Query master do pátio** — retorna todas as filas com ônibus, escala, alertas e fichas |
| `importacao.py` | `/importacao` | ADMIN/COORDENADOR | Upload de planilha Excel |

#### Endpoint Principal — `GET /patio`
Este é o endpoint mais importante do sistema. Retorna em uma única query tudo que a tela do pátio precisa:

- Todas as filas ativas, ordenadas por tipo e posição
- Para cada fila: lista de ônibus alocados
- Para cada ônibus: setor, status, posição na fila, linha da escala, horário de saída, alertas ativos, fichas abertas

A query usa múltiplos `LEFT JOIN` para agregar todos os dados em uma passagem. Deduplicação de ônibus com múltiplas escalas (prioriza linha real sobre placeholder `MAN-*`).

#### Lógica de Data de Serviço (`get_data_servico`)
Regra operacional crítica: após as 20h00, o sistema considera a data de amanhã como referência. Isso reflete a operação real — ônibus preparados à noite já pertencem ao serviço do dia seguinte.

### 5.5 Service — `importacao_excel.py`

O serviço de importação é a peça mais sofisticada do backend. Suporta dois formatos de planilha:

**Formato Sambaíba (padrão real das planilhas da empresa):**
- Múltiplas abas: E2, AR2, MANOBRA
- Abas E2/AR2: 3 grupos de colunas por linha `(carro|hora|linha)` nos índices `(0,1,2)`, `(6,7,8)`, `(12,13,14)` — 3 linhas de cabeçalho
- Aba MANOBRA: 6 grupos `(carro|hora)` — 1 linha de cabeçalho
- Reconhece marcadores especiais: `PRESO` (cria alerta), `AMOSTRAL`, `EVENTO`, `TABELAS` (ignora)

**Formato simples (alternativo):**
- Uma aba, uma linha por ônibus: `carro | linha | horário | RE motorista | tipo`

**Auto-detecção:** identifica o formato pelo nome/conteúdo das abas. Sem configuração manual.

**Fluxo de importação:**
1. Parse da planilha → lista de `LinhaParseada`
2. Separa linhas com marcador `PRESO` das escalas normais
3. Deduplicação: um ônibus deve ter no máximo uma escala por dia (linha real vence manobra)
4. Soft-delete das escalas anteriores da mesma data (se `substituir_existentes=True`)
5. Resolve todos os alertas PRESO ativos (serão recriados para os que permanecem presos)
6. Cria registro de `ImportacaoEscala` para rastreabilidade
7. Para cada linha válida: auto-cria ônibus e linha se não existirem no banco
8. Valida compatibilidade de setor (ônibus E2 não pode ter linha AR2)
9. Insere escalas em lote
10. Cria alertas PRESO para ônibus marcados na planilha
11. Atualiza status da importação (SUCESSO / PARCIAL / ERRO)

---

## 6. Banco de Dados

**Banco:** `gestao_frota_sambaiba` no PostgreSQL 15+

### 6.1 Tabelas Principais

| Tabela | Modelo | Descrição |
|---|---|---|
| `onibus` | Onibus | Frota de veículos. Setor (E2/AR2) gerado pelo banco |
| `fila` | Fila | Posições do pátio (numéricas + especiais + manutenção) |
| `alocacao_patio` | AlocacaoPatio | Estado atual do pátio — onde cada ônibus está |
| `escala` | Escala | Escala diária (ônibus × linha × horário) |
| `alerta` | Alerta | Alertas PRESO e AMOSTRAL |
| `ficha_manutencao` | FichaManutencao | Fichas de serviço de manutenção |
| `importacao_escala` | ImportacaoEscala | Log de importações de planilha |
| `motorista` | Motorista | Cadastro de motoristas |
| `usuario` | Usuario | Usuários do sistema web |
| `linha` | Linha | Catálogo de linhas E2 e AR2 |
| `tipo_defeito` | TipoDefeito | Categorias de defeito |
| `garagem` | Garagem | Garagens (preparação multi-garagem) |
| `patio_v2_estado` | — | Blob JSONB para sync multi-usuário (legado V2) |

### 6.2 Regra de Negócio Central — Setor E2 / AR2

```sql
-- Coluna gerada automaticamente no banco
setor GENERATED ALWAYS AS (
  CASE
    WHEN numero_frota BETWEEN 1000 AND 1999 THEN 'E2'::setor_enum
    WHEN numero_frota BETWEEN 2000 AND 2999 THEN 'AR2'::setor_enum
  END
) STORED
```

- **E2** = carros de centro, plantão E2
- **AR2** = carros de bairro, plantão AR2
- Ônibus E2 não pode rodar linha AR2 e vice-versa
- O banco **rejeita automaticamente** inserções inválidas (regra implementada em trigger e no serviço Python)

### 6.3 Posições do Pátio (Seeds)

**Filas numéricas:** 1 a 33 (tipo `NUMERICA`)

**Posições especiais** (tipo `ESPECIAL`):
- Coqueiro, Laje, Lavador, Bomba, Elétricos, Fundão

**Posições de manutenção** (tipo `MANUTENCAO`): pré-cadastradas por tipo de serviço

Total de posições: ~40 entre numéricas, especiais e manutenção.

### 6.4 Migrations

Scripts SQL em `database/migrations/`, aplicados em ordem:

| Arquivo | Conteúdo |
|---|---|
| `001-create-database.sql` | Criação do banco |
| `002-extensions-enums.sql` | Extensões PostgreSQL e tipos enumerados |
| `003-tables-core.sql` | Tabelas principais (onibus, fila, alocacao_patio) |
| `004-tables-apoio.sql` | Tabelas operacionais (escala, alerta, manutencao) |
| `005-constraints-indexes.sql` | Índices e constraints de integridade |
| `006-functions-triggers.sql` | Funções e triggers (auditoria, validação de setor) |
| `007-seeds.sql` | Dados iniciais (filas, posições, linhas de exemplo) |
| `008-data-referencia-primeiro-acesso.sql` | Configuração de primeiro acesso de usuário |
| `009-cpf-usuario.sql` | Campo CPF no usuário (senha inicial) |
| `010-patio-v2-estado.sql` | Tabela de estado V2 (blob JSONB para sync) |

---

## 7. Perfis de Usuário e Permissões

| Perfil | Pode ver pátio | Pode alocar | Pode importar | Pode criar alertas | Pode gerenciar usuários |
|---|---|---|---|---|---|
| ADMIN | ✅ | ✅ | ✅ | ✅ | ✅ |
| COORDENADOR | ✅ | ✅ | ✅ | ✅ | ❌ |
| OPERADOR_PATIO | ✅ | ✅ | ❌ | ✅ | ❌ |
| MOTORISTA | ✅ (somente leitura + busca) | ❌ | ❌ | ❌ | ❌ |
| MECANICO | ✅ | ❌ | ❌ | ❌ | ❌ |

**Senha inicial:** últimos 4 dígitos do CPF. O sistema exige troca no primeiro acesso via `POST /auth/trocar-senha`.

---

## 8. Fluxos Operacionais Principais

### 8.1 Login
1. Operador acessa `www.gestaopatiosambaiba.com.br`
2. Informa RE + senha
3. Frontend chama `POST /auth/login`
4. Backend valida credenciais com bcrypt, gera JWT (24h)
5. Token salvo no `localStorage` do navegador
6. Redirecionamento automático para `patio.html`

### 8.2 Visualização do Pátio
1. `patio.page.js` chama `GET /patio` a cada 5 segundos
2. Backend executa query master (múltiplos LEFT JOINs)
3. Frontend renderiza chips de ônibus nas filas correspondentes
4. Barra de stats atualizada: Frota total / Alocados / Manutenção / Presos
5. Chip vermelho = PRESO · Chip amarelo = AMOSTRAL · Chip laranja = EM MANUTENÇÃO

### 8.3 Importação de Escala
1. Coordenador acessa `importacao.html`
2. Seleciona arquivo `.xlsx` (escala do dia)
3. Frontend envia `POST /importacao/excel` com o arquivo
4. Backend auto-detecta formato (Sambaíba ou simples)
5. Parser processa abas E2, AR2 e MANOBRA
6. Escalas anteriores da mesma data recebem soft-delete
7. Alertas PRESO ativos são resolvidos e recriados conforme nova escala
8. Resultado exibido: N importados com sucesso, K erros, M substituídos, P presos criados
9. Tela do pátio já reflete a nova escala no próximo ciclo de polling (5s)

### 8.4 Registro de Alerta PRESO
1. Operador acessa `alertas.html`
2. Seleciona o ônibus e informa motivo
3. Frontend chama `POST /alertas`
4. Chip do ônibus fica vermelho na tela do pátio para todos os dispositivos
5. Alerta resolvido quando o ônibus retorna: `PATCH /alertas/{id}`

### 8.5 Alocação Manual de Ônibus
1. Operador clica em um chip vazio na fila destino
2. Modal abre para informar o número do ônibus (com autocomplete)
3. Frontend chama `POST /alocacoes`
4. Backend registra alocação com data de referência calculada
5. Pátio de todos os outros dispositivos reflete a mudança em até 5 segundos

---

## 9. Segurança

- **Autenticação:** JWT HS256, 24h de validade, gerado com `python-jose` + `bcrypt` (12 rounds)
- **Rate limiting:** login bloqueado após 10 tentativas por IP por 60 segundos (in-memory)
- **Proteção de admin:** admin não pode alterar o próprio perfil ou desativar a própria conta
- **CORS:** apenas domínios autorizados podem chamar a API
- **Permissões por perfil:** verificadas em cada endpoint pelo `CurrentUser` dependency
- **Soft delete:** dados jamais são apagados definitivamente — apenas marcados com `deletado_em`
- **Auditoria:** todo registro registra quem criou e quem editou (`criado_por`, `atualizado_por`)

---

## 10. Pendências e Próximos Passos

| Item | Status | Prioridade |
|---|---|---|
| Tela de cadastro de usuários (`usuarios.html`) | Pendente | Alta |
| Teste de impressão em celular/impressora real | Pendente | Alta |
| Cadastro de operadores para equipe de campo | Pendente | Alta |
| Tela de remanejamento — teste em campo | Pendente | Média |
| Tela de manutenção — teste em campo | Pendente | Média |
| Integração com API Nimer (sistema interno) | Planejado | Futura |
| Expansão para outras garagens | Planejado | Futura |
| App mobile nativo | Planejado | Futura |

---

## 11. Estrutura de Arquivos

```
Gestao-Patio-Sambaiba/
├── frontend-v3/                    ← Frontend em produção
│   ├── index.html                  ← Login
│   ├── patio.html                  ← Tela principal
│   ├── alertas.html                ← Alertas
│   ├── importacao.html             ← Importação Excel
│   ├── manutencao.html             ← Manutenção
│   ├── remanejamento.html          ← Remanejamento
│   ├── cadastros.html              ← Cadastros (admin)
│   └── assets/
│       ├── css/style.css           ← Estilos (CSS Variables, sem framework)
│       ├── img/                    ← Logos e imagens
│       └── js/
│           ├── config.js           ← URLs e constantes globais
│           ├── api.js              ← Cliente HTTP centralizado
│           ├── auth.js             ← Sessão e autenticação
│           ├── patio.page.js       ← Tela do pátio (polling + renderização)
│           ├── alocacao.bloco.js   ← Modo bloco (Ida/Volta)
│           ├── mover.chip.modal.js ← Modal de mover ônibus
│           ├── menu.js             ← Menu de contexto por fila
│           ├── importacao.js       ← Upload e resultado de escala
│           ├── alertas.js          ← Gestão de alertas
│           ├── remanejamento.js    ← Tela de remanejamento
│           ├── manutencao.js       ← Fichas de manutenção
│           ├── login.page.js       ← Formulário de login
│           └── cadastros.js        ← Cadastros admin
│
├── backend/                        ← API FastAPI
│   └── app/
│       ├── main.py                 ← Entrada da aplicação
│       ├── core/
│       │   ├── config.py           ← Configurações (.env)
│       │   ├── database.py         ← Engine SQLAlchemy
│       │   ├── deps.py             ← Dependências (CurrentUser)
│       │   ├── security.py         ← JWT + bcrypt
│       │   ├── utils.py            ← Paginação e auditoria
│       │   └── exception_handlers.py
│       ├── models/
│       │   ├── enums.py            ← Tipos enumerados
│       │   ├── frota.py            ← Onibus, Fila, AlocacaoPatio
│       │   ├── pessoas.py          ← Motorista, Usuario
│       │   ├── operacoes.py        ← Escala, Alerta, FichaManutencao
│       │   ├── catalogos.py        ← Linha, TipoDefeito, Garagem
│       │   └── mixins.py           ← AuditoriaMixin, SoftDeleteMixin
│       ├── routers/                ← 14 arquivos de endpoints REST
│       ├── schemas/                ← Pydantic (validação entrada/saída)
│       └── services/
│           └── importacao_excel.py ← Parser de planilha Excel
│
├── database/
│   ├── migrations/                 ← Scripts SQL (001 a 010)
│   └── seeds/                      ← Dados iniciais (filas, linhas, admin)
│
└── docs/                           ← Documentação do projeto
```

---

*Documentação gerada com base no código fonte em produção — branch `v3.0-dev`, commit de 2026-06-22.*
