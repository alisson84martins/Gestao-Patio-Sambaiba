-- ============================================================================
-- MIGRATION 047 — Fiscalização passa a usar os postos da Escala de Fiscais
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: fiscalizacao (altera turno, cria turno_posto, apaga ponto e
--         ponto_linha); só LÊ coordenadoria.* (GRANT de leitura no fim)
-- DATA:   2026-09-26
-- AUTOR:  Claude Code
-- DEPENDE DE: 029-modulo-fiscalizacao.sql (turno, ponto, ponto_linha),
--             045-escala-fiscais.sql (coordenadoria.escala_fiscal_posto,
--             escala_fiscal_posto_linha, escala_fiscal_ponto_final) — em
--             produção roda DEPOIS da 045 e da 046.
-- ORIGEM: _handoff-claude/PROMPT-fiscalizacao-pontos-da-escala-2026-09-26.md
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Até aqui o fiscal cadastrava o próprio ponto e as linhas dentro da
--   Fiscalização (D37 — POST /fiscalizacao/pontos). O mesmo lugar agora é
--   cadastrado pelo coordenador na aba Escala de Fiscais → Postos, e os dois
--   cadastros divergiam. A Escala vira a FONTE ÚNICA: a lista "Escolha o
--   ponto" do app do fiscal passa a ler os postos ativos da escala, e o
--   cadastro de ponto da Fiscalização é apagado.
--
-- O QUE MUDA
--   1. turno.ponto_nome — rótulo do que o fiscal escolheu, gravado ao abrir
--      (snapshot): nome do ponto final ou, sem ponto final, as linhas unidas
--      por " / ". Backfill dos turnos antigos com o nome do ponto antigo.
--   2. turno.ponto_codigo perde a FK e o NOT NULL — fica só como histórico
--      dos turnos antigos; turno novo grava NULL.
--   3. Índice único do turno ABERTO passa de (pessoa, ponto, período, dia)
--      para (pessoa, período, dia) — R7: um turno aberto por pessoa, período
--      e dia, em qualquer posto. Se já houver duplicata, o índice NÃO é
--      criado (NOTICE lista) e o router continua barrando com 409.
--   4. fiscalizacao.turno_posto — quais postos da escala o turno cobre.
--      ⛔ SEM FK para coordenadoria.escala_fiscal_posto (regra de fronteira:
--      a única FK que sai do schema fiscalizacao é funcionario). lado e
--      ponto_final_nome são snapshot — o turno continua legível se o posto
--      for editado ou desativado na escala.
--   5. fiscalizacao.ponto e ponto_linha são APAGADOS. Os turnos (e partidas,
--      eventos, baita, observações) FICAM — por isso o backfill do nome vem
--      antes do DROP.
--
-- IDEMPOTENTE: pode rodar duas vezes; na segunda, ponto/ponto_linha já não
--   existem e os blocos que dependem deles só avisam.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 047-fiscalizacao-pontos-da-escala.sql
-- ============================================================================

BEGIN;

SET search_path TO fiscalizacao, public;

-- ============================================================================
-- 1 · turno.ponto_nome (snapshot)
-- ============================================================================
ALTER TABLE fiscalizacao.turno ADD COLUMN IF NOT EXISTS ponto_nome VARCHAR(160);
COMMENT ON COLUMN fiscalizacao.turno.ponto_nome IS 'Snapshot do rótulo do lugar escolhido ao abrir o turno: nome do ponto final da Escala de Fiscais ou, sem ponto final, as linhas unidas por " / ". Turnos anteriores à 047 recebem o nome do fiscalizacao.ponto antigo.';

-- ============================================================================
-- 2 · Backfill do nome nos turnos antigos — ANTES do DROP de ponto
-- ============================================================================
DO $$
DECLARE
    n BIGINT;
BEGIN
    IF to_regclass('fiscalizacao.ponto') IS NOT NULL THEN
        EXECUTE 'UPDATE fiscalizacao.turno t SET ponto_nome = p.nome
                   FROM fiscalizacao.ponto p
                  WHERE p.codigo = t.ponto_codigo AND t.ponto_nome IS NULL';
        GET DIAGNOSTICS n = ROW_COUNT;
        RAISE NOTICE '047: % turno(s) antigo(s) receberam ponto_nome do ponto antigo.', n;
    ELSE
        RAISE NOTICE '047: fiscalizacao.ponto já não existe — backfill de ponto_nome pulado.';
    END IF;
END $$;

-- ============================================================================
-- 3 · Solta turno.ponto_codigo do catálogo antigo
-- ============================================================================
-- O nome da FK é descoberto no pg_constraint (não supor turno_ponto_codigo_fkey).
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT c.conname
          FROM pg_constraint c
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
         WHERE c.conrelid = 'fiscalizacao.turno'::regclass
           AND c.contype = 'f'
           AND a.attname = 'ponto_codigo'
    LOOP
        EXECUTE format('ALTER TABLE fiscalizacao.turno DROP CONSTRAINT IF EXISTS %I', r.conname);
        RAISE NOTICE '047: FK % de turno.ponto_codigo removida.', r.conname;
    END LOOP;
END $$;

ALTER TABLE fiscalizacao.turno ALTER COLUMN ponto_codigo DROP NOT NULL;
COMMENT ON COLUMN fiscalizacao.turno.ponto_codigo IS 'Histórico: código do fiscalizacao.ponto (apagado na 047) dos turnos antigos. Turno aberto depois da 047 grava NULL — os postos estão em turno_posto e o rótulo em ponto_nome.';

