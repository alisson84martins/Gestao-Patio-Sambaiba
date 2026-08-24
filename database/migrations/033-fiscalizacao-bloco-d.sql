-- ============================================================================
-- MIGRATION 033 — Fiscalização Bloco D: contagem informada e tabela opcional
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: fiscalizacao (já existe, criado pela 029)
-- DATA:   2026-08-24
-- AUTOR:  Claude Code
-- DEPENDE DE: 029-modulo-fiscalizacao.sql (fiscalizacao.turno_linha,
--             fiscalizacao.registro_partida)
-- ORIGEM: _handoff-claude/DESENHO-fiscalizacao-rev3-bloco-D.md (D33, D35),
--         _handoff-claude/PROMPT-fiscalizacao-bloco-D-tela-do-fiscal.md §2
-- ----------------------------------------------------------------------------
-- ⚠️ NÚMERO — os arquivos vão até 032. Próximo número livre: 033.
--
-- 🟢 100% ADITIVA — só ADD COLUMN e um ALTER COLUMN DROP NOT NULL, os dois
--   idempotentes por natureza (rodar duas vezes não muda nada na segunda).
--   Nenhum DROP TABLE, DROP COLUMN nem DROP SCHEMA.
--
-- POR QUÊ
--   D32 — o primeiro corte do fiscal é a ANORMALIDADE, não a grade inteira
--   (a escala gerencial ainda não foi importada para a maioria das linhas).
--   Duas consequências de schema:
--     D35 — sem grade, PARTIDAS PROGRAMADAS/REALIZADAS do fechamento não
--       têm de onde vir automaticamente. O fiscal informa os três números
--       (programadas, realizadas, extras) por linha do turno, e o gerador
--       usa esse valor só quando não existe grade vigente (D6 preservado:
--       extras continua em coluna própria, nunca somado dentro do total
--       gravado). As três colunas nascem NULAS de propósito — nulo é
--       "não informado", zero é uma afirmação; o gerador de fechamento
--       (app/services/fechamento_fiscal.py::_totais_da_linha) imprime "—"
--       quando o valor é nulo, nunca "00".
--     D33 — nem toda anormalidade tem tabela conhecida na hora ("Tabela 08"
--       é comum no fechamento real, mas o fiscal às vezes só sabe o
--       horário e o motivo). numero_tabela deixa de ser obrigatório em
--       registro_partida.
--
-- ⚠️ EFEITO COLATERAL DA 1.2 NA UNIQUE EXISTENTE — leia antes de mexer em
--   app/routers/fiscalizacao.py::marcar_partida
--   A UNIQUE (turno_id, linha_codigo, numero_tabela, terminal,
--   horario_programado) de registro_partida (029) CONTINUA como está —
--   esta migration não a recria. Mas no Postgres (e no SQLite dos testes)
--   NULL não é igual a NULL para fins de UNIQUE: dois registros do mesmo
--   turno/linha/terminal/horário, ambos com numero_tabela NULL, NÃO colidem
--   e a constraint deixa passar os dois — ela para de proteger contra
--   duplicata exatamente no caso em que a tabela não foi informada.
--   A guarda passa a ser do backend: o SELECT que o upsert de
--   PUT /turnos/{id}/partidas já faz antes de inserir compara
--   `RegistroPartida.numero_tabela == payload.numero_tabela` — quando o
--   valor do lado direito é `None`, o SQLAlchemy traduz essa comparação
--   para `numero_tabela IS NULL` sozinho (é o comportamento padrão de
--   `Column.__eq__(None)`), então o mesmo SELECT que já existia encontra o
--   registro sem tabela informada e faz UPDATE nele — não precisou de
--   código novo, só o tipo do campo virar Optional (ver comentário em
--   app/routers/fiscalizacao.py::marcar_partida). Isto NÃO é descuido —
--   é a razão de não recriar a UNIQUE aqui.
--
-- REGRA DE FRONTEIRA — nenhuma coluna nova tem FK; são inteiros/opcionais.
--
-- ⚠️ DADO PESSOAL: nenhum nesta migration.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 033-fiscalizacao-bloco-d.sql
-- ============================================================================

-- ============================================================================
-- 1 · CONTAGEM INFORMADA PELO FISCAL (D35) — fiscalizacao.turno_linha
-- ============================================================================
ALTER TABLE fiscalizacao.turno_linha
    ADD COLUMN IF NOT EXISTS programadas_informadas SMALLINT,
    ADD COLUMN IF NOT EXISTS realizadas_informadas  SMALLINT,
    ADD COLUMN IF NOT EXISTS extras_informadas       SMALLINT;

