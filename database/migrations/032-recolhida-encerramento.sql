-- ============================================================================
-- MIGRATION 032 — Portaria: encerramento da recolhida anormal
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria  (existente — migration 024)
-- DATA:   2026-08-23
-- AUTOR:  Claude Code
-- DEPENDE DE: 026-recolhida-anormal.sql (portaria.recolhida_anormal,
--             status AGUARDANDO/AVALIADA/DESCARTADA), 024-modulo-portaria.sql
--             (RBAC), 006-functions-triggers.sql (fn_ficha_concluida_em —
--             preenche ficha_manutencao.concluida_em ao entrar em
--             CONCLUIDA/CANCELADA, ver services/manutencao_recolhida.py)
-- ORIGEM: prompt de execução "Barra por módulo + RA como aba da Manutenção",
--         Fase 2 (sessão de trabalho de 23/08/2026)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Hoje o ciclo da recolhida para em AVALIADA: a manutenção diz LIBERADO/
--   RETIDO e a ficha (quando existe) fica aberta pra sempre. Falta o
--   fechamento — a manutenção dizer se não havia defeito ou se o serviço foi
--   executado, e esse desfecho refletir na ficha_manutencao que a própria RA
--   abriu (ver services/manutencao_recolhida.py).
--
-- 🟡 NÃO 100% ADITIVA EM SCHEMA (diferente da 026) — esta migration troca o
--   CHECK de `status` (DROP + ADD, é VARCHAR simples, não enum do Postgres,
--   então não tem ALTER TYPE ... ADD VALUE) pra caber 'ENCERRADA'. As duas
--   ALTER TABLE de constraint são idempotentes por construção (DROP
--   CONSTRAINT IF EXISTS seguido de ADD CONSTRAINT do mesmo nome) — rodar
--   esta migration duas vezes não muda nada na segunda. As 4 colunas novas
--   são 100% aditivas (ADD COLUMN IF NOT EXISTS), como sempre.
--
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 032-recolhida-encerramento.sql
-- ============================================================================

-- ============================================================================
-- 1 · COLUNAS NOVAS — o que a MANUTENÇÃO informa ao encerrar.
-- ============================================================================
ALTER TABLE portaria.recolhida_anormal
    ADD COLUMN IF NOT EXISTS desfecho VARCHAR(20)
        CHECK (desfecho IN ('SEM_DEFEITO','SERVICO_EXECUTADO')),
    ADD COLUMN IF NOT EXISTS encerramento_relato TEXT,
    ADD COLUMN IF NOT EXISTS encerrado_por UUID REFERENCES public.funcionario(id),
    ADD COLUMN IF NOT EXISTS encerrado_em TIMESTAMPTZ;

COMMENT ON COLUMN portaria.recolhida_anormal.desfecho IS 'Fechamento do ciclo (Fase 2/23-08): SEM_DEFEITO ou SERVICO_EXECUTADO. Só preenchido quando status=ENCERRADA (ver ck_recolhida_desfecho_status) — quem avalia (AVALIADA) não é necessariamente quem encerra, são dois momentos diferentes na operação real.';
COMMENT ON COLUMN portaria.recolhida_anormal.encerramento_relato IS 'Descrição livre do mecânico no encerramento — opcional, nunca obrigatória (regra número um: nada impede o registro).';
COMMENT ON COLUMN portaria.recolhida_anormal.encerrado_por IS 'Quem encerrou — mesmo recurso `manutencao` escrever de quem avalia (avaliado_por). Quem avalia é quem encerra.';
COMMENT ON COLUMN portaria.recolhida_anormal.encerrado_em IS 'UTC — carimbado pelo backend no momento do encerramento, mesmo padrão de avaliado_em.';

-- ============================================================================
-- 2 · STATUS ganha ENCERRADA — VARCHAR simples (não enum do Postgres), então
--     é DROP + ADD do CHECK, não ALTER TYPE ... ADD VALUE. 'ENCERRADA' tem 9
--     caracteres — cabe no VARCHAR(12) atual, não precisa alargar a coluna.
-- ============================================================================
ALTER TABLE portaria.recolhida_anormal
    DROP CONSTRAINT IF EXISTS recolhida_anormal_status_check;

ALTER TABLE portaria.recolhida_anormal
    ADD CONSTRAINT recolhida_anormal_status_check
    CHECK (status IN ('AGUARDANDO','AVALIADA','DESCARTADA','ENCERRADA'));

-- ============================================================================
-- 3 · CONSISTÊNCIA — desfecho preenchido ⟺ status = ENCERRADA. Um não
--     existe sem o outro (nem ENCERRADA sem desfecho, nem desfecho fora de
--     ENCERRADA).
-- ============================================================================
ALTER TABLE portaria.recolhida_anormal
    DROP CONSTRAINT IF EXISTS ck_recolhida_desfecho_status;

ALTER TABLE portaria.recolhida_anormal
    ADD CONSTRAINT ck_recolhida_desfecho_status
    CHECK ((desfecho IS NOT NULL) = (status = 'ENCERRADA'));

-- ============================================================================
-- 4 · ÍNDICE — nenhum novo. idx_recolhida_status (status, momento DESC),
--     criado na 026, já cobre tanto a fila "em aberto" (status IN
--     ('AGUARDANDO','AVALIADA'), ordenado por momento) quanto a conferência
--     do dia (status='ENCERRADA' + filtro de data_referencia, que tem seu
--     próprio índice idx_recolhida_data) — um índice parcial a mais não
--     mudaria plano de consulta pra fila deste tamanho (uma oficina, não um
--     data warehouse). Criar sem uso real seria índice de propósito
--     nenhum, só custo de escrita.
-- ============================================================================

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- 4 colunas novas, todas NULL (nenhuma RA foi encerrada ainda):
--   SELECT count(*) FROM portaria.recolhida_anormal WHERE desfecho IS NOT NULL;  -- esperado: 0
--
--   -- Status aceita ENCERRADA:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'portaria.recolhida_anormal'::regclass
--      AND conname = 'recolhida_anormal_status_check';
--
--   -- Prova da consistência (esperado: 0 linhas em ambos os UPDATE de teste,
--   -- não rode em produção — só documentação do que o CHECK barra):
--   -- UPDATE portaria.recolhida_anormal SET status = 'ENCERRADA' WHERE id = '<algum id>';               -- deve falhar sem desfecho
--   -- UPDATE portaria.recolhida_anormal SET desfecho = 'SEM_DEFEITO' WHERE id = '<algum id>';            -- deve falhar fora de ENCERRADA
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ALTER TABLE portaria.recolhida_anormal DROP CONSTRAINT IF EXISTS ck_recolhida_desfecho_status;
-- ALTER TABLE portaria.recolhida_anormal DROP CONSTRAINT IF EXISTS recolhida_anormal_status_check;
-- ALTER TABLE portaria.recolhida_anormal
--     ADD CONSTRAINT recolhida_anormal_status_check
--     CHECK (status IN ('AGUARDANDO','AVALIADA','DESCARTADA'));
-- ALTER TABLE portaria.recolhida_anormal
--     DROP COLUMN IF EXISTS desfecho,
--     DROP COLUMN IF EXISTS encerramento_relato,
--     DROP COLUMN IF EXISTS encerrado_por,
--     DROP COLUMN IF EXISTS encerrado_em;
-- ⚠️ Só reverta se nenhuma RA tiver sido ENCERRADA ainda (senão o rollback
-- do status_check acima falha, porque existiria linha com status='ENCERRADA'
-- fora do CHECK restaurado — decida manualmente esses casos primeiro).
-- ============================================================================
