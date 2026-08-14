-- ============================================================================
-- MIGRATION 023 — Nº B.O. da SPTrans na ocorrência
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: coordenadoria
-- DATA:   2026-08-14
-- AUTOR:  Claude Code (PROMPT-correcoes-pre-ocorrencia.md, Item 8)
-- DEPENDE DE: 012-modulo-coordenadoria-ocorrencias.sql (tabela ocorrencia)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   A SPTrans emite um número de B.O. próprio (órgão regulador da empresa),
--   diferente do numero_bo da polícia que a ocorrência já grava. Sem campo
--   pra isso, o Alisson anotou o número na observação de uma autoridade numa
--   ocorrência real — decisão com ele (12/08): campo fixo da ocorrência,
--   NÃO um campo novo no card de autoridade ("não costumamos pegar números
--   de documentos de autoridades").
--
-- ADIÇÃO PURA — nenhuma tabela existente é alterada além desta coluna.
-- ⛔ Sem NOT NULL — toda ocorrência já registrada ficaria inválida.
--
-- NATUREZA: idempotente (ADD COLUMN IF NOT EXISTS).
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table ocorrencia", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 023-bo-sptrans.sql
-- ============================================================================

ALTER TABLE coordenadoria.ocorrencia
    ADD COLUMN IF NOT EXISTS numero_bo_sptrans VARCHAR(40);

COMMENT ON COLUMN coordenadoria.ocorrencia.numero_bo_sptrans IS
    'Nº do B.O. emitido pela SPTrans (diferente de numero_bo, que é da polícia). Campo fixo da ocorrência, exibido na tela só quando há autoridade SPTrans na lista — mas o valor nunca é apagado quando o campo some (ver ocorrencia.form.js).';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
-- \d coordenadoria.ocorrencia
-- -- esperado: coluna numero_bo_sptrans, VARCHAR(40), nullable
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE coordenadoria.ocorrencia DROP COLUMN IF EXISTS numero_bo_sptrans;
-- ============================================================================
