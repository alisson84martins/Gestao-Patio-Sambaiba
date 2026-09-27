-- ============================================================================
-- MIGRATION 048 — Uma tabela de linhas (número + código) para todos os módulos
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: public (altera linha), coordenadoria (escala_fiscal_posto_linha
--         ganha linha_id), fiscalizacao (normaliza o texto de linha_codigo)
-- DATA:   2026-09-27
-- AUTOR:  Claude Code
-- DEPENDE DE: 045-escala-fiscais.sql (coordenadoria.escala_fiscal_posto_linha),
--             047-fiscalizacao-pontos-da-escala.sql — em produção roda DEPOIS
--             da 047.
-- ORIGEM: _handoff-claude/PROMPT-cadastro-unico-de-linhas-2026-09-27.md (v2)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Na operação, número + código formam linhas DIFERENTES: 271A-10 sai da
--   Penha e 271A-51 do Cangaíba; 2023-10, 2023-41, 2023-42. Cada módulo
--   escrevia a linha de um jeito — public.linha guardava o texto cru da
--   planilha do Pátio (271A, 271A51, 202341), a Escala de Fiscais texto livre
--   (1726/10, 1726-10), a Fiscalização 1726 — e nenhum enxergava o outro.
--   public.linha passa a ser o cadastro ÚNICO: cada registro é uma linha
--   completa (numero + sufixo), com codigo canônico NUMERO-SUFIXO.
--
-- O QUE MUDA
--   1. public.linha ganha numero (VARCHAR 8) e sufixo (VARCHAR 3, só dígitos).
--   2. Catálogo normalizado pela regra R2 (mesma de app/core/linha.py):
--      4 caracteres [0-9A-Z] de número (ao menos um dígito) + sufixo opcional de 1–3 dígitos
--      depois de - / . ou espaço; sem sufixo = 10. codigo vira NUMERO-SUFIXO
--      (271A → 271A-10, 271A51 → 271A-51). Placeholders MAN-<setor> ficam
--      como estão (numero/sufixo NULL). Duas linhas que viram a MESMA
--      (177H e 177H-10) são FUNDIDAS: fica a que tem mais escala (empate: a
--      mais antiga), recebe o nome "de verdade", todas as FKs para linha.id
--      são repontadas e a outra é DESATIVADA (codigo || '#fundida'). ⛔ Nunca
--      DELETE. Se a fusão falhar (ex.: trigger de setor da escala), ela é
--      desfeita, as duas ficam como estão e o NOTICE diz por quê.
--   3. UNIQUE (numero, sufixo) — índice parcial WHERE numero IS NOT NULL.
--   4. coordenadoria.escala_fiscal_posto_linha.linha_id → public.linha
--      (ON DELETE RESTRICT). Texto reescrito no código canônico (1726/10 →
--      1726-10; duplicata no mesmo posto é apagada — é a mesma linha).
--      Linha da escala que NÃO existe no catálogo NÃO é criada aqui: o lote
--      do posto não indica o setor com segurança (há posto de lote AR2 com
--      linha que o Pátio roda em E2 e vice-versa) — NOTICE e fica sem
--      linha_id ("linha sem cadastro" na Fiscalização) até alguém cadastrar
--      em Escala de Fiscais → Linhas, que liga o posto sozinho.
--   5. fiscalizacao: linha_coordenador, turno_linha e os filhos do turno
--      (registro_partida, evento_turno, observacao_turno, baita,
--      acao_coordenacao) continuam texto — snapshot do código canônico —
--      e são normalizados (1726 → 1726-10), para o fechamento do turno
--      antigo continuar casando linha com partida. partida_programada e
--      icv_apurado (vêm de importação própria, já com hífen) não são tocados.
--
-- O NOT NULL de numero/sufixo/linha_id fica para uma 049, DEPOIS de conferir
-- a produção.
--
-- IDEMPOTENTE: pode rodar duas vezes; na segunda, o que já está no formato
--   canônico só é contado. Função de normalização em pg_temp, dropada no fim.
--
-- ⚠️ Rodar FORA do horário de importação da escala do Pátio (a importação
--   cria/busca linha por código — no meio da troca de formato criaria
--   duplicata no formato antigo).
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 048-linha-unica.sql
-- ============================================================================

BEGIN;

SET search_path TO public;

