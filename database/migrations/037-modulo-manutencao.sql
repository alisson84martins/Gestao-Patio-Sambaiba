-- ============================================================================
-- MIGRATION 037 — Módulo Manutenção: separa de Pátio + recolhida REGISTRAR × TRATAR
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: public (RBAC — funcao, recurso, modulo, funcao_permissao)
-- DATA:   2026-08-24
-- AUTOR:  Claude Code
-- DEPENDE DE: 011-rbac-cadastro-central.sql (funcionario/recurso/funcao/
--             funcao_permissao/modulo), 013-destino-login-por-funcao.sql
--             (funcao.modulo_padrao), 024-modulo-portaria.sql (mesmo molde
--             de criação de módulo — nenhuma view nova, tudo dirigido por
--             dado), 026-recolhida-anormal.sql (recurso recolhida_anormal,
--             hoje concedido também ao MECANICO)
-- ORIGEM: _handoff-claude/PROMPT-portaria-ajustes-2026-08-24.md, Bloco I
--         (decidido em 24/08, depois de o Alisson achar a aba RA que já
--         funcionava — ver Bloco H do mesmo prompt)
-- ----------------------------------------------------------------------------
-- ⚠️ NÚMERO — os arquivos vão até 036 (034-funilaria, 035 e 036 nascem no
--   mesmo prompt, blocos D e G). Próximo número livre: 037.
--
-- POR QUÊ
--   A barra do Pátio hoje é Pátio · Remanejamento · Alertas · Manutenção ·
--   Importação, e Manutenção não é operação de pátio — é outro setor, outra
--   chefia, outro ritmo. Duas decisões tomadas pelo Alisson em 24/08,
--   ⛔ não reabrir:
--     1. O atalho sai de vez do Pátio (barra do Pátio fica com 4 itens).
--        Quem quiser Manutenção troca de módulo — 2 cliques, custo aceito.
--     2. A recolhida se divide em dois acessos (ver seção 3 abaixo) —
--        corrige de caminho um defeito de produção que já existia.
--
-- 🔴 O BUG QUE ESTA MIGRATION CORRIGE (achado na varredura, não introduzido
--   por ela): MECANICO tem `recolhida_anormal` (migration 026), recurso do
--   módulo PORTARIA. `vw_modulos_usuario` libera um módulo pra quem tem
--   QUALQUER recurso dele — então o mecânico via o card PORTARIA na tela de
--   seleção. Clicar levava a portaria.html, que exige `acesso_veicular` —
--   que ele nunca teve. Card que não abre. Causa raiz: um recurso só
--   (`recolhida_anormal`) cobrindo dois atos de setores diferentes:
--   REGISTRAR (controlador, na guarita) e TRATAR (mecânico, na oficina).
--   A seção 3 separa os dois; a seção 4 tira `recolhida_anormal` do
--   MECANICO, que é a linha que faz o card PORTARIA sumir da tela dele.
--
-- CONFERIDO NO BANCO LOCAL ANTES DE ESCREVER ESTA MIGRATION (24/08/2026):
--   SELECT fn.codigo FROM funcao_permissao fp_ra JOIN funcao fn ON fn.id =
--     fp_ra.funcao_id WHERE fp_ra.recurso = 'recolhida_anormal' AND NOT
--     EXISTS (SELECT 1 FROM funcao_permissao fp_av WHERE fp_av.funcao_id =
--     fp_ra.funcao_id AND fp_av.recurso = 'acesso_veicular');
--   → devolveu só MECANICO. Confirmado: é a única função com
--   recolhida_anormal sem acesso_veicular hoje. Depois da seção 4, esta
--   mesma consulta devolve 0 linhas (ver CONFERÊNCIA).
--
-- ⚠️ EFEITO COLATERAL DA SEÇÃO 2 (mover `manutencao` de PATIO pra
--   MANUTENCAO) E A DECISÃO DE NÃO MEXER NELE AINDA
--   Hoje têm leitura em `manutencao`: ADMIN, MECANICO, COORDENADOR_TRAFEGO,
--   ENCARREGADO, GERENTE_GERAL, GERENTE_OPERACIONAL, OPERADOR_PATIO,
--   PLANTONISTA (conferido no banco local em 24/08 — ver query acima do
--   mesmo tipo, trocando o recurso). Mudar o módulo do recurso faz todos
--   eles ganharem o card MANUTENÇÃO na tela de seleção — pra a maioria
--   (ADMIN, os dois gerentes, encarregado, coordenador, mecânico) isso é
--   correto, é gente que efetivamente lida com manutenção.
--
--   OPERADOR_PATIO e PLANTONISTA são os dois casos discutíveis — hoje só
--   leem `manutencao` (nunca escrevem), candidatos naturais a perder o
--   acesso pelo princípio de menor privilégio (migration 020). ⛔ DECISÃO
--   DE 24/08, DEPOIS DE DISCUTIDA COM O ALISSON: esta migration NÃO remove
--   a leitura deles. Motivo: a pergunta certa não era "quem pode abrir a
--   tela da manutenção" — era "como o operador de pátio fica sabendo, na
--   hora, que um carro está pronto pra voltar pra frota". Tirar o acesso
--   agora, sem um substituto, deixa o operador SEM a tela E SEM aviso
--   nenhum ao mesmo tempo — pior que a situação atual. O substituto certo
--   (um aviso na própria tela do Pátio, não acesso a um módulo que não é
--   dele) é o Bloco J — ver
--   _handoff-claude/PROMPT-patio-liberados-bloco-J.md. A remoção da
--   leitura de OPERADOR_PATIO/PLANTONISTA em `manutencao` fica para a
--   MIGRATION 038, publicada junto do Bloco J, não antes. Até lá, os dois
--   verão o card MANUTENÇÃO na tela de seleção — front-end sabido, aceito
--   de propósito, não é bug desta migration.
--
--   `APONTADOR`, citado como candidato no prompt original, não tem
--   `manutencao` hoje (conferido — 0 linhas) — não é questão nesta rodada.
--
-- REGRA DE FRONTEIRA — nenhuma tabela nova, nenhuma FK nova. Só catálogo
--   compartilhado (modulo/recurso/funcao/funcao_permissao), mesmo padrão da
--   024.
--
-- NATUREZA: ADITIVA quase toda — INSERT ... ON CONFLICT DO NOTHING e dois
--   UPDATE restritos (ver EXCEÇÕES CONTROLADAS abaixo) + um DELETE de UMA
--   linha (MECANICO × recolhida_anormal). Não idempotente no sentido
--   estrito do DELETE (rodar duas vezes não erra — a segunda vez não acha
--   a linha —, mas não é um INSERT reversível de graça; ver ROLLBACK).
--
-- ⚠️ EXCEÇÕES CONTROLADAS (fora do padrão 100% aditivo):
--   1. UPDATE public.recurso — move `manutencao` de PATIO pra MANUTENCAO.
--   2. UPDATE public.funcao — modulo_padrao do MECANICO de PATIO pra
--      MANUTENCAO.
--   3. DELETE public.funcao_permissao — UMA linha (MECANICO ×
--      recolhida_anormal). Nenhuma outra função é tocada; CONTROLADOR_ACESSO
--      mantém recolhida_anormal (registrar continua sendo ato da portaria).
--
-- ⚠️ DADO PESSOAL: nenhum nesta migration.
--
-- ORDEM OBRIGATÓRIA DE DEPLOY (ver I.5 do prompt): esta migration + o
--   backend (routers/portaria_recolhidas.py, troca de recurso exigido)
--   ANTES do frontend. Se o frontend subir primeiro, o mecânico aterrissa
--   num módulo que /auth/me ainda não devolve. E depois: TODO MUNDO precisa
--   deslogar e logar de novo — módulo e permissões são lidos no login e
--   ficam no localStorage.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 037-modulo-manutencao.sql
-- ============================================================================

