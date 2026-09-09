-- ============================================================================
-- MIGRATION 041 — Portaria: frota de apoio, reservado e setor de destino
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria (existente — migration 024)
-- DATA:   2026-09-08
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (tabelas portaria.veiculo e
--             portaria.movimento, view portaria.vw_dentro)
-- ORIGEM: _handoff-claude/PROMPT-portaria-frota-e-empresa-2026-09-08.md
--         (R1, R1.b, R4, R5, R6, D18)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   Três frentes levantadas no uso real da tela pelo Alisson (P2, P3, P4 do
--   prompt de origem):
--
--   1. FROTA DE APOIO (P2/D18) — moto, van, guincho e caminhão da empresa
--      caem hoje em tipo='OUTRO' e ficam misturados com carro particular em
--      "Dentro agora". O tipo CHECK ganha 4 valores para a tela separar.
--
--   2. RESERVADO (P4/R1) — o ônibus que sai levando funcionário precisa ser
--      registrado na portaria (RE, KM, observação), mas ⛔ NÃO é cadastro de
--      veículo: é qualquer ônibus disponível naquele momento, e cadastrar
--      cada um espelharia a frota inteira dentro deste schema — exatamente o
--      que a regra de fronteira proíbe. Por isso o reservado é só uma
--      PASSAGEM: portaria.movimento ganha `prefixo` (texto, sem FK, mesmo
--      arranjo de recolhida_anormal.prefixo — migration 026) e `onibus_id`
--      (conveniência de leitura, sem FK). ⛔ NÃO existe tipo='ONIBUS' em
--      portaria.veiculo.
--
--      R1.b: reservado ⛔ NÃO grava placa — coletivo se identifica por
--      PREFIXO em todo o schema portaria (recolhida_anormal e avaria_saida
--      também não têm coluna de placa). Por isso `placa_registrada` deixa de
--      ser NOT NULL, com CHECK novo garantindo placa OU prefixo — nunca os
--      dois nulos (linha órfã), mesmo arranjo do ck_veiculo_dono (024/039).
--
--   3. SETOR DE DESTINO (P3/R4/R6) — "setor" pertence à VISITA (o mesmo
--      guincho vem hoje pra manutenção e amanhã pro almoxarifado), nunca ao
--      veículo. Tabela nova `portaria.setor` (não CHECK: a lista muda com a
--      estrutura da garagem, sem exigir deploy — espelha portaria.local),
--      com as três categorias fechadas decididas pelo Alisson em 08/09:
--      Manutenção, Operação, Administração. `movimento.setor_codigo` é
--      opcional (regra número um) e NUNCA substitui `terceiro_destino`
--      (snapshot de texto, D4) — os dois convivem.
--
-- 🔴 REGRA NÚMERO UM DO MÓDULO SEGUE VALENDO — O SISTEMA NUNCA IMPEDE UM
--   REGISTRO. `ck_movimento_identificacao` é a ÚNICA recusa nova desta
--   migration, e não é sobre a situação de ninguém: é sobre linha órfã
--   (nem placa, nem prefixo) — o mesmo tipo de guarda que já existe em
--   ck_veiculo_dono, não uma exceção à regra número um.
--
-- REGRA DE FRONTEIRA — reafirmada, não alterada
--   ⛔ Nenhum FK para public.onibus neste trabalho. O coletivo entra por
--   PREFIXO em texto, exatamente como recolhida_anormal (026) e avaria_saida
--   (036) já fazem. `onibus_id` é resolvido pelo backend quando existe
--   ônibus cadastrado com aquele número de frota; NULL não é erro.
--
-- D8 — VARCHAR + CHECK, nunca ENUM nativo. Por isso o bloco 1 é
--   DROP/ADD CONSTRAINT simples (mesmo padrão de movimento_origem_check na
--   migration 040) e ⛔ NÃO precisa de COMMIT separado no pgAdmin —
--   diferente da pegadinha do ALTER TYPE ADD VALUE.
--
-- 🟢 QUASE ADITIVA — cria 1 tabela nova (portaria.setor) e altera 2
--   constraints existentes (veiculo_tipo_check, e NOT NULL de
--   placa_registrada) para AMPLIAR o que é aceito, nunca para restringir
--   dado já gravado. Nenhum dado existente é migrado "no chute": movimento
--   antigo continua com o texto que a pessoa escreveu em terceiro_destino,
--   sem setor_codigo retroativo.
--
-- ⚠️ CONFERIR O NOME REAL DA CONSTRAINT ANTES DE RODAR (pode ter sido
--   renomeada fora deste histórico de migrations):
--   SELECT conname FROM pg_constraint
--    WHERE conrelid = 'portaria.veiculo'::regclass AND contype = 'c';
--   -- esperado hoje: veiculo_tipo_check (nome automático do Postgres para
--   -- CHECK inline sem nome explícito na coluna `tipo`, migration 024).
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 041-portaria-frota-reservado-e-setor.sql
-- ============================================================================

