-- ============================================================================
-- MIGRATION 024 — Módulo Portaria: Controle de Acesso Veicular
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria  (novo — isolado do public do Pátio e do coordenadoria)
-- DATA:   2026-08-20
-- AUTOR:  Claude Code
-- DEPENDE DE: 011-rbac-cadastro-central.sql (funcionario, funcao, recurso,
--             modulo, funcao_permissao), 013-destino-login-por-funcao.sql
--             (funcao.modulo_padrao)
-- ORIGEM: _handoff-claude/DESENHO-modulo-portaria.md (revisão 4, 19/08/2026)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Hoje a portaria de leves da garagem registra entrada e saída de veículo
--   num caderno de papel. O portão é MANUAL — não há cancela; quem abre é o
--   controlador de acesso. Este módulo tira o registro do papel sem mudar
--   nada da operação física: o app ajuda a lembrar e registrar, nunca decide
--   quem entra. Ver REGRA NÚMERO UM abaixo.
--
-- 🔴 REGRA NÚMERO UM DO MÓDULO — O SISTEMA NUNCA IMPEDE UM REGISTRO
--   Veículo não cadastrado? Registra e marca como não cadastrado.
--   Veículo suspenso/baixado? Registra, avisa em vermelho, exige observação.
--   Falta o KM? Registra e marca como pendente.
--   Recusar o registro não impede a entrada — só apaga a prova dela. Nenhum
--   endpoint deste módulo retorna 4xx por causa da SITUAÇÃO do veículo.
--
-- REGRA DE FRONTEIRA (mesmo padrão da migration 012 — Coordenadoria)
--   Portaria é módulo SEPARADO do Pátio. Não tem FK para nenhuma tabela
--   operacional do Pátio (onibus, linha, fila, alocacao_patio, alerta,
--   escala). A única FK para fora do schema portaria é public.funcionario —
--   identidade, compartilhada por toda a Suite.
--
-- DECISÕES DE DESENHO QUE MOLDAM ESTE SCHEMA (ver DESENHO-modulo-portaria.md)
--   D1  Uma tabela veiculo com discriminador `propriedade`, não três tabelas.
--   D2  O condutor mora no MOVIMENTO, nunca no cadastro do veículo.
--   D3  "Dentro agora" = último movimento por placa, não par entrada/saída.
--   D4  placa/RE/nome são SNAPSHOT (VARCHAR) no movimento, além do FK.
--   D5  Terceiro é uma EMPRESA (empresa_terceira) com N veículos e N
--       condutores; o condutor de cada visita é texto no movimento.
--   D6  O controlador CADASTRA (nasce PENDENTE); só encarregado/gerência
--       AUTORIZAM. Três recursos de RBAC — nunca dois — para essa separação
--       não virar combinado verbal.
--   D7  exige_hodometro é coluna do veículo (default TRUE em EMPRESA), não
--       regra chumbada no código.
--   D8  VARCHAR + CHECK em todo enum — nunca ENUM nativo do Postgres
--       (ALTER TYPE ADD VALUE exige COMMIT separado; mesma armadilha que a
--       migration 022 já documentou).
--   D9  data_referencia = CURRENT_DATE (calendário, 24h). NÃO usa
--       get_data_servico() do Pátio — a regra das 20h é do Pátio, não da
--       Portaria. Divergência proposital, registrada aqui para ninguém
--       "corrigir" depois achando que foi esquecimento.
--   D10 Placa normalizada (maiúscula, sem hífen/espaço) — responsabilidade
--       do BACKEND, ao gravar e ao buscar, não desta migration.
--   D11 Veículo suspenso/baixado pode ser registrado, com observação
--       obrigatória — ver REGRA NÚMERO UM.
--   D12 Autorização NÃO VENCE — sem coluna `validade`. Quem encerra é
--       pessoa (ação "bloquear por RE"), não relógio.
--   D14 Histórico de mudança de situação em tabela própria, escrito pelo
--       BACKEND (nunca trigger) — para registrar a intenção de quem clicou,
--       não o efeito colateral de um UPDATE qualquer.
--
-- ⚠️ DADO PESSOAL
--   placa + RE + horário revela rotina de vida. Nunca dado pessoal real em
--   seed, fixture, log ou comentário desta migration.
--
-- NATUREZA: 100% ADITIVA E IDEMPOTENTE.
--   CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS, INSERT ON CONFLICT DO NOTHING.
--   Nenhum ALTER TABLE, DROP ou RENAME em tabela existente. Nenhuma view
--   existente é tocada — vw_acesso_efetivo, vw_modulos_usuario e
--   vw_destino_login são genéricas, dirigidas por dados; o recurso novo
--   aparece nelas sozinho assim que existir linha em funcao_permissao.
--   ⚠️ EXCEÇÃO CONTROLADA: um único UPDATE em public.funcao, restrito à
--   linha 'CONTROLADOR_ACESSO' que esta própria migration acabou de criar
--   (funcao.modulo_padrao, coluna da migration 013). Nenhuma outra função
--   é tocada.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table funcionario", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 024-modulo-portaria.sql
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS portaria;
COMMENT ON SCHEMA portaria IS 'Módulo Portaria — controle de acesso veicular. Isolado do public (Pátio) e do coordenadoria. Sem FK para tabela operacional do Pátio.';