-- ============================================================================
-- 1 · O MÓDULO
-- ============================================================================
INSERT INTO public.modulo (codigo, nome, descricao, ordem) VALUES
  ('MANUTENCAO', 'Manutenção',
   'Fichas de defeito, serviços da frota e tratativa de recolhidas.', 6)
ON CONFLICT (codigo) DO NOTHING;

-- ============================================================================
-- 2 · O recurso `manutencao` muda de casa: PATIO -> MANUTENCAO
-- ============================================================================
-- ⚠️ EXCEÇÃO CONTROLADA — efeito colateral aceito e documentado no cabeçalho:
-- todo mundo com leitura em `manutencao` (inclusive OPERADOR_PATIO e
-- PLANTONISTA, de propósito, ver cabeçalho) passa a ver o card MANUTENÇÃO.
UPDATE public.recurso SET modulo_codigo = 'MANUTENCAO' WHERE codigo = 'manutencao';

-- ============================================================================
-- 3 · Recolhida: separa REGISTRAR (Portaria) de TRATAR (Manutenção)
-- ============================================================================
INSERT INTO public.recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
  ('recolhida_tratativa', 'Tratativa de Recolhida',
   'Avaliar (liberar/reter) e encerrar recolhida anormal.', 'MANUTENCAO', 2)
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, v.recurso, v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('MECANICO',             'recolhida_tratativa', TRUE,  TRUE),
        ('ADMIN',                'recolhida_tratativa', TRUE,  TRUE),

        ('GERENTE_GERAL',        'recolhida_tratativa', TRUE,  FALSE),
        ('GERENTE_OPERACIONAL',  'recolhida_tratativa', TRUE,  FALSE),
        ('ENCARREGADO',          'recolhida_tratativa', TRUE,  FALSE),
        ('COORDENADOR_TRAFEGO',  'recolhida_tratativa', TRUE,  FALSE)
       ) AS v(funcao_codigo, recurso, pode_ler, pode_escrever) ON v.funcao_codigo = fn.codigo
 ON CONFLICT ON CONSTRAINT uq_funcao_permissao DO NOTHING;

