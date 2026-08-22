-- ============================================================================
-- MIGRATION 029 — Módulo Fiscalização: turnos, partidas, eventos e fechamento
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: fiscalizacao  (recriado — ver ⚠️ NÚMERO abaixo)
-- DATA:   2026-08-22
-- AUTOR:  Claude Code
-- DEPENDE DE: 011-rbac-cadastro-central.sql (funcionario, recurso, funcao,
--             funcao_permissao — o recurso 'fiscalizacao' já existe nesse
--             catálogo desde a 011), 026-recolhida-anormal.sql (portaria,
--             leitura cruzada em vw_partida_recolhida)
-- ORIGEM: _handoff-claude/PROMPT-fiscalizacao-blocos-A-B-C.md, Bloco A, sobre
--         _handoff-claude/DESENHO-modulo-fiscalizacao.md (revisão 2, D1–D19)
-- ----------------------------------------------------------------------------
-- ⚠️ NÚMERO — 027 não existe na pasta (não foi pulado de propósito, só não
--   está lá) e os arquivos vão até 028. Próximo número livre: 029.
--
-- 🔴 ÚNICA MIGRATION NÃO-ADITIVA DO PROJETO — E POR QUÊ
--   Ao inspecionar o banco de dev antes de escrever esta migration
--   (`\dn` + `\dt fiscalizacao.*`), o schema `fiscalizacao` já existia —
--   populado pelo sistema de julho (`_handoff-claude/.../backend-fiscal`),
--   que D1 do desenho já havia descartado como código mas cuja MIGRATION
--   tinha rodado neste banco em algum momento da análise pré-junção:
--     fiscalizacao.partida_programada  — 264 linhas (linha_id FK, ENUM
--       tipo_dia, SEM coluna periodo)
--     fiscalizacao.registro_partida    —   1 linha  (ENUM status/motivo_perda,
--       motorista_re/cobrador_re por partida — desenho antigo, D4/D17 mudou)
--     fiscalizacao.turno_fiscal        —   7 linhas (linha_id único — D9 mudou
--       para ponto cobrindo N linhas)
--     fiscalizacao.linha_fiscal, previsao_recolhida, refeicao,
--       jornada_operador, usuario (a tabela de auth morta — D1), além de
--       alerta_manutencao/baita_registro/cobrador/evento_veiculo vazias, e
--       as views vw_ipp_diario/vw_pareto_motivos/vw_operadores_recorrentes.
--   `CREATE TABLE IF NOT EXISTS fiscalizacao.partida_programada` contra uma
--   tabela já existente com esse nome mas colunas incompatíveis faria um
--   NO-OP silencioso — a tabela ficaria com o schema de julho, e todo
--   INSERT/SELECT deste módulo quebraria em produção na primeira chamada.
--   Confirmado com o Alisson (22/08/2026): as tabelas antigas não guardam
--   nada que precise sobreviver — DROP SCHEMA CASCADE + recriar do zero.
--   NENHUMA outra migration deste projeto apaga schema ou tabela; esta é a
--   exceção deliberada, documentada aqui para não virar precedente.
--
-- POR QUÊ (D2 do desenho)
--   O fiscal já monta à mão, todo fim de turno, uma mensagem de WhatsApp:
--   linha, período, partidas programadas/realizadas, os seis contadores de
--   ocorrência, observações do dia, BAITA e ANTI-BAITA. Este módulo é o app
--   que escreve essa mensagem sozinho — marcar partida é o meio, o fechamento
--   pronto às 23h45 é o fim. Mesmo molde de app/services/mensagem_sinistro.py.
--
-- DECISÕES DE DESENHO QUE MOLDAM ESTE SCHEMA (ver DESENHO-modulo-fiscalizacao.md)
--   D4  🔑 Contadores são EVENTOS (evento_turno), não perdas. Perdas =
--       programadas − realizadas_programadas, sempre calculado, nunca um
--       contador próprio. Uma R.A pode acontecer sem custar viagem; uma
--       viagem pode se perder sem R.A (motivo OUTRO, sem contador).
--   D5  Lista de motivo/contador CONGELADA nos seis que aparecem nos
--       fechamentos reais — sem TRANSITO (não aparece em nenhum dos três
--       fechamentos coletados) e FALTA_OPERADORES é UM contador, não dois.
--   D6  Viagem extra soma em realizadas_total, mas realizadas_programadas e
--       extras ficam em colunas/contagens SEPARADAS na view — perder essa
--       separação apaga para sempre a pergunta "quanto foi programado e
--       quanto foi extra".
--   D7  ATRASADA é estado CALCULADO NA LEITURA (backend, nunca aqui) —
--       nenhuma rotina marca partida como perdida por decurso de tempo.
--   D8  Período é '1'/'2' (1º/2º período — a linguagem do fechamento e da
--       planilha), nunca MANHA/TARDE.
--   D9  Turno é por PONTO (pessoa+ponto+período+data), cobrindo N linhas —
--       não por linha como o turno_fiscal de julho.
--   D13 BAITA/ANTI-BAITA dentro do primeiro corte — duas linhas por turno.
--   D14 pastas_prefixo é opcional (só um campo: o carro que leva as pastas).
--   D15 Refeição do FISCAL (dois campos de hora no turno) — não confundir
--       com jornada/refeição da dupla, que fica fora (D16).
--   D17 Recolhida é leitura cruzada por VIEW (vw_partida_recolhida), nunca
--       coluna copiada — ⛔ zero escrita cruzando os módulos nos dois
--       sentidos, ⛔ Fiscalização não abre ficha de manutenção (quem abre é
--       a Portaria; duas portas gerando a mesma ficha enche a fila da
--       manutenção com o mesmo carro).
--   D18 Prefixo só é pedido ao fiscal quando o motivo é RA ou SOS.
--
-- REGRA DE FRONTEIRA (mesma da 012/024/026)
--   ⛔ Zero FK para tabela operacional do Pátio (onibus, linha, fila,
--   alocacao_patio, alerta, escala, motorista). Única FK para fora do schema:
--   public.funcionario (identidade). linha_codigo, prefixo e todo *_re são
--   SNAPSHOT texto — sem exceção, este módulo não tem a exceção que a 026 tem
--   para ficha_manutencao (a Fiscalização não abre ficha — D17).
--
-- ⚠️ DUAS CORREÇÕES MECÂNICAS AO TEXTO LITERAL DO PROMPT (registradas aqui,
--   não são decisão de negócio — são consistência de chave única):
--   1. partida_programada.UNIQUE ganhou `terminal` além de
--      (linha_codigo,tipo_dia,numero_tabela,sequencia,vigencia): o parser
--      (§7.2) numera sequencia_tp e sequencia_ts SEPARADAMENTE a partir de 1
--      — sem terminal na chave, a partida TP nº1 e a TS nº1 da mesma tabela
--      colidiriam na mesma UNIQUE.
--   2. registro_partida.UNIQUE ganhou `terminal` pelo mesmo motivo — TP e TS
--      podem, em tese, programar o mesmo horario_programado.
--
-- ⚠️ Sem SET search_path — todo objeto qualificado explicitamente com o
--   schema (fiscalizacao./public./portaria.), mesmo padrão da 026.
--
-- NATUREZA: aditiva e idempotente A PARTIR do DROP SCHEMA acima — depois do
--   DROP, tudo é CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS e INSERT ... ON
--   CONFLICT DO NOTHING, então rodar esta migration uma segunda vez não
--   apaga nem duplica nada (o schema já vai existir, IF NOT EXISTS resolve).
--
-- ⚠️ DADO PESSOAL: zero dado pessoal real em seed, fixture ou comentário.
--
-- 🔴 NÃO faz UPDATE funcao SET modulo_padrao='FISCALIZACAO' — já foi feito
--   pela migration 013 (FISCAL já aponta pra lá) e a página fiscal.html
--   ainda não existe (não está em PAGINAS_PRONTAS do frontend). Isso é
--   tarefa do prompt da tela, não deste.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 029-modulo-fiscalizacao.sql
-- ============================================================================