-- ----------------------------------------------------------------------------
-- Regra R2 em SQL (espelho de app/core/linha.py::normalizar_linha).
-- Devolve {numero, sufixo} ou NULL.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION pg_temp.normalizar_linha_048(txt TEXT)
RETURNS TEXT[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    m TEXT[];
BEGIN
    IF txt IS NULL THEN
        RETURN NULL;
    END IF;
    m := regexp_match(
        upper(regexp_replace(txt, '^\s+|\s+$', '', 'g')),
        '^([0-9A-Z]{4})(?:[-/. ]?([0-9]{1,3}))?$'
    );
    IF m IS NULL OR m[1] !~ '[0-9]' THEN  -- número sem dígito (LIXO) não é linha
        RETURN NULL;
    END IF;
    RETURN ARRAY[m[1], COALESCE(m[2], '10')];
END $$;

-- ============================================================================
-- 1 · public.linha ganha numero e sufixo
-- ============================================================================
ALTER TABLE public.linha
    ADD COLUMN IF NOT EXISTS numero VARCHAR(8),
    ADD COLUMN IF NOT EXISTS sufixo VARCHAR(3);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.linha'::regclass AND conname = 'ck_linha_sufixo_digitos'
    ) THEN
        ALTER TABLE public.linha
            ADD CONSTRAINT ck_linha_sufixo_digitos CHECK (sufixo ~ '^[0-9]{1,3}$');
        RAISE NOTICE '048: CHECK ck_linha_sufixo_digitos criado.';
    END IF;
END $$;

COMMENT ON COLUMN public.linha.numero IS 'Número da linha (4 caracteres, ex.: 271A) — agrupa as linhas de mesmo número. NULL só nos placeholders de manobra MAN-<setor> e nas linhas fundidas (#fundida).';
COMMENT ON COLUMN public.linha.sufixo IS 'Código da linha na operação (1–3 dígitos, ex.: 51). 271A-10 e 271A-51 são linhas DIFERENTES. codigo = numero || ''-'' || sufixo, montado pelo backend.';

-- ============================================================================
-- 2 · Normaliza o catálogo (renomeia; funde duplicatas)
-- ============================================================================
DO $$
DECLARE
    g              RECORD;
    r              RECORD;
    fk             RECORD;
    canon          TEXT;
    keeper_id      UUID;
    keeper_codigo  TEXT;
    keeper_nome    TEXT;
    keeper_setor   TEXT;
    nome_real      TEXT;
    n              BIGINT;
    n_repontadas   BIGINT;
    n_renomeadas   INT := 0;
    n_ja_ok        INT := 0;
    n_fundidas     INT := 0;
    n_sem_regra    INT := 0;
