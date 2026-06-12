-- =====================================================================
-- Migration 009 — Adiciona coluna CPF na tabela usuario
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Contexto: CPF é usado apenas para gerar a senha inicial do usuário
-- (últimos 4 dígitos). Nunca exposto na API.
-- =====================================================================

ALTER TABLE usuario
    ADD COLUMN IF NOT EXISTS cpf VARCHAR(14);

-- =====================================================================
