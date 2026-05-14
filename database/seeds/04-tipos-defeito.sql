-- =====================================================================
-- Seed 04 — Tipos de defeito
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================

INSERT INTO tipo_defeito (codigo, nome, categoria, ativo) VALUES
    -- Mecânica
    ('MEC_MOTOR',     'Motor',                'mecanica', TRUE),
    ('MEC_FREIO',     'Freios',               'mecanica', TRUE),
    ('MEC_SUSPENSAO', 'Suspensão',            'mecanica', TRUE),
    ('MEC_DIRECAO',   'Direção',              'mecanica', TRUE),
    ('MEC_CAMBIO',    'Câmbio / Transmissão', 'mecanica', TRUE),
    ('MEC_EMBREAGEM', 'Embreagem',            'mecanica', TRUE),
    ('MEC_ESCAPE',    'Escapamento',          'mecanica', TRUE),

    -- Elétrica
    ('ELE_BATERIA',   'Bateria',              'eletrica', TRUE),
    ('ELE_PARTIDA',   'Motor de partida',     'eletrica', TRUE),
    ('ELE_ALTERN',    'Alternador',           'eletrica', TRUE),
    ('ELE_FAROL',     'Faróis / Lanternas',   'eletrica', TRUE),
    ('ELE_PAINEL',    'Painel / Instrumentos','eletrica', TRUE),
    ('ELE_FIACAO',    'Fiação',               'eletrica', TRUE),

    -- Ar-condicionado
    ('AC_GERAL',      'Ar-condicionado',      'ar', TRUE),
    ('AC_GAS',        'Recarga de gás',       'ar', TRUE),

    -- Lataria
    ('LAT_PARACHO',   'Pára-choque',          'lataria', TRUE),
    ('LAT_RETROVISOR','Retrovisor',           'lataria', TRUE),
    ('LAT_PORTA',     'Porta',                'lataria', TRUE),
    ('LAT_VIDRO',     'Vidro / Janela',       'lataria', TRUE),
    ('LAT_PINTURA',   'Pintura',              'lataria', TRUE),

    -- Pneus
    ('PNE_FURO',      'Pneu furado',          'pneus', TRUE),
    ('PNE_TROCA',     'Troca de pneu',        'pneus', TRUE),
    ('PNE_CALIBRA',   'Calibragem',           'pneus', TRUE),

    -- Interno (passageiros)
    ('INT_BANCO',     'Banco / Estofamento',  'interno', TRUE),
    ('INT_VALIDADOR', 'Validador',            'interno', TRUE),
    ('INT_CAMERA',    'Câmera de segurança',  'interno', TRUE),

    -- Outros
    ('OUT_LAVAGEM',   'Lavagem',              'outros', TRUE),
    ('OUT_LIMPEZA',   'Limpeza interna',      'outros', TRUE),
    ('OUT_OUTROS',    'Outros',               'outros', TRUE);