BEGIN
    -- Linhas fora da R2 (menos manobra e as já fundidas): só avisa.
    FOR r IN
        SELECT codigo, nome FROM public.linha
         WHERE codigo NOT LIKE 'MAN-%' AND codigo NOT LIKE '%#fundida'
           AND pg_temp.normalizar_linha_048(codigo) IS NULL
         ORDER BY codigo
    LOOP
        n_sem_regra := n_sem_regra + 1;
        RAISE NOTICE '048: linha "%" (%) não bate com a regra número+código — NÃO mexida.', r.codigo, r.nome;
    END LOOP;

    FOR g IN
        SELECT (pg_temp.normalizar_linha_048(codigo))[1] AS numero,
               (pg_temp.normalizar_linha_048(codigo))[2] AS sufixo,
               count(*) AS qtd
          FROM public.linha
         WHERE codigo NOT LIKE 'MAN-%' AND codigo NOT LIKE '%#fundida'
           AND pg_temp.normalizar_linha_048(codigo) IS NOT NULL
         GROUP BY 1, 2
         ORDER BY 1, 2
    LOOP
        canon := g.numero || '-' || g.sufixo;

        IF g.qtd = 1 THEN
            SELECT id, codigo, nome, numero, sufixo INTO r
              FROM public.linha
             WHERE codigo NOT LIKE 'MAN-%' AND codigo NOT LIKE '%#fundida'
               AND (pg_temp.normalizar_linha_048(codigo))[1] = g.numero
               AND (pg_temp.normalizar_linha_048(codigo))[2] = g.sufixo;

            IF r.codigo = canon AND r.numero IS NOT DISTINCT FROM g.numero
               AND r.sufixo IS NOT DISTINCT FROM g.sufixo THEN
                n_ja_ok := n_ja_ok + 1;
                CONTINUE;
            END IF;

            UPDATE public.linha
               SET numero = g.numero,
                   sufixo = g.sufixo,
                   codigo = canon,
                   nome   = CASE WHEN nome = r.codigo THEN canon ELSE nome END
             WHERE id = r.id;
            IF r.codigo <> canon THEN
                n_renomeadas := n_renomeadas + 1;
                RAISE NOTICE '048: linha "%" → "%".', r.codigo, canon;
            ELSE
                n_ja_ok := n_ja_ok + 1;
            END IF;
            CONTINUE;
        END IF;

        -- ── Fusão: 2+ registros viram a mesma linha ─────────────────────────
        SELECT l.id, l.codigo, l.nome, l.setor::text
          INTO keeper_id, keeper_codigo, keeper_nome, keeper_setor
          FROM public.linha l
         WHERE l.codigo NOT LIKE 'MAN-%' AND l.codigo NOT LIKE '%#fundida'
           AND (pg_temp.normalizar_linha_048(l.codigo))[1] = g.numero
           AND (pg_temp.normalizar_linha_048(l.codigo))[2] = g.sufixo
         ORDER BY (SELECT count(*) FROM public.escala e WHERE e.linha_id = l.id) DESC,
                  l.criado_em ASC, l.codigo ASC
         LIMIT 1;

        BEGIN  -- subtransação: se qualquer passo falhar, a fusão inteira volta
            nome_real := CASE WHEN keeper_nome NOT IN (keeper_codigo, canon) THEN keeper_nome END;
            n_repontadas := 0;

            FOR r IN
                SELECT l.id, l.codigo, l.nome, l.setor::text AS setor
                  FROM public.linha l
                 WHERE l.id <> keeper_id
                   AND l.codigo NOT LIKE 'MAN-%' AND l.codigo NOT LIKE '%#fundida'
                   AND (pg_temp.normalizar_linha_048(l.codigo))[1] = g.numero
                   AND (pg_temp.normalizar_linha_048(l.codigo))[2] = g.sufixo
                 ORDER BY l.criado_em
            LOOP
                IF nome_real IS NULL AND r.nome NOT IN (r.codigo, canon) THEN
                    nome_real := r.nome;
                END IF;
                IF r.setor <> keeper_setor THEN
                    RAISE NOTICE '048: fusão %: "%" é % e a que fica ("%") é % — a que fica manda.',
                        canon, r.codigo, r.setor, keeper_codigo, keeper_setor;
                END IF;

                -- Libera o UNIQUE(codigo) e desativa (⛔ nunca DELETE).
                UPDATE public.linha
                   SET codigo = left(codigo, 12) || '#fundida',
                       ativa  = false,
                       numero = NULL,
                       sufixo = NULL
                 WHERE id = r.id;

                -- Reponta TODAS as FKs que apontam para linha.id.
                FOR fk IN
                    SELECT c.conrelid::regclass AS tabela, a.attname AS coluna
                      FROM pg_constraint c
                      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                     WHERE c.contype = 'f'
                       AND c.confrelid = 'public.linha'::regclass
                       AND array_length(c.conkey, 1) = 1
                LOOP
                    EXECUTE format('UPDATE %s SET %I = $1 WHERE %I = $2', fk.tabela, fk.coluna, fk.coluna)
                        USING keeper_id, r.id;
                    GET DIAGNOSTICS n = ROW_COUNT;
                    n_repontadas := n_repontadas + n;
                END LOOP;

                RAISE NOTICE '048: FUNDIDA "%" (%) em "%" — desativada como "%#fundida".',
                    r.codigo, r.nome, keeper_codigo, left(r.codigo, 12);
            END LOOP;

            UPDATE public.linha
               SET numero = g.numero,
                   sufixo = g.sufixo,
                   codigo = canon,
                   nome   = COALESCE(nome_real, canon)
             WHERE id = keeper_id;

            n_fundidas := n_fundidas + 1;
            RAISE NOTICE '048: fusão % concluída — fica "%" (nome "%"), % registro(s) repontado(s).',
                canon, keeper_codigo, COALESCE(nome_real, canon), n_repontadas;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE '048: fusão % NÃO feita (%). As linhas ficam como estavam, sem numero/sufixo — resolva à mão e rode a 048 de novo.',
                canon, SQLERRM;
        END;
    END LOOP;

    RAISE NOTICE '048: catálogo — % renomeada(s), % já no formato, % fusão(ões), % fora da regra.',
        n_renomeadas, n_ja_ok, n_fundidas, n_sem_regra;
