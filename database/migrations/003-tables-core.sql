-- =====================================================================
-- Migration 003 — Tabelas do Núcleo Operacional (8 tabelas)
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Ordem importa: tabelas referenciadas vêm antes das que referenciam.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 2. MOTORISTA — cadastro operacional (criada antes de USUARIO por causa da FK)
-- ---------------------------------------------------------------------
CREATE TABLE motorista (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    re              VARCHAR(20)  NOT NULL UNIQUE,
    nome            VARCHAR(120) NOT NULL,
    cpf             VARCHAR(14),
    status          status_motorista_enum NOT NULL DEFAULT 'ATIVO',
    codigo_externo  VARCHAR(50),

    -- Auditoria
    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    criado_por      UUID,
    atualizado_em   TIMESTAMPTZ,
    atualizado_por  UUID
);

COMMENT ON TABLE motorista IS 'Cadastro de motoristas. Pode existir sem virar usuário do sistema.';
COMMENT ON COLUMN motorista.re IS 'Registro de funcionário';
COMMENT ON COLUMN motorista.codigo_externo IS 'Reservado para integração futura (Nimer/ERP)';

-- ---------------------------------------------------------------------
-- 1. USUARIO — quem loga no sistema
-- ---------------------------------------------------------------------
CREATE TABLE usuario (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    re              VARCHAR(20)  NOT NULL UNIQUE,
    nome            VARCHAR(120) NOT NULL,
    senha_hash      VARCHAR(255) NOT NULL,
    perfil          perfil_usuario_enum NOT NULL,
    ativo           BOOLEAN      NOT NULL DEFAULT TRUE,
    motorista_id   UUID         REFERENCES motorista(id) ON DELETE SET NULL,
    ultimo_acesso   TIMESTAMPTZ,

    -- Auditoria
    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    criado_por      UUID,
    atualizado_em   TIMESTAMPTZ,
    atualizado_por  UUID
);

COMMENT ON TABLE usuario IS 'Usuários do sistema (coordenadores, operadores, motoristas-consulta, mecânicos, admin).';
COMMENT ON COLUMN usuario.motorista_id IS 'FK opcional: liga o usuário ao motorista correspondente quando aplicável.';

-- FKs de auditoria circulares (resolvidas após criar usuario)
ALTER TABLE motorista
    ADD CONSTRAINT fk_motorista_criado_por
    FOREIGN KEY (criado_por) REFERENCES usuario(id) ON DELETE SET NULL;
ALTER TABLE motorista
    ADD CONSTRAINT fk_motorista_atualizado_por
    FOREIGN KEY (atualizado_por) REFERENCES usuario(id) ON DELETE SET NULL;
ALTER TABLE usuario
    ADD CONSTRAINT fk_usuario_criado_por
    FOREIGN KEY (criado_por) REFERENCES usuario(id) ON DELETE SET NULL;
