/*
 * portaria-avaria.page.js — Tela do controlador para avaria na saída da frota
 * -----------------------------------------------------------
 * Bloco G: o controlador confere o carro saindo do pátio e vê um dano —
 * para-choque quebrado, retrovisor rachado, risco na lateral. Não é
 * recolhida (o carro está saindo, não voltando) e não é ocorrência (não
 * houve sinistro).
 *
 * Caso de uso número um: "esse risco já estava aí ontem?" — por isso o
 * campo do prefixo já mostra o histórico de 60 dias daquele carro assim
 * que o controlador sai do campo, antes mesmo de registrar nada novo.
 *
 * 🔴 Regra número um: POST /portaria/avarias sempre registra (201) — RE que
 * não resolve não bloqueia, prefixo não cadastrado não bloqueia.
 *
 * Mesmo esqueleto de portaria-recolhida.page.js (Blocos F+G), sem
 * linha/motivo/defeito — avaria de veículo qualquer, não só coletivo.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { buscarPorRe } from './identidade.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

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

// ─── Carro/prefixo — busca por carro: mostra o histórico de 60 dias ────
function initPrefixo() {
    document.getElementById('av-prefixo').addEventListener('blur', carregarHistoricoCarro);
}

function _linhaHistorico(a) {
    const sub = [a.motorista_re ? `RE ${a.motorista_re}` : null, a.motorista_nome].filter(Boolean).join(' · ');
    const div = document.createElement('div');
    div.className = 'portaria-item';
    div.style.marginBottom = '8px';
    div.style.cursor = 'default';
    div.innerHTML = `
        <div>
            <div class="portaria-item-placa">${escapeHtml(a.data_servico)}</div>
            <div class="portaria-item-sub">${escapeHtml(a.descricao)}${sub ? ' — ' + escapeHtml(sub) : ''}</div>
        </div>
    `;
    return div;
}

async function carregarHistoricoCarro() {
    const prefixo = document.getElementById('av-prefixo').value.trim();
    const status = document.getElementById('av-prefixo-status');
    const wrap = document.getElementById('av-historico-wrap');
    const lista = document.getElementById('av-historico-carro');
    status.textContent = '';
    if (!prefixo) {
        wrap.style.display = 'none';
        return;
    }
    wrap.style.display = 'block';
    lista.innerHTML = '<div class="patio-loading">Carregando…</div>';
    try {
        const avarias = await apiGet(`/portaria/avarias?prefixo=${encodeURIComponent(prefixo)}`);
        if (avarias.length === 0) {
            status.textContent = 'Nenhuma avaria nos últimos 60 dias.';
            status.style.color = 'var(--muted)';
            lista.innerHTML = '<div class="oc-vazio">Nenhuma avaria registrada nos últimos 60 dias.</div>';
            return;
        }
        status.textContent = `${avarias.length} avaria(s) nos últimos 60 dias — confira antes de liberar.`;
        status.style.color = '#f59e0b';
        lista.innerHTML = '';
        for (const a of avarias) {
            lista.appendChild(_linhaHistorico(a));
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        status.textContent = '';
        lista.innerHTML = `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

// ─── RE motorista — confirmação visual (§5.3), nunca bloqueia ──────────
function initIdentificacao() {
    document.getElementById('av-motorista-re').addEventListener('blur', resolverRe);
}

async function resolverRe() {
    const campoRe = document.getElementById('av-motorista-re');
    const status = document.getElementById('av-motorista-status');
    const campoNome = document.getElementById('av-motorista-nome');
    const re = campoRe.value.trim();
    status.textContent = '';
    if (re.length < 3) {
        campoNome.style.display = 'none';
        return;
    }
    try {
        const resp = await buscarPorRe(re);
        if (resp.encontrado) {
            campoNome.style.display = 'none';
            campoNome.value = '';
            if (resp.ativo === false) {
                status.textContent = `${resp.nome} — desligado/inativo. Registra assim mesmo.`;
                status.style.color = '#f59e0b';
            } else {
                status.textContent = resp.nome;
                status.style.color = 'var(--accent3)';
            }
        } else {
            status.textContent = 'Não encontrado — pode informar o nome.';
            status.style.color = 'var(--muted)';
            campoNome.style.display = 'block';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-avaria] erro ao resolver RE:', err);
    }
}

// ─── Registrar ───────────────────────────────────────────────────────────
function initRegistrar() {
    document.getElementById('btn-registrar-avaria').addEventListener('click', registrar);
}

async function registrar() {
    const erro = document.getElementById('av-erro');
    erro.style.display = 'none';

    const prefixo = document.getElementById('av-prefixo').value.trim();
    const descricao = document.getElementById('av-descricao').value.trim();
    if (!prefixo) {
        erro.textContent = 'Digite o carro.';
        erro.style.display = 'block';
        return;
    }
    if (!descricao) {
        erro.textContent = 'Descreva o que foi visto.';
        erro.style.display = 'block';
        return;
    }

    const btn = document.getElementById('btn-registrar-avaria');
    btn.disabled = true;
    try {
        await apiPost('/portaria/avarias', {
            prefixo,
            descricao,
            motorista_re: document.getElementById('av-motorista-re').value.trim() || null,
            motorista_nome: document.getElementById('av-motorista-nome').value.trim() || null,
        });
        limparFormulario();
        await carregarUltimas();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

function limparFormulario() {
    document.getElementById('av-prefixo').value = '';
    document.getElementById('av-prefixo-status').textContent = '';
    document.getElementById('av-historico-wrap').style.display = 'none';
    document.getElementById('av-motorista-re').value = '';
    document.getElementById('av-motorista-status').textContent = '';
    document.getElementById('av-motorista-nome').value = '';
    document.getElementById('av-motorista-nome').style.display = 'none';
    document.getElementById('av-descricao').value = '';
    document.getElementById('av-prefixo').focus();
}

// ─── Suas avarias do turno — só conferência do que você mesmo registrou ──
async function carregarUltimas() {
    const el = document.getElementById('av-lista-turno');
    const user = getCurrentUser();
    const meuId = user ? (user.funcionario_id || user.id) : null;
    try {
        const todas = await apiGet('/portaria/avarias');
        const minhas = todas.filter((a) => a.registrado_por === meuId).slice(0, 8);
        if (minhas.length === 0) {
            el.innerHTML = '<div class="oc-vazio">Nenhuma avaria registrada ainda.</div>';
            return;
        }
        el.innerHTML = '';
        for (const a of minhas) {
            const div = document.createElement('div');
            div.className = 'portaria-item';
            div.style.marginBottom = '8px';
            div.style.cursor = 'default';
            div.innerHTML = `
                <div>
                    <div class="portaria-item-placa">${escapeHtml(a.prefixo)}</div>
                    <div class="portaria-item-sub">${escapeHtml(a.descricao)}</div>
                </div>
            `;
            el.appendChild(div);
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        el.innerHTML = `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
initHeader();
initPrefixo();
initIdentificacao();
initRegistrar();
carregarUltimas();
