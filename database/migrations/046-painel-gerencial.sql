-- ============================================================================
-- MIGRATION 046 — Painel Gerencial: tudo que Portaria e Pátio registram
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: public (RBAC + view de leitura); lê portaria.* e public.alocacao_patio
-- DATA:   2026-09-25
-- AUTOR:  Claude Code
-- DEPENDE DE: 011-rbac-cadastro-central.sql (modulo/recurso/funcao/
--             funcao_permissao), 024/026/032/036/041/042 (tabelas da
--             Portaria), 006-functions-triggers.sql (fn_alocacao_unica_ativa)
--             — NADA da 045 (em produção roda depois dela só pela ordem).
-- ORIGEM: _handoff-claude/PROMPT-painel-gerencial-2026-09-25.md, seção 3
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   O gerente geral quer acompanhar tudo que a Portaria e o Pátio registram,
--   ao vivo e com o histórico inteiro, num portal SÓ DE LEITURA. Recurso
--   novo (`painel_gerencial`) e módulo próprio — não reaproveita
--   `relatorios`, porque o COORDENADOR_TRAFEGO tem `relatorios` e NÃO pode
--   ver o painel.
--
--   A view `vw_painel_evento` é a linha do tempo unificada: um UNION ALL das
--   tabelas de origem com colunas padronizadas. Nenhuma tabela nova, nenhum
--   trigger, nenhuma escrita — nenhum router da Portaria/Pátio muda.
--
-- COLUNAS DA VIEW
--   id, momento (timestamptz — o INSTANTE do evento, nunca data_referencia),
--   modulo (PORTARIA|PATIO), tipo, categoria, identificacao (placa/prefixo/
--   número do ônibus), detalhe, pessoa_re/pessoa_nome (condutor ou motorista
--   envolvido), autor_re/autor_nome (quem registrou no sistema).
--   ⚠️ `id` NÃO é único na view: é o id da linha de origem, e uma mesma
--   linha gera mais de um evento (RECOLHIDA + RECOLHIDA_AVALIADA +
--   RECOLHIDA_ENCERRADA; AVARIA + AVARIA_ENCERRADA; ALOCACAO + RETIRADA).
--   A chave de um evento é (tipo, id).
--
-- CATEGORIA DO MOVIMENTO DA PORTARIA
--   RESERVADO (prefixo preenchido) → FROTA_APOIO (veículo EMPRESA) →
--   TERCEIRO → FUNCIONARIO. TERCEIRO aqui é o mesmo critério do
--   _enriquecer_dentro (routers/portaria.py): veículo cadastrado como
--   TERCEIRO OU terceiro_nome OU terceiro_empresa — o prompt pedia só
--   terceiro_nome, mas o prestador de empresa cadastrada nem sempre traz o
--   nome do condutor, e cairia em FUNCIONARIO.
--
-- REGRAS DO PÁTIO (conferidas em 006::fn_alocacao_unica_ativa e
--   routers/alocacoes.py em 25/09):
--   • Mover = INSERT de linha nova; o trigger desativa a anterior na MESMA
--     transação com atualizado_em = NOW() e atualizado_por = alocado_por.
--     NOW() é o início da transação, igual ao default de alocado_em da
--     linha nova — por isso a janela de ±2 s é folga, não aproximação.
--   • MOVIMENTACAO = existe linha anterior do mesmo ônibus (LAG por
--     alocado_em) e ela foi desativada no instante desta (±2 s).
--   • ALOCACAO = primeira do ônibus, ou a anterior já tinha saído antes
--     (retirada).
--   • RETIRADA = ativa = FALSE e nenhuma linha do mesmo ônibus nasceu em
--     atualizado_em ± 2 s (senão foi movimentação, já contada na linha nova).
--   • DELETE /alocacoes ("limpar tudo") é um UPDATE em massa sem autor:
--     essas retiradas saem com autor NULL (o front mostra "Limpeza geral do
--     pátio"). Pendência conhecida, não é bug desta view.
--   • `posicao` é a posição ATUAL da linha — a renumeração
--     (_renumerar_fila) reescreve a posição das ativas. Para linha já
--     desativada é a última posição que o carro teve naquela fila.
--   • alocado_por/atualizado_por apontam para a tabela LEGADA `usuario`
--     (FK de produção), não para `funcionario` — autor vem de usuario.re/nome.
--
-- AVARIA_REVISTA — só constatação POSTERIOR à abertura: registrar avaria
--   nova já grava a 1ª constatação no mesmo instante (conferido no banco
--   local em 25/09: sem o filtro, cada AVARIA aparecia também como REVISTA).
--
-- ÍNDICES — só os que faltavam em produção (conferido em 25/09):
--   alocacao_patio(alocado_em) já existe (idx_alocacao_alocado_em).
--
-- NATUREZA: 100% ADITIVA e idempotente (ON CONFLICT DO NOTHING,
--   CREATE OR REPLACE VIEW, CREATE INDEX IF NOT EXISTS). Não altera
--   funcao.modulo_padrao de ninguém.
--
-- ⚠️ DADO PESSOAL: a view expõe RE e nome de condutores/motoristas que já
--   estão nas tabelas de origem — acesso só por `painel_gerencial`.
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): rode com SET ROLE sambaiba.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 046-painel-gerencial.sql
-- ============================================================================

