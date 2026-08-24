-- ============================================================================
-- MIGRATION 036 — Avaria na saída da frota (funcionalidade nova)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria (existente — migration 024)
-- DATA:   2026-08-24
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (schema portaria, recurso acesso_veicular)
-- ORIGEM: _handoff-claude/PROMPT-portaria-ajustes-2026-08-24.md, Bloco G
-- ----------------------------------------------------------------------------
-- ⚠️ NÚMERO — nasce depois da 037 no relógio (Bloco I, já aplicada), mas é
--   independente dela — rodar em ordem numérica 034→035→036→037 funciona.
--
-- 🟢 100% ADITIVA — só CREATE TABLE/INDEX IF NOT EXISTS. Nenhum ALTER
--   TABLE, DROP ou RENAME em tabela existente. Nenhuma permissão nova: ver
--   RBAC abaixo.
--
-- POR QUÊ
--   O controlador confere o carro saindo do pátio e vê um para-choque
--   quebrado, um retrovisor rachado, um risco na lateral. Hoje não tem onde
--   anotar — não é recolhida (o carro está saindo, não voltando) e não é
--   ocorrência (não houve sinistro). Depois, quando o carro volta com dano
--   maior, ninguém sabe dizer se já saiu assim.
--
-- SNAPSHOT, NÃO FK (mesma decisão de recolhida_anormal, migration 026) —
--   prefixo e motorista_nome são o que o controlador VIU naquele momento;
--   renumeração de frota ou troca de cadastro não pode reescrever o passado.
--   ⛔ Sem FK para onibus/motorista (regra de fronteira do módulo).
--
-- ⚠️ EXPURGO — o Alisson pediu "apaga em um ou dois meses". 60 dias.
--   O projeto não tem scheduler (mesma decisão da migration 028, pré-
--   cadastro) — nenhum cron/job de background nesta rodada. Em vez disso:
--   GET /portaria/avarias filtra `expira_em > NOW()` no backend
--   (routers/portaria_avarias.py) — o registro vencido some da tela na hora
--   certa mesmo sem ninguém rodar nada. Limpeza FÍSICA da linha fica para
--   quando existir scheduler.
--
-- RBAC — NENHUM RECURSO NOVO. Reaproveita `acesso_veicular` (migration
--   024): quem confere o carro saindo (POST /portaria/movimentos) é a
--   mesma pessoa que registra a avaria vista na saída — mesmo controlador,
--   mesmo ato de guarita. Menor privilégio (padrão da migration 020): criar
--   um recurso novo só duplicaria a permissão de quem já tem exatamente o
--   escopo certo.
--
-- ⚠️ DADO PESSOAL: motorista_re/motorista_nome (snapshot, opcional) — mesma
--   natureza de recolhida_anormal.motorista_re (migration 026). Nunca
--   cpf/rg/cnh nesta tabela.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 036-avaria-saida-frota.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS portaria.avaria_saida (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- SNAPSHOT de texto, não FK: a avaria é do que o controlador viu naquele
    -- momento; renumeração de frota não pode reescrever o passado (mesma
    -- decisão de recolhida_anormal, migration 026).
    prefixo        VARCHAR(10)  NOT NULL,
    data_servico   DATE         NOT NULL,
    ocorrido_em    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    motorista_re   VARCHAR(20),
    motorista_nome VARCHAR(120),
    descricao      TEXT         NOT NULL,
    registrado_por UUID         NOT NULL REFERENCES public.funcionario(id),
    criado_em      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Expurgo: o Alisson pediu "apaga em um ou dois meses". 60 dias.
    expira_em      TIMESTAMPTZ  NOT NULL DEFAULT NOW() + INTERVAL '60 days'
);

CREATE INDEX IF NOT EXISTS ix_avaria_saida_prefixo ON portaria.avaria_saida (prefixo, ocorrido_em DESC);
CREATE INDEX IF NOT EXISTS ix_avaria_saida_expira  ON portaria.avaria_saida (expira_em);

COMMENT ON TABLE portaria.avaria_saida IS 'Avaria observada na conferência de saída da frota. Retenção curta (60 dias) — serve para comparar o estado do carro na saída com o da volta, não é histórico permanente de frota nem substitui ficha de manutenção.';
COMMENT ON COLUMN portaria.avaria_saida.prefixo IS 'Snapshot texto, não FK — mesma decisão de recolhida_anormal.prefixo (migration 026).';
COMMENT ON COLUMN portaria.avaria_saida.data_servico IS 'Ciclo operacional (backend: get_data_servico(), vira às 20h) — não é a mesma regra de portaria.movimento.data_referencia (D9, 24h corrido, divergência proposital documentada na migration 024). A avaria acompanha o dia de OPERAÇÃO do carro, não o dia de calendário do portão.';
COMMENT ON COLUMN portaria.avaria_saida.expira_em IS '60 dias — expurgo por filtro (GET /portaria/avarias exige expira_em > NOW()), não por job: o projeto não tem scheduler (mesma decisão da migration 028). Limpeza física da linha fica para quando existir.';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT count(*) FROM portaria.avaria_saida;  -- esperado: 0 (tabela nova)
--
--   -- Os dois índices existem:
--   SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'portaria' AND tablename = 'avaria_saida';
--   -- esperado: ix_avaria_saida_prefixo, ix_avaria_saida_expira (+ PK)
--
--   -- Nenhum recurso/permissão novo — RBAC reaproveita acesso_veicular:
--   SELECT fp.recurso, fp.pode_ler, fp.pode_escrever
--     FROM public.funcao_permissao fp
--     JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fn.codigo = 'CONTROLADOR_ACESSO' AND fp.recurso = 'acesso_veicular';
--   -- esperado: já existia antes desta migration (TRUE, TRUE)
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DROP TABLE IF EXISTS portaria.avaria_saida;
-- (Seguro: nenhuma outra tabela referencia portaria.avaria_saida.)
-- ============================================================================
