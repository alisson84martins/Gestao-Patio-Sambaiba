-- ============================================================================
-- MIGRATION 013 — Destino de login por função
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: public
-- DATA:   2026-07-30
-- AUTOR:  Alisson Martins
-- DEPENDE DE: 011-rbac-cadastro-central.sql
-- ----------------------------------------------------------------------------
-- O PROBLEMA QUE ISTO RESOLVE
--   A regra "quem tem um módulo só entra direto nele; quem tem vários escolhe"
--   funciona para quase todos, mas não para o FISCAL: pela matriz de permissões
--   ele lê o Pátio (precisa saber onde está cada carro) E escreve na
--   Fiscalização. Dois módulos — cairia na tela de escolha, quando o lugar dele
--   é a tela de linhas.
--
--   A causa é que duas coisas diferentes estavam sendo tratadas como uma só:
--     • a QUAIS módulos a pessoa tem acesso  (matriz de permissões)
--     • em QUAL módulo ela aterrissa ao entrar (rotina de trabalho)
--
--   Esta migration separa as duas. `funcao.modulo_padrao` define o destino:
--     • preenchido → login vai direto para aquele módulo
--     • nulo       → login mostra a tela de escolha
--   Quem tem acesso a mais de um módulo continua podendo trocar pelo menu.
--
-- COMPORTAMENTO RESULTANTE
--   MOTORISTA, COBRADOR, APONTADOR, MECANICO, OPERADOR_PATIO, PLANTONISTA
--       → entram direto no Pátio
--   FISCAL
--       → entra direto na Fiscalização (mas segue com leitura do Pátio)
--   COORDENADOR_TRAFEGO, ENCARREGADO, GERENTE_GERAL, GERENTE_OPERACIONAL, ADMIN
--       → tela de escolha de módulo
--
--   Mudar o destino de qualquer função é um UPDATE, sem tocar em código.
--
-- NATUREZA: aditiva. Não altera acesso de ninguém — só o ponto de chegada.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 013-destino-login-por-funcao.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1 · Coluna de destino
-- ----------------------------------------------------------------------------
ALTER TABLE funcao
    ADD COLUMN IF NOT EXISTS modulo_padrao VARCHAR(20) REFERENCES modulo(codigo);

COMMENT ON COLUMN funcao.modulo_padrao IS
'Módulo em que a pessoa aterrissa ao entrar. NULO = mostra a tela de escolha. Não concede acesso — quem decide isso é funcao_permissao.';

-- ----------------------------------------------------------------------------
-- 2 · Destinos por função
-- ----------------------------------------------------------------------------
UPDATE funcao SET modulo_padrao = 'PATIO'
 WHERE codigo IN ('MOTORISTA','COBRADOR','APONTADOR','MECANICO','OPERADOR_PATIO','PLANTONISTA');

UPDATE funcao SET modulo_padrao = 'FISCALIZACAO'
 WHERE codigo = 'FISCAL';

-- Gestão e administração escolhem — destino nulo, explícito
UPDATE funcao SET modulo_padrao = NULL
 WHERE codigo IN ('COORDENADOR_TRAFEGO','ENCARREGADO','GERENTE_GERAL','GERENTE_OPERACIONAL','ADMIN');

-- ----------------------------------------------------------------------------
-- 3 · VIEW de destino — o backend consulta esta para saber onde mandar a pessoa
--
--   Critério da função de referência: a marcada como `principal`; na falta
--   dela, a de menor nível (mais alta na hierarquia).
--
--   Consequência desejada: quem acumula ADMIN (destino nulo) com MOTORISTA
--   (destino Pátio) cai na tela de escolha — o cargo mais alto manda.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_destino_login AS
SELECT DISTINCT ON (f.id)
       f.id             AS funcionario_id,
       fn.codigo        AS funcao_referencia,
       fn.nivel         AS nivel_referencia,
       fn.modulo_padrao AS modulo_padrao
  FROM funcionario f
  JOIN funcionario_funcao ff ON ff.funcionario_id = f.id
                            AND ff.ativo
                            AND (ff.data_fim IS NULL OR ff.data_fim >= CURRENT_DATE)
  JOIN funcao fn ON fn.id = ff.funcao_id AND fn.ativo
 ORDER BY f.id, ff.principal DESC, fn.nivel ASC;

COMMENT ON VIEW vw_destino_login IS
'Para onde mandar a pessoa depois do login. modulo_padrao nulo = tela de escolha de módulo.';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
-- Destinos configurados (esperado: 6 em PATIO, 1 em FISCALIZACAO, 5 nulos):
--   SELECT COALESCE(modulo_padrao,'(escolhe)') AS destino,
--          count(*), string_agg(codigo, ', ' ORDER BY nivel)
--     FROM funcao GROUP BY 1 ORDER BY 1;
--
-- Para onde cada pessoa do roster vai cair:
--   SELECT f.re, f.nome, d.funcao_referencia,
--          COALESCE(d.modulo_padrao,'(tela de escolha)') AS destino
--     FROM vw_destino_login d
--     JOIN funcionario f ON f.id = d.funcionario_id
--    ORDER BY d.nivel_referencia, f.re;
--
-- Casos que provam a regra:
--   • RE 5598  (ADMIN + COORDENADOR_TRAFEGO) → (tela de escolha)
--   • RE 12728 (OPERADOR_PATIO + MOTORISTA)  → PATIO
--   • RE 5400  (APONTADOR)                    → PATIO

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DROP VIEW IF EXISTS vw_destino_login;
-- ALTER TABLE funcao DROP COLUMN IF EXISTS modulo_padrao;
-- ============================================================================