SET search_path TO portaria, public;

-- ============================================================================
-- 1 · TIPOS DA FROTA DE APOIO (P2/D18)
-- ============================================================================
ALTER TABLE portaria.veiculo DROP CONSTRAINT IF EXISTS veiculo_tipo_check;
ALTER TABLE portaria.veiculo ADD CONSTRAINT veiculo_tipo_check
    CHECK (tipo IN ('CARRO','MOTO','VAN','GUINCHO','CAMINHAO','UTILITARIO','OUTRO'));

COMMENT ON COLUMN portaria.veiculo.tipo IS
'Natureza do veículo LEVE que passa pelo portão. VAN/GUINCHO/CAMINHAO/UTILITARIO '
'entraram na 041 porque a frota de apoio caía toda em OUTRO e a tela não a '
'separava do carro particular (D18). ⛔ Não existe ONIBUS aqui de propósito: '
'o coletivo reservado é PASSAGEM (movimento.prefixo), nunca cadastro de '
'veículo — qualquer ônibus disponível pode ser o reservado do dia, e '
'espelhar a frota dentro deste schema é exatamente o que a regra de '
'fronteira proíbe (R1).';

-- ============================================================================
-- 2 · RESERVADO — prefixo na PASSAGEM, nunca cadastro (P4/R1/R1.b)
-- ============================================================================
ALTER TABLE portaria.movimento ADD COLUMN IF NOT EXISTS prefixo VARCHAR(10);
-- Conveniência de leitura, ⛔ SEM FK — mesmo arranjo de recolhida_anormal
-- (migration 026). Resolvido pelo backend quando existe ônibus com aquele
-- número de frota; NULL não é erro.
ALTER TABLE portaria.movimento ADD COLUMN IF NOT EXISTS onibus_id UUID;

COMMENT ON COLUMN portaria.movimento.prefixo IS
'Número de frota do coletivo RESERVADO — o ônibus que sai levando '
'funcionário. Preenchido só nessas passagens. ⛔ Movimento com prefixo NÃO '
'ENTRA em "dentro agora", em "sem saída" nem em contador algum (D18): o '
'reservado é ônibus pego entre os disponíveis, a Portaria marca a passagem '
'dele (RE, KM, observação), não a posse dele. Snapshot de texto — '
'renumeração de frota não reescreve o passado.';

COMMENT ON COLUMN portaria.movimento.onibus_id IS
'Resolvido pelo backend a partir de `prefixo`, quando existe ônibus '
'cadastrado com aquele número de frota — conveniência de leitura, ⛔ SEM FK '
'(regra de fronteira, mesmo arranjo de recolhida_anormal.onibus_id, '
'migration 026). NULL não é erro: prefixo não encontrado registra assim '
'mesmo (regra número um).';

CREATE INDEX IF NOT EXISTS ix_movimento_prefixo
    ON portaria.movimento (prefixo, data_referencia) WHERE prefixo IS NOT NULL;