SET search_path TO portaria, public;

-- ============================================================================
-- 1 · LOCAL — os pontos de acesso
-- ============================================================================
CREATE TABLE IF NOT EXISTS portaria.local (
    codigo     VARCHAR(20) PRIMARY KEY,
    nome       VARCHAR(60) NOT NULL,
    descricao  TEXT,
    ordem      SMALLINT NOT NULL DEFAULT 1,
    ativo      BOOLEAN  NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE portaria.local IS 'Pontos de acesso. Fase 1 só usa LEVES; PESADOS nasce desligada (ativo=FALSE) de propósito, para a portaria de pesados entrar depois com um UPDATE, sem migration nova.';

INSERT INTO portaria.local (codigo, nome, descricao, ordem, ativo) VALUES
    ('LEVES',   'Portaria de veículos leves',  'Entrada e saída de carros, motos e veículos leves em geral.', 1, TRUE),
    ('PESADOS', 'Portaria de veículos pesados', 'Fora do escopo da Fase 1 — nasce desligada.',                  2, FALSE)
ON CONFLICT (codigo) DO NOTHING;

-- ============================================================================
-- 2 · EMPRESA_TERCEIRA — o prestador / fornecedor (D5)
-- ============================================================================
CREATE TABLE IF NOT EXISTS portaria.empresa_terceira (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nome        VARCHAR(120) NOT NULL,
    cnpj        VARCHAR(18),
    observacao  TEXT,
    ativo       BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    criado_por  UUID
);
COMMENT ON TABLE portaria.empresa_terceira IS 'Prestador/fornecedor recorrente (D5). O mesmo prestador vem com carros diferentes, e o mesmo carro vem com pessoas diferentes — por isso a empresa se cadastra uma vez e os veículos penduram nela; o condutor de cada visita nunca se cadastra, vai em movimento.terceiro_nome.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_empresa_terceira_nome
    ON portaria.empresa_terceira (upper(nome)) WHERE ativo;

-- ============================================================================
-- 3 · VEICULO — o cadastro (D1, D6, D7, D12)
-- ============================================================================
CREATE TABLE IF NOT EXISTS portaria.veiculo (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    propriedade          VARCHAR(12) NOT NULL
                         CHECK (propriedade IN ('PARTICULAR','EMPRESA','TERCEIRO')),
    funcionario_id       UUID REFERENCES public.funcionario(id),          -- dono; só PARTICULAR
    empresa_terceira_id  UUID REFERENCES portaria.empresa_terceira(id),   -- só TERCEIRO

    placa                VARCHAR(8)  NOT NULL,
    tipo                 VARCHAR(12) NOT NULL DEFAULT 'CARRO'
                         CHECK (tipo IN ('CARRO','MOTO','OUTRO')),
    marca_modelo         VARCHAR(60),
    cor                  VARCHAR(30),

    exige_hodometro      BOOLEAN NOT NULL DEFAULT FALSE,

    situacao             VARCHAR(12) NOT NULL DEFAULT 'PENDENTE'
                         CHECK (situacao IN ('PENDENTE','AUTORIZADO','SUSPENSO','BAIXADO')),
    situacao_por         UUID REFERENCES public.funcionario(id),
    situacao_em          TIMESTAMPTZ,
    situacao_motivo      TEXT,

    observacao           TEXT,
    ativo                BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    criado_por           UUID,
    atualizado_em        TIMESTAMPTZ,
    atualizado_por       UUID,

    CONSTRAINT ck_veiculo_dono CHECK (
        (propriedade = 'PARTICULAR' AND funcionario_id      IS NOT NULL) OR
        (propriedade = 'EMPRESA'    AND funcionario_id      IS NULL)     OR
        (propriedade = 'TERCEIRO'   AND empresa_terceira_id IS NOT NULL)
    )
);
COMMENT ON TABLE portaria.veiculo IS 'Cadastro de veículo (D1: um discriminador `propriedade`, não três tabelas). Nasce PENDENTE — quem cadastra (controlador) não é quem autoriza (encarregado/gerência); ver D6 e a permissão de CONTROLADOR_ACESSO em funcao_permissao mais abaixo.';
COMMENT ON COLUMN portaria.veiculo.funcionario_id IS 'Dono do veículo. Só preenchido em PARTICULAR. NUNCA é "quem está dirigindo agora" — isso mora em movimento.funcionario_id (D2), porque carro de empresa sai com um e volta com outro.';
COMMENT ON COLUMN portaria.veiculo.exige_hodometro IS 'D7: coluna, não regra chumbada. Default FALSE aqui; o backend decide o default TRUE para propriedade=EMPRESA na criação, mas o campo continua editável — carro da empresa sem KM controlado, ou particular que a gerência quer acompanhar, é um UPDATE, não um deploy.';
COMMENT ON COLUMN portaria.veiculo.situacao IS 'PENDENTE=cadastrado, aguardando autorização. AUTORIZADO=liberado. SUSPENSO=proibido (temporário ou não), com motivo. BAIXADO=cadastro cancelado (vendeu o carro, saiu da empresa). Mudança de situação sempre grava linha em veiculo_situacao_hist — ver D14.';
COMMENT ON COLUMN portaria.veiculo.situacao_motivo IS 'Obrigatório em SUSPENSO e BAIXADO — validado pelo backend, não por CHECK aqui (a regra depende da combinação com `situacao`, e fica mais legível na camada que já lê o payload inteiro).';
COMMENT ON COLUMN portaria.veiculo.placa IS 'Normalizada pelo backend (maiúscula, sem hífen/espaço) ao gravar E ao buscar — D10. Esta coluna não valida formato: placa de outro país ou provisória existe, e a regra número um (nunca impedir um registro) vale para o cadastro também.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_portaria_veiculo_placa
    ON portaria.veiculo (placa) WHERE ativo;

-- ⛔ Sem campo `validade` — D12: a autorização não vence. Quem encerra é uma
-- pessoa (ação "bloquear por RE" no backend), nunca um relógio. Coluna que
-- nasce e vive sempre nula é convite para alguém preencher torto depois.

-- ============================================================================
-- 4 · VEICULO_SITUACAO_HIST — histórico das decisões (D14)
-- ============================================================================
CREATE TABLE IF NOT EXISTS portaria.veiculo_situacao_hist (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    veiculo_id     UUID NOT NULL REFERENCES portaria.veiculo(id) ON DELETE CASCADE,
    situacao_de    VARCHAR(12),
    situacao_para  VARCHAR(12) NOT NULL,
    motivo         TEXT,
    decidido_por   UUID NOT NULL REFERENCES public.funcionario(id),
    decidido_em    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE portaria.veiculo_situacao_hist IS 'Extrato de toda mudança de situação (D14). Suspender o carro de alguém é a decisão mais contestável do módulo — "quem decidiu, quando, por quê" chega meses depois. Escrita é responsabilidade do BACKEND a cada troca de situação, nunca de trigger: o histórico registra a intenção de quem clicou, não o efeito colateral de um UPDATE qualquer.';

CREATE INDEX IF NOT EXISTS idx_veiculo_situacao_hist
    ON portaria.veiculo_situacao_hist (veiculo_id, decidido_em DESC);

-- ============================================================================
-- 5 · MOVIMENTO — o evento (D2, D3, D4, D9)
-- ============================================================================
CREATE TABLE IF NOT EXISTS portaria.movimento (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    local_codigo          VARCHAR(20) NOT NULL DEFAULT 'LEVES'
                          REFERENCES portaria.local(codigo),
    sentido               VARCHAR(8)  NOT NULL CHECK (sentido IN ('ENTRADA','SAIDA')),
    momento               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data_referencia       DATE        NOT NULL DEFAULT CURRENT_DATE,

    veiculo_id            UUID REFERENCES portaria.veiculo(id),
    funcionario_id        UUID REFERENCES public.funcionario(id),

    placa_registrada      VARCHAR(8)   NOT NULL,
    re_registrado         VARCHAR(20),
    nome_registrado       VARCHAR(120),

    terceiro_nome         VARCHAR(120),
    terceiro_destino      VARCHAR(120),
    terceiro_empresa      VARCHAR(120),

    hodometro_km          INTEGER CHECK (hodometro_km >= 0),

    cadastrado            BOOLEAN NOT NULL DEFAULT TRUE,
    origem                VARCHAR(12) NOT NULL DEFAULT 'MANUAL'
                          CHECK (origem IN ('MANUAL','RETROATIVO','QR','TAG','LPR')),
    movimento_entrada_id  UUID REFERENCES portaria.movimento(id),

    registrado_por        UUID NOT NULL REFERENCES public.funcionario(id),
    observacao            TEXT,
    criado_em             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE portaria.movimento IS 'Registro de entrada/saída — a tabela que importa. "Dentro agora" (D3) é derivado do ÚLTIMO movimento por placa via vw_dentro, nunca de um par entrada/saída: o portão é manual, saída sem registro acontece, e um par obrigatório acumula linha eternamente aberta. movimento_entrada_id fica opcional, só como conveniência de UI quando a saída nasce de um toque na lista "dentro agora".';
COMMENT ON COLUMN portaria.movimento.placa_registrada IS 'Snapshot (D4), não FK — mesmo padrão de coordenadoria.ocorrencia (motorista_re é VARCHAR). Guarda o que foi registrado NAQUELE momento: funcionário troca de carro, veículo é vendido, pessoa sai da empresa, e o histórico continua contando a verdade de quando aconteceu. Sempre preenchido, mesmo quando há veiculo_id.';
COMMENT ON COLUMN portaria.movimento.re_registrado IS 'Snapshot (D4) — ver comentário de placa_registrada.';
COMMENT ON COLUMN portaria.movimento.nome_registrado IS 'Snapshot (D4) — ver comentário de placa_registrada.';
COMMENT ON COLUMN portaria.movimento.funcionario_id IS 'CONDUTOR desta passagem específica (D2) — nunca o dono do veículo. Carro da empresa sai com um funcionário e volta com outro; se o condutor morasse no cadastro do veículo, cada saída exigiria editar o cadastro ou mentir no histórico.';
COMMENT ON COLUMN portaria.movimento.terceiro_nome IS 'Quem dirigia, texto puro, nunca FK (D5) — o condutor de um veículo de terceiro muda a cada visita e não se cadastra, só a empresa e o veículo se cadastram.';
COMMENT ON COLUMN portaria.movimento.data_referencia IS 'D9: CURRENT_DATE puro — dia de calendário, 24h. NÃO usa get_data_servico() do Pátio: a regra das 20h existe por causa da escala do Pátio, e não se aplica aqui. Um carro que entra 23h e sai 6h aparece em dois dias diferentes no relatório, e isso está certo — foram duas passagens. Divergência proposital, não esquecimento.';
COMMENT ON COLUMN portaria.movimento.cadastrado IS 'FALSE = passagem de veículo sem cadastro (visitante avulso, entrega única). A regra número um do módulo: o sistema nunca deixa de registrar por falta de cadastro.';
COMMENT ON COLUMN portaria.movimento.origem IS 'MANUAL = digitado na hora pelo controlador. RETROATIVO = lançado depois a partir do papel, na contingência de queda de internet — exige observação no backend. QR/TAG/LPR reservados para quando a identificação vier de leitura automática (Bloco E usa QR); QR/TAG/LPR são só ACELERAÇÃO da digitação, nunca pré-requisito para o registro acontecer.';
COMMENT ON COLUMN portaria.movimento.movimento_entrada_id IS 'Opcional (D3) — vínculo de conveniência quando a saída nasce de um toque na lista "dentro agora", usado para calcular permanência. A ausência dele nunca impede a saída de ser registrada.';

CREATE INDEX IF NOT EXISTS idx_mov_placa_momento ON portaria.movimento (placa_registrada, momento DESC);
CREATE INDEX IF NOT EXISTS idx_mov_veiculo       ON portaria.movimento (veiculo_id, momento DESC);
CREATE INDEX IF NOT EXISTS idx_mov_data          ON portaria.movimento (data_referencia);
CREATE INDEX IF NOT EXISTS idx_mov_sem_cadastro  ON portaria.movimento (data_referencia)
    WHERE NOT cadastrado;

-- ============================================================================
-- 6 · VIEW — quem está dentro agora (D3)
-- ============================================================================
CREATE OR REPLACE VIEW portaria.vw_dentro AS
SELECT * FROM (
    SELECT DISTINCT ON (m.placa_registrada) m.*
      FROM portaria.movimento m
     ORDER BY m.placa_registrada, m.momento DESC
) ult
WHERE ult.sentido = 'ENTRADA';

COMMENT ON VIEW portaria.vw_dentro IS 'Estado derivado do último movimento por placa (D3), não de par entrada/saída — se a saída de ontem não foi registrada, a entrada de hoje simplesmente vira o último movimento e o veículo volta a constar dentro, sem lixo nem linha órfã.';

-- ============================================================================
-- 7 · RBAC — módulo, recursos, função e permissões
-- ----------------------------------------------------------------------------
-- Nada de view nova: vw_acesso_efetivo, vw_modulos_usuario e vw_destino_login
-- (migrations 011/013) são genéricas, dirigidas por dados. Todo INSERT aqui
-- é linha NOVA de catálogo compartilhado, sempre ON CONFLICT ... DO NOTHING
-- — nunca DO UPDATE, que em catálogo compartilhado pode sobrescrever
-- permissão de outro módulo.
-- ============================================================================
INSERT INTO public.modulo (codigo, nome, descricao, ordem) VALUES
    ('PORTARIA', 'Portaria', 'Controle de acesso de veículos à garagem.', 5)
ON CONFLICT (codigo) DO NOTHING;

-- 🔄 TRÊS recursos, não dois — a separação "cadastrar × autorizar" da D6
--    só existe de verdade se forem recursos diferentes no RBAC.
INSERT INTO public.recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
    ('acesso_veicular',      'Acesso Veicular', 'Registro de entrada e saída de veículos.',          'PORTARIA', 11),
    ('veiculo_portaria',     'Veículos',        'Cadastro de veículos e empresas prestadoras.',      'PORTARIA', 12),
    ('autorizacao_veicular', 'Autorização',     'Liberar, suspender ou baixar veículo cadastrado.',  'PORTARIA', 13)
ON CONFLICT (codigo) DO NOTHING;

-- categoria e nivel conferidos no CHECK real da 011 (linha 110):
--   categoria IN ('GESTAO','OPERACAO','MANUTENCAO','PATIO','SISTEMA')
--   nivel 6 = mesma faixa de FISCAL e PLANTONISTA
INSERT INTO public.funcao (codigo, nome, categoria, nivel, descricao) VALUES
    ('CONTROLADOR_ACESSO', 'Controlador de Acesso', 'OPERACAO', 6,
     'Registra entrada e saída de veículos na portaria.')
ON CONFLICT (codigo) DO NOTHING;

-- ⚠️ EXCEÇÃO CONTROLADA (única UPDATE fora de portaria nesta migration):
-- só a linha que acabamos de criar acima, nenhuma outra função é tocada.
UPDATE public.funcao SET modulo_padrao = 'PORTARIA'
 WHERE codigo = 'CONTROLADOR_ACESSO' AND modulo_padrao IS DISTINCT FROM 'PORTARIA';

-- Pacote de permissões — 🔴 a linha do CONTROLADOR_ACESSO é o coração da D6:
-- escreve no cadastro (veiculo_portaria) mas só LÊ a autorização
-- (autorizacao_veicular) — não consegue se autoautorizar.
INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, v.recurso, v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('CONTROLADOR_ACESSO',   'acesso_veicular',      TRUE,  TRUE),
        ('CONTROLADOR_ACESSO',   'veiculo_portaria',     TRUE,  TRUE),
        ('CONTROLADOR_ACESSO',   'autorizacao_veicular', TRUE,  FALSE),

        ('ENCARREGADO',          'acesso_veicular',      TRUE,  FALSE),
        ('ENCARREGADO',          'veiculo_portaria',      TRUE,  TRUE),
        ('ENCARREGADO',          'autorizacao_veicular',  TRUE,  TRUE),

        ('GERENTE_OPERACIONAL',  'acesso_veicular',      TRUE,  FALSE),
        ('GERENTE_OPERACIONAL',  'veiculo_portaria',      TRUE,  TRUE),
        ('GERENTE_OPERACIONAL',  'autorizacao_veicular',  TRUE,  TRUE),

        ('GERENTE_GERAL',        'acesso_veicular',      TRUE,  FALSE),
        ('GERENTE_GERAL',        'veiculo_portaria',      TRUE,  TRUE),
        ('GERENTE_GERAL',        'autorizacao_veicular',  TRUE,  TRUE),

        ('COORDENADOR_TRAFEGO',  'acesso_veicular',      TRUE,  FALSE),
        ('COORDENADOR_TRAFEGO',  'veiculo_portaria',      TRUE,  FALSE),
        ('COORDENADOR_TRAFEGO',  'autorizacao_veicular',  TRUE,  FALSE),

        ('ADMIN',                'acesso_veicular',      TRUE,  TRUE),
        ('ADMIN',                'veiculo_portaria',      TRUE,  TRUE),
        ('ADMIN',                'autorizacao_veicular',  TRUE,  TRUE)
       ) AS v(funcao_codigo, recurso, pode_ler, pode_escrever) ON v.funcao_codigo = fn.codigo
 ON CONFLICT ON CONSTRAINT uq_funcao_permissao DO NOTHING;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'portaria' ORDER BY table_name;  -- esperado: 5 tabelas
--   SELECT count(*) FROM portaria.local;              -- esperado: 2 (LEVES ativo, PESADOS inativo)
--
-- Prova da fronteira — nenhuma FK deste schema aponta para tabela operacional
-- do Pátio (esperado: 0 linhas):
--   SELECT tc.table_name, ccu.table_schema || '.' || ccu.table_name AS aponta_para
--     FROM information_schema.table_constraints tc
--     JOIN information_schema.constraint_column_usage ccu
--       ON ccu.constraint_name = tc.constraint_name
--    WHERE tc.table_schema = 'portaria'
--      AND tc.constraint_type = 'FOREIGN KEY'
--      AND ccu.table_name IN ('onibus','linha','fila','alocacao_patio','alerta','escala','motorista');
--
-- Prova da D6 — CONTROLADOR_ACESSO escreve cadastro, só lê autorização:
--   SELECT fp.recurso, fp.pode_ler, fp.pode_escrever
--     FROM public.funcao_permissao fp
--     JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fn.codigo = 'CONTROLADOR_ACESSO' ORDER BY fp.recurso;
--   -- esperado: acesso_veicular (T,T), autorizacao_veicular (T,F), veiculo_portaria (T,T)
--
-- Controlador cai direto na Portaria ao logar:
--   SELECT modulo_padrao FROM public.funcao WHERE codigo = 'CONTROLADOR_ACESSO';  -- esperado: PORTARIA
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DELETE FROM public.funcao_permissao WHERE recurso IN ('acesso_veicular','veiculo_portaria','autorizacao_veicular');
-- UPDATE public.funcao SET modulo_padrao = NULL WHERE codigo = 'CONTROLADOR_ACESSO';
-- DELETE FROM public.funcao WHERE codigo = 'CONTROLADOR_ACESSO';
-- DELETE FROM public.recurso WHERE codigo IN ('acesso_veicular','veiculo_portaria','autorizacao_veicular');
-- DELETE FROM public.modulo WHERE codigo = 'PORTARIA';
-- DROP SCHEMA IF EXISTS portaria CASCADE;
-- (Seguro: nada fora do schema depende dele.)
-- ============================================================================