-- ============================================================================
-- 4 · Destino de login do mecânico
-- ============================================================================
UPDATE public.funcao SET modulo_padrao = 'MANUTENCAO'
 WHERE codigo = 'MECANICO' AND modulo_padrao IS DISTINCT FROM 'MANUTENCAO';

-- ============================================================================
-- 5 · Remove `recolhida_anormal` do MECANICO — corrige o bug do cabeçalho
-- ============================================================================
-- ⛔ NÃO remover de CONTROLADOR_ACESSO — registrar continua sendo ato da
-- portaria; a fronteira inteira desta mudança é essa.
DELETE FROM public.funcao_permissao
 WHERE recurso = 'recolhida_anormal'
   AND funcao_id = (SELECT id FROM public.funcao WHERE codigo = 'MECANICO');

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- Módulo criado, ordem 6 (não colide com PORTARIA=5):
--   SELECT codigo, ordem FROM modulo WHERE codigo = 'MANUTENCAO';
--
--   -- manutencao agora é do módulo MANUTENCAO:
--   SELECT modulo_codigo FROM recurso WHERE codigo = 'manutencao';  -- esperado: MANUTENCAO
--
--   -- recolhida_tratativa existe e tem as 6 concessões esperadas:
--   SELECT fn.codigo, fp.pode_ler, fp.pode_escrever
--     FROM funcao_permissao fp JOIN funcao fn ON fn.id = fp.funcao_id
--    WHERE fp.recurso = 'recolhida_tratativa' ORDER BY fn.codigo;
--   -- esperado: ADMIN(T,T), COORDENADOR_TRAFEGO(T,F), ENCARREGADO(T,F),
--   --           GERENTE_GERAL(T,F), GERENTE_OPERACIONAL(T,F), MECANICO(T,T)
--
--   -- Mecânico cai direto na Manutenção ao logar:
--   SELECT modulo_padrao FROM funcao WHERE codigo = 'MECANICO';  -- esperado: MANUTENCAO
--
--   -- 🔴 A checagem central: ninguém tem recolhida_anormal sem acesso_veicular
--   -- (a mesma consulta rodada ANTES desta migration devolvia só MECANICO):
--   SELECT fn.codigo FROM funcao_permissao fp_ra
--     JOIN funcao fn ON fn.id = fp_ra.funcao_id
--    WHERE fp_ra.recurso = 'recolhida_anormal'
--      AND NOT EXISTS (
--          SELECT 1 FROM funcao_permissao fp_av
--           WHERE fp_av.funcao_id = fp_ra.funcao_id AND fp_av.recurso = 'acesso_veicular'
--      );
--   -- esperado: 0 linhas
--
--   -- CONTROLADOR_ACESSO não foi tocado — continua com recolhida_anormal (T,T):
--   SELECT fp.pode_ler, fp.pode_escrever FROM funcao_permissao fp
--     JOIN funcao fn ON fn.id = fp.funcao_id
--    WHERE fn.codigo = 'CONTROLADOR_ACESSO' AND fp.recurso = 'recolhida_anormal';
--   -- esperado: (TRUE, TRUE)
--
--   -- Efeito colateral aceito, documentado, não é regressão: OPERADOR_PATIO
--   -- e PLANTONISTA continuam com manutencao (ler). Remoção fica pra 038:
--   SELECT fn.codigo, fp.pode_ler FROM funcao_permissao fp
--     JOIN funcao fn ON fn.id = fp.funcao_id
--    WHERE fp.recurso = 'manutencao' AND fn.codigo IN ('OPERADOR_PATIO','PLANTONISTA');
--   -- esperado: 2 linhas, pode_ler = TRUE nas duas
--
--   -- Rodar o arquivo inteiro DUAS VEZES não duplica nada (o DELETE da
--   -- seção 5 já não acha linha na segunda vez — sem erro).
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- -- Devolve recolhida_anormal ao MECANICO (mesmos valores da migration 026):
-- INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
-- SELECT id, 'recolhida_anormal', TRUE, TRUE FROM public.funcao WHERE codigo = 'MECANICO'
-- ON CONFLICT ON CONSTRAINT uq_funcao_permissao DO NOTHING;
--
-- UPDATE public.funcao SET modulo_padrao = 'PATIO' WHERE codigo = 'MECANICO';
--
-- DELETE FROM public.funcao_permissao WHERE recurso = 'recolhida_tratativa';
-- DELETE FROM public.recurso WHERE codigo = 'recolhida_tratativa';
--
-- UPDATE public.recurso SET modulo_codigo = 'PATIO' WHERE codigo = 'manutencao';
--
-- DELETE FROM public.modulo WHERE codigo = 'MANUTENCAO';
-- -- (Seguro: nada além de `manutencao`/`recolhida_tratativa` referencia
-- -- MANUTENCAO por modulo_codigo, e os dois já foram revertidos acima.)
-- ============================================================================
