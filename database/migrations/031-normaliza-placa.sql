-- ============================================================================
-- MIGRATION 031 — Base limpa de placa: normalização + indicador de atípica
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMAS: portaria, coordenadoria
-- DATA:   2026-08-23
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (portaria.veiculo/movimento),
--             012-modulo-coordenadoria-ocorrencias.sql (coordenadoria.ocorrencia/
--             ocorrencia_veiculo_terceiro), 022-pre-ocorrencia.sql
--             (coordenadoria.pre_ocorrencia)
-- ORIGEM: _handoff-claude/PROMPT-AJUSTES-2026-08-23.md, Bloco A1
-- ----------------------------------------------------------------------------
-- 🔴 D10 MANTIDA SEM EXCEÇÃO (confirmado com o Alisson antes de escrever
--   esta migration): nenhuma placa fora do padrão é rejeitada em lugar
--   nenhum do sistema — nem no registro de movimento, nem no cadastro de
--   veículo. Provisória ou de outro país existe e continua sendo aceita.
--   Esta migration só faz duas coisas, nenhuma delas bloqueia nada:
--     1. Normaliza (maiúscula, sem hífen/espaço) toda placa já gravada,
--        pros valores no banco baterem com o que o backend passa a gravar
--        sempre a partir de agora (app/core/placa.py).
--     2. Acrescenta `portaria.veiculo.placa_atipica` — sinalizador pra
--        revisão manual depois (tela de veículos filtra por ele), nunca
--        um bloqueio.
--
-- 🟡 NÃO-ADITIVA EM DADO (ainda que aditiva em schema): as UPDATEs abaixo
--   reescrevem colunas existentes. Todas são idempotentes (rodar duas
--   vezes não muda nada na segunda) e nenhuma perde informação — só tira
--   hífen/espaço e sobe caixa.
--
-- ⚠️ RISCO REAL — portaria.veiculo tem índice único parcial em placa
--   (uq_portaria_veiculo_placa, WHERE ativo — ver migration 024): duas
--   placas ativas que hoje diferem só por hífen/espaço/caixa (ex.:
--   "ABC-1234" e "abc1234") colidem depois de normalizadas. Por isso o
--   bloco 1 abaixo é OBRIGATORIAMENTE rodado antes do bloco 2: primeiro o
--   diagnóstico (SELECT, só leitura), depois um guard que ABORTA a
--   migration inteira (RAISE EXCEPTION, sem gravar nada) se achar
--   qualquer colisão. Isso é o "pare o script e reporte" do prompt,
--   expresso em SQL em vez de depender de quem roda ler o SELECT antes.
--   Nenhuma outra tabela tocada aqui (movimento, ocorrencia,
--   ocorrencia_veiculo_terceiro, pre_ocorrencia) tem índice único em
--   placa — sem risco de colisão nessas.
--
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 031-normaliza-placa.sql
--   Se o guard abortar (ver mensagem), rode o diagnóstico do bloco 1
--   isolado, decida manualmente o que fazer com cada colisão (a normalização
--   NÃO decide isso sozinha) e só então rode esta migration de novo.
-- ============================================================================

-- ============================================================================
-- 1 · DIAGNÓSTICO — só leitura. Lista o que colidiria depois de normalizar.
-- ============================================================================
SELECT
    upper(regexp_replace(placa, '[^A-Za-z0-9]', '', 'g')) AS placa_normalizada,
    count(*)                                              AS quantas,
    array_agg(id)                                         AS ids
  FROM portaria.veiculo
 WHERE ativo
 GROUP BY 1
HAVING count(*) > 1;

-- ============================================================================
-- 2 · GUARD — aborta a migration inteira se o diagnóstico acima achou
--     alguma colisão. Nenhuma UPDATE abaixo roda se isto disparar.
-- ============================================================================
DO $$
DECLARE
    total_colisoes INTEGER;
BEGIN
    SELECT count(*) INTO total_colisoes
      FROM (
          SELECT upper(regexp_replace(placa, '[^A-Za-z0-9]', '', 'g')) AS placa_normalizada
            FROM portaria.veiculo
           WHERE ativo
           GROUP BY 1
          HAVING count(*) > 1
      ) colisoes;

    IF total_colisoes > 0 THEN
        RAISE EXCEPTION
            'Migration 031 abortada: % placa(s) ativa(s) colidiriam depois de normalizadas. Rode o SELECT de diagnóstico do bloco 1 isolado, decida manualmente cada caso, e só então rode esta migration de novo. Nenhum dado foi alterado.',
            total_colisoes;
    END IF;