-- ============================================================================
-- 1 · O MÓDULO (ordem 7 — PATIO 1 … MANUTENCAO 6, migrations 011/024/037)
-- ============================================================================
INSERT INTO public.modulo (codigo, nome, descricao, ordem) VALUES
  ('PAINEL_GERENCIAL', 'Painel Gerencial',
   'Tudo que a Portaria e o Pátio registram — ao vivo e histórico. Só leitura.', 7)
ON CONFLICT (codigo) DO NOTHING;

-- ============================================================================
-- 2 · O RECURSO
-- ============================================================================
INSERT INTO public.recurso (codigo, nome, descricao, modulo_codigo, ordem) VALUES
  ('painel_gerencial', 'Painel gerencial',
   'Consulta de tudo que Portaria e Pátio registram', 'PAINEL_GERENCIAL', 1)
ON CONFLICT (codigo) DO NOTHING;

-- ============================================================================
-- 3 · PERMISSÕES — só leitura, só gestão. COORDENADOR_TRAFEGO fica de fora.
-- ============================================================================
INSERT INTO public.funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, v.recurso, v.pode_ler, v.pode_escrever
  FROM public.funcao fn
  JOIN (VALUES
        ('ADMIN',               'painel_gerencial', TRUE, FALSE),
        ('GERENTE_GERAL',       'painel_gerencial', TRUE, FALSE),
        ('GERENTE_OPERACIONAL', 'painel_gerencial', TRUE, FALSE),
        ('ENCARREGADO',         'painel_gerencial', TRUE, FALSE)
       ) AS v(funcao_codigo, recurso, pode_ler, pode_escrever) ON v.funcao_codigo = fn.codigo
 ON CONFLICT ON CONSTRAINT uq_funcao_permissao DO NOTHING;

-- ============================================================================
-- 4 · A VIEW — linha do tempo unificada
-- ============================================================================
CREATE OR REPLACE VIEW public.vw_painel_evento AS

-- PORTARIA · ENTRADA / SAIDA ------------------------------------------------
SELECT m.id,
       m.momento,
       'PORTARIA'::text                                   AS modulo,
       m.sentido::text                                    AS tipo,
       CASE
         WHEN m.prefixo IS NOT NULL                        THEN 'RESERVADO'
         WHEN v.propriedade = 'EMPRESA'                    THEN 'FROTA_APOIO'
         WHEN v.propriedade = 'TERCEIRO'
           OR m.terceiro_nome IS NOT NULL
           OR m.terceiro_empresa IS NOT NULL               THEN 'TERCEIRO'
         ELSE 'FUNCIONARIO'
       END                                                AS categoria,
       COALESCE(m.prefixo, m.placa_registrada)::text      AS identificacao,
       NULLIF(concat_ws(' · ',
              m.terceiro_empresa,
              m.terceiro_destino,
              st.nome,
              CASE WHEN m.hodometro_km IS NOT NULL THEN m.hodometro_km || ' km' END,
              m.observacao), '')                          AS detalhe,
       m.re_registrado::text                              AS pessoa_re,
       COALESCE(m.nome_registrado, m.terceiro_nome)::text AS pessoa_nome,
       au.re::text                                        AS autor_re,
       au.nome::text                                      AS autor_nome
  FROM portaria.movimento m
  LEFT JOIN portaria.veiculo  v  ON v.id = m.veiculo_id
  LEFT JOIN portaria.setor    st ON st.codigo = m.setor_codigo
  LEFT JOIN public.funcionario au ON au.id = m.registrado_por

