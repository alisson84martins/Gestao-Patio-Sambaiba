/*
 * Tela de Remanejamento — Frontend V3
 * -------------------------------------
 * Consome GET /patio/remanejamento (Fase 5.4).
 * Lista ônibus em manutenção que têm escala no dia corrente,
 * ordenados por horário de saída (ordenação feita pelo backend).
 *
 * Filtros disponíveis: todos | urgente (<1h) | atrasado
 * "Marcar como resolvido": descarte local na sessão (dismiss Set).
 *   — Na próxima recarga o item reaparece caso ainda esteja na base.
 *   — Um endpoint dedicado pode ser adicionado numa fase futura.
 *
 * Polling: 30 s (mesmo intervalo do patio.page.js).
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, ApiError } from './api.js';

// Guard de sessão — redireciona pra login se não autenticado
if (!requireAuth()) throw new Error('Não autenticado');

/* ------------------------------------------------------------------ */
/*  ESTADO LOCAL                                                        */
/* ------------------------------------------------------------------ */

/** IDs (onibus_id) marcados como resolvidos nesta sessão */
const descartados = new Set();

/** Último snapshot da API */
let ultimaLista = [];

/** Filtro ativo: 'todos' | 'urgente' | 'atrasado' */
let filtroAtivo = 'todos';

/* ------------------------------------------------------------------ */
/*  DOM                                                                 */
/* ------------------------------------------------------------------ */

const elLista      = document.getElementById('remanejo-lista');
const elPendentes  = document.getElementById('stat-pendentes');
const elUrgentes   = document.getElementById('stat-urgentes');
const elResolvidos = document.getElementById('stat-resolvidos');
const elPollDot    = document.getElementById('poll-dot');
const elPollStatus = document.getElementById('poll-status');
const elPollTime   = document.getElementById('poll-time');
const elUserName   = document.getElementById('user-name');
const elUserMeta   = document.getElementById('user-meta');

/* ------------------------------------------------------------------ */
/*  INICIALIZAÇÃO DO HEADER                                             */
/* ------------------------------------------------------------------ */

function initHeader() {
    const user = getCurrentUser();
    if (user) {
        elUserName.textContent = user.nome ?? user.re ?? '—';
        elUserMeta.textContent = user.perfil ?? '—';
    }

    document.getElementById('btn-logout').addEventListener('click', () => {
        logout();
        window.location.replace('index.html');
    });
}

/* ------------------------------------------------------------------ */
/*  CÁLCULO DE URGÊNCIA                                                 */
/* ------------------------------------------------------------------ */

/**
 * Classifica um item por urgência com base no horário de saída.
 * @param {string} horario_saida — ex: "06:30:00"
 * @returns {'atrasado'|'urgente'|'normal'}
 */
function calcUrgencia(horario_saida) {
    if (!horario_saida) return 'normal';
    const now = new Date();
    const [h, m] = horario_saida.split(':').map(Number);
    const saida = new Date(now);
    saida.setHours(h, m, 0, 0);
    const diffMin = (saida - now) / 60_000;
    if (diffMin < 0)  return 'atrasado';
    if (diffMin < 60) return 'urgente';
    return 'normal';
}

/** Formata HH:MM a partir de "HH:MM:SS" */
function fmtHora(t) {
    if (!t) return '—';
    return t.slice(0, 5);
}

/** Formata data/hora de abertura da ficha */
function fmtFichaAberta(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    const dia  = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
    const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
    return `${dia} ${hora}`;
}

/* ------------------------------------------------------------------ */
/*  RENDERIZAÇÃO                                                        */
/* ------------------------------------------------------------------ */

function renderLista(lista) {
    // Filtra descartados localmente
    const ativos = lista.filter(it => !descartados.has(it.onibus_id));

    // Aplica filtro de urgência
    const visivel = filtroAtivo === 'todos'
        ? ativos
        : ativos.filter(it => calcUrgencia(it.horario_saida) === filtroAtivo);

    // Atualiza KPIs
    const urgentes = ativos.filter(it => calcUrgencia(it.horario_saida) === 'urgente').length;
    elPendentes.textContent  = ativos.length;
    elUrgentes.textContent   = urgentes;
    elResolvidos.textContent = descartados.size;

    // Estado vazio
    if (ativos.length === 0) {
        elLista.innerHTML = `
            <div class="placeholder-msg">
                <div class="icon">✅</div>
                <div class="title">Nenhum remanejamento pendente</div>
                <div class="sub">Todos os ônibus escalados para hoje estão disponíveis.</div>
            </div>`;
        return;
    }

    if (visivel.length === 0) {
        elLista.innerHTML = `
            <div class="placeholder-msg">
                <div class="icon">🔍</div>
                <div class="title">Nenhum item neste filtro</div>
                <div class="sub">Tente "Todos" para ver todos os pendentes.</div>
            </div>`;
        return;
    }

    elLista.innerHTML = visivel.map(it => buildCard(it)).join('');

    // Eventos dos botões "Resolver"
    elLista.querySelectorAll('.btn-resolver').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = btn.dataset.id;
            descartados.add(id);
            renderLista(ultimaLista);
        });
    });
}

