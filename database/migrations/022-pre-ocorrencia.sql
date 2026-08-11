-- ============================================================================
-- MIGRATION 022 — Pré-ocorrência do motorista (captura por link, sem conta)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: coordenadoria (mesmo schema de ocorrencia — pré-ocorrência é
--         insumo da Coordenadoria, nunca do Pátio)
-- DATA:   2026-08-11
-- AUTOR:  Claude Code (Bloco C do fechamento)
-- DEPENDE DE: 011-rbac-cadastro-central.sql (funcionario, funcao, recurso,
--             funcao_permissao), 012-modulo-coordenadoria-ocorrencias.sql
--             (schema coordenadoria, tabela ocorrencia)
-- ORIGEM: _handoff-claude/PROMPT-pre-ocorrencia-motorista.md +
--         _handoff-claude/DESENHO-pre-ocorrencia.md (Fase 0, respondida
--         antes desta migration)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Hoje o motorista relata o acidente por telefone/WhatsApp, de voz, pro
--   coordenador anotar. O relato nas palavras dele — o que mais pesa como
--   prova — se perde na transmissão. Esta migration cria a captura direta:
--   coordenador/CCO abre uma autorização, o motorista preenche pelo link
--   (sem conta, sem senha), o coordenador converte em Ocorrencia definitiva
--   depois.
--
-- ADIÇÃO PURA — nenhuma tabela existente é alterada.
-- ⛔ funcionario.re e funcionario.cpf continuam UNIQUE separados (não virou
--    chave composta) — restrição composta aceitaria dois CPFs cadastrados
--    sob REs diferentes, que é o duplicado que a unicidade separada evita.
--
-- NATUREZA: idempotente (CREATE TABLE/INDEX IF NOT EXISTS, INSERT ON CONFLICT).
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table funcionario", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 022-pre-ocorrencia.sql
-- ============================================================================

