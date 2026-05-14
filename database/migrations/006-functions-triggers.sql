-- =====================================================================
-- Migration 006 — Funções e Triggers
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================

-- ---------------------------------------------------------------------
-- FUNÇÃO: atualiza atualizado_em automaticamente em qualquer UPDATE
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_set_atualizado_em()
RETURNS TRIGGER AS $$
BEGIN
    NEW.atualizado_em := NOW();
    -- Incrementa versão para resolução de conflitos no offline-first
    IF TG_TABLE_NAME IN ('alocacao_patio', 'escala', 'alerta', 'ficha_manutencao') THEN
        NEW.versao := COALESCE(OLD.versao, 0) + 1;
        -- Marca como não-sincronizado quando há mudança local
        IF NEW.sincronizado_em IS NOT DISTINCT FROM OLD.sincronizado_em THEN
            NEW.sincronizado_em := NULL;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_set_atualizado_em IS
    'Trigger BEFORE UPDATE: atualiza atualizado_em e incrementa versao em tabelas com sync.';

-- ---------------------------------------------------------------------
-- TRIGGERS — aplicar em todas as tabelas com auditoria
-- ---------------------------------------------------------------------

CREATE TRIGGER trg_motorista_atualizado
    BEFORE UPDATE ON motorista
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

CREATE TRIGGER trg_usuario_atualizado
    BEFORE UPDATE ON usuario
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

CREATE TRIGGER trg_onibus_atualizado
    BEFORE UPDATE ON onibus
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

CREATE TRIGGER trg_alocacao_atualizado
    BEFORE UPDATE ON alocacao_patio
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

CREATE TRIGGER trg_escala_atualizado
    BEFORE UPDATE ON escala
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

CREATE TRIGGER trg_alerta_atualizado
    BEFORE UPDATE ON alerta
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

CREATE TRIGGER trg_ficha_atualizado
    BEFORE UPDATE ON ficha_manutencao
    FOR EACH ROW EXECUTE FUNCTION fn_set_atualizado_em();

-- ---------------------------------------------------------------------
-- FUNÇÃO: validar cruzamento de setor entre linha e ônibus na escala
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_valida_setor_escala()
RETURNS TRIGGER AS $$
DECLARE
    v_setor_onibus setor_enum;
    v_setor_linha  setor_enum;
BEGIN
    SELECT setor INTO v_setor_onibus FROM onibus WHERE id = NEW.onibus_id;
    SELECT setor INTO v_setor_linha  FROM linha  WHERE id = NEW.linha_id;

    IF v_setor_onibus IS DISTINCT FROM v_setor_linha THEN
        RAISE EXCEPTION 'Setor incompatível: ônibus é % mas linha é %',
            v_setor_onibus, v_setor_linha
            USING ERRCODE = 'check_violation',
                  HINT = 'Linha E2 só em frota 1xxx; linha AR2 só em frota 2xxx';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_valida_setor_escala IS
    'Garante que escala respeita prefixo da frota: 1xxx → E2, 2xxx → AR2.';

CREATE TRIGGER trg_escala_valida_setor
    BEFORE INSERT OR UPDATE OF onibus_id, linha_id ON escala
    FOR EACH ROW EXECUTE FUNCTION fn_valida_setor_escala();

-- ---------------------------------------------------------------------
-- FUNÇÃO: ao criar nova alocação ativa, desativa a anterior do mesmo ônibus
-- ---------------------------------------------------------------------
-- Garante que só exista uma alocação ativa por ônibus, sem precisar
-- gerenciar isso na aplicação (defesa em profundidade junto com o índice único).
CREATE OR REPLACE FUNCTION fn_alocacao_unica_ativa()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.ativa = TRUE THEN
        UPDATE alocacao_patio
        SET ativa = FALSE,
            atualizado_em = NOW(),
            atualizado_por = NEW.alocado_por
        WHERE onibus_id = NEW.onibus_id
          AND ativa = TRUE
          AND id <> NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_alocacao_unica_ativa IS
    'Antes de inserir/atualizar uma alocação ativa, desativa a anterior do mesmo ônibus.';

CREATE TRIGGER trg_alocacao_unica_ativa
    BEFORE INSERT OR UPDATE OF ativa ON alocacao_patio
    FOR EACH ROW
    WHEN (NEW.ativa = TRUE)
    EXECUTE FUNCTION fn_alocacao_unica_ativa();

-- ---------------------------------------------------------------------
-- FUNÇÃO: quando ficha de manutenção é concluída, preenche concluida_em
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_ficha_concluida_em()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status IN ('CONCLUIDA', 'CANCELADA') AND NEW.concluida_em IS NULL THEN
        NEW.concluida_em := NOW();
    END IF;
    IF NEW.status IN ('ABERTA', 'EM_ANDAMENTO') THEN
        NEW.concluida_em := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ficha_concluida_em
    BEFORE INSERT OR UPDATE OF status ON ficha_manutencao
    FOR EACH ROW EXECUTE FUNCTION fn_ficha_concluida_em();

-- =====================================================================
-- FIM da migration 006
-- =====================================================================
