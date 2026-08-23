-- ============================================================================
-- MIGRATION 030 — Fiscalização Bloco E: ICV apurado, coordenação por linha e ação da coordenação
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: fiscalizacao (já existe, criado pela 029)
-- DATA:   2026-08-23
-- AUTOR:  Claude Code
-- DEPENDE DE: 029-modulo-fiscalizacao.sql (schema fiscalizacao, tabela
--             partida_programada/registro_partida/turno/evento_turno,
--             recurso fiscalizacao_painel), 011-rbac-cadastro-central.sql
--             (funcionario, funcao, funcao_permissao)
-- ORIGEM: _handoff-claude/PROMPT-fiscalizacao-bloco-E-icv.md (D20-D30),
--         revisada por _handoff-claude/PROMPT-fiscalizacao-refatora-coordenador.md
--         antes de qualquer deploy — ver "🔁 REVISADA" abaixo.
-- ----------------------------------------------------------------------------
-- ⚠️ NÚMERO — os arquivos vão até 029. Próximo número livre: 030.
--
-- 🟢 ADITIVA — ao contrário da 029, esta migration só CRIA. Nenhum DROP.
--   A 029 é a única migration não-aditiva do projeto (documentado no
--   próprio cabeçalho dela) e não abre precedente — confirmado nunca se
--   repetir.
--
-- 🔁 REVISADA ANTES DE QUALQUER DEPLOY — "bacia" saiu, "linha_coordenador" entrou
--   Esta migration nunca rodou fora do banco local de desenvolvimento, e
--   origin/v3.0-dev nunca recebeu nenhum dos commits deste módulo — por
--   isso a correção editou o arquivo NO LUGAR, sem criar uma 031 e sem
--   tocar no histórico do git.
--
--   O motivo: "bacia" não é uma entidade da operação — é só o apelido do
--   conjunto de linhas de um coordenador, e o nome dela é o nome dele.
--   Modelar isso como tabela própria criava um substantivo para o que na
--   verdade é uma RELAÇÃO simples (esta linha, neste período, é deste
--   coordenador), e um cadastro que alguém teria que lembrar de manter.
--
--   Confirmado com o especialista de domínio antes do primeiro deploy:
--     1. Os dois "nomes de bacia" que apareciam por linha na planilha são
--        DOIS PERÍODOS cobertos por pessoas diferentes (1º período de
--        manhã, 2º à tarde) — não dois agrupamentos distintos de linhas.
--        As linhas são as mesmas nos dois. A associação usa a MESMA
--        dimensão que fiscalizacao.turno.periodo já usa (D8) — '1'/'2',
--        nunca um domínio novo.
--     2. Meta e corte de aceitável são ÚNICOS para toda a operação — não
--        por agrupamento.
--     3. O histórico do ICV não precisa se associar a coordenador
--        nenhum: icv_apurado já guarda por linha e por dia, então nenhum
--        número histórico se perde quando a responsabilidade muda — só o
--        agrupamento por coordenador passa a ser sempre o de HOJE. Por
--        isso linha_coordenador não tem vigência: trocar o coordenador é
--        um UPDATE, e é para ser assim mesmo.
--
-- POR QUÊ (D20 do prompt original)
--   O módulo Fiscalização (029) só calcula ICV a partir do que o fiscal
--   registra em campo — e nenhum fiscal está registrando partida ainda
--   (a tela dele, fiscal.html, é o Bloco D, ainda não construído). Mas a
--   gerência já manda, toda semana, uma planilha de ICV apurado pela
--   SPTrans (SIM) — importando essa planilha, o coordenador ganha
--   ranking e o seu ICV ponderado NA MESMA SEMANA, sem depender de
--   adesão de ninguém em campo. Esta migration é o banco que sustenta
--   essa camada.
--
-- DECISÕES QUE MOLDAM O SCHEMA
--   D20 Duas fontes de ICV convivem SEM se misturar numa coluna só:
--       icv_apurado (planilha da gerência, oficial) e o calculado de
--       registro_partida (campo, antecipa e explica). vw_icv_linha_dia
--       expõe as duas lado a lado.
--   D21 Coordenação por linha é RELAÇÃO DIRETA, sem vigência (ver
--       "🔁 REVISADA" acima) — o histórico do ICV é por linha e por dia,
--       nunca por coordenador.
--   D22 Todo agregado é PONDERADO por viagem programada — nunca média
--       simples de percentuais. Ver COMMENT ON VIEW de vw_icv_coordenador_dia.
--   D23 Priorização por PERDA ABSOLUTA (programadas × (1 − icv)), sempre
--       em view, nunca coluna gravada.
--   D24 Cascata é condição CALCULADA NA LEITURA sobre registro_partida —
--       nada gravado aqui, nenhuma tabela, nenhum job.
--   D25 Importador marca `suspeito` quando os DOIS contadores brutos
--       repetem o dia anterior já importado E o ICV do dia não é 100%.
--   D28 🔴 Reconciliação do denominador: icv_apurado.programadas é
--       gravado como veio da planilha, SEM comparar com partida_programada
--       automaticamente aqui — a divergência é calculada na leitura
--       (view/serviço), nunca escolhida/convertida na gravação.
--   D29 Meta e corte de aceitável são CONFIGURÁVEIS e ÚNICOS para toda a
--       operação (fiscalizacao.parametro), nunca hardcoded em código,
--       view ou seed — e nunca por agrupamento (ver decisão 2 acima).
--   D30 ICV pode passar de 100% — nenhum teto em nenhuma coluna nem view;
--       os contadores crus são gravados como vieram.
--
-- REGRA DE FRONTEIRA (mesma da 012/024/026/029)
--   ⛔ Zero FK para tabela operacional do Pátio. Única FK para fora do
--   schema: public.funcionario (identidade), sem prefixo "public." no
--   modelo (mesma pegadinha já documentada na 029). linha_codigo é
--   SNAPSHOT texto em toda tabela deste módulo — sem exceção.
--
-- ⚠️ Sem SET search_path — todo objeto qualificado explicitamente com o
--   schema (fiscalizacao./public.), mesmo padrão da 026/029.
--
-- ⚠️ DADO PESSOAL: zero dado pessoal real em seed, fixture ou comentário.
--   ⛔ Nenhum percentual de meta ou de corte, código de linha ou nome de
--   garagem escrito em código, view ou seed versionado — e, acima de
--   tudo, nenhum nome de pessoa em lugar nenhum do arquivo, nem em
--   comentário. fiscalizacao.parametro é o único lugar onde os números
--   de meta/corte existem.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 030-fiscalizacao-icv-bacia.sql
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS fiscalizacao;

