-- ============================================================================
-- MIGRATION 019 — RG e CNH no cadastro central de funcionário
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- DATA:   2026-08-10
-- AUTOR:  Alisson Martins
-- DEPENDE DE: 011-rbac-cadastro-central.sql (tabela funcionario)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   funcionario tinha id, re, nome, cpf, telefone, status, data_admissao,
--   codigo_externo — sem rg nem cnh. O autopreenchimento da ocorrência
--   (endpoints /ocorrencias/autopreencher/pessoa) precisa desses dois campos
--   pra oferecer documento a partir do cadastro central antes de cair pra
--   última ocorrência como reserva. Sem a coluna, não tem o que devolver.
--
-- NATUREZA: aditiva. Idempotente — ADD COLUMN IF NOT EXISTS não falha se
-- rodada de novo, e não apaga dado já gravado por uma execução anterior.
-- Nenhum formato imposto: RG não tem padrão nacional (varia por estado,
-- alguns terminam em letra — ver mascaras.js), CNH tem 11 dígitos mas isso
-- é validado no frontend, não em CHECK constraint.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table funcionario", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 019-funcionario-rg-cnh.sql
-- ============================================================================

ALTER TABLE funcionario ADD COLUMN IF NOT EXISTS rg  VARCHAR(20);
ALTER TABLE funcionario ADD COLUMN IF NOT EXISTS cnh VARCHAR(20);

COMMENT ON COLUMN funcionario.rg IS
    'Sem máscara/formato imposto — RG varia por estado e alguns terminam em letra.';
COMMENT ON COLUMN funcionario.cnh IS
    '11 dígitos, validado no frontend (mascaras.js); aqui é só VARCHAR livre.';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   Colunas existem e com o tamanho esperado (esperado: 2 linhas, 20 cada):
--     SELECT column_name, character_maximum_length FROM information_schema.columns
--      WHERE table_schema = 'public' AND table_name = 'funcionario'
--        AND column_name IN ('rg', 'cnh');
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE funcionario DROP COLUMN IF EXISTS rg;
-- ALTER TABLE funcionario DROP COLUMN IF EXISTS cnh;
-- (Seguro: nenhuma outra tabela referencia essas colunas.)
-- ============================================================================
