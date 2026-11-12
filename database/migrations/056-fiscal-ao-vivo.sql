-- ============================================================================
-- MIGRATION 056 — Fiscal ao vivo + Central "minhas linhas" (v4.0, fase 3)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMAS: monitoramento (053/054), fiscalizacao (029)
-- DATA:   2026-11-12
-- AUTOR:  Claude
-- DEPENDE DE: 053, 054, 029/047 (fiscalizacao.turno)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Bloco A — o fiscal é identificado pelo NIMER: o robô lê a Escala de
--   Fiscais do NIMER (/Departure, Função = Fiscal) e manda pegada, largada,
--   local e a batida de início com foto. Com a foto, o sistema abre o turno
--   sozinho com as linhas do ponto (ponto_nimer).
--     nimer_fiscal_escala — um cartão da escala de fiscais do NIMER por
--                           (data de serviço, RE, tabela). Upsert a cada envio.
--     ponto_nimer         — Local do NIMER (normalizado, começo do texto) →
--                           linha + terminal. Uma linha por par local × linha.
--     turno.origem        — MANUAL | NIMER (nullable: turnos antigos ficam NULL).
--
--   Bloco B — o robô puxa o Rastro do SIM (pontos a cada 10–15 s) só dos
--   carros em dúvida, um por vez, para não perder a saída de quem entra e
--   sai do terminal entre duas leituras de 60 s.
--     leitura_veiculo.fonte — leitura | rastro (nullable).
--     rastro_coleta         — cada envio do Rastro (carro + janela).
--
--   Dado pessoal: nimer_fiscal_escala guarda o RE do fiscal como escalado no
--   NIMER (mesmo nível de turno.fiscal_re). Sem nome. Semente sem dado pessoal.
--
-- IDEMPOTENTE: pode rodar duas vezes (IF NOT EXISTS / ON CONFLICT DO NOTHING).
-- Coluna nova em tabela existente: sempre NULLABLE e sem default destrutivo.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR (copie para /tmp antes: o usuário postgres não lê /root):
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f /tmp/056-fiscal-ao-vivo.sql
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS monitoramento;

-- ─── Bloco A · Escala de fiscais do NIMER ───────────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.nimer_fiscal_escala (
    id              BIGSERIAL PRIMARY KEY,
    data_servico    DATE NOT NULL,
    re              VARCHAR(20) NOT NULL,
    tabela          VARCHAR(20) NOT NULL DEFAULT '',
    pegada_em       TIMESTAMPTZ,
    largada_em      TIMESTAMPTZ,
    local_texto     VARCHAR(200),
    inicio_em       TIMESTAMPTZ,
    fim_em          TIMESTAMPTZ,
    coletado_em     TIMESTAMPTZ,
    atualizado_em   TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_nimer_fiscal_escala UNIQUE (data_servico, re, tabela)
);

COMMENT ON TABLE monitoramento.nimer_fiscal_escala IS
    'Cartão da Escala de Fiscais do NIMER (/Departure, Função = Fiscal), lido pelo robô. Upsert a cada envio. Só leitura do NIMER.';
COMMENT ON COLUMN monitoramento.nimer_fiscal_escala.re IS
    'RE do fiscal COMO ESCALADO NO NIMER — comparado com funcionario.re como TEXTO (pode ter letra).';
COMMENT ON COLUMN monitoramento.nimer_fiscal_escala.pegada_em IS
    'Hora de parede de São Paulo do NIMER convertida (FUSO_OPERACAO) para instante.';
COMMENT ON COLUMN monitoramento.nimer_fiscal_escala.inicio_em IS
    'Batida de início COM FOTO no NIMER. Só a hora vem do NIMER: a data é a da pegada; hora < pegada − 6 h = dia seguinte. NULL = ainda não bateu.';
COMMENT ON COLUMN monitoramento.nimer_fiscal_escala.fim_em IS
    'Batida de fim no NIMER. Preenchido = o turno automático não abre de novo no mesmo dia.';

CREATE INDEX IF NOT EXISTS idx_nimer_fiscal_escala_re_data
    ON monitoramento.nimer_fiscal_escala (re, data_servico);

