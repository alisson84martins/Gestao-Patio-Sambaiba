-- =====================================================================
-- Seed 02 — Posições especiais (6)
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Posições especiais do pátio. Para adicionar nova (ex.: Coqueirinho),
-- basta INSERIR uma nova linha aqui ou via tela administrativa.
-- =====================================================================

INSERT INTO fila (tipo, numero, nome, ordem_exibicao, ativa) VALUES
    ('ESPECIAL', NULL, 'Coqueiro',   101, TRUE),
    ('ESPECIAL', NULL, 'Laje',       102, TRUE),
    ('ESPECIAL', NULL, 'Lavador',    103, TRUE),
    ('ESPECIAL', NULL, 'Bomba',      104, TRUE),
    ('ESPECIAL', NULL, 'Elétricos',  105, TRUE),
    ('ESPECIAL', NULL, 'Fundão',     106, TRUE);
