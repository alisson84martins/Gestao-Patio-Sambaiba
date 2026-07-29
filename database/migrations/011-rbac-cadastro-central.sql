-- ============================================================================
-- MIGRATION 011 — Cadastro central de funcionários + RBAC
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: public
-- DATA:   2026-07-28
-- AUTOR:  Alisson Martins
-- ----------------------------------------------------------------------------
-- OBJETIVO
--   1. Trazer para o versionamento as tabelas do cadastro central que foram
--      aplicadas manualmente em produção em 27/07/2026 (PARTE A). Em produção
--      esta parte é NO-OP; em banco novo, cria tudo do zero.
--   2. Corrigir a tabela `permissao`, que hoje só aponta para a tabela antiga
--      `usuario` e cairia junto com ela na aposentadoria do modelo velho.
--   3. Criar a view de acesso efetivo e o catálogo de recursos.
--
-- NATUREZA: 100% ADITIVA E IDEMPOTENTE.
--   Nenhum DROP, nenhum ALTER destrutivo. O sistema em produção continua lendo
--   `usuario` e `usuario.perfil` normalmente depois desta migration.
--   A aposentadoria do modelo antigo é a migration 012, executada só depois de
--   dias de operação real com o modelo novo validado.
--
-- COMO RODAR (produção, sempre com o dono correto):
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 011-rbac-cadastro-central.sql
--
-- ENSAIAR ANTES na cópia `gestao_frota_teste`.
-- ROLLBACK: 99-rollback.sql (remove só as tabelas novas) + seção no fim deste arquivo.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ============================================================================
-- PARTE 0 — Padronização do dono das tabelas
-- ----------------------------------------------------------------------------
-- POR QUE ISTO EXISTE (descoberto no ensaio de 29/07/2026)
--   O schema `public` tinha DOIS donos: as 15 tabelas do núcleo do Pátio
--   (migrations 001–010) pertenciam ao `postgres`, e as 7 tabelas criadas na
--   migração de 27/07 pertenciam ao `sambaiba`.
--
--   Consequência: rodar este script com `SET ROLE sambaiba` fazia a Parte B
--   falhar com `must be owner of table permissao` — e, em cascata, a coluna
--   `funcionario_id`, o backfill e as duas views não eram criados. O script
--   terminava "sem erro fatal", mas pela metade. Silencioso e perigoso.
--
--   Este bloco unifica o dono em `sambaiba` antes de qualquer ALTER. É mudança
--   de metadado: instantânea, não move dado, não interrompe quem está usando.
--
-- OBS.: precisa de superusuário. O RESET ROLE abaixo volta ao papel original
--       da sessão (postgres) e o SET ROLE no fim devolve para `sambaiba`.
-- ============================================================================
RESET ROLE;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables
              WHERE schemaname = 'public' AND tableowner <> 'sambaiba'
    LOOP EXECUTE format('ALTER TABLE public.%I OWNER TO sambaiba', r.tablename); END LOOP;

    FOR r IN SELECT viewname FROM pg_views
              WHERE schemaname = 'public' AND viewowner <> 'sambaiba'
    LOOP EXECUTE format('ALTER VIEW public.%I OWNER TO sambaiba', r.viewname); END LOOP;

    FOR r IN SELECT sequencename FROM pg_sequences
              WHERE schemaname = 'public' AND sequenceowner <> 'sambaiba'
    LOOP EXECUTE format('ALTER SEQUENCE public.%I OWNER TO sambaiba', r.sequencename); END LOOP;

    RAISE NOTICE 'Dono das tabelas do schema public padronizado em sambaiba.';
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'Sem privilégio para trocar o dono — rode como superusuário se a Parte B falhar.';
END $$;

SET ROLE sambaiba;

-- ============================================================================
-- PARTE A — Tabelas do cadastro central
-- (já aplicadas em produção em 27/07/2026; aqui só para versionar e permitir
--  instalação do zero. Em produção todos os comandos abaixo são no-op.)
-- ============================================================================