-- ============================================================================
-- 1 · LINHA_COORDENADOR — quem coordena cada linha, por período
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.linha_coordenador (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    linha_codigo   VARCHAR(20) NOT NULL,  -- ⛔ sem FK — snapshot texto, regra de fronteira
    funcionario_id UUID        NOT NULL REFERENCES public.funcionario(id),
    periodo        VARCHAR(1)  NOT NULL CHECK (periodo IN ('1','2')),
    ativo          BOOLEAN     NOT NULL DEFAULT TRUE,
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em  TIMESTAMPTZ,

    UNIQUE (linha_codigo, periodo)
);
COMMENT ON TABLE fiscalizacao.linha_coordenador IS 'Não existe entidade "bacia" — o que a planilha da gerência chama de bacia é o conjunto de linhas de um coordenador, e o nome dela é o nome dele. Aqui a relação é direta: esta linha, neste período, é deste coordenador. ⛔ Sem vigência de propósito — o histórico do ICV é por linha e por dia (icv_apurado), então nenhum número se perde quando a responsabilidade muda. Trocar o coordenador é um UPDATE, e é para ser assim mesmo.';
COMMENT ON COLUMN fiscalizacao.linha_coordenador.periodo IS 'Mesmo domínio de fiscalizacao.turno.periodo (D8) — os dois períodos de uma linha são cobertos por PESSOAS diferentes (1º turno de manhã, 2º à tarde), não dois agrupamentos distintos de linhas. UNIQUE(linha_codigo, periodo): uma linha tem um coordenador por período.';

CREATE INDEX IF NOT EXISTS idx_linha_coordenador_funcionario
    ON fiscalizacao.linha_coordenador (funcionario_id);

