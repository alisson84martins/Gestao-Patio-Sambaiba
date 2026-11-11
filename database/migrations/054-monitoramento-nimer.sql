-- ============================================================================
-- MIGRATION 054 — Fiscalização em tempo real, fase 2 (NIMER + SIM)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: monitoramento (criado na 053)
-- DATA:   2026-11-10
-- AUTOR:  Claude
-- DEPENDE DE: 053 (schema monitoramento), funcionario (public)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   O robô (extensão do Chrome na coordenação) passa a ler também o NIMER
--   (Relatório de Linha, a cada 3 min) e o SIM de TODAS as linhas
--   monitoradas. O sistema cruza as duas fontes partida a partida, calcula
--   ICV/IPP ao vivo, alertas, previsão de chegada, e o fiscal corrige o que
--   veio errado do NIMER (sem escrever no NIMER — só leitura).
--
--   linha_monitorada   — quais linhas o robô acompanha. Incluir linha =
--                        INSERT aqui (+ traçado em app/data/tracados/).
--   sim_codigo_linha   — o código linha-sentido do SIM (`li`) de cada linha,
--                        aprendido sozinho pelo recebimento do SIM.
--   nimer_viagem       — uma linha por viagem do NIMER (upsert a cada coleta).
--   nimer_coleta       — batimento do robô do NIMER, por linha.
--   viagem_correcao    — correção do fiscal (status/motivo/carro/real_inicio).
--                        ⛔ Nunca apagar: a mais recente de cada campo vale.
--   alerta_tratativa   — "resolvido" da fila de ação da Central (o alerta em
--                        si é CALCULADO, D24 — ⛔ nada vai para a tabela
--                        `alerta` do Pátio).
--   velocidade_trecho  — histórico de velocidade por trecho de ~500 m e faixa
--                        de 30 min (previsão de chegada), alimentado pelas
--                        leituras antes da retenção de 3 dias apagá-las.
--   velocidade_agregacao — marca de dia já agregado em velocidade_trecho.
--
--   Dado pessoal: nimer_viagem guarda o RE de motorista/cobrador COMO
--   ESCALADO NO NIMER (mesmo nível do fechamento do fiscal, que já leva RE).
--   Sem nome. Seed sem dado pessoal (só a linha piloto e os códigos do SIM).
--
-- IDEMPOTENTE: pode rodar duas vezes.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR (copie para /tmp antes: o usuário postgres não lê /root):
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f /tmp/054-monitoramento-nimer.sql
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS monitoramento;

