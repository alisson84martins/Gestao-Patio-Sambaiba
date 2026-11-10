-- ============================================================================
-- MIGRATION 053 — Monitoramento ao vivo da linha (SIM SPTrans → sistema)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: monitoramento (novo)
-- DATA:   2026-11-10
-- AUTOR:  Claude
-- DEPENDE DE: nada (schema isolado, sem FK para outros módulos)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   O "robô" (extensão do Chrome no PC da coordenação, logada no SIM SPTrans)
--   lê a posição de cada carro da linha a cada minuto e envia para
--   POST /monitoramento/leituras. O fiscal em campo e a coordenação
--   acompanham no celular (ao-vivo.html), sem SIM nem extensão.
--
--   monitoramento.leitura_veiculo — uma linha por posição de GPS de um carro.
--     UNIQUE (prefixo, posicao_em): o robô pode reenviar a mesma leitura;
--     o insert usa ON CONFLICT DO NOTHING.
--     prefixo = 5 dígitos do SIM (22412); o pátio usa 4 (2412) — a conversão
--     é feita na leitura para as telas, não aqui.
--     linha_codigo = código canônico do cadastro único (048): '1726-10'.
--   monitoramento.coleta — o "batimento" do robô: uma linha por envio,
--     inclusive status='sessao' sem veículos (login do SIM caiu). A tela usa
--     para mostrar "Robô sem dados desde HH:MM".
--
--   Retenção (feita pelo backend no próprio POST): leituras > 3 dias e
--   coletas > 7 dias são apagadas.
--
--   ⛔ Sem dado pessoal (só carro, posição e hora). Sem seed.
--
-- IDEMPOTENTE: pode rodar duas vezes.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 053-monitoramento-tempo-real.sql
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS monitoramento;

CREATE TABLE IF NOT EXISTS monitoramento.leitura_veiculo (
    id              BIGSERIAL PRIMARY KEY,
    recebido_em     TIMESTAMPTZ DEFAULT now(),
    posicao_em      TIMESTAMPTZ NOT NULL,
    prefixo         VARCHAR(10) NOT NULL,
    linha_codigo    VARCHAR(20) NOT NULL,
    sim_codigo      INTEGER NOT NULL,
    sentido         SMALLINT NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    velocidade      SMALLINT,
    local_sim       VARCHAR(120),
    CONSTRAINT uq_leitura_veiculo_prefixo_posicao UNIQUE (prefixo, posicao_em)
);

COMMENT ON TABLE monitoramento.leitura_veiculo IS
    'Posições de GPS dos carros das linhas monitoradas, enviadas pelo robô (extensão no SIM SPTrans). Retenção de 3 dias.';
COMMENT ON COLUMN monitoramento.leitura_veiculo.posicao_em IS
    'Hora do GPS informada pelo SIM (campo d, epoch ms), em UTC.';
COMMENT ON COLUMN monitoramento.leitura_veiculo.prefixo IS
    'Prefixo de 5 dígitos do SIM (22412). O pátio usa 4 (2412): tira o 1º dígito.';
COMMENT ON COLUMN monitoramento.leitura_veiculo.linha_codigo IS
    'Código canônico do cadastro único de linhas (migration 048), ex.: 1726-10.';
COMMENT ON COLUMN monitoramento.leitura_veiculo.sim_codigo IS
    'Código linha-sentido do SIM (campo li): 1726-10 = 1403 (sentido 1) / 34171 (sentido 2).';

CREATE INDEX IF NOT EXISTS idx_leitura_veiculo_linha_posicao
    ON monitoramento.leitura_veiculo (linha_codigo, posicao_em DESC);

CREATE TABLE IF NOT EXISTS monitoramento.coleta (
    id              BIGSERIAL PRIMARY KEY,
    recebido_em     TIMESTAMPTZ DEFAULT now(),
    origem          VARCHAR(40),
    status          VARCHAR(20),
    qtd_veiculos    INTEGER,
    mensagem        VARCHAR(200)
);

COMMENT ON TABLE monitoramento.coleta IS
    'Batimento do robô: uma linha por envio (ok | sessao | erro). Retenção de 7 dias.';

CREATE INDEX IF NOT EXISTS idx_coleta_recebido
    ON monitoramento.coleta (recebido_em DESC);

-- 🔴 Schema e tabelas novos nascem sem permissão para o papel da aplicação —
-- sem isto o recebimento quebra só em produção (armadilha_migration_sem_grant).
GRANT USAGE ON SCHEMA monitoramento TO sambaiba;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA monitoramento TO sambaiba;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA monitoramento TO sambaiba;

COMMIT;
