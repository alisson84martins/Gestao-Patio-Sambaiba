-- ============================================================================
-- MIGRATION 052 — Recolhida anormal com vários defeitos
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria
-- DATA:   2026-11-08
-- AUTOR:  Claude (decisão do Alisson)
-- DEPENDE DE: 026-recolhida-anormal.sql, 032-recolhida-encerramento.sql
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   A tela da recolhida só deixava marcar UM defeito. Carro que recolhe com
--   elétrica E pneu obrigava o controlador a escolher um e perder o outro.
--   Decisão do Alisson (08/11/2026), mesmo princípio do carrinho da avaria:
--     - UMA recolhida (o carro recolheu uma vez — não vira 3 RAs na fila
--       da Manutenção nem 3 recolhidas na conta do motorista);
--     - VÁRIOS defeitos dentro dela, cada um com observação própria;
--     - UMA ficha de manutenção POR DEFEITO (elétrica e pneu podem ir pra
--       equipes diferentes).
--
--   recolhida_anormal.tipo_defeito_codigo e .ficha_id CONTINUAM existindo e
--   são preenchidos com o 1º defeito — compatibilidade com o CHECK
--   ck_recolhida_motivo_defeito, a análise e as telas antigas. A lista
--   completa mora aqui.
--
-- IDEMPOTENTE: pode rodar duas vezes (CREATE IF NOT EXISTS, backfill com
-- ON CONFLICT DO NOTHING).
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 052-recolhida-varios-defeitos.sql
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS portaria.recolhida_defeito (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recolhida_id          UUID NOT NULL
                          REFERENCES portaria.recolhida_anormal(id) ON DELETE CASCADE,
    ordem                 SMALLINT NOT NULL DEFAULT 1,
    -- TEXTO, não FK — mesmo princípio de recolhida_anormal.tipo_defeito_codigo
    -- (o catálogo pode mudar, o histórico continua verdadeiro).
    tipo_defeito_codigo   VARCHAR(20) NOT NULL,
    observacao            TEXT,
    ficha_id              UUID REFERENCES public.ficha_manutencao(id),
    ficha_falhou_motivo   TEXT,
    criado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- O mesmo defeito não entra duas vezes na mesma recolhida.
    CONSTRAINT uq_recolhida_defeito_tipo UNIQUE (recolhida_id, tipo_defeito_codigo)
);

COMMENT ON TABLE portaria.recolhida_defeito IS
    'Defeitos de uma recolhida anormal (motivo=DEFEITO) — 1 recolhida, N defeitos, 1 ficha de manutenção por defeito. O 1º defeito também fica em recolhida_anormal.tipo_defeito_codigo/ficha_id por compatibilidade.';

CREATE INDEX IF NOT EXISTS idx_recolhida_defeito_recolhida
    ON portaria.recolhida_defeito (recolhida_id, ordem);

-- Backfill: toda recolhida antiga de DEFEITO vira 1 linha aqui, com a ficha
-- que ela já tinha. A observação antiga era o relato geral — fica no relato.
INSERT INTO portaria.recolhida_defeito
    (recolhida_id, ordem, tipo_defeito_codigo, ficha_id, ficha_falhou_motivo, criado_em)
SELECT r.id, 1, r.tipo_defeito_codigo, r.ficha_id, r.ficha_falhou_motivo, r.criado_em
  FROM portaria.recolhida_anormal r
 WHERE r.tipo_defeito_codigo IS NOT NULL
ON CONFLICT (recolhida_id, tipo_defeito_codigo) DO NOTHING;

-- 🔴 Tabela nova nasce sem permissão para o papel da aplicação — sem isto a
-- recolhida quebra só em produção (armadilha da avaria, 17/09).
GRANT SELECT, INSERT, UPDATE, DELETE ON portaria.recolhida_defeito TO sambaiba;

COMMIT;

-- CONFERÊNCIA (rodar depois):
--   SELECT count(*) FROM portaria.recolhida_anormal WHERE tipo_defeito_codigo IS NOT NULL;
--   SELECT count(*) FROM portaria.recolhida_defeito;   -- tem que bater