-- ============================================================================
-- 1 · pre_ocorrencia_autorizacao — o que o coordenador ou CCO abre
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.pre_ocorrencia_autorizacao (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    aberta_por        UUID         NOT NULL REFERENCES public.funcionario(id),
    coordenador_id    UUID         NOT NULL REFERENCES public.funcionario(id),
    telefone_destino  VARCHAR(20)  NOT NULL,

    -- O que quem abre já sabe do rádio — tudo opcional (decisão 10: o
    -- motorista preenche de novo no formulário, isto aqui é só o que
    -- ajuda a rotear/identificar antes de qualquer relato existir).
    motorista_re      VARCHAR(20),
    motorista_nome    VARCHAR(120),
    prefixo           VARCHAR(10),
    linha_codigo      VARCHAR(20),

    -- 🔴 Token em claro NUNCA é gravado — só o hash (SHA-256). Token em
    -- claro no banco é senha em claro no banco.
    token_hash        VARCHAR(64)  NOT NULL UNIQUE,
    expira_em         TIMESTAMPTZ  NOT NULL,
    usada_em          TIMESTAMPTZ,
    status            VARCHAR(15)  NOT NULL DEFAULT 'AGUARDANDO'
                      CHECK (status IN ('AGUARDANDO','ENVIADA','EXPIRADA','CANCELADA')),
    criada_em         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE coordenadoria.pre_ocorrencia_autorizacao IS
    'Janela de autorização aberta por coordenador ou CCO. Sem uma linha aqui com token válido, o link público não funciona.';
COMMENT ON COLUMN coordenadoria.pre_ocorrencia_autorizacao.token_hash IS
    'SHA-256 do token de uso único, hex (64 chars). O token em claro só existe na resposta do POST que cria esta linha — nunca mais.';
COMMENT ON COLUMN coordenadoria.pre_ocorrencia_autorizacao.coordenador_id IS
    'Dono da pré-ocorrência resultante. = aberta_por quando o próprio coordenador abre; obrigatório informar quando é o CCO que abre (CCO não é dono de nada, só roteia).';

CREATE INDEX IF NOT EXISTS idx_pre_oc_auth_coordenador ON coordenadoria.pre_ocorrencia_autorizacao (coordenador_id);
CREATE INDEX IF NOT EXISTS idx_pre_oc_auth_aberta_por  ON coordenadoria.pre_ocorrencia_autorizacao (aberta_por);
-- token_hash já é UNIQUE (índice implícito) — cobre a busca do endpoint público.

-- ============================================================================
-- 2 · pre_ocorrencia — o que o motorista preencheu (um por autorização)
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.pre_ocorrencia (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    autorizacao_id   UUID         NOT NULL UNIQUE
                     REFERENCES coordenadoria.pre_ocorrencia_autorizacao(id) ON DELETE CASCADE,

    -- ── Motorista (quem preenche) ────────────────────────────────────────
    motorista_re     VARCHAR(20),
    motorista_nome   VARCHAR(120),
    motorista_telefone VARCHAR(20),
    motorista_cpf    VARCHAR(14),
    motorista_rg     VARCHAR(20),
    motorista_cnh    VARCHAR(20),

    -- ── Cobrador — não vem pré-preenchido (decisão 10) ───────────────────
    cobrador_re      VARCHAR(20),
    cobrador_nome    VARCHAR(120),
    cobrador_telefone VARCHAR(20),

    -- ── Veículo — não vem pré-preenchido (decisão 10) ────────────────────
    prefixo          VARCHAR(10),

    -- ── Quando/onde ───────────────────────────────────────────────────────
    data_ocorrencia  DATE,
    hora_ocorrencia  TIME         NOT NULL,   -- obrigatório: âncora temporal do relato
    local_logradouro VARCHAR(200),
    local_numero     VARCHAR(20),
    local_bairro     VARCHAR(80),
    local_cidade     VARCHAR(80),
    local_referencia VARCHAR(200),

    -- ── O campo mais valioso ──────────────────────────────────────────────
    relato           TEXT         NOT NULL,

    -- ── Terceiro — lista de coleta, tudo opcional (decisão 9) ────────────
    terceiro_placa       VARCHAR(10),
    terceiro_modelo_cor  VARCHAR(120),
    terceiro_nome        VARCHAR(120),
    terceiro_telefone    VARCHAR(20),
    terceiro_seguradora  VARCHAR(80),

    status           VARCHAR(15)  NOT NULL DEFAULT 'AGUARDANDO'
                     CHECK (status IN ('AGUARDANDO','ENVIADA','CONVERTIDA','DESCARTADA')),

    criado_em        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    atualizado_em    TIMESTAMPTZ,
    enviada_em       TIMESTAMPTZ,
    convertida_em    TIMESTAMPTZ,
    ocorrencia_id    UUID REFERENCES coordenadoria.ocorrencia(id) ON DELETE SET NULL   -- preenchido na conversão
);
COMMENT ON TABLE coordenadoria.pre_ocorrencia IS
    'Relato do motorista, capturado pelo link de uso único. Escrita sem leitura pelo público (decisão 5) — o formulário grava, nunca devolve dado de outra linha.';
COMMENT ON COLUMN coordenadoria.pre_ocorrencia.relato IS 'TEXT sem limite apertado — é o campo mais valioso desta tabela inteira.';
COMMENT ON COLUMN coordenadoria.pre_ocorrencia.ocorrencia_id IS
    'Preenchido só na conversão. A Ocorrencia resultante tem registrado_por = coordenador que converteu, NUNCA o motorista — ver regra na Fase 2.4 do prompt.';

CREATE INDEX IF NOT EXISTS idx_pre_oc_autorizacao ON coordenadoria.pre_ocorrencia (autorizacao_id);
CREATE INDEX IF NOT EXISTS idx_pre_oc_status       ON coordenadoria.pre_ocorrencia (status);

-- ============================================================================
-- 3 · pre_ocorrencia_anexo — CNH e documento do veículo
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.pre_ocorrencia_anexo (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    pre_ocorrencia_id UUID        NOT NULL REFERENCES coordenadoria.pre_ocorrencia(id) ON DELETE CASCADE,
    tipo             VARCHAR(15)  NOT NULL CHECK (tipo IN ('CNH','DOC_VEICULO','OUTRO')),
    caminho          VARCHAR(255) NOT NULL,
    nome_original    VARCHAR(255),
    mime_type        VARCHAR(100),
    tamanho_bytes     INTEGER,
    criado_em        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    -- Sem coluna de autor: o upload é anônimo por token, não existe "quem"
    -- além da própria pré-ocorrência (diferente de ocorrencia_anexo.enviado_por).
);
COMMENT ON TABLE coordenadoria.pre_ocorrencia_anexo IS
    'CNH e documento do veículo, mesmo padrão de ocorrencia_anexo. ⚠️ CNH é o arquivo mais sensível do sistema — foto, filiação, CPF e RG numa imagem só (decisão 7: fica armazenada, mas entra na conversa de retenção LGPD).';

CREATE INDEX IF NOT EXISTS idx_pre_oc_anexo_pre_oc ON coordenadoria.pre_ocorrencia_anexo (pre_ocorrencia_id);
-- Máximo de 5 anexos por pré-ocorrência é aplicado no backend (contagem
-- antes de gravar), não como CHECK — CHECK não enxerga outras linhas.

-- ============================================================================
-- 4 · RBAC — recurso novo + função CCO + permissões
-- ============================================================================
INSERT INTO public.recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
    ('pre_ocorrencia', 'Pré-ocorrência do motorista',
     'Captura de relato do motorista por link, antes de virar ocorrência definitiva.',
     'COORDENADORIA', 11)
ON CONFLICT (codigo) DO UPDATE
   SET modulo_codigo = EXCLUDED.modulo_codigo,
       nome          = EXCLUDED.nome,
       descricao     = EXCLUDED.descricao,
       ordem         = EXCLUDED.ordem;

-- ⚠️ categoria = 'OPERACAO', não 'OPERACIONAL' como o prompt sugeria —
-- funcao.categoria tem CHECK fechado em ('GESTAO','OPERACAO','MANUTENCAO',
-- 'PATIO','SISTEMA') desde a 011; 'OPERACIONAL' quebraria o INSERT.
INSERT INTO public.funcao (codigo, nome, categoria, nivel, modulo_padrao, descricao) VALUES
    ('CCO', 'Centro de Controle Operacional', 'OPERACAO', 6, 'COORDENADORIA',
     'Abre e roteia pré-ocorrência de motorista para o coordenador certo. Não lê conteúdo de relato nem dado de terceiro.')
ON CONFLICT (codigo) DO NOTHING;

-- Permissões (tabela da Fase 1 do prompt):
--   CCO                  → só escreve (abre/roteia); leitura de CONTEÚDO é
--                           travada no endpoint, não aqui — ver Fase 2.2
--   COORDENADOR_TRAFEGO  → lê + escreve (só as próprias, via filtro no backend)
--   ENCARREGADO          → só lê (acompanha)
--   ADMIN                → lê + escreve (tudo)
--   GERENTE_GERAL / GERENTE_OPERACIONAL → só lê (acompanha)
INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, 'pre_ocorrencia', v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('CCO',                  FALSE, TRUE),
        ('COORDENADOR_TRAFEGO',  TRUE,  TRUE),
        ('ENCARREGADO',          TRUE,  FALSE),
        ('ADMIN',                TRUE,  TRUE),
        ('GERENTE_GERAL',        TRUE,  FALSE),
        ('GERENTE_OPERACIONAL',  TRUE,  FALSE)
       ) AS v(codigo, pode_ler, pode_escrever) ON v.codigo = fn.codigo
 ON CONFLICT (funcao_id, recurso) DO UPDATE
    SET pode_ler = EXCLUDED.pode_ler, pode_escrever = EXCLUDED.pode_escrever;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
-- \dt coordenadoria.pre_ocorrencia*
--
-- Recurso e permissões aplicadas:
--   SELECT fn.codigo, fp.pode_ler, fp.pode_escrever
--     FROM public.funcao_permissao fp JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fp.recurso = 'pre_ocorrencia' ORDER BY fn.codigo;
--   -- esperado: 6 linhas (CCO, COORDENADOR_TRAFEGO, ENCARREGADO, ADMIN,
--   --            GERENTE_GERAL, GERENTE_OPERACIONAL)
--
-- CCO cai direto na Coordenadoria ao logar:
--   SELECT modulo_padrao FROM public.funcao WHERE codigo = 'CCO';  -- esperado: COORDENADORIA
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DELETE FROM public.funcao_permissao WHERE recurso = 'pre_ocorrencia';
-- DELETE FROM public.funcao WHERE codigo = 'CCO';
-- DELETE FROM public.recurso WHERE codigo = 'pre_ocorrencia';
-- DROP TABLE IF EXISTS coordenadoria.pre_ocorrencia_anexo;
-- DROP TABLE IF EXISTS coordenadoria.pre_ocorrencia;
-- DROP TABLE IF EXISTS coordenadoria.pre_ocorrencia_autorizacao;
-- ============================================================================
