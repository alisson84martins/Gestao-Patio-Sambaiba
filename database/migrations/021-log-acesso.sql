-- ============================================================================
-- MIGRATION 021 — Trilha de auditoria mínima (log_acesso)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- DATA:   2026-08-11
-- AUTOR:  Claude Code (fechamento da auditoria, Bloco B item 7)
-- DEPENDE DE: 011-rbac-cadastro-central.sql (tabela funcionario)
-- ORIGEM: _handoff-claude/RELATORIO-SEGURANCA-2026-08-10.md (§3.4) +
--         _handoff-claude/PROMPT-2026-08-11-fechamento.md, Bloco B item 7
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Hoje não existe registro de quem leu o quê. Num incidente envolvendo
--   dado de vítima/testemunha de ocorrência (nome, CPF, RG, endereço,
--   telefone — ver §6 do SISTEMA-EM-PRODUCAO.md, "dado pessoal de
--   terceiro"), não há como responder "quem acessou o dado da vítima X" —
--   nem internamente, nem pra ANPD.
--
--   Registra o mínimo: login (sucesso e falha, NUNCA a senha), negativa de
--   403 (via exige() — RBAC por recurso) e leitura de ocorrência (GET
--   /ocorrencias/{id} — quem, quando, qual). Nenhum dado pessoal de
--   terceiro entra aqui — só o `id` da ocorrência, nunca nome/CPF da
--   vítima.
--
-- ESCOPO DELIBERADAMENTE MÍNIMO — o que este item NÃO cobre:
--   - 403 de autoria em ocorrência (_exige_pode_ver/_exige_autoria em
--     ocorrencias.py) — são HTTPException levantadas fora de exige(),
--     não capturadas por este hook. É a negativa mais relevante pro
--     cenário de vítima ("coordenador tentou ver ocorrência de outro"),
--     mas o prompt de execução listou só _checar_rate_limit,
--     get_current_funcionario e exige() como pontos de instrumentação —
--     registrado como próxima extensão natural, não implementado agora.
--   - 403 dos 12 routers legados (require_admin, _require_operador_ou_admin,
--     _require_mecanico_ou_superior em deps.py) — não passam por exige().
--   - Rate limit (429) de auth.py — já tem seus próprios contadores em
--     memória (_LOGIN_TENTATIVAS, _TENTATIVAS_POR_CONTA); não duplicado
--     aqui.
--
-- VOLUME ESPERADO (estimativa, não medição — ver EXECUCAO-2026-08-11-
-- fechamento.md): ~200 operadores, uso concentrado em horário comercial.
-- Login 1x/turno + algumas leituras de ocorrência por coordenador + 403
-- ocasional → estimativa grosseira de dezenas a poucas centenas de linhas
-- por dia útil, não milhares. Cresce sem parar (append-only) — SEM
-- política de expurgo implementada. Decisão de negócio pro Alisson: por
-- quanto tempo o log precisa ficar disponível? (mesma pergunta ainda em
-- aberto pra retenção de dado de ocorrência, §6 do SISTEMA-EM-PRODUCAO.md)
--
-- NATUREZA: idempotente (CREATE TABLE/INDEX IF NOT EXISTS).
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table funcionario", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 021-log-acesso.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS log_acesso (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evento          VARCHAR(30) NOT NULL,
    -- NULL quando o login falhou antes de identificar um Funcionario de
    -- verdade (RE inexistente) — re_tentativa cobre esse caso.
    funcionario_id  UUID REFERENCES funcionario(id) ON DELETE SET NULL,
    -- RE usado na tentativa de login (sucesso ou falha) — identificador
    -- funcional, não é "dado pessoal de terceiro" no sentido do §6 (é o
    -- próprio funcionário se autenticando, não vítima/testemunha).
    re_tentativa    VARCHAR(20),
    -- Recurso do RBAC quando o evento é NEGADO_403 (ex.: 'usuarios').
    recurso         VARCHAR(50),
    -- id da ocorrência lida ou negada — SEM FK pra coordenadoria.ocorrencia:
    -- mesma regra de fronteira do resto do sistema (§1 do
    -- SISTEMA-EM-PRODUCAO.md — só funcionario cruza os dois schemas).
    ocorrencia_id   UUID,
    ip              VARCHAR(45),
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_log_acesso_evento CHECK (
        evento IN ('LOGIN_SUCESSO', 'LOGIN_FALHA', 'NEGADO_403', 'LEITURA_OCORRENCIA')
    )
);

CREATE INDEX IF NOT EXISTS idx_log_acesso_funcionario ON log_acesso(funcionario_id);
CREATE INDEX IF NOT EXISTS idx_log_acesso_ocorrencia ON log_acesso(ocorrencia_id) WHERE ocorrencia_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_log_acesso_criado_em ON log_acesso(criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_log_acesso_evento ON log_acesso(evento);

COMMENT ON TABLE log_acesso IS
    'Trilha de auditoria mínima (login, negativa de 403 via exige(), leitura de ocorrência). Append-only, sem expurgo ainda. Nunca guarda dado pessoal de terceiro — só id.';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
-- \d log_acesso
-- SELECT evento, count(*) FROM log_acesso GROUP BY evento ORDER BY 2 DESC;
-- SELECT * FROM log_acesso ORDER BY criado_em DESC LIMIT 20;
--
-- Responde a pergunta motivadora ("quem leu a ocorrência X"):
-- SELECT la.criado_em, f.re, f.nome
--   FROM log_acesso la LEFT JOIN funcionario f ON f.id = la.funcionario_id
--  WHERE la.evento = 'LEITURA_OCORRENCIA' AND la.ocorrencia_id = '<uuid-da-ocorrencia>'
--  ORDER BY la.criado_em;
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DROP TABLE IF EXISTS log_acesso;
-- ============================================================================
