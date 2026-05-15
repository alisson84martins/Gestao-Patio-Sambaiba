/*
 * Tela do pátio — Frontend V3 da Sambaíba
 * ----------------------------------------
 * Responsabilidades:
 *   1. Proteger a rota (só usuário logado).
 *   2. Buscar GET /patio a cada POLLING_INTERVAL_MS e renderizar.
 *   3. Atualizar barra de stats (Frota / Alocados / Manutenção / Presos).
 *   4. Mostrar status do polling (bolinha verde/vermelha + timestamp).
 *   5. Tratar erros de rede sem parar o polling.
 *
 * Importante: NÃO hardcoda quantidade de filas. Itera sobre o payload —
 * quando o backend devolver mais filas (5.2.1: Noturno, Reservados,
 * Coqueirinho, Ilha), a UI se ajusta sozinha.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, ApiError } from './api.js';
import { POLLING_INTERVAL_MS } from './config.js';

// --- Protege a rota antes de qualquer coisa ---
if (!requireAuth()) {
    // requireAuth já redireciona se não autenticado; nada mais a fazer
    throw new Error('Sessao nao autenticada — interrompendo carga da pagina');
}

// --- Refs do DOM ---
const elUserName   = document.getElementById('user-name');
const elUserMeta   = document.getElementById('user-meta');
const elLogout     = document.getElementById('btn-logout');
const elGrid       = document.getElementById('patio-grid');
const elStatFrota      = document.getElementById('stat-frota');
const elStatAlocados   = document.getElementById('stat-alocados');
const elStatManutencao = document.getElementById('stat-manutencao');
const elStatPresos     = document.getElementById('stat-presos');
const elPollDot    = document.getElementById('poll-dot');
const elPollStatus = document.getElementById('poll-status');
const elPollTime   = document.getElementById('poll-time');

// --- Estado interno ---
let pollHandle = null;

// ============================================================
// HEADER E LOGOUT
// ============================================================
function setupHeader() {
    const user = getCurrentUser();
    if (user) {
        elUserName.textContent = user.nome || '—';
        elUserMeta.textContent =
            `${user.re || ''} · ${user.perfil || ''}`.toUpperCase();
    }
    elLogout.addEventListener('click', () => {
        stopPolling();
        logout();
        window.location.replace('index.html');
    });
}

// ============================================================
// FORMATAÇÃO
// ============================================================
function pad2(n) {
    return String(n).padStart(2, '0');
}

/**
 * "04:30:00" → "04:30". Aceita null/undefined.
 */
function formatHorario(hhmmss) {
    if (!hhmmss) return '';
    const [h, m] = hhmmss.split(':');
    return `${h}:${m}`;
}

/**
 * Retorna true se o horário de saída é ≥ 16h (preparando conceito de noturno).
 */
function isNoturno(hhmmss) {
    if (!hhmmss) return false;
    const hora = Number(hhmmss.split(':')[0]);
    return hora >= 16;
}

/**
 * Nome do cabeçalho da fila: numérica vira "01", "02"… especiais usam o nome.
 */
function nomeCabecalho(fila) {
    if (fila.fila_tipo === 'NUMERICA' && fila.fila_numero != null) {
        return pad2(fila.fila_numero);
    }
    return (fila.fila_nome || '—').toUpperCase();
}

/**
 * Título da seção visual (agrupa por tipo).
 */
function tituloSecao(tipo) {
    switch (tipo) {
        case 'NUMERICA':        return 'Filas';
        case 'ESPECIAL':        return 'Posições especiais';
        case 'ESPECIAL_REMOTA': return 'Fora da garagem';
        case 'MANUTENCAO':      return 'Manutenção';
        default:                return tipo;
    }
}

// ============================================================
// RENDER — CHIP DO ÔNIBUS
// ============================================================
function renderChip(onibus) {
    const chip = document.createElement('div');
    chip.className = 'onibus-chip';
    if (onibus.status_onibus === 'RESERVA') {
        chip.classList.add('status-reserva');
    }
    chip.title = `Frota ${onibus.numero_frota} · pos ${onibus.posicao}`;

    // Número de frota
    const frota = document.createElement('span');
    frota.className = 'chip-frota';
    frota.textContent = String(onibus.numero_frota);
    chip.appendChild(frota);

    // Badge de setor (E2/AR2)
    if (onibus.setor) {
        const setor = document.createElement('span');
        setor.className = `chip-setor setor-${onibus.setor.toLowerCase()}`;
        setor.textContent = onibus.setor;
        chip.appendChild(setor);
    }

    // Bloco com linha + horário
    const meta = document.createElement('div');
    meta.className = 'chip-meta';
    if (onibus.linha_codigo) {
        const linha = document.createElement('span');
        linha.className = 'chip-linha';
        linha.textContent = onibus.linha_codigo;
        meta.appendChild(linha);
    }
    if (onibus.horario_saida) {
        const horario = document.createElement('span');
        horario.className = 'chip-horario';
        if (isNoturno(onibus.horario_saida)) {
            horario.classList.add('noturno');
        }
        horario.textContent = formatHorario(onibus.horario_saida);
        meta.appendChild(horario);
    }
    if (meta.children.length > 0) chip.appendChild(meta);

    // Ícones de status (preso / ficha / pegadinha)
    const icons = document.createElement('div');
    icons.className = 'chip-icons';

    if (onibus.alerta_tipo === 'PRESO') {
        const i = document.createElement('span');
        i.className = 'chip-icon icon-preso';
        i.textContent = 'PR';
        i.title = 'Preso (alerta manual)';
        icons.appendChild(i);
    }
    if (onibus.ficha_status === 'ABERTA' || onibus.ficha_status === 'EM_ANDAMENTO') {
        const i = document.createElement('span');
        i.className = 'chip-icon icon-ficha';
        i.textContent = 'MA';
        i.title = `Ficha de manutenção ${onibus.ficha_status.toLowerCase()}`;
        icons.appendChild(i);
    }
    // Pegadinha — sempre presente mas oculta via CSS até existir o dado.
    const peg = document.createElement('span');
    peg.className = 'chip-icon icon-pegadinha';
    peg.textContent = 'PG';
    peg.title = 'Pegadinha — recolhe mais cedo';
    icons.appendChild(peg);

    if (icons.children.length > 0) chip.appendChild(icons);

    return chip;
}

