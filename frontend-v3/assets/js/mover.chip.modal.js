/*
 * Modal mover/remover ônibus alocado — Fase 5.3
 * -----------------------------------------------
 * Abre ao clicar num chip do pátio. Permite mover para outra
 * fila (via POST /alocacoes/bloco, sentido ida) ou remover a
 * alocação (via DELETE /alocacoes/{id}).
 *
 * Nota de implementação: "Manter posição atual" com troca de
 * linha também usa POST /alocacoes/bloco na mesma fila — o ônibus
 * vai para o final da fila como efeito colateral. Limitação
 * conhecida do MVP (a linha vem da Escala, não da Alocação).
 */

import { apiPost, apiDelete, ApiError } from './api.js';

// ─── Estado do módulo ────────────────────────────────────────────
let alocacaoAtual = null;   // { alocacaoId, frota, filaAtualId }
let _filasCache   = [];
let _onSuccess    = null;

// ─── INIT ────────────────────────────────────────────────────────
export function init({ onSuccess } = {}) {
    _onSuccess = onSuccess || null;

    document.getElementById('btn-mover-salvar').addEventListener('click', onSalvar);
    document.getElementById('btn-mover-remover').addEventListener('click', onRemover);
    document.getElementById('btn-mover-cancelar').addEventListener('click', fecharModalMoverChip);
    document.getElementById('modal-mover-fechar').addEventListener('click', fecharModalMoverChip);

    // Fechar ao clicar no overlay (fora do .modal)
    document.getElementById('modal-mover-chip').addEventListener('click', (e) => {
        if (e.target.id === 'modal-mover-chip') fecharModalMoverChip();
    });

    // Fechar com Esc
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') fecharModalMoverChip();
    });
}

// ─── ABRIR ───────────────────────────────────────────────────────
export function abrirModalMoverChip({ alocacaoId, frota, filaAtualId, linhaAtual, filasDisponiveis }) {
    alocacaoAtual = { alocacaoId, frota, filaAtualId };
    _filasCache   = filasDisponiveis || [];

    document.getElementById('modal-mover-titulo').textContent = `Veículo ${frota}`;
    document.getElementById('mover-linha').value = linhaAtual || '';

    // Popula select: "manter posição" + todas as filas (pré-seleciona a atual)
    const select = document.getElementById('mover-nova-posicao');
    let opts = '<option value="">— Manter posição atual —</option>';
    for (const fila of _filasCache) {
        const label = nomeFila(fila);
        const sel   = fila.fila_id === filaAtualId ? ' selected' : '';
        opts += `<option value="${fila.fila_id}"${sel}>${label}</option>`;
    }
    select.innerHTML = opts;

    document.getElementById('modal-mover-chip').classList.add('open');
    select.focus();
}

// ─── FECHAR ──────────────────────────────────────────────────────
export function fecharModalMoverChip() {
    const overlay = document.getElementById('modal-mover-chip');
    if (overlay) overlay.classList.remove('open');
    alocacaoAtual = null;
}

// ─── SALVAR ──────────────────────────────────────────────────────
async function onSalvar() {
    if (!alocacaoAtual) return;

    const novaFilaId = document.getElementById('mover-nova-posicao').value;
    const linha      = document.getElementById('mover-linha').value.trim();

    // "" → mantém na fila atual; caso contrário, muda pra fila selecionada
    const filaId = novaFilaId || alocacaoAtual.filaAtualId;
    const fila   = _filasCache.find(f => String(f.fila_id) === String(filaId));
    if (!fila) {
        alert('Fila não encontrada. Atualize a página e tente novamente.');
        return;
    }

    try {
        await apiPost('/alocacoes/bloco', {
            numero_frota: alocacaoAtual.frota,
            fila:         filaParaBloco(fila),
            linha_codigo: linha || null,
            sentido:      'ida',
        });
        fecharModalMoverChip();
        if (_onSuccess) await _onSuccess();
    } catch (err) {
        alert(err instanceof ApiError ? err.message : `Erro ao salvar: ${err.message}`);
    }
}

// ─── REMOVER ─────────────────────────────────────────────────────
async function onRemover() {
    if (!alocacaoAtual) return;
    if (!confirm(`Remover ônibus ${alocacaoAtual.frota} desta posição?`)) return;

    try {
        await apiDelete(`/alocacoes/${alocacaoAtual.alocacaoId}`);
        fecharModalMoverChip();
        if (_onSuccess) await _onSuccess();
    } catch (err) {
        alert(err instanceof ApiError ? err.message : `Erro ao remover: ${err.message}`);
    }
}

// ─── HELPERS ─────────────────────────────────────────────────────

/** Rótulo legível da fila para exibir no <select>. */
function nomeFila(fila) {
    if (fila.fila_tipo === 'NUMERICA' && fila.fila_numero != null) {
        return `Fila ${String(fila.fila_numero).padStart(2, '0')}`;
    }
    return fila.fila_nome || '—';
}

/** Valor amigável da fila para o endpoint POST /alocacoes/bloco.
 *  Numéricas: envia o número como string ("5"). Outras: envia o nome.
 */
function filaParaBloco(fila) {
    if (fila.fila_tipo === 'NUMERICA' && fila.fila_numero != null) {
        return String(fila.fila_numero);
    }
    return fila.fila_nome;
}