DROP TRIGGER IF EXISTS trg_linha_coordenador_atualizado ON fiscalizacao.linha_coordenador;
CREATE TRIGGER trg_linha_coordenador_atualizado
    BEFORE UPDATE ON fiscalizacao.linha_coordenador
    FOR EACH ROW EXECUTE FUNCTION fiscalizacao.fn_set_atualizado_em();

-- ⚠️ Nenhuma linha/coordenador seedado aqui — ver
-- database/seeds/10-fiscalizacao-linha-coordenador.sql.exemplo.

-- ============================================================================
-- 2 · PARAMETRO — chave/valor para o que não pertence a nenhuma tabela de negócio (D29)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.parametro (
    chave         VARCHAR(40)  PRIMARY KEY,
    valor         NUMERIC(6,2) NOT NULL,
    descricao     TEXT         NOT NULL,
    atualizado_em TIMESTAMPTZ
);
COMMENT ON TABLE fiscalizacao.parametro IS 'D29 — configuração de negócio do módulo, não dado real de operação: meta oficial e corte de aceitável do ICV, iguais para toda a operação (não por agrupamento, não por linha). Os valores abaixo são parâmetro, não segredo nem dado pessoal — podem ser versionados.';

DROP TRIGGER IF EXISTS trg_parametro_atualizado ON fiscalizacao.parametro;
CREATE TRIGGER trg_parametro_atualizado
    BEFORE UPDATE ON fiscalizacao.parametro
    FOR EACH ROW EXECUTE FUNCTION fiscalizacao.fn_set_atualizado_em();

-- D29 — por que meta e aceitável são coisas diferentes (corrige uma
-- citação de corte errada, "~94,3%", numa versão anterior desta
-- migration — o corte real, confirmado com o Alisson, é 95%): das 12
-- linhas de uma carteira real analisada, 9 estavam abaixo da meta de 98%
-- e só 5 apareciam vermelhas — quatro linhas passavam por boas estando
-- abaixo da meta. É por isso que a comparação visível é sempre contra a
-- meta, e o corte serve só para a cor do farol.
INSERT INTO fiscalizacao.parametro (chave, valor, descricao) VALUES
    ('icv_meta',      98.00, 'Meta oficial cobrada — igual para todas as linhas.'),
    ('icv_aceitavel', 95.00, 'Corte de aceitável (cor do farol) — NÃO é a meta. Abaixo disto, a linha aparece crítica na cor.')
ON CONFLICT (chave) DO NOTHING;

-- ============================================================================
-- 3 · ICV_APURADO — o número oficial, importado da planilha da gerência (D20, D28, D30)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.icv_apurado (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    linha_codigo      VARCHAR(20)  NOT NULL,
    data_referencia   DATE         NOT NULL,
    programadas       INTEGER      NOT NULL CHECK (programadas >= 0),
    realizadas_tp_ts  INTEGER      NOT NULL DEFAULT 0,
    realizadas_ts_tp  INTEGER      NOT NULL DEFAULT 0,
    lote              VARCHAR(4)   CHECK (lote IN ('E2','AR2')),
    origem            VARCHAR(16)  NOT NULL DEFAULT 'PLANILHA' CHECK (origem IN ('PLANILHA','MANUAL')),
    suspeito          BOOLEAN      NOT NULL DEFAULT FALSE,
    suspeito_motivo   TEXT,
    arquivo_nome      VARCHAR(255),
    importado_por     UUID REFERENCES public.funcionario(id),
    bacia_texto       VARCHAR(60),
    criado_em         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    atualizado_em     TIMESTAMPTZ,

    UNIQUE (linha_codigo, data_referencia)
);
COMMENT ON TABLE fiscalizacao.icv_apurado IS 'D20 — o ICV OFICIAL, apurado pela SPTrans no SIM e importado da planilha semanal da gerência. Convive com o cálculo de registro_partida (campo) sem sobrescrever: as duas fontes aparecem lado a lado em vw_icv_linha_dia. UNIQUE(linha_codigo, data_referencia) — reimportar o mesmo dia ATUALIZA, não duplica.';
COMMENT ON COLUMN fiscalizacao.icv_apurado.realizadas_tp_ts IS 'Sentido TP→TS, coluna separada de realizadas_ts_tp (não só a soma) — casa com o registro do fiscal, que é por terminal (D10), e o dado real mostra a mesma linha com total igual e divisão diferente entre os dois sentidos em dias distintos.';
COMMENT ON COLUMN fiscalizacao.icv_apurado.programadas IS 'D30 — gravado como veio da planilha, SEM teto: o total realizado (tp_ts+ts_tp) pode superar este valor (viagem extra somando nas realizadas) e o ICV calculado passa de 100% de propósito — quem decide teto é a apresentação, nunca o banco.';
COMMENT ON COLUMN fiscalizacao.icv_apurado.suspeito IS 'D25 — TRUE quando os DOIS contadores brutos (realizadas_tp_ts e realizadas_ts_tp) são idênticos aos do dia anterior já importado para a mesma linha E o ICV do dia não é 100% (linha 100% que repete é esperado, não suspeito — célula arrastada no Excel é o padrão que este campo tenta pegar). Nunca recusa a linha, só registra e mostra (suspeito_motivo).';
COMMENT ON COLUMN fiscalizacao.icv_apurado.origem IS 'PLANILHA = veio do upload da planilha de ICV da gerência (app/services/importacao_icv.py). MANUAL = lançamento avulso pelo painel, para o dia em que a planilha atrasar.';
COMMENT ON COLUMN fiscalizacao.icv_apurado.bacia_texto IS 'O que a planilha da gerência chama de bacia, gravado exatamente como veio. Não é entidade e não tem FK — serve só para conferir depois se o agrupamento por coordenador do sistema (fiscalizacao.linha_coordenador) bate com o da gerência.';