END $$;

-- ============================================================================
-- 3 · NORMALIZAÇÃO — maiúscula, sem hífen/espaço/ponto. Idempotente.
-- ============================================================================
UPDATE portaria.veiculo
   SET placa = upper(regexp_replace(placa, '[^A-Za-z0-9]', '', 'g'))
 WHERE placa <> upper(regexp_replace(placa, '[^A-Za-z0-9]', '', 'g'));

UPDATE portaria.movimento
   SET placa_registrada = upper(regexp_replace(placa_registrada, '[^A-Za-z0-9]', '', 'g'))
 WHERE placa_registrada <> upper(regexp_replace(placa_registrada, '[^A-Za-z0-9]', '', 'g'));

UPDATE coordenadoria.ocorrencia
   SET placa = upper(regexp_replace(placa, '[^A-Za-z0-9]', '', 'g'))
 WHERE placa IS NOT NULL
   AND placa <> upper(regexp_replace(placa, '[^A-Za-z0-9]', '', 'g'));

UPDATE coordenadoria.ocorrencia_veiculo_terceiro
   SET placa = upper(regexp_replace(placa, '[^A-Za-z0-9]', '', 'g'))
 WHERE placa IS NOT NULL
   AND placa <> upper(regexp_replace(placa, '[^A-Za-z0-9]', '', 'g'));

UPDATE coordenadoria.pre_ocorrencia
   SET terceiro_placa = upper(regexp_replace(terceiro_placa, '[^A-Za-z0-9]', '', 'g'))
 WHERE terceiro_placa IS NOT NULL
   AND terceiro_placa <> upper(regexp_replace(terceiro_placa, '[^A-Za-z0-9]', '', 'g'));

-- ============================================================================
-- 4 · PLACA_ATIPICA — indicador pra revisão (nunca bloqueio). Mesmas duas
--     regex de app/core/placa.py::placa_valida (antiga AAA0000, Mercosul
--     AAA0A00) — não duplicar essa lógica em nenhum outro lugar do SQL.
-- ============================================================================
ALTER TABLE portaria.veiculo
    ADD COLUMN IF NOT EXISTS placa_atipica BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN portaria.veiculo.placa_atipica IS 'D10: nunca bloqueia o cadastro — só sinaliza pra revisão manual (tela de veículos filtra por este campo). TRUE quando a placa (já normalizada) não bate nem no formato antigo (AAA0000) nem no Mercosul (AAA0A00) — calculado por placa_valida() em routers/portaria_veiculos.py a cada create/update, e aqui uma vez, retroativo.';

UPDATE portaria.veiculo
   SET placa_atipica = NOT (
       placa ~ '^[A-Z]{3}[0-9]{4}$' OR placa ~ '^[A-Z]{3}[0-9][A-Z][0-9]{2}$'
   );

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- Nenhuma placa deveria sobrar com hífen/espaço/minúscula:
--   SELECT count(*) FROM portaria.veiculo WHERE placa ~ '[^A-Z0-9]';               -- esperado: 0
--   SELECT count(*) FROM portaria.movimento WHERE placa_registrada ~ '[^A-Z0-9]';  -- esperado: 0
--   SELECT count(*) FROM coordenadoria.ocorrencia WHERE placa ~ '[^A-Z0-9]';       -- esperado: 0
--   SELECT count(*) FROM coordenadoria.ocorrencia_veiculo_terceiro WHERE placa ~ '[^A-Z0-9]'; -- esperado: 0
--   SELECT count(*) FROM coordenadoria.pre_ocorrencia WHERE terceiro_placa ~ '[^A-Z0-9]';      -- esperado: 0
--
--   -- Quantos vieram atípicos (revisar manualmente, não é erro por si só):
--   SELECT count(*) FROM portaria.veiculo WHERE placa_atipica;
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ⚠️ Sem rollback pra normalização (passo 3) — é troca de formato, não
-- perda de informação, e reverter pra hífen/minúscula não tem valor.
-- ALTER TABLE portaria.veiculo DROP COLUMN IF EXISTS placa_atipica;
-- ============================================================================
