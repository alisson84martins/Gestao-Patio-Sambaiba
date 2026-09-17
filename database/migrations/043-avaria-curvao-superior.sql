-- ============================================================================
-- MIGRATION 043 — Vocabulário do controlador: curvão superior (lateral)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria (existente — migration 024)
-- DATA:   2026-09-17
-- AUTOR:  Claude Code
-- DEPENDE DE: 042-avaria-mapa-e-deduplicacao.sql (tabela portaria.avaria_zona)
-- ORIGEM: decisão do Alisson em 17/09 — a tela de avaria só tem o que o
--   CONTROLADOR DE ACESSO enxerga do chão, com o motorista na frente
--   (INTERNO/TETO saem da tela, itens 3a/3b da correção de 17/09 — sem
--   migration, é só frontend). Este arquivo cobre só o item 3c: duas zonas
--   novas que a garagem chama de "curvão superior" — a quina de CIMA de
--   cada lateral (acima da janela, subindo até o teto), distinta dos
--   curvões dianteiro/traseiro que a 042 já tem (esses ficam com o nome
--   como está).
-- ----------------------------------------------------------------------------
-- 🟢 PURAMENTE ADITIVA — dois INSERT em portaria.avaria_zona, mesmo padrão
--   da 042 (ordem intercalada, sem reordenar o que já existe). Nenhum DROP,
--   nenhum RENAME, nenhuma tabela nova.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 043-avaria-curvao-superior.sql
-- ============================================================================

SET search_path TO portaria, public;

INSERT INTO portaria.avaria_zona (codigo, nome, vista, regiao_ocorrencia, requer_caracteristica, ordem, ativo) VALUES
    -- vocabulário garagem: o "curvão superior" é a quina de CIMA de cada
    -- lateral (acima da janela, subindo até o teto) — diferente do
    -- CURVAO_DIANT_*/CURVAO_TRAS_* já existentes (essas são as quinas do
    -- para-choque, dianteira/traseira; ⛔ não mudam de nome). Ordem 166/216:
    -- logo depois do VIGIA_LAT_* de cada lado (165/215), sem reordenar nada.
    ('CURVAO_SUP_ESQ', 'Curvão superior esquerdo', 'LATERAL_ESQ', 'LATERAL_ESQUERDA', NULL, 166, TRUE),
    ('CURVAO_SUP_DIR', 'Curvão superior direito',  'LATERAL_DIR', 'LATERAL_DIREITA',  NULL, 216, TRUE)
ON CONFLICT (codigo) DO NOTHING;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- As duas zonas novas existem, ativas, na lateral certa:
--   SELECT codigo, nome, vista FROM portaria.avaria_zona
--    WHERE codigo IN ('CURVAO_SUP_ESQ', 'CURVAO_SUP_DIR');  -- esperado: 2 linhas
--
--   -- Os curvões existentes NÃO mudaram de nome:
--   SELECT codigo, nome FROM portaria.avaria_zona
--    WHERE codigo IN ('CURVAO_DIANT_ESQ', 'CURVAO_DIANT_DIR', 'CURVAO_TRAS_ESQ', 'CURVAO_TRAS_DIR');
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ⚠️ Só reverter se nenhuma avaria real usar essas zonas ainda (avaria_saida
-- referencia avaria_zona por FK — o DELETE abaixo falha se houver uso, o que
-- é o comportamento certo: não apagar zona em uso).
-- DELETE FROM portaria.avaria_zona WHERE codigo IN ('CURVAO_SUP_ESQ', 'CURVAO_SUP_DIR');
-- ============================================================================
