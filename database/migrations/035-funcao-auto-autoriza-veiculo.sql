-- ============================================================================
-- MIGRATION 035 — Gestor cadastrado entra sem passar por autorização
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: public (funcao)
-- DATA:   2026-08-24
-- AUTOR:  Claude Code
-- DEPENDE DE: nenhuma (funcao já existe desde a 001/007-seeds)
-- ORIGEM: _handoff-claude/PROMPT-portaria-ajustes-2026-08-24.md, Bloco D
-- ----------------------------------------------------------------------------
-- ⚠️ NÚMERO — os arquivos vão até 034 (a 037 já existe, reservada para o
--   Bloco I; esta e a 036 nascem depois dela no relógio mas são
--   independentes — rodar em ordem numérica 034→035→036→037 funciona).
--
-- 🟢 100% ADITIVA — só ADD COLUMN (idempotente) e um UPDATE de valor.
--   Nenhum DROP TABLE, DROP COLUMN nem DROP SCHEMA.
--
-- POR QUÊ
--   Hoje todo veículo cadastrado nasce PENDENTE (D6 — quem cadastra não é
--   quem autoriza). Faz sentido pro controlador da guarita, mas não pra
--   gerente/encarregado/coordenador já cadastrados no sistema: o sistema já
--   sabe quem a pessoa é, esperar autorização não protege nada — é como
--   exigir crachá de visitante de quem já tem crachá da casa.
--
-- ⛔ A REGRA É DADO, NÃO CÓDIGO — de propósito. Hard-codar a lista de
--   funções no Python significa que amanhã entra "Encarregado de Tráfego" e
--   ninguém lembra de editar o código. `veiculo_auto_autorizado` vira coluna
--   de funcao; o backend (routers/portaria_veiculos.py::cadastrar_veiculo)
--   só lê o dado.
--
-- LISTA CONFIRMADA PELO ALISSON (24/08): GERENTE_GERAL, GERENTE_OPERACIONAL,
--   ENCARREGADO, COORDENADOR_TRAFEGO, ADMIN — "gerente, encarregados e
--   coordenadores".
--
-- ⚠️ DADO PESSOAL: nenhum nesta migration.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 035-funcao-auto-autoriza-veiculo.sql
-- ============================================================================

ALTER TABLE funcao ADD COLUMN IF NOT EXISTS veiculo_auto_autorizado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN funcao.veiculo_auto_autorizado IS 'TRUE = veículo PARTICULAR de quem tem esta função nasce AUTORIZADO na portaria, sem passar por PENDENTE. Cargo de gestão responde pelo próprio carro.';

UPDATE funcao SET veiculo_auto_autorizado = TRUE
 WHERE codigo IN ('GERENTE_GERAL', 'GERENTE_OPERACIONAL', 'ENCARREGADO', 'COORDENADOR_TRAFEGO', 'ADMIN');

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- A coluna existe, não aceita NULL, default FALSE:
--   SELECT column_name, is_nullable, column_default FROM information_schema.columns
--    WHERE table_name = 'funcao' AND column_name = 'veiculo_auto_autorizado';
--
--   -- As cinco funções esperadas, e só elas:
--   SELECT codigo FROM funcao WHERE veiculo_auto_autorizado = TRUE ORDER BY codigo;
--   -- esperado: ADMIN, COORDENADOR_TRAFEGO, ENCARREGADO, GERENTE_GERAL, GERENTE_OPERACIONAL
--
--   -- Rodar o arquivo inteiro DUAS VEZES não erra (idempotência).
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE funcao DROP COLUMN IF EXISTS veiculo_auto_autorizado;
-- ============================================================================