-- ─── Bloco A · Local do NIMER → linha + terminal ────────────────────────────
CREATE TABLE IF NOT EXISTS monitoramento.ponto_nimer (
    id              BIGSERIAL PRIMARY KEY,
    local_texto     VARCHAR(200) NOT NULL,
    linha_codigo    VARCHAR(20) NOT NULL,
    terminal        VARCHAR(2) NOT NULL CHECK (terminal IN ('TP', 'TS')),
    ativo           BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em       TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_ponto_nimer UNIQUE (local_texto, linha_codigo)
);

COMMENT ON TABLE monitoramento.ponto_nimer IS
    'Local da escala de fiscais do NIMER → linha e terminal do ponto. local_texto é o COMEÇO do Local, normalizado (minúsculo, sem acento, espaços únicos); vale o mais longo que casar.';

-- Semente (12/11/2026). ⚠️ Terminais de 178T-10, 1764-10, 2025-10 e 1783-10
-- no Metrô Santana: TS provisório — a confirmar com o Alisson.
INSERT INTO monitoramento.ponto_nimer (local_texto, linha_codigo, terminal) VALUES
    ('metro santana', '1726-10', 'TS'),
    ('metro santana', '178T-10', 'TS'),
    ('metro santana', '1764-10', 'TS'),
    ('metro santana', '2025-10', 'TS'),
    ('metro santana', '1783-10', 'TS'),
    ('cem. parque dos pinheiros', '1726-10', 'TP')
ON CONFLICT (local_texto, linha_codigo) DO NOTHING;

-- ─── Bloco A · Origem do turno ──────────────────────────────────────────────
ALTER TABLE fiscalizacao.turno ADD COLUMN IF NOT EXISTS origem VARCHAR(10);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_turno_origem') THEN
        ALTER TABLE fiscalizacao.turno
            ADD CONSTRAINT ck_turno_origem CHECK (origem IS NULL OR origem IN ('MANUAL', 'NIMER'));
    END IF;
END $$;

COMMENT ON COLUMN fiscalizacao.turno.origem IS
    'MANUAL = aberto pelo fiscal em fiscal.html; NIMER = aberto sozinho pela batida de início com foto no NIMER (056). NULL = turno anterior à 056.';

-- ─── Bloco B · Rastro do SIM (carros em dúvida) ─────────────────────────────
ALTER TABLE monitoramento.leitura_veiculo ADD COLUMN IF NOT EXISTS fonte VARCHAR(10);

COMMENT ON COLUMN monitoramento.leitura_veiculo.fonte IS
    'leitura = consulta do SIM a cada minuto; rastro = Rastro do SIM (pontos a cada 10–15 s) puxado só para carro em dúvida. NULL = anterior à 056. A UNIQUE (prefixo, posicao_em) descarta o ponto repetido.';

CREATE TABLE IF NOT EXISTS monitoramento.rastro_coleta (
    id              BIGSERIAL PRIMARY KEY,
    recebido_em     TIMESTAMPTZ DEFAULT now(),
    prefixo         VARCHAR(10) NOT NULL,
    desde           TIMESTAMPTZ,
    ate             TIMESTAMPTZ,
    status          VARCHAR(20),
    pontos          INTEGER,
    gravados        INTEGER
);

COMMENT ON TABLE monitoramento.rastro_coleta IS
    'Um envio do Rastro do SIM pelo robô (um carro, uma janela). Serve para não pedir de novo a mesma janela e para a saúde da coleta. Retenção de 7 dias.';

CREATE INDEX IF NOT EXISTS idx_rastro_coleta_prefixo
    ON monitoramento.rastro_coleta (prefixo, ate);

-- 🔴 Tabelas novas nascem sem permissão para o papel da aplicação
-- (armadilha_migration_sem_grant).
GRANT USAGE ON SCHEMA monitoramento TO sambaiba;
GRANT SELECT, INSERT, UPDATE, DELETE ON monitoramento.nimer_fiscal_escala TO sambaiba;
GRANT SELECT, INSERT, UPDATE, DELETE ON monitoramento.ponto_nimer TO sambaiba;
GRANT USAGE, SELECT ON SEQUENCE monitoramento.nimer_fiscal_escala_id_seq TO sambaiba;
GRANT USAGE, SELECT ON SEQUENCE monitoramento.ponto_nimer_id_seq TO sambaiba;
GRANT SELECT, INSERT, UPDATE, DELETE ON monitoramento.rastro_coleta TO sambaiba;
GRANT USAGE, SELECT ON SEQUENCE monitoramento.rastro_coleta_id_seq TO sambaiba;

COMMIT;
