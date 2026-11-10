/*
 * ao-vivo.entradas.js — botões "🛰 Ao vivo" (migration 053)
 * -------------------------------------------------------------------------------
 * Compartilhado por fiscal.html (linhas do turno aberto) e fiscal-painel.html
 * (todas as linhas monitoradas): um botão por linha monitorada ao vivo, que
 * abre ao-vivo.html?linha=<código>. Uma função só para as duas telas não
 * divergirem em silêncio.
 *
 * Nunca atrapalha a tela de origem: se /monitoramento/linhas falhar (rede,
 * permissão, backend sem o módulo), o container só fica escondido.
 */

import { apiGet } from './api.js';
import { escapeHtml } from './escape.js';

let cacheLinhas = null;

async function linhasMonitoradas() {
    if (!cacheLinhas) cacheLinhas = await apiGet('/monitoramento/linhas');
    return cacheLinhas;
}

/**
 * Preenche `container` com um link por linha monitorada. `filtro` (lista de
 * códigos canônicos, ex. as linhas do turno) limita às linhas que estão nele;
 * sem filtro, mostra todas.
 */
export async function renderEntradasAoVivo(container, filtro = null) {
    if (!container) return;
    let linhas;
    try {
        linhas = await linhasMonitoradas();
    } catch {
        container.hidden = true;
        return;
    }
    const permitidas = filtro ? new Set(filtro) : null;
    const visiveis = linhas.filter((l) => !permitidas || permitidas.has(l.codigo));
    container.innerHTML = visiveis.map((l) => `
        <a class="av-entrada" href="ao-vivo.html?linha=${encodeURIComponent(l.codigo)}"
           title="${escapeHtml(l.nome)}">🛰 Ao vivo ${escapeHtml(l.codigo)}</a>
    `).join('');
    container.hidden = visiveis.length === 0;
}