END $$;

-- ============================================================================
-- 3 · UNIQUE (numero, sufixo)
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uq_linha_numero_sufixo
    ON public.linha (numero, sufixo)
    WHERE numero IS NOT NULL;

-- ============================================================================
-- 4 · Escala de Fiscais aponta para o cadastro único
-- ============================================================================
ALTER TABLE coordenadoria.escala_fiscal_posto_linha
    ADD COLUMN IF NOT EXISTS linha_id UUID REFERENCES public.linha(id) ON DELETE RESTRICT;
COMMENT ON COLUMN coordenadoria.escala_fiscal_posto_linha.linha_id IS 'Linha do cadastro único (public.linha). NULL = linha da escala ainda sem cadastro (a Fiscalização mostra "linha sem cadastro"). A coluna linha (texto) guarda o código canônico — regras, montagem e impressão leem a lista de textos.';
CREATE INDEX IF NOT EXISTS idx_escala_fiscal_posto_linha_linha_id
    ON coordenadoria.escala_fiscal_posto_linha (linha_id);

DO $$
DECLARE
    r            RECORD;
    par          TEXT[];
    canon        TEXT;
    v_linha_id   UUID;
    n_ligadas    INT := 0;
    n_reescritas INT := 0;
    n_duplicadas INT := 0;
    n_sem_regra  INT := 0;
    sem_cadastro TEXT[] := ARRAY[]::TEXT[];
BEGIN
    FOR r IN
        SELECT posto_id, linha, ordem, linha_id
          FROM coordenadoria.escala_fiscal_posto_linha
         ORDER BY posto_id, ordem, linha
    LOOP
        par := pg_temp.normalizar_linha_048(r.linha);
        IF par IS NULL THEN
            n_sem_regra := n_sem_regra + 1;
            RAISE NOTICE '048: posto % — linha "%" não bate com a regra número+código — NÃO mexida.', r.posto_id, r.linha;
            CONTINUE;
        END IF;
        canon := par[1] || '-' || par[2];

        -- 2127/10 e 2127-10 no mesmo posto = a mesma linha: apaga a repetida.
        IF r.linha <> canon AND EXISTS (
            SELECT 1 FROM coordenadoria.escala_fiscal_posto_linha x
             WHERE x.posto_id = r.posto_id AND x.linha = canon
        ) THEN
            DELETE FROM coordenadoria.escala_fiscal_posto_linha
             WHERE posto_id = r.posto_id AND linha = r.linha;
            n_duplicadas := n_duplicadas + 1;
            RAISE NOTICE '048: posto % — "%" repetia "%" — apagada.', r.posto_id, r.linha, canon;
            CONTINUE;
        END IF;

        SELECT id INTO v_linha_id
          FROM public.linha
         WHERE numero = par[1] AND sufixo = par[2];

        IF v_linha_id IS NULL AND NOT (canon = ANY (sem_cadastro)) THEN
            sem_cadastro := sem_cadastro || canon;
        END IF;

        IF r.linha <> canon OR r.linha_id IS DISTINCT FROM COALESCE(v_linha_id, r.linha_id) THEN
            UPDATE coordenadoria.escala_fiscal_posto_linha
               SET linha    = canon,
                   linha_id = COALESCE(v_linha_id, linha_id)
             WHERE posto_id = r.posto_id AND linha = r.linha;
            IF r.linha <> canon THEN
                n_reescritas := n_reescritas + 1;
            END IF;
            IF v_linha_id IS NOT NULL AND r.linha_id IS NULL THEN
                n_ligadas := n_ligadas + 1;
            END IF;
        END IF;
    END LOOP;

    IF array_length(sem_cadastro, 1) > 0 THEN
        RAISE NOTICE '048: % linha(s) da Escala SEM cadastro em public.linha (não criadas — setor não é seguro pelo lote; cadastre em Escala de Fiscais → Linhas): %',
            array_length(sem_cadastro, 1), array_to_string(sem_cadastro, ', ');
    END IF;
    RAISE NOTICE '048: Escala de Fiscais — % texto(s) reescrito(s) no canônico, % ligada(s) ao cadastro, % duplicata(s) apagada(s), % fora da regra.',
        n_reescritas, n_ligadas, n_duplicadas, n_sem_regra;
