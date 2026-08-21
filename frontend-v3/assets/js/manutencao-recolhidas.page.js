/*
 * manutencao-recolhidas.page.js — Fila de avaliação da manutenção (Bloco F)
 * -----------------------------------------------------------
 * Contador grande + fila AGUARDANDO primeiro, mais recente no topo. Tocar
 * num item abre a avaliação: LIBERADO (com prazo) ou RETIDO.
 *
 * A seção gerencial (motorista/carro/linha) só aparece pra quem tem
 * `recolhida_gerencial` — e só porque a API efetivamente devolveu esses
 * campos, nunca por decisão só de tela (§2.5): quem não tem o recurso
 * recebe 403 em /recolhidas/gerencial e a seção nem tenta carregar.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPatch, ApiError } from './api.js';
import { podeLer } from './sessao.js';
import { escapeHtml } from './escape.js';
import { POLLING_INTERVAL_MS } from './config.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

let pollHandle = null;
let recolhidaAtual = null;   // RecolhidaRead aberta no modal de avaliação
let avaliacaoEscolhida = null; // 'LIBERADO' | 'RETIDO'
let prazoMinutos = null;

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

function abrirModal(id) { document.getElementById(id).classList.add('open'); }
function fecharModal(id) { document.getElementById(id).classList.remove('open'); }

function fmtHora(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// ─── Fila + contador ─────────────────────────────────────────────────────
async function carregarFila() {
    try {
        const [contagem, pendentes] = await Promise.all([
            apiGet('/portaria/recolhidas/contagem-pendentes'),
            apiGet('/portaria/recolhidas/pendentes'),
        ]);
        document.getElementById('rec-contador-numero').textContent = String(contagem.total);
        renderPendentes(pendentes);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[manutencao-recolhidas] erro ao carregar fila:', err);
    }
}

function renderPendentes(itens) {
    const el = document.getElementById('rec-lista-pendentes');
    if (itens.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Nenhuma recolhida aguardando avaliação.</div>';
        return;
    }
    el.innerHTML = '';
    for (const r of itens) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'portaria-item';
        btn.style.marginBottom = '8px';
        const sub = [r.tipo_defeito_codigo, r.linha_codigo].filter(Boolean).join(' · ');
        btn.innerHTML = `
            <div>
                <div class="portaria-item-placa">${escapeHtml(r.prefixo)}</div>
                <div class="portaria-item-sub">${escapeHtml(sub)}${r.relato ? ' — ' + escapeHtml(r.relato) : ''}</div>
            </div>
            <div class="portaria-item-hora">${fmtHora(r.momento)}</div>
        `;
        btn.addEventListener('click', () => abrirAvaliacao(r));
        el.appendChild(btn);
    }
}

// ─── Avaliação ───────────────────────────────────────────────────────────
function abrirAvaliacao(recolhida) {
    recolhidaAtual = recolhida;
    avaliacaoEscolhida = null;
    prazoMinutos = null;

    document.getElementById('aval-prefixo').textContent = recolhida.prefixo;
    document.getElementById('aval-linha').textContent = recolhida.linha_codigo ? `Linha ${recolhida.linha_codigo}` : '';
    document.getElementById('aval-defeito').textContent = recolhida.tipo_defeito_codigo;
    document.getElementById('aval-relato').textContent = recolhida.relato || '';
    document.getElementById('aval-ficha').textContent = recolhida.ficha_id
        ? 'Ficha de manutenção aberta.'
        : (recolhida.ficha_falhou_motivo || 'Ficha não foi aberta automaticamente.');

    document.getElementById('aval-prazo-wrap').style.display = 'none';
    document.getElementById('aval-prazo-input').style.display = 'none';
    document.getElementById('aval-prazo-input').value = '';
    document.querySelectorAll('#aval-prazo-atalhos .recolhida-chip').forEach((b) => b.classList.remove('active'));
    document.getElementById('aval-observacao').value = '';
    document.getElementById('aval-erro').style.display = 'none';
    document.getElementById('btn-confirmar-avaliacao').style.display = 'none';

    abrirModal('modal-avaliacao');
}

function initAvaliacao() {
    document.getElementById('fechar-avaliacao').addEventListener('click', () => fecharModal('modal-avaliacao'));
    document.getElementById('btn-cancelar-avaliacao').addEventListener('click', () => fecharModal('modal-avaliacao'));

    document.getElementById('btn-liberado').addEventListener('click', () => {
        avaliacaoEscolhida = 'LIBERADO';
        document.getElementById('aval-prazo-wrap').style.display = 'block';
        document.getElementById('btn-confirmar-avaliacao').style.display = prazoMinutos ? 'block' : 'none';
    });
    document.getElementById('btn-retido').addEventListener('click', () => {
        avaliacaoEscolhida = 'RETIDO';
        document.getElementById('aval-prazo-wrap').style.display = 'none';
        document.getElementById('btn-confirmar-avaliacao').style.display = 'block';
    });

    document.getElementById('aval-prazo-atalhos').addEventListener('click', (e) => {
        const btn = e.target.closest('.recolhida-chip');
        if (!btn) return;
        document.querySelectorAll('#aval-prazo-atalhos .recolhida-chip').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const inputMin = document.getElementById('aval-prazo-input');
        if (btn.dataset.min === 'outro') {
            inputMin.style.display = 'block';
            inputMin.focus();
            prazoMinutos = null;
            document.getElementById('btn-confirmar-avaliacao').style.display = 'none';
        } else {
            inputMin.style.display = 'none';
            prazoMinutos = Number(btn.dataset.min);
            document.getElementById('btn-confirmar-avaliacao').style.display = 'block';
        }
    });

    document.getElementById('aval-prazo-input').addEventListener('input', (e) => {
        const valor = Number(e.target.value.trim());
        prazoMinutos = valor > 0 ? valor : null;
        document.getElementById('btn-confirmar-avaliacao').style.display = prazoMinutos ? 'block' : 'none';
    });

    document.getElementById('btn-confirmar-avaliacao').addEventListener('click', confirmarAvaliacao);
}

async function confirmarAvaliacao() {
    if (!recolhidaAtual || !avaliacaoEscolhida) return;
    const erro = document.getElementById('aval-erro');
    erro.style.display = 'none';

    const payload = {
        avaliacao: avaliacaoEscolhida,
        prazo_minutos: avaliacaoEscolhida === 'LIBERADO' ? prazoMinutos : null,
        avaliacao_relato: document.getElementById('aval-observacao').value.trim() || null,
    };

    const btn = document.getElementById('btn-confirmar-avaliacao');
    btn.disabled = true;
    try {
        await apiPatch(`/portaria/recolhidas/${recolhidaAtual.id}/avaliacao`, payload);
        fecharModal('modal-avaliacao');
        await carregarFila();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Visão gerencial (§2.5, §2.7) ────────────────────────────────────────
async function carregarGerencial() {
    if (!podeLer('recolhida_gerencial')) return;
    document.getElementById('secao-gerencial').style.display = 'block';
    try {
        const analise = await apiGet('/portaria/recolhidas/analise');
        renderAgregados(analise);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('gerencial-agregados').innerHTML =
            `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar análise: ${escapeHtml(err.message)}</div>`;
    }
}

function _tabela(titulo, itens) {
    if (!itens || itens.length === 0) {
        return `<div class="portaria-secao-titulo" style="margin-top:14px">${escapeHtml(titulo)}</div><div class="oc-vazio">Sem dados no período.</div>`;
    }
    const linhas = itens.map((i) => `
        <tr><td>${escapeHtml(i.chave)}</td><td>${i.total}</td></tr>
    `).join('');
    return `
        <div class="portaria-secao-titulo" style="margin-top:14px">${escapeHtml(titulo)}</div>
        <div class="portaria-tabela-wrap">
            <table class="portaria-tabela">
                <thead><tr><th>Chave</th><th>Total</th></tr></thead>
                <tbody>${linhas}</tbody>
            </table>
        </div>
    `;
}

function renderAgregados(analise) {
    const el = document.getElementById('gerencial-agregados');
    const tempo = analise.tempo_medio_avaliacao_minutos != null
        ? `<p class="portaria-ficha-linha">Tempo médio até a avaliação: <strong>${analise.tempo_medio_avaliacao_minutos} min</strong></p>`
        : '';
    el.innerHTML = tempo
        + _tabela('Por prefixo', analise.por_prefixo)
        + _tabela('Por linha', analise.por_linha)
        + _tabela('Por motorista', analise.por_motorista)
        + _tabela('Por tipo de defeito', analise.por_tipo_defeito)
        + _tabela('Por faixa de horário', analise.por_faixa_horario);
}

// ─── Polling ───────────────────────────────────────────────────────────
function startPolling() {
    if (pollHandle) return;
    pollHandle = setInterval(() => {
        if (document.querySelector('.modal-overlay.open')) return;
        carregarFila();
    }, POLLING_INTERVAL_MS);
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
initHeader();
initAvaliacao();
carregarFila();
carregarGerencial();
startPolling();