-- ⚠️ Ver cabeçalho "ÚNICA MIGRATION NÃO-ADITIVA" acima — confirmado com o
-- Alisson em 22/08/2026, as tabelas do sistema de julho não guardam nada
-- que precise sobreviver.
DROP SCHEMA IF EXISTS fiscalizacao CASCADE;

CREATE SCHEMA IF NOT EXISTS fiscalizacao;
COMMENT ON SCHEMA fiscalizacao IS 'Módulo Fiscalização — turnos do fiscal, registro de partidas, contadores e fechamento. Isolado do public (Pátio) e dos demais módulos. Sem FK para tabela operacional do Pátio.';

-- ============================================================================
-- 1 · PONTO — pontos finais de fiscalização (D9, D10)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.ponto (
    codigo     VARCHAR(20) PRIMARY KEY,
    nome       VARCHAR(60) NOT NULL,
    terminal   VARCHAR(2)  NOT NULL CHECK (terminal IN ('TP','TS')),
    ativo      BOOLEAN     NOT NULL DEFAULT TRUE,
    criado_em  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fiscalizacao.ponto IS 'Ponto final de fiscalização. O turno é do PONTO, não da linha (D9) — a G3 tem ponto com mais de uma linha saindo junto. terminal fixa D10: um fiscal por terminal (TP ou TS), nunca os dois no mesmo turno.';

-- ⚠️ Nenhum ponto seedado aqui — o Alisson ainda vai levantar quais pontos
-- têm quais linhas na G3 (lição de casa nº 2 do desenho). Ver
-- database/seeds/09-fiscalizacao-pontos.sql.exemplo.

-- ============================================================================
-- 2 · PONTO_LINHA — quais linhas saem de cada ponto (D9, D11)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.ponto_linha (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ponto_codigo  VARCHAR(20) NOT NULL REFERENCES fiscalizacao.ponto(codigo),
    linha_codigo  VARCHAR(20) NOT NULL,  -- ⛔ sem FK — regra de fronteira
    ativo         BOOLEAN     NOT NULL DEFAULT TRUE,
    UNIQUE (ponto_codigo, linha_codigo)
);
COMMENT ON TABLE fiscalizacao.ponto_linha IS 'Quais linhas saem de cada ponto — ponto com mais de uma linha é comum na G3 (D9). Alimenta as abas do app do fiscal (D11) e a confirmação de linhas ao abrir turno.';

-- ============================================================================
-- 3 · TURNO — pessoa + ponto + período + data (D8, D9, D14, D15)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.turno (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    funcionario_id    UUID        NOT NULL REFERENCES public.funcionario(id),
    fiscal_re         VARCHAR(20) NOT NULL,  -- snapshot — o fechamento imprime "FISCAL RE... 11959"

    ponto_codigo      VARCHAR(20) NOT NULL REFERENCES fiscalizacao.ponto(codigo),
    terminal          VARCHAR(2)  NOT NULL CHECK (terminal IN ('TP','TS')),  -- snapshot do ponto
    periodo           VARCHAR(1)  NOT NULL CHECK (periodo IN ('1','2')),    -- D8: 1º/2º período, não MANHA/TARDE

    data_referencia   DATE        NOT NULL DEFAULT (NOW() AT TIME ZONE 'America/Sao_Paulo')::date,
    tipo_dia          VARCHAR(8)  CHECK (tipo_dia IN ('UTIL','SABADO','DOMINGO')),

    status            VARCHAR(10) NOT NULL DEFAULT 'ABERTO' CHECK (status IN ('ABERTO','FECHADO')),

    refeicao_inicio   TIME,  -- D15 — refeição do PRÓPRIO fiscal, vira linha do OBS
    refeicao_fim      TIME,

    pastas_prefixo    VARCHAR(10),  -- D14 — opcional, o carro que leva as pastas

    aberto_em         TIMESTAMPTZ,
    fechado_em        TIMESTAMPTZ,

    criado_em         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em     TIMESTAMPTZ
);
COMMENT ON TABLE fiscalizacao.turno IS 'Turno = pessoa + ponto + período (1º/2º) + data, cobrindo N linhas (D9) — fundação diferente do turno_fiscal de julho, que amarrava a uma linha só. Refeição (D15) e pastas (D14) moram aqui porque são do turno inteiro, não de uma linha ou partida.';
COMMENT ON COLUMN fiscalizacao.turno.fiscal_re IS 'Snapshot do RE do fiscal — o fechamento imprime "FISCAL RE... 11959" mesmo que o cadastro do funcionário mude depois.';
COMMENT ON COLUMN fiscalizacao.turno.terminal IS 'Snapshot de fiscalizacao.ponto.terminal no momento de abrir o turno — D10: um fiscal por terminal.';
COMMENT ON COLUMN fiscalizacao.turno.data_referencia IS 'Dia de calendário em America/Sao_Paulo (FUSO_OPERACAO) — ⛔ nunca get_data_servico() do Pátio (a regra das 20h é só do alocacao_patio) nem CURRENT_DATE cru do servidor. O backend sempre preenche explicitamente; o default aqui é rede de segurança para INSERT direto.';
COMMENT ON COLUMN fiscalizacao.turno.refeicao_inicio IS 'D15: refeição do PRÓPRIO fiscal — dois campos de hora que ele informa sobre si mesmo. Não é a refeição da dupla (motorista/cobrador), que fica fora do módulo (D16).';

-- Um turno ABERTO por (funcionario_id, ponto_codigo, periodo, data_referencia) — segundo turno igual → 409.
CREATE UNIQUE INDEX IF NOT EXISTS uq_turno_aberto_por_pessoa_ponto_periodo_dia
    ON fiscalizacao.turno (funcionario_id, ponto_codigo, periodo, data_referencia)
    WHERE status = 'ABERTO';

CREATE INDEX IF NOT EXISTS idx_turno_data ON fiscalizacao.turno (data_referencia);
CREATE INDEX IF NOT EXISTS idx_turno_ponto ON fiscalizacao.turno (ponto_codigo, data_referencia);

CREATE OR REPLACE FUNCTION fiscalizacao.fn_set_atualizado_em()
RETURNS TRIGGER AS $$
BEGIN
    NEW.atualizado_em := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
COMMENT ON FUNCTION fiscalizacao.fn_set_atualizado_em IS 'Trigger BEFORE UPDATE — mesmo padrão de public.fn_set_atualizado_em (migration 006), copiado para dentro do schema porque este módulo não referencia objetos de public além de funcionario (regra de fronteira).';

CREATE TRIGGER trg_turno_atualizado
    BEFORE UPDATE ON fiscalizacao.turno
    FOR EACH ROW EXECUTE FUNCTION fiscalizacao.fn_set_atualizado_em();

-- ============================================================================
-- 4 · TURNO_LINHA — quais linhas este turno cobre hoje
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.turno_linha (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turno_id      UUID NOT NULL REFERENCES fiscalizacao.turno(id) ON DELETE CASCADE,
    linha_codigo  VARCHAR(20) NOT NULL,  -- ⛔ sem FK
    UNIQUE (turno_id, linha_codigo)
);
COMMENT ON TABLE fiscalizacao.turno_linha IS 'Linhas que este turno efetivamente cobre hoje — uma linha do ponto pode não ser fiscalizada num dia, por isso é tabela própria, não derivação automática de ponto_linha.';

-- ============================================================================
-- 5 · PARTIDA_PROGRAMADA — a grade importada da escala gerencial (§7)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.partida_programada (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    linha_codigo   VARCHAR(20) NOT NULL,
    tipo_dia       VARCHAR(8)  NOT NULL CHECK (tipo_dia IN ('UTIL','SABADO','DOMINGO')),
    numero_tabela  SMALLINT    NOT NULL,
    sequencia      SMALLINT    NOT NULL,
    terminal       VARCHAR(2)  NOT NULL CHECK (terminal IN ('TP','TS')),
    horario        TIME        NOT NULL,
    periodo        VARCHAR(1)  CHECK (periodo IN ('1','2')),  -- nullable de propósito, ver comentário
    vigencia       DATE        NOT NULL,
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ⚠️ Ganhou `terminal` em relação ao texto literal do prompt — ver
    -- cabeçalho "DUAS CORREÇÕES MECÂNICAS".
    UNIQUE (linha_codigo, tipo_dia, numero_tabela, sequencia, terminal, vigencia)
);
COMMENT ON TABLE fiscalizacao.partida_programada IS 'A grade importada da escala gerencial (§7 do prompt) — vem de app/services/importacao_escala_fiscal.py, porta do parser de julho (escalas.py::_parse_aba, ~400 linhas de heurística sobre planilha irregular, já provadas contra o arquivo real).';
COMMENT ON COLUMN fiscalizacao.partida_programada.periodo IS 'NULL de propósito quando o parser não consegue determinar o período (§7.3) — nunca chuta. Partida com periodo IS NULL aparece no turno pelo filtro de horário (ver services/fiscalizacao.py); o importador reporta quantas ficaram assim no retorno do endpoint.';

CREATE INDEX IF NOT EXISTS idx_partida_prog_linha_vigencia
    ON fiscalizacao.partida_programada (linha_codigo, tipo_dia, vigencia DESC);

-- ============================================================================
-- 6 · REGISTRO_PARTIDA — o que o fiscal respondeu (D7, D18)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.registro_partida (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turno_id                  UUID NOT NULL REFERENCES fiscalizacao.turno(id) ON DELETE CASCADE,
    partida_programada_id     UUID REFERENCES fiscalizacao.partida_programada(id) ON DELETE SET NULL,

    -- Snapshot (o registro sobrevive à reimportação da grade)
    linha_codigo              VARCHAR(20) NOT NULL,
    numero_tabela             SMALLINT    NOT NULL,
    terminal                  VARCHAR(2)  NOT NULL CHECK (terminal IN ('TP','TS')),
    horario_programado        TIME        NOT NULL,

    resultado                 VARCHAR(10) NOT NULL CHECK (resultado IN ('REALIZADA','PERDIDA')),
    horario_real              TIME,

    motivo                    VARCHAR(24) CHECK (motivo IN (
                                   'FALTA_OPERADORES','RA','SOS','ATRASO_GARAGEM',
                                   'TROCA_OPERACIONAL','OUTRO'
                               )),
    motivo_outro              TEXT,       -- texto livre; vira linha do OBS
    prefixo                   VARCHAR(10),-- D18 — só quando RA/SOS
    operador_re               VARCHAR(20),-- quando FALTA_OPERADORES

    observacao                TEXT,

    registrado_em             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em             TIMESTAMPTZ,

    CONSTRAINT ck_registro_perdida_exige_motivo
        CHECK (resultado <> 'PERDIDA' OR motivo IS NOT NULL),
    CONSTRAINT ck_registro_outro_exige_texto
        CHECK (motivo <> 'OUTRO' OR motivo_outro IS NOT NULL),
    CONSTRAINT ck_registro_ra_sos_exige_prefixo
        CHECK (motivo NOT IN ('RA','SOS') OR prefixo IS NOT NULL),

    -- ⚠️ Ganhou `terminal` em relação ao texto literal do prompt — ver
    -- cabeçalho "DUAS CORREÇÕES MECÂNICAS".
    UNIQUE (turno_id, linha_codigo, numero_tabela, terminal, horario_programado)
);
COMMENT ON TABLE fiscalizacao.registro_partida IS 'O que o fiscal respondeu — uma resposta por horário programado por turno, e ele pode mudar a resposta (upsert pela UNIQUE). Snapshot de linha/tabela/terminal/horário: o registro sobrevive à reimportação da grade.';
COMMENT ON COLUMN fiscalizacao.registro_partida.motivo IS 'D5 — lista congelada nos seis do fechamento real (sem TRANSITO). Marcar PERDIDA com motivo RA/SOS/FALTA_OPERADORES/ATRASO_GARAGEM/TROCA_OPERACIONAL cria também um evento_turno vinculado (D4). OUTRO não cria evento — vira linha do OBS a partir de motivo_outro.';
COMMENT ON COLUMN fiscalizacao.registro_partida.prefixo IS 'D18 — só pedido ao fiscal quando motivo é RA ou SOS (ck_registro_ra_sos_exige_prefixo). Serve o cruzamento de leitura com portaria.recolhida_anormal (D17, vw_partida_recolhida).';

CREATE INDEX IF NOT EXISTS idx_registro_partida_turno ON fiscalizacao.registro_partida (turno_id);
CREATE INDEX IF NOT EXISTS idx_registro_partida_prefixo_motivo
    ON fiscalizacao.registro_partida (prefixo, motivo) WHERE prefixo IS NOT NULL;

-- ⚠️ Armadilha já registrada duas vezes neste projeto (constraint única
-- imediata + flush do SQLAlchemy): se algum serviço limpar e recriar a
-- coleção de registro_partida de um turno, db.flush() logo depois do
-- .clear(), antes dos .append() — ver §13 do SISTEMA-EM-PRODUCAO.md.

CREATE TRIGGER trg_registro_partida_atualizado
    BEFORE UPDATE ON fiscalizacao.registro_partida
    FOR EACH ROW EXECUTE FUNCTION fiscalizacao.fn_set_atualizado_em();

-- ============================================================================
-- 7 · EVENTO_TURNO — os seis contadores, como EVENTOS (D4 — decisão central)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.evento_turno (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turno_id               UUID NOT NULL REFERENCES fiscalizacao.turno(id) ON DELETE CASCADE,
    linha_codigo           VARCHAR(20) NOT NULL,  -- o fechamento é por linha
    tipo                   VARCHAR(24) NOT NULL CHECK (tipo IN (
                               'FALTA_OPERADORES','RA','SOS','ATRASO_GARAGEM',
                               'TROCA_OPERACIONAL','VIAGEM_EXTRA'
                           )),
    horario                TIME,
    numero_tabela          SMALLINT,
    prefixo                VARCHAR(10),
    observacao             TEXT,
    registro_partida_id    UUID REFERENCES fiscalizacao.registro_partida(id) ON DELETE SET NULL,
    criado_em              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fiscalizacao.evento_turno IS '🔑 D4 — a decisão mais importante da modelagem: contadores são EVENTOS, não perdas. Verificado no fechamento real do 2032-10: programadas 40, realizadas 39 → 1 perda; mas os contadores marcavam R.A 01 E Atraso de garagem 01 → 2 eventos; a perda de verdade foi "sem condobus", sem contador, só no OBS. Uma R.A pode acontecer sem custar viagem; uma viagem pode se perder sem R.A.';
COMMENT ON COLUMN fiscalizacao.evento_turno.registro_partida_id IS 'Preenchido quando o evento nasceu de marcar uma partida PERDIDA (motivo RA/SOS/FALTA_OPERADORES/ATRASO_GARAGEM/TROCA_OPERACIONAL). NULL para evento avulso (registrado sem que tenha havido perda — endpoint POST .../eventos). Desfazer/alterar a resposta da partida ajusta o evento vinculado por este campo.';

CREATE INDEX IF NOT EXISTS idx_evento_turno_turno_linha_tipo
    ON fiscalizacao.evento_turno (turno_id, linha_codigo, tipo);
CREATE INDEX IF NOT EXISTS idx_evento_turno_registro
    ON fiscalizacao.evento_turno (registro_partida_id) WHERE registro_partida_id IS NOT NULL;

-- ============================================================================
-- 8 · OBSERVACAO_TURNO — as linhas de 👉🏼 do OBS (D5, escape hatch)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.observacao_turno (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turno_id       UUID NOT NULL REFERENCES fiscalizacao.turno(id) ON DELETE CASCADE,
    linha_codigo   VARCHAR(20),  -- NULL = observação do turno inteiro
    numero_tabela  SMALLINT,
    horario        TIME,
    texto          TEXT NOT NULL,
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fiscalizacao.observacao_turno IS 'D5 — o escape hatch: motivo que ainda não virou contador (ex.: "sem condobus", "deixou o carro por conta") vive aqui em texto livre. "O sistema vai se moldando com o que o fiscal preenche" — o que aparecer muito no OBS vira contador na versão seguinte, com evidência, não palpite.';

CREATE INDEX IF NOT EXISTS idx_observacao_turno ON fiscalizacao.observacao_turno (turno_id, criado_em);

-- ============================================================================
-- 9 · BAITA — BAITA e ANTI-BAITA, dois registros por turno (D13)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.baita (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turno_id       UUID NOT NULL REFERENCES fiscalizacao.turno(id) ON DELETE CASCADE,
    linha_codigo   VARCHAR(20) NOT NULL,
    tipo           VARCHAR(12) NOT NULL CHECK (tipo IN ('BAITA','ANTI_BAITA')),
    prefixo        VARCHAR(10) NOT NULL,
    motorista_re   VARCHAR(20),
    cobrador_re    VARCHAR(20),
    saida_tp       TIME,
    saida_ts       TIME,
    ts_circular    BOOLEAN NOT NULL DEFAULT FALSE,
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em  TIMESTAMPTZ,

    CONSTRAINT ck_baita_ts_circular_sem_horario
        CHECK (ts_circular = FALSE OR saida_ts IS NULL),

    UNIQUE (turno_id, linha_codigo, tipo)
);
COMMENT ON TABLE fiscalizacao.baita IS 'D13 — dentro do primeiro corte (a revisão 1 do desenho tinha deixado de fora; errado, são parte obrigatória do fechamento). Duas duplas por turno inteiro (BAITA e ANTI_BAITA), não uma por tabela — barato.';
COMMENT ON COLUMN fiscalizacao.baita.ts_circular IS '"SAÍDA DO TS: circular" — a linha não tem terminal secundário. ⛔ NÃO é o mesmo que "recolheu direto do TS" (rec_ts do sistema de julho) — são coisas diferentes.';

CREATE TRIGGER trg_baita_atualizado
    BEFORE UPDATE ON fiscalizacao.baita
    FOR EACH ROW EXECUTE FUNCTION fiscalizacao.fn_set_atualizado_em();

-- ============================================================================
-- 10 · VIEWS
-- ============================================================================

-- vw_fechamento_turno — uma linha por (turno_id, linha_codigo). Base é
-- turno_linha (D9: turno cobre N linhas) — uma linha do turno sem nenhum
-- registro ainda aparece com zeros, não some da view.
CREATE OR REPLACE VIEW fiscalizacao.vw_fechamento_turno AS
SELECT
    t.id AS turno_id,
    tl.linha_codigo,
    COALESCE(prog.programadas, 0)                                        AS programadas,
    COALESCE(real.realizadas_programadas, 0)                             AS realizadas_programadas,
    COALESCE(ev.extras, 0)                                                AS extras,
    COALESCE(real.realizadas_programadas, 0) + COALESCE(ev.extras, 0)    AS realizadas_total,
    COALESCE(prog.programadas, 0) - COALESCE(real.realizadas_programadas, 0) AS perdidas,
    COALESCE(ev.falta_operadores, 0)   AS falta_operadores,
    COALESCE(ev.ra, 0)                 AS ra,
    COALESCE(ev.sos, 0)                AS sos,
    COALESCE(ev.atraso_garagem, 0)     AS atraso_garagem,
    COALESCE(ev.troca_operacional, 0)  AS troca_operacional,
    COALESCE(ev.viagem_extra, 0)       AS viagem_extra
  FROM fiscalizacao.turno t
  JOIN fiscalizacao.turno_linha tl ON tl.turno_id = t.id
  LEFT JOIN LATERAL (
      SELECT COUNT(*) AS programadas
        FROM fiscalizacao.partida_programada pp
       WHERE pp.linha_codigo = tl.linha_codigo
         AND pp.tipo_dia = t.tipo_dia
         AND pp.periodo = t.periodo
         AND pp.vigencia = (
             SELECT MAX(pp2.vigencia)
               FROM fiscalizacao.partida_programada pp2
              WHERE pp2.linha_codigo = tl.linha_codigo
                AND pp2.tipo_dia = t.tipo_dia
                AND pp2.vigencia <= t.data_referencia
         )
  ) prog ON TRUE
  LEFT JOIN LATERAL (
      SELECT COUNT(*) AS realizadas_programadas
        FROM fiscalizacao.registro_partida rp
       WHERE rp.turno_id = t.id
         AND rp.linha_codigo = tl.linha_codigo
         AND rp.resultado = 'REALIZADA'
  ) real ON TRUE
  LEFT JOIN LATERAL (
      SELECT
          COUNT(*) FILTER (WHERE et.tipo = 'VIAGEM_EXTRA')       AS extras,
          COUNT(*) FILTER (WHERE et.tipo = 'FALTA_OPERADORES')   AS falta_operadores,
          COUNT(*) FILTER (WHERE et.tipo = 'RA')                 AS ra,
          COUNT(*) FILTER (WHERE et.tipo = 'SOS')                AS sos,
          COUNT(*) FILTER (WHERE et.tipo = 'ATRASO_GARAGEM')     AS atraso_garagem,
          COUNT(*) FILTER (WHERE et.tipo = 'TROCA_OPERACIONAL')  AS troca_operacional,
          COUNT(*) FILTER (WHERE et.tipo = 'VIAGEM_EXTRA')       AS viagem_extra
        FROM fiscalizacao.evento_turno et
       WHERE et.turno_id = t.id AND et.linha_codigo = tl.linha_codigo
  ) ev ON TRUE;

COMMENT ON VIEW fiscalizacao.vw_fechamento_turno IS 'D6 — expõe realizadas_programadas e extras SEPARADOS, além do realizadas_total somado. O fechamento imprime o total; a análise futura consegue separar. "programadas" usa a vigência mais recente <= data_referencia do turno para (linha_codigo, tipo_dia) — partida com periodo IS NULL fica fora deste total (ver comentário de partida_programada.periodo); ela ainda aparece na listagem do turno pelo filtro de horário, feito no backend.';

-- vw_ipp_diario — por linha e DATA (soma os dois períodos do dia).
CREATE OR REPLACE VIEW fiscalizacao.vw_ipp_diario AS
SELECT
    t.data_referencia,
    vf.linha_codigo,
    SUM(vf.programadas)       AS programadas,
    SUM(vf.realizadas_total)  AS realizadas_total,
    CASE WHEN SUM(vf.programadas) > 0
         THEN ROUND(SUM(vf.realizadas_total)::numeric / SUM(vf.programadas) * 100, 2)
         ELSE NULL
    END AS ipp_percentual
  FROM fiscalizacao.vw_fechamento_turno vf
  JOIN fiscalizacao.turno t ON t.id = vf.turno_id
 GROUP BY t.data_referencia, vf.linha_codigo;

COMMENT ON VIEW fiscalizacao.vw_ipp_diario IS 'IPP por linha e dia — soma 1º e 2º período do mesmo dia. Reaproveita a lógica de julho (D8 do §8 do desenho), reescrita sobre as tabelas novas.';

-- vw_pareto_motivos — contagem e percentual por motivo, por linha.
CREATE OR REPLACE VIEW fiscalizacao.vw_pareto_motivos AS
SELECT
    rp.linha_codigo,
    rp.motivo,
    COUNT(*) AS total,
    ROUND(
        COUNT(*)::numeric * 100 / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY rp.linha_codigo), 0),
        2
    ) AS percentual
  FROM fiscalizacao.registro_partida rp
 WHERE rp.resultado = 'PERDIDA'
 GROUP BY rp.linha_codigo, rp.motivo;

COMMENT ON VIEW fiscalizacao.vw_pareto_motivos IS 'Pareto de motivo de perda, por linha — só partidas PERDIDA. Percentual é sobre o total de perdas DA MESMA linha (partição), não do total geral.';

-- vw_partida_recolhida — o cruzamento com a Portaria (D17), SOMENTE LEITURA.
-- Constante nomeada (não existe variável em VIEW pura): JANELA_RECOLHIDA = 6 horas.
CREATE OR REPLACE VIEW fiscalizacao.vw_partida_recolhida AS
SELECT
    rp.id AS registro_partida_id,
    rp.turno_id,
    rp.linha_codigo,
    rp.numero_tabela,
    rp.horario_programado,
    rp.motivo,
    rp.prefixo,
    ra.id AS recolhida_id,
    ra.momento,
    ra.motivo AS recolhida_motivo,
    ra.avaliacao,
    ra.prazo_minutos
  FROM fiscalizacao.registro_partida rp
  JOIN fiscalizacao.turno t ON t.id = rp.turno_id
  LEFT JOIN LATERAL (
      SELECT ra2.id, ra2.momento, ra2.motivo, ra2.avaliacao, ra2.prazo_minutos
        FROM portaria.recolhida_anormal ra2
       WHERE ra2.prefixo = rp.prefixo
         -- momento é instante real (TIMESTAMPTZ) — não filtra por
         -- data_referencia de propósito: partida perto da meia-noite pode
         -- recolher no dia seguinte, e data_referencia não deve decidir
         -- isso, só a janela de tempo abaixo.
         AND ra2.momento >= (t.data_referencia + rp.horario_programado) AT TIME ZONE 'America/Sao_Paulo'
         AND ra2.momento <= (t.data_referencia + rp.horario_programado) AT TIME ZONE 'America/Sao_Paulo' + INTERVAL '6 hours'  -- JANELA_RECOLHIDA
       ORDER BY ra2.momento ASC
       LIMIT 1
  ) ra ON TRUE
 WHERE rp.resultado = 'PERDIDA'
   AND rp.motivo IN ('RA','SOS')
   AND rp.prefixo IS NOT NULL;

COMMENT ON VIEW fiscalizacao.vw_partida_recolhida IS 'D17 — cruzamento SOMENTE LEITURA com portaria.recolhida_anormal, por prefixo + janela de tempo (recolhida mais próxima DEPOIS do horário programado, teto de 6h — JANELA_RECOLHIDA). ⛔ A Fiscalização nunca escreve em portaria.*, e a Portaria nunca escreve aqui. ⛔ Nenhuma coluna recolhida_id em registro_partida — dado de outro módulo copiado vira mentira no dia em que o outro módulo corrigir o dele; a correlação vive só nesta view.';

-- ============================================================================
-- 11 · RBAC — recurso fiscalizacao_painel (fiscalizacao já existe, migration 011)
-- ============================================================================

-- 'fiscalizacao' já é um dos recursos do catálogo desde a 011 — não duplicar.
-- 'fiscalizacao_painel' é novo, espelhando a separação recolhida_anormal /
-- recolhida_gerencial que a Portaria já usa (migration 026).
INSERT INTO public.recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
    ('fiscalizacao_painel', 'Painel da Fiscalização', 'Visão agregada: linha ao vivo, IPP, Pareto e cruzamento com a recolhida.', 'FISCALIZACAO', 16)
ON CONFLICT (codigo) DO NOTHING;

-- ON CONFLICT DO NOTHING de propósito nas linhas de 'fiscalizacao': o
-- catálogo já tem essas permissões desde 28/07/2026 (database/seeds/
-- 08-funcao-permissao.sql) — não sobrescrever o que já está lá, só
-- acrescentar 'fiscalizacao_painel', que é novo.
INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, v.recurso, v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('FISCAL',               'fiscalizacao',         TRUE,  TRUE),
        ('FISCAL',               'fiscalizacao_painel',  FALSE, FALSE),  -- ⛔ D — fiscal registra, não vê agregado

        ('COORDENADOR_TRAFEGO',  'fiscalizacao_painel',  TRUE,  FALSE),
        ('ENCARREGADO',          'fiscalizacao_painel',  TRUE,  FALSE),
        ('GERENTE_OPERACIONAL',  'fiscalizacao_painel',  TRUE,  FALSE),
        ('GERENTE_GERAL',        'fiscalizacao_painel',  TRUE,  FALSE),

        ('ADMIN',                'fiscalizacao',         TRUE,  TRUE),
        ('ADMIN',                'fiscalizacao_painel',  TRUE,  FALSE)
       ) AS v(funcao_codigo, recurso, pode_ler, pode_escrever) ON v.funcao_codigo = fn.codigo
 ON CONFLICT ON CONSTRAINT uq_funcao_permissao DO NOTHING;

