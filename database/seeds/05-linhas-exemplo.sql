-- =====================================================================
-- Seed 05 — Linhas (exemplos iniciais)
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Catálogo fechado. Cadastre as linhas reais da Garagem 3 antes de
-- importar a primeira escala. Linhas E2 só rodam em frota 1xxx;
-- linhas AR2 só rodam em frota 2xxx.
-- =====================================================================
-- IMPORTANTE: Substituir os exemplos abaixo pelas linhas REAIS da
-- Garagem 3. Os códigos abaixo são fictícios e servem apenas de modelo.
-- =====================================================================

-- ----- Linhas E2 (frota 1xxx) -----
INSERT INTO linha (codigo, nome, setor, ativa) VALUES
    ('8500-10', 'Term. Sapopemba — Term. Princesa Isabel',  'E2', TRUE),
    ('5106-21', 'Jd. Aracati — Term. Lapa',                 'E2', TRUE),
    ('273J-10', 'Cidade Tiradentes — Term. Sacomã',         'E2', TRUE),
    ('177H-10', 'Sacomã — Pq. Dom Pedro II',                'E2', TRUE);

-- ----- Linhas AR2 (frota 2xxx) -----
INSERT INTO linha (codigo, nome, setor, ativa) VALUES
    ('938P-10', 'Vila Carrão — Term. Pq. Dom Pedro',        'AR2', TRUE),
    ('407M-10', 'Itaim Paulista — Term. Itaquera',          'AR2', TRUE),
    ('351A-10', 'Cohab Itaquera I — Sé',                    'AR2', TRUE),
    ('342J-10', 'Cohab José Bonifácio — Term. São Mateus',  'AR2', TRUE);

-- =====================================================================
-- Para adicionar uma nova linha em produção, use a tela administrativa
-- ou rode: INSERT INTO linha (codigo, nome, setor, ativa) VALUES (...);
-- =====================================================================
