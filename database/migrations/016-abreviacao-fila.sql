-- ============================================================================
-- MIGRATION 016 — Abreviação curta para filas especiais
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- DATA:   2026-07-31
-- AUTOR:  Alisson Martins
-- DEPENDE DE: 001-create-database.sql (tabela fila)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   As posições especiais têm nome (Coqueiro, Elétricos...) e no impresso
--   o nome inteiro transborda pra coluna vizinha. Os motoristas já usam
--   apelidos curtos no dia a dia — esta coluna guarda esse apelido pra
--   telas e impressos com espaço apertado. Quando nula, quem exibe usa o
--   `nome` normalmente (fallback, não obrigatório preencher pra toda fila).
--
-- NATUREZA: aditiva. Idempotente — ADD COLUMN IF NOT EXISTS e
-- UPDATE...WHERE por código, pode rodar de novo sem duplicar nem sobrescrever
-- uma abreviação diferente que tenha sido ajustada manualmente depois
-- (usa nome como chave, então só toca nas 6 linhas que já existem).
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 016-abreviacao-fila.sql
-- ============================================================================

ALTER TABLE fila ADD COLUMN IF NOT EXISTS abreviacao VARCHAR(6);

COMMENT ON COLUMN fila.abreviacao IS
    'Rótulo curto para espaços apertados (impressão, chips). Quando nulo, quem exibe usa o nome.';

-- ----------------------------------------------------------------------------
-- Seed das 6 posições especiais — valores confirmados pelo Alisson (31/07/2026).
-- Filas numéricas não precisam: o próprio número já é curto.
-- ----------------------------------------------------------------------------

UPDATE fila SET abreviacao = 'COQ'   WHERE nome = 'Coqueiro'  AND abreviacao IS NULL;
UPDATE fila SET abreviacao = 'LAJE'  WHERE nome = 'Laje'      AND abreviacao IS NULL;
UPDATE fila SET abreviacao = 'LAV'   WHERE nome = 'Lavador'   AND abreviacao IS NULL;
UPDATE fila SET abreviacao = 'BOMBA' WHERE nome = 'Bomba'     AND abreviacao IS NULL;
UPDATE fila SET abreviacao = 'ELE'   WHERE nome = 'Elétricos' AND abreviacao IS NULL;
UPDATE fila SET abreviacao = 'FUN'   WHERE nome = 'Fundão'    AND abreviacao IS NULL;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT nome, abreviacao FROM fila WHERE abreviacao IS NOT NULL ORDER BY nome;
--   -- esperado: 6 linhas (Coqueiro/COQ, Laje/LAJE, Lavador/LAV, Bomba/BOMBA,
--   --            Elétricos/ELE, Fundão/FUN)
--
--   Alguma das 6 posições especiais não existe com esse nome exato no banco
--   (sintoma de nome cadastrado diferente do esperado; esperado: 0 linhas):
--     SELECT nome FROM (VALUES ('Coqueiro'),('Laje'),('Lavador'),('Bomba'),
--                               ('Elétricos'),('Fundão')) AS esperado(nome)
--      WHERE nome NOT IN (SELECT nome FROM fila);
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE fila DROP COLUMN IF EXISTS abreviacao;
-- (Seguro: nenhuma outra tabela depende dela, é só exibição.)
-- ============================================================================
