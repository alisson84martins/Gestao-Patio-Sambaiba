-- ============================================================================
-- MIGRATION 042 — Avaria: mapa clicável, deduplicação e prova (Fase 1)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria (existente — migration 024)
-- DATA:   2026-09-16
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (schema portaria, recurso acesso_veicular)
--             036-avaria-saida-frota.sql (tabela portaria.avaria_saida — esta
--             migration é a filha dela, evolui a mesma tabela em vez de
--             substituí-la)
--   Referência de VOCABULÁRIO (não é dependência de schema — nenhuma FK
--   cruza para lá): REGIOES_AVARIA em
--   frontend-v3/assets/js/ocorrencia.vocabulario.js, espelhando a migration
--   012 (coordenadoria.ocorrencia_avaria). avaria_zona.regiao_ocorrencia é
--   VARCHAR comparado por CONFERÊNCIA, não FK — módulos publicam fatos,
--   ⛔ não se enxertam uns nos outros (regra de fronteira).
-- ORIGEM: _handoff-claude/PROMPT-avaria-mapa-visual-2026-09-15.md, Fase 1
-- ----------------------------------------------------------------------------
-- POR QUÊ — e por que a retenção de 365 dias, a imutabilidade da
--   constatação e o ON DELETE RESTRICT NÃO SÃO CAPRICHO:
--
--   A entrevista de 15/09 mudou o enquadramento inteiro do módulo. A
--   migration 036 nasceu achando que a avaria servia para comparar "o carro
--   saiu assim ou não". Serve, mas é efeito colateral. A finalidade real:
--
--     O motorista sai da garagem e faz o checklist das avarias que já
--     existem no ônibus. Quando o carro é consertado, o conserto NÃO é
--     cobrado desse motorista, porque ficou registrado que ele pegou o
--     carro já avariado. Se não marcou, ou não viu, ou foi ele quem fez na
--     rua.
--
--   A marcação PROTEGE o motorista; a ausência dela o RESPONSABILIZA. Isto
--   não é log operacional — é documento usado em apuração de
--   responsabilidade sobre pessoa. Daí as três decisões que um leitor
--   apressado chamaria de exagero:
--
--   1. RETENÇÃO 365 DIAS (não 60, como a 036 fixou em expira_em): motorista chamado
--      em fevereiro por um amassado de setembro não pode ouvir "o sistema
--      apagou". Ver seção 6.
--   2. CONSTATAÇÃO IMUTÁVEL (portaria.avaria_constatacao, nova): sem
--      UPDATE, sem DELETE no backend. Correção só por anulação — a linha
--      continua lá, com quem anulou e por quê. Ver seção 3.
--   3. ON DELETE RESTRICT (nunca CASCADE) de avaria_constatacao e
--      avaria_contestacao para avaria_saida: apagar uma avaria levaria
--      junto a defesa de TODOS os motoristas que passaram por ela. RESTRICT
--      transforma a tentativa em erro de banco — que é o que se quer.
--      Ver seção 3 e a CONFERÊNCIA (o DELETE que tem que falhar).
--
--   O que viabiliza tudo isso é o MAPA CLICÁVEL (P3, frontend): enquanto a
--   avaria for parágrafo digitado em textarea livre, deduplicar é
--   impossível ("retrovisor rachado" × "retrov. trincado" × "espelho
--   quebrado" são três coisas diferentes para a máquina). Com zona + tipo
--   vindos de catálogo (seção 1), o sistema consegue perguntar "esse dano
--   já não está marcado?" — e a chave dessa pergunta é o índice único da
--   seção 5: prefixo + zona_codigo + tipo_codigo, enquanto status='ABERTA'.
--
-- 🔴 REGRA NÚMERO UM DO MÓDULO SEGUE VALENDO — o sistema nunca recusa o
--   registro de uma avaria. O índice único da seção 5 não é um 409 para o
--   controlador: é o que o backend (P2, fora desta migration) usa para
--   decidir "abre avaria nova" × "acrescenta constatação na que já existe".
--   Cada motorista está certo em marcar de novo — é a defesa dele.
--
-- 🟢 QUASE ADITIVA — 4 tabelas novas (avaria_zona, avaria_tipo,
--   avaria_constatacao, avaria_contestacao) e um ALTER TABLE que só
--   ACRESCENTA coluna em portaria.avaria_saida. Nenhum DROP, nenhum RENAME.
--   `descricao` de avaria_saida continua NOT NULL e não é tocada: tem dado
--   de produção e vira a observação livre da abertura.
--
-- RBAC — NENHUM RECURSO NOVO. Marcar, contestar e consultar seguem em
--   `acesso_veicular` (migration 024), como a 036 já fazia — menor
--   privilégio (padrão da migration 020). A única exigência nova de
--   permissão (`manutencao`, escrever=True, para /encerrar) é regra de
--   ROTA no backend (P2), não muda nada nesta migration nem cria recurso.
--
-- DADO PESSOAL: avaria_constatacao.motorista_re / motorista_nome — mesma
--   natureza de avaria_saida.motorista_re (036) e recolhida_anormal (026),
--   com peso maior aqui porque é o registro que decide de quem foi a culpa.
--   ⛔ Nunca cpf/rg/cnh nestas tabelas.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_patio_sambaiba -c "SET ROLE sambaiba;" \
--        -f 042-avaria-mapa-e-deduplicacao.sql
-- ============================================================================

SET search_path TO portaria, public;

-- ============================================================================
-- 1 · CATÁLOGOS — tabela, não ENUM
-- ============================================================================
-- 🔴 Tabela, não `CREATE TYPE ... AS ENUM`: ALTER TYPE ... ADD VALUE exige
-- COMMIT próprio e não roda no mesmo batch do pgAdmin (memória
-- postgres_alter_type_enum). Zona e tipo VÃO crescer — um INSERT novo não
-- pode custar migration com pegadinha.
CREATE TABLE IF NOT EXISTS portaria.avaria_zona (
    codigo            VARCHAR(30) PRIMARY KEY,
    nome              VARCHAR(60) NOT NULL,
    -- Onde desenhar no mapa (P3): FRENTE|TRASEIRA|LATERAL_ESQ|LATERAL_DIR|INTERNO|TETO
    vista             VARCHAR(20) NOT NULL,
    -- Vocabulário da COORDENADORIA (REGIOES_AVARIA / migration 012) — ⛔ NÃO
    -- é a mesma coisa que `vista`: RETROVISOR_ESQ desenha em vista=FRENTE
    -- mas regiao_ocorrencia=RETROVISOR. É o que torna a Fase 3 trivial.
    regiao_ocorrencia VARCHAR(20) NOT NULL,
    -- NULL = zona de qualquer carro. 'ARTICULADO' / 'ELETRICO' = só aparece
    -- no mapa de carro com essa característica. Nasce aqui para a Fase 4
    -- (migration 044) não custar migration nova; até lá fica tudo NULL nas
    -- zonas comuns e nada muda no mapa da Fase 1.
    requer_caracteristica VARCHAR(20),
    ordem             SMALLINT    NOT NULL,
    ativo             BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS portaria.avaria_tipo (
    codigo            VARCHAR(20) PRIMARY KEY,
    nome              VARCHAR(40) NOT NULL,
    -- Decide, na tela (P3), se confirmar é um toque ou exige segundo toque
    -- quando a avaria está aberta há mais de 30 dias.
    exige_conferencia BOOLEAN     NOT NULL DEFAULT FALSE,
    ordem             SMALLINT    NOT NULL,
    ativo             BOOLEAN     NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE portaria.avaria_zona IS 'Catálogo de partes do ônibus onde uma avaria pode ser marcada. Tabela (não ENUM) de propósito: cresce por INSERT, sem pegadinha de ALTER TYPE / COMMIT separado. Zonas com requer_caracteristica preenchido nascem aqui na 042 mas só aparecem no mapa a partir da Fase 4 (migration 044).';
COMMENT ON COLUMN portaria.avaria_zona.vista IS 'Onde desenhar no mapa SVG (P3/avaria.mapa.js). Coluna de PROPÓSITO diferente de regiao_ocorrencia — não fundir as duas.';
COMMENT ON COLUMN portaria.avaria_zona.regiao_ocorrencia IS 'Vocabulário da coordenadoria (REGIOES_AVARIA em frontend-v3/assets/js/ocorrencia.vocabulario.js, espelhando a migration 012 / coordenadoria.ocorrencia_avaria). Toda zona aponta para um desses dez valores — é o que permite a Fase 3 cruzar avaria×ocorrência sem tradutor. ⛔ Não inventar vocabulário novo: está gravado como texto em ocorrências reais e na impressão do formulário.';
COMMENT ON COLUMN portaria.avaria_zona.requer_caracteristica IS 'NULL = zona de qualquer carro. ARTICULADO/ELETRICO = só entra no catálogo devolvido para um carro com essa característica (Fase 4, migration 044 — public.modelo_onibus). Seed nasce aqui para a 044 não precisar migration nova.';
COMMENT ON TABLE portaria.avaria_tipo IS 'Catálogo de tipos de dano. Tabela (não ENUM) pelo mesmo motivo de avaria_zona.';
COMMENT ON COLUMN portaria.avaria_tipo.exige_conferencia IS 'TRUE para danos graves (amassado, quebrado, faltando, trincado). Na tela (P3), confirmar uma avaria com este tipo aberta há mais de 30 dias pede um segundo toque em vez de um só — ralado/pichado/etc. seguem com um toque, é volume grande e não vale atrito.';

-- ----------------------------------------------------------------------------
-- Seed das zonas — arranjo copiado da folha de vistoria da Garagem 3
-- (_handoff-claude/REFERENCIA-folha-avaria-garagem.jpg): laterais esquerda e
-- direita grandes, frente e traseira em miniatura, interno/teto como chips.
-- Números de `ordem` saltam de 10 em 10 — zona nova é INSERT, não migration.
--
-- 🟢 17 ZONAS DE VOCABULÁRIO DA GARAGEM — absorvidas em 16/09 do complemento
-- `_handoff-claude/042b-zonas-vocabulario-garagem.sql` (agora apagado; a 042
-- ainda não tinha ido para produção, então o seed final mora só aqui).
-- Origem: leitura das 84 avarias reais em produção com o Alisson — o
-- catálogo genérico (PARACHOQUE_DIANT, LAT_ESQ_CENTRO...) não é a língua da
-- garagem, que fala PONTEIRA, CURVÃO, SAIA, BRAÇO (do retrovisor) e LUZ DO
-- VIGIA. Decisão do Alisson: ⛔ NÃO renomear nem desativar as zonas
-- genéricas — o vocabulário da garagem entra AO LADO ("mantenha os que você
-- fez apenas inserindo esses modelos para facilitar, vamos aos poucos
-- mudando a forma como eles falam e inserem no sistema"). Se o controlador
-- não achar "ponteira" no mapa, ele não usa o mapa — volta a digitar em
-- texto livre e a deduplicação morre na origem.
-- `ordem` intercalada (11, 12, 61, 81…) de propósito: cada zona nova cai ao
-- lado da genérica equivalente no mapa, ⛔ sem reordenar o que já existia.
-- Ver `_handoff-claude/PROGRESSO-2026-09-16.md` para a leitura completa das
-- 84 descrições e os 3 casos de duplicidade que motivaram a frente inteira.
-- ----------------------------------------------------------------------------
INSERT INTO portaria.avaria_zona (codigo, nome, vista, regiao_ocorrencia, requer_caracteristica, ordem, ativo) VALUES
    -- FRENTE
    ('PARACHOQUE_DIANT', 'Para-choque dianteiro', 'FRENTE',       'FRENTE',            NULL, 10,  TRUE),
    -- vocabulário garagem (PONTEIRA = quina do para-choque; CURVÃO = quina acima, até o teto; VIGIA = luz delimitadora)
    ('PONTEIRA_DIANT_ESQ', 'Ponteira dianteira esquerda',     'FRENTE', 'FRENTE', NULL, 11, TRUE),
    ('PONTEIRA_DIANT_DIR', 'Ponteira dianteira direita',      'FRENTE', 'FRENTE', NULL, 12, TRUE),
    ('CURVAO_DIANT_ESQ',   'Curvão dianteiro esquerdo',       'FRENTE', 'FRENTE', NULL, 13, TRUE),
    ('CURVAO_DIANT_DIR',   'Curvão dianteiro direito',        'FRENTE', 'FRENTE', NULL, 14, TRUE),
    ('VIGIA_DIANT_ESQ',    'Luz do vigia dianteira esquerda', 'FRENTE', 'FRENTE', NULL, 15, TRUE),
    ('VIGIA_DIANT_DIR',    'Luz do vigia dianteira direita',  'FRENTE', 'FRENTE', NULL, 16, TRUE),
    ('GRADE_FRONTAL',    'Grade frontal',         'FRENTE',       'FRENTE',            NULL, 20,  TRUE),
    ('FAROL_ESQ',        'Farol esquerdo',        'FRENTE',       'FRENTE',            NULL, 30,  TRUE),
    ('FAROL_DIR',        'Farol direito',         'FRENTE',       'FRENTE',            NULL, 40,  TRUE),
    ('PARABRISA',        'Para-brisa',            'FRENTE',       'PARABRISA',         NULL, 50,  TRUE),
    ('RETROVISOR_ESQ',   'Retrovisor esquerdo',   'FRENTE',       'RETROVISOR',        NULL, 60,  TRUE),
    -- vocabulário garagem: braço do retrovisor quebra sozinho, conserto separado do espelho
    ('BRACO_RETROV_ESQ', 'Braço do retrovisor esquerdo', 'FRENTE', 'RETROVISOR', NULL, 61, TRUE),
    ('RETROVISOR_DIR',   'Retrovisor direito',    'FRENTE',       'RETROVISOR',        NULL, 70,  TRUE),
    ('BRACO_RETROV_DIR', 'Braço do retrovisor direito',  'FRENTE', 'RETROVISOR', NULL, 71, TRUE),
    -- TRASEIRA
    ('PARACHOQUE_TRAS',  'Para-choque traseiro',  'TRASEIRA',     'TRASEIRA',          NULL, 80,  TRUE),
    -- vocabulário garagem
    ('PONTEIRA_TRAS_ESQ', 'Ponteira traseira esquerda',     'TRASEIRA', 'TRASEIRA', NULL, 81, TRUE),
    ('PONTEIRA_TRAS_DIR', 'Ponteira traseira direita',      'TRASEIRA', 'TRASEIRA', NULL, 82, TRUE),
    ('CURVAO_TRAS_ESQ',   'Curvão traseiro esquerdo',       'TRASEIRA', 'TRASEIRA', NULL, 83, TRUE),
    ('CURVAO_TRAS_DIR',   'Curvão traseiro direito',        'TRASEIRA', 'TRASEIRA', NULL, 84, TRUE),
    ('VIGIA_TRAS_ESQ',    'Luz do vigia traseira esquerda', 'TRASEIRA', 'TRASEIRA', NULL, 85, TRUE),
    ('VIGIA_TRAS_DIR',    'Luz do vigia traseira direita',  'TRASEIRA', 'TRASEIRA', NULL, 86, TRUE),
    ('TAMPA_MOTOR',      'Tampa do motor',        'TRASEIRA',     'TRASEIRA',          NULL, 90,  TRUE),
    ('LANTERNA_ESQ',     'Lanterna esquerda',     'TRASEIRA',     'TRASEIRA',          NULL, 100, TRUE),
    ('LANTERNA_DIR',     'Lanterna direita',      'TRASEIRA',     'TRASEIRA',          NULL, 110, TRUE),
    ('VIDRO_TRAS',       'Vidro traseiro',        'TRASEIRA',     'TRASEIRA',          NULL, 120, TRUE),
    -- LATERAL ESQUERDA
    ('LAT_ESQ_DIANT',    'Lateral esquerda dianteira', 'LATERAL_ESQ', 'LATERAL_ESQUERDA', NULL, 130, TRUE),
    ('LAT_ESQ_CENTRO',   'Lateral esquerda central',   'LATERAL_ESQ', 'LATERAL_ESQUERDA', NULL, 140, TRUE),
    ('LAT_ESQ_TRAS',     'Lateral esquerda traseira',  'LATERAL_ESQ', 'LATERAL_ESQUERDA', NULL, 150, TRUE),
    ('JANELA_ESQ',       'Janela esquerda',            'LATERAL_ESQ', 'LATERAL_ESQUERDA', NULL, 160, TRUE),
    -- vocabulário garagem
    ('VIGIA_LAT_ESQ',    'Luz do vigia lateral esquerda', 'LATERAL_ESQ', 'LATERAL_ESQUERDA', NULL, 165, TRUE),
    -- LATERAL DIREITA (portas ficam deste lado)
    ('PORTA_DIANT',      'Porta dianteira',            'LATERAL_DIR', 'LATERAL_DIREITA',  NULL, 170, TRUE),
    ('PORTA_TRAS',       'Porta traseira',              'LATERAL_DIR', 'LATERAL_DIREITA',  NULL, 180, TRUE),
    -- vocabulário garagem: a "saia" é a lateral em 3 pedaços por lado — o
    -- esquerdo já tinha os 3 (LAT_ESQ_DIANT/CENTRO/TRAS); o direito só tinha
    -- CENTRO e TRAS no seed genérico, porque as portas ocupam a dianteira.
    ('LAT_DIR_DIANT',    'Lateral direita dianteira',   'LATERAL_DIR', 'LATERAL_DIREITA',  NULL, 185, TRUE),
    ('LAT_DIR_CENTRO',   'Lateral direita central',     'LATERAL_DIR', 'LATERAL_DIREITA',  NULL, 190, TRUE),
    ('LAT_DIR_TRAS',     'Lateral direita traseira',    'LATERAL_DIR', 'LATERAL_DIREITA',  NULL, 200, TRUE),
    ('JANELA_DIR',       'Janela direita',              'LATERAL_DIR', 'LATERAL_DIREITA',  NULL, 210, TRUE),
    -- vocabulário garagem
    ('VIGIA_LAT_DIR',    'Luz do vigia lateral direita', 'LATERAL_DIR', 'LATERAL_DIREITA', NULL, 215, TRUE),
    -- RODADO (vista segue o lado)
    ('RODA_ESQ_DIANT',   'Roda esquerda dianteira', 'LATERAL_ESQ', 'RODADO', NULL, 220, TRUE),
    ('RODA_ESQ_TRAS',    'Roda esquerda traseira',  'LATERAL_ESQ', 'RODADO', NULL, 230, TRUE),
    ('RODA_DIR_DIANT',   'Roda direita dianteira',  'LATERAL_DIR', 'RODADO', NULL, 240, TRUE),
    ('RODA_DIR_TRAS',    'Roda direita traseira',   'LATERAL_DIR', 'RODADO', NULL, 250, TRUE),
    -- TETO
    ('TETO',             'Teto', 'TETO', 'TETO', NULL, 260, TRUE),
    -- INTERNO (chips abaixo do desenho, P3)
    ('BANCO',            'Banco',            'INTERNO', 'INTERIOR', NULL, 270, TRUE),
    ('PISO',              'Piso',             'INTERNO', 'INTERIOR', NULL, 280, TRUE),
    ('CATRACA',           'Catraca',          'INTERNO', 'INTERIOR', NULL, 290, TRUE),
    ('VALIDADOR',         'Validador',        'INTERNO', 'INTERIOR', NULL, 300, TRUE),
    ('ELEVADOR_PCD',      'Elevador PCD',     'INTERNO', 'INTERIOR', NULL, 310, TRUE),
    ('PAINEL_MOTORISTA',  'Painel do motorista', 'INTERNO', 'INTERIOR', NULL, 320, TRUE),
    -- 🔴 Válvula de escape — regra número um: sem isto, dano sem zona no
    -- mapa (extintor, limpador de para-brisa, cinto de segurança, cheiro
    -- de queimado) trava o registro. Ativa (⛔ diferente de NAO_INFORMADA,
    -- que é só backfill) e sempre visível como chip, mesmo com vista=INTERNO.
    ('OUTRO',             'Outro (descreva)', 'INTERNO', 'OUTRO', NULL, 990, TRUE),
    -- Backfill only — nunca aparece no mapa (ativo=FALSE)
    ('NAO_INFORMADA',     'Não informada', 'INTERNO', 'OUTRO', NULL, 999, FALSE),
    -- Fase 4 (requer_caracteristica) — seed agora, mapa só a partir da 044
    ('SANFONA',           'Sanfona',                      'LATERAL_ESQ', 'OUTRO',            'ARTICULADO', 400, TRUE),
    ('SEC2_LAT_ESQ',      'Lateral esquerda — 2ª seção',  'LATERAL_ESQ', 'LATERAL_ESQUERDA', 'ARTICULADO', 410, TRUE),
    ('SEC2_LAT_DIR',      'Lateral direita — 2ª seção',   'LATERAL_DIR', 'LATERAL_DIREITA',  'ARTICULADO', 420, TRUE),
    ('RODA_3EIXO_ESQ',    'Roda 3º eixo — esquerda',      'LATERAL_ESQ', 'RODADO',           'ARTICULADO', 430, TRUE),
    ('RODA_3EIXO_DIR',    'Roda 3º eixo — direita',       'LATERAL_DIR', 'RODADO',           'ARTICULADO', 440, TRUE),
    ('PORTA_3',           'Porta 3',                      'LATERAL_DIR', 'LATERAL_DIREITA',  'ARTICULADO', 450, TRUE),
    ('TOMADA_RECARGA',    'Tomada de recarga',            'TRASEIRA',    'OUTRO',            'ELETRICO',   460, TRUE),
    ('BATERIA',           'Bateria',                      'TETO',        'TETO',             'ELETRICO',   470, TRUE)
ON CONFLICT (codigo) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Seed dos tipos
-- ----------------------------------------------------------------------------
INSERT INTO portaria.avaria_tipo (codigo, nome, exige_conferencia, ordem, ativo) VALUES
    ('RALADO',        'Ralado',         FALSE, 10,  TRUE),
    ('AMASSADO',       'Amassado',      TRUE,  20,  TRUE),
    ('QUEBRADO',       'Quebrado',      TRUE,  30,  TRUE),
    ('TRINCADO',       'Trincado',      TRUE,  40,  TRUE),
    ('FALTANDO',       'Faltando',      TRUE,  50,  TRUE),
    ('SOLTO',          'Solto',         FALSE, 60,  TRUE),
    ('FURADO',         'Furado',        FALSE, 70,  TRUE),
    ('PICHADO',        'Pichado',       FALSE, 80,  TRUE),
    ('MANCHADO',       'Manchado',      FALSE, 90,  TRUE),
    -- Backfill only — nunca aparece na tela (ativo=FALSE)
    ('NAO_INFORMADO',  'Não informado', FALSE, 999, FALSE)
ON CONFLICT (codigo) DO NOTHING;

-- ============================================================================
-- 2 · EVOLUIR portaria.avaria_saida — só ACRESCENTA coluna
-- ============================================================================
ALTER TABLE portaria.avaria_saida
    ADD COLUMN IF NOT EXISTS zona_codigo       VARCHAR(30) REFERENCES portaria.avaria_zona(codigo),
    ADD COLUMN IF NOT EXISTS tipo_codigo       VARCHAR(20) REFERENCES portaria.avaria_tipo(codigo),
    ADD COLUMN IF NOT EXISTS severidade        VARCHAR(10)  NOT NULL DEFAULT 'LEVE',
    ADD COLUMN IF NOT EXISTS status            VARCHAR(14)  NOT NULL DEFAULT 'ABERTA',
    ADD COLUMN IF NOT EXISTS primeira_vez_em   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ultima_vez_em     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS vezes_vista       INTEGER      NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS encerrada_em      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS encerrada_por     UUID REFERENCES public.funcionario(id),
    ADD COLUMN IF NOT EXISTS encerramento      VARCHAR(20),
    ADD COLUMN IF NOT EXISTS encerramento_nota TEXT;

-- zona_codigo/tipo_codigo ficam NULLABLE até o backfill (seção 7) preencher
-- as linhas antigas; viram NOT NULL só no fim desta migration.

ALTER TABLE portaria.avaria_saida DROP CONSTRAINT IF EXISTS ck_avaria_saida_severidade;
ALTER TABLE portaria.avaria_saida ADD CONSTRAINT ck_avaria_saida_severidade
    CHECK (severidade IN ('LEVE', 'MEDIA', 'GRAVE'));

ALTER TABLE portaria.avaria_saida DROP CONSTRAINT IF EXISTS ck_avaria_saida_status;
ALTER TABLE portaria.avaria_saida ADD CONSTRAINT ck_avaria_saida_status
    CHECK (status IN ('ABERTA', 'REPARADA', 'INEXISTENTE'));

ALTER TABLE portaria.avaria_saida DROP CONSTRAINT IF EXISTS ck_avaria_saida_encerramento;
ALTER TABLE portaria.avaria_saida ADD CONSTRAINT ck_avaria_saida_encerramento
    CHECK (encerramento IN ('REPARADA', 'NAO_EXISTIA', 'DUPLICADA'));

COMMENT ON COLUMN portaria.avaria_saida.status IS 'Estado do DANO (⚠️ vocabulário diferente do de `encerramento`, de propósito — um descreve o estado, o outro o motivo de ter chegado lá). ABERTA = em aberto, mostra no mapa. REPARADA/INEXISTENTE = encerrada, some da tela do dia a dia mas continua no banco (prova). INEXISTENTE ⛔ não é "apagada": é "conferida e não existe" (marcada por engano, ou já reparada antes de existir sistema).';
COMMENT ON COLUMN portaria.avaria_saida.encerramento IS 'Motivo do encerramento: REPARADA (funilaria consertou, Fase 2) | NAO_EXISTIA (não foi confirmada — engano, ou regra das duas contestações) | DUPLICADA. NULL enquanto a avaria está ABERTA.';
COMMENT ON COLUMN portaria.avaria_saida.encerrada_por IS 'NULL quando o encerramento foi automático (regra das duas contestações, portaria.avaria_contestacao) — encerramento do SISTEMA, não de uma pessoa. Preenchido quando é a manutenção que dá baixa (POST /portaria/avarias/{id}/encerrar, Fase 2).';
COMMENT ON COLUMN portaria.avaria_saida.primeira_vez_em IS 'Quando este ciclo da avaria nasceu (1ª constatação). Junto de ultima_vez_em, delimita a janela de responsabilidade entre motoristas.';
COMMENT ON COLUMN portaria.avaria_saida.vezes_vista IS 'Quantas constatações este ciclo já recebeu. Cresce a cada POST repetido em vez de criar avaria nova — é a deduplicação (índice único da seção 5) na prática.';

-- ============================================================================
-- 3 · CONSTATAÇÃO — a tabela que é prova
-- ============================================================================
-- 🔴 IMUTÁVEL. O backend (P2, routers/portaria_avarias.py) NÃO expõe PUT nem
-- DELETE nesta tabela. Correção só por anulação (anulada_em/anulada_por/
-- anulacao_nota) — a linha continua consultável. Se o Claude Code for
-- tentado a criar um UPDATE/DELETE de constatação num próximo prompt, a
-- resposta é não: é prova de que um motorista pegou o carro já avariado.
CREATE TABLE IF NOT EXISTS portaria.avaria_constatacao (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    avaria_id      UUID NOT NULL REFERENCES portaria.avaria_saida(id) ON DELETE RESTRICT,
    data_servico   DATE        NOT NULL,
    ocorrido_em    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    motorista_re   VARCHAR(20),
    motorista_nome VARCHAR(120),
    observacao     TEXT,
    piorou         BOOLEAN     NOT NULL DEFAULT FALSE,
    -- SNAPSHOT do código da linha que o carro ia operar nesta saída (pedido
    -- do Alisson em 16/09). Fica na CONSTATAÇÃO, não na avaria: o carro roda
    -- 1782 hoje e 8022 amanhã, e o dano continua o mesmo — igual motorista_re.
    -- Código do catálogo fiscalizacao.linha, escolhido pelo seletor na tela
    -- (P3) — ⛔ SEM FK: teste 17 da Fase 1 exige aceitar linha fora do
    -- catálogo como snapshot (carro pode operar linha recém-criada), a trava
    -- é o seletor na tela, não o banco.
    linha_codigo   VARCHAR(20),
    registrado_por UUID        NOT NULL REFERENCES public.funcionario(id),
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    anulada_em     TIMESTAMPTZ,
    anulada_por    UUID REFERENCES public.funcionario(id),
    anulacao_nota  TEXT,
    CONSTRAINT ck_constatacao_piorou_nota  CHECK (piorou = FALSE OR observacao IS NOT NULL),
    CONSTRAINT ck_constatacao_anulada_nota CHECK (anulada_em IS NULL OR anulacao_nota IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_avaria_constatacao_avaria ON portaria.avaria_constatacao (avaria_id, ocorrido_em DESC);
-- "o que o RE 4102 já marcou" — a consulta que protege o motorista quando ele é chamado
CREATE INDEX IF NOT EXISTS ix_avaria_constatacao_motorista ON portaria.avaria_constatacao (motorista_re, ocorrido_em DESC);

COMMENT ON TABLE portaria.avaria_constatacao IS 'A DECLARAÇÃO: fulano pegou este carro nesta data e o dano já estava aqui. É a prova, e é IMUTÁVEL — o backend não expõe UPDATE nem DELETE nesta tabela (só INSERT e a anulação via anulada_em/anulada_por/anulacao_nota, que preserva a linha). ON DELETE RESTRICT em avaria_id: apagar a avaria levaria junto a defesa de todos os motoristas que passaram por ela — por isso nunca CASCADE.';
COMMENT ON COLUMN portaria.avaria_constatacao.piorou IS 'TRUE quando o controlador percebe que o dano piorou desde a última constatação — abre uma delimitação nova de responsabilidade sem abrir avaria nova (o índice único da seção 5 bloquearia, e partiria a linha do tempo do dano em duas). Observação é obrigatória (ck_constatacao_piorou_nota): é ela que documenta o que mudou.';
COMMENT ON COLUMN portaria.avaria_constatacao.linha_codigo IS 'Snapshot da linha que o carro ia operar nesta saída (fiscalizacao.linha, escolhida pelo seletor de catálogo — nunca digitação livre). Fica na constatação, não na avaria, porque o carro muda de linha de um dia para o outro e o dano não. ⛔ Sem FK: aceito fora do catálogo (carro pode operar linha recém-criada) — quem trava é o seletor na tela.';
COMMENT ON COLUMN portaria.avaria_constatacao.anulacao_nota IS 'Obrigatória quando anulada_em é preenchido (ck_constatacao_anulada_nota). Anulação NÃO é DELETE: a linha continua na tabela e consultável, só sai das contagens ativas.';

-- ============================================================================
-- 4 · CONTESTAÇÃO — o ciclo de vida sem depender da oficina
-- ============================================================================
CREATE TABLE IF NOT EXISTS portaria.avaria_contestacao (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    avaria_id      UUID NOT NULL REFERENCES portaria.avaria_saida(id) ON DELETE RESTRICT,
    ocorrido_em    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data_servico   DATE        NOT NULL,
    nota           TEXT,
    registrado_por UUID        NOT NULL REFERENCES public.funcionario(id),
    CONSTRAINT uq_contestacao_por_pessoa UNIQUE (avaria_id, registrado_por)
);

COMMENT ON TABLE portaria.avaria_contestacao IS 'O controlador diz "não vi mais essa". A avaria continua ABERTA e marcada na tela — contestação NÃO apaga nem esconde o dano enquanto ele não encerrar: os motoristas que passarem no meio continuam podendo se proteger com ela. REGRA DAS DUAS: quando duas pessoas DIFERENTES, em dias de serviço DIFERENTES, contestam a mesma avaria, o backend (P2) encerra sozinho com avaria_saida.encerramento=NAO_EXISTIA e encerrada_por=NULL — é encerramento do SISTEMA, não de uma pessoa (ver COMMENT em avaria_saida.encerrada_por). A checagem de "dias diferentes" é do backend; o UNIQUE (avaria_id, registrado_por) só impede a MESMA pessoa contestar duas vezes e fechar sozinha.';
COMMENT ON CONSTRAINT uq_contestacao_por_pessoa ON portaria.avaria_contestacao IS 'Impede o mesmo controlador contestar a mesma avaria duas vezes e, sozinho, acionar a regra das duas. A regra em si (duas pessoas, dias diferentes) é checada pelo backend, não pelo banco.';

-- ============================================================================
-- 5 · A TRAVA CONTRA DUPLICATA
-- ============================================================================
-- ⚠️ O "AND zona_codigo <> 'NAO_INFORMADA'" NÃO é decoração: sem ele o
-- backfill (seção 7) quebra na hora — um carro com três avarias antigas
-- viraria três linhas com a mesma chave (todas NAO_INFORMADA/NAO_INFORMADO).
--
-- 🟢 É PARCIAL também para permitir o renascimento: avaria REPARADA não
-- bloqueia uma nova com a mesma chave. O mesmo para-choque pode amassar de
-- novo em dezembro e nascer como avaria SEPARADA, com seus próprios
-- motoristas — é disso que sai a reincidência.
--
-- 🔴 ARMADILHA CONHECIDA — já mordeu este projeto 2 vezes
-- (armadilha_constraint_imediata_flush): constraint única + flush do
-- SQLAlchemy estoura num ponto LONGE da causa. Mitigação é do backend (P2):
-- consultar antes de inserir na mesma transação, tratar IntegrityError como
-- "alguém gravou junto — recarrega e acrescenta constatação", ⛔ nunca como
-- erro na tela.
CREATE UNIQUE INDEX IF NOT EXISTS uq_avaria_aberta_por_zona_tipo
    ON portaria.avaria_saida (prefixo, zona_codigo, tipo_codigo)
    WHERE status = 'ABERTA' AND zona_codigo <> 'NAO_INFORMADA';

-- ============================================================================
-- 6 · RETENÇÃO — 365 dias, e a conta muda de base
-- ============================================================================
-- Hoje (036) expira_em = criado_em + 60 dias, fixo. Dois defeitos: o dano
-- crônico some da tela no 61º dia ainda existindo, e — o grave — a defesa
-- do motorista evapora antes da cobrança chegar.
--
--   expira_em = GREATEST(ultima_vez_em, COALESCE(encerrada_em, ultima_vez_em)) + 365 dias
--
-- recalculado pelo BACKEND (P2, fora desta migration) a cada constatação e
-- no encerramento — ⚠️ 365 é constante nomeada lá (RETENCAO_AVARIA_DIAS),
-- ⛔ não número solto: o Alisson ainda vai confirmar o prazo com RH/jurídico
-- e trocar isso não pode custar migration.
--
-- O DEFAULT da coluna muda de acompanhamento (para INSERT direto, fora do
-- caminho normal do backend, não herdar a regra de 60 dias antiga):
ALTER TABLE portaria.avaria_saida ALTER COLUMN expira_em SET DEFAULT NOW() + INTERVAL '365 days';

COMMENT ON COLUMN portaria.avaria_saida.expira_em IS '365 dias (a partir da 042; era 60 na 036) — expurgo por FILTRO no GET (expira_em > NOW()), ⛔ nunca DELETE físico e ⛔ nunca job: o projeto não tem scheduler (mesma decisão da migration 028). Recalculado pelo backend a cada constatação e no encerramento: GREATEST(ultima_vez_em, COALESCE(encerrada_em, ultima_vez_em)) + 365 dias. A linha fica no banco e segue consultável por busca direta mesmo depois de sair da tela — é o que sustenta a defesa do motorista chamado meses depois.';

-- ============================================================================
-- 7 · BACKFILL — toda avaria da 036 ganha zona/tipo e sua constatação
-- ============================================================================
-- Nenhum registro de produção pode ficar sem constatação: são defesas de
-- motoristas reais, não podem desaparecer na evolução do schema.
UPDATE portaria.avaria_saida
   SET zona_codigo     = 'NAO_INFORMADA',
       tipo_codigo     = 'NAO_INFORMADO',
       status          = 'ABERTA',
       primeira_vez_em = ocorrido_em,
       ultima_vez_em   = ocorrido_em,
       vezes_vista     = 1,
       -- recomputa já na base nova (365 dias), senão a linha herdaria o
       -- expira_em de 60 dias calculado pela 036 e sumiria cedo demais.
       expira_em       = ocorrido_em + INTERVAL '365 days'
 WHERE zona_codigo IS NULL;

-- Uma constatação por avaria antiga — a primeira (e, até aqui, única) vez
-- que ela foi vista.
INSERT INTO portaria.avaria_constatacao
    (avaria_id, data_servico, ocorrido_em, motorista_re, motorista_nome, observacao, registrado_por)
SELECT a.id, a.data_servico, a.ocorrido_em, a.motorista_re, a.motorista_nome, a.descricao, a.registrado_por
  FROM portaria.avaria_saida a
 WHERE NOT EXISTS (
        SELECT 1 FROM portaria.avaria_constatacao c WHERE c.avaria_id = a.id
       );

-- Só agora, com todo backfill feito, zona_codigo e tipo_codigo viram
-- obrigatórios para qualquer linha nova.
ALTER TABLE portaria.avaria_saida ALTER COLUMN zona_codigo SET NOT NULL;
ALTER TABLE portaria.avaria_saida ALTER COLUMN tipo_codigo SET NOT NULL;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT count(*) FROM portaria.avaria_saida WHERE zona_codigo IS NULL OR tipo_codigo IS NULL;  -- 0
--
--   -- toda avaria antiga ganhou sua constatação (nenhuma defesa perdida)
--   SELECT (SELECT count(*) FROM portaria.avaria_saida) = (SELECT count(*) FROM portaria.avaria_constatacao);  -- t
--
--   -- toda zona aponta para uma região que a coordenadoria conhece
--   SELECT DISTINCT regiao_ocorrencia FROM portaria.avaria_zona;
--   -- esperado: só valores de REGIOES_AVARIA (ocorrencia.vocabulario.js / migration 012):
--   -- FRENTE, TRASEIRA, LATERAL_ESQUERDA, LATERAL_DIREITA, TETO, INTERIOR, RODADO, RETROVISOR, PARABRISA, OUTRO
--
--   SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_avaria_aberta_por_zona_tipo';
--   -- e MORDE: rodar 2x o mesmo INSERT com zona/tipo reais → o 2º tem que ser RECUSADO
--
--   -- a prova não pode ser destruída: este DELETE tem que FALHAR com violação de FK
--   DELETE FROM portaria.avaria_saida WHERE id = (SELECT avaria_id FROM portaria.avaria_constatacao LIMIT 1);
--
--   -- nenhum recurso/permissão novo — RBAC segue em acesso_veicular, como a 036:
--   SELECT fp.recurso, fp.pode_ler, fp.pode_escrever
--     FROM public.funcao_permissao fp
--     JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fn.codigo = 'CONTROLADOR_ACESSO' AND fp.recurso = 'acesso_veicular';
--   -- esperado: já existia antes desta migration (TRUE, TRUE)
--
--   -- catálogos nasceram com o seed inteiro:
--   SELECT count(*) FROM portaria.avaria_zona;  -- 59 (32 comuns + 1 OUTRO + 1 backfill + 8 Fase 4 + 17 vocabulário garagem)
--   SELECT count(*) FROM portaria.avaria_tipo;  -- 10 (9 comuns + 1 backfill)
--
--   -- a válvula de escape existe, está ATIVA (⛔ diferente do backfill) e é INTERNO/OUTRO:
--   SELECT codigo, ativo, vista, regiao_ocorrencia FROM portaria.avaria_zona WHERE codigo = 'OUTRO';
--   -- esperado: OUTRO | t | INTERNO | OUTRO
--
--   -- as 17 zonas de vocabulário da garagem nasceram na ordem intercalada certa:
--   SELECT ordem, codigo, nome, vista FROM portaria.avaria_zona
--    WHERE vista IN ('FRENTE','TRASEIRA') ORDER BY ordem;
--   SELECT codigo FROM portaria.avaria_zona WHERE codigo IN (
--     'PONTEIRA_DIANT_ESQ','PONTEIRA_DIANT_DIR','PONTEIRA_TRAS_ESQ','PONTEIRA_TRAS_DIR',
--     'CURVAO_DIANT_ESQ','CURVAO_DIANT_DIR','CURVAO_TRAS_ESQ','CURVAO_TRAS_DIR',
--     'BRACO_RETROV_ESQ','BRACO_RETROV_DIR',
--     'VIGIA_DIANT_ESQ','VIGIA_DIANT_DIR','VIGIA_TRAS_ESQ','VIGIA_TRAS_DIR',
--     'VIGIA_LAT_ESQ','VIGIA_LAT_DIR','LAT_DIR_DIANT');
--   -- esperado: as 17 linhas
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- 🔴 PENSAR DUAS VEZES antes de reverter depois de uso real: derrubar
-- avaria_constatacao apaga PROVA — a defesa de todo motorista que constou
-- ali. Só fazer sentido se a migration foi aplicada e revertida no mesmo
-- dia, sem nenhum POST real do backend novo no meio.
--
-- ALTER TABLE portaria.avaria_saida ALTER COLUMN zona_codigo DROP NOT NULL;
-- ALTER TABLE portaria.avaria_saida ALTER COLUMN tipo_codigo DROP NOT NULL;
-- DROP INDEX IF EXISTS portaria.uq_avaria_aberta_por_zona_tipo;
-- DROP TABLE IF EXISTS portaria.avaria_contestacao;
-- DROP TABLE IF EXISTS portaria.avaria_constatacao;  -- ⚠️ apaga prova, ver acima
-- ALTER TABLE portaria.avaria_saida ALTER COLUMN expira_em SET DEFAULT NOW() + INTERVAL '60 days';
-- ALTER TABLE portaria.avaria_saida
--     DROP COLUMN IF EXISTS zona_codigo,
--     DROP COLUMN IF EXISTS tipo_codigo,
--     DROP COLUMN IF EXISTS severidade,
--     DROP COLUMN IF EXISTS status,
--     DROP COLUMN IF EXISTS primeira_vez_em,
--     DROP COLUMN IF EXISTS ultima_vez_em,
--     DROP COLUMN IF EXISTS vezes_vista,
--     DROP COLUMN IF EXISTS encerrada_em,
--     DROP COLUMN IF EXISTS encerrada_por,
--     DROP COLUMN IF EXISTS encerramento,
--     DROP COLUMN IF EXISTS encerramento_nota;
-- DROP TABLE IF EXISTS portaria.avaria_tipo;
-- DROP TABLE IF EXISTS portaria.avaria_zona;
-- (avaria_saida em si, da 036, não é tocada por este rollback.)
-- ============================================================================