CREATE INDEX IF NOT EXISTS idx_icv_apurado_linha_data
    ON fiscalizacao.icv_apurado (linha_codigo, data_referencia DESC);
CREATE INDEX IF NOT EXISTS idx_icv_apurado_data
    ON fiscalizacao.icv_apurado (data_referencia);

-- DROP + CREATE (não CREATE OR REPLACE TRIGGER) por portabilidade — mesmo
-- efeito idempotente sem depender de PG 14+.
DROP TRIGGER IF EXISTS trg_icv_apurado_atualizado ON fiscalizacao.icv_apurado;
CREATE TRIGGER trg_icv_apurado_atualizado
    BEFORE UPDATE ON fiscalizacao.icv_apurado
    FOR EACH ROW EXECUTE FUNCTION fiscalizacao.fn_set_atualizado_em();

-- ============================================================================
-- 4 · ACAO_COORDENACAO — "ação tomada" mora no coordenador (D26)
-- ============================================================================
CREATE TABLE IF NOT EXISTS fiscalizacao.acao_coordenacao (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    linha_codigo          VARCHAR(20) NOT NULL,
    data_referencia       DATE        NOT NULL,
    faixa_hora            SMALLINT    CHECK (faixa_hora BETWEEN 0 AND 23),
    descricao             TEXT        NOT NULL,
    resultado_observado   TEXT,
    registrado_por        UUID        NOT NULL REFERENCES public.funcionario(id),
    criado_em             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em         TIMESTAMPTZ
);
COMMENT ON TABLE fiscalizacao.acao_coordenacao IS 'D26 — o que o coordenador FEZ diante de um problema de linha, e o resultado observado. Único caso replicável observado nas planilhas reais analisadas (uma linha foi de 94,62% a 99,46% em dois dias por ação da equipe) e que se perde sem este registro. ⛔ Não é campo da tela do fiscal — quem age aqui é o coordenador, no painel.';
COMMENT ON COLUMN fiscalizacao.acao_coordenacao.faixa_hora IS 'Hora cheia (17, 18, 19...) opcional — mesma granularidade da cascata (D24). NULL = ação sobre o dia inteiro, não uma faixa específica.';

CREATE INDEX IF NOT EXISTS idx_acao_coordenacao_linha_data
    ON fiscalizacao.acao_coordenacao (linha_codigo, data_referencia DESC);

DROP TRIGGER IF EXISTS trg_acao_coordenacao_atualizado ON fiscalizacao.acao_coordenacao;
CREATE TRIGGER trg_acao_coordenacao_atualizado
    BEFORE UPDATE ON fiscalizacao.acao_coordenacao
    FOR EACH ROW EXECUTE FUNCTION fiscalizacao.fn_set_atualizado_em();

-- ============================================================================
-- 5 · VIEWS
-- ============================================================================

