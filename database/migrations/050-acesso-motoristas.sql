-- ============================================================================
-- MIGRATION 050 — Acesso de motorista a partir das ocorrências
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: public (usuario_login, log_acesso, pessoa_pre_cadastro)
-- DATA:   2026-11-02
-- AUTOR:  Claude
-- DEPENDE DE: 011-rbac-cadastro-central.sql (usuario_login),
--             021-log-acesso.sql (log_acesso),
--             028-pessoa-pre-cadastro.sql (pessoa_pre_cadastro)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   1. 🔴 Bug existente: a 011 criou usuario_login.politica_senha com
--      CHECK ('CPF','FORTE'), mas PATCH /funcionarios/{id}/login/senha grava
--      'PROPRIA' — trocar a própria senha dava 500 (violação de CHECK).
--      Conferido em produção em 02/11/2026 (pg_get_constraintdef). Entram
--      'PROPRIA' e 'PROVISORIA' (login criado pelo sistema para o motorista,
--      que obriga a confirmar identidade e trocar a senha no 1º acesso).
--   2. log_acesso ganha os eventos ACESSO_CRIADO, IDENTIDADE_CONFIRMADA e
--      IDENTIDADE_NEGADA (mantém os 4 atuais).
--   3. pessoa_pre_cadastro.pendencia — motivo legível de por que o acesso
--      NÃO foi criado (CPF inválido, CPF de outra pessoa, nome divergente…).
--   4. usuario_login.identidade_confirmada_em — carimbo do sistema (UTC)
--      de quando o motorista respondeu "Sou eu".
--
-- GRANT: esta migration NÃO cria tabela nova — só troca CHECK e adiciona
--   coluna em tabelas que o papel `sambaiba` já acessa. Por isso não há
--   GRANT aqui. Se um dia ela passar a criar tabela, incluir
--   GRANT SELECT, INSERT, UPDATE, DELETE ON <tabela> TO sambaiba;
--
-- IDEMPOTENTE: pode rodar duas vezes.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 050-acesso-motoristas.sql
-- ============================================================================

BEGIN;

SET search_path TO public;

-- ----------------------------------------------------------------------------
-- 1 · usuario_login.politica_senha — o CHECK da 011 é anônimo (o Postgres
--     dá o nome sozinho, normalmente usuario_login_politica_senha_check).
--     Remove TODO check desta tabela que mencione politica_senha, qualquer
--     que seja o nome, e recria com nome fixo.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT conname
          FROM pg_constraint
         WHERE conrelid = 'public.usuario_login'::regclass
           AND contype = 'c'
           AND pg_get_constraintdef(oid) LIKE '%politica_senha%'
    LOOP
        EXECUTE format('ALTER TABLE public.usuario_login DROP CONSTRAINT %I', r.conname);
        RAISE NOTICE '050: CHECK % removido de usuario_login.', r.conname;
    END LOOP;
END $$;

ALTER TABLE public.usuario_login
    ADD CONSTRAINT chk_usuario_login_politica_senha
    CHECK (politica_senha IN ('CPF','FORTE','PROPRIA','PROVISORIA'));

-- ----------------------------------------------------------------------------
-- 2 · log_acesso.evento — eventos novos do fluxo de acesso do motorista.
-- ----------------------------------------------------------------------------
ALTER TABLE public.log_acesso DROP CONSTRAINT IF EXISTS chk_log_acesso_evento;
ALTER TABLE public.log_acesso
    ADD CONSTRAINT chk_log_acesso_evento CHECK (
        evento IN (
            'LOGIN_SUCESSO', 'LOGIN_FALHA', 'NEGADO_403', 'LEITURA_OCORRENCIA',
            'ACESSO_CRIADO', 'IDENTIDADE_CONFIRMADA', 'IDENTIDADE_NEGADA'
        )
    );

-- ----------------------------------------------------------------------------
-- 3 · pessoa_pre_cadastro.pendencia
-- ----------------------------------------------------------------------------
ALTER TABLE public.pessoa_pre_cadastro ADD COLUMN IF NOT EXISTS pendencia TEXT;
COMMENT ON COLUMN public.pessoa_pre_cadastro.pendencia IS
    'Por que o acesso ao sistema NÃO foi criado automaticamente (conferências de app/services/acesso_motorista.py). Texto legível, um motivo por linha. ⛔ Nunca contém CPF completo. NULL = sem pendência.';

-- ----------------------------------------------------------------------------
-- 4 · usuario_login.identidade_confirmada_em
-- ----------------------------------------------------------------------------
ALTER TABLE public.usuario_login ADD COLUMN IF NOT EXISTS identidade_confirmada_em TIMESTAMPTZ;
COMMENT ON COLUMN public.usuario_login.identidade_confirmada_em IS
    'Carimbo do sistema (UTC) de quando a pessoa confirmou "Sou eu" no 1º acesso de um login PROVISORIA. NULL = nunca confirmou (ou login antigo, que não passa por isso).';

COMMIT;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid IN ('public.usuario_login'::regclass, 'public.log_acesso'::regclass)
--      AND contype = 'c';
--   -- esperado: chk_usuario_login_politica_senha com os 4 valores e
--   --           chk_log_acesso_evento com os 7 eventos.
--   SELECT politica_senha, count(*) FROM usuario_login GROUP BY 1;
-- ============================================================================

-- ============================================================================
-- ROLLBACK (⚠️ só funciona se nenhum login estiver PROPRIA/PROVISORIA e
-- nenhum log tiver os eventos novos)
-- ============================================================================
-- ALTER TABLE public.usuario_login DROP CONSTRAINT IF EXISTS chk_usuario_login_politica_senha;
-- ALTER TABLE public.usuario_login ADD CONSTRAINT usuario_login_politica_senha_check
--     CHECK (politica_senha IN ('CPF','FORTE'));
-- ALTER TABLE public.log_acesso DROP CONSTRAINT IF EXISTS chk_log_acesso_evento;
-- ALTER TABLE public.log_acesso ADD CONSTRAINT chk_log_acesso_evento CHECK (
--     evento IN ('LOGIN_SUCESSO', 'LOGIN_FALHA', 'NEGADO_403', 'LEITURA_OCORRENCIA'));
-- ALTER TABLE public.usuario_login DROP COLUMN IF EXISTS identidade_confirmada_em;
-- ALTER TABLE public.pessoa_pre_cadastro DROP COLUMN IF EXISTS pendencia;
-- ============================================================================
