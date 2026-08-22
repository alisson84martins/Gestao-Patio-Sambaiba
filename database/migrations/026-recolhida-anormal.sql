-- ============================================================================
-- MIGRATION 026 — Portaria: recolhida anormal — Blocos F + G
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria  (existente — migration 024)
-- DATA:   2026-08-21
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (RBAC),
--             011-rbac-cadastro-central.sql (public.funcionario/recurso/
--             funcao/funcao_permissao), tabelas legadas public.onibus,
--             public.ficha_manutencao
-- ORIGEM: _handoff-claude/PROMPT-portaria-blocos-E-F.md, Bloco F, corrigido
--         pela revisão §2.9 (correção de escopo — motorista/cobrador
--         digitados pelo controlador, não resolvidos só pela escala) e
--         estendido pelo §5.1 (Bloco G — motivo da recolhida)
-- ⚠️ Esta migration nunca rodou em banco nenhum (confirmado com o Alisson,
--   21/08/2026) — por isso a correção de escopo do §2.9 e o Bloco G foram
--   incorporados direto nesta 026, em vez de 027/028 aditivas. Ver
--   checklist do prompt.
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Ônibus que recolhe fora de hora, com defeito — no pior momento possível
--   (frota quase toda na rua, faltam carros pra repor). Recolhida anormal
--   NÃO é evento de manutenção, é evento de OPERAÇÃO: a pergunta urgente é
--   "esse carro volta? em quanto tempo? preciso mandar outro?", não "qual o
--   defeito". Três estações, cada uma vê só a sua face:
--     1 Portaria     — registra carro/linha/defeito/motorista/cobrador/relato
--     2 Manutenção   — avalia: LIBERADO (com prazo) ou RETIDO
--     3 Gerência     — recebe a resposta e decide a reposição, vê a análise
--
-- 🔴 A REGRA DO MOTORISTA (corrigida em 21/08 — §2.9-0 do prompt)
--   Associar motorista+carro+linha é o objetivo analítico central, e a
--   separação NÃO é sobre o campo — é sobre o ACUMULADO:
--     • O controlador DIGITA motorista_re e cobrador_re (ele está com o
--       carro na frente, é a melhor fonte do dado, não a pior).
--     • O controlador NUNCA vê histórico, agregado ou ranking — isso é
--       gerencial (recurso recolhida_gerencial).
--   A escala continua servindo de PRÉ-PREENCHIMENTO (sugestão) pro RE do
--   motorista — nunca fonte única, nunca trava o registro se não resolver.
--   origem_identificacao registra de onde veio o dado, não se é visível:
--     PORTARIA = controlador digitou (o caso normal)
--     ESCALA   = veio sugerido da escala e ele confirmou sem alterar
--     NAO_INFORMADO = deixou em branco (permitido — regra número um)
--
-- ⚠️ COBRADOR — divergência registrada, não silenciosa (decisão de
--   21/08/2026): a tabela `escala` só tem `motorista_id`, nenhum campo de
--   cobrador — não existe, em lugar nenhum do sistema, um cadastro de
--   cobrador ligado a horário/carro. Por isso NUNCA há sugestão automática
--   pro campo cobrador_re — o controlador sempre digita os dois REs, mas
--   só o de motorista pode vir pré-preenchido pela escala. Consertar isso
--   de verdade (cobrador na escala) é trabalho próprio, fora deste deploy
--   (ver §2.9-C do prompt).
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
--   de treinamento — o dado é do VEÍCULO, não da pessoa. Por isso só quem
--   tem recolhida_gerencial vê a análise por motorista/histórico.
--
-- 🔧 BLOCO G — O MOTIVO É MAIS AMPLO QUE "DEFEITO" (§5.1 do prompt)
--   Recolhida anormal também acontece por colisão, falta de motorista ou
--   de cobrador — não só defeito mecânico/elétrico/funilaria. Sem a coluna
--   `motivo`, TODA recolhida abriria ficha de manutenção automaticamente,
--   o que enche a fila da oficina de trabalho que não existe (uma falta de
--   motorista não é ordem de serviço). A ficha só nasce quando
--   motivo='DEFEITO'; nos demais casos ficha_id fica NULL e
--   ficha_falhou_motivo explica que o motivo não gera ordem de serviço —
--   isso não é falha, é o comportamento correto.
--   ⚠️ COLISÃO é o caso ambíguo (pode gerar avaria pra funilaria), mas quem
--   decide isso é a manutenção ao avaliar, não a portaria ao registrar —
--   nesta fase, COLISAO também NÃO abre ficha automática.
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

    -- 🔑 §5.2b: SEM local_codigo de propósito — recolhida anormal é sempre
    -- de COLETIVO (identificado por prefixo/frota, nunca placa), e coletivo
    -- sempre entra pelo mesmo portão. O portão é consequência de o veículo
    -- ser coletivo, não uma classificação própria — gravar o local não
    -- acrescentaria informação, só um campo que parece corte de análise e
    -- não é (e hoje o frontend mandaria 'LEVES' fixo, informação falsa).
    -- portaria.local continua servindo só o movimento de veículo de
    -- passeio (portaria.movimento), onde local é verdade.
    momento             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data_referencia     DATE NOT NULL DEFAULT (NOW() AT TIME ZONE 'America/Sao_Paulo')::date,

    -- ── o que a PORTARIA informa ──────────────────────────────────────
    prefixo             VARCHAR(10) NOT NULL,   -- snapshot texto; ⛔ sem FK para onibus
    onibus_id           UUID,                   -- resolvido pelo backend, quando existir; ⛔ sem FK (regra de fronteira)
    linha_codigo        VARCHAR(20),            -- snapshot texto; ⛔ sem FK para linha
    -- Bloco G (§5.1): nem toda recolhida é defeito mecânico.
    motivo              VARCHAR(20) NOT NULL DEFAULT 'DEFEITO'
                        CHECK (motivo IN ('DEFEITO','COLISAO','FALTA_MOTORISTA','FALTA_COBRADOR','OUTRO')),
    -- Código do catálogo tipo_defeito, TEXTO — não FK. Só obrigatório
    -- quando motivo='DEFEITO' (ver CHECK ck_recolhida_motivo_defeito abaixo).
    tipo_defeito_codigo VARCHAR(20),
    relato              TEXT,

    -- ── digitado pelo controlador (§2.9-0) — nunca sai de endpoint sem
    --    recolhida_gerencial; a escala só SUGERE o RE do motorista ──────
    motorista_re        VARCHAR(20),
    motorista_nome      VARCHAR(120),
    cobrador_re         VARCHAR(20),
    cobrador_nome       VARCHAR(120),
    origem_identificacao VARCHAR(16) NOT NULL DEFAULT 'NAO_INFORMADO'
                        CHECK (origem_identificacao IN ('PORTARIA','ESCALA','NAO_INFORMADO')),

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
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_recolhida_motivo_defeito
        CHECK (motivo <> 'DEFEITO' OR tipo_defeito_codigo IS NOT NULL)
);

