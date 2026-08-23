/*
 * portaria-recolhida.page.js — Tela do controlador para recolhida anormal
 * -----------------------------------------------------------
 * Ônibus que recolhe fora de hora — poucos toques, sem formulário de várias
 * seções (§2.6 do prompt: "sem muita poluição visual, sem modelos de
 * exemplo").
 *
 * 🔴 §2.9-0 (correção de escopo): o controlador DIGITA RE motorista e RE
 * cobrador — ele está com o carro na frente, é a melhor fonte do dado. A
 * escala só SUGERE o RE do motorista (pré-preenchimento, nunca trava). O
 * que esta tela nunca mostra é o ACUMULADO — histórico, contagem por
 * motorista, dias anteriores — isso é recolhida_gerencial.
 *
 * 🔧 Bloco G: motivo vem antes do defeito — só motivo=DEFEITO mostra a
 * seção de categoria/tipo de defeito.
 *
 * 🔴 Regra número um: POST /portaria/recolhidas sempre registra (201) —
 * prefixo não cadastrado, sem escala, ficha que não nasceu, nada disso
 * bloqueia o registro.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { buscarPorRe } from './identidade.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

const CATEGORIAS = [
    { codigo: 'mecanica', label: 'Mecânica' },
    { codigo: 'eletrica', label: 'Elétrica' },
    { codigo: 'ar', label: 'Ar-condicionado' },
    { codigo: 'lataria', label: 'Lataria' },
    { codigo: 'pneus', label: 'Pneus' },
    { codigo: 'interno', label: 'Interno' },
    { codigo: 'outros', label: 'Outros' },
];

const MOTIVOS = [
    { codigo: 'DEFEITO', label: 'Defeito' },
    { codigo: 'COLISAO', label: 'Colisão' },
    { codigo: 'FALTA_MOTORISTA', label: 'Falta motorista' },
    { codigo: 'FALTA_COBRADOR', label: 'Falta cobrador' },
    { codigo: 'OUTRO', label: 'Outro' },
];

let tiposDefeitoCache = null;
let linhasCache = null;
let tipoSelecionado = null;    // { codigo, nome } do catálogo tipo_defeito
let linhaSelecionada = null;   // { codigo, nome } do catálogo linha
let motivoSelecionado = 'DEFEITO';

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

// ─── Carro/prefixo — mostra "cadastrado" e sugere o RE do motorista ────
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
        // §2.9-0: sugestão é só pré-preenchimento — nunca sobrescreve o que
        // o controlador já digitou.
        const campoRe = document.getElementById('rec-motorista-re');
        if (resp.motorista_re_sugerido && !campoRe.value.trim()) {
            campoRe.value = resp.motorista_re_sugerido;
            await resolverRe('motorista');
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-recolhida] erro ao resolver prefixo:', err);
    }
}

// ─── RE motorista/cobrador — confirmação visual (§5.3), nunca bloqueia ──
function initIdentificacao() {
    document.getElementById('rec-motorista-re').addEventListener('blur', () => resolverRe('motorista'));
    document.getElementById('rec-cobrador-re').addEventListener('blur', () => resolverRe('cobrador'));
}

async function resolverRe(papel) {
    const campoRe = document.getElementById(`rec-${papel}-re`);
    const status = document.getElementById(`rec-${papel}-status`);
    const campoNome = document.getElementById(`rec-${papel}-nome`);
    const re = campoRe.value.trim();
    status.textContent = '';
    if (re.length < 3) {
        campoNome.style.display = 'none';
        return;
    }
    try {
        // Bloco A2: busca por RE unificada (app/routers/identidade.py) —
        // antes cada tela chamava um endpoint próprio pra confirmar RE.
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
        console.error(`[portaria-recolhida] erro ao resolver RE (${papel}):`, err);
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

// ─── Motivo — 5 botões; só DEFEITO mostra a seção de defeito (Bloco G) ──
function renderMotivos() {
    const el = document.getElementById('rec-motivos');
    el.innerHTML = '';
    for (const motivo of MOTIVOS) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip' + (motivo.codigo === motivoSelecionado ? ' active' : '');
        btn.textContent = motivo.label;
        btn.addEventListener('click', () => selecionarMotivo(motivo.codigo, btn));
        el.appendChild(btn);
    }
    atualizarVisibilidadeDefeito();
}

function selecionarMotivo(codigo, btnAtivo) {
    motivoSelecionado = codigo;
    document.querySelectorAll('#rec-motivos .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');
    atualizarVisibilidadeDefeito();
}

function atualizarVisibilidadeDefeito() {
    document.getElementById('rec-defeito-campo').style.display =
        motivoSelecionado === 'DEFEITO' ? 'block' : 'none';
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
    if (motivoSelecionado === 'DEFEITO' && !tipoSelecionado) {
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
            motivo: motivoSelecionado,
            tipo_defeito_codigo: motivoSelecionado === 'DEFEITO' ? tipoSelecionado.codigo : null,
            relato: document.getElementById('rec-relato').value.trim() || null,
            motorista_re: document.getElementById('rec-motorista-re').value.trim() || null,
            motorista_nome: document.getElementById('rec-motorista-nome').value.trim() || null,
            cobrador_re: document.getElementById('rec-cobrador-re').value.trim() || null,
            cobrador_nome: document.getElementById('rec-cobrador-nome').value.trim() || null,
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
    document.getElementById('rec-motorista-re').value = '';
    document.getElementById('rec-motorista-status').textContent = '';
    document.getElementById('rec-motorista-nome').value = '';
    document.getElementById('rec-motorista-nome').style.display = 'none';
    document.getElementById('rec-cobrador-re').value = '';
    document.getElementById('rec-cobrador-status').textContent = '';
    document.getElementById('rec-cobrador-nome').value = '';
    document.getElementById('rec-cobrador-nome').style.display = 'none';
    motivoSelecionado = 'DEFEITO';
    renderMotivos();
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
            const sub = [r.motivo, r.tipo_defeito_codigo, r.linha_codigo].filter(Boolean).join(' · ');
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
initIdentificacao();
initLinha();
renderMotivos();
initRegistrar();
carregarCatalogos();
carregarUltimas();
