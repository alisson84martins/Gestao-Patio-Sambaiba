-- =====================================================================
-- Seed 07 — Posições extras (2 físicas + 2 remotas)
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Coqueirinho e Ilha: posições físicas dentro da garagem (ESPECIAL).
-- Noturno e Reservados: carros que SAÍRAM pra rodar serviços que retornam
-- depois (ESPECIAL_REMOTA). Plantão noturno marca os carros como
-- alocados nessas filas antes do diurno chegar pra soltura.
--
-- Pré-requisito: migration 007 já aplicada.
-- =====================================================================

INSERT INTO fila (tipo, numero, nome, ordem_exibicao, ativa) VALUES
    -- Físicas (continuação das 6 especiais — ordem 101 a 106)
    ('ESPECIAL',         NULL, 'Coqueirinho', 107, TRUE),
    ('ESPECIAL',         NULL, 'Ilha',        108, TRUE),

    -- Remotas (faixa 300 — aparecem por último na ordenação global)
    ('ESPECIAL_REMOTA',  NULL, 'Noturno',     301, TRUE),
    ('ESPECIAL_REMOTA',  NULL, 'Reservados',  302, TRUE);