UNION ALL
-- PORTARIA · RECOLHIDA -----------------------------------------------------
SELECT r.id, r.momento, 'PORTARIA', 'RECOLHIDA',
       r.motivo::text,
       r.prefixo::text,
       NULLIF(concat_ws(' · ',
              td.nome,
              CASE WHEN r.linha_codigo IS NOT NULL THEN 'Linha ' || r.linha_codigo END,
              r.relato), ''),
       r.motorista_re::text, r.motorista_nome::text,
       au.re::text, au.nome::text
  FROM portaria.recolhida_anormal r
  LEFT JOIN public.tipo_defeito td ON td.codigo = r.tipo_defeito_codigo
  LEFT JOIN public.funcionario  au ON au.id = r.registrado_por

UNION ALL
-- PORTARIA · RECOLHIDA_AVALIADA --------------------------------------------
SELECT r.id, r.avaliado_em, 'PORTARIA', 'RECOLHIDA_AVALIADA',
       r.avaliacao::text,
       r.prefixo::text,
       NULLIF(concat_ws(' · ',
              r.avaliacao,
              CASE WHEN r.prazo_minutos IS NOT NULL THEN 'prazo ' || r.prazo_minutos || ' min' END,
              r.avaliacao_relato), ''),
       r.motorista_re::text, r.motorista_nome::text,
       au.re::text, au.nome::text
  FROM portaria.recolhida_anormal r
  LEFT JOIN public.funcionario au ON au.id = r.avaliado_por
 WHERE r.avaliado_em IS NOT NULL

UNION ALL
-- PORTARIA · RECOLHIDA_ENCERRADA -------------------------------------------
SELECT r.id, r.encerrado_em, 'PORTARIA', 'RECOLHIDA_ENCERRADA',
       r.desfecho::text,
       r.prefixo::text,
       NULLIF(concat_ws(' · ', r.desfecho, r.encerramento_relato), ''),
       r.motorista_re::text, r.motorista_nome::text,
       au.re::text, au.nome::text
  FROM portaria.recolhida_anormal r
  LEFT JOIN public.funcionario au ON au.id = r.encerrado_por
 WHERE r.encerrado_em IS NOT NULL

UNION ALL
-- PORTARIA · AVARIA (nova) ---------------------------------------------------
SELECT s.id, s.criado_em, 'PORTARIA', 'AVARIA',
       s.zona_codigo::text,
       s.prefixo::text,
       NULLIF(concat_ws(' · ', z.nome, t.nome, s.severidade, s.descricao), ''),
       s.motorista_re::text, s.motorista_nome::text,
       au.re::text, au.nome::text
  FROM portaria.avaria_saida s
  LEFT JOIN portaria.avaria_zona z ON z.codigo = s.zona_codigo
  LEFT JOIN portaria.avaria_tipo t ON t.codigo = s.tipo_codigo
  LEFT JOIN public.funcionario  au ON au.id = s.registrado_por

UNION ALL
-- PORTARIA · AVARIA_REVISTA (constatação não anulada) ------------------------
SELECT c.id, c.criado_em, 'PORTARIA', 'AVARIA_REVISTA',
       s.zona_codigo::text,
       s.prefixo::text,
       NULLIF(concat_ws(' · ', z.nome, t.nome,
              CASE WHEN c.piorou THEN 'PIOROU' END, c.observacao), ''),
       c.motorista_re::text, c.motorista_nome::text,
       au.re::text, au.nome::text
  FROM portaria.avaria_constatacao c
  JOIN portaria.avaria_saida s ON s.id = c.avaria_id
  LEFT JOIN portaria.avaria_zona z ON z.codigo = s.zona_codigo
  LEFT JOIN portaria.avaria_tipo t ON t.codigo = s.tipo_codigo
  LEFT JOIN public.funcionario  au ON au.id = c.registrado_por
 WHERE c.anulada_em IS NULL
   -- A abertura da avaria já grava a 1ª constatação no mesmo instante
   -- (portaria_avarias.py::_nova_constatacao, ocorrido_em = agora da
   -- avaria). Sem este filtro toda AVARIA nova contaria também como REVISTA.
   AND c.ocorrido_em > s.ocorrido_em

