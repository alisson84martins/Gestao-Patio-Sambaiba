-- ============================================================================
-- MIGRATION 026 — Portaria: recolhida anormal — Bloco F
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria  (existente — migration 024)
-- DATA:   2026-08-21
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (portaria.local, RBAC),
--             011-rbac-cadastro-central.sql (public.funcionario/recurso/
--             funcao/funcao_permissao), tabelas legadas public.onibus,
--             public.ficha_manutencao
-- ORIGEM: _handoff-claude/PROMPT-portaria-blocos-E-F.md, Bloco F
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Ônibus que recolhe fora de hora, com defeito — no pior momento possível
--   (frota quase toda na rua, faltam carros pra repor). Recolhida anormal
--   NÃO é evento de manutenção, é evento de OPERAÇÃO: a pergunta urgente é
--   "esse carro volta? em quanto tempo? preciso mandar outro?", não "qual o
--   defeito". Três estações, cada uma vê só a sua face:
--     1 Portaria     — registra carro/linha/defeito/relato
--     2 Manutenção   — avalia: LIBERADO (com prazo) ou RETIDO
--     3 Gerência     — recebe a resposta e decide a reposição, vê a análise
--
-- 🔴 A REGRA DO MOTORISTA
--   Associar motorista+carro+linha é o objetivo analítico central — mas a
--   tela da PORTARIA não mostra, não pede e não devolve motorista nem
--   cobrador (esse dado é gerencial). O backend resolve pela escala
--   (prefixo + data_referencia + hora) — o controlador não digita nada
--   disso. Não resolveu -> grava NAO_IDENTIFICADO, campos nulos, registra
--   a recolhida assim mesmo (regra número um).
--
-- ⚠️ COBRADOR — divergência registrada, não silenciosa (decisão de
--   21/08/2026, revisão deste bloco): a tabela `escala` só tem
--   `motorista_id`, nenhum campo de cobrador — não existe, em lugar nenhum
--   do sistema, um cadastro de cobrador ligado a horário/carro pra
--   resolver por join. As colunas cobrador_re/cobrador_nome abaixo EXISTEM
--   (o prompt original pediu) mas o backend NUNCA as preenche
--   automaticamente — ficam sempre NULL até existir uma fonte de dado real
--   pra isso. `origem_identificacao='ESCALA'` significa "motorista
--   resolvido pela escala", não "motorista e cobrador".
--
-- REGRA DE FRONTEIRA (mesma da 012/024) — COM UMA EXCEÇÃO DELIBERADA
--   Nenhuma FK para onibus, linha, motorista, alocacao_patio, fila, alerta,
--   escala — prefixo/linha são SNAPSHOT texto, tipo_defeito_codigo é texto
--   do código do catálogo (não FK: o catálogo pode mudar e o histórico
--   precisa continuar verdadeiro).
--   ⚠️ A ÚNICA EXCEÇÃO: FK para public.ficha_manutencao(id). É deliberada —
--   a ligação recolhida↔ficha é o PRODUTO da feature. A escrita do lado da
--   ficha é sempre um INSERT de linha nova (nunca UPDATE/DELETE em dado do
--   Pátio) — ver services/manutencao_recolhida.py.
--
-- FINALIDADE DO DADO (pra constar, ver também routers/portaria_recolhidas.py)
--   Este dado existe para melhoria de processo e de frota. A associação
--   motorista↔defeito serve pra encontrar padrão de operação e necessidade
--   de treinamento — o dado é do VEÍCULO, não da pessoa. Por isso a tela da
--   portaria não vê motorista, e só quem tem recolhida_gerencial vê a
--   análise por motorista.
--
-- ⚠️ Esta migration NÃO usa SET search_path — todo objeto qualificado
--   explicitamente com o schema (portaria./public.).
--
-- NATUREZA: 100% ADITIVA E IDEMPOTENTE.
--   CREATE TABLE/INDEX IF NOT EXISTS. INSERT ... ON CONFLICT DO NOTHING.
--   Nenhum ALTER TABLE, DROP ou RENAME em tabela existente. Nenhuma view
--   existente é tocada. Nenhuma permissão de `manutencao` é alterada — a
--   avaliação usa esse recurso já existente, só leitura aqui.
--
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 026-recolhida-anormal.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS portaria.recolhida_anormal (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    local_codigo        VARCHAR(20) NOT NULL REFERENCES portaria.local(codigo),
    momento             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data_referencia     DATE NOT NULL DEFAULT (NOW() AT TIME ZONE 'America/Sao_Paulo')::date,

    -- ── o que a PORTARIA informa ──────────────────────────────────────
    prefixo             VARCHAR(10) NOT NULL,   -- snapshot texto; ⛔ sem FK para onibus
    onibus_id           UUID,                   -- resolvido pelo backend, quando existir; ⛔ sem FK (regra de fronteira)
    linha_codigo        VARCHAR(20),            -- snapshot texto; ⛔ sem FK para linha
    tipo_defeito_codigo VARCHAR(20) NOT NULL,    -- código do catálogo tipo_defeito; ⛔ sem FK
    relato              TEXT,

    -- ── 🔴 GERENCIAL — nunca exposto na tela da portaria ──────────────
    motorista_re        VARCHAR(20),
    motorista_nome      VARCHAR(120),
    cobrador_re         VARCHAR(20),
    cobrador_nome       VARCHAR(120),
    origem_identificacao VARCHAR(16) NOT NULL DEFAULT 'NAO_IDENTIFICADO'
                        CHECK (origem_identificacao IN ('ESCALA','MANUAL','NAO_IDENTIFICADO')),

    -- ── ligação com a manutenção — ÚNICA exceção à regra de fronteira ──
    ficha_id            UUID REFERENCES public.ficha_manutencao(id),
    ficha_falhou_motivo TEXT,   -- por que a ficha não nasceu, quando não nasceu

    -- ── resposta da MANUTENÇÃO ───────────────────────────────────────
    avaliacao           VARCHAR(12)
                        CHECK (avaliacao IN ('LIBERADO','RETIDO')),
    prazo_minutos       INTEGER CHECK (prazo_minutos >= 0),
    avaliacao_relato    TEXT,
    avaliado_por        UUID REFERENCES public.funcionario(id),
    avaliado_em         TIMESTAMPTZ,

    status              VARCHAR(12) NOT NULL DEFAULT 'AGUARDANDO'
                        CHECK (status IN ('AGUARDANDO','AVALIADA','DESCARTADA')),

    registrado_por      UUID NOT NULL REFERENCES public.funcionario(id),
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE portaria.recolhida_anormal IS 'Ônibus que recolhe fora de hora, com defeito — evento de OPERAÇÃO, não de manutenção (a pergunta urgente é "esse carro volta? em quanto tempo?"). Existe para melhoria de processo e de frota: a associação motorista↔defeito serve pra encontrar padrão de operação e necessidade de treinamento — o dado é do VEÍCULO, não da pessoa. Por isso a tela da portaria (recurso recolhida_anormal) nunca vê motorista/cobrador; só quem tem recolhida_gerencial vê.';
COMMENT ON COLUMN portaria.recolhida_anormal.ficha_id IS '⚠️ ÚNICA exceção à regra de fronteira deste módulo — FK para public.ficha_manutencao(id), deliberada porque a ligação recolhida↔ficha é o produto da feature. A escrita do lado da ficha é sempre um INSERT de linha nova (nunca UPDATE/DELETE em dado do Pátio).';
COMMENT ON COLUMN portaria.recolhida_anormal.motorista_re IS 'GERENCIAL — nunca devolvido por endpoint que não exige recolhida_gerencial. Resolvido pela escala (prefixo+data+hora), nunca digitado pela portaria.';
COMMENT ON COLUMN portaria.recolhida_anormal.cobrador_re IS 'GERENCIAL — existe na tabela, mas o backend NUNCA preenche sozinho: a tabela escala não tem campo de cobrador (só motorista_id), não há de onde resolver por join. Fica NULL até existir uma fonte de dado real. Ver cabeçalho desta migration.';
COMMENT ON COLUMN portaria.recolhida_anormal.origem_identificacao IS 'ESCALA = motorista resolvido via escala (cobrador não — ver comentário da coluna cobrador_re). MANUAL = reservado para completar depois na visão gerencial. NAO_IDENTIFICADO = não resolveu; a recolhida foi registrada do mesmo jeito (regra número um).';
COMMENT ON COLUMN portaria.recolhida_anormal.tipo_defeito_codigo IS 'Código do catálogo tipo_defeito, TEXTO — não FK. O catálogo pode mudar e o histórico precisa continuar verdadeiro (mesmo princípio dos snapshots do resto do módulo).';

CREATE INDEX IF NOT EXISTS idx_recolhida_status  ON portaria.recolhida_anormal (status, momento DESC);
CREATE INDEX IF NOT EXISTS idx_recolhida_data    ON portaria.recolhida_anormal (data_referencia);
CREATE INDEX IF NOT EXISTS idx_recolhida_prefixo ON portaria.recolhida_anormal (prefixo, momento DESC);
CREATE INDEX IF NOT EXISTS idx_recolhida_motorista ON portaria.recolhida_anormal (motorista_re)
    WHERE motorista_re IS NOT NULL;

-- ============================================================================
-- RBAC — dois recursos novos (§2.4). Nenhuma permissão de `manutencao` é
-- tocada aqui — a avaliação da manutenção usa esse recurso já existente.
-- ============================================================================
INSERT INTO public.recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
  ('recolhida_anormal',  'Recolhida Anormal', 'Registro de recolhida com defeito na portaria.', 'PORTARIA', 14),
  ('recolhida_gerencial','Análise de Recolhidas', 'Visão gerencial: motorista, carro e linha.',  'PORTARIA', 15)
ON CONFLICT (codigo) DO NOTHING;

-- 🔴 CONTROLADOR_ACESSO não tem recolhida_gerencial — é essa linha que
-- garante que a portaria não vê motorista.
INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, v.recurso, v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('CONTROLADOR_ACESSO',   'recolhida_anormal',   TRUE,  TRUE),

        ('MECANICO',             'recolhida_anormal',   TRUE,  TRUE),

        ('COORDENADOR_TRAFEGO',  'recolhida_anormal',   TRUE,  FALSE),
        ('COORDENADOR_TRAFEGO',  'recolhida_gerencial', TRUE,  FALSE),

        ('ENCARREGADO',          'recolhida_anormal',   TRUE,  FALSE),
        ('ENCARREGADO',          'recolhida_gerencial', TRUE,  FALSE),

        ('GERENTE_OPERACIONAL',  'recolhida_anormal',   TRUE,  FALSE),
        ('GERENTE_OPERACIONAL',  'recolhida_gerencial', TRUE,  FALSE),

        ('GERENTE_GERAL',        'recolhida_anormal',   TRUE,  FALSE),
        ('GERENTE_GERAL',        'recolhida_gerencial', TRUE,  FALSE),

        ('ADMIN',                'recolhida_anormal',   TRUE,  TRUE),
        ('ADMIN',                'recolhida_gerencial', TRUE,  FALSE)
       ) AS v(funcao_codigo, recurso, pode_ler, pode_escrever) ON v.funcao_codigo = fn.codigo
 ON CONFLICT ON CONSTRAINT uq_funcao_permissao DO NOTHING;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT count(*) FROM portaria.recolhida_anormal;  -- esperado: 0 (tabela nova)
