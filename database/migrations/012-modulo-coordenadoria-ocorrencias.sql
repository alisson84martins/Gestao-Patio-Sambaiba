-- ============================================================================
-- MIGRATION 012 — Módulo Coordenadoria: Relatório de Ocorrências
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: coordenadoria  (novo — isolado do public do Pátio)
-- DATA:   2026-07-29
-- AUTOR:  Alisson Martins
-- DEPENDE DE: 011-rbac-cadastro-central.sql
-- ----------------------------------------------------------------------------
-- REGRA DE FRONTEIRA (fixada por Alisson em 28/07/2026)
--   Gestão de Pátio é módulo SEPARADO. Ocorrências NÃO influenciam alocação.
--   Por isso este módulo vive em schema próprio e **não tem FK para as tabelas
--   operacionais do Pátio** (onibus, linha, fila, alocacao_patio, alerta).
--   Prefixo, linha, RE de motorista e cobrador são gravados como TEXTO — mesmo
--   padrão que o módulo de fiscalização já usa (registro_partida.motorista_re).
--   Assim nenhum DELETE/UPDATE no Pátio arrasta ocorrência, e vice-versa.
--   A única FK para fora é `public.funcionario` — identidade, compartilhada
--   por toda a Suite.
--
-- ORIGEM DO MODELO
--   Transcrição fiel do formulário "Eu ❤ Sambaíba — RELATÓRIO DE OCORRÊNCIAS"
--   (4 páginas: Capa → Veículos → Análise → Testemunhas) + os 5 campos que a
--   mensagem do grupo do sinistro exige e o papel não tem.
--   Ver _handoff-claude/MODULO-OCORRENCIAS-FORMULARIO.md e -MENSAGEM-SINISTRO.md
--
-- ⚠️ DADO PESSOAL DE TERCEIRO
--   Vítimas, testemunhas e condutores de terceiros têm nome, RG, CPF, endereço
--   e telefone aqui. Nunca em seed, fixture, log ou repositório. Acesso só pelo
--   RBAC (recurso `ocorrencia`). Retenção a definir.
--
-- NATUREZA: aditiva. Não altera nada do que já existe.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 012-modulo-coordenadoria-ocorrencias.sql
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS coordenadoria;
COMMENT ON SCHEMA coordenadoria IS 'Módulo Coordenadoria — ocorrências internas. Isolado do schema public (Pátio).';

SET search_path TO coordenadoria, public;

-- ============================================================================
-- 1. CATÁLOGOS
-- ============================================================================