-- R1.b: passagem de reservado NÃO tem placa — coletivo se identifica por
-- prefixo em todo o schema portaria (recolhida_anormal 026 e avaria_saida
-- 036 também não têm coluna de placa). Mesmo arranjo do ck_veiculo_dono da
-- migration 039: um OU outro, ⛔ nunca os dois nulos.
ALTER TABLE portaria.movimento ALTER COLUMN placa_registrada DROP NOT NULL;
ALTER TABLE portaria.movimento DROP CONSTRAINT IF EXISTS ck_movimento_identificacao;
ALTER TABLE portaria.movimento ADD CONSTRAINT ck_movimento_identificacao CHECK (
    placa_registrada IS NOT NULL OR prefixo IS NOT NULL
);

COMMENT ON COLUMN portaria.movimento.placa_registrada IS
'Snapshot (D4), não FK. Sempre preenchida nas passagens de veículo LEVE, que '
'é o que a Portaria controla. Passa a aceitar NULL a partir da 041 para a '
'passagem de RESERVADO: coletivo se identifica por prefixo em todo este '
'schema (recolhida_anormal e avaria_saida também não guardam placa), e a '
'placa do ônibus vive em public.onibus, a um prefixo de distância (R1.b). '
'⛔ Não preencher placa em movimento com prefixo. '
'ck_movimento_identificacao garante prefixo OU placa, nunca os dois nulos.';

-- 🔴 O WHERE abaixo é OBRIGATÓRIO, não documentação — sem ele a view
-- QUEBRA, não só "fica sem nome pra ler". DISTINCT ON trata NULL como igual
-- a outro NULL (ao contrário de um WHERE x = x), então TODAS as passagens
-- de reservado (placa_registrada NULL) colapsariam num único grupo do
-- DISTINCT ON, e a mais recente delas apareceria como se fosse um veículo
-- "dentro" — um reservado entraria no contador pela porta dos fundos. ⛔ Não
-- remover este WHERE achando que é só comentário explicativo.
CREATE OR REPLACE VIEW portaria.vw_dentro AS
SELECT * FROM (
    SELECT DISTINCT ON (m.placa_registrada) m.*
      FROM portaria.movimento m
     WHERE m.prefixo IS NULL AND m.placa_registrada IS NOT NULL
     ORDER BY m.placa_registrada, m.momento DESC
) ult
WHERE ult.sentido = 'ENTRADA';

COMMENT ON VIEW portaria.vw_dentro IS
'Estado derivado do último movimento por placa (D3), não de par '
'entrada/saída. A partir da 041, exclui explicitamente passagens de '
'RESERVADO (prefixo IS NOT NULL / placa NULL) de dentro do DISTINCT ON — '
'nunca depois dele, senão a última passagem de um reservado mascararia a '
'entrada anterior de uma placa de verdade que não existe aqui (D18).';

