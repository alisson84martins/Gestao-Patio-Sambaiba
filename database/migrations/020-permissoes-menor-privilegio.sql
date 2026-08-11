-- ============================================================================
-- MIGRATION 020 — Menor privilégio: COORDENADOR_TRAFEGO e ENCARREGADO
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- DATA:   2026-08-11
-- AUTOR:  Alisson Martins (decisão de 11/08/2026)
-- DEPENDE DE: 011-rbac-cadastro-central.sql (tabela funcao_permissao)
-- ORIGEM: _handoff-claude/RELATORIO-SEGURANCA-2026-08-10.md, SEV-01
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   SEV-01: COORDENADOR_TRAFEGO tinha pode_escrever=TRUE no recurso
--   "usuarios" — o mesmo gate que protege POST /funcionarios/{id}/funcoes,
--   endpoint que atribui QUALQUER função, inclusive ADMIN, sem restrição
--   adicional no corpo. Decisão do Alisson (11/08): ADMIN é só o RE 5598;
--   coordenador não cadastra usuário.
--
--   Decisão adicional do Alisson (11/08, Bloco B do prompt de execução):
--   ENCARREGADO deixa de escrever em "ocorrencia" — aciona um coordenador
--   em vez de registrar diretamente. Ver Bloco B (visibilidade de
--   ocorrência por autoria).
--
-- NATUREZA: idempotente. UPDATE por condição — rodar de novo não faz mal.
--   ⚠️ O Alisson já aplicou o primeiro UPDATE manualmente em produção em
--   11/08 (confirmado: só o RE 5598 escreve em "usuarios" hoje). Esta
--   migration precisa existir mesmo assim — sem ela, uma instalação nova
--   ou uma reexecução do seed 08 traz o buraco de volta.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table funcao_permissao", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 020-permissoes-menor-privilegio.sql
-- ============================================================================

-- SEV-01 · COORDENADOR_TRAFEGO deixa de escrever em "usuarios"
UPDATE funcao_permissao fp SET pode_escrever = FALSE
  FROM funcao fn
 WHERE fn.id = fp.funcao_id AND fn.codigo = 'COORDENADOR_TRAFEGO'
   AND fp.recurso = 'usuarios';

-- Bloco B · ENCARREGADO deixa de escrever em "ocorrencia" — ele aciona um
-- coordenador (decisão do Alisson, 11/08/2026)
UPDATE funcao_permissao fp SET pode_escrever = FALSE
  FROM funcao fn
 WHERE fn.id = fp.funcao_id AND fn.codigo = 'ENCARREGADO'
   AND fp.recurso = 'ocorrencia';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
-- Depois de aplicar: quem ainda escreve em "usuarios"? Deve sobrar SÓ o RE 5598.
-- SELECT f.re, f.nome, v.pode_escrever, v.e_excecao
--   FROM vw_acesso_efetivo v JOIN funcionario f ON f.id = v.funcionario_id
--  WHERE v.recurso = 'usuarios' AND v.pode_escrever ORDER BY f.re;
--
-- ⚠️ O override individual (tabela `permissao`) SOBRESCREVE o pacote da
-- função — vw_acesso_efetivo faz COALESCE(p.pode_escrever, a.pode_escrever,
-- FALSE), não "o mais permissivo vence". Se a consulta acima devolver
-- alguém além do RE 5598, confira se essa pessoa tem uma linha em
-- `permissao` para o recurso 'usuarios' com pode_escrever=TRUE — essa
-- migration só mexe em funcao_permissao (o pacote padrão), não em
-- exceções individuais:
-- SELECT f.re, f.nome, p.recurso, p.pode_ler, p.pode_escrever
--   FROM permissao p JOIN funcionario f ON f.id = p.funcionario_id
--  WHERE p.recurso IN ('usuarios', 'ocorrencia') AND p.pode_escrever;
--
-- Quem ainda escreve em "ocorrencia" (esperado: ADMIN, COORDENADOR_TRAFEGO —
-- não mais ENCARREGADO):
-- SELECT f.re, f.nome, v.pode_escrever, v.e_excecao
--   FROM vw_acesso_efetivo v JOIN funcionario f ON f.id = v.funcionario_id
--  WHERE v.recurso = 'ocorrencia' AND v.pode_escrever ORDER BY f.re;
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- UPDATE funcao_permissao fp SET pode_escrever = TRUE
--   FROM funcao fn
--  WHERE fn.id = fp.funcao_id AND fn.codigo = 'COORDENADOR_TRAFEGO'
--    AND fp.recurso = 'usuarios';
-- UPDATE funcao_permissao fp SET pode_escrever = TRUE
--   FROM funcao fn
--  WHERE fn.id = fp.funcao_id AND fn.codigo = 'ENCARREGADO'
--    AND fp.recurso = 'ocorrencia';
-- (Não recomendado — reabre SEV-01. Só use se o Alisson decidir reverter
-- a decisão de negócio.)
-- ============================================================================