-- 1.1 · Tipos de ocorrência — os 12 do formulário. TABELA, não enum: tipo novo
--       entra com INSERT, sem migration (mesma razão de `funcao` e `recurso`).
CREATE TABLE IF NOT EXISTS coordenadoria.tipo_ocorrencia (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo      VARCHAR(30)  NOT NULL UNIQUE,
    nome        VARCHAR(60)  NOT NULL,
    exige_vitima      BOOLEAN NOT NULL DEFAULT FALSE,  -- sugere o bloco de vítimas
    exige_terceiro    BOOLEAN NOT NULL DEFAULT FALSE,  -- sugere o bloco de veículo de terceiro
    exige_analise     BOOLEAN NOT NULL DEFAULT FALSE,  -- sugere a página de análise/croqui
    ordem       SMALLINT     NOT NULL DEFAULT 99,
    ativo       BOOLEAN      NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE coordenadoria.tipo_ocorrencia IS 'Os 12 tipos do formulário em papel. As flags exige_* apenas SUGEREM seções na tela — não bloqueiam nada, e o impresso sai sempre com as 4 páginas.';

INSERT INTO coordenadoria.tipo_ocorrencia (codigo, nome, exige_vitima, exige_terceiro, exige_analise, ordem) VALUES
    ('ACIDENTE_COM_VITIMA',   'Acidente com Vítima',   TRUE,  TRUE,  TRUE,  1),
    ('ACIDENTE_SEM_VITIMA',   'Acidente sem Vítima',   FALSE, TRUE,  TRUE,  2),
    ('ATROPELAMENTO',         'Atropelamento',         TRUE,  FALSE, TRUE,  3),
    ('QUEDA_DE_PASSAGEIRO',   'Queda de Passageiro',   TRUE,  FALSE, FALSE, 4),
    ('INCIDENTE',             'Incidente',             FALSE, FALSE, FALSE, 5),
    ('VANDALISMO',            'Vandalismo',            FALSE, FALSE, FALSE, 6),
    ('AVARIA_NO_PATIO',       'Avaria no Pátio',       FALSE, FALSE, FALSE, 7),
    ('FURTO_DE_EQUIPAMENTOS', 'Furto de Equipamentos', FALSE, FALSE, FALSE, 8),
    ('CHOQUE',                'Choque',                FALSE, TRUE,  TRUE,  9),
    ('FURTO_DE_VEICULOS',     'Furto de Veículos',     FALSE, FALSE, FALSE,10),
    ('INCENDIO_DO_VEICULO',   'Incêndio do Veículo',   FALSE, FALSE, FALSE,11),
    ('OUTROS',                'Outros',                FALSE, FALSE, FALSE,12)
ON CONFLICT (codigo) DO NOTHING;

-- 1.2 · Órgãos de autoridade — catálogo aberto.
--       Decisão 29/07: nem sempre todos comparecem, mas a opção existe para todos.
CREATE TABLE IF NOT EXISTS coordenadoria.orgao_autoridade (
    id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo  VARCHAR(30) NOT NULL UNIQUE,
    nome    VARCHAR(60) NOT NULL,   -- rótulo como sai na mensagem do sinistro
    ordem   SMALLINT    NOT NULL DEFAULT 99,
    ativo   BOOLEAN     NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE coordenadoria.orgao_autoridade IS 'Órgãos que podem comparecer ao local. Catálogo aberto — órgão novo entra com INSERT.';

INSERT INTO coordenadoria.orgao_autoridade (codigo, nome, ordem) VALUES
    ('BOMBEIROS',       'Corpo de Bombeiros',            1),
    ('SAMU',            'SAMU',                          2),
    ('RESGATE',         'Resgate',                       3),
    ('PM',              'Polícia Militar',               4),
    ('PM_TRANSITO',     'Polícia Militar - Trânsito',    5),
    ('POLICIA_CIVIL',   'Polícia Civil',                 6),
    ('POLICIA_TECNICA', 'Polícia Técnica / Perícia',     7),
    ('GCM',             'Guarda Civil Metropolitana',    8),
    ('CET',             'CET',                           9),
    ('SPTRANS',         'SPTrans',                      10),
    ('POLICIA_RODOVIARIA','Polícia Rodoviária',         11),
    ('DEFESA_CIVIL',    'Defesa Civil',                 12),
    ('CONCESSIONARIA',  'Concessionária da via',        13),
    ('OUTRO',           'Outro',                        99)
ON CONFLICT (codigo) DO NOTHING;

-- ============================================================================
-- 2. OCORRENCIA — a capa do relatório (página 1)
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.ocorrencia (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    numero              BIGINT       GENERATED ALWAYS AS IDENTITY,  -- o "Nº" do formulário
    tipo_ocorrencia_id  UUID         NOT NULL REFERENCES coordenadoria.tipo_ocorrencia(id),
    status              VARCHAR(15)  NOT NULL DEFAULT 'RASCUNHO'
                        CHECK (status IN ('RASCUNHO','FINALIZADA','CANCELADA')),

    -- ── Quando ────────────────────────────────────────────────────────────
    data_ocorrencia     DATE         NOT NULL,
    hora_ocorrencia     TIME         NOT NULL,

    -- ── Nosso veículo (TEXTO, sem FK — ver regra de fronteira) ────────────
    prefixo             VARCHAR(10)  NOT NULL,
    placa               VARCHAR(10),
    linha_codigo        VARCHAR(20),

    -- ── Condutor ──────────────────────────────────────────────────────────
    condutor_re         VARCHAR(20),
    condutor_nome       VARCHAR(120),
    condutor_funcao     VARCHAR(40),
    condutor_cnh        VARCHAR(20),
    condutor_rg         VARCHAR(20),
    condutor_cpf        VARCHAR(14),
    direcao_defensiva   BOOLEAN,

    -- ── Cobrador ──────────────────────────────────────────────────────────
    cobrador_re         VARCHAR(20),
    cobrador_nome       VARCHAR(120),

    -- ── Velocidades e atendimento ─────────────────────────────────────────
    velocidade_via      SMALLINT,
    velocidade_onibus   SMALLINT,
    foi_ao_local        BOOLEAN,
    confirmado          BOOLEAN,

    -- ── Marcadores da via (checkboxes da capa) ────────────────────────────
    via_urbana          BOOLEAN NOT NULL DEFAULT FALSE,
    via_rodoviaria      BOOLEAN NOT NULL DEFAULT FALSE,
    area_interna        BOOLEAN NOT NULL DEFAULT FALSE,
    corredor            BOOLEAN NOT NULL DEFAULT FALSE,
    tem_fotos           BOOLEAN NOT NULL DEFAULT FALSE,
    monitoramento       BOOLEAN NOT NULL DEFAULT FALSE,
    sentido             VARCHAR(40),   -- 'TP-TS', 'TS-TP', 'TS', 'centro bairro'…

    -- ── Local ─────────────────────────────────────────────────────────────
    local_ocorrido      VARCHAR(200),
    numero_local        VARCHAR(20),
    bairro              VARCHAR(80),
    cidade              VARCHAR(80)  NOT NULL DEFAULT 'São Paulo',

    -- ── Contagem ──────────────────────────────────────────────────────────
    quant_acidentes     SMALLINT,
    isentos             SMALLINT,
    culpados            SMALLINT,

    -- ── Manutenção ────────────────────────────────────────────────────────
    problemas_mecanicos       BOOLEAN,
    problemas_mecanicos_qual  TEXT,
    condutor_avisou_manutencao BOOLEAN,
    manutencao_avisado_nome   VARCHAR(120),

    -- ── Narrativas (página 1) ─────────────────────────────────────────────
    descricao_coordenador  TEXT,   -- vira o bloco *OBS:* na mensagem do sinistro
    descricao_motorista    TEXT,   -- vira *DESCRIÇÃO DO NOSSO MOTORISTA*
    descricao_terceiro     TEXT,

    -- ── Ocorrência policial (página 4) ────────────────────────────────────
    ocorrencia_policial    BOOLEAN NOT NULL DEFAULT FALSE,
    viatura_numero         VARCHAR(30),
    bpm                    VARCHAR(30),
    cia                    VARCHAR(30),
    distrito               VARCHAR(60),
    numero_to              VARCHAR(40),
    numero_bo              VARCHAR(40),
    protocolo              VARCHAR(60),   -- ⚠️ novo: protocolo da delegacia eletrônica
    houve_policia_tecnica  BOOLEAN NOT NULL DEFAULT FALSE,
    nome_perito            VARCHAR(120),

    observacoes            TEXT,

    -- ── Campos que a mensagem do sinistro exige e o papel não tem ─────────
    controlador_acesso     VARCHAR(120),  -- ⚠️ novo: quem recebeu os relatórios

    -- ── Auditoria ─────────────────────────────────────────────────────────
    registrado_por      UUID REFERENCES public.funcionario(id) ON DELETE SET NULL,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em       TIMESTAMPTZ,
    atualizado_por      UUID REFERENCES public.funcionario(id) ON DELETE SET NULL,
    finalizada_em       TIMESTAMPTZ,
    excluida_em         TIMESTAMPTZ   -- soft delete
);
COMMENT ON TABLE  coordenadoria.ocorrencia IS 'Capa do Relatório de Ocorrências. Prefixo/linha/RE são texto de propósito: o módulo não depende do Pátio.';
COMMENT ON COLUMN coordenadoria.ocorrencia.numero IS 'Substitui o "Nº" preenchido à mão no bloco de papel.';
COMMENT ON COLUMN coordenadoria.ocorrencia.protocolo IS 'Protocolo da delegacia eletrônica. As CREDENCIAIS da delegacia nunca ficam no sistema — só o protocolo e o PDF do B.O.';
COMMENT ON COLUMN coordenadoria.ocorrencia.controlador_acesso IS 'Controlador de acesso que recebeu os relatórios (ex.: Matheus, Mary, Ademar). Só existe na mensagem do sinistro, não no papel.';

CREATE INDEX IF NOT EXISTS idx_ocorrencia_data    ON coordenadoria.ocorrencia (data_ocorrencia DESC);
CREATE INDEX IF NOT EXISTS idx_ocorrencia_tipo    ON coordenadoria.ocorrencia (tipo_ocorrencia_id);
CREATE INDEX IF NOT EXISTS idx_ocorrencia_prefixo ON coordenadoria.ocorrencia (prefixo);
CREATE INDEX IF NOT EXISTS idx_ocorrencia_linha   ON coordenadoria.ocorrencia (linha_codigo);
CREATE INDEX IF NOT EXISTS idx_ocorrencia_status  ON coordenadoria.ocorrencia (status) WHERE excluida_em IS NULL;

-- ============================================================================
-- 3. ANALISE DO ACIDENTE (página 3) — 1:1, só existe em ocorrência que analisa
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.ocorrencia_analise (
    ocorrencia_id  UUID PRIMARY KEY REFERENCES coordenadoria.ocorrencia(id) ON DELETE CASCADE,

    -- Tipo de colisão / acidente
    colisao        VARCHAR(15) CHECK (colisao IN ('FRONTAL','TRASEIRA','LATERAL')),
    acidente       VARCHAR(15) CHECK (acidente IN ('CAPOTAMENTO','TOMBAMENTO','ENGAVETAMENTO')),
    condicoes      VARCHAR(15) CHECK (condicoes IN ('TRANSITANDO','MANOBRANDO','PARADO')),
    deslocamento   VARCHAR(15) CHECK (deslocamento IN ('EM_FRENTE','EM_RE','REBOCADO')),

    -- Perfil da via / pista
    reta           VARCHAR(15) CHECK (reta  IN ('EM_PLANO','EM_ACLIVE','EM_DECLIVE')),
    curva          VARCHAR(15) CHECK (curva IN ('EM_PLANO','EM_ACLIVE','EM_DECLIVE','DEPRESSAO','LOMBADA','NORMAL')),
    via            VARCHAR(15) CHECK (via   IN ('TREVO','CRUZAMENTO','BIFURCACAO','NORMAL')),
    numero_faixas  VARCHAR(15) CHECK (numero_faixas IN ('UMA','DUAS','TRES','MAIS_DE_TRES')),
    mao_direcao    VARCHAR(20) CHECK (mao_direcao   IN ('UNICA','DUPLA','PRIVATIVA_COLETIVO')),
    preferencial   VARCHAR(15) CHECK (preferencial  IN ('SIM','NAO','NAO_SE_APLICA')),
    condicao_pista VARCHAR(15) CHECK (condicao_pista IN ('SECA','MOLHADA','OLEOSA','ENLAMEADA')),
    pavimentacao   VARCHAR(20) CHECK (pavimentacao  IN ('ASFALTO','CONCRETO','PARALELEPIPEDO','OUTROS')),
    conservacao    VARCHAR(15) CHECK (conservacao   IN ('BOM','DANIFICADO','EM_OBRAS')),

    -- Perfil de sinalização
    sinal_horizontal        VARCHAR(20) CHECK (sinal_horizontal IN ('NAO_EXISTE','FAIXA_SIMPLES','FAIXA_DUPLA','TRAV_PEDESTRE','PARE','OUTROS')),
    sinal_horizontal_outros VARCHAR(120),
    sinal_vertical          VARCHAR(20) CHECK (sinal_vertical IN ('NAO_EXISTE','PARE','ESCOLA','MAO_DE_DIRECAO','VELOCIDADE','PREFERENCIAL','OUTROS')),
    sinal_vertical_outros   VARCHAR(120),
    dispositivos_aux        VARCHAR(15) CHECK (dispositivos_aux IN ('NAO_EXISTE','NORMAL','DESLIGADO','COM_DEFEITO','ATENCAO')),

    -- Perfil do local
    iluminacao     VARCHAR(30) CHECK (iluminacao IN ('DIA','NOITE_COM_ILUMINACAO','NOITE_SEM_ILUMINACAO','ANOITECER_AMANHECER','OUTROS')),
    tempo          VARCHAR(15) CHECK (tempo      IN ('BOM','NUBLADO','CHUVA','GAROA','NEBLINA')),
    visibilidade   VARCHAR(15) CHECK (visibilidade IN ('BOM','REGULAR','MA')),

    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE coordenadoria.ocorrencia_analise IS 'Página "Análise do Acidente". Tabela 1:1 separada para não inchar a capa — só acidente de trânsito preenche.';

-- ============================================================================
-- 4. VEICULOS DE TERCEIRO (página 2)
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.ocorrencia_veiculo_terceiro (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ocorrencia_id  UUID NOT NULL REFERENCES coordenadoria.ocorrencia(id) ON DELETE CASCADE,
    ordem          SMALLINT NOT NULL DEFAULT 1,   -- "Veículo nº 1" / "nº 2"

    danos          VARCHAR(10) CHECK (danos IN ('GRANDE','MEDIO','PEQUENO')),
    marca          VARCHAR(40),
    modelo         VARCHAR(60),
    ano            VARCHAR(9),
    cor            VARCHAR(30),
    placa          VARCHAR(10),
    cidade_placa   VARCHAR(80),
    estado_placa   VARCHAR(2),
    renavam        VARCHAR(20),

    -- Dados pessoais do condutor/proprietário terceiro
    proprietario   VARCHAR(120),
    fones          VARCHAR(60),
    email          VARCHAR(120),
    endereco       VARCHAR(200),
    cidade         VARCHAR(80),
    rg             VARCHAR(20),
    cpf            VARCHAR(14),
    cnh            VARCHAR(20),

    seguradora     VARCHAR(80),
    seguradora_fone VARCHAR(30),
    sinistro_numero VARCHAR(40),
    partes_avariadas TEXT,

    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_veiculo_terceiro_ordem UNIQUE (ocorrencia_id, ordem)
);
COMMENT ON TABLE coordenadoria.ocorrencia_veiculo_terceiro IS 'Veículos de terceiro. O papel tem 2 blocos; a tabela aceita quantos forem (engavetamento).';

-- ============================================================================
-- 5. AVARIAS DO NOSSO VEICULO — decisão 29/07: caixa por região, não desenho
--    Ganho: permite Pareto de "onde nossos carros mais batem".
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.ocorrencia_avaria (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ocorrencia_id  UUID NOT NULL REFERENCES coordenadoria.ocorrencia(id) ON DELETE CASCADE,
    regiao         VARCHAR(25) NOT NULL
                   CHECK (regiao IN ('FRENTE','TRASEIRA','LATERAL_ESQUERDA','LATERAL_DIREITA',
                                     'TETO','INTERIOR','RODADO','RETROVISOR','PARABRISA','OUTRO')),
    descricao      TEXT,
    CONSTRAINT uq_avaria_regiao UNIQUE (ocorrencia_id, regiao)
);
COMMENT ON TABLE coordenadoria.ocorrencia_avaria IS 'Partes avariadas do NOSSO veículo, por região. Substitui a marcação no desenho do papel e vira dado consultável.';

-- ============================================================================
-- 6. VITIMAS (página 4)
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.ocorrencia_vitima (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ocorrencia_id  UUID NOT NULL REFERENCES coordenadoria.ocorrencia(id) ON DELETE CASCADE,
    ordem          SMALLINT NOT NULL DEFAULT 1,

    nome           VARCHAR(120) NOT NULL,
    rg             VARCHAR(20),
    cpf            VARCHAR(14),
    idade          SMALLINT,
    fone           VARCHAR(30),
    endereco       VARCHAR(200),
    numero         VARCHAR(20),
    bairro         VARCHAR(80),
    cidade         VARCHAR(80),
    dados_pessoais TEXT,

    era_passageiro BOOLEAN,          -- "quais eram passageiros do nosso ônibus?"

    -- ⚠️ novos: exigidos pela mensagem do sinistro, não existem no papel
    destino_socorro    VARCHAR(120), -- 'PS Santana', 'UPA Mooca'
    contato_parentesco VARCHAR(40),  -- 'Esposo', 'Filha'…
    contato_nome       VARCHAR(120),
    contato_fone       VARCHAR(30),

    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vitima_ordem UNIQUE (ocorrencia_id, ordem)
);
COMMENT ON TABLE coordenadoria.ocorrencia_vitima IS 'Pessoas vitimadas. DADO PESSOAL DE TERCEIRO — nunca em seed, log ou repositório.';

-- ============================================================================
-- 7. TESTEMUNHAS (página 4)
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.ocorrencia_testemunha (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ocorrencia_id  UUID NOT NULL REFERENCES coordenadoria.ocorrencia(id) ON DELETE CASCADE,
    ordem          SMALLINT NOT NULL DEFAULT 1,

    nome           VARCHAR(120) NOT NULL,
    rg             VARCHAR(20),
    endereco       VARCHAR(200),
    numero         VARCHAR(20),
    bairro         VARCHAR(80),
    cidade         VARCHAR(80),
    fone1          VARCHAR(30),
    fone2          VARCHAR(30),

    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_testemunha_ordem UNIQUE (ocorrencia_id, ordem)
);
COMMENT ON TABLE coordenadoria.ocorrencia_testemunha IS 'Testemunhas. DADO PESSOAL DE TERCEIRO. O papel tem 3 blocos; a tabela aceita quantas houver.';

-- ============================================================================
-- 8. AUTORIDADES NO LOCAL
--    ⚠️ Não existe no papel (que tem campo único "Viatura Nº / BPM / CIA").
--    A realidade exige vários: na ocorrência de 22/07 foram 4 autoridades de
--    3 órgãos — 2 viaturas dos Bombeiros e 1 da PM-Trânsito com 2 responsáveis.
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.ocorrencia_autoridade (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ocorrencia_id  UUID NOT NULL REFERENCES coordenadoria.ocorrencia(id) ON DELETE CASCADE,
    orgao_id       UUID NOT NULL REFERENCES coordenadoria.orgao_autoridade(id),

    identificacao  VARCHAR(40),   -- 'UR-02118', 'VT T-02135', 'VT 5378'
    responsavel    VARCHAR(200),  -- 'Tenente Moura', 'SD Eliseu Fernandes / SD Ana Paula'
    observacao     TEXT,
    ordem          SMALLINT NOT NULL DEFAULT 1,

    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE coordenadoria.ocorrencia_autoridade IS 'Autoridades que compareceram. Uma linha por viatura/equipe. Permite responder "em quantas ocorrências a SPTrans esteve presente no mês".';

CREATE INDEX IF NOT EXISTS idx_ocorrencia_autoridade_oc    ON coordenadoria.ocorrencia_autoridade (ocorrencia_id);
CREATE INDEX IF NOT EXISTS idx_ocorrencia_autoridade_orgao ON coordenadoria.ocorrencia_autoridade (orgao_id);

-- ============================================================================
-- 9. ANEXOS — fotos do acidente, dos relatórios, croqui e PDF do B.O.
--    Decisão 29/07: ficam no servidor, junto do registro.
--    O banco guarda só o CAMINHO e os metadados; o arquivo vive em disco.
-- ============================================================================
CREATE TABLE IF NOT EXISTS coordenadoria.ocorrencia_anexo (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ocorrencia_id  UUID NOT NULL REFERENCES coordenadoria.ocorrencia(id) ON DELETE CASCADE,
    tipo           VARCHAR(20) NOT NULL
                   CHECK (tipo IN ('FOTO_ACIDENTE','FOTO_RELATORIO','CROQUI','BO_PDF','OUTRO')),
    caminho        VARCHAR(255) NOT NULL,   -- caminho relativo no storage do servidor
    nome_original  VARCHAR(200),
    mime_type      VARCHAR(60),
    tamanho_bytes  BIGINT,
    largura        SMALLINT,
    altura         SMALLINT,
    descricao      TEXT,
    ordem          SMALLINT NOT NULL DEFAULT 1,

    enviado_por    UUID REFERENCES public.funcionario(id) ON DELETE SET NULL,
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE coordenadoria.ocorrencia_anexo IS 'Fotos e documentos da ocorrência. O arquivo fica em disco no servidor; aqui só o caminho e os metadados.';
COMMENT ON COLUMN coordenadoria.ocorrencia_anexo.caminho IS 'Relativo à raiz de uploads. ⚠️ Conferir espaço em disco do VPS antes de definir limite por ocorrência.';

CREATE INDEX IF NOT EXISTS idx_ocorrencia_anexo_oc ON coordenadoria.ocorrencia_anexo (ocorrencia_id, tipo);

-- ============================================================================
-- 10. VIEW de apoio — resumo para listagem e para o Pareto
-- ============================================================================
CREATE OR REPLACE VIEW coordenadoria.vw_ocorrencia_resumo AS
SELECT  o.id,
        o.numero,
        o.data_ocorrencia,
        o.hora_ocorrencia,
        t.codigo             AS tipo_codigo,
        t.nome               AS tipo_nome,
        o.prefixo,
        o.linha_codigo,
        o.bairro,
        o.status,
        (SELECT count(*) FROM coordenadoria.ocorrencia_vitima      v WHERE v.ocorrencia_id = o.id) AS qtd_vitimas,
        (SELECT count(*) FROM coordenadoria.ocorrencia_testemunha  w WHERE w.ocorrencia_id = o.id) AS qtd_testemunhas,
        (SELECT count(*) FROM coordenadoria.ocorrencia_autoridade  a WHERE a.ocorrencia_id = o.id) AS qtd_autoridades,
        (SELECT count(*) FROM coordenadoria.ocorrencia_anexo       x WHERE x.ocorrencia_id = o.id) AS qtd_anexos,
        f.nome               AS coordenador_nome,
        f.re                 AS coordenador_re
  FROM  coordenadoria.ocorrencia o
  JOIN  coordenadoria.tipo_ocorrencia t ON t.id = o.tipo_ocorrencia_id
  LEFT JOIN public.funcionario f        ON f.id = o.registrado_por
 WHERE  o.excluida_em IS NULL;

COMMENT ON VIEW coordenadoria.vw_ocorrencia_resumo IS 'Listagem de ocorrências e base do Pareto por tipo, linha, prefixo e bairro.';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT count(*) FROM coordenadoria.tipo_ocorrencia;    -- esperado: 12
--   SELECT count(*) FROM coordenadoria.orgao_autoridade;   -- esperado: 14
--   SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'coordenadoria' ORDER BY table_name;  -- esperado: 10 tabelas + 1 view
--
-- Prova da fronteira — nenhuma FK deste schema aponta para tabela operacional
-- do Pátio (esperado: 0 linhas):
--   SELECT tc.table_name, ccu.table_schema || '.' || ccu.table_name AS aponta_para
--     FROM information_schema.table_constraints tc
--     JOIN information_schema.constraint_column_usage ccu
--       ON ccu.constraint_name = tc.constraint_name
--    WHERE tc.table_schema = 'coordenadoria'
--      AND tc.constraint_type = 'FOREIGN KEY'
--      AND ccu.table_name IN ('onibus','linha','fila','alocacao_patio','alerta','escala','motorista');

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DROP SCHEMA IF EXISTS coordenadoria CASCADE;
-- (Seguro: nada fora do schema depende dele.)
-- ============================================================================