-- ============================================================================
-- 3 · SETOR de destino da visita (P3/R4/R6)
-- ============================================================================
-- Tabela, não CHECK: a lista muda com a estrutura da garagem e não pode
-- exigir deploy. Espelha portaria.local (migration 024).
CREATE TABLE IF NOT EXISTS portaria.setor (
    codigo     VARCHAR(20) PRIMARY KEY,
    nome       VARCHAR(60) NOT NULL,
    descricao  TEXT,
    ordem      SMALLINT NOT NULL DEFAULT 1,
    ativo      BOOLEAN  NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE portaria.setor IS
'Para onde a visita vai dentro da garagem. Vale para TERCEIRO e para '
'passagem AVULSA. ⛔ Não é atributo do veículo (o mesmo guincho vem hoje '
'pra manutenção e amanhã pro almoxarifado, R4) — mora no movimento. '
'Desativar setor é UPDATE ativo=FALSE, ⛔ nunca DELETE: movimento antigo '
'continua apontando pra ele.';

-- R6: três, decididos pelo Alisson em 08/09. ⛔ Não acrescentar "Outro" —
-- com três categorias largas ele só serviria de atalho pra não escolher; o
-- campo é opcional e o texto livre (terceiro_destino) cobre o resto.
INSERT INTO portaria.setor (codigo, nome, ordem, ativo) VALUES
    ('MANUTENCAO',    'Manutenção',    1, TRUE),
    ('OPERACAO',      'Operação',      2, TRUE),
    ('ADMINISTRACAO', 'Administração', 3, TRUE)
ON CONFLICT (codigo) DO NOTHING;

ALTER TABLE portaria.movimento
    ADD COLUMN IF NOT EXISTS setor_codigo VARCHAR(20) REFERENCES portaria.setor(codigo);

COMMENT ON COLUMN portaria.movimento.setor_codigo IS
'Setor de destino escolhido na lista. NULL quando o controlador digitou um '
'destino fora da lista — nesse caso vale terceiro_destino (texto). ⛔ Nunca '
'obrigatório (regra número um). FK dentro do próprio schema portaria, igual '
'a local_codigo — não fere a fronteira, que proíbe FK para tabela '
'operacional do Pátio.';

CREATE INDEX IF NOT EXISTS ix_movimento_setor
    ON portaria.movimento (setor_codigo, data_referencia) WHERE setor_codigo IS NOT NULL;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- TODAS as checks de portaria.veiculo (sem filtrar por nome — é assim
--   -- que se pega uma segunda check sobre `tipo` que tenha sobrevivido):
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'portaria.veiculo'::regclass AND contype = 'c';
--   -- esperado: só veiculo_tipo_check e ck_veiculo_dono (039); a de tipo
--   -- aceitando CARRO/MOTO/VAN/GUINCHO/CAMINHAO/UTILITARIO/OUTRO.
--
--   -- placa_registrada aceita NULL agora, e o CHECK novo existe:
--   SELECT is_nullable FROM information_schema.columns
--    WHERE table_schema='portaria' AND table_name='movimento' AND column_name='placa_registrada';
--   -- esperado: YES
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'portaria.movimento'::regclass AND conname = 'ck_movimento_identificacao';
--
--   -- INSERT com placa e prefixo nulos tem que ser RECUSADO:
--   -- INSERT INTO portaria.movimento
--   --   (sentido, data_referencia, registrado_por, placa_registrada, prefixo)
--   -- VALUES ('ENTRADA', CURRENT_DATE, (SELECT id FROM public.funcionario LIMIT 1), NULL, NULL);
--   -- esperado: ERROR — violates check constraint "ck_movimento_identificacao"
--
--   -- vw_dentro nunca traz movimento com prefixo:
--   SELECT count(*) FROM portaria.vw_dentro WHERE prefixo IS NOT NULL;  -- esperado: 0
--
--   -- setor nasceu com as 3 linhas:
--   SELECT codigo, nome, ordem FROM portaria.setor ORDER BY ordem;
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ⚠️ Só reverter o CHECK de tipo se nenhum veículo tiver os tipos novos, e só
-- voltar placa_registrada a NOT NULL se nenhum movimento tiver prefixo —
-- senão os ALTERs abaixo falham (linha existente violaria a regra antiga).
-- ALTER TABLE portaria.movimento DROP COLUMN IF EXISTS setor_codigo;
-- DROP INDEX IF EXISTS portaria.ix_movimento_setor;
-- DROP TABLE IF EXISTS portaria.setor;
--
-- CREATE OR REPLACE VIEW portaria.vw_dentro AS
-- SELECT * FROM (
--     SELECT DISTINCT ON (m.placa_registrada) m.*
--       FROM portaria.movimento m
--      ORDER BY m.placa_registrada, m.momento DESC
-- ) ult
-- WHERE ult.sentido = 'ENTRADA';
--
-- ALTER TABLE portaria.movimento DROP CONSTRAINT IF EXISTS ck_movimento_identificacao;
-- ALTER TABLE portaria.movimento ALTER COLUMN placa_registrada SET NOT NULL;
-- DROP INDEX IF EXISTS portaria.ix_movimento_prefixo;
-- ALTER TABLE portaria.movimento DROP COLUMN IF EXISTS onibus_id;
-- ALTER TABLE portaria.movimento DROP COLUMN IF EXISTS prefixo;
--
-- ALTER TABLE portaria.veiculo DROP CONSTRAINT IF EXISTS veiculo_tipo_check;
-- ALTER TABLE portaria.veiculo ADD CONSTRAINT veiculo_tipo_check
--     CHECK (tipo IN ('CARRO','MOTO','OUTRO'));
-- ============================================================================
