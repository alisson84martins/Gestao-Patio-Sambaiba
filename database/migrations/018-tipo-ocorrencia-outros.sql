-- ============================================================================
-- MIGRATION 018 — Descrição livre para o tipo de ocorrência "Outros"
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- DATA:   2026-08-01
-- AUTOR:  Alisson Martins
-- DEPENDE DE: 012-modulo-coordenadoria-ocorrencias.sql (tabela ocorrencia)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Os 12 tipos de coordenadoria.tipo_ocorrencia são o catálogo oficial —
--   espelham o formulário de papel e não mudam por pedido de tela. Mas
--   algumas situações de campo não cabem em nenhum dos 12; até aqui, ao
--   escolher "Outros" não havia onde o coordenador registrar qual era.
--   Esta coluna guarda esse texto — do REGISTRO, não do catálogo. Nenhuma
--   linha nova entra em tipo_ocorrencia por causa disso.
--
-- NATUREZA: aditiva. Idempotente — ADD COLUMN IF NOT EXISTS não falha se
-- rodada de novo, e não apaga texto já gravado por uma execução anterior.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 018-tipo-ocorrencia-outros.sql
-- ============================================================================

ALTER TABLE coordenadoria.ocorrencia
    ADD COLUMN IF NOT EXISTS tipo_outros_descricao VARCHAR(120);

COMMENT ON COLUMN coordenadoria.ocorrencia.tipo_outros_descricao IS
    'Preenchido só quando tipo_ocorrencia.codigo = ''OUTROS''. Guarda o tipo que o coordenador escreveu à mão — texto do registro, não do catálogo oficial.';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   Coluna existe e com o tamanho esperado (esperado: 1 linha, 120):
--     SELECT character_maximum_length FROM information_schema.columns
--      WHERE table_schema = 'coordenadoria' AND table_name = 'ocorrencia'
--        AND column_name = 'tipo_outros_descricao';
--
--   Nenhuma ocorrência com descrição preenchida num tipo que não é OUTROS
--   (sintoma de bug no formulário; esperado: 0 linhas):
--     SELECT o.numero, t.codigo
--       FROM coordenadoria.ocorrencia o
--       JOIN coordenadoria.tipo_ocorrencia t ON t.id = o.tipo_ocorrencia_id
--      WHERE o.tipo_outros_descricao IS NOT NULL AND t.codigo <> 'OUTROS';
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE coordenadoria.ocorrencia DROP COLUMN IF EXISTS tipo_outros_descricao;
-- (Seguro: nenhuma outra tabela depende dela, é só texto do registro.)
-- ============================================================================
