/*
 * portaria-empresas.js — cadastro e cache de empresa terceira (D5)
 * -----------------------------------------------------------
 * P1 (08/09/2026): preencherSelectEmpresas + empresasCache viviam
 * duplicados em portaria.page.js e portaria-veiculos.page.js, cada um com
 * seu próprio cache independente — cadastrar numa tela não refletia na
 * outra sem F5. Módulo único, importado pelos dois.
 *
 * `incluirOutra` existe só pra quem precisa do caminho "empresa não
 * listada, sem travar o registro" (modal Terceiro) — o select de cadastro
 * de veículo (cad-empresa/nv-empresa) nunca usa, porque cadastrar veículo
 * TERCEIRO exige empresa_terceira_id de verdade (ck_veiculo_dono).
 */
import { apiGet, apiPost, ApiError } from './api.js';

let empresasCache = null;

export function invalidarCacheEmpresas() {
    empresasCache = null;
}

async function carregarEmpresas() {
    if (empresasCache) return empresasCache;
    try {
        empresasCache = await apiGet('/portaria/empresas?apenas_ativas=true');
    } catch (err) {
        if (!(err instanceof ApiError && err.status === 401)) {
            console.error('[portaria-empresas] erro ao carregar empresas:', err);
        }
        empresasCache = [];
    }
    return empresasCache;
}

export function empresaNomePorId(id) {
    return (empresasCache || []).find((e) => e.id === id)?.nome || '';
}

export async function preencherSelectEmpresas(selectId, { incluirOutra = false } = {}) {
    const select = document.getElementById(selectId);
    const empresas = await carregarEmpresas();
    select.innerHTML = '<option value="">Selecione…</option>';
    for (const emp of empresas) {
        const opt = document.createElement('option');
        opt.value = emp.id;
        opt.textContent = emp.nome;
        select.appendChild(opt);
    }
    if (incluirOutra) {
        const opt = document.createElement('option');
        opt.value = '__OUTRA__';
        opt.textContent = 'Outra / não listada — digitar';
        select.appendChild(opt);
    }
}

export async function cadastrarEmpresa({ nome, cnpj, observacao }) {
    const nova = await apiPost('/portaria/empresas', {
        nome,
        cnpj: cnpj || null,
        observacao: observacao || null,
    });
    // Recarrega do servidor (em vez de só empurrar `nova` no array local) —
    // é um cadastro raro, o custo de um GET extra é zero comparado ao risco
    // de duas cópias do cache divergirem.
    invalidarCacheEmpresas();
    await carregarEmpresas();
    return nova;
}