-- A.1 · FUNCIONARIO — cadastro central: toda pessoa da empresa entra aqui
CREATE TABLE IF NOT EXISTS funcionario (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    re              VARCHAR(20)   NOT NULL UNIQUE,
    nome            VARCHAR(120)  NOT NULL,
    cpf             VARCHAR(14)   UNIQUE
                    CHECK (cpf IS NULL OR cpf ~ '^[0-9]{11}$'),
    telefone        VARCHAR(20),
    status          VARCHAR(15)   NOT NULL DEFAULT 'ATIVO'
                    CHECK (status IN ('ATIVO','AFASTADO','FERIAS','DESLIGADO')),
    data_admissao   DATE,
    codigo_externo  VARCHAR(50),
    criado_em       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    criado_por      UUID,
    atualizado_em   TIMESTAMPTZ,
    atualizado_por  UUID
);
COMMENT ON TABLE  funcionario     IS 'Cadastro central de toda pessoa da empresa (dirija ela o sistema ou não).';
COMMENT ON COLUMN funcionario.re  IS 'Registro do Empregado — ÚNICO. Nunca dois cadastros com o mesmo RE.';
COMMENT ON COLUMN funcionario.cpf IS 'CPF só com dígitos (11), ÚNICO. Correção de erro = editar o registro existente, nunca criar outro.';

