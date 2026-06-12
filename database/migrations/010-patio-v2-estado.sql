-- =====================================================================
-- Migration 010 — Tabela de estado V2 do pátio (blob JSON)
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Contexto: Armazena o estado completo da tela V2 como JSONB, permitindo
-- sincronização multi-usuário sem reescrever o frontend. Um único
-- registro (id=1) é sempre atualizado via upsert em cada save().
-- =====================================================================

CREATE TABLE IF NOT EXISTS patio_v2_estado (
    id            INT          PRIMARY KEY,
    estado        JSONB        NOT NULL DEFAULT '{}',
    atualizado_em TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    atualizado_por VARCHAR(20)
);

-- Seed: garante a linha id=1 para o upsert funcionar desde o início
INSERT INTO patio_v2_estado (id, estado)
VALUES (1, '{}')
ON CONFLICT (id) DO NOTHING;

-- =====================================================================
