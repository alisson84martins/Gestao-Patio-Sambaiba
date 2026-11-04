-- ============================================================================
-- MIGRATION 051 — Consulta de placa (veículo de terceiro na ocorrência)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: coordenadoria
-- DATA:   2026-11-04
-- AUTOR:  Claude
-- DEPENDE DE: 011-rbac-cadastro-central.sql (public.funcionario),
--             012-modulo-coordenadoria-ocorrencias.sql (schema coordenadoria)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   O coordenador consulta a placa do veículo de terceiro no fornecedor
--   consultadeplaca.net (pago por consulta, ~R$ 0,07). Esta tabela guarda
--   UMA LINHA POR CONSULTA PAGA (encontrada ou não encontrada) e serve a:
--     1. cache de 30 dias por placa (não paga duas vezes pela mesma placa);
--     2. limite diário de consultas (conta as linhas do dia em São Paulo);
--     3. histórico de gasto (preco).
--   Erro sem cobrança (402 sem saldo, 503, timeout) NÃO grava linha.
--
--   `dados` guarda só os campos do veículo usados no formulário (marca,
--   modelo, versão, anos, cor, combustível, tipo, município, UF).
--   ⛔ Sem indicador de roubo/furto e sem FIPE (D9 — decisão futura).
--   ⛔ O fornecedor não devolve dono/CPF/RENAVAM/chassi — nada disso aqui.
--
--   Não altera nenhuma tabela de ocorrência (D8).
--
-- IDEMPOTENTE: pode rodar duas vezes.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 051-consulta-placa.sql
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS coordenadoria.consulta_placa (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    placa           VARCHAR(10) NOT NULL,
    resultado       VARCHAR(12) NOT NULL
                    CONSTRAINT chk_consulta_placa_resultado
                    CHECK (resultado IN ('ENCONTRADA', 'NAO_ENCONTRADA')),
    dados           JSONB,
    preco           NUMERIC(6,2),
    consultado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    consultado_por  UUID REFERENCES public.funcionario(id)
);

COMMENT ON TABLE coordenadoria.consulta_placa IS
    'Uma linha por consulta PAGA de placa (consultadeplaca.net): cache de 30 dias, limite diário e histórico de gasto. Erro sem cobrança não grava. Sem roubo/furto e sem FIPE.';
COMMENT ON COLUMN coordenadoria.consulta_placa.consultado_em IS
    'Momento da consulta, gravado pelo sistema em UTC. O limite diário conta pelo dia civil de America/Sao_Paulo.';

CREATE INDEX IF NOT EXISTS idx_consulta_placa_placa_data
    ON coordenadoria.consulta_placa (placa, consultado_em DESC);

-- Limite diário conta as linhas do dia independentemente da placa.
CREATE INDEX IF NOT EXISTS idx_consulta_placa_data
    ON coordenadoria.consulta_placa (consultado_em DESC);

-- 🔴 Tabela nova nasce sem permissão para o papel da aplicação — sem isto a
-- consulta quebra só em produção.
GRANT SELECT, INSERT, UPDATE, DELETE ON coordenadoria.consulta_placa TO sambaiba;

COMMIT;
