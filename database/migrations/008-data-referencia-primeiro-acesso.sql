-- =====================================================================
-- Migration 008 — data_referencia + primeiro_acesso
-- Sistema de Gestão de Pátio Sambaíba v3.0 — Fase 5.8
-- =====================================================================
-- Rode CONECTADO no banco gestao_patio_sambaiba.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. alocacao_patio — coluna data_referencia
-- ---------------------------------------------------------------------
-- Problema resolvido: sem data_referencia, um ônibus que não fosse
-- alocado hoje permanecia na posição de ontem (ativa=TRUE sem data).
--
-- Regra operacional: o turno de alocação começa às ~23:30 e termina
-- às ~04:30. O corte é às 20h: após esse horário as alocações já
-- pertencem ao dia de serviço seguinte (data_referencia = amanhã).
-- Isso é calculado no backend (get_data_servico) — o banco só armazena.
-- ---------------------------------------------------------------------
ALTER TABLE alocacao_patio
    ADD COLUMN data_referencia DATE NOT NULL DEFAULT CURRENT_DATE;

-- Índice composto: cobre a query principal do pátio (ativa + data)
CREATE INDEX idx_alocacao_data_ativa
    ON alocacao_patio (data_referencia, ativa);

-- Índice para o endpoint "onde está o ônibus X" (onibus_id + ativa + data)
CREATE INDEX idx_alocacao_onibus_data_ativa
    ON alocacao_patio (onibus_id, data_referencia, ativa);

COMMENT ON COLUMN alocacao_patio.data_referencia IS
    'Data de serviço real. Após 20h o backend grava a data do dia seguinte '
    '(o turno de alocação começa ~23:30 e serve a operação do dia seguinte). '
    'Permite limpar o pátio a cada dia sem apagar histórico.';

-- ---------------------------------------------------------------------
-- 2. usuario — coluna primeiro_acesso
-- ---------------------------------------------------------------------
-- Força troca de senha no primeiro login do usuário.
-- O admin cria a conta com senha = RE; o usuário deve trocar no 1º acesso.
-- O backend verifica este flag em /auth/login e inclui na resposta JWT.
-- ---------------------------------------------------------------------
ALTER TABLE usuario
    ADD COLUMN primeiro_acesso BOOLEAN NOT NULL DEFAULT TRUE;

-- Admin já tem conta ativa e senha própria — não precisa trocar
UPDATE usuario SET primeiro_acesso = FALSE WHERE perfil = 'ADMIN';

COMMENT ON COLUMN usuario.primeiro_acesso IS
    'TRUE = usuário ainda não trocou a senha inicial (RE). '
    'Setado para FALSE em POST /auth/trocar-senha.';

-- =====================================================================
-- FIM da migration 008
-- =====================================================================
