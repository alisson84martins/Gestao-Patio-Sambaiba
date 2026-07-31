-- ============================================================================
-- MIGRATION 014 — Espelho transitório em `usuario` para contas do fluxo novo
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- DATA:   2026-07-31
-- AUTOR:  Alisson Martins
-- DEPENDE DE: 011-rbac-cadastro-central.sql
-- ----------------------------------------------------------------------------
-- BUG CORRIGIDO (urgente — gente travada em produção)
--   get_current_user (app/core/deps.py) resolve o JWT novo assim:
--   Funcionario → busca Usuario pelo RE → se não achar, 401. Quem foi
--   cadastrado só em funcionario + usuario_login (fluxo novo, tela de
--   Cadastros) nunca ganhou linha em `usuario`, então TODO endpoint que usa
--   CurrentUser — os 14 routers legados do Pátio — devolve 401. api.js trata
--   401 limpando a sessão e mandando pro login: a pessoa loga, abre o Pátio,
--   e é expulsa. Foi o caso de um Gerente Operacional cadastrado em
--   30/07/2026.
--
-- O QUE ESTE SCRIPT FAZ
--   Backfill único: para todo funcionario com usuario_login e SEM linha
--   correspondente em `usuario` (mesmo RE), cria a linha espelho, com o
--   MESMO hash de senha do usuario_login (a senha já digitada continua
--   funcionando — ninguém precisa trocar nada).
--   Dali em diante, POST /funcionarios/{id}/login (app/routers/
--   funcionarios.py, _criar_ou_atualizar_espelho_usuario) cria esse espelho
--   na hora — este script cobre só quem já tinha login ANTES do fix.
--
-- usuario.perfil É DEPRECADO
--   NOT NULL, enum de 5 valores herdado do sistema antigo, sem efeito no
--   RBAC novo (que usa funcao_permissao/permissao, não usuario.perfil).
--   Só existe aqui pra satisfazer a constraint. Derivado da função
--   PRINCIPAL da pessoa:
--     ADMIN                 → ADMIN
--     COORDENADOR_TRAFEGO    → COORDENADOR
--     OPERADOR_PATIO         → OPERADOR_PATIO
--     MECANICO                → MECANICO
--     qualquer outra função   → MOTORISTA  (perfil mais restrito do legado)
--
-- NATUREZA: aditiva, idempotente (INSERT ... WHERE NOT EXISTS). Pode rodar
-- de novo sem duplicar nem sobrescrever quem já tem espelho.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 014-espelho-usuario-transicao.sql
-- ============================================================================

BEGIN;

INSERT INTO usuario (id, re, nome, senha_hash, perfil, ativo, cpf, criado_em)
SELECT
    gen_random_uuid(),
    f.re,
    f.nome,
    ul.senha_hash,
    (CASE fn.codigo
        WHEN 'ADMIN'               THEN 'ADMIN'
        WHEN 'COORDENADOR_TRAFEGO' THEN 'COORDENADOR'
        WHEN 'OPERADOR_PATIO'      THEN 'OPERADOR_PATIO'
        WHEN 'MECANICO'            THEN 'MECANICO'
        ELSE 'MOTORISTA'
    END)::perfil_usuario_enum,
    ul.ativo,
    f.cpf,
    NOW()
  FROM funcionario f
  JOIN usuario_login ul ON ul.funcionario_id = f.id
  LEFT JOIN funcionario_funcao ff
         ON ff.funcionario_id = f.id AND ff.principal = TRUE
  LEFT JOIN funcao fn ON fn.id = ff.funcao_id
 WHERE NOT EXISTS (SELECT 1 FROM usuario u WHERE u.re = f.re);

COMMIT;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   Ninguém deve ficar sem espelho depois de rodar (esperado: 0 linhas):
--     SELECT f.re, f.nome
--       FROM funcionario f
--       JOIN usuario_login ul ON ul.funcionario_id = f.id
--      WHERE NOT EXISTS (SELECT 1 FROM usuario u WHERE u.re = f.re);
--
--   Perfil atribuído por pessoa, pra conferência humana:
--     SELECT f.re, f.nome, fn.codigo AS funcao_principal, u.perfil
--       FROM funcionario f
--       JOIN usuario_login ul ON ul.funcionario_id = f.id
--       JOIN usuario u ON u.re = f.re
--       LEFT JOIN funcionario_funcao ff
--              ON ff.funcionario_id = f.id AND ff.principal = TRUE
--       LEFT JOIN funcao fn ON fn.id = ff.funcao_id
--      ORDER BY f.nome;
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ⚠️ Não é seguro em cima de um banco que já tinha contas legadas em
-- `usuario` antes da migration 011 — o filtro abaixo remove QUALQUER linha
-- de usuario cujo RE bata com um funcionario com usuario_login, não só as
-- criadas por este backfill. Confira a lista antes de rodar:
--   SELECT u.* FROM usuario u
--     WHERE EXISTS (
--           SELECT 1 FROM funcionario f
--           JOIN usuario_login ul ON ul.funcionario_id = f.id
--          WHERE f.re = u.re
--     );
-- DELETE FROM usuario u
--  WHERE EXISTS (
--        SELECT 1 FROM funcionario f
--        JOIN usuario_login ul ON ul.funcionario_id = f.id
--       WHERE f.re = u.re
--  );
-- ============================================================================