-- vw_icv_linha_dia — NÃO MUDA (já é por linha, sem nenhuma referência a
-- agrupamento). As duas fontes lado a lado, sem misturar (D20), com
-- perda_absoluta (D23) calculada sobre a fonte oficial quando existir,
-- senão sobre a de campo, e uma coluna dizendo qual fonte foi usada.
--
-- ⚠️ Esta view é a fonte da verdade documentada para Postgres/consulta
-- direta. A LEITURA usada pelos endpoints/testes é a função Python
-- equivalente em app/services/icv.py (calcular_icv_linha_dia) — mesmo
-- precedente já registrado em vw_fechamento_turno/calcular_fechamento_linha
-- (029): os testes rodam em SQLite, que não tem FULL OUTER JOIN em todas
-- as versões nem LATERAL igual ao Postgres, então a lógica é reimplementada
-- em Python para rodar igual nos dois bancos. Se as duas divergirem, ver
-- o débito já registrado em FISCALIZACAO-02-onde-paramos.md §7.
CREATE OR REPLACE VIEW fiscalizacao.vw_icv_linha_dia AS
WITH oficial AS (
    SELECT
        linha_codigo,
        data_referencia,
        programadas                              AS programadas_oficial,
        (realizadas_tp_ts + realizadas_ts_tp)     AS realizadas_oficial,
        CASE WHEN programadas > 0
             THEN ROUND((realizadas_tp_ts + realizadas_ts_tp)::numeric / programadas * 100, 2)
             ELSE NULL
        END                                       AS icv_oficial,
        suspeito
      FROM fiscalizacao.icv_apurado
),
campo AS (
    SELECT linha_codigo, data_referencia, programadas AS programadas_campo,
           realizadas_total AS realizadas_campo, ipp_percentual AS icv_campo
      FROM fiscalizacao.vw_ipp_diario
)
SELECT
    COALESCE(oficial.linha_codigo, campo.linha_codigo)         AS linha_codigo,
    COALESCE(oficial.data_referencia, campo.data_referencia)   AS data_referencia,
    oficial.programadas_oficial,
    oficial.realizadas_oficial,
    oficial.icv_oficial,
    COALESCE(oficial.suspeito, FALSE)                          AS suspeito,
    campo.programadas_campo,
    campo.realizadas_campo,
    campo.icv_campo,
    CASE
        WHEN oficial.programadas_oficial IS NOT NULL
             THEN ROUND(oficial.programadas_oficial * (1 - COALESCE(oficial.icv_oficial, 0) / 100.0), 2)
        WHEN campo.programadas_campo IS NOT NULL
             THEN ROUND(campo.programadas_campo * (1 - COALESCE(campo.icv_campo, 0) / 100.0), 2)
        ELSE NULL
    END                                                          AS perda_absoluta,
    CASE
        WHEN oficial.programadas_oficial IS NOT NULL THEN 'OFICIAL'
        WHEN campo.programadas_campo IS NOT NULL THEN 'CAMPO'
        ELSE NULL
    END                                                          AS fonte_perda_absoluta
  FROM oficial
  FULL OUTER JOIN campo
    ON campo.linha_codigo = oficial.linha_codigo AND campo.data_referencia = oficial.data_referencia;

COMMENT ON VIEW fiscalizacao.vw_icv_linha_dia IS 'D20 — as duas fontes de ICV (oficial da planilha, campo do fiscal) lado a lado, NUNCA numa coluna combinada. D23 — perda_absoluta = programadas × (1 − icv), calculada aqui, nunca gravada; usa a fonte oficial quando existe (é o número que a empresa cobra), senão a de campo, e fonte_perda_absoluta diz qual foi usada. D30 — icv_oficial e icv_campo não têm teto: podem passar de 100%.';