-- A.2 · FUNCAO — catálogo de funções (tabela, não enum: cresce com INSERT)
CREATE TABLE IF NOT EXISTS funcao (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          VARCHAR(30)   NOT NULL UNIQUE,
    nome            VARCHAR(60)   NOT NULL,
    categoria       VARCHAR(20)   NOT NULL
                    CHECK (categoria IN ('GESTAO','OPERACAO','MANUTENCAO','PATIO','SISTEMA')),
    nivel           SMALLINT      NOT NULL DEFAULT 5,
    ativo           BOOLEAN       NOT NULL DEFAULT TRUE,
    descricao       TEXT,
    criado_em       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
COMMENT ON COLUMN funcao.codigo IS 'Chave estável usada nas regras de acesso; NÃO mudar depois de criada.';
COMMENT ON COLUMN funcao.nivel  IS 'Hierarquia relativa (1 = mais alto). A função principal da pessoa é a de menor nível.';

-- A.3 · FUNCIONARIO_FUNCAO — N:N (uma pessoa pode ter VÁRIAS funções)
CREATE TABLE IF NOT EXISTS funcionario_funcao (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    funcionario_id  UUID          NOT NULL REFERENCES funcionario(id) ON DELETE CASCADE,
    funcao_id       UUID          NOT NULL REFERENCES funcao(id)      ON DELETE RESTRICT,
    principal       BOOLEAN       NOT NULL DEFAULT FALSE,
    data_inicio     DATE          NOT NULL DEFAULT CURRENT_DATE,
    data_fim        DATE,
    ativo           BOOLEAN       NOT NULL DEFAULT TRUE,
    criado_em       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    criado_por      UUID,
    CONSTRAINT uq_funcionario_funcao UNIQUE (funcionario_id, funcao_id)
);
COMMENT ON TABLE funcionario_funcao IS 'Liga pessoa ↔ função. É aqui que "operador de pátio que também é motorista" acontece.';

CREATE UNIQUE INDEX IF NOT EXISTS idx_funcionario_funcao_principal
    ON funcionario_funcao (funcionario_id)
    WHERE principal = TRUE AND ativo = TRUE;

-- A.4 · USUARIO_LOGIN — conta de acesso (1:1 opcional com funcionario)
--   Login = funcionario.re | Senha base = 4 últimos dígitos do CPF (não trocável)
--   Cargos altos (nível <= 4) = política FORTE, definida por admin.
CREATE TABLE IF NOT EXISTS usuario_login (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    funcionario_id  UUID          NOT NULL UNIQUE REFERENCES funcionario(id) ON DELETE CASCADE,
    senha_hash      VARCHAR(255)  NOT NULL,
    politica_senha  VARCHAR(10)   NOT NULL DEFAULT 'CPF'
                    CHECK (politica_senha IN ('CPF','FORTE')),
    ativo           BOOLEAN       NOT NULL DEFAULT TRUE,
    ultimo_acesso   TIMESTAMPTZ,
    criado_em       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    criado_por      UUID,
    atualizado_em   TIMESTAMPTZ,
    atualizado_por  UUID
);
COMMENT ON TABLE usuario_login IS 'Conta de acesso. Login = RE; senha base = 4 últimos do CPF; cargos altos usam senha FORTE.';

-- A.5 · FUNCAO_PERMISSAO — pacote PADRÃO de acesso por função
CREATE TABLE IF NOT EXISTS funcao_permissao (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    funcao_id       UUID          NOT NULL REFERENCES funcao(id) ON DELETE CASCADE,
    recurso         VARCHAR(50)   NOT NULL,
    pode_ler        BOOLEAN       NOT NULL DEFAULT FALSE,
    pode_escrever   BOOLEAN       NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_funcao_permissao UNIQUE (funcao_id, recurso)
);
COMMENT ON TABLE funcao_permissao IS 'Acesso PADRÃO por função × recurso. Exceções por pessoa vão na tabela `permissao`.';

-- A.6 · Índices de apoio
CREATE INDEX IF NOT EXISTS idx_funcionario_status      ON funcionario (status);
CREATE INDEX IF NOT EXISTS idx_funcionario_funcao_func ON funcionario_funcao (funcionario_id);
CREATE INDEX IF NOT EXISTS idx_funcionario_funcao_fun  ON funcionario_funcao (funcao_id);
CREATE INDEX IF NOT EXISTS idx_funcao_permissao_funcao ON funcao_permissao (funcao_id);

-- A.7 · Catálogo de funções (12)
INSERT INTO funcao (codigo, nome, categoria, nivel, descricao) VALUES
    ('GERENTE_GERAL',       'Gerente Geral',          'GESTAO',     1, 'Direção geral da operação.'),
    ('GERENTE_OPERACIONAL', 'Gerente Operacional',    'GESTAO',     2, 'Gestão da operação de transporte.'),
    ('ENCARREGADO',         'Encarregado',            'GESTAO',     3, 'Supervisão de equipes de operação.'),
    ('COORDENADOR_TRAFEGO', 'Coordenador de Tráfego', 'GESTAO',     4, 'Coordena linhas, ocorrências e saída da frota.'),
    ('FISCAL',              'Fiscal',                 'OPERACAO',   6, 'Fiscalização de partidas e cumprimento de viagens.'),
    ('PLANTONISTA',         'Plantonista',            'OPERACAO',   6, 'Plantão operacional.'),
    ('OPERADOR_PATIO',      'Operador de Pátio',      'PATIO',      6, 'Organiza filas e alocação no pátio.'),
    ('MOTORISTA',           'Motorista',              'OPERACAO',   8, 'Condução de veículos da frota.'),
    ('COBRADOR',            'Cobrador',               'OPERACAO',   8, 'Cobrança e apoio a bordo.'),
    ('APONTADOR',           'Apontador',              'OPERACAO',   7, 'Acompanha e registra a posição dos carros no pátio; acesso de consulta.'),
    ('MECANICO',            'Mecânico',               'MANUTENCAO', 7, 'Manutenção corretiva/preventiva da frota.'),
    ('ADMIN',               'Administrador',          'SISTEMA',    1, 'Administração do sistema (perfil técnico).')
ON CONFLICT (codigo) DO NOTHING;


-- ============================================================================
-- PARTE B — Correções (o que esta migration traz de novo)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- B.1 · Catálogo de MÓDULOS e RECURSOS (duas camadas)
--
--   MÓDULO  = o aplicativo que a pessoa escolhe ao entrar (Pátio, Fiscalização,
--             Coordenadoria, Administração). É o que a tela de seleção mostra.
--   RECURSO = a funcionalidade dentro do módulo, sobre a qual se concede
--             ler/escrever. Vários recursos por módulo.
--
--   Por que duas camadas: sem o módulo, a tela de escolha pós-login não tem
--   como agrupar — `alocacao`, `escala`, `alerta`, `manutencao` e `frota` são
--   todos Pátio, mas o banco não sabia disso.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modulo (
    codigo      VARCHAR(20)  PRIMARY KEY,
    nome        VARCHAR(40)  NOT NULL,
    descricao   TEXT,
    ordem       SMALLINT     NOT NULL DEFAULT 99,
    ativo       BOOLEAN      NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE modulo IS 'Aplicativos da Suite Coordenadoria. A pessoa escolhe um ao entrar; a lista sai do acesso efetivo dela.';

INSERT INTO modulo (codigo, nome, descricao, ordem) VALUES
    ('PATIO',         'Pátio',          'Filas, alocação, escala, alertas e manutenção da frota.',        1),
    ('COORDENADORIA', 'Coordenadoria',  'Ocorrências internas, panorama de linha e indicadores.',         2),
    ('FISCALIZACAO',  'Fiscalização',   'Turnos do fiscal, registro de partidas e eventos em campo.',     3),
    ('ADMINISTRACAO', 'Administração',  'Cadastro de pessoas, funções e permissões.',                     4)
ON CONFLICT (codigo) DO NOTHING;

CREATE TABLE IF NOT EXISTS recurso (
    codigo        VARCHAR(50)  PRIMARY KEY,
    nome          VARCHAR(60)  NOT NULL,
    descricao     TEXT,
    modulo_codigo VARCHAR(20)  REFERENCES modulo(codigo),
    ordem         SMALLINT     NOT NULL DEFAULT 99,
    ativo         BOOLEAN      NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE recurso IS 'Funcionalidades sobre as quais se concede acesso, agrupadas por módulo.';

-- Coluna adicionada em separado para o caso de a tabela já existir de execução anterior
ALTER TABLE recurso ADD COLUMN IF NOT EXISTS modulo_codigo VARCHAR(20) REFERENCES modulo(codigo);

INSERT INTO recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
    ('alocacao',     'Pátio / Alocação',     'Filas, chips, alocação e remanejamento de veículos.',   'PATIO',         1),
    ('escala',       'Escala',               'Escala diária e importação de planilha.',               'PATIO',         2),
    ('alerta',       'Alertas',              'Alertas PRESO (retido na rua) e AMOSTRAL (SPTrans).',   'PATIO',         3),
    ('manutencao',   'Manutenção',           'Fichas de defeito e serviços da frota.',                'PATIO',         4),
    ('frota',        'Frota',                'Cadastro de ônibus e linhas.',                          'PATIO',         5),
    ('ocorrencia',   'Ocorrências',          'Registro e tratativa de ocorrências operacionais.',     'COORDENADORIA', 6),
    ('coordenacao',  'Coordenação',          'Panorama de linha, previsão de recolhida e cobertura.', 'COORDENADORIA', 7),
    ('relatorios',   'Relatórios',           'IPP, ICV, Pareto de motivos e indicadores de qualidade.','COORDENADORIA', 8),
    ('fiscalizacao', 'Fiscalização',         'Turnos do fiscal, registro de partidas e eventos.',     'FISCALIZACAO',  9),
    ('usuarios',     'Cadastros e Usuários', 'Cadastro de pessoas, funções e permissões.',            'ADMINISTRACAO',10)
ON CONFLICT (codigo) DO UPDATE
   SET modulo_codigo = EXCLUDED.modulo_codigo,
       nome          = EXCLUDED.nome,
       descricao     = EXCLUDED.descricao,
       ordem         = EXCLUDED.ordem;

-- ----------------------------------------------------------------------------
-- B.2 · PERMISSAO passa a apontar para FUNCIONARIO
--   PROBLEMA: `permissao.usuario_id` referencia a tabela antiga `usuario`
--   (ON DELETE CASCADE). Quando `usuario` for aposentada na migration 012, todas
--   as exceções individuais de acesso seriam apagadas junto — o "liberar caso a
--   caso" sumiria sem aviso.
--   SOLUÇÃO: coluna nova apontando para `funcionario`, backfill pelo RE, e
--   `usuario_id` deixa de ser obrigatória. Nada é apagado; a coluna antiga fica
--   durante toda a transição.
-- ----------------------------------------------------------------------------
ALTER TABLE permissao
    ADD COLUMN IF NOT EXISTS funcionario_id UUID REFERENCES funcionario(id) ON DELETE CASCADE;

COMMENT ON COLUMN permissao.funcionario_id IS 'Exceção de acesso por PESSOA (sobrepõe o pacote padrão da função). Substitui usuario_id.';
COMMENT ON COLUMN permissao.usuario_id     IS 'DEPRECADO — mantido durante a transição. Usar funcionario_id.';

-- Backfill: casa a permissão antiga com a pessoa pelo RE
UPDATE permissao p
   SET funcionario_id = f.id
  FROM usuario u
  JOIN funcionario f ON f.re = u.re
 WHERE p.usuario_id = u.id
   AND p.funcionario_id IS NULL;

-- usuario_id deixa de ser obrigatória (permissões novas nascem só com funcionario_id)
ALTER TABLE permissao ALTER COLUMN usuario_id DROP NOT NULL;

-- Unicidade no novo eixo (pessoa × recurso)
CREATE UNIQUE INDEX IF NOT EXISTS uq_permissao_funcionario_recurso
    ON permissao (funcionario_id, recurso)
    WHERE funcionario_id IS NOT NULL;

-- Garante que toda linha tenha ao menos um dos dois vínculos
ALTER TABLE permissao DROP CONSTRAINT IF EXISTS ck_permissao_vinculo;
ALTER TABLE permissao ADD  CONSTRAINT ck_permissao_vinculo
    CHECK (usuario_id IS NOT NULL OR funcionario_id IS NOT NULL);

-- ----------------------------------------------------------------------------
-- B.3 · VIEW de acesso efetivo por FUNÇÃO
--   A regra "quem tem duas funções soma os acessos, e a mais permissiva vence"
--   mora aqui, no banco — não espalhada pelo código. O backend aplica por cima
--   apenas o override individual da tabela `permissao`.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_acesso_funcao AS
SELECT  f.id                      AS funcionario_id,
        fp.recurso                AS recurso,
        bool_or(fp.pode_ler)      AS pode_ler,
        bool_or(fp.pode_escrever) AS pode_escrever
  FROM  funcionario f
  JOIN  funcionario_funcao ff ON ff.funcionario_id = f.id
                             AND ff.ativo
                             AND (ff.data_fim IS NULL OR ff.data_fim >= CURRENT_DATE)
  JOIN  funcao fn             ON fn.id = ff.funcao_id AND fn.ativo
  JOIN  funcao_permissao fp   ON fp.funcao_id = fn.id
 GROUP BY f.id, fp.recurso;

COMMENT ON VIEW vw_acesso_funcao IS 'Acesso herdado das funções da pessoa (união; a mais permissiva vence). O override individual da tabela permissao é aplicado por cima, no backend.';

-- ----------------------------------------------------------------------------
-- B.4 · VIEW de acesso EFETIVO (função + override individual)
--   Esta é a que o backend consulta. Resolve as duas camadas de uma vez.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_acesso_efetivo AS
SELECT  COALESCE(a.funcionario_id, p.funcionario_id)      AS funcionario_id,
        COALESCE(a.recurso,        p.recurso)             AS recurso,
        COALESCE(p.pode_ler,       a.pode_ler,     FALSE) AS pode_ler,
        COALESCE(p.pode_escrever,  a.pode_escrever, FALSE) AS pode_escrever,
        (p.funcionario_id IS NOT NULL)                    AS e_excecao
  FROM  vw_acesso_funcao a
  FULL OUTER JOIN permissao p
    ON  p.funcionario_id = a.funcionario_id
   AND  p.recurso        = a.recurso
 WHERE  COALESCE(a.funcionario_id, p.funcionario_id) IS NOT NULL;

COMMENT ON VIEW vw_acesso_efetivo IS 'Acesso final da pessoa: pacote das funções + exceção individual por cima. e_excecao = TRUE indica ajuste manual.';

-- ----------------------------------------------------------------------------
-- B.4.1 · VIEW de MÓDULOS liberados — alimenta a tela de escolha pós-login
--   Uma pessoa "entra" num módulo se tiver leitura em pelo menos um recurso
--   dele. `pode_escrever` indica se ela opera ou só acompanha.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_modulos_usuario AS
SELECT  v.funcionario_id,
        m.codigo                   AS modulo,
        m.nome                     AS modulo_nome,
        m.descricao                AS modulo_descricao,
        m.ordem                    AS modulo_ordem,
        bool_or(v.pode_escrever)   AS pode_escrever,
        count(*) FILTER (WHERE v.pode_ler) AS recursos_liberados
  FROM  vw_acesso_efetivo v
  JOIN  recurso r ON r.codigo = v.recurso AND r.ativo
  JOIN  modulo  m ON m.codigo = r.modulo_codigo AND m.ativo
 WHERE  v.pode_ler
 GROUP BY v.funcionario_id, m.codigo, m.nome, m.descricao, m.ordem;

COMMENT ON VIEW vw_modulos_usuario IS 'Módulos que a pessoa pode abrir. É a fonte da tela de seleção de módulo após o login.';

-- ----------------------------------------------------------------------------
-- B.5 · Função de apoio — promove a função principal (a de menor nível)
--   Chamada depois de atribuir/remover função de alguém.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_ajustar_funcao_principal(p_funcionario_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE funcionario_funcao SET principal = FALSE
     WHERE funcionario_id = p_funcionario_id AND principal;

    UPDATE funcionario_funcao ff SET principal = TRUE
     WHERE ff.id = (
        SELECT ff2.id
          FROM funcionario_funcao ff2
          JOIN funcao fn ON fn.id = ff2.funcao_id
         WHERE ff2.funcionario_id = p_funcionario_id
           AND ff2.ativo
           AND (ff2.data_fim IS NULL OR ff2.data_fim >= CURRENT_DATE)
         ORDER BY fn.nivel ASC, ff2.data_inicio ASC
         LIMIT 1
     );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_ajustar_funcao_principal IS 'Define como principal a função de menor nível vigente da pessoa. Rodar após atribuir/encerrar vínculo.';


-- ============================================================================
-- CONFERÊNCIA — rodar depois da migration (esperado ao lado de cada consulta)
-- ============================================================================
-- Tabelas antigas intactas:
--   SELECT count(*) FROM usuario;     -- esperado: 21
--   SELECT count(*) FROM motorista;   -- esperado: 1
--
-- Cadastro central:
--   SELECT count(*) FROM funcionario; -- esperado: 19
--   SELECT count(*) FROM funcao;      -- esperado: 12
--   SELECT count(*) FROM modulo;      -- esperado: 4
--   SELECT count(*) FROM recurso;     -- esperado: 10
--
-- Módulos que a pessoa pode abrir — é a tela de escolha pós-login (troque o RE):
--   SELECT m.modulo_nome, m.pode_escrever, m.recursos_liberados
--     FROM vw_modulos_usuario m
--     JOIN funcionario f ON f.id = m.funcionario_id
--    WHERE f.re = '5598' ORDER BY m.modulo_ordem;
--
-- Multifunção (o teste que importa — deve trazer Damião, Daniel e você):
--   SELECT f.re, f.nome, string_agg(fn.codigo, ', ' ORDER BY fn.nivel) AS funcoes
--     FROM funcionario f
--     JOIN funcionario_funcao ff ON ff.funcionario_id = f.id AND ff.ativo
--     JOIN funcao fn ON fn.id = ff.funcao_id
--    GROUP BY f.re, f.nome HAVING count(*) > 1;
--
-- Acesso efetivo de uma pessoa (troque o RE):
--   SELECT r.nome, v.pode_ler, v.pode_escrever, v.e_excecao
--     FROM vw_acesso_efetivo v
--     JOIN funcionario f ON f.id = v.funcionario_id
--     JOIN recurso r ON r.codigo = v.recurso
--    WHERE f.re = '12728' ORDER BY r.ordem;
--
-- Permissões antigas migradas:
--   SELECT count(*) FILTER (WHERE funcionario_id IS NOT NULL) AS migradas,
--          count(*) FILTER (WHERE funcionario_id IS NULL)     AS orfas
--     FROM permissao;   -- orfas > 0 = permissão de usuário sem funcionario correspondente


-- ============================================================================
-- ROLLBACK desta migration (a PARTE B; para a PARTE A use 99-rollback.sql)
-- ============================================================================
-- DROP VIEW IF EXISTS vw_modulos_usuario;
-- DROP VIEW IF EXISTS vw_acesso_efetivo;
-- DROP VIEW IF EXISTS vw_acesso_funcao;
-- DROP FUNCTION IF EXISTS fn_ajustar_funcao_principal(UUID);
-- DROP INDEX IF EXISTS uq_permissao_funcionario_recurso;
-- ALTER TABLE permissao DROP CONSTRAINT IF EXISTS ck_permissao_vinculo;
-- ALTER TABLE permissao DROP COLUMN IF EXISTS funcionario_id;
-- ALTER TABLE permissao ALTER COLUMN usuario_id SET NOT NULL;  -- só se não houver linhas órfãs
-- DROP TABLE IF EXISTS recurso;
-- DROP TABLE IF EXISTS modulo;
-- ============================================================================
