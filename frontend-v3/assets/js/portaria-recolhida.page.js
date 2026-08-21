/*
 * portaria-recolhida.page.js — Tela do controlador para recolhida anormal
 * -----------------------------------------------------------
 * Ônibus que recolhe fora de hora, com defeito — no pior momento possível.
 * Quatro campos, poucos toques, sem formulário de várias seções (§2.6 do
 * prompt: "sem muita poluição visual, sem modelos de exemplo").
 *
 * 🔴 Regra do motorista (§2.3): esta tela NÃO mostra, não pede e não
 * devolve motorista nem cobrador — o backend resolve pela escala sozinho.
 * Nenhum campo de motorista/cobrador existe aqui de propósito.
 *
 * 🔴 Regra número um: POST /portaria/recolhidas sempre registra (201) —
 * prefixo não cadastrado, sem escala, ficha que não nasceu, nada disso
 * bloqueia o registro.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { escapeHtml } from './escape.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

// Rótulos na linguagem do catálogo real (tipo_defeito.categoria — ver
// database/seeds/04-tipos-defeito.sql), não um vocabulário inventado.
const CATEGORIAS = [
    { codigo: 'mecanica', label: 'Mecânica' },
    { codigo: 'eletrica', label: 'Elétrica' },
    { codigo: 'ar', label: 'Ar-condicionado' },
    { codigo: 'lataria', label: 'Lataria' },
    { codigo: 'pneus', label: 'Pneus' },
    { codigo: 'interno', label: 'Interno' },
    { codigo: 'outros', label: 'Outros' },
];

let tiposDefeitoCache = null;
let linhasCache = null;
let tipoSelecionado = null;   // { codigo, nome } do catálogo tipo_defeito
let linhaSelecionada = null;  // { codigo, nome } do catálogo linha

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

// ─── Carro/prefixo — mostra "cadastrado" sem bloquear o resto ─────────
function initPrefixo() {
    document.getElementById('rec-prefixo').addEventListener('blur', resolverPrefixo);
}

async function resolverPrefixo() {
    const prefixo = document.getElementById('rec-prefixo').value.trim();
    const status = document.getElementById('rec-prefixo-status');
    status.textContent = '';
    if (!prefixo) return;
    try {
        const resp = await apiGet(`/portaria/recolhidas/resolver-prefixo?prefixo=${encodeURIComponent(prefixo)}`);
        if (resp.encontrado) {
            status.textContent = resp.placa ? `Cadastrado — ${resp.placa}` : 'Cadastrado.';
            status.style.color = 'var(--accent3)';
        } else {
            status.textContent = 'Não cadastrado — registra assim mesmo.';
            status.style.color = 'var(--muted)';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-recolhida] erro ao resolver prefixo:', err);
    }
}

// ─── Linha — autocomplete, toque escolhe ────────────────────────────────
function initLinha() {
    const input = document.getElementById('rec-linha-busca');
    let handle = null;
    input.addEventListener('input', () => {
        linhaSelecionada = null;
        atualizarLinhaSelecionada();
        clearTimeout(handle);
        handle = setTimeout(() => renderSugestoesLinha(input.value.trim()), 150);
    });
    input.addEventListener('focus', () => renderSugestoesLinha(input.value.trim()));
}

function renderSugestoesLinha(termo) {
    const el = document.getElementById('rec-linha-sugestoes');
    const lista = (linhasCache || []).filter((l) => l.ativa);
    const filtradas = termo
        ? lista.filter((l) => `${l.codigo} ${l.nome}`.toLowerCase().includes(termo.toLowerCase()))
        : lista;
    el.innerHTML = '';
    for (const linha of filtradas.slice(0, 12)) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = linha.codigo;
        btn.title = linha.nome;
        btn.addEventListener('click', () => {
            linhaSelecionada = linha;
            document.getElementById('rec-linha-busca').value = linha.codigo;
            atualizarLinhaSelecionada();
            el.innerHTML = '';
        });
        el.appendChild(btn);
    }
}

function atualizarLinhaSelecionada() {
    document.getElementById('rec-linha-selecionada').textContent =
        linhaSelecionada ? linhaSelecionada.nome : '';
}

// ─── Defeito — dois toques: categoria, depois tipo ──────────────────────
function renderCategorias() {
    const el = document.getElementById('rec-categorias');
    el.innerHTML = '';
    for (const cat of CATEGORIAS) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = cat.label;
        btn.addEventListener('click', () => selecionarCategoria(cat.codigo, btn));
        el.appendChild(btn);
    }
}

function selecionarCategoria(codigo, btnAtivo) {
    document.querySelectorAll('#rec-categorias .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');

    tipoSelecionado = null;
    atualizarDefeitoSelecionado();

    const el = document.getElementById('rec-tipos');
    el.innerHTML = '';
    const tipos = (tiposDefeitoCache || []).filter((t) => t.categoria === codigo && t.ativo);
    for (const tipo of tipos) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = tipo.nome;
        btn.addEventListener('click', () => {
            tipoSelecionado = tipo;
            atualizarDefeitoSelecionado();
            el.querySelectorAll('.recolhida-chip').forEach((b) => b.classList.remove('active'));
            btn.classList.add('active');
        });
        el.appendChild(btn);
    }
}

function atualizarDefeitoSelecionado() {
    document.getElementById('rec-defeito-selecionado').textContent =
        tipoSelecionado ? tipoSelecionado.nome : '';
}

async function carregarCatalogos() {
    try {
        [tiposDefeitoCache, linhasCache] = await Promise.all([
            apiGet('/tipos-defeito'),
            apiGet('/linhas'),
        ]);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-recolhida] erro ao carregar catálogos:', err);
        tiposDefeitoCache = tiposDefeitoCache || [];
        linhasCache = linhasCache || [];
    }
    renderCategorias();
}

// ─── Registrar ───────────────────────────────────────────────────────────
function initRegistrar() {
    document.getElementById('btn-registrar-recolhida').addEventListener('click', registrar);
}

async function registrar() {
    const erro = document.getElementById('rec-erro');
    erro.style.display = 'none';

    const prefixo = document.getElementById('rec-prefixo').value.trim();
    if (!prefixo) {
        erro.textContent = 'Digite o carro.';
        erro.style.display = 'block';
        return;
    }
    if (!tipoSelecionado) {
        erro.textContent = 'Escolha o defeito.';
        erro.style.display = 'block';
        return;
    }

    const btn = document.getElementById('btn-registrar-recolhida');
    btn.disabled = true;
    try {
        await apiPost('/portaria/recolhidas', {
            prefixo,
            linha_codigo: linhaSelecionada ? linhaSelecionada.codigo : null,
            tipo_defeito_codigo: tipoSelecionado.codigo,
            relato: document.getElementById('rec-relato').value.trim() || null,
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
    document.getElementById('rec-prefixo').value = '';
    document.getElementById('rec-prefixo-status').textContent = '';
    document.getElementById('rec-linha-busca').value = '';
    document.getElementById('rec-linha-sugestoes').innerHTML = '';
    linhaSelecionada = null;
    atualizarLinhaSelecionada();
    document.querySelectorAll('#rec-categorias .recolhida-chip').forEach((b) => b.classList.remove('active'));
    document.getElementById('rec-tipos').innerHTML = '';
    tipoSelecionado = null;
    atualizarDefeitoSelecionado();
    document.getElementById('rec-relato').value = '';
    document.getElementById('rec-prefixo').focus();
}

// ─── Últimas recolhidas do próprio turno — só conferência, ⛔ sem motorista ──
function _statusTexto(r) {
    if (r.status === 'AGUARDANDO') return 'Aguardando avaliação';
    if (r.status === 'AVALIADA') {
        return r.avaliacao === 'LIBERADO' ? `Liberado (${r.prazo_minutos} min)` : 'Retido';
    }
    return 'Descartada';
}

async function carregarUltimas() {
    const el = document.getElementById('rec-lista-turno');
    const user = getCurrentUser();
    const meuId = user ? (user.funcionario_id || user.id) : null;
    try {
        const todas = await apiGet('/portaria/recolhidas');
        const minhas = todas.filter((r) => r.registrado_por === meuId).slice(0, 8);
        if (minhas.length === 0) {
            el.innerHTML = '<div class="oc-vazio">Nenhuma recolhida registrada ainda.</div>';
            return;
        }
        el.innerHTML = '';
        for (const r of minhas) {
            const div = document.createElement('div');
            div.className = 'portaria-item';
            div.style.marginBottom = '8px';
            div.style.cursor = 'default';
            const sub = [r.tipo_defeito_codigo, r.linha_codigo].filter(Boolean).join(' · ');
            div.innerHTML = `
                <div>
                    <div class="portaria-item-placa">${escapeHtml(r.prefixo)}</div>
                    <div class="portaria-item-sub">${escapeHtml(sub)}</div>
                </div>
                <div class="portaria-item-hora">${escapeHtml(_statusTexto(r))}</div>
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
initLinha();
initRegistrar();
carregarCatalogos();
carregarUltimas();
