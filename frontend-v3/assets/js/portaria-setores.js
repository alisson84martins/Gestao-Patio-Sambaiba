/*
 * portaria-setores.js — cache e select de setor de destino (P3/R4/R6)
 * -----------------------------------------------------------
 * Mesmo desenho de portaria-empresas.js — módulo único, importado por toda
 * tela que precisa do select de setor (⛔ não duplicar de novo: foi a
 * duplicação do cache de empresa que criou o P1). Sem cadastro aqui: a
 * lista de três setores é fechada (R6) e mantida só pelo banco.
 */
import { apiGet, ApiError } from './api.js';

let setoresCache = null;

export function invalidarCacheSetores() {
    setoresCache = null;
}

async function carregarSetores() {
    if (setoresCache) return setoresCache;
    try {
        setoresCache = await apiGet('/portaria/setores?apenas_ativos=true');
    } catch (err) {
        if (!(err instanceof ApiError && err.status === 401)) {
            console.error('[portaria-setores] erro ao carregar setores:', err);
        }
        setoresCache = [];
    }
    return setoresCache;
}

export function setorNomePorCodigo(codigo) {
    return (setoresCache || []).find((s) => s.codigo === codigo)?.nome || '';
}

// `placeholder` muda entre "Sem setor" (registro) e "Todo setor" (filtro de
// histórico) — mesmo select, dois contextos.
export async function preencherSelectSetores(selectId, { placeholder = 'Sem setor' } = {}) {
    const select = document.getElementById(selectId);
    const setores = await carregarSetores();
    select.innerHTML = `<option value="">${placeholder}</option>`;
    for (const s of setores) {
        const opt = document.createElement('option');
        opt.value = s.codigo;
        opt.textContent = s.nome;
        select.appendChild(opt);
    }
}
