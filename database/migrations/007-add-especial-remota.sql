-- =====================================================================
-- Migration 007 — Adiciona valor ESPECIAL_REMOTA ao tipo_fila_enum
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Contexto: filas "remotas" representam carros que NÃO estão fisicamente
-- na garagem (Noturno, Reservados — saíram pra rodar e voltam depois).
-- O motorista enxerga essas posições como qualquer outra ao perguntar
-- "onde está meu carro?", mas o KPI "Frota no pátio" não as conta.
--
-- Rode CONECTADO no banco gestao_patio_sambaiba.
-- IMPORTANTE: rodar ESTA migration COMPLETA antes do seed 07.
--
-- PEGADINHA DO POSTGRESQL:
--   ALTER TYPE ... ADD VALUE precisa ser COMMITADO antes do valor poder
--   ser referenciado por outros statements. Por isso o COMMIT explícito
--   na linha abaixo. Se rodar via pgAdmin Query Tool, executar em DOIS
--   batches (F5 separados): primeiro o ALTER TYPE, depois o resto.
-- =====================================================================

SET client_encoding = 'UTF8';
SET timezone = 'America/Sao_Paulo';

-- ---------------------------------------------------------------------
-- 1. Adiciona o novo valor ao enum existente
-- ---------------------------------------------------------------------
ALTER TYPE tipo_fila_enum ADD VALUE IF NOT EXISTS 'ESPECIAL_REMOTA';
COMMIT;

-- ---------------------------------------------------------------------
-- 2. Atualiza o CHECK constraint da tabela fila pra aceitar o novo tipo
-- ---------------------------------------------------------------------
-- Constraint atual permite numero apenas para NUMERICA; ESPECIAL e
-- MANUTENCAO ficam com numero NULL. ESPECIAL_REMOTA segue a mesma regra
-- de ESPECIAL (sem numero).
ALTER TABLE fila DROP CONSTRAINT IF EXISTS chk_fila_numerica;

ALTER TABLE fila ADD CONSTRAINT chk_fila_numerica CHECK (
    (tipo = 'NUMERICA' AND numero IS NOT NULL AND numero BETWEEN 1 AND 33)
    OR (tipo IN ('ESPECIAL', 'ESPECIAL_REMOTA', 'MANUTENCAO') AND numero IS NULL)
);

-- =====================================================================
-- FIM da migration 007
-- =====================================================================
