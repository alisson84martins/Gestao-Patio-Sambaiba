-- ============================================================================
-- MIGRATION 015 — Renumera posições do pátio e elimina os buracos
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- DATA:   2026-07-31
-- AUTOR:  Alisson Martins
-- DEPENDE DE: 005-constraints-indexes.sql (uq_alocacao_fila_posicao_ativa)
-- ----------------------------------------------------------------------------
-- BUG CORRIGIDO
--   Não existia renumeração em lugar nenhum do backend: desativar() só
--   marcava ativa=FALSE e deixava o buraco; POST /bloco no sentido ida
--   calculava MAX(posicao)+1 (que nunca volta a cair); no sentido volta,
--   remarcar um carro que já estava na fila inflava o MAX em 1 a cada vez.
--   Em dias de operação sem limpar o pátio, as posições chegavam a 232.
--   app/routers/alocacoes.py ganhou _renumerar_fila() e passa a chamá-la
--   depois de toda operação que tira ou move ônibus de fila — mas isso só
--   vale daqui pra frente. Este script corrige os dados que já estão
--   errados em produção.
--
-- O QUE ESTE SCRIPT FAZ
--   Renumera as alocações ATIVAS de TODAS as filas em 1..N, ordenando pela
--   posição atual (preserva a ordem física em que os carros foram
--   marcados — só tira os buracos, nunca reordena). Mesma técnica de duas
--   fases que _renumerar_fila usa no backend, aqui em SQL direto:
--     1) desloca cada ativa pra uma posição temporária alta, única dentro
--        da própria fila (ROW_NUMBER() particionado por fila_id) — nunca
--        colide com uma posição real nem entre si;
--     2) desloca de volta pra 1..N final — de novo sem colisão, porque
--        nenhuma posição pequena ainda está ocupada nesse ponto.
--   uq_alocacao_fila_posicao_ativa é um índice único parcial comum (não
--   DEFERRABLE) — daí a necessidade das duas fases em vez de reatribuir
--   direto.
--
-- NATUREZA: só reescreve a coluna `posicao` de linhas já ativas. Não perde
-- dado nenhum — nenhum ônibus muda de fila, nenhuma alocação é criada ou
-- desativada. Idempotente: rodar de novo com tudo já 1..N não muda nada
-- (ROW_NUMBER() reproduz a mesma ordem).
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 015-renumera-posicoes-patio.sql
-- ============================================================================

BEGIN;

-- Fase 1 — desloca todas as ativas pra uma faixa temporária alta.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY fila_id ORDER BY posicao ASC) AS rn
      FROM alocacao_patio
     WHERE ativa = TRUE
)
UPDATE alocacao_patio a
   SET posicao = 1000000 + ranked.rn
  FROM ranked
 WHERE a.id = ranked.id;

-- Fase 2 — desloca da faixa temporária pra 1..N final.
UPDATE alocacao_patio
   SET posicao = posicao - 1000000
 WHERE ativa = TRUE
   AND posicao > 1000000;

COMMIT;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   Nenhuma fila pode ter buraco depois da correção (esperado: 0 linhas):
SELECT f.nome, count(*) AS carros, max(a.posicao) AS maior_posicao
  FROM alocacao_patio a JOIN fila f ON f.id = a.fila_id
 WHERE a.ativa GROUP BY f.nome HAVING count(*) <> max(a.posicao);
--
--   Nenhuma posição pode ter sobrado na faixa temporária (esperado: 0 linhas):
--     SELECT * FROM alocacao_patio WHERE ativa AND posicao > 1000000;
--
--   Total de ônibus ativos antes e depois tem que bater (rode antes de
--   aplicar e compare — nenhuma linha pode sumir ou aparecer):
--     SELECT count(*) FROM alocacao_patio WHERE ativa;
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- Não há rollback significativo: a operação não perde dado (nenhum ônibus
-- muda de fila, nenhuma linha é criada, alterada em ativa ou removida) —
-- só reescreve a coluna `posicao` das ativas pra tirar os buracos,
-- preservando a ordem física original. Não existe estado anterior "correto"
-- pra restaurar; o estado anterior é o próprio bug (232, 132 etc.). Se por
-- algum motivo for necessário desfazer, restaure `alocacao_patio` de um
-- backup anterior à execução deste script.
-- ============================================================================