-- vw_icv_coordenador_dia (substitui vw_icv_bacia_dia) — agregado
-- PONDERADO (D22) por funcionario_id + periodo, via linha_coordenador.
-- 🔁 SEM RESOLUÇÃO POR DATA de propósito: linha_coordenador não tem
-- vigência (ver "REVISADA" no cabeçalho) — a composição usada é sempre a
-- de HOJE, então um agregado de uma data passada usa o coordenador ATUAL
-- daquela linha, não quem coordenava na época. O histórico do ICV é por
-- LINHA (icv_apurado), nunca por coordenador.
CREATE OR REPLACE VIEW fiscalizacao.vw_icv_coordenador_dia AS
SELECT
    lc.funcionario_id,
    lc.periodo,
    vild.data_referencia,
    SUM(COALESCE(vild.programadas_oficial, vild.programadas_campo, 0))  AS programadas,
    SUM(COALESCE(vild.realizadas_oficial, vild.realizadas_campo, 0))    AS realizadas,
    CASE WHEN SUM(COALESCE(vild.programadas_oficial, vild.programadas_campo, 0)) > 0
         THEN ROUND(
             SUM(COALESCE(vild.realizadas_oficial, vild.realizadas_campo, 0))::numeric
             / SUM(COALESCE(vild.programadas_oficial, vild.programadas_campo, 0)) * 100, 2)
         ELSE NULL
    END                                                                     AS icv_ponderado,
    (SELECT valor FROM fiscalizacao.parametro WHERE chave = 'icv_meta')       AS icv_meta,
    (SELECT valor FROM fiscalizacao.parametro WHERE chave = 'icv_aceitavel')  AS icv_aceitavel
  FROM fiscalizacao.linha_coordenador lc
  JOIN fiscalizacao.vw_icv_linha_dia vild
    ON vild.linha_codigo = lc.linha_codigo
 WHERE lc.ativo
 GROUP BY lc.funcionario_id, lc.periodo, vild.data_referencia;

COMMENT ON VIEW fiscalizacao.vw_icv_coordenador_dia IS 'D22 — 🔴 SOMA numerador e denominador de TODAS as linhas do coordenador (composição de HOJE, linha_coordenador não tem vigência) antes de dividir. ⛔ AVG(percentual) é proibido aqui e em qualquer lugar do módulo. icv_meta e icv_aceitavel vêm de fiscalizacao.parametro junto, para a tela não precisar buscar em outro lugar (D29) — nunca hardcoded aqui nem em código.';

-- vw_prioridade_linha — NÃO MUDA (D23): ranking por perda absoluta, com
-- divergência de denominador (D28). Só a coluna de agrupamento (que
-- vinha de bacia_linha) saiu — a ordenação e o cálculo de divergência
-- são exatamente os mesmos de antes.
CREATE OR REPLACE VIEW fiscalizacao.vw_prioridade_linha AS
SELECT
    vild.linha_codigo,
    vild.data_referencia,
    vild.programadas_oficial,
    vild.realizadas_oficial,
    vild.icv_oficial,
    vild.suspeito,
    vild.programadas_campo,
    vild.realizadas_campo,
    vild.icv_campo,
    vild.perda_absoluta,
    vild.fonte_perda_absoluta,
    escala.programado_escala,
    CASE
        WHEN vild.programadas_oficial IS NOT NULL
             AND escala.programado_escala IS NOT NULL
             AND vild.programadas_oficial <> escala.programado_escala
        THEN vild.programadas_oficial - escala.programado_escala
        ELSE NULL
    END                                           AS divergencia_denominador
  FROM fiscalizacao.vw_icv_linha_dia vild
  LEFT JOIN LATERAL (
      SELECT COUNT(*) AS programado_escala
        FROM fiscalizacao.partida_programada pp
       WHERE pp.linha_codigo = vild.linha_codigo
         AND pp.tipo_dia = (CASE EXTRACT(DOW FROM vild.data_referencia)
                                 WHEN 0 THEN 'DOMINGO' WHEN 6 THEN 'SABADO' ELSE 'UTIL' END)
         AND pp.vigencia = (
             SELECT MAX(pp2.vigencia) FROM fiscalizacao.partida_programada pp2
              WHERE pp2.linha_codigo = pp.linha_codigo AND pp2.tipo_dia = pp.tipo_dia
                AND pp2.vigencia <= vild.data_referencia
         )
  ) escala ON TRUE
 ORDER BY vild.perda_absoluta DESC NULLS LAST;

