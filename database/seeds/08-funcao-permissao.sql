-- ============================================================================
-- SEED 08 — Matriz de permissões padrão (12 funções × 9 recursos)
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- DATA:   2026-07-28
-- AUTOR:  Alisson Martins  (matriz aprovada em 28/07/2026)
-- DEPENDE DE: migration 011-rbac-cadastro-central.sql
-- ----------------------------------------------------------------------------
-- O QUE ESTE SCRIPT FAZ
--   Preenche `funcao_permissao` — o pacote PADRÃO de acesso de cada função.
--   Quem tem a função herda este pacote. Ajuste individual (o "liberar caso a
--   caso") vai na tabela `permissao`, que sobrepõe o que estiver aqui.
--
-- REGRA DE COMBINAÇÃO
--   Pessoa com duas funções recebe a UNIÃO dos pacotes — a mais permissiva
--   vence. Ex.: Damião é OPERADOR_PATIO (escreve em alocacao) + MOTORISTA (só
--   lê alocacao) → resultado: escreve. Está correto: o poder vem do cargo mais
--   alto que a pessoa exerce.
--
-- IDEMPOTENTE: pode rodar de novo; ON CONFLICT atualiza os flags.
--
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 08-funcao-permissao.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- MATRIZ APROVADA  (⭕ nada · 👁 ler · ✍ ler e escrever)
-- ----------------------------------------------------------------------------
--                        |------- MÓDULO PÁTIO --------|-FISC-|--- COORDENADORIA ---|-ADM-
--  Função                | aloc | esca | aler | manu | frot | fisc | ocor | coor | rela | usua
--  ----------------------|------|------|------|------|------|------|------|------|------|-----
--  ADMIN                 |  ✍  |  ✍  |  ✍  |  ✍  |  ✍  |  ✍  |  ✍  |  ✍  |  ✍  |  ✍
--  GERENTE_GERAL         |  👁  |  👁  |  👁  |  👁  |  👁  |  👁  |  👁  |  👁  |  👁  |  ⭕
--  GERENTE_OPERACIONAL   |  👁  |  ✍  |  👁  |  👁  |  👁  |  👁  |  👁  |  ✍  |  👁  |  ⭕
--  ENCARREGADO           |  ✍  |  ✍  |  ✍  |  👁  |  👁  |  👁  |  ✍  |  👁  |  👁  |  ⭕
--  COORDENADOR_TRAFEGO   |  ✍  |  ✍  |  ✍  |  👁  |  👁  |  ✍  |  ✍  |  ✍  |  👁  |  ✍
--  FISCAL                |  👁  |  👁  |  ✍  |  ⭕  |  👁  |  ✍  |  ⭕  |  ⭕  |  ⭕  |  ⭕
--  PLANTONISTA           |  ✍  |  👁  |  ✍  |  👁  |  👁  |  👁  |  ⭕  |  ⭕  |  ⭕  |  ⭕
--  OPERADOR_PATIO        |  ✍  |  👁  |  ✍  |  👁  |  👁  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕
--  APONTADOR             |  👁  |  👁  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕
--  MECANICO              |  👁  |  ⭕  |  👁  |  ✍  |  👁  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕
--  MOTORISTA             |  👁  |  👁  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕
--  COBRADOR              |  👁  |  👁  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕  |  ⭕
--
-- QUEM VÊ QUAL MÓDULO NA TELA DE ESCOLHA (consequência direta da matriz):
--   ADMIN / COORDENADOR_TRAFEGO ..... Pátio · Coordenadoria · Fiscalização · Administração
--   GERENTE_GERAL / OPERACIONAL ..... Pátio · Coordenadoria · Fiscalização   (só acompanha)
--   ENCARREGADO ..................... Pátio · Coordenadoria · Fiscalização
--   FISCAL .......................... Pátio · Fiscalização
--   PLANTONISTA ..................... Pátio · Fiscalização
--   OPERADOR_PATIO / APONTADOR ...... Pátio
--   MECANICO / MOTORISTA / COBRADOR . Pátio
--
-- DECISÕES REGISTRADAS (28/07/2026):
--   1. Coordenador de Tráfego ESCREVE no pátio — na prática ele opera quando
--      precisa (aloca, move chip, limpa fila), não só acompanha.
--   2. Gerência é LEITURA (painel de acompanhamento de fora da garagem).
--      Exceção: Gerente Operacional escreve em escala e coordenação.
--   3. Cadastro de pessoas: ADMIN + Coordenador de Tráfego. Os coordenadores
--      cadastram e atribuem funções; a trava de RE/CPF único protege contra
--      duplicata (regra nascida do caso Adriano RE 5400/55400).
--   4. Fiscal escreve em alertas — quem está na rua vendo o carro preso é quem
--      tem a informação na hora.
--   5. Motorista, Cobrador e Apontador seguem em consulta (decisão anterior,
--      mantida).
--   6. OCORRÊNCIAS (novo, 28/07): só Coordenador de Tráfego e Encarregado
--      REGISTRAM. Gerência apenas VISUALIZA. Quem atende a ocorrência é quem
--      registra — dado consistente, padrão sob controle da coordenação.
--   7. Alerta PRESO e Ocorrência seguem SEPARADOS. PRESO continua sendo alerta
--      operacional do pátio (carro retido, alta rotatividade); ocorrência é o
--      registro analítico da coordenação. Nada muda no fluxo em produção.
-- ============================================================================

BEGIN;

-- Limpa o pacote padrão antes de reaplicar (não toca nas exceções por pessoa,
-- que vivem na tabela `permissao`).
DELETE FROM funcao_permissao;

INSERT INTO funcao_permissao (funcao_id, recurso, pode_ler, pode_escrever)
SELECT fn.id, m.recurso, m.pode_ler, m.pode_escrever
  FROM funcao fn
  JOIN (VALUES
    -- ── ADMIN — acesso total ────────────────────────────────────────────────
    ('ADMIN','alocacao',TRUE,TRUE),   ('ADMIN','escala',TRUE,TRUE),
    ('ADMIN','alerta',TRUE,TRUE),     ('ADMIN','manutencao',TRUE,TRUE),
    ('ADMIN','frota',TRUE,TRUE),      ('ADMIN','fiscalizacao',TRUE,TRUE),
    ('ADMIN','coordenacao',TRUE,TRUE),('ADMIN','relatorios',TRUE,TRUE),
    ('ADMIN','ocorrencia',TRUE,TRUE), ('ADMIN','usuarios',TRUE,TRUE),

    -- ── GERENTE_GERAL — acompanha tudo, não opera ───────────────────────────
    ('GERENTE_GERAL','alocacao',TRUE,FALSE),   ('GERENTE_GERAL','escala',TRUE,FALSE),
    ('GERENTE_GERAL','alerta',TRUE,FALSE),     ('GERENTE_GERAL','manutencao',TRUE,FALSE),
    ('GERENTE_GERAL','frota',TRUE,FALSE),      ('GERENTE_GERAL','fiscalizacao',TRUE,FALSE),
    ('GERENTE_GERAL','coordenacao',TRUE,FALSE),('GERENTE_GERAL','relatorios',TRUE,FALSE),
    ('GERENTE_GERAL','ocorrencia',TRUE,FALSE),

    -- ── GERENTE_OPERACIONAL — leitura + escrita em escala e coordenação ─────
    ('GERENTE_OPERACIONAL','alocacao',TRUE,FALSE),   ('GERENTE_OPERACIONAL','escala',TRUE,TRUE),
    ('GERENTE_OPERACIONAL','alerta',TRUE,FALSE),     ('GERENTE_OPERACIONAL','manutencao',TRUE,FALSE),
    ('GERENTE_OPERACIONAL','frota',TRUE,FALSE),      ('GERENTE_OPERACIONAL','fiscalizacao',TRUE,FALSE),
    ('GERENTE_OPERACIONAL','coordenacao',TRUE,TRUE), ('GERENTE_OPERACIONAL','relatorios',TRUE,FALSE),
    ('GERENTE_OPERACIONAL','ocorrencia',TRUE,FALSE),

    -- ── ENCARREGADO — opera pátio, escala e alertas ─────────────────────────
    ('ENCARREGADO','alocacao',TRUE,TRUE),    ('ENCARREGADO','escala',TRUE,TRUE),
    ('ENCARREGADO','alerta',TRUE,TRUE),      ('ENCARREGADO','manutencao',TRUE,FALSE),
    ('ENCARREGADO','frota',TRUE,FALSE),      ('ENCARREGADO','fiscalizacao',TRUE,FALSE),
    ('ENCARREGADO','coordenacao',TRUE,FALSE),('ENCARREGADO','relatorios',TRUE,FALSE),
    ('ENCARREGADO','ocorrencia',TRUE,TRUE),

    -- ── COORDENADOR_TRAFEGO — o cargo do dia a dia da coordenação ───────────
    ('COORDENADOR_TRAFEGO','alocacao',TRUE,TRUE),    ('COORDENADOR_TRAFEGO','escala',TRUE,TRUE),
    ('COORDENADOR_TRAFEGO','alerta',TRUE,TRUE),      ('COORDENADOR_TRAFEGO','manutencao',TRUE,FALSE),
    ('COORDENADOR_TRAFEGO','frota',TRUE,FALSE),      ('COORDENADOR_TRAFEGO','fiscalizacao',TRUE,TRUE),
    ('COORDENADOR_TRAFEGO','coordenacao',TRUE,TRUE), ('COORDENADOR_TRAFEGO','relatorios',TRUE,FALSE),
    ('COORDENADOR_TRAFEGO','ocorrencia',TRUE,TRUE),  ('COORDENADOR_TRAFEGO','usuarios',TRUE,TRUE),

    -- ── FISCAL — registra na rua ────────────────────────────────────────────
    ('FISCAL','alocacao',TRUE,FALSE), ('FISCAL','escala',TRUE,FALSE),
    ('FISCAL','alerta',TRUE,TRUE),    ('FISCAL','frota',TRUE,FALSE),
    ('FISCAL','fiscalizacao',TRUE,TRUE),

    -- ── PLANTONISTA — opera pátio no plantão, acompanha fiscalização ────────
    ('PLANTONISTA','alocacao',TRUE,TRUE),  ('PLANTONISTA','escala',TRUE,FALSE),
    ('PLANTONISTA','alerta',TRUE,TRUE),    ('PLANTONISTA','manutencao',TRUE,FALSE),
    ('PLANTONISTA','frota',TRUE,FALSE),    ('PLANTONISTA','fiscalizacao',TRUE,FALSE),

    -- ── OPERADOR_PATIO — o pátio é dele ─────────────────────────────────────
    ('OPERADOR_PATIO','alocacao',TRUE,TRUE),   ('OPERADOR_PATIO','escala',TRUE,FALSE),
    ('OPERADOR_PATIO','alerta',TRUE,TRUE),     ('OPERADOR_PATIO','manutencao',TRUE,FALSE),
    ('OPERADOR_PATIO','frota',TRUE,FALSE),

    -- ── APONTADOR — consulta ────────────────────────────────────────────────
    ('APONTADOR','alocacao',TRUE,FALSE), ('APONTADOR','escala',TRUE,FALSE),

    -- ── MECANICO — a manutenção é dele ──────────────────────────────────────
    ('MECANICO','alocacao',TRUE,FALSE),  ('MECANICO','alerta',TRUE,FALSE),
    ('MECANICO','manutencao',TRUE,TRUE), ('MECANICO','frota',TRUE,FALSE),

    -- ── MOTORISTA / COBRADOR — consulta ─────────────────────────────────────
    ('MOTORISTA','alocacao',TRUE,FALSE), ('MOTORISTA','escala',TRUE,FALSE),
    ('COBRADOR','alocacao',TRUE,FALSE),  ('COBRADOR','escala',TRUE,FALSE)
  ) AS m(funcao_codigo, recurso, pode_ler, pode_escrever)
    ON m.funcao_codigo = fn.codigo
ON CONFLICT (funcao_id, recurso) DO UPDATE
   SET pode_ler      = EXCLUDED.pode_ler,
       pode_escrever = EXCLUDED.pode_escrever;

COMMIT;


-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
-- Total de linhas gravadas (esperado: 73):
--   SELECT count(*) FROM funcao_permissao;
--
-- Matriz montada por função (leitura humana):
--   SELECT fn.codigo, fn.nivel,
--          string_agg(r.nome || CASE WHEN fp.pode_escrever THEN ' [escreve]' ELSE ' [lê]' END,
--                     ' · ' ORDER BY r.ordem) AS acessos
--     FROM funcao fn
--     LEFT JOIN funcao_permissao fp ON fp.funcao_id = fn.id
--     LEFT JOIN recurso r ON r.codigo = fp.recurso
--    GROUP BY fn.codigo, fn.nivel ORDER BY fn.nivel, fn.codigo;
--
-- Teste da união de funções — Damião (RE 12728, OPERADOR_PATIO + MOTORISTA)
-- deve aparecer com pode_escrever = TRUE em alocacao:
--   SELECT r.nome, v.pode_ler, v.pode_escrever
--     FROM vw_acesso_efetivo v
--     JOIN funcionario f ON f.id = v.funcionario_id
--     JOIN recurso r ON r.codigo = v.recurso
--    WHERE f.re = '12728' ORDER BY r.ordem;
--
-- Nenhuma função pode ficar sem acesso nenhum (sintoma de código digitado errado):
--   SELECT fn.codigo FROM funcao fn
--    WHERE NOT EXISTS (SELECT 1 FROM funcao_permissao fp WHERE fp.funcao_id = fn.id);
--    -- esperado: 0 linhas
-- ============================================================================