ALTER TABLE usuario
    ADD CONSTRAINT fk_usuario_atualizado_por
    FOREIGN KEY (atualizado_por) REFERENCES usuario(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- 3. ONIBUS — frota da Garagem 3
-- ---------------------------------------------------------------------
CREATE TABLE onibus (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    numero_frota    INTEGER      NOT NULL UNIQUE,
    placa           VARCHAR(10),
    -- Setor é coluna gerada a partir do prefixo da frota
    setor           setor_enum   GENERATED ALWAYS AS (
                        CASE
                            WHEN numero_frota BETWEEN 1000 AND 1999 THEN 'E2'::setor_enum
                            WHEN numero_frota BETWEEN 2000 AND 2999 THEN 'AR2'::setor_enum
                        END
                    ) STORED,
    status          status_onibus_enum NOT NULL DEFAULT 'ATIVO',
    codigo_externo  VARCHAR(50),

    -- Auditoria
    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    criado_por      UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    atualizado_em   TIMESTAMPTZ,
    atualizado_por  UUID         REFERENCES usuario(id) ON DELETE SET NULL,

    -- Validações
    CONSTRAINT chk_onibus_numero_frota CHECK (numero_frota BETWEEN 1000 AND 9999),
    CONSTRAINT chk_onibus_setor_valido CHECK (
        numero_frota BETWEEN 1000 AND 1999 OR
        numero_frota BETWEEN 2000 AND 2999
    )
);

COMMENT ON TABLE onibus IS 'Frota de ônibus da Garagem 3.';
COMMENT ON COLUMN onibus.setor IS 'Coluna GERADA: E2 para frota 1xxx, AR2 para frota 2xxx';
COMMENT ON COLUMN onibus.codigo_externo IS 'Reservado para integração futura (Nimer/ERP)';

-- ---------------------------------------------------------------------
-- 4. FILA — 33 numéricas + posições especiais + manutenção
-- ---------------------------------------------------------------------
CREATE TABLE fila (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo              tipo_fila_enum NOT NULL,
    numero            INTEGER,
    nome              VARCHAR(50)  NOT NULL,
    ordem_exibicao    INTEGER      NOT NULL DEFAULT 0,
    ativa             BOOLEAN      NOT NULL DEFAULT TRUE,

    criado_em         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Validações
    CONSTRAINT chk_fila_numerica CHECK (
        (tipo = 'NUMERICA' AND numero IS NOT NULL AND numero BETWEEN 1 AND 33)
        OR (tipo IN ('ESPECIAL', 'MANUTENCAO') AND numero IS NULL)
    )
);

COMMENT ON TABLE fila IS 'Filas e posições do pátio. Manutenção é tipo de fila.';
COMMENT ON COLUMN fila.numero IS 'Apenas para tipo NUMERICA (1 a 33). NULL para ESPECIAL e MANUTENCAO.';

-- ---------------------------------------------------------------------
-- 5. ALOCACAO_PATIO — histórico de movimentações (coluna ativa marca atual)
-- ---------------------------------------------------------------------
CREATE TABLE alocacao_patio (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    onibus_id         UUID         NOT NULL REFERENCES onibus(id) ON DELETE CASCADE,
    fila_id           UUID         NOT NULL REFERENCES fila(id) ON DELETE RESTRICT,
    posicao           INTEGER      NOT NULL,
    alocado_por       UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    alocado_em        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ativa             BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Sincronização offline-first
    sincronizado_em   TIMESTAMPTZ,
    versao            INTEGER      NOT NULL DEFAULT 1,

    -- Auditoria
    criado_em         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    criado_por        UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    atualizado_em     TIMESTAMPTZ,
    atualizado_por    UUID         REFERENCES usuario(id) ON DELETE SET NULL,

    CONSTRAINT chk_alocacao_posicao CHECK (posicao > 0)
);

COMMENT ON TABLE alocacao_patio IS 'Histórico de alocações no pátio. Coluna ativa marca a posição atual.';

-- ---------------------------------------------------------------------
-- 6. ESCALA — escala diária por ônibus/motorista/linha
-- ---------------------------------------------------------------------
-- (linha_id e importacao_id serão FK depois que essas tabelas existirem)
CREATE TABLE escala (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    data              DATE         NOT NULL,
    onibus_id         UUID         NOT NULL REFERENCES onibus(id) ON DELETE CASCADE,
    motorista_id      UUID         REFERENCES motorista(id) ON DELETE SET NULL,
    linha_id          UUID         NOT NULL,  -- FK adicionada na 004
    horario_saida     TIME         NOT NULL,
    tipo              tipo_escala_enum NOT NULL,
    origem            origem_escala_enum NOT NULL DEFAULT 'MANUAL',
    importacao_id     UUID,                    -- FK adicionada na 004

    -- Soft delete
    deletado_em       TIMESTAMPTZ,

    -- Sincronização offline-first
    sincronizado_em   TIMESTAMPTZ,
    versao            INTEGER      NOT NULL DEFAULT 1,

    -- Auditoria
    criado_em         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    criado_por        UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    atualizado_em     TIMESTAMPTZ,
    atualizado_por    UUID         REFERENCES usuario(id) ON DELETE SET NULL
);

COMMENT ON TABLE escala IS 'Escala diária. Cada registro pertence a uma data; coexistem entre dias.';

-- ---------------------------------------------------------------------
-- 7. ALERTA — PRESO e AMOSTRAL
-- ---------------------------------------------------------------------
CREATE TABLE alerta (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    onibus_id         UUID         NOT NULL REFERENCES onibus(id) ON DELETE CASCADE,
    tipo              tipo_alerta_enum NOT NULL,
    motivo            TEXT,
    registrado_por    UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    resolvido         BOOLEAN      NOT NULL DEFAULT FALSE,
    resolvido_em      TIMESTAMPTZ,
    resolvido_por     UUID         REFERENCES usuario(id) ON DELETE SET NULL,

    -- Soft delete
    deletado_em       TIMESTAMPTZ,

    -- Sincronização offline-first
    sincronizado_em   TIMESTAMPTZ,
    versao            INTEGER      NOT NULL DEFAULT 1,

    -- Auditoria
    criado_em         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    criado_por        UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    atualizado_em     TIMESTAMPTZ,
    atualizado_por    UUID         REFERENCES usuario(id) ON DELETE SET NULL,

    CONSTRAINT chk_alerta_resolvido CHECK (
        (resolvido = FALSE AND resolvido_em IS NULL AND resolvido_por IS NULL)
        OR (resolvido = TRUE AND resolvido_em IS NOT NULL)
    )
);

COMMENT ON TABLE alerta IS 'Alertas de PRESO (retido na rua) e AMOSTRAL (SPTRANS). Prioridade na impressão: PRESO > AMOSTRAL.';

-- ---------------------------------------------------------------------
-- 8. FICHA_MANUTENCAO — defeitos e serviços
-- ---------------------------------------------------------------------
-- (tipo_defeito_id será FK depois)
CREATE TABLE ficha_manutencao (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    onibus_id         UUID         NOT NULL REFERENCES onibus(id) ON DELETE CASCADE,
    motorista_id      UUID         REFERENCES motorista(id) ON DELETE SET NULL,
    mecanico_id       UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    tipo_defeito_id   UUID         NOT NULL,   -- FK adicionada na 004
    descricao         TEXT,
    status            status_ficha_enum NOT NULL DEFAULT 'ABERTA',
    aberta_em         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    concluida_em      TIMESTAMPTZ,

    -- Soft delete
    deletado_em       TIMESTAMPTZ,

    -- Sincronização offline-first
    sincronizado_em   TIMESTAMPTZ,
    versao            INTEGER      NOT NULL DEFAULT 1,

    -- Auditoria
    criado_em         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    criado_por        UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    atualizado_em     TIMESTAMPTZ,
    atualizado_por    UUID         REFERENCES usuario(id) ON DELETE SET NULL,

    CONSTRAINT chk_ficha_concluida CHECK (
        (status IN ('ABERTA', 'EM_ANDAMENTO') AND concluida_em IS NULL)
        OR (status IN ('CONCLUIDA', 'CANCELADA') AND concluida_em IS NOT NULL)
    )
);

COMMENT ON TABLE ficha_manutencao IS 'Fichas de defeito/serviço. Conceito separado da localização física do ônibus.';

-- =====================================================================
-- FIM da migration 003
-- =====================================================================
