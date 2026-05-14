-- =====================================================================
-- Seed 03 — Setor de manutenção
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Setor de manutenção como tipo de fila. Permite expansão futura para
-- sub-setores (Mecânica, Lataria, Elétrica, Ar-Condicionado).
-- =====================================================================

INSERT INTO fila (tipo, numero, nome, ordem_exibicao, ativa) VALUES
    ('MANUTENCAO', NULL, 'Manutenção', 200, TRUE);

-- Sub-setores opcionais (descomente quando precisar):
--    ('MANUTENCAO', NULL, 'Mecânica',       201, TRUE),
--    ('MANUTENCAO', NULL, 'Lataria',        202, TRUE),
--    ('MANUTENCAO', NULL, 'Elétrica',       203, TRUE),
--    ('MANUTENCAO', NULL, 'Ar-Condicionado',204, TRUE);