/**
 * Monta o HTML de um card de remanejamento.
 * @param {Object} it — RemanejamentoItem do backend
 */
function buildCard(it) {
    const urgencia = calcUrgencia(it.horario_saida);

    // Setor: E2 (prefixo 1-299) ou AR2 (300+)
    const setor    = it.numero_frota <= 299 ? 'E2' : 'AR2';
    const setorCls = setor === 'E2' ? 'setor-e2' : 'setor-ar2';

    // Badge de urgência
    const urgenciaBadge = {
        atrasado: '<span class="remanejo-badge badge-atrasado">ATRASADO</span>',
        urgente:  '<span class="remanejo-badge badge-urgente">URGENTE</span>',
        normal:   '',
    }[urgencia];

    // Badge de status da ficha de manutenção
    let fichaBadge = '';
    if (it.status_ficha === 'aberta') {
        fichaBadge = '<span class="remanejo-badge badge-ficha">ABERTA</span>';
    } else if (it.status_ficha === 'em_andamento') {
        fichaBadge = '<span class="remanejo-badge badge-ficha badge-ficha-andamento">EM ANDAMENTO</span>';
    }

    // Linha de detalhe da ficha
    const fichaDetalhe = it.tipo_defeito
        ? `<div class="remanejo-defeito">
               🔧 ${it.tipo_defeito}
               ${it.ficha_aberta_em ? `<span class="remanejo-ficha-data">· desde ${fmtFichaAberta(it.ficha_aberta_em)}</span>` : ''}
           </div>`
        : '';

    return `
        <div class="remanejo-card remanejo-card-${urgencia}">
            <div class="remanejo-card-header">
                <div class="remanejo-frota">
                    <span class="chip-frota">${it.numero_frota}</span>
                    <span class="chip-setor ${setorCls}">${setor}</span>
                    ${urgenciaBadge}
                    ${fichaBadge}
                </div>
                <div class="remanejo-horario remanejo-horario-${urgencia}">
                    ⏱ ${fmtHora(it.horario_saida)}
                </div>
            </div>

            <div class="remanejo-linha">
                <span class="remanejo-linha-codigo">${it.linha_codigo}</span>
                <span class="remanejo-linha-nome">${it.linha_nome}</span>
            </div>

            <div class="remanejo-info-row">
                <span class="remanejo-fila-label">Fila manutenção:</span>
                <span class="remanejo-fila-valor">${it.fila_manutencao}</span>
            </div>

            ${fichaDetalhe}

            <button class="btn btn-ghost btn-resolver" data-id="${it.onibus_id}">
                Marcar como resolvido
            </button>
        </div>`;
}

/* ------------------------------------------------------------------ */
/*  POLLING                                                             */
/* ------------------------------------------------------------------ */

const POLL_INTERVALO_MS = 30_000;

function setFooter(online, msg) {
    elPollDot.className = `status-dot ${online ? 'online' : 'offline'}`;
    elPollStatus.textContent = msg;
    elPollTime.textContent = new Date().toLocaleTimeString('pt-BR');
}

async function fetchRemanejamento() {
    try {
        const lista = await apiGet('/patio/remanejamento');
        ultimaLista = lista;
        renderLista(lista);
        setFooter(true, 'Atualizado');
    } catch (err) {
        setFooter(false, err instanceof ApiError
            ? `Erro ${err.status}: ${err.message}`
            : 'Sem conexão com o servidor');
        // Não limpa a lista — mantém o último estado visível
    }
}

/* ------------------------------------------------------------------ */
/*  FILTROS                                                             */
/* ------------------------------------------------------------------ */

function initFiltros() {
    document.querySelectorAll('.filtro-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filtro-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            filtroAtivo = btn.dataset.filtro;
            renderLista(ultimaLista);
        });
    });
}

/* ------------------------------------------------------------------ */
/*  BOOTSTRAP                                                           */
/* ------------------------------------------------------------------ */

initHeader();
initFiltros();

// Primeira carga imediata, depois polling a cada 30s
fetchRemanejamento();
setInterval(fetchRemanejamento, POLL_INTERVALO_MS);