-- ============================================================================
-- 4 · Um turno ABERTO por pessoa + período + dia (R7)
-- ============================================================================
DROP INDEX IF EXISTS fiscalizacao.uq_turno_aberto_por_pessoa_ponto_periodo_dia;

DO $$
DECLARE
    r RECORD;
    duplicatas INT := 0;
BEGIN
    FOR r IN
        SELECT funcionario_id, periodo, data_referencia, count(*) AS qtd
          FROM fiscalizacao.turno
         WHERE status = 'ABERTO'
         GROUP BY funcionario_id, periodo, data_referencia
        HAVING count(*) > 1
    LOOP
        duplicatas := duplicatas + 1;
        RAISE NOTICE '047: duplicata de turno ABERTO — funcionario_id=% periodo=% data=% (% turnos)',
            r.funcionario_id, r.periodo, r.data_referencia, r.qtd;
    END LOOP;

    IF duplicatas = 0 THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_turno_aberto_por_pessoa_periodo_dia
            ON fiscalizacao.turno (funcionario_id, periodo, data_referencia)
            WHERE status = 'ABERTO';
        RAISE NOTICE '047: índice uq_turno_aberto_por_pessoa_periodo_dia presente.';
    ELSE
        RAISE NOTICE '047: % grupo(s) duplicado(s) — índice uq_turno_aberto_por_pessoa_periodo_dia NÃO criado. Feche os turnos repetidos e rode a 047 de novo; até lá o router barra com 409.', duplicatas;
    END IF;
END $$;

-- ============================================================================
-- 5 · turno_posto — quais postos da Escala de Fiscais o turno cobre
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.turno_posto (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turno_id         UUID NOT NULL REFERENCES fiscalizacao.turno(id) ON DELETE CASCADE,
    -- ⛔ Sem FK para coordenadoria.escala_fiscal_posto — regra de fronteira.
    posto_id         UUID NOT NULL,
    lado             VARCHAR(2) NOT NULL CHECK (lado IN ('TP', 'TS')),
    ponto_final_nome VARCHAR(80),
    UNIQUE (turno_id, posto_id)
);
COMMENT ON TABLE fiscalizacao.turno_posto IS 'Postos da Escala de Fiscais (coordenadoria.escala_fiscal_posto) que o turno cobre — 1 posto, ou vários do MESMO ponto final e MESMA ponta. posto_id sem FK (fronteira do schema). lado e ponto_final_nome são snapshot do momento de abrir: o turno continua legível se o posto for editado ou desativado na escala.';

-- ============================================================================
-- 6 · Apaga o cadastro de ponto da Fiscalização (turnos ficam)
-- ============================================================================
DO $$
DECLARE
    n_ponto BIGINT := 0;
    n_ponto_linha BIGINT := 0;
BEGIN
    IF to_regclass('fiscalizacao.ponto_linha') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM fiscalizacao.ponto_linha' INTO n_ponto_linha;
    END IF;
    IF to_regclass('fiscalizacao.ponto') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM fiscalizacao.ponto' INTO n_ponto;
    END IF;
    RAISE NOTICE '047: apagando % ponto(s) e % ponto_linha(s).', n_ponto, n_ponto_linha;
END $$;

DROP TABLE IF EXISTS fiscalizacao.ponto_linha;
DROP TABLE IF EXISTS fiscalizacao.ponto;

-- ============================================================================
-- 7 · 🔴 GRANT (a falta de GRANT derrubou produção em 17/09)
-- ============================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA fiscalizacao TO sambaiba;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA fiscalizacao TO sambaiba;
-- Leitura nova: GET /fiscalizacao/postos lê os postos da escala.
GRANT USAGE ON SCHEMA coordenadoria TO sambaiba;
GRANT SELECT ON ALL TABLES IN SCHEMA coordenadoria TO sambaiba;

COMMIT;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT to_regclass('fiscalizacao.ponto'), to_regclass('fiscalizacao.ponto_linha');  -- NULL, NULL
--   SELECT indexname FROM pg_indexes WHERE schemaname = 'fiscalizacao' AND tablename = 'turno';
--   -- esperado: uq_turno_aberto_por_pessoa_periodo_dia (sem o _ponto_)
--   SELECT count(*) FILTER (WHERE ponto_nome IS NULL) FROM fiscalizacao.turno;  -- 0
--   SELECT has_table_privilege('sambaiba', 'fiscalizacao.turno_posto', 'SELECT');           -- t
--   SELECT has_table_privilege('sambaiba', 'coordenadoria.escala_fiscal_posto', 'SELECT');  -- t
-- ============================================================================

-- ============================================================================
-- ROLLBACK (parcial — o cadastro de ponto apagado NÃO volta)
-- ============================================================================
-- DROP TABLE IF EXISTS fiscalizacao.turno_posto;
-- DROP INDEX IF EXISTS fiscalizacao.uq_turno_aberto_por_pessoa_periodo_dia;
-- ALTER TABLE fiscalizacao.turno DROP COLUMN IF EXISTS ponto_nome;
-- -- ponto/ponto_linha e a FK/NOT NULL de turno.ponto_codigo só voltam
-- -- recriando as tabelas da 029 e recadastrando os pontos à mão.
-- ============================================================================