--
-- Prova de que CONTROLADOR_ACESSO não tem recolhida_gerencial (esperado: 0 linhas):
--   SELECT fp.* FROM public.funcao_permissao fp
--     JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fn.codigo = 'CONTROLADOR_ACESSO' AND fp.recurso = 'recolhida_gerencial';
--
-- Prova de que MECANICO segue com manutencao escrever, sem alteração (esperado: pode_escrever = TRUE):
--   SELECT fp.pode_ler, fp.pode_escrever FROM public.funcao_permissao fp
--     JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fn.codigo = 'MECANICO' AND fp.recurso = 'manutencao';
--
-- Prova da fronteira — só ficha_manutencao é FK pra fora do schema, além de
-- funcionario/local (esperado: só ficha_manutencao, funcionario e local):
--   SELECT ccu.table_schema || '.' || ccu.table_name AS aponta_para
--     FROM information_schema.table_constraints tc
--     JOIN information_schema.constraint_column_usage ccu
--       ON ccu.constraint_name = tc.constraint_name
--    WHERE tc.table_name = 'recolhida_anormal' AND tc.constraint_type = 'FOREIGN KEY';
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DELETE FROM public.funcao_permissao WHERE recurso IN ('recolhida_anormal','recolhida_gerencial');
-- DELETE FROM public.recurso WHERE codigo IN ('recolhida_anormal','recolhida_gerencial');
-- DROP TABLE IF EXISTS portaria.recolhida_anormal;
-- (Seguro: nenhuma outra tabela referencia portaria.recolhida_anormal.)
-- ============================================================================
