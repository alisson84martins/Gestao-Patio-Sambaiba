/*
 * portaria-consulta.page.js — Histórico de movimentos com filtros
 * -----------------------------------------------------------
 * GET /portaria/movimentos não devolve total nem paginação com contagem
 * (é uma lista simples, ver routers/portaria.py::listar_movimentos) — a
 * paginação aqui é por offset/limit "cego": Próxima fica habilitada
 * enquanto a página vier cheia (LIMITE_PAGINA itens).
 *
 * "Exportar CSV" e o atalho "Sem saída há 24h+" são só do frontend — sem
 * endpoint dedicado no backend, mesmo padrão de exportação de menu.js
 * (BOM + `;` + aspas, pra abrir certo no Excel em pt-BR).
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { aplicarMascara } from './mascaras.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

const LIMITE_PAGINA = 50;
let paginaAtual = 0;
let modoSemSaida = false;

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

// ─── Filtros ────────────────────────────────────────────────────────────
function lerFiltros() {
    return {
        data_inicio: document.getElementById('filtro-data-inicio').value || null,
        data_fim: document.getElementById('filtro-data-fim').value || null,
        placa: document.getElementById('filtro-placa').value.trim() || null,
        re: document.getElementById('filtro-re').value.trim() || null,
        propriedade: document.getElementById('filtro-propriedade').value || null,
        sentido: document.getElementById('filtro-sentido').value || null,
        apenas_nao_cadastrados: document.getElementById('filtro-nao-cadastrados').checked || null,
        apenas_suspensos: document.getElementById('filtro-suspensos').checked || null,
    };
}

function montarQueryString(filtros, { paginado = true } = {}) {
    const params = new URLSearchParams();
    for (const [chave, valor] of Object.entries(filtros)) {
        if (valor !== null && valor !== undefined && valor !== false) params.set(chave, String(valor));
    }
    if (paginado) {
        params.set('skip', String(paginaAtual * LIMITE_PAGINA));
        params.set('limit', String(LIMITE_PAGINA));
    } else {
        params.set('limit', '5000');
    }
    return params.toString();
}

function initFiltros() {
    // A1: só uppercase, sem agrupar nem exigir placa completa (é filtro
    // parcial) — mesma regra da máscara, importada de mascaras.js.
    aplicarMascara(document.getElementById('filtro-placa'), 'placa-parcial');

    // Mexer em qualquer filtro sai do atalho "sem saída há 24h+" — senão a
    // mudança de filtro parece não fazer nada, porque a lista continua
    // presa no /alertas/sem-saida.
    const recarregarDaPagina0 = () => { modoSemSaida = false; paginaAtual = 0; carregarLista(); };
    ['filtro-data-inicio', 'filtro-data-fim', 'filtro-propriedade', 'filtro-sentido',
        'filtro-nao-cadastrados', 'filtro-suspensos'].forEach(id => {
        document.getElementById(id).addEventListener('change', recarregarDaPagina0);
    });
    let handlePlaca = null;
    document.getElementById('filtro-placa').addEventListener('input', () => {
        clearTimeout(handlePlaca);
        handlePlaca = setTimeout(recarregarDaPagina0, 300);
    });
    let handleRe = null;
    document.getElementById('filtro-re').addEventListener('input', () => {
        clearTimeout(handleRe);
        handleRe = setTimeout(recarregarDaPagina0, 300);
    });

    document.getElementById('btn-limpar-filtros').addEventListener('click', () => {
        document.getElementById('filtro-data-inicio').value = '';
        document.getElementById('filtro-data-fim').value = '';
        document.getElementById('filtro-placa').value = '';
        document.getElementById('filtro-re').value = '';
        document.getElementById('filtro-propriedade').value = '';
        document.getElementById('filtro-sentido').value = '';
        document.getElementById('filtro-nao-cadastrados').checked = false;
        document.getElementById('filtro-suspensos').checked = false;
        modoSemSaida = false;
        recarregarDaPagina0();
    });

    document.getElementById('btn-sem-saida-24h').addEventListener('click', () => {
        modoSemSaida = true;
        paginaAtual = 0;
        carregarLista();
    });

    document.getElementById('btn-pagina-anterior').addEventListener('click', () => {
        if (paginaAtual === 0) return;
        paginaAtual -= 1;
        carregarLista();
    });
    document.getElementById('btn-proxima-pagina').addEventListener('click', () => {
        paginaAtual += 1;
        carregarLista();
    });
}

// ─── Carregar e renderizar ──────────────────────────────────────────────
async function carregarLista() {
    const corpo = document.getElementById('consulta-corpo');
    const erro = document.getElementById('consulta-erro');
    erro.style.display = 'none';
    corpo.innerHTML = '<tr><td colspan="7" class="oc-vazio">Carregando…</td></tr>';
    try {
        let dados;
        if (modoSemSaida) {
            dados = await apiGet('/portaria/alertas/sem-saida?horas=24');
        } else {
            const qs = montarQueryString(lerFiltros());
            dados = await apiGet(`/portaria/movimentos?${qs}`);
        }
        renderTabela(dados);
        renderResumo(dados.length);
        atualizarPaginacao(dados.length);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        corpo.innerHTML = '';
        erro.textContent = 'Erro ao carregar: ' + err.message;
        erro.style.display = 'block';
    }
}

function renderResumo(qtd) {
    const el = document.getElementById('consulta-resumo');
    if (modoSemSaida) {
        el.textContent = `${qtd} movimento(s) com entrada há mais de 24h sem saída registrada.`;
        return;
    }
    if (qtd === 0) {
        el.textContent = 'Nenhum movimento nesta página.';
        return;
    }
    const inicio = paginaAtual * LIMITE_PAGINA + 1;
    el.textContent = `${inicio}–${inicio + qtd - 1} · página ${paginaAtual + 1}`;
}

function atualizarPaginacao(qtd) {
    document.getElementById('btn-pagina-anterior').disabled = modoSemSaida || paginaAtual === 0;
    document.getElementById('btn-proxima-pagina').disabled = modoSemSaida || qtd < LIMITE_PAGINA;
}

function fmtDataHora(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function renderTabela(movimentos) {
    const corpo = document.getElementById('consulta-corpo');
    if (movimentos.length === 0) {
        corpo.innerHTML = '<tr><td colspan="7" class="oc-vazio">Nenhum movimento encontrado.</td></tr>';
        return;
    }
    corpo.innerHTML = movimentos.map(m => `
        <tr>
            <td>${fmtDataHora(m.momento)}</td>
            <td>${m.sentido === 'ENTRADA' ? 'Entrada' : 'Saída'}</td>
            <td>${escapeHtml(m.placa_registrada)}${m.cadastrado ? '' : ' <span style="color:var(--muted)">(avulso)</span>'}</td>
            <td>${escapeHtml(m.re_registrado || '—')}</td>
            <td>${escapeHtml(m.nome_registrado || m.terceiro_nome || '—')}</td>
            <td>${escapeHtml(m.origem)}</td>
            <td>${escapeHtml(m.observacao || '')}</td>
        </tr>
    `).join('');
}

// ─── Exportar CSV — mesmo padrão de menu.js (BOM + ; + aspas) ──────────
async function exportarCsv() {
    const btn = document.getElementById('btn-exportar-csv');
    btn.disabled = true;
    try {
        const qs = montarQueryString(lerFiltros(), { paginado: false });
        const dados = await apiGet(`/portaria/movimentos?${qs}`);

        const cabecalho = ['Data/hora', 'Sentido', 'Placa', 'Cadastrado', 'RE', 'Nome', 'Origem', 'Observação'];
        const linhas = dados.map(m => [
            fmtDataHora(m.momento),
            m.sentido === 'ENTRADA' ? 'Entrada' : 'Saída',
            m.placa_registrada,
            m.cadastrado ? 'Sim' : 'Não',
            m.re_registrado || '',
            m.nome_registrado || m.terceiro_nome || '',
            m.origem,
            m.observacao || '',
        ]);

        const bom = '﻿';
        const csv = bom + [cabecalho, ...linhas]
            .map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(';'))
            .join('\r\n');

        const nomeArquivo = `portaria-movimentos_${new Date().toLocaleDateString('pt-BR').replace(/\//g, '-')}.csv`;
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = nomeArquivo;
        a.click();
        URL.revokeObjectURL(url);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        const erro = document.getElementById('consulta-erro');
        erro.textContent = 'Erro ao exportar: ' + err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
initHeader();
initFiltros();
document.getElementById('btn-exportar-csv').addEventListener('click', exportarCsv);
carregarLista();