UNION ALL
-- PORTARIA · AVARIA_CONTESTADA -----------------------------------------------
SELECT ct.id, ct.ocorrido_em, 'PORTARIA', 'AVARIA_CONTESTADA',
       s.zona_codigo::text,
       s.prefixo::text,
       NULLIF(concat_ws(' · ', z.nome, t.nome, ct.nota), ''),
       NULL::text, NULL::text,
       au.re::text, au.nome::text
  FROM portaria.avaria_contestacao ct
  JOIN portaria.avaria_saida s ON s.id = ct.avaria_id
  LEFT JOIN portaria.avaria_zona z ON z.codigo = s.zona_codigo
  LEFT JOIN portaria.avaria_tipo t ON t.codigo = s.tipo_codigo
  LEFT JOIN public.funcionario  au ON au.id = ct.registrado_por

UNION ALL
-- PORTARIA · AVARIA_ENCERRADA ------------------------------------------------
SELECT s.id, s.encerrada_em, 'PORTARIA', 'AVARIA_ENCERRADA',
       s.encerramento::text,
       s.prefixo::text,
       NULLIF(concat_ws(' · ', z.nome, t.nome, s.encerramento, s.encerramento_nota), ''),
       s.motorista_re::text, s.motorista_nome::text,
       au.re::text, au.nome::text
  FROM portaria.avaria_saida s
  LEFT JOIN portaria.avaria_zona z ON z.codigo = s.zona_codigo
  LEFT JOIN portaria.avaria_tipo t ON t.codigo = s.tipo_codigo
  LEFT JOIN public.funcionario  au ON au.id = s.encerrada_por
 WHERE s.encerrada_em IS NOT NULL

UNION ALL
-- PORTARIA · VEICULO_SITUACAO ------------------------------------------------
SELECT h.id, h.decidido_em, 'PORTARIA', 'VEICULO_SITUACAO',
       h.situacao_para::text,
       v.placa::text,
       NULLIF(concat_ws(' · ',
              COALESCE(h.situacao_de, '—') || ' → ' || h.situacao_para,
              h.motivo), ''),
       COALESCE(dono.re, v.re_dono_texto)::text, dono.nome::text,
       au.re::text, au.nome::text
  FROM portaria.veiculo_situacao_hist h
  JOIN portaria.veiculo v ON v.id = h.veiculo_id
  LEFT JOIN public.funcionario dono ON dono.id = v.funcionario_id
  LEFT JOIN public.funcionario au   ON au.id = h.decidido_por

UNION ALL
-- PATIO · ALOCACAO / MOVIMENTACAO (cada linha de alocacao_patio) -------------
SELECT seq.id, seq.alocado_em, 'PATIO',
       CASE WHEN seq.fila_ant IS NOT NULL
             AND seq.ant_desativada_em BETWEEN seq.alocado_em - interval '2 seconds'
                                           AND seq.alocado_em + interval '2 seconds'
            THEN 'MOVIMENTACAO' ELSE 'ALOCACAO' END,
       f.nome::text,
       o.numero_frota::text,
       CASE WHEN seq.fila_ant IS NOT NULL
             AND seq.ant_desativada_em BETWEEN seq.alocado_em - interval '2 seconds'
                                           AND seq.alocado_em + interval '2 seconds'
            THEN fa.nome || ' → ' || f.nome || ' · pos ' || seq.posicao
            ELSE f.nome || ' · pos ' || seq.posicao END,
       NULL::text, NULL::text,
       u.re::text, u.nome::text
  FROM (SELECT ap.id, ap.onibus_id, ap.fila_id, ap.posicao, ap.alocado_em, ap.alocado_por,
               LAG(ap.fila_id)       OVER w AS fila_ant,
               LAG(ap.atualizado_em) OVER w AS ant_desativada_em
          FROM public.alocacao_patio ap
        WINDOW w AS (PARTITION BY ap.onibus_id ORDER BY ap.alocado_em, ap.id)) seq
  JOIN public.onibus o ON o.id = seq.onibus_id
  JOIN public.fila   f ON f.id = seq.fila_id
  LEFT JOIN public.fila    fa ON fa.id = seq.fila_ant
  LEFT JOIN public.usuario u  ON u.id = seq.alocado_por

