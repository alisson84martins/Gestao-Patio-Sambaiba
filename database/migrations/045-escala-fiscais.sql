-- ============================================================================
-- MIGRATION 045 — Escala de Fiscais (aba da Coordenadoria) · Fase 1: banco
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: coordenadoria (existente — dono sambaiba) + public (RBAC — recurso,
--         funcao_permissao; só INSERT)
-- DATA:   2026-09-23
-- AUTOR:  Claude Code
-- DEPENDE DE: 011-rbac-cadastro-central.sql (funcionario/recurso/funcao/
--             funcao_permissao/modulo — módulo COORDENADORIA e funções
--             COORDENADOR_TRAFEGO, ADMIN e FISCAL já existem)
-- ORIGEM: pedido do Alisson em 23/09 — Fase 1 (banco) da aba Escala de
--         Fiscais. Estrutura das tabelas definida por ele, adaptada só ao
--         padrão das migrations 042–044.
-- REVISÃO 24/09 (decisões D-A e D-B do Alisson, corrigida NO PRÓPRIO
--         arquivo — a 045 só existia no banco local, com as tabelas vazias):
--   · D-A: FISCAL é referenciado pelo RE em TEXTO (VARCHAR(20)), SEM FK —
--          a escala aceita RE ainda não cadastrado em Pessoas. Coordenador
--          e "quem fez a ação" continuam com FK em funcionario(id).
--   · D-B: fidelidade ao Excel — coluna `marcador` guarda o texto original
--          quando não é RE (vazio, ****, xxx, G1, G2, G4, DIRETO, -).
-- ----------------------------------------------------------------------------
-- O QUE É — UMA ABA, não um sistema novo:
--   A escala diária dos fiscais (quem cobre cada posto — grupo de linhas
--   marcado numa ponta, TP ou TS — em cada período do dia) passa a ser
--   montada dentro do módulo COORDENADORIA, e o fiscal ganha uma tela só
--   de LEITURA com a própria escala publicada. ⛔ Não toca o módulo
--   Fiscalização nem o Pátio.
--
-- FRONTEIRA COM OS OUTROS MÓDULOS — por que não há FK para fora:
--   · A ÚNICA FK que sai do schema é para public.funcionario(id), e só para
--     o COORDENADOR (coordenador_periodo, coordenador_horario, plantao) e
--     para QUEM FEZ A AÇÃO (publicada_por, alterado_por, criado_por).
--   · O FISCAL é o RE em texto, sem FK (D-A): o Alisson cadastra os fiscais
--     em Pessoas um por um, e a escala não pode esperar. O nome vem de
--     funcionario por junção pelo RE NA LEITURA (quando existir); sem
--     cadastro, a tela mostra só o RE. RE é texto: sem espaço nas pontas,
--     nunca convertido para número, zero à esquerda preservado. A futura
--     "Minha escala" acha as alocações pelo RE do fiscal logado (índice
--     em escala_fiscal_alocacao(re)).
--   · escala_fiscal_posto_linha.linha é TEXTO, sem FK para o catálogo de
--     linhas do Pátio — módulos publicam fatos, ⛔ não se enxertam uns nos
--     outros (regra de fronteira, mesma da 042).
--   · escala_fiscal_ponto_final é catálogo PRÓPRIO; ⛔ não reaproveita
--     fiscalizacao.ponto (é outro módulo, outro significado).
--
-- DATA DE CALENDÁRIO, NÃO DATA OPERACIONAL:
--   escala_fiscal_dia.data vira à MEIA-NOITE. ⚠️ Diferente do Pátio, cuja
--   data_referencia vira às 20h (FUSO_OPERACAO). Quem ler a escala do dia
--   no backend usa a data civil de São Paulo, não a data do Pátio.
--
-- 🟢 PURAMENTE ADITIVA — 14 tabelas novas, todas com prefixo escala_fiscal_
--   e CREATE TABLE IF NOT EXISTS; 2 linhas novas em public.recurso e 3 em
--   public.funcao_permissao, todas com ON CONFLICT DO NOTHING. Nenhum
--   ALTER, nenhum DROP, nenhum UPDATE em tabela existente.
--
-- RBAC — dois recursos novos no módulo COORDENADORIA (menor privilégio,
--   padrão da migration 020):
--   · escala_fiscal          — montar/publicar a escala: ler+escrever para
--                              COORDENADOR_TRAFEGO e ADMIN.
--   · escala_fiscal_propria  — ver a PRÓPRIA escala publicada: só leitura
--                              para FISCAL. O filtro "só a própria" é regra
--                              de ROTA no backend (Fase 2), não do banco.
--   ⚠️ EFEITO COLATERAL CONHECIDO: vw_modulos_usuario libera o card de um
--   módulo para quem lê QUALQUER recurso dele. Depois desta migration todo
--   FISCAL enxerga o card COORDENADORIA no login. A Fase 2 precisa levar
--   quem só tem escala_fiscal_propria direto para a tela da própria
--   escala — ⛔ não subir esta migration em produção antes da Fase 2.
--
-- ⚠️ DADO PESSOAL: RE (texto) e funcionario_id (UUID). ⛔ Nenhum RE, nome ou
--   dado real nesta migration — o repositório é público. Os postos, pontos finais,
--   modelos, o quadro de fiscais e o horário dos coordenadores de plantão
--   são cadastrados pela tela (Fase 2), nunca por seed aqui.
--
-- DOIS TIPOS DE HORÁRIO, DUAS REGRAS:
--   · FISCAL (modelo_posto, alocacao): nenhum posto passa da meia-noite
--     → CHECK hora_termino > hora_inicio.
--   · COORDENADOR DE PLANTÃO (coordenador_horario, plantao): pode passar
--     da meia-noite (ex.: 15:00 às 02:30) → só CHECK hora_fim <> hora_inicio;
--     hora_fim menor = termina no dia seguinte.
--
-- GRANT NO FIM (seção 5): sem ele a aplicação quebra só em produção, como
--   aconteceu com a avaria em 17/09. Rodando com SET ROLE sambaiba o role
--   já é dono das tabelas e o GRANT é inofensivo; ele existe para o caso
--   de a migration ser rodada por outro role.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 045-escala-fiscais.sql
-- ============================================================================