COMMENT ON TABLE portaria.recolhida_anormal IS 'Ônibus que recolhe fora de hora — evento de OPERAÇÃO, não de manutenção (a pergunta urgente é "esse carro volta? em quanto tempo?"). Existe para melhoria de processo e de frota: a associação motorista↔defeito serve pra encontrar padrão de operação e necessidade de treinamento — o dado é do VEÍCULO, não da pessoa. O controlador digita motorista_re/cobrador_re (é quem está com o carro na frente), mas nunca vê histórico, agregado ou ranking — isso exige recolhida_gerencial. §5.2b: SEM local_codigo, de propósito — recolhida anormal é sempre de COLETIVO (prefixo/frota), e coletivo sempre entra pelo mesmo portão; o portão é consequência da natureza do veículo, não uma classificação própria desta tabela.';
COMMENT ON COLUMN portaria.recolhida_anormal.motivo IS 'Bloco G (§5.1): recolhida anormal nem sempre é defeito — pode ser colisão ou falta de motorista/cobrador. Só motivo=DEFEITO abre ficha de manutenção automaticamente (ver ficha_id/ficha_falhou_motivo).';
COMMENT ON COLUMN portaria.recolhida_anormal.tipo_defeito_codigo IS 'Código do catálogo tipo_defeito, TEXTO — não FK. Obrigatório só quando motivo=DEFEITO (CHECK ck_recolhida_motivo_defeito). O catálogo pode mudar e o histórico precisa continuar verdadeiro (mesmo princípio dos snapshots do resto do módulo).';
COMMENT ON COLUMN portaria.recolhida_anormal.ficha_id IS '⚠️ ÚNICA exceção à regra de fronteira deste módulo — FK para public.ficha_manutencao(id), deliberada porque a ligação recolhida↔ficha é o produto da feature. Só nasce quando motivo=DEFEITO. A escrita do lado da ficha é sempre um INSERT de linha nova (nunca UPDATE/DELETE em dado do Pátio).';
COMMENT ON COLUMN portaria.recolhida_anormal.motorista_re IS 'GERENCIAL na leitura (nunca devolvido por endpoint que não exige recolhida_gerencial), mas digitado diretamente pelo CONTROLADOR (§2.9-0) — a escala só entra como sugestão de pré-preenchimento (nunca fonte única, nunca trava o registro).';
COMMENT ON COLUMN portaria.recolhida_anormal.cobrador_re IS 'GERENCIAL na leitura, digitado diretamente pelo CONTROLADOR (§2.9-0). ⚠️ Nunca sugerido automaticamente — a tabela escala não tem campo de cobrador, não há de onde pré-preencher por join (ver cabeçalho desta migration).';
COMMENT ON COLUMN portaria.recolhida_anormal.origem_identificacao IS 'PORTARIA = controlador digitou o RE. ESCALA = veio sugerido da escala (só motorista) e ele confirmou sem alterar. NAO_INFORMADO = deixou em branco — a recolhida foi registrada do mesmo jeito (regra número um).';

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
  ('recolhida_gerencial','Análise de Recolhidas', 'Visão gerencial: histórico, motorista, carro e linha.',  'PORTARIA', 15)
ON CONFLICT (codigo) DO NOTHING;

-- 🔴 CONTROLADOR_ACESSO não tem recolhida_gerencial — é essa linha que
-- garante que a portaria não vê HISTÓRICO/agregado, mesmo digitando o RE.
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
-- funcionario (esperado: só ficha_manutencao e funcionario — ⛔ sem local,
-- ver §5.2b):
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