COMMENT ON VIEW fiscalizacao.vw_prioridade_linha IS 'D23 — o ranking: ORDER BY perda_absoluta DESC, nunca por percentual (uma linha "verde" a 95,22% que programa 212 perde mais viagem absoluta que uma a 91,53% que programa 13). É esta view que qualquer tela consome. D28 — 🔴 divergencia_denominador compara icv_apurado.programadas contra o total de partida_programada da mesma linha/tipo de dia (escala importada), SEM escolher um dos dois nem converter: só reporta a diferença, para "conferir antes de usar este número em reunião".';

-- ============================================================================
-- 6 · RBAC — escrita em fiscalizacao_painel para quem registra ação e importa ICV
-- ============================================================================

-- fiscalizacao_painel já existe (029). Este bloco só ACRESCENTA
-- pode_escrever=TRUE para COORDENADOR_TRAFEGO e ADMIN — DO UPDATE
-- (não DO NOTHING) porque a linha já existe da 029 com pode_escrever=FALSE
-- e precisa ser promovida, não ignorada.
INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, v.recurso, v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('COORDENADOR_TRAFEGO',  'fiscalizacao_painel',  TRUE, TRUE),
        ('ADMIN',                'fiscalizacao_painel',  TRUE, TRUE)
       ) AS v(funcao_codigo, recurso, pode_ler, pode_escrever) ON v.funcao_codigo = fn.codigo
 ON CONFLICT ON CONSTRAINT uq_funcao_permissao
 DO UPDATE SET pode_ler = EXCLUDED.pode_ler, pode_escrever = EXCLUDED.pode_escrever;

-- ⛔ FISCAL continua sem NADA em fiscalizacao_painel — não tocado aqui,
-- a linha ('FISCAL','fiscalizacao_painel',FALSE,FALSE) da 029 permanece.

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'fiscalizacao' AND table_name IN
--          ('linha_coordenador','parametro','icv_apurado','acao_coordenacao');  -- 4
--
--   SELECT table_name FROM information_schema.views
--    WHERE table_schema = 'fiscalizacao' AND table_name IN
--          ('vw_icv_linha_dia','vw_icv_coordenador_dia','vw_prioridade_linha');  -- 3
--
--   SELECT chave, valor FROM fiscalizacao.parametro ORDER BY chave;
--    -- esperado: icv_aceitavel=95.00, icv_meta=98.00
--
-- Prova de que COORDENADOR_TRAFEGO e ADMIN podem escrever em
-- fiscalizacao_painel e FISCAL continua sem nada:
--   SELECT fn.codigo, fp.pode_ler, fp.pode_escrever
--     FROM public.funcao_permissao fp JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fp.recurso = 'fiscalizacao_painel' ORDER BY fn.codigo;
--
-- Prova da fronteira — nenhuma FK nova aponta para tabela operacional do
-- Pátio (esperado: só funcionario, nada de onibus/linha/fila/alerta/etc.):
--   SELECT tc.table_name, ccu.table_schema || '.' || ccu.table_name AS aponta_para
--     FROM information_schema.table_constraints tc
--     JOIN information_schema.constraint_column_usage ccu
--       ON ccu.constraint_name = tc.constraint_name
--    WHERE tc.table_schema = 'fiscalizacao' AND tc.constraint_type = 'FOREIGN KEY'
--      AND tc.table_name IN ('linha_coordenador','parametro','icv_apurado','acao_coordenacao');
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DELETE FROM public.funcao_permissao
--  WHERE recurso = 'fiscalizacao_painel' AND funcao_id IN (
--      SELECT id FROM public.funcao WHERE codigo IN ('COORDENADOR_TRAFEGO','ADMIN')
--  );  -- ⚠️ isso também apagaria pode_ler, se algum dia o UPDATE acima mudar pode_ler
--      -- de algo que já não fosse TRUE — hoje é seguro pois ambos já liam.
-- DROP VIEW IF EXISTS fiscalizacao.vw_prioridade_linha;
-- DROP VIEW IF EXISTS fiscalizacao.vw_icv_coordenador_dia;
-- DROP VIEW IF EXISTS fiscalizacao.vw_icv_linha_dia;
-- DROP TABLE IF EXISTS fiscalizacao.acao_coordenacao;
-- DROP TABLE IF EXISTS fiscalizacao.icv_apurado;
-- DROP TABLE IF EXISTS fiscalizacao.parametro;
-- DROP TABLE IF EXISTS fiscalizacao.linha_coordenador;
-- ============================================================================