SET search_path TO coordenadoria, public;

-- ============================================================================
-- 1 · CADASTROS — quem, onde e o quê
-- ============================================================================

-- Dados de escala de cada fiscal. A chave é o RE em texto (D-A): entra
-- fiscal ainda não cadastrado em Pessoas; o nome vem de funcionario pelo RE
-- na leitura, quando existir. Um fiscal, uma linha.
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_quadro (
    re             VARCHAR(20) PRIMARY KEY CHECK (re = btrim(re) AND re <> ''),
    periodo        SMALLINT NOT NULL CHECK (periodo IN (1, 2)),
    -- Dia de folga nos meses ÍMPARES; nos pares inverte (regra da Fase 2).
    folga_base     VARCHAR(7) CHECK (folga_base IN ('sabado', 'domingo')),
    ativo          BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE coordenadoria.escala_fiscal_quadro IS 'Quadro de fiscais da escala: período fixo (1º ou 2º) e folga-base de fim de semana de cada fiscal. Chave = RE em texto, sem FK (aceita RE ainda não cadastrado em Pessoas); nome por junção com public.funcionario.re na leitura.';

-- Qual coordenador edita qual período (um coordenador por período, na
-- prática; a PK composta permite um coordenador responder pelos dois).
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_coordenador_periodo (
    funcionario_id UUID NOT NULL REFERENCES public.funcionario(id),
    periodo        SMALLINT NOT NULL CHECK (periodo IN (1, 2)),
    PRIMARY KEY (funcionario_id, periodo)
);
COMMENT ON TABLE coordenadoria.escala_fiscal_coordenador_periodo IS 'Qual coordenador de tráfego é responsável por editar a escala de qual período.';

-- Local onde as linhas fazem final (base da regra de acúmulo de postos).
-- ⛔ Catálogo próprio — NÃO usar fiscalizacao.ponto.
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_ponto_final (
    id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nome  VARCHAR(80) NOT NULL UNIQUE,
    ativo BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE coordenadoria.escala_fiscal_ponto_final IS 'Pontos finais onde as linhas terminam. Postos no mesmo ponto final podem ser acumulados por um só fiscal. Catálogo próprio da escala, independente de fiscalizacao.ponto.';

-- Posto = grupo de linhas marcado numa ponta (TP ou TS).
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_posto (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lado           VARCHAR(2) NOT NULL CHECK (lado IN ('TP', 'TS')),
    cod_jb         VARCHAR(40),
    lote           VARCHAR(20),
    ponto_final_id UUID REFERENCES coordenadoria.escala_fiscal_ponto_final(id),
    ativo          BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE coordenadoria.escala_fiscal_posto IS 'Posto de fiscalização: um grupo de linhas marcado numa ponta (TP = terminal principal, TS = terminal secundário).';

CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_posto_linha (
    posto_id UUID NOT NULL REFERENCES coordenadoria.escala_fiscal_posto(id),
    -- Texto, ⛔ sem FK para o catálogo de linhas do Pátio (fronteira).
    linha    VARCHAR(10) NOT NULL,
    ordem    SMALLINT NOT NULL DEFAULT 1,
    PRIMARY KEY (posto_id, linha)
);
COMMENT ON TABLE coordenadoria.escala_fiscal_posto_linha IS 'Linhas que compõem cada posto, na ordem de exibição. linha é texto, sem FK para o Pátio.';

-- ============================================================================
-- 2 · MODELOS — o "esqueleto" de cada tipo de dia
-- ============================================================================

-- Modelo de dia: útil, sábado ímpar, domingo par, feriado, natal, ano novo.
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_modelo (
    id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo_dia VARCHAR(20) NOT NULL CHECK (tipo_dia IN ('util', 'sabado', 'domingo', 'feriado', 'natal', 'ano_novo')),
    paridade VARCHAR(5) CHECK (paridade IN ('impar', 'par')),
    nome     VARCHAR(60) NOT NULL UNIQUE
);
COMMENT ON TABLE coordenadoria.escala_fiscal_modelo IS 'Modelo (gabarito) de escala por tipo de dia e, no fim de semana, paridade do mês. Um dia concreto nasce copiando o seu modelo.';

CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_modelo_posto (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    modelo_id             UUID NOT NULL REFERENCES coordenadoria.escala_fiscal_modelo(id),
    posto_id              UUID NOT NULL REFERENCES coordenadoria.escala_fiscal_posto(id),
    ordem                 SMALLINT NOT NULL,
    periodo               SMALLINT NOT NULL CHECK (periodo IN (1, 2)),
    hora_inicio           TIME,
    hora_termino          TIME,
    situacao_padrao       VARCHAR(15) NOT NULL CHECK (situacao_padrao IN ('escalado', 'outra_garagem', 'descoberto', 'direto')),
    -- D-A: RE do fiscal em texto, sem FK (aceita RE ainda não cadastrado).
    re_padrao             VARCHAR(20) CHECK (re_padrao IS NULL OR (re_padrao = btrim(re_padrao) AND re_padrao <> '')),
    outra_garagem         VARCHAR(3),
    -- D-B: texto original da célula quando NÃO é RE ('', '****', 'xxx',
    -- 'G1', 'DIRETO', '-'...); NULL quando a célula tem RE. A impressão
    -- mostra exatamente este texto.
    marcador              VARCHAR(10),
    CHECK (hora_termino IS NULL OR hora_inicio IS NULL OR hora_termino > hora_inicio),
    -- 'escalado' ⇔ tem RE (mesma regra da alocação).
    CHECK ((situacao_padrao = 'escalado') = (re_padrao IS NOT NULL)),
    -- G3 é a própria garagem — "outra garagem" nunca pode ser ela.
    CHECK (outra_garagem IS NULL OR (outra_garagem ~ '^G[0-9]+$' AND outra_garagem <> 'G3'))
);
COMMENT ON TABLE coordenadoria.escala_fiscal_modelo_posto IS 'Linha do modelo: posto, período, horário e quem cobre por padrão (RE em texto, sem FK). marcador = texto original da planilha quando não é RE. Copiado para escala_fiscal_alocacao quando o dia é gerado.';

-- ============================================================================
-- 3 · ESCALA DO DIA — o que foi montado, publicado e alterado
-- ============================================================================

-- Escala de um dia concreto. data = data de CALENDÁRIO (vira à meia-noite,
-- ⚠️ não às 20h como a data_referencia do Pátio).
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_dia (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data          DATE NOT NULL UNIQUE,
    modelo_id     UUID NOT NULL REFERENCES coordenadoria.escala_fiscal_modelo(id),
    status        VARCHAR(10) NOT NULL DEFAULT 'rascunho' CHECK (status IN ('rascunho', 'publicada')),
    versao        INT NOT NULL DEFAULT 1,
    publicada_em  TIMESTAMPTZ,
    publicada_por UUID REFERENCES public.funcionario(id)
);
COMMENT ON TABLE coordenadoria.escala_fiscal_dia IS 'Escala de um dia de calendário (vira à meia-noite). Rascunho só o coordenador vê; publicada o fiscal vê. versao sobe a cada alteração depois de publicada.';

-- Quem cobre cada posto em cada período naquele dia.
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_alocacao (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    escala_dia_id UUID NOT NULL REFERENCES coordenadoria.escala_fiscal_dia(id),
    posto_id      UUID NOT NULL REFERENCES coordenadoria.escala_fiscal_posto(id),
    periodo       SMALLINT NOT NULL CHECK (periodo IN (1, 2)),
    hora_inicio   TIME,
    hora_termino  TIME,
    situacao      VARCHAR(15) NOT NULL CHECK (situacao IN ('escalado', 'outra_garagem', 'descoberto', 'direto')),
    -- D-A: RE do fiscal em texto, sem FK. A futura "Minha escala" acha as
    -- alocações do fiscal logado por este campo (índice logo abaixo).
    re            VARCHAR(20) CHECK (re IS NULL OR (re = btrim(re) AND re <> '')),
    outra_garagem VARCHAR(3),
    -- D-B: texto original quando não é RE; NULL quando tem RE.
    marcador      VARCHAR(10),
    alterado_por  UUID REFERENCES public.funcionario(id),
    alterado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (escala_dia_id, posto_id, periodo),
    CHECK (hora_termino IS NULL OR hora_inicio IS NULL OR hora_termino > hora_inicio),
    -- 'escalado' ⇔ tem RE; qualquer outra situação ⇔ sem RE.
    CHECK ((situacao = 'escalado') = (re IS NOT NULL)),
    -- 'outra_garagem' ⇔ tem o código da garagem que cobre.
    CHECK ((situacao = 'outra_garagem') = (outra_garagem IS NOT NULL)),
    -- G3 é a própria garagem — "outra garagem" nunca pode ser ela.
    CHECK (outra_garagem IS NULL OR (outra_garagem ~ '^G[0-9]+$' AND outra_garagem <> 'G3'))
);
COMMENT ON TABLE coordenadoria.escala_fiscal_alocacao IS 'Cobertura de um posto num período de um dia: fiscal escalado (RE em texto, sem FK), outra garagem, descoberto ou direto; marcador guarda o texto original. Uma linha por (dia, posto, período).';
CREATE INDEX IF NOT EXISTS ix_escala_fiscal_alocacao_re
    ON coordenadoria.escala_fiscal_alocacao (re) WHERE re IS NOT NULL;

-- Férias, atestado, afastamento. data_fim é INCLUSIVA; NULL = sem previsão.
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_ausencia (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- D-A: RE do fiscal em texto, sem FK.
    re             VARCHAR(20) NOT NULL CHECK (re = btrim(re) AND re <> ''),
    tipo           VARCHAR(12) NOT NULL CHECK (tipo IN ('ferias', 'atestado', 'afastado')),
    data_inicio    DATE NOT NULL,
    data_fim       DATE,
    observacao     TEXT,
    criado_por     UUID REFERENCES public.funcionario(id),
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (data_fim IS NULL OR data_fim >= data_inicio)
);
COMMENT ON TABLE coordenadoria.escala_fiscal_ausencia IS 'Ausência do fiscal (férias, atestado, afastado), pelo RE em texto. data_fim inclusiva; NULL = sem previsão de volta.';
CREATE INDEX IF NOT EXISTS ix_escala_fiscal_ausencia_re
    ON coordenadoria.escala_fiscal_ausencia (re);

-- Trocas de folga 2x2 e 1x1.
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_troca (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo          VARCHAR(3) NOT NULL CHECK (tipo IN ('2x2', '1x1')),
    -- D-A: RE dos dois fiscais em texto, sem FK.
    re_a          VARCHAR(20) NOT NULL CHECK (re_a = btrim(re_a) AND re_a <> ''),
    re_b          VARCHAR(20) NOT NULL CHECK (re_b = btrim(re_b) AND re_b <> ''),
    data_sabado   DATE NOT NULL,
    observacao    TEXT,
    criado_por    UUID REFERENCES public.funcionario(id),
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (re_a <> re_b)
);
COMMENT ON TABLE coordenadoria.escala_fiscal_troca IS 'Troca de folga de fim de semana entre dois fiscais (RE em texto), identificada pelo sábado do fim de semana trocado.';

-- Cadastro dos coordenadores de plantão e seu horário padrão (editável
-- pela coordenação). ⚠️ O horário do COORDENADOR pode passar da meia-noite
-- (ex.: 15:00 às 02:30) — por isso aqui NÃO vale "fim > início", como vale
-- para o fiscal em escala_fiscal_alocacao. hora_fim < hora_inicio = termina
-- no dia seguinte; só é proibido início = fim (turno de duração zero).
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_coordenador_horario (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    funcionario_id UUID NOT NULL REFERENCES public.funcionario(id),
    turno          VARCHAR(5) NOT NULL CHECK (turno IN ('manha', 'tarde')),
    hora_inicio    TIME NOT NULL,
    -- Se for menor que hora_inicio, termina no dia seguinte.
    hora_fim       TIME NOT NULL,
    ativo          BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (funcionario_id, turno),
    CHECK (hora_fim <> hora_inicio)
);
COMMENT ON TABLE coordenadoria.escala_fiscal_coordenador_horario IS 'Cadastro dos coordenadores de plantão: turno e horário padrão. Cada dia copia daqui para escala_fiscal_plantao. hora_fim menor que hora_inicio = termina no dia seguinte.';

-- Plantão de um dia concreto (rodapé da escala): nasce COPIADO do cadastro
-- acima e pode ser ajustado só naquele dia (ex.: coordenador que ficou no
-- fechamento por falta de pessoal) — o ajuste não mexe no cadastro.
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_plantao (
    escala_dia_id  UUID NOT NULL REFERENCES coordenadoria.escala_fiscal_dia(id),
    turno          VARCHAR(5) NOT NULL CHECK (turno IN ('manha', 'tarde')),
    ordem          SMALLINT NOT NULL DEFAULT 1,
    funcionario_id UUID NOT NULL REFERENCES public.funcionario(id),
    hora_inicio    TIME NOT NULL,
    -- Se for menor que hora_inicio, termina no dia seguinte.
    hora_fim       TIME NOT NULL,
    PRIMARY KEY (escala_dia_id, turno, ordem),
    CHECK (hora_fim <> hora_inicio)
);
COMMENT ON TABLE coordenadoria.escala_fiscal_plantao IS 'Rodapé da escala: coordenadores de plantão de um dia, copiados de escala_fiscal_coordenador_horario e ajustáveis só naquele dia. hora_fim menor que hora_inicio = termina no dia seguinte.';

-- Registro de alteração em escala já publicada.
CREATE TABLE IF NOT EXISTS coordenadoria.escala_fiscal_alteracao (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    escala_dia_id UUID NOT NULL REFERENCES coordenadoria.escala_fiscal_dia(id),
    versao        INT NOT NULL,
    alterado_por  UUID NOT NULL REFERENCES public.funcionario(id),
    alterado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    antes         JSONB,
    depois        JSONB
);
COMMENT ON TABLE coordenadoria.escala_fiscal_alteracao IS 'Histórico de alterações feitas numa escala depois de publicada: quem, quando, versão e o antes/depois em JSON.';

-- ============================================================================
-- 4 · RBAC — dois recursos novos no módulo COORDENADORIA
-- ============================================================================
-- Ordem 12 e 13: logo depois de pre_ocorrencia (11), sem reordenar nada.
INSERT INTO public.recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
  ('escala_fiscal',         'Escala de Fiscais',
   'Montar, publicar e alterar a escala diária dos fiscais.', 'COORDENADORIA', 12),
  ('escala_fiscal_propria', 'Minha Escala',
   'Fiscal vê só a própria escala publicada.',              'COORDENADORIA', 13)
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, v.recurso, v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('COORDENADOR_TRAFEGO', 'escala_fiscal',         TRUE,  TRUE),
        ('ADMIN',               'escala_fiscal',         TRUE,  TRUE),

        -- ⛔ Só leitura. "Só a própria" é filtro de rota (Fase 2).
        ('FISCAL',              'escala_fiscal_propria', TRUE,  FALSE)
       ) AS v(funcao, recurso, pode_ler, pode_escrever)
    ON v.funcao = fn.codigo
ON CONFLICT ON CONSTRAINT uq_funcao_permissao DO NOTHING;

-- ============================================================================
-- 5 · GRANT para a aplicação
-- ============================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA coordenadoria TO sambaiba;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA coordenadoria TO sambaiba;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- 1) As 14 tabelas existem e a aplicação lê/escreve em todas
--   --    (esperado: 14 linhas, todas t):
--   SELECT table_name,
--          has_table_privilege('sambaiba', 'coordenadoria.' || table_name, 'SELECT,INSERT,UPDATE,DELETE')
--     FROM information_schema.tables
--    WHERE table_schema = 'coordenadoria' AND table_name LIKE 'escala_fiscal%'
--    ORDER BY 1;
--
--   -- 2) Os dois recursos (esperado: 2 linhas, COORDENADORIA, 12 e 13):
--   SELECT codigo, modulo_codigo, ordem FROM public.recurso
--    WHERE codigo IN ('escala_fiscal', 'escala_fiscal_propria');
--
--   -- 3) Permissões (esperado: ADMIN e COORDENADOR_TRAFEGO t/t em
--   --    escala_fiscal; FISCAL t/f em escala_fiscal_propria; nada mais):
--   SELECT fn.codigo, fp.recurso, fp.pode_ler, fp.pode_escrever
--     FROM public.funcao_permissao fp JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fp.recurso LIKE 'escala_fiscal%' ORDER BY 2, 1;
--
--   -- 4) 'escalado' sem RE tem que FALHAR (CHECK) — teste dentro de
--   --    BEGIN ... ROLLBACK, nunca em produção com dado real.
--
--   -- Rodar o arquivo inteiro DUAS VEZES não erra nem duplica nada.
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ⚠️ Só com as tabelas VAZIAS ou com a escala exportada antes: o DROP
-- apaga a escala e o histórico de alterações. Ordem: filhas antes das mães.
-- DELETE FROM public.funcao_permissao WHERE recurso IN ('escala_fiscal', 'escala_fiscal_propria');
-- DELETE FROM public.recurso WHERE codigo IN ('escala_fiscal', 'escala_fiscal_propria');
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_alteracao;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_plantao;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_coordenador_horario;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_troca;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_ausencia;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_alocacao;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_dia;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_modelo_posto;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_modelo;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_posto_linha;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_posto;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_ponto_final;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_coordenador_periodo;
-- DROP TABLE IF EXISTS coordenadoria.escala_fiscal_quadro;
-- -- ⚠️ Depois do rollback, todo mundo desloga e loga de novo (módulos e
-- -- permissões ficam no localStorage desde o login).
-- ============================================================================
