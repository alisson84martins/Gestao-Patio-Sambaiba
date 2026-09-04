-- ============================================================================
-- MIGRATION 040 — Coluna de medição da leitura de placa por câmera
-- ----------------------------------------------------------------------------
-- BANCO:  gestao_frota_sambaiba (produção) / gestao_patio_sambaiba (dev)
-- SCHEMA: portaria (existente — migration 024)
-- DATA:   2026-09-04/05
-- AUTOR:  Claude Code
-- DEPENDE DE: 024-modulo-portaria.sql (tabela portaria.movimento, coluna
--             origem e seu CHECK)
-- ORIGEM: _handoff-claude/PROMPT-leitura-placa.md, Bloco 2 (P13)
-- ----------------------------------------------------------------------------
-- POR QUÊ
--   A câmera substitui o QR (negativa da diretoria em 03/09) e vai para
--   produção SEM prova prévia — a própria operação real é o teste (P13 do
--   PROMPT-leitura-placa.md). Sem medir, o Alisson chega na diretoria
--   dizendo "funcionou bem"; com a coluna abaixo, ele chega dizendo "em N
--   dias, X leituras, Y% confirmadas sem correção". A diferença entre as
--   duas frases é esta migration.
--
-- 🟢 QUASE ADITIVA — um ALTER TABLE ADD COLUMN e uma troca do CHECK de
--   origem (DROP + ADD, mesmo nome, só acrescenta 'CAMERA' ao conjunto
--   aceito). Nenhuma tabela nova, nenhum DROP de dado, nenhuma imagem
--   guardada (P4 do PROMPT-leitura-placa.md continua valendo sem exceção —
--   isto é texto, nunca imagem).
--
-- placa_lida_bruta — o texto CRU que o reconhecimento devolveu, antes de o
--   controlador confirmar ou corrigir na tela (P2). NULLABLE, sem default,
--   sem CHECK de formato: movimento digitado (origem='MANUAL') continua
--   com ela vazia — é isso que separa os dois grupos na consulta de
--   medição. Quando o controlador corrige a placa lida, o valor bruto e o
--   valor corrigido (placa_registrada) ficam os dois gravados de propósito
--   (P13): a diferença entre eles é o dado que interessa.
--
-- origem — o CHECK existente (migration 024) só aceitava
--   ('MANUAL','RETROATIVO','QR','TAG','LPR'). 'CAMERA' entra como sexto
--   valor porque o Bloco 3 (tela da Portaria) vai gravar origem='CAMERA'
--   quando a placa vier da leitura — sem este ALTER, o primeiro movimento
--   registrado pela câmera falharia com violação de CHECK em produção.
--   ⛔ 'QR' permanece na lista: P11 do PROMPT-leitura-placa.md tira o QR da
--   TELA, não do banco — código e dado dormindo não custam nada, e o valor
--   continua válido para histórico de movimentos antigos.
--
-- CONSULTA QUE ESTA MIGRATION HABILITA (P13, sem tabela nova):
--   SELECT count(*) FILTER (WHERE placa_lida_bruta = placa_registrada) AS acertou,
--          count(*)                                                    AS total
--     FROM portaria.movimento
--    WHERE origem = 'CAMERA';
--
-- ARMADILHA DE DONO DE TABELA (ver 011, PARTE 0): se der
-- "must be owner of table X", rode SET ROLE sambaiba; antes.
-- COMO RODAR:
--   sudo -u postgres psql -d gestao_frota_sambaiba -c "SET ROLE sambaiba;" \
--        -f 040-leitura-placa-medicao.sql
-- ⛔ ESCREVER APENAS — não executar. A engine de reconhecimento ainda não
-- foi escolhida (depende de medir RAM/CPU do servidor, só o Alisson entra
-- lá) e o interruptor LEITURA_PLACA_ATIVA (P14) nasce desligado.
-- ============================================================================

ALTER TABLE portaria.movimento
    ADD COLUMN IF NOT EXISTS placa_lida_bruta VARCHAR(8) NULL;

COMMENT ON COLUMN portaria.movimento.placa_lida_bruta IS
'Texto CRU devolvido pelo reconhecimento de placa por câmera, antes da '
'confirmação/correção do controlador (P2 do PROMPT-leitura-placa.md). '
'NULL quando o movimento não veio de câmera (origem != CAMERA) ou quando a '
'leitura não achou placa nenhuma. Gravado MESMO quando o controlador '
'corrige a placa — a diferença entre este campo e placa_registrada é a '
'taxa de acerto que vai à diretoria (P13). ⛔ Nunca imagem, só texto — P4 '
'continua valendo sem exceção: nenhuma foto é armazenada em lugar nenhum.';

ALTER TABLE portaria.movimento DROP CONSTRAINT IF EXISTS movimento_origem_check;
ALTER TABLE portaria.movimento ADD CONSTRAINT movimento_origem_check CHECK (
    origem IN ('MANUAL','RETROATIVO','QR','TAG','LPR','CAMERA')
);

COMMENT ON COLUMN portaria.movimento.origem IS
'MANUAL = digitado na hora pelo controlador. RETROATIVO = lançado depois a '
'partir do papel, na contingência de queda de internet — exige observação '
'no backend. CAMERA = placa veio do reconhecimento por foto (Bloco 1/3 do '
'PROMPT-leitura-placa.md), sempre com placa_lida_bruta preenchido e sempre '
'confirmada por um humano antes de virar movimento (P1). QR/TAG/LPR '
'reservados — QR saiu da TELA em 04/09 (P11) mas o valor continua válido '
'para histórico; nenhum dos três é gerado por código novo hoje. Em todos '
'os casos, a leitura automática é só ACELERAÇÃO da digitação, nunca '
'pré-requisito para o registro acontecer.';

-- ============================================================================
-- CONFERÊNCIA
-- ============================================================================
--   -- Coluna nova existe:
--   SELECT column_name, is_nullable, data_type FROM information_schema.columns
--    WHERE table_schema = 'portaria' AND table_name = 'movimento'
--      AND column_name = 'placa_lida_bruta';
--   -- esperado: placa_lida_bruta | YES | character varying
--
--   -- CHECK novo aceita CAMERA e continua recusando lixo:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'portaria.movimento'::regclass
--      AND conname = 'movimento_origem_check';
--
--   -- Nenhum movimento existente foi tocado (ALTER é aditivo):
--   SELECT count(*) FILTER (WHERE placa_lida_bruta IS NOT NULL) AS com_bruta,
--          count(*) AS total
--     FROM portaria.movimento;
--   -- esperado antes do primeiro dia com a câmera ligada: com_bruta = 0
-- ============================================================================

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- ⚠️ Só reverter o CHECK se nenhuma linha tiver origem = 'CAMERA' — senão o
-- ALTER abaixo falha (linha existente violaria o CHECK antigo).
-- ALTER TABLE portaria.movimento DROP CONSTRAINT IF EXISTS movimento_origem_check;
-- ALTER TABLE portaria.movimento ADD CONSTRAINT movimento_origem_check CHECK (
--     origem IN ('MANUAL','RETROATIVO','QR','TAG','LPR')
-- );
-- ALTER TABLE portaria.movimento DROP COLUMN IF EXISTS placa_lida_bruta;
-- ============================================================================