COMMENT ON COLUMN fiscalizacao.turno_linha.programadas_informadas IS 'D35 — só usado quando não há grade vigente para a linha (fonte="INFORMADO" em services/fechamento_fiscal.py::_totais_da_linha). NULL = "não informado", diferente de 0 — o medidor de prontidão (CONTAGEM_NAO_INFORMADA) e o gerador do fechamento ("—" em vez de "00") dependem dessa diferença.';
COMMENT ON COLUMN fiscalizacao.turno_linha.realizadas_informadas IS 'D35 — ver programadas_informadas. Par com programadas_informadas para calcular perdidas quando não há grade.';
COMMENT ON COLUMN fiscalizacao.turno_linha.extras_informadas IS 'D35/D6 — viagem extra continua em coluna própria mesmo sem grade: o fechamento soma realizadas+extras na exibição, mas as duas ficam separadas no banco.';

-- CHECK de não-negativo, idempotente por DROP CONSTRAINT IF EXISTS + ADD
-- CONSTRAINT do mesmo nome (mesmo padrão de 032-recolhida-encerramento.sql
-- §2) — rodar esta migration duas vezes não muda nada na segunda.
ALTER TABLE fiscalizacao.turno_linha
    DROP CONSTRAINT IF EXISTS ck_turno_linha_programadas_informadas_nao_negativo;
ALTER TABLE fiscalizacao.turno_linha
    ADD CONSTRAINT ck_turno_linha_programadas_informadas_nao_negativo
    CHECK (programadas_informadas IS NULL OR programadas_informadas >= 0);

ALTER TABLE fiscalizacao.turno_linha
    DROP CONSTRAINT IF EXISTS ck_turno_linha_realizadas_informadas_nao_negativo;
ALTER TABLE fiscalizacao.turno_linha
    ADD CONSTRAINT ck_turno_linha_realizadas_informadas_nao_negativo
    CHECK (realizadas_informadas IS NULL OR realizadas_informadas >= 0);

ALTER TABLE fiscalizacao.turno_linha
    DROP CONSTRAINT IF EXISTS ck_turno_linha_extras_informadas_nao_negativo;
ALTER TABLE fiscalizacao.turno_linha
    ADD CONSTRAINT ck_turno_linha_extras_informadas_nao_negativo
    CHECK (extras_informadas IS NULL OR extras_informadas >= 0);

-- ============================================================================
-- 2 · TABELA OPCIONAL NA ANORMALIDADE (D33) — fiscalizacao.registro_partida
-- ============================================================================
-- Idempotente por natureza: DROP NOT NULL numa coluna que já aceita NULL não
-- erra na segunda execução.
ALTER TABLE fiscalizacao.registro_partida ALTER COLUMN numero_tabela DROP NOT NULL;

COMMENT ON COLUMN fiscalizacao.registro_partida.numero_tabela IS 'D33 — opcional desde a 033: nem toda anormalidade tem tabela conhecida na hora do registro. ⚠️ A UNIQUE (turno_id, linha_codigo, numero_tabela, terminal, horario_programado) desta tabela não protege contra duplicata quando numero_tabela é NULL (NULL não colide com NULL) — a guarda é o SELECT-antes-de-inserir do backend, ver cabeçalho desta migration.';

-- ============================================================================
-- 3 · Nada mais — sem seed de ponto (o cadastro entra pela tela, D37).
-- ============================================================================

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- As três colunas novas existem e são nulas por padrão:
--   SELECT column_name, is_nullable, data_type FROM information_schema.columns
--    WHERE table_schema = 'fiscalizacao' AND table_name = 'turno_linha'
--      AND column_name IN ('programadas_informadas','realizadas_informadas','extras_informadas');
--
--   -- numero_tabela aceita NULL:
--   SELECT is_nullable FROM information_schema.columns
--    WHERE table_schema = 'fiscalizacao' AND table_name = 'registro_partida'
--      AND column_name = 'numero_tabela';  -- esperado: YES
--
--   -- Os três CHECK de não-negativo existem:
--   SELECT conname FROM pg_constraint
--    WHERE conrelid = 'fiscalizacao.turno_linha'::regclass
--      AND conname LIKE 'ck_turno_linha_%_informadas_nao_negativo';  -- esperado: 3 linhas
--
--   -- Rodar o arquivo inteiro DUAS VEZES não erra (idempotência).
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE fiscalizacao.registro_partida ALTER COLUMN numero_tabela SET NOT NULL;
-- ⚠️ Só reverta o NOT NULL se nenhum registro_partida tiver numero_tabela
-- NULL ainda (senão o ALTER falha, e é esperado que falhe — decida
-- manualmente esses casos antes).
-- ALTER TABLE fiscalizacao.turno_linha
--     DROP CONSTRAINT IF EXISTS ck_turno_linha_programadas_informadas_nao_negativo,
--     DROP CONSTRAINT IF EXISTS ck_turno_linha_realizadas_informadas_nao_negativo,
--     DROP CONSTRAINT IF EXISTS ck_turno_linha_extras_informadas_nao_negativo,
--     DROP COLUMN IF EXISTS programadas_informadas,
--     DROP COLUMN IF EXISTS realizadas_informadas,
--     DROP COLUMN IF EXISTS extras_informadas;
-- ============================================================================