UNION ALL
-- PATIO · RETIRADA (desativada sem sucessora no mesmo instante) --------------
SELECT ap.id, ap.atualizado_em, 'PATIO', 'RETIRADA',
       f.nome::text,
       o.numero_frota::text,
       'Saiu de ' || f.nome || ' · pos ' || ap.posicao,
       NULL::text, NULL::text,
       u.re::text, u.nome::text
  FROM public.alocacao_patio ap
  JOIN public.onibus o ON o.id = ap.onibus_id
  JOIN public.fila   f ON f.id = ap.fila_id
  LEFT JOIN public.usuario u ON u.id = ap.atualizado_por
 WHERE ap.ativa = FALSE
   AND ap.atualizado_em IS NOT NULL
   AND NOT EXISTS (
         SELECT 1 FROM public.alocacao_patio n
          WHERE n.onibus_id = ap.onibus_id
            AND n.id <> ap.id
            AND n.alocado_em BETWEEN ap.atualizado_em - interval '2 seconds'
                                 AND ap.atualizado_em + interval '2 seconds');

COMMENT ON VIEW public.vw_painel_evento IS
  'Painel Gerencial (046): linha do tempo unificada, só leitura, de tudo que Portaria e Pátio registram. momento = instante do evento. Chave de evento = (tipo, id) — id é o da linha de origem e se repete entre tipos.';

-- ============================================================================
-- 5 · ÍNDICES (os que faltavam)
-- ============================================================================
CREATE INDEX IF NOT EXISTS ix_painel_movimento_momento ON portaria.movimento (momento);
CREATE INDEX IF NOT EXISTS ix_painel_recolhida_momento ON portaria.recolhida_anormal (momento);
CREATE INDEX IF NOT EXISTS ix_painel_avaria_criado_em  ON portaria.avaria_saida (criado_em);
CREATE INDEX IF NOT EXISTS ix_painel_alocacao_onibus_alocado
    ON public.alocacao_patio (onibus_id, alocado_em);

-- ============================================================================
-- 6 · 🔴 GRANT (a falta de GRANT derrubou produção em 17/09)
-- ============================================================================
GRANT SELECT ON public.vw_painel_evento TO sambaiba;

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   SELECT codigo, ordem FROM public.modulo WHERE codigo = 'PAINEL_GERENCIAL';   -- 7
--   SELECT fn.codigo, fp.pode_ler, fp.pode_escrever
--     FROM public.funcao_permissao fp JOIN public.funcao fn ON fn.id = fp.funcao_id
--    WHERE fp.recurso = 'painel_gerencial' ORDER BY fn.codigo;
--   -- esperado: ADMIN, ENCARREGADO, GERENTE_GERAL, GERENTE_OPERACIONAL — (T,F)
--   SELECT has_table_privilege('sambaiba', 'public.vw_painel_evento', 'SELECT');  -- t
--   SELECT modulo, tipo, count(*), min(momento), max(momento)
--     FROM public.vw_painel_evento GROUP BY 1, 2 ORDER BY 1, 2;
--   -- RETIRADA tem que ser bem menor que MOVIMENTACAO (senão a regra do
--   -- sucessor está errada).
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- DROP VIEW IF EXISTS public.vw_painel_evento;
-- DROP INDEX IF EXISTS portaria.ix_painel_movimento_momento;
-- DROP INDEX IF EXISTS portaria.ix_painel_recolhida_momento;
-- DROP INDEX IF EXISTS portaria.ix_painel_avaria_criado_em;
-- DROP INDEX IF EXISTS public.ix_painel_alocacao_onibus_alocado;
-- DELETE FROM public.funcao_permissao WHERE recurso = 'painel_gerencial';
-- DELETE FROM public.permissao WHERE recurso = 'painel_gerencial';  -- overrides individuais, se alguém criou
-- DELETE FROM public.recurso WHERE codigo = 'painel_gerencial';
-- DELETE FROM public.modulo  WHERE codigo = 'PAINEL_GERENCIAL';
-- ============================================================================
