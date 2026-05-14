-- =====================================================================
-- Migration 001 — Criação do banco de dados
-- Sistema de Gestão de Pátio Sambaíba v3.0
-- PostgreSQL 15+
-- =====================================================================
-- Conectar como superusuário (postgres) e rodar este script.
-- Depois conectar no banco recém-criado para rodar as próximas migrations.
-- =====================================================================

-- Cria o banco de dados (rode conectado em outro banco, ex.: postgres)
CREATE DATABASE gestao_patio_sambaiba
    WITH
    OWNER = postgres
    ENCODING = 'UTF8'
    LC_COLLATE = 'pt_BR.UTF-8'
    LC_CTYPE = 'pt_BR.UTF-8'
    TEMPLATE = template0
    CONNECTION LIMIT = -1;

COMMENT ON DATABASE gestao_patio_sambaiba IS
    'Sistema de Gestão de Pátio — Sambaíba Transportes Urbanos · Garagem 3 · v3.0';

-- =====================================================================
-- A partir daqui, conecte-se ao banco gestao_patio_sambaiba e rode
-- as próximas migrations: 002, 003, 004, 005, 006 e 007 nessa ordem.
-- =====================================================================
