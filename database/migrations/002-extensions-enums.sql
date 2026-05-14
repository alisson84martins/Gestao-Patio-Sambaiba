-- =====================================================================
-- Migration 002 — Extensões e Tipos Enumerados
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- =====================================================================
-- Rode CONECTADO no banco gestao_patio_sambaiba.
-- =====================================================================

SET client_encoding = 'UTF8';
SET timezone = 'America/Sao_Paulo';

-- ---------------------------------------------------------------------
-- EXTENSÕES
-- ---------------------------------------------------------------------
-- pgcrypto: gen_random_uuid() para gerar UUIDs v4 nativamente.
-- (PostgreSQL 13+ tem gen_random_uuid no core, mas pgcrypto é seguro.)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- citext: comparações case-insensitive (útil para nomes, RE etc.)
CREATE EXTENSION IF NOT EXISTS citext;

-- ---------------------------------------------------------------------
-- TIPOS ENUMERADOS
-- ---------------------------------------------------------------------

-- Setor operacional do ônibus (derivado do prefixo da frota)
CREATE TYPE setor_enum AS ENUM ('E2', 'AR2');

-- Status operacional do ônibus
CREATE TYPE status_onibus_enum AS ENUM (
    'ATIVO',
    'MANUTENCAO',
    'INATIVO',
    'RESERVA'
);

-- Status do motorista
CREATE TYPE status_motorista_enum AS ENUM (
    'ATIVO',
    'AFASTADO',
    'FERIAS',
    'DESLIGADO'
);

-- Perfil do usuário do sistema
CREATE TYPE perfil_usuario_enum AS ENUM (
    'ADMIN',
    'COORDENADOR',
    'OPERADOR_PATIO',
    'MOTORISTA',
    'MECANICO'
);

-- Tipo de fila no pátio
CREATE TYPE tipo_fila_enum AS ENUM (
    'NUMERICA',     -- 1 a 33
    'ESPECIAL',     -- Coqueiro, Laje, Lavador, Bomba, Elétricos, Fundão
    'MANUTENCAO'    -- Manutenção (e sub-setores futuros)
);

-- Tipo de alerta
CREATE TYPE tipo_alerta_enum AS ENUM (
    'PRESO',        -- Veículo retido na rua
    'AMOSTRAL'      -- Amostral SPTRANS
);

-- Status da ficha de manutenção
CREATE TYPE status_ficha_enum AS ENUM (
    'ABERTA',
    'EM_ANDAMENTO',
    'CONCLUIDA',
    'CANCELADA'
);

-- Tipo de escala
CREATE TYPE tipo_escala_enum AS ENUM (
    'MANOBRA',
    'PLANTAO_E2',
    'PLANTAO_AR2'
);

-- Origem do registro de escala
CREATE TYPE origem_escala_enum AS ENUM (
    'IMPORTACAO_EXCEL',
    'MANUAL'
);

-- Status da importação de escala (planilha)
CREATE TYPE status_importacao_enum AS ENUM (
    'SUCESSO',
    'ERRO',
    'PARCIAL'
);

-- =====================================================================
-- FIM da migration 002
-- =====================================================================