-- 🔴 NÃO faz UPDATE funcao SET modulo_padrao='FISCALIZACAO' — ver cabeçalho.

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'fiscalizacao' ORDER BY table_name;  -- esperado: 9 tabelas
--
-- Prova da fronteira — nenhuma FK deste schema aponta para tabela
-- operacional do Pátio, e a única FK pra fora do schema é funcionario
-- (esperado: só funcionario.id, nada de onibus/linha/fila/alocacao_patio/
-- alerta/escala/motorista):
--   SELECT tc.table_name, ccu.table_schema || '.' || ccu.table_name AS aponta_para
--     FROM information_schema.table_constraints tc
--     JOIN information_schema.constraint_column_usage ccu
--       ON ccu.constraint_name = tc.constraint_name
--    WHERE tc.table_schema = 'fiscalizacao' AND tc.constraint_type = 'FOREIGN KEY';
--
-- Prova de que FISCAL não tem fiscalizacao_painel (esperado: pode_ler=FALSE, pode_escrever=FALSE):
--   SELECT fp.pode_ler, fp.pode_escrever FROM public.funcao_permissao fp
--     JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fn.codigo = 'FISCAL' AND fp.recurso = 'fiscalizacao_painel';
--
-- Prova de que modulo_padrao do FISCAL não foi mexido por esta migration
-- (esperado: FISCALIZACAO — já era assim desde a 013):
--   SELECT modulo_padrao FROM public.funcao WHERE codigo = 'FISCAL';
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DELETE FROM public.funcao_permissao WHERE recurso = 'fiscalizacao_painel';
-- DELETE FROM public.recurso WHERE codigo = 'fiscalizacao_painel';
-- DROP SCHEMA IF EXISTS fiscalizacao CASCADE;
-- ⚠️ Rollback NÃO restaura as tabelas do sistema de julho apagadas pelo DROP
-- do topo desta migration — eram 264+7+1+... linhas de teste, não dado de
-- produção (confirmado com o Alisson em 22/08/2026). Se precisar delas de
-- volta, é restore de backup do Postgres, não deste rollback.
-- ============================================================================