END $$;

-- ============================================================================
-- 5 · Fiscalização: texto no código canônico (1726 → 1726-10)
-- ============================================================================
DO $$
DECLARE
    t     TEXT;
    n     BIGINT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'linha_coordenador', 'turno_linha', 'registro_partida', 'evento_turno',
        'observacao_turno', 'baita', 'acao_coordenacao'
    ] LOOP
        IF to_regclass('fiscalizacao.' || t) IS NULL THEN
            RAISE NOTICE '048: fiscalizacao.% não existe — pulada.', t;
            CONTINUE;
        END IF;
        BEGIN
            EXECUTE format(
                'UPDATE fiscalizacao.%I
                    SET linha_codigo = (pg_temp.normalizar_linha_048(linha_codigo))[1] || ''-''
                                    || (pg_temp.normalizar_linha_048(linha_codigo))[2]
                  WHERE pg_temp.normalizar_linha_048(linha_codigo) IS NOT NULL
                    AND linha_codigo <> (pg_temp.normalizar_linha_048(linha_codigo))[1] || ''-''
                                     || (pg_temp.normalizar_linha_048(linha_codigo))[2]', t);
            GET DIAGNOSTICS n = ROW_COUNT;
            RAISE NOTICE '048: fiscalizacao.% — % registro(s) normalizado(s).', t, n;
        EXCEPTION WHEN unique_violation THEN
            RAISE NOTICE '048: fiscalizacao.% NÃO normalizada — o código canônico já existe ao lado do antigo (%). Resolva à mão e rode de novo.', t, SQLERRM;
        END;
    END LOOP;
END $$;

DROP FUNCTION IF EXISTS pg_temp.normalizar_linha_048(TEXT);

-- ============================================================================
-- 6 · 🔴 GRANT (a falta de GRANT derrubou produção em 17/09)
-- ============================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON public.linha TO sambaiba;
GRANT USAGE ON SCHEMA coordenadoria TO sambaiba;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA coordenadoria TO sambaiba;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA coordenadoria TO sambaiba;
GRANT USAGE ON SCHEMA fiscalizacao TO sambaiba;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA fiscalizacao TO sambaiba;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA fiscalizacao TO sambaiba;

COMMIT;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- linhas sem número (esperado: só MAN-* e #fundida)
--   SELECT codigo, nome, ativa FROM public.linha WHERE numero IS NULL ORDER BY codigo;
--   -- fundidas
--   SELECT codigo, nome, setor FROM public.linha WHERE codigo LIKE '%#fundida';
--   -- código fora do formato canônico (esperado: 0)
--   SELECT codigo FROM public.linha WHERE numero IS NOT NULL AND codigo <> numero || '-' || sufixo;
--   -- postos da escala sem cadastro de linha
--   SELECT pl.linha, count(*) FROM coordenadoria.escala_fiscal_posto_linha pl
--    WHERE pl.linha_id IS NULL GROUP BY 1 ORDER BY 1;
--   -- escala por linha (rodar ANTES e DEPOIS; o total tem que bater)
--   SELECT l.codigo, count(e.id) FROM public.linha l LEFT JOIN public.escala e ON e.linha_id = l.id
--    GROUP BY 1 ORDER BY 1;
--   SELECT count(*) FROM public.escala;
--   SELECT has_table_privilege('sambaiba', 'coordenadoria.escala_fiscal_posto_linha', 'UPDATE');  -- t
-- ============================================================================

-- ============================================================================
-- ROLLBACK (parcial — o texto antigo dos códigos NÃO volta)
-- ============================================================================
-- ALTER TABLE coordenadoria.escala_fiscal_posto_linha DROP COLUMN IF EXISTS linha_id;
-- DROP INDEX IF EXISTS public.uq_linha_numero_sufixo;
-- ALTER TABLE public.linha DROP CONSTRAINT IF EXISTS ck_linha_sufixo_digitos;
-- ALTER TABLE public.linha DROP COLUMN IF EXISTS numero, DROP COLUMN IF EXISTS sufixo;
-- -- Linha fundida: reativar à mão (UPDATE public.linha SET ativa = true,
-- -- codigo = replace(codigo, '#fundida', '') WHERE codigo LIKE '%#fundida')
-- -- — as escalas repontadas continuam na que ficou.
-- ============================================================================
