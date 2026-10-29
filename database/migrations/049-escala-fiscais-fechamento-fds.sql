-- ============================================================================
-- MIGRATION 049 — Escala de Fiscais: posto obrigatório no fim de semana
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: coordenadoria (altera escala_fiscal_posto)
-- DATA:   2026-10-29
-- AUTOR:  Claude
-- DEPENDE DE: 045-escala-fiscais.sql, 048-linha-unica.sql (código canônico
--             da linha, ex.: 2023-10, 1783-10) — em produção roda DEPOIS da 048.
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   O botão "Fechar fim de semana" da Montagem preenche sozinho o sábado e o
--   domingo. Ele precisa saber quais postos NÃO podem ficar sem fiscal:
--     · todo posto TP (regra da garagem — não depende desta coluna);
--     · os TS das RENDIÇÕES (2023 e 1783/1764), que precisam de fiscal.
--   Os outros TS podem ficar descobertos no fim de semana: o fiscal deles
--   vai para o TP.
--
-- O QUE MUDA
--   1. escala_fiscal_posto.obrigatorio_fds BOOLEAN NOT NULL DEFAULT false.
--   2. Só na PRIMEIRA vez (quando a coluna é criada): marca como obrigatórios
--      os postos TS que têm a linha 2023 ou a 1783. Depois disso quem manda
--      é a aba Postos (caixa "Obrigatório no fim de semana") — rodar de novo
--      NÃO desfaz o que o coordenador marcou ou desmarcou.
--
-- IDEMPOTENTE: pode rodar duas vezes.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 049-escala-fiscais-fechamento-fds.sql
-- ============================================================================

BEGIN;

SET search_path TO coordenadoria, public;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'coordenadoria' AND table_name = 'escala_fiscal_posto'
          AND column_name = 'obrigatorio_fds'
    ) THEN
        ALTER TABLE coordenadoria.escala_fiscal_posto
            ADD COLUMN obrigatorio_fds BOOLEAN NOT NULL DEFAULT false;

        UPDATE coordenadoria.escala_fiscal_posto p
           SET obrigatorio_fds = true
         WHERE p.lado = 'TS'
           AND EXISTS (
               SELECT 1 FROM coordenadoria.escala_fiscal_posto_linha pl
                WHERE pl.posto_id = p.id
                  AND (pl.linha LIKE '2023%' OR pl.linha LIKE '1783%')
           );
        RAISE NOTICE '049: coluna obrigatorio_fds criada; % posto(s) TS marcado(s) como rendição.',
            (SELECT count(*) FROM coordenadoria.escala_fiscal_posto WHERE obrigatorio_fds);
    ELSE
        RAISE NOTICE '049: obrigatorio_fds já existe — nada alterado.';
    END IF;
END $$;

COMMENT ON COLUMN coordenadoria.escala_fiscal_posto.obrigatorio_fds IS
    'Fechamento automático do fim de semana: TS que não pode ficar sem fiscal no sábado e no domingo (rendições). TP é sempre obrigatório, com ou sem a marca.';

-- 🔴 GRANT (a falta de GRANT derrubou produção em 17/09)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA coordenadoria TO sambaiba;

COMMIT;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT p.lado, string_agg(pl.linha, ' ' ORDER BY pl.ordem) AS linhas
--     FROM coordenadoria.escala_fiscal_posto p
--     JOIN coordenadoria.escala_fiscal_posto_linha pl ON pl.posto_id = p.id
--    WHERE p.obrigatorio_fds GROUP BY p.id, p.lado;
--   -- esperado: os TS das rendições (2023-10 2023-41 · 1783-10 1764-10)
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE coordenadoria.escala_fiscal_posto DROP COLUMN IF EXISTS obrigatorio_fds;
-- ============================================================================
