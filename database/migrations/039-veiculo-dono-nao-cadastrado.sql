-- ============================================================================
-- MIGRATION 039 — Veículo PARTICULAR cadastra mesmo sem o dono no cadastro
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria (existente — migration 024)
-- DATA:   2026-09-02
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (tabela portaria.veiculo, CONSTRAINT
--             ck_veiculo_dono), 028-pessoa-pre-cadastro.sql (pessoa_pre_cadastro)
-- ORIGEM: _handoff-claude/PROMPT-portaria-correcoes-2026-08-25.md, C1
-- ----------------------------------------------------------------------------
-- 🔴 BUG DE PRODUÇÃO — trava o controlador hoje. O controlador vai cadastrar
--   um carro PARTICULAR, digita o RE do dono, e /portaria/funcionarios/busca
--   não devolve ninguém (a pessoa nunca foi cadastrada como funcionário, ou
--   o RE está diferente). Sem funcionario_id, o cadastro é recusado com 422
--   e o carro não entra no sistema — viola a regra número um do módulo
--   Portaria (nunca impedir um registro). Todo o resto do módulo só avisa
--   (placa fora do padrão, veículo suspenso, hodômetro faltando); este era o
--   único ponto que recusava.
--
-- 🟢 QUASE ADITIVA — um ALTER TABLE ADD COLUMN e uma troca de CONSTRAINT
--   (DROP + ADD, mesmo nome). Nenhuma tabela nova, nenhum DROP de dado.
--
-- COMO CORRIGE — cadastra mesmo sem achar o RE, guardando o que o
--   controlador digitou em re_dono_texto (SNAPSHOT, não FK). Quando alguém
--   promover essa pessoa a funcionário (aba Pré-cadastros, Bloco F), o
--   veículo passa a ser vinculado por funcionario_id e a coluna vira
--   histórico. ⛔ re_dono_texto NUNCA é a fonte de verdade do dono —
--   funcionario_id é. É por isso que o CHECK abaixo aceita "um dos dois",
--   nunca promove re_dono_texto a substituto do vínculo.
--
-- ⚠️ O CHECK continua APERTADO nas outras duas pontas (a revisão de 20/08
--   fechou EMPRESA e TERCEIRO de propósito) — a única frouxidão é aceitar
--   re_dono_texto no lugar de funcionario_id, e ainda assim PARTICULAR
--   precisa de UM dos dois. ⛔ Nunca deixar os dois nulos.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 039-veiculo-dono-nao-cadastrado.sql
-- ============================================================================

ALTER TABLE portaria.veiculo
    ADD COLUMN IF NOT EXISTS re_dono_texto VARCHAR(20);

COMMENT ON COLUMN portaria.veiculo.re_dono_texto IS
'SNAPSHOT do RE que o controlador digitou quando a pessoa não estava no cadastro de '
'funcionário. Regra número um: o registro nunca é recusado. Quando alguém promover essa '
'pessoa a funcionário (aba Pré-cadastros), o veículo é vinculado por funcionario_id e '
'esta coluna vira histórico. ⛔ Nunca é a fonte de verdade do dono — funcionario_id é.';

ALTER TABLE portaria.veiculo DROP CONSTRAINT IF EXISTS ck_veiculo_dono;
ALTER TABLE portaria.veiculo ADD CONSTRAINT ck_veiculo_dono CHECK (
    (propriedade = 'PARTICULAR'
       AND empresa_terceira_id IS NULL
       AND (funcionario_id IS NOT NULL OR re_dono_texto IS NOT NULL)) OR
    (propriedade = 'EMPRESA'  AND funcionario_id IS NULL AND empresa_terceira_id IS NULL) OR
    (propriedade = 'TERCEIRO' AND funcionario_id IS NULL AND empresa_terceira_id IS NOT NULL)
);

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- Coluna nova existe:
--   SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'portaria' AND table_name = 'veiculo' AND column_name = 're_dono_texto';
--
--   -- Nenhum PARTICULAR ficou com os dois nulos (regra número um do CHECK):
--   SELECT count(*) FROM portaria.veiculo WHERE propriedade = 'PARTICULAR'
--     AND funcionario_id IS NULL AND re_dono_texto IS NULL;   -- esperado: 0, sempre
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE portaria.veiculo DROP CONSTRAINT IF EXISTS ck_veiculo_dono;
-- ALTER TABLE portaria.veiculo ADD CONSTRAINT ck_veiculo_dono CHECK (
--     (propriedade = 'PARTICULAR' AND funcionario_id IS NOT NULL AND empresa_terceira_id IS NULL) OR
--     (propriedade = 'EMPRESA'    AND funcionario_id IS NULL     AND empresa_terceira_id IS NULL) OR
--     (propriedade = 'TERCEIRO'   AND funcionario_id IS NULL     AND empresa_terceira_id IS NOT NULL)
-- );
-- ⚠️ Só reverter o CHECK se nenhuma linha depender de re_dono_texto sozinho —
-- senão o ALTER acima falha (linha existente violaria o CHECK antigo).
-- ALTER TABLE portaria.veiculo DROP COLUMN IF EXISTS re_dono_texto;
-- ============================================================================
