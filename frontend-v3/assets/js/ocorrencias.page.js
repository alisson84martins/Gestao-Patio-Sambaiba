/*
 * ocorrencias.page.js — Lista do Relatório de Ocorrências
 * -----------------------------------------------------------
 * Página inicial do módulo Coordenadoria. Filtra por período, tipo,
 * prefixo, linha e status; cada linha mostra os indicadores de quantas
 * vítimas/testemunhas/anexos a ocorrência tem (vindos de
 * coordenadoria.vw_ocorrencia_resumo via GET /ocorrencias).
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, ApiError } from './api.js';
import { podeEscrever } from './sessao.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

const LIMITE_PAGINA = 30;

let paginaAtual = 0;
let totalRegistros = 0;
let catalogoTipos = [];

// ─── Header ─────────────────────────────────────────────────────────────
function initHeader() {
    const user = getCurrentUser();
    if (user) {
        document.getElementById('user-name').textContent = user.nome || '—';
        document.getElementById('user-meta').textContent = (user.re || '—').toUpperCase();
    }
    document.getElementById('btn-logout').addEventListener('click', () => {
        logout();
        window.location.replace('index.html');
    });
}

// ─── Catálogos (tipos para o select de filtro) ─────────────────────────
async function carregarCatalogos() {
    try {
        const dados = await apiGet('/ocorrencias/catalogos');
        catalogoTipos = dados.tipos || [];
        const select = document.getElementById('filtro-tipo');
        for (const tipo of catalogoTipos) {
            const opt = document.createElement('option');
            opt.value = tipo.codigo;
            opt.textContent = tipo.nome;
            select.appendChild(opt);
        }
    } catch (err) {
        console.error('[ocorrencias] erro ao carregar catálogos:', err);
    }
}

// ─── Filtros ────────────────────────────────────────────────────────────
function lerFiltros() {
    return {
        data_inicio: document.getElementById('filtro-data-inicio').value || null,
        data_fim: document.getElementById('filtro-data-fim').value || null,
        tipo: document.getElementById('filtro-tipo').value || null,
        prefixo: document.getElementById('filtro-prefixo').value.trim() || null,
        linha: document.getElementById('filtro-linha').value.trim() || null,
        status: document.getElementById('filtro-status').value || null,
    };
}

function montarQueryString(filtros) {
    const params = new URLSearchParams();
    for (const [chave, valor] of Object.entries(filtros)) {
        if (valor) params.set(chave, valor);
    }
    params.set('skip', String(paginaAtual * LIMITE_PAGINA));
    params.set('limit', String(LIMITE_PAGINA));
    return params.toString();
}

// ─── Carregar e renderizar lista ────────────────────────────────────────
async function carregarLista() {
    const secao = document.getElementById('ocorrencias-lista');
    secao.innerHTML = '<div class="patio-loading">Carregando…</div>';
    try {
        const qs = montarQueryString(lerFiltros());
        const resp = await apiGet(`/ocorrencias?${qs}`);
        totalRegistros = resp.total || 0;
        renderLista(resp.itens || []);
        renderResumo();
        atualizarPaginacao();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        secao.innerHTML = `<div class="patio-loading" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
        console.error('[ocorrencias] erro ao carregar lista:', err);
    }
}

function renderResumo() {
    const el = document.getElementById('ocorrencias-resumo');
    if (totalRegistros === 0) {
        el.textContent = '';
        return;
    }
    const inicio = paginaAtual * LIMITE_PAGINA + 1;
    const fim = Math.min(totalRegistros, inicio + LIMITE_PAGINA - 1);
    el.textContent = `${inicio}–${fim} de ${totalRegistros} ocorrências`;
}

function atualizarPaginacao() {
    document.getElementById('btn-pagina-anterior').disabled = paginaAtual === 0;
    document.getElementById('btn-proxima-pagina').disabled = (paginaAtual + 1) * LIMITE_PAGINA >= totalRegistros;
}

function fmtData(iso) {
    if (!iso) return '—';
    const [ano, mes, dia] = iso.split('-');
    return `${dia}/${mes}/${ano}`;
}

function fmtHora(hhmmss) {
    if (!hhmmss) return '—';
    const [h, m] = hhmmss.split(':');
    return `${h}:${m}`;
}

function badgeStatus(status) {
    const mapa = {
        RASCUNHO: ['Rascunho', 'amarelo'],
        FINALIZADA: ['Finalizada', 'verde'],
        CANCELADA: ['Cancelada', 'cinza'],
    };
    const [label, cor] = mapa[status] || [status, 'cinza'];
    const cores = {
        verde: 'background:#1a3a1a;color:#4caf50',
        cinza: 'background:#2a2a2a;color:#888',
        amarelo: 'background:#3a2e00;color:#ffd600',
        vermelho: 'background:#3a1a1a;color:#f44336',
    };
    return `<span style="${cores[cor]};padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:700">${label}</span>`;
}

function indicador(icone, qtd, titulo) {
    if (!qtd) return '';
    return `<span title="${titulo}" style="margin-right:8px;white-space:nowrap">${icone} ${qtd}</span>`;
}

function renderLista(itens) {
    const secao = document.getElementById('ocorrencias-lista');

    if (!itens || itens.length === 0) {
        secao.innerHTML = '<div class="patio-loading">Nenhuma ocorrência encontrada com esses filtros.</div>';
        return;
    }

    const linhas = itens.map(item => `
        <tr data-id="${item.id}" style="border-bottom:1px solid var(--surface2);cursor:pointer"
            onmouseover="this.style.background='var(--surface2)'"
            onmouseout="this.style.background=''">
            <td style="padding:8px 12px;font-family:var(--mono);white-space:nowrap">#${item.numero}</td>
            <td style="padding:8px 12px;white-space:nowrap">${fmtData(item.data_ocorrencia)} ${fmtHora(item.hora_ocorrencia)}</td>
            <td style="padding:8px 12px">${escapeHtml(item.tipo_nome)}</td>
            <td style="padding:8px 12px;font-family:var(--mono)">${escapeHtml(item.prefixo)}</td>
            <td style="padding:8px 12px">${escapeHtml(item.linha_codigo || '—')}</td>
            <td style="padding:8px 12px">${escapeHtml(item.bairro || '—')}</td>
            <td style="padding:8px 12px;white-space:nowrap">
                ${indicador('🚑', item.qtd_vitimas, 'Vítimas')}
                ${indicador('👥', item.qtd_testemunhas, 'Testemunhas')}
                ${indicador('📎', item.qtd_anexos, 'Anexos')}
            </td>
            <td style="padding:8px 12px">${badgeStatus(item.status)}</td>
        </tr>
    `).join('');

    secao.innerHTML = `
        <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:0.875rem">
            <thead>
                <tr style="background:var(--surface2);text-align:left">
                    <th style="padding:8px 12px;white-space:nowrap">Nº</th>
                    <th style="padding:8px 12px;white-space:nowrap">Data/Hora</th>
                    <th style="padding:8px 12px">Tipo</th>
                    <th style="padding:8px 12px">Prefixo</th>
                    <th style="padding:8px 12px">Linha</th>
                    <th style="padding:8px 12px">Bairro</th>
                    <th style="padding:8px 12px">Indicadores</th>
                    <th style="padding:8px 12px">Status</th>
                </tr>
            </thead>
            <tbody>${linhas}</tbody>
        </table>
        </div>`;

    secao.querySelectorAll('tr[data-id]').forEach(tr => {
        tr.addEventListener('click', () => {
            window.location.href = `ocorrencia-form.html?id=${tr.dataset.id}`;
        });
    });
}

function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ─── Filtros: listeners ──────────────────────────────────────────────────
function initFiltros() {
    const ids = ['filtro-data-inicio', 'filtro-data-fim', 'filtro-tipo', 'filtro-status'];
    ids.forEach(id => document.getElementById(id).addEventListener('change', () => {
        paginaAtual = 0;
        carregarLista();
    }));

    let timeoutBusca = null;
    ['filtro-prefixo', 'filtro-linha'].forEach(id => {
        document.getElementById(id).addEventListener('input', () => {
            clearTimeout(timeoutBusca);
            timeoutBusca = setTimeout(() => {
                paginaAtual = 0;
                carregarLista();
            }, 400);
        });
    });

    document.getElementById('btn-limpar-filtros').addEventListener('click', () => {
        ids.forEach(id => { document.getElementById(id).value = ''; });
        document.getElementById('filtro-prefixo').value = '';
        document.getElementById('filtro-linha').value = '';
        paginaAtual = 0;
        carregarLista();
    });

    document.getElementById('btn-pagina-anterior').addEventListener('click', () => {
        if (paginaAtual > 0) {
            paginaAtual -= 1;
            carregarLista();
        }
    });
    document.getElementById('btn-proxima-pagina').addEventListener('click', () => {
        paginaAtual += 1;
        carregarLista();
    });
}

// ─── Boot ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    initHeader();
    initFiltros();

    const btnNova = document.getElementById('btn-nova-ocorrencia');
    if (podeEscrever('ocorrencia')) {
        btnNova.addEventListener('click', () => {
            window.location.href = 'ocorrencia-form.html';
        });
    } else {
        btnNova.style.display = 'none';
    }

    await carregarCatalogos();
    await carregarLista();
});
