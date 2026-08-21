-- ============================================================================
-- MIGRATION 025 — Portaria: credencial (QR) do veículo — Bloco E
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria  (existente — migration 024)
-- DATA:   2026-08-21
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (portaria.veiculo, public.funcionario)
-- ORIGEM: _handoff-claude/PROMPT-portaria-blocos-E-F.md, Bloco E
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Adesivo QR no para-brisa é ACELERAÇÃO da busca por placa, nunca
--   substituto dela — câmera pode falhar, adesivo pode descolar, e o campo
--   de busca continua funcionando igual. Também não é credencial de
--   segurança: adesivo em vidro é fotografável e recolável, então a leitura
--   cai no MESMO card de confirmação da busca por placa e quem decide
--   continua sendo o controlador olhando pro carro.
--
-- 🔴 O QUE VAI DENTRO DO QR — só o `codigo`, token opaco
--   (secrets.token_urlsafe(16), gerado pelo backend). NUNCA placa, RE, nome,
--   CPF ou URL com dado nenhum: adesivo em para-brisa é público, e com
--   placa/RE dentro o adesivo vira vazamento colado no vidro do carro (LGPD)
--   além de virar clonável (qualquer um reimprime o QR de qualquer placa).
--
-- TABELA, NÃO COLUNA EM portaria.veiculo
--   Adesivo descola, desbota, o carro é vendido — reemissão é rotina, e o
--   código antigo precisa parar de valer SEM apagar o registro de que
--   existiu (quem emitiu, quando, por que foi revogado).
--
-- ⚠️ Esta migration NÃO usa SET search_path — todo objeto é qualificado
--   explicitamente com o schema (portaria./public.). A migration 024 usou
--   SET search_path e uma contaminação de sessão jogou uma view no schema
--   errado, custando uma hora de diagnóstico; aqui não corre esse risco
--   porque não há sessão pra contaminar.
--
-- NATUREZA: 100% ADITIVA E IDEMPOTENTE.
--   CREATE TABLE/INDEX IF NOT EXISTS. Nenhum ALTER TABLE, DROP ou RENAME em
--   tabela existente. Nenhuma view existente é tocada. Nenhum RBAC novo —
--   os endpoints do Bloco E reusam os recursos que a 024 já criou
--   (veiculo_portaria para emitir/revogar/imprimir, acesso_veicular para a
--   leitura pela portaria).
--
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 025-portaria-credencial.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS portaria.credencial (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    veiculo_id        UUID NOT NULL REFERENCES portaria.veiculo(id) ON DELETE CASCADE,
    tipo              VARCHAR(10) NOT NULL DEFAULT 'QR' CHECK (tipo IN ('QR','TAG','CARTAO')),
    codigo            VARCHAR(64) NOT NULL,
    emitida_em        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    emitida_por       UUID REFERENCES public.funcionario(id),
    revogada_em       TIMESTAMPTZ,
    revogada_por      UUID REFERENCES public.funcionario(id),
    motivo_revogacao  TEXT,
    ativa             BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE portaria.credencial IS 'QR/TAG/cartão do veículo (Bloco E). Tabela, não coluna em portaria.veiculo — adesivo descola, desbota, o carro é vendido, e reemissão é rotina; o código antigo precisa parar de valer sem apagar o registro de que existiu. NUNCA é credencial de segurança: adesivo em para-brisa é fotografável e recolável, então a leitura sempre cai no mesmo card de confirmação da busca por placa (D15) e quem decide é o controlador olhando pro carro.';
COMMENT ON COLUMN portaria.credencial.codigo IS 'Token opaco (secrets.token_urlsafe(16), gerado pelo backend) — NUNCA placa, RE, nome, CPF ou URL com dado nenhum. Adesivo em para-brisa é público; qualquer coisa legível ali seria dado pessoal colado no vidro (LGPD) e permitiria clonar o QR de outra placa.';
COMMENT ON COLUMN portaria.credencial.ativa IS 'FALSE = revogada (reemissão ou baixa). ⚠️ Não impede o veículo de entrar — isso é papel de portaria.veiculo.situacao. Credencial revogada é só "adesivo velho", não proibição.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_credencial_codigo ON portaria.credencial (codigo);
CREATE INDEX IF NOT EXISTS idx_credencial_veiculo ON portaria.credencial (veiculo_id) WHERE ativa;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT count(*) FROM portaria.credencial;  -- esperado: 0 (tabela nova, sem seed)
--
-- Prova de que o código nunca repete e nunca fica sem dono (esperado: 0 linhas):
--   SELECT codigo, count(*) FROM portaria.credencial GROUP BY codigo HAVING count(*) > 1;
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DROP TABLE IF EXISTS portaria.credencial;
-- (Seguro: nenhuma outra tabela referencia portaria.credencial.)
-- ============================================================================
