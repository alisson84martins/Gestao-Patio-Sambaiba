-- ============================================================================
-- MIGRATION 028 — Pré-cadastro de pessoas (Bloco H)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: public (identidade é a camada compartilhada da Suite)
-- DATA:   2026-08-22
-- AUTOR:  Claude Code
-- DEPENDE DE: 011-rbac-cadastro-central.sql (public.funcionario/recurso/
--             funcao/funcao_permissao/modulo — usa o módulo ADMINISTRACAO
--             já existente)
-- ORIGEM: _handoff-claude/PROMPT-portaria-blocos-E-F.md §5.2 (Bloco H)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   "a cada cadastro vai entrando em uma tabela que existe no banco (...)
--   mas em uma ocorrência pegamos dados valiosos que podem virar o
--   cadastro do motorista. para acesso ao sistema apenas com a sua devida
--   permissão, o mesmo vale para o cobrador." — Alisson, 21/08/2026.
--
--   Todo RE capturado em qualquer módulo alimenta um cadastro preliminar.
--   A portaria contribui pouco (só o RE, na recolhida anormal); a
--   pré-ocorrência contribui muito (nome, CPF, RG, CNH, telefone). Com o
--   tempo a ficha se completa sozinha, e o RH promove pra cadastro real —
--   isso NUNCA cria acesso ao sistema sozinho (ver services/pre_cadastro.py).
--
-- ⚠️ LGPD — a parte mais sensível do sistema até aqui. Uma tabela que
--   ACUMULA automaticamente nome/CPF/RG/CNH/telefone, alimentada por
--   eventos operacionais, é um repositório de dado pessoal por acúmulo —
--   ninguém decidiu criá-lo conscientemente, nasce como efeito colateral.
--   Finalidade (também no código, ver routers/pre_cadastro.py): REDUZIR
--   RETRABALHO DE CADASTRO A PARTIR DE DADOS JÁ LEGITIMAMENTE COLETADOS NA
--   OPERAÇÃO. Retenção: ver retencao_expira_em abaixo (12 meses sugeridos,
--   sem job de expurgo ainda — decisão explícita de não construir isso
--   agora). Acesso: só os dois recursos RBAC abaixo. ⛔ Nunca export CSV
--   nesta fase (aplicado no router, não há endpoint de exportação).
--
-- 🔴 REGRA MAIS IMPORTANTE: RE já cadastrado (em funcionario OU motorista)
--   NUNCA vira pré-cadastro — ver services/pre_cadastro.py e
--   services/identidade.py (§5.3, que já existe — resolve nas DUAS
--   tabelas de pessoa do sistema, não só funcionario).
--
-- 🔧 DIVERGÊNCIA REGISTRADA (não decidida em silêncio): o esboço de tabela
--   do prompt não tinha colunas de auditoria pro DESCARTE
--   (descartado_por/descartado_em/descarte_motivo) nem a coluna de
--   retenção — mas a seção "LGPD" do mesmo prompt exige explicitamente
--   "promoção E descarte gravam quem e quando" e "deixe a coluna [de
--   retenção] e o comentário prontos". Adicionei as três colunas de
--   descarte (espelhando promovido_por/promovido_em, que O prompt já
--   tinha) e retencao_expira_em pra cumprir o que o próprio texto pede.
--   Nenhuma outra coluna foi acrescentada além dessas.
--
-- REGRA DE FRONTEIRA
--   ⛔ NÃO referencia pre_ocorrencia nem portaria.recolhida_anormal — a
--   origem é só um rótulo de texto (ultima_origem). Um pré-cadastro
--   sobrevive ao apagamento do evento que o originou.
--
-- NATUREZA: 100% ADITIVA E IDEMPOTENTE.
--   CREATE TABLE/INDEX IF NOT EXISTS. INSERT ... ON CONFLICT DO NOTHING.
--   Nenhum ALTER TABLE, DROP ou RENAME em tabela existente. Nenhuma view
--   existente é tocada.
--
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 028-pessoa-pre-cadastro.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.pessoa_pre_cadastro (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    re              VARCHAR(20) NOT NULL,
    nome            VARCHAR(120),
    cpf             VARCHAR(14),
    rg              VARCHAR(20),
    cnh             VARCHAR(20),
    telefone        VARCHAR(20),
    papel_sugerido  VARCHAR(12) NOT NULL DEFAULT 'INDEFINIDO'
                    CHECK (papel_sugerido IN ('MOTORISTA','COBRADOR','INDEFINIDO')),
    vezes_visto     INTEGER NOT NULL DEFAULT 1,
    primeira_vez_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ultima_vez_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ultima_origem   VARCHAR(30),   -- 'PORTARIA_RECOLHIDA' | 'PRE_OCORRENCIA' — texto livre, não FK
    status          VARCHAR(12) NOT NULL DEFAULT 'PENDENTE'
                    CHECK (status IN ('PENDENTE','PROMOVIDO','DESCARTADO')),

    funcionario_id  UUID REFERENCES public.funcionario(id),
    promovido_por   UUID REFERENCES public.funcionario(id),
    promovido_em    TIMESTAMPTZ,

    -- Colunas de auditoria do descarte — ver "DIVERGÊNCIA REGISTRADA" no
    -- cabeçalho: o esboço do prompt não as listava, mas a seção LGPD do
    -- mesmo prompt exige "promoção e descarte gravam quem e quando".
    descartado_por  UUID REFERENCES public.funcionario(id),
    descartado_em   TIMESTAMPTZ,
    descarte_motivo TEXT,

    -- LGPD §2 do prompt: "deixe a coluna e o comentário prontos" — prazo
    -- de retenção sugerido (12 meses da criação), sem job de expurgo
    -- ainda (decisão explícita — ver COMMENT abaixo).
    retencao_expira_em TIMESTAMPTZ,

    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pre_cadastro_re
    ON public.pessoa_pre_cadastro (re) WHERE status = 'PENDENTE';
CREATE INDEX IF NOT EXISTS idx_pre_cadastro_status ON public.pessoa_pre_cadastro (status);

COMMENT ON TABLE public.pessoa_pre_cadastro IS 'Pré-cadastro de pessoas alimentado automaticamente pela operação (portaria, pré-ocorrência). FINALIDADE: reduzir retrabalho de cadastro a partir de dados já legitimamente coletados na operação. NÃO cria acesso ao sistema — isso nasce só no fluxo de cadastro formal (POST /pre-cadastros/{id}/promover, recurso `usuarios`). RE já cadastrado em funcionario OU motorista nunca entra aqui (ver app/services/pre_cadastro.py e identidade.py).';
COMMENT ON COLUMN public.pessoa_pre_cadastro.retencao_expira_em IS 'LGPD: prazo sugerido de 12 meses para um pré-cadastro PENDENTE sem promoção. ⚠️ Nenhum job de expurgo automático existe ainda — decisão explícita de não construir isso nesta fase; a coluna existe pronta para quando o expurgo for implementado.';
COMMENT ON COLUMN public.pessoa_pre_cadastro.papel_sugerido IS 'Sugestão de papel (MOTORISTA/COBRADOR) de quem alimentou o registro — não é um cadastro formal de função, só ajuda a triagem na promoção.';
COMMENT ON COLUMN public.pessoa_pre_cadastro.ultima_origem IS 'Rótulo de texto livre (ex.: PORTARIA_RECOLHIDA, PRE_OCORRENCIA) — ⛔ nunca FK: um pré-cadastro sobrevive ao apagamento do evento que o originou.';

-- ============================================================================
-- RBAC — um recurso novo, deliberadamente estreito (§5.2 do prompt)
-- ============================================================================
INSERT INTO public.recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
  ('pre_cadastro','Pré-cadastro de Pessoas',
   'Fila de pessoas vistas na operação, aguardando cadastro formal.','ADMINISTRACAO', 21)
ON CONFLICT (codigo) DO NOTHING;

-- 🔴 CONTROLADOR_ACESSO não aparece aqui — a portaria ESCREVE neste
-- recurso através do serviço (nunca via API direta), mas nunca LÊ. Essa
-- assimetria é o ponto: quem alimenta não precisa ver o acumulado, igual
-- à recolhida_gerencial.
INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, v.recurso, v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('ADMIN',                'pre_cadastro', TRUE, TRUE),
        ('GERENTE_GERAL',        'pre_cadastro', TRUE, TRUE),
        ('GERENTE_OPERACIONAL',  'pre_cadastro', TRUE, FALSE)
       ) AS v(funcao_codigo, recurso, pode_ler, pode_escrever) ON v.funcao_codigo = fn.codigo
 ON CONFLICT ON CONSTRAINT uq_funcao_permissao DO NOTHING;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT count(*) FROM public.pessoa_pre_cadastro;  -- esperado: 0 (tabela nova)
--
-- Prova de que CONTROLADOR_ACESSO não tem pre_cadastro (esperado: 0 linhas):
--   SELECT fp.* FROM public.funcao_permissao fp
--     JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fn.codigo = 'CONTROLADOR_ACESSO' AND fp.recurso = 'pre_cadastro';
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DELETE FROM public.funcao_permissao WHERE recurso = 'pre_cadastro';
-- DELETE FROM public.recurso WHERE codigo = 'pre_cadastro';
-- DROP TABLE IF EXISTS public.pessoa_pre_cadastro;
-- (Seguro: nenhuma outra tabela referencia public.pessoa_pre_cadastro.)
-- ============================================================================
