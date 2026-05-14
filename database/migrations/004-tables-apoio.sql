-- =====================================================================
-- Migration 004 — Catálogos e Tabelas de Sistema (4 tabelas)
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================

-- ---------------------------------------------------------------------
-- 9. LINHA — catálogo fechado de linhas E2 e AR2
-- ---------------------------------------------------------------------
CREATE TABLE linha (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          VARCHAR(20)  NOT NULL UNIQUE,
    nome            VARCHAR(120) NOT NULL,
    setor           setor_enum   NOT NULL,
    ativa           BOOLEAN      NOT NULL DEFAULT TRUE,

    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE linha IS 'Catálogo de linhas. Setor amarrado: linha E2 só em frota E2; AR2 só em AR2.';

-- ---------------------------------------------------------------------
-- 10. TIPO_DEFEITO — categorias de defeito
-- ---------------------------------------------------------------------
CREATE TABLE tipo_defeito (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          VARCHAR(20)  NOT NULL UNIQUE,
    nome            VARCHAR(120) NOT NULL,
    categoria       VARCHAR(50),
    ativo           BOOLEAN      NOT NULL DEFAULT TRUE,

    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE tipo_defeito IS 'Catálogo de categorias de defeito (mecânica, elétrica, freios, ar, lataria…).';

-- ---------------------------------------------------------------------
-- 11. PERMISSAO — matriz granular usuário × recurso
-- ---------------------------------------------------------------------
CREATE TABLE permissao (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      UUID         NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    recurso         VARCHAR(50)  NOT NULL,
    pode_ler        BOOLEAN      NOT NULL DEFAULT TRUE,
    pode_escrever   BOOLEAN      NOT NULL DEFAULT FALSE,
    concedido_por   UUID         REFERENCES usuario(id) ON DELETE SET NULL,

    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_permissao_usuario_recurso UNIQUE (usuario_id, recurso)
);

COMMENT ON TABLE permissao IS 'Permissões granulares por usuário e recurso. Recursos: alocacao, escala, alerta, manutencao, frota, etc.';

-- ---------------------------------------------------------------------
-- 12. IMPORTACAO_ESCALA — histórico de uploads de planilha
-- ---------------------------------------------------------------------
CREATE TABLE importacao_escala (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    arquivo_nome        VARCHAR(255) NOT NULL,
    arquivo_hash        VARCHAR(64),
    data_escala         DATE         NOT NULL,
    total_registros     INTEGER      NOT NULL DEFAULT 0,
    registros_sucesso   INTEGER      NOT NULL DEFAULT 0,
    registros_erro      INTEGER      NOT NULL DEFAULT 0,
    status              status_importacao_enum NOT NULL,
    erro_detalhe        TEXT,
    importado_por       UUID         REFERENCES usuario(id) ON DELETE SET NULL,
    importado_em        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_importacao_totais CHECK (
        total_registros >= 0
        AND registros_sucesso >= 0
        AND registros_erro >= 0
        AND registros_sucesso + registros_erro <= total_registros
    )
);

COMMENT ON TABLE importacao_escala IS 'Registro de cada upload de planilha de escala. Permite reverter import errado.';

-- ---------------------------------------------------------------------
-- FKs pendentes da migration 003
-- ---------------------------------------------------------------------
ALTER TABLE escala
    ADD CONSTRAINT fk_escala_linha
    FOREIGN KEY (linha_id) REFERENCES linha(id) ON DELETE RESTRICT;

ALTER TABLE escala
    ADD CONSTRAINT fk_escala_importacao
    FOREIGN KEY (importacao_id) REFERENCES importacao_escala(id) ON DELETE SET NULL;

ALTER TABLE ficha_manutencao
    ADD CONSTRAINT fk_ficha_tipo_defeito
    FOREIGN KEY (tipo_defeito_id) REFERENCES tipo_defeito(id) ON DELETE RESTRICT;

-- =====================================================================
-- FIM da migration 004
-- =====================================================================
