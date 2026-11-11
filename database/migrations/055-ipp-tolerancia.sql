-- ============================================================================
-- MIGRATION 055 — IPP = pontualidade de partida (tolerância em parâmetro)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: fiscalizacao (tabela parametro, migration 030)
-- DATA:   2026-11-11
-- AUTOR:  Claude
-- DEPENDE DE: 030 (fiscalizacao.parametro)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Regra do Alisson (11/11/2026 00:58): partida pontual = saída real dentro
--   de ±3 min do programado; no horário cheio (07:00, 08:00…) só de 0 a +3
--   min — sair antes perde o ICV da faixa seguinte. IPP = pontuais ÷
--   programadas. A tolerância fica em fiscalizacao.parametro (D29: nunca
--   número solto no código); sem esta linha o backend usa 3.
--
--   Só um INSERT de configuração. Sem dado pessoal. Sem tabela nova (o
--   GRANT da 030 já cobre fiscalizacao.parametro).
--
-- IDEMPOTENTE: pode rodar duas vezes (ON CONFLICT DO NOTHING).
-- COMO RODAR (copie para /tmp antes: o usuário postgres não lê /root):
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f /tmp/055-ipp-tolerancia.sql
-- ============================================================================

BEGIN;

INSERT INTO fiscalizacao.parametro (chave, valor, descricao) VALUES
    ('ipp_tolerancia_min', 3.00,
     'IPP: tolerância da partida pontual, em minutos, para mais e para menos. No horário cheio (HH:00) não pode sair antes — só de 0 a +tolerância.')
ON CONFLICT (chave) DO NOTHING;

COMMIT;
