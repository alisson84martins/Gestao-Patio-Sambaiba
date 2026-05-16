/*
 * Painel Registrar Filas — modo bloco — Fase 5.3
 * ------------------------------------------------
 * Módulo ES isolado: sem dependência de estado global de patio.page.js.
 * Estado local: blocoSentido, historicoLocal.
 *
 * Exceções V3 vs V2 (decisões 6.8 e 6.9):
 *   - Input "fila" é texto simples, sem autocomplete popup.
 *   - Após Marcar: limpa prefixo + linha, mantém fila, foca prefixo.
 */

import { apiGet, apiPost, apiDelete, ApiError } from './api.js';

// ─── Estado do módulo ────────────────────────────────────────────
let blocoSentido = 'ida';

// [{ alocacao_id, frota, fila, linha, pos }] — usado pra Desfazer e status
let historicoLocal = [];

let _onSuccess = null;
let _getFilasSnapshot = null;

// ─── INIT ────────────────────────────────────────────────────────
/**
 * @param {object} opts
 * @param {() => Promise<void>} opts.onSuccess   — chamado após Marcar/Desfazer com sucesso
 * @param {() => Array}         opts.getFilasSnapshot — retorna o último payload GET /patio
 */
export function init({ onSuccess, getFilasSnapshot } = {}) {
    _onSuccess = onSuccess || null;
    _getFilasSnapshot = getFilasSnapshot || null;

    const btnIda      = document.getElementById('btn-ida');
    const btnVolta    = document.getElementById('btn-volta');
    const btnMarcar   = document.getElementById('btn-marcar');
    const btnDesfazer = document.getElementById('btn-desfazer');
    const inputCarro  = document.getElementById('bloco-carro');
    const inputFila   = document.getElementById('bloco-fila-input');
    const inputLinha  = document.getElementById('bloco-linha');

    btnIda.addEventListener('click', () => setSentido('ida'));
    btnVolta.addEventListener('click', () => setSentido('volta'));
    btnMarcar.addEventListener('click', onMarcar);
    btnDesfazer.addEventListener('click', onDesfazer);

    inputCarro.addEventListener('keydown', e => {
        if (e.key === 'Enter') inputFila.focus();
    });
    inputFila.addEventListener('input', atualizarStatusBloco);
    inputFila.addEventListener('keydown', e => {
        if (e.key === 'Enter') inputLinha.focus();
    });
    inputLinha.addEventListener('keydown', e => {
        if (e.key === 'Enter') onMarcar();
    });

    popularLinhas();
    atualizarStatusBloco();
}

// ─── SENTIDO ─────────────────────────────────────────────────────
function setSentido(s) {
    blocoSentido = s;
    document.getElementById('btn-ida').classList.toggle('active', s === 'ida');
    document.getElementById('btn-volta').classList.toggle('active', s === 'volta');
    atualizarStatusBloco();
}

// ─── STATUS BAR ──────────────────────────────────────────────────
function atualizarStatusBloco() {
    const filaInput = document.getElementById('bloco-fila-input')?.value.trim() || '';
    const statusEl  = document.getElementById('bloco-status');
    if (!statusEl) return;
    if (!filaInput) { statusEl.textContent = ''; return; }

    let nCarros = 0;

    if (_getFilasSnapshot) {
        const filas = _getFilasSnapshot();
        const fila = encontrarFilaNoSnapshot(filas, filaInput);
        if (fila) nCarros = (fila.onibus || []).length;
    } else {
        // fallback: conta histórico local desta sessão para a fila digitada
        nCarros = historicoLocal.filter(
            h => h.fila.toLowerCase() === filaInput.toLowerCase()
        ).length;
    }

    const proxPos      = blocoSentido === 'ida' ? nCarros + 1 : 1;
    const sentidoLabel = blocoSentido === 'ida' ? '→' : '←';
    statusEl.textContent = `${filaInput} ${sentidoLabel} · ${nCarros} carros · próxima pos.${proxPos}`;
}

/** Localiza a fila no snapshot pelo input amigável (número ou nome). */
function encontrarFilaNoSnapshot(filas, input) {
    if (!filas || !filas.length) return null;
    const val = input.trim();
    if (/^\d+$/.test(val)) {
        return filas.find(f => f.fila_tipo === 'NUMERICA' && String(f.fila_numero) === val) || null;
    }
    return filas.find(f => (f.fila_nome || '').toLowerCase() === val.toLowerCase()) || null;
}

// ─── MARCAR ──────────────────────────────────────────────────────
async function onMarcar() {
    const inputCarro = document.getElementById('bloco-carro');
    const inputFila  = document.getElementById('bloco-fila-input');
    const inputLinha = document.getElementById('bloco-linha');

    const frota = inputCarro.value.trim();
    const fila  = inputFila.value.trim();
    const linha = inputLinha.value.trim();

    if (!frota) { inputCarro.focus(); return; }
    if (!fila)  { inputFila.focus();  return; }

    try {
        const resultado = await apiPost('/alocacoes/bloco', {
            numero_frota: Number(frota),
            fila,
            linha_codigo: linha || null,
            sentido: blocoSentido,
        });

        historicoLocal.push({
            alocacao_id: resultado.id,
            frota,
            fila,
            linha: linha || '',
            pos: resultado.posicao,
        });

        // Limpa prefixo + linha, mantém fila, foca prefixo
        inputCarro.value = '';
        inputLinha.value = '';
        inputCarro.focus();

        renderHistorico();
        if (_onSuccess) await _onSuccess();
        atualizarStatusBloco();
    } catch (err) {
        alert(err instanceof ApiError ? err.message : `Erro ao registrar: ${err.message}`);
    }
}

// ─── DESFAZER ────────────────────────────────────────────────────
async function onDesfazer() {
    if (!historicoLocal.length) return;
    const ultimo = historicoLocal[historicoLocal.length - 1];

    if (!confirm(`Remover o último carro marcado (frota ${ultimo.frota})?`)) return;

    try {
        await apiDelete(`/alocacoes/${ultimo.alocacao_id}`);
        historicoLocal.pop();
        renderHistorico();
        if (_onSuccess) await _onSuccess();
        atualizarStatusBloco();
    } catch (err) {
        alert(err instanceof ApiError ? err.message : `Erro ao desfazer: ${err.message}`);
    }
}

// ─── RENDER HISTÓRICO ────────────────────────────────────────────
function renderHistorico() {
    const container = document.getElementById('bloco-historico');
    if (!container) return;
    if (!historicoLocal.length) { container.innerHTML = ''; return; }

    // Último marcado no topo (ordem reversa)
    container.innerHTML = [...historicoLocal].reverse().map(h => `
        <div class="bloco-item">
            <span class="bloco-item-pos">Pos.${h.pos}</span>
            <span class="bloco-item-frota">${h.frota}</span>
            <span class="bloco-item-linha">${h.linha ? 'L.' + h.linha : '—'}</span>
        </div>`).join('');
}

// ─── POPULAR DATALIST DE LINHAS ──────────────────────────────────
async function popularLinhas() {
    const dl = document.getElementById('lista-linhas-bloco');
    if (!dl) return;
    try {
        const linhas = await apiGet('/linhas?limit=200');
        dl.innerHTML = linhas
            .map(l => l.codigo)
            .filter(Boolean)
            .sort()
            .map(c => `<option value="${c}">`)
            .join('');
    } catch {
        // endpoint indisponível ou sem linhas cadastradas: datalist fica vazio
    }
}