-- ─── Linhas monitoradas ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.linha_monitorada (
    linha_codigo    VARCHAR(20) PRIMARY KEY,
    nimer_line_id   INTEGER,
    ativo           BOOLEAN NOT NULL DEFAULT TRUE,
    atualizado_em   TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE monitoramento.linha_monitorada IS
    'Linhas acompanhadas ao vivo (SIM + NIMER). Código canônico do cadastro único (048). Incluir linha = inserir aqui + gerar o traçado.';
COMMENT ON COLUMN monitoramento.linha_monitorada.nimer_line_id IS
    'LineId do Relatório de Linha do NIMER (option do select#LineId). NULL = só SIM.';

INSERT INTO monitoramento.linha_monitorada (linha_codigo, nimer_line_id, ativo)
VALUES ('1726-10', 33534, TRUE)
ON CONFLICT (linha_codigo) DO NOTHING;

-- ─── Códigos linha-sentido do SIM ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.sim_codigo_linha (
    sim_codigo      INTEGER PRIMARY KEY,
    linha_codigo    VARCHAR(20) NOT NULL,
    sentido         SMALLINT,
    visto_em        TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE monitoramento.sim_codigo_linha IS
    'Código linha-sentido do SIM (campo li) -> linha e sentido. Aprendido pelo recebimento do SIM (agrupa l + s).';

-- Medido em 10/11/2026: 1726-10 = 1403 (sentido 1, TP→TS) / 34171 (sentido 2).
INSERT INTO monitoramento.sim_codigo_linha (sim_codigo, linha_codigo, sentido)
VALUES (1403, '1726-10', 1), (34171, '1726-10', 2)
ON CONFLICT (sim_codigo) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_sim_codigo_linha_linha
    ON monitoramento.sim_codigo_linha (linha_codigo);

-- ─── Viagens do NIMER ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.nimer_viagem (
    id                  BIGSERIAL PRIMARY KEY,
    data_servico        DATE NOT NULL,
    linha_codigo        VARCHAR(20) NOT NULL,
    tabela              VARCHAR(12),
    nimer_table_id      VARCHAR(40),
    nimer_departure_id  VARCHAR(40) NOT NULL,
    idx                 SMALLINT,
    sentido             SMALLINT,
    status              VARCHAR(10),
    prog_inicio         TIMESTAMPTZ,
    prog_fim            TIMESTAMPTZ,
    real_inicio         TIMESTAMPTZ,
    real_fim            TIMESTAMPTZ,
    fonte_inicio        VARCHAR(10),
    fonte_fim           VARCHAR(10),
    atraso_inicio_min   SMALLINT,
    motivo_perda_texto  VARCHAR(120),
    carro               VARCHAR(10),
    motorista_re        VARCHAR(20),
    cobrador_re         VARCHAR(20),
    sem_motorista       BOOLEAN NOT NULL DEFAULT FALSE,
    removida_em         TIMESTAMPTZ,
    atualizado_em       TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_nimer_viagem_data_departure UNIQUE (data_servico, nimer_departure_id)
);

COMMENT ON TABLE monitoramento.nimer_viagem IS
    'Uma viagem do Relatório de Linha do NIMER, por data de serviço. Upsert a cada coleta do robô (3 min). Só leitura do NIMER.';
COMMENT ON COLUMN monitoramento.nimer_viagem.nimer_departure_id IS
    'departureId do NIMER. Sem departureId (viagem futura sem saída criada) = "t<tableId>-<idx>".';
COMMENT ON COLUMN monitoramento.nimer_viagem.sentido IS
    '1 = TP→TS (tpToTs), 2 = TS→TP — mesmo sentido do SIM (1726-10: 1403 = sentido 1 = destino TS).';
COMMENT ON COLUMN monitoramento.nimer_viagem.status IS
    'Como veio do NIMER: done | lost | missing | future. O status efetivo (com correção do fiscal) é calculado.';
COMMENT ON COLUMN monitoramento.nimer_viagem.motorista_re IS
    'RE do motorista COMO ESCALADO NO NIMER — não é prova de quem dirigiu ou faltou.';
COMMENT ON COLUMN monitoramento.nimer_viagem.sem_motorista IS
    'TRUE quando o NIMER mostra "Sem Motorista" na tabela.';
COMMENT ON COLUMN monitoramento.nimer_viagem.removida_em IS
    'Preenchido quando a viagem deixou de vir na coleta do NIMER (tabela retirada). Fica fora das contas.';

CREATE INDEX IF NOT EXISTS idx_nimer_viagem_linha_data
    ON monitoramento.nimer_viagem (linha_codigo, data_servico);

-- ─── Batimento do robô do NIMER ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.nimer_coleta (
    id                  BIGSERIAL PRIMARY KEY,
    recebido_em         TIMESTAMPTZ DEFAULT now(),
    coletado_em         TIMESTAMPTZ,
    linha_codigo        VARCHAR(20),
    data_servico        DATE,
    status              VARCHAR(20),
    planejadas          INTEGER,
    realizadas          INTEGER,
    perdidas            INTEGER,
    faltantes           INTEGER,
    pontualidade_pct    NUMERIC(6, 2),
    qtd_viagens         INTEGER,
    mensagem            VARCHAR(200)
);

COMMENT ON TABLE monitoramento.nimer_coleta IS
    'Batimento do robô do NIMER: uma linha por envio por linha (ok | sessao | erro), com os totais do próprio NIMER. Retenção de 7 dias.';

CREATE INDEX IF NOT EXISTS idx_nimer_coleta_linha_recebido
    ON monitoramento.nimer_coleta (linha_codigo, recebido_em DESC);

-- ─── Correção do fiscal (bloco G) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.viagem_correcao (
    id                      BIGSERIAL PRIMARY KEY,
    nimer_viagem_id         BIGINT NOT NULL REFERENCES monitoramento.nimer_viagem (id),
    campo                   VARCHAR(20) NOT NULL,
    valor_nimer             VARCHAR(120),
    valor_corrigido         VARCHAR(120),
    justificativa           TEXT NOT NULL,
    autor_funcionario_id    UUID NOT NULL REFERENCES funcionario (id),
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_viagem_correcao_campo CHECK (campo IN ('status', 'motivo', 'carro', 'real_inicio')),
    CONSTRAINT ck_viagem_correcao_justificativa CHECK (length(btrim(justificativa)) > 0)
);

COMMENT ON TABLE monitoramento.viagem_correcao IS
    'Correção do fiscal sobre uma viagem do NIMER. Nunca apagar — vale a mais recente de cada campo. O NIMER não é alterado.';

CREATE INDEX IF NOT EXISTS idx_viagem_correcao_viagem
    ON monitoramento.viagem_correcao (nimer_viagem_id, criado_em DESC);

-- ─── "Resolvido" da fila de ação (bloco I) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.alerta_tratativa (
    id                      BIGSERIAL PRIMARY KEY,
    data_servico            DATE NOT NULL,
    linha_codigo            VARCHAR(20) NOT NULL,
    chave                   VARCHAR(160) NOT NULL,
    acao_coordenacao_id     UUID,
    autor_funcionario_id    UUID NOT NULL REFERENCES funcionario (id),
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_alerta_tratativa UNIQUE (data_servico, chave)
);

COMMENT ON TABLE monitoramento.alerta_tratativa IS
    'Alerta calculado marcado como resolvido na Central da Fiscalização. chave = tipo + linha + tabela/carro + início. acao_coordenacao_id aponta fiscalizacao.acao_coordenacao (sem FK: fronteira de módulo).';

-- ─── Histórico de velocidade (bloco F) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.velocidade_trecho (
    linha_codigo    VARCHAR(20) NOT NULL,
    sentido         SMALLINT NOT NULL,
    trecho          SMALLINT NOT NULL,
    faixa           SMALLINT NOT NULL,
    tipo_dia        VARCHAR(8) NOT NULL,
    mediana_kmh     REAL NOT NULL,
    n               INTEGER NOT NULL,
    atualizado_em   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (linha_codigo, sentido, trecho, faixa, tipo_dia)
);

COMMENT ON TABLE monitoramento.velocidade_trecho IS
    'Velocidade típica por trecho de 500 m do traçado (trecho = km acumulado / 0,5) e faixa de 30 min (0–47, hora de SP), por tipo de dia. mediana_kmh = média das medianas diárias ponderada por n.';

CREATE TABLE IF NOT EXISTS monitoramento.velocidade_agregacao (
    data_servico    DATE NOT NULL,
    linha_codigo    VARCHAR(20) NOT NULL,
    leituras        INTEGER,
    agregado_em     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (data_servico, linha_codigo)
);

COMMENT ON TABLE monitoramento.velocidade_agregacao IS
    'Dia já somado em velocidade_trecho (evita somar o mesmo dia duas vezes).';

-- 🔴 Tabelas novas nascem sem permissão para o papel da aplicação
-- (armadilha_migration_sem_grant).
GRANT USAGE ON SCHEMA monitoramento TO sambaiba;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA monitoramento TO sambaiba;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA monitoramento TO sambaiba;

COMMIT;
