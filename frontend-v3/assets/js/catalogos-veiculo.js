/*
 * catalogos-veiculo.js — Cores DENATRAN e siglas de UF
 * -----------------------------------------------------------------------------
 * Espelho de backend/app/core/veiculo.py (CORES_DENATRAN e UFS) — mesmo
 * padrão de mascaras.js ↔ app/core/placa.py. Mexeu num, mexe no outro:
 * duplicata diverge em silêncio.
 *
 * Cores = tabela DENATRAN/RENAVAM (a do CRLV e da NF-e, `cCorDENATRAN`),
 * 16 valores na ordem oficial. Valor fora da lista (ocorrência antiga com
 * "Prata metálico") continua aceito — o select mostra o valor antigo como
 * opção extra para não apagá-lo ao salvar.
 */

export const CORES_VEICULO = Object.freeze([
    'AMARELA', 'AZUL', 'BEGE', 'BRANCA', 'CINZA', 'DOURADA', 'GRENÁ', 'LARANJA',
    'MARROM', 'PRATA', 'PRETA', 'ROSA', 'ROXA', 'VERDE', 'VERMELHA', 'FANTASIA',
]);

export const UFS = Object.freeze([
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS',
    'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC',
    'SP', 'SE', 'TO',
]);
