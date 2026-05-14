-- =====================================================================
-- Seed 06 — Usuário admin inicial e permissões base
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- IMPORTANTE: Substituir o senha_hash abaixo por um hash real.
-- Gerar com bcrypt no backend antes do primeiro deploy.
-- O hash de exemplo abaixo é APENAS placeholder — NÃO usar em produção.
-- =====================================================================

-- Usuário admin (você, Alisson)
INSERT INTO usuario (id, re, nome, senha_hash, perfil, ativo) VALUES (
    gen_random_uuid(),
    'ADMIN001',
    'Alisson Martins',
    '$2b$12$PLACEHOLDER_SUBSTITUIR_POR_HASH_REAL_BCRYPT_NO_DEPLOY',
    'ADMIN',
    TRUE
);

-- =====================================================================
-- PERMISSÕES BASE POR PERFIL (matriz de referência)
-- =====================================================================
-- Recursos do sistema:
--   - alocacao         → mover ônibus entre filas/posições
--   - escala           → cadastrar/editar escala
--   - escala_importar  → fazer upload de planilha
--   - alerta           → registrar/resolver PRESO e AMOSTRAL
--   - manutencao       → abrir/atualizar fichas
--   - frota            → cadastrar/editar ônibus
--   - motorista        → cadastrar/editar motoristas
--   - linha            → cadastrar/editar linhas
--   - usuario          → cadastrar/editar usuários
--   - permissao        → gerenciar permissões
--   - relatorio        → ver relatórios e analytics
-- =====================================================================

-- Concede todas as permissões ao admin recém-criado
DO $$
DECLARE
    v_admin_id UUID;
    v_recurso  TEXT;
    v_recursos TEXT[] := ARRAY[
        'alocacao', 'escala', 'escala_importar', 'alerta',
        'manutencao', 'frota', 'motorista', 'linha',
        'usuario', 'permissao', 'relatorio'
    ];
BEGIN
    SELECT id INTO v_admin_id FROM usuario WHERE re = 'ADMIN001' LIMIT 1;

    IF v_admin_id IS NULL THEN
        RAISE EXCEPTION 'Usuário ADMIN001 não encontrado.';
    END IF;

    FOREACH v_recurso IN ARRAY v_recursos LOOP
        INSERT INTO permissao (usuario_id, recurso, pode_ler, pode_escrever, concedido_por)
        VALUES (v_admin_id, v_recurso, TRUE, TRUE, v_admin_id)
        ON CONFLICT (usuario_id, recurso) DO NOTHING;
    END LOOP;
END $$;

-- =====================================================================
-- TEMPLATES DE PERMISSÕES POR PERFIL (referência — não executa)
-- =====================================================================
/*
-- COORDENADOR (acesso total operacional, sem gerenciar usuários)
INSERT INTO permissao (usuario_id, recurso, pode_ler, pode_escrever) VALUES
    (:user_id, 'alocacao',        TRUE, TRUE),
    (:user_id, 'escala',          TRUE, TRUE),
    (:user_id, 'escala_importar', TRUE, TRUE),
    (:user_id, 'alerta',          TRUE, TRUE),
    (:user_id, 'manutencao',      TRUE, TRUE),
    (:user_id, 'frota',           TRUE, TRUE),
    (:user_id, 'motorista',       TRUE, TRUE),
    (:user_id, 'linha',           TRUE, FALSE),
    (:user_id, 'relatorio',       TRUE, FALSE);

-- OPERADOR_PATIO (foco no pátio)
INSERT INTO permissao (usuario_id, recurso, pode_ler, pode_escrever) VALUES
    (:user_id, 'alocacao',  TRUE, TRUE),
    (:user_id, 'escala',    TRUE, FALSE),
    (:user_id, 'alerta',    TRUE, TRUE),
    (:user_id, 'manutencao',TRUE, FALSE),
    (:user_id, 'frota',     TRUE, FALSE);

-- MOTORISTA (apenas consulta)
INSERT INTO permissao (usuario_id, recurso, pode_ler, pode_escrever) VALUES
    (:user_id, 'alocacao', TRUE, FALSE),
    (:user_id, 'escala',   TRUE, FALSE),
    (:user_id, 'frota',    TRUE, FALSE);

-- MECANICO (manutenção e consulta de pátio)
INSERT INTO permissao (usuario_id, recurso, pode_ler, pode_escrever) VALUES
    (:user_id, 'alocacao',   TRUE, FALSE),
    (:user_id, 'manutencao', TRUE, TRUE),
    (:user_id, 'frota',      TRUE, FALSE);
*/