// ============================================================
// RENDER — CARD DE FILA
// ============================================================
function renderFila(fila) {
    const card = document.createElement('div');
    card.className = 'fila-card';
    card.dataset.filaId = fila.fila_id;
    card.dataset.tipo = fila.fila_tipo;

    const header = document.createElement('div');
    header.className = 'fila-header';
    const numero = document.createElement('span');
    numero.className = 'fila-numero';
    numero.textContent = nomeCabecalho(fila);
    const contador = document.createElement('span');
    contador.className = 'fila-contador';
    const n = (fila.onibus || []).length;
    contador.textContent = n === 1 ? '1 carro' : `${n} carros`;
    header.appendChild(numero);
    header.appendChild(contador);
    card.appendChild(header);

    const body = document.createElement('div');
    body.className = 'fila-body';
    if (n === 0) {
        const vazia = document.createElement('div');
        vazia.className = 'fila-vazia';
        vazia.textContent = '— vazia —';
        body.appendChild(vazia);
    } else {
        for (const onibus of fila.onibus) {
            body.appendChild(renderChip(onibus));
        }
    }
    card.appendChild(body);

    return card;
}

// ============================================================
// RENDER — SEÇÕES AGRUPADAS POR TIPO
// ============================================================
function renderPatio(filas) {
    elGrid.innerHTML = '';
    if (!filas || filas.length === 0) {
        const aviso = document.createElement('div');
        aviso.className = 'patio-loading';
        aviso.textContent = 'Nenhuma fila configurada no sistema';
        elGrid.appendChild(aviso);
        return;
    }

    // Agrupa preservando a ordem em que o backend devolveu
    const grupos = new Map();
    for (const fila of filas) {
        if (!grupos.has(fila.fila_tipo)) {
            grupos.set(fila.fila_tipo, []);
        }
        grupos.get(fila.fila_tipo).push(fila);
    }

    for (const [tipo, lista] of grupos) {
        const section = document.createElement('section');
        section.className = 'patio-section';
        section.dataset.tipo = tipo;

        const title = document.createElement('h2');
        title.className = 'patio-section-title';
        title.textContent = tituloSecao(tipo);
        section.appendChild(title);

        const grid = document.createElement('div');
        grid.className = 'patio-grid';
        for (const fila of lista) {
            grid.appendChild(renderFila(fila));
        }
        section.appendChild(grid);

        elGrid.appendChild(section);
    }
}

// ============================================================
// STATS — calculados em cima do payload
// ============================================================
function updateStats(filas) {
    let frota = 0;
    let alocados = 0;
    let manutencao = 0;
    let presos = 0;

    for (const fila of filas || []) {
        const list = fila.onibus || [];
        const isRemota = fila.fila_tipo === 'ESPECIAL_REMOTA';
        const isManutencao = fila.fila_tipo === 'MANUTENCAO';

        // Frota no pátio = presença física (exclui remotas: carros rodando fora)
        if (!isRemota) frota += list.length;

        // Alocados (prontos pra sair) = nem manutenção, nem rodando fora
        if (!isRemota && !isManutencao) alocados += list.length;

        // Manutenção: só a fila MANUTENCAO
        if (isManutencao) manutencao += list.length;

        // Presos: alerta PRESO independente do tipo da fila (inclusive remotas)
        for (const o of list) {
            if (o.alerta_tipo === 'PRESO') presos++;
        }
    }

    elStatFrota.textContent      = frota;
    elStatAlocados.textContent   = alocados;
    elStatManutencao.textContent = manutencao;
    elStatPresos.textContent     = presos;
}

// ============================================================
// STATUS DO POLLING
// ============================================================
function markOnline() {
    elPollDot.classList.remove('offline');
    elPollDot.classList.add('online');
    elPollStatus.textContent = 'Online';
}

function markOffline(msg) {
    elPollDot.classList.remove('online');
    elPollDot.classList.add('offline');
    elPollStatus.textContent = msg || 'Sem conexão';
}

function stampNow() {
    const now = new Date();
    elPollTime.textContent =
        `${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}`;
}

// ============================================================
// LOOP PRINCIPAL
// ============================================================
async function loadPatio() {
    try {
        const filas = await apiGet('/patio');
        renderPatio(filas);
        updateStats(filas);
        markOnline();
        stampNow();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
            // o api.js já redireciona — só evita stack trace feia
            return;
        }
        console.error('[patio] erro ao carregar:', err);
        markOffline(err?.message || 'Erro ao carregar pátio');
        // não para o polling — tenta de novo na próxima tick
    }
}

function startPolling() {
    if (pollHandle) return;
    pollHandle = setInterval(loadPatio, POLLING_INTERVAL_MS);
}

function stopPolling() {
    if (pollHandle) {
        clearInterval(pollHandle);
        pollHandle = null;
    }
}

// ============================================================
// BOOT
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    setupHeader();
    loadPatio();
    startPolling();
});

// Para evitar polling fantasma se o usuário fechar a aba
window.addEventListener('beforeunload', stopPolling);
