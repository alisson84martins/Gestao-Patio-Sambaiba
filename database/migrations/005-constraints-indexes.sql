-- =====================================================================
-- Migration 005 — Constraints únicos parciais e Índices
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================

-- ---------------------------------------------------------------------
-- CONSTRAINTS UNIQUE PARCIAIS (regras de negócio críticas)
-- ---------------------------------------------------------------------

-- Um ônibus só pode ter UMA alocação ativa por vez no pátio.
-- Isso elimina estruturalmente os bugs 1 e 2 da v2 (ônibus em dois lugares).
CREATE UNIQUE INDEX uq_alocacao_onibus_ativa
    ON alocacao_patio (onibus_id)
    WHERE ativa = TRUE;

-- Não pode haver alerta PRESO ou AMOSTRAL ativo duplicado para o mesmo ônibus.
CREATE UNIQUE INDEX uq_alerta_onibus_tipo_ativo
    ON alerta (onibus_id, tipo)
    WHERE resolvido = FALSE AND deletado_em IS NULL;

-- Posição na fila é única quando ativa (não pode ter dois ônibus na mesma posição da mesma fila).
CREATE UNIQUE INDEX uq_alocacao_fila_posicao_ativa
    ON alocacao_patio (fila_id, posicao)
    WHERE ativa = TRUE;

-- Filas numéricas: número único entre as ativas.
CREATE UNIQUE INDEX uq_fila_numerica
    ON fila (numero)
    WHERE tipo = 'NUMERICA' AND ativa = TRUE;

-- Posição especial: nome único entre as ativas.
CREATE UNIQUE INDEX uq_fila_especial_nome
    ON fila (nome)
    WHERE tipo IN ('ESPECIAL', 'MANUTENCAO') AND ativa = TRUE;

-- ---------------------------------------------------------------------
-- ÍNDICES OPERACIONAIS (queries do dia a dia)
-- ---------------------------------------------------------------------

-- ÔNIBUS: busca por código externo (integração futura)
CREATE INDEX idx_onibus_codigo_externo
    ON onibus (codigo_externo)
    WHERE codigo_externo IS NOT NULL;

CREATE INDEX idx_onibus_status
    ON onibus (status);

-- MOTORISTA: busca por código externo
CREATE INDEX idx_motorista_codigo_externo
    ON motorista (codigo_externo)
    WHERE codigo_externo IS NOT NULL;

CREATE INDEX idx_motorista_status
    ON motorista (status);

-- ALOCACAO_PATIO: renderizar fila inteira (já coberto por uq_alocacao_fila_posicao_ativa
-- mas adicionamos um índice descendente para histórico recente)
CREATE INDEX idx_alocacao_alocado_em
    ON alocacao_patio (alocado_em DESC);

-- ESCALA: filtro do dia atual (mais comum)
CREATE INDEX idx_escala_data_onibus
    ON escala (data, onibus_id)
    WHERE deletado_em IS NULL;

CREATE INDEX idx_escala_data_horario
    ON escala (data, horario_saida)
    WHERE deletado_em IS NULL;

CREATE INDEX idx_escala_motorista_data
    ON escala (motorista_id, data)
    WHERE deletado_em IS NULL AND motorista_id IS NOT NULL;

CREATE INDEX idx_escala_importacao
    ON escala (importacao_id)
    WHERE importacao_id IS NOT NULL;

-- ALERTA: alertas ativos do ônibus
CREATE INDEX idx_alerta_onibus_resolvido
    ON alerta (onibus_id, resolvido)
    WHERE deletado_em IS NULL;

-- FICHA_MANUTENCAO: fichas abertas/em andamento
CREATE INDEX idx_ficha_onibus_status
    ON ficha_manutencao (onibus_id, status)
    WHERE deletado_em IS NULL;

CREATE INDEX idx_ficha_aberta_em
    ON ficha_manutencao (aberta_em DESC)
    WHERE deletado_em IS NULL;

CREATE INDEX idx_ficha_mecanico
    ON ficha_manutencao (mecanico_id)
    WHERE deletado_em IS NULL AND mecanico_id IS NOT NULL;

-- LINHA: filtro por setor (cruzamento E2/AR2)
CREATE INDEX idx_linha_setor_ativa
    ON linha (setor)
    WHERE ativa = TRUE;

-- USUARIO: índice por perfil para listagens administrativas
CREATE INDEX idx_usuario_perfil
    ON usuario (perfil)
    WHERE ativo = TRUE;

-- IMPORTACAO_ESCALA: histórico por data
CREATE INDEX idx_importacao_data
    ON importacao_escala (data_escala DESC, importado_em DESC);

-- ---------------------------------------------------------------------
-- ÍNDICES DE SINCRONIZAÇÃO (offline-first)
-- ---------------------------------------------------------------------
-- Servidor busca registros não-sincronizados para enviar de volta ao cliente.

CREATE INDEX idx_alocacao_nao_sincronizado
    ON alocacao_patio (sincronizado_em)
    WHERE sincronizado_em IS NULL;

CREATE INDEX idx_escala_nao_sincronizado
    ON escala (sincronizado_em)
    WHERE sincronizado_em IS NULL;

CREATE INDEX idx_alerta_nao_sincronizado
    ON alerta (sincronizado_em)
    WHERE sincronizado_em IS NULL;

CREATE INDEX idx_ficha_nao_sincronizado
    ON ficha_manutencao (sincronizado_em)
    WHERE sincronizado_em IS NULL;

-- =====================================================================
-- FIM da migration 005
-- =====================================================================
