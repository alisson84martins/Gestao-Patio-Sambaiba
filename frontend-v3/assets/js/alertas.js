/*
 * Tela de Alertas — Frontend V3
 * --------------------------------
 * Consome GET /alertas, POST /alertas e PATCH /alertas/{id}/resolver.
 * Gerencia dois tipos de alerta: PRESO (borda vermelha) e AMOSTRAL (borda azul).
 *
 * Filtros: ativos | preso | amostral | resolvidos (todos client-side).
 * O pre-load de /onibus resolve onibus_id → numero_frota na exibição
 * e faz a conversão inversa numero_frota → onibus_id no cadastro.
 *
 * Polling: 30 s (mesmo intervalo das demais telas).
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, apiPatch, ApiError } from './api.js';

// Guard de sessão — redireciona pra login se não autenticado
if (!requireAuth()) throw new Error('Não autenticado');

/* ------------------------------------------------------------------ */
/*  CONSTANTES                                                          */
/* ------------------------------------------------------------------ */

const POLL_INTERVALO_MS = 30_000;

/* ------------------------------------------------------------------ */
/*  ESTADO LOCAL                                                        */
/* ------------------------------------------------------------------ */

/** Snapshot completo da API (ativos + resolvidos) */
let todosAlertas = [];

/** Mapa onibus_id → numero_frota (pre-carregado no init) */
const mapIdParaFrota = new Map();

/** Mapa numero_frota → onibus_id (pre-carregado no init) */
const mapFrotaParaId = new Map();

/** Filtro ativo: 'ativos' | 'preso' | 'amostral' | 'resolvidos' */
let filtroAtivo = 'ativos';

/** Tipo selecionado no modal: 'PRESO' | 'AMOSTRAL' */
let tipoSelecionado = 'PRESO';

/* ------------------------------------------------------------------ */
/*  DOM                                                                 */
/* ------------------------------------------------------------------ */

const elLista       = document.getElementById('alerta-lista');
const elStatPresos  = document.getElementById('stat-presos');
const elStatAmostr  = document.getElementById('stat-amostrais');
const elStatResol   = document.getElementById('stat-resolvidos');
const elPollDot     = document.getElementById('poll-dot');
const elPollStatus  = document.getElementById('poll-status');
const elPollTime    = document.getElementById('poll-time');
const elUserName    = document.getElementById('user-name');
const elUserMeta    = document.getElementById('user-meta');

const elModalOverlay = document.getElementById('modal-novo-alerta');
const elModalErro    = document.getElementById('modal-erro');
const elInputFrota   = document.getElementById('modal-frota');
const elInputMotivo  = document.getElementById('modal-motivo');

/* ------------------------------------------------------------------ */
/*  HEADER                                                              */
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
/*  PRE-LOAD DE FROTA                                                   */
/* ------------------------------------------------------------------ */

/**
 * Carrega todos os ônibus cadastrados e popula os mapas de lookup.
 * Chamado uma única vez no bootstrap.
 */
async function preloadOnibus() {
    try {
        const lista = await apiGet('/onibus?limit=1000');
        for (const onibus of lista) {
            mapIdParaFrota.set(onibus.id, onibus.numero_frota);
            mapFrotaParaId.set(Number(onibus.numero_frota), onibus.id);
        }
    } catch (_err) {
        // Falha silenciosa no pre-load — exibição mostrará UUID encurtado
    }
}

/* ------------------------------------------------------------------ */
/*  FILTROS                                                             */
/* ------------------------------------------------------------------ */

function initFiltros() {
    document.querySelectorAll('.filtro-btn[data-filtro]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filtro-btn[data-filtro]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            filtroAtivo = btn.dataset.filtro;
            renderAlertas(todosAlertas);
        });
    });
}

/* ------------------------------------------------------------------ */
/*  MODAL                                                               */
/* ------------------------------------------------------------------ */

function abrirModal() {
    // Reseta estado do modal a cada abertura
    tipoSelecionado = 'PRESO';
    elInputFrota.value  = '';
    elInputMotivo.value = '';
    esconderErroModal();
    atualizarTipoToggle();
    elModalOverlay.classList.add('open');
    elInputFrota.focus();
}

function fecharModal() {
    elModalOverlay.classList.remove('open');
}

function mostrarErroModal(msg) {
    elModalErro.textContent = msg;
    elModalErro.style.display = 'block';
}

function esconderErroModal() {
    elModalErro.textContent = '';
    elModalErro.style.display = 'none';
}

function atualizarTipoToggle() {
    document.querySelectorAll('.tipo-toggle-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tipo === tipoSelecionado);
    });
}

function initModal() {
    // Abrir modal
    document.getElementById('btn-novo-alerta').addEventListener('click', abrirModal);

    // Fechar modal
    document.getElementById('modal-fechar').addEventListener('click', fecharModal);
    document.getElementById('btn-cancelar').addEventListener('click', fecharModal);

    // Fechar ao clicar no overlay (fora do card)
    elModalOverlay.addEventListener('click', e => {
        if (e.target === elModalOverlay) fecharModal();
    });

    // Toggle de tipo PRESO / AMOSTRAL
    document.querySelectorAll('.tipo-toggle-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            tipoSelecionado = btn.dataset.tipo;
            atualizarTipoToggle();
            esconderErroModal();
        });
    });

    // Submit via botão Registrar
    document.getElementById('btn-registrar').addEventListener('click', criarAlerta);

    // Submit via Enter no campo frota
    elInputFrota.addEventListener('keydown', e => {
        if (e.key === 'Enter') criarAlerta();
    });
}

/* ------------------------------------------------------------------ */
/*  CRIAR ALERTA                                                        */
/* ------------------------------------------------------------------ */

async function criarAlerta() {
    esconderErroModal();

    const frotaRaw = elInputFrota.value.trim();
    if (!frotaRaw) {
        mostrarErroModal('Informe o número da frota.');
        elInputFrota.focus();
        return;
    }

    const numeroFrota = Number(frotaRaw);
    if (!Number.isInteger(numeroFrota) || numeroFrota <= 0) {
        mostrarErroModal('Frota inválida. Digite um número inteiro positivo.');
        elInputFrota.focus();
        return;
    }

    const onibusId = mapFrotaParaId.get(numeroFrota);
    if (!onibusId) {
        mostrarErroModal(`Frota ${numeroFrota} não encontrada no cadastro.`);
        elInputFrota.focus();
        return;
    }

    const motivo = elInputMotivo.value.trim() || undefined;

    const btnRegistrar = document.getElementById('btn-registrar');
    btnRegistrar.disabled = true;
    btnRegistrar.textContent = 'Registrando…';

    try {
        await apiPost('/alertas', {
            onibus_id: onibusId,
            tipo: tipoSelecionado,
            ...(motivo ? { motivo } : {}),
        });
        fecharModal();
        await fetchAlertas();
    } catch (err) {
        mostrarErroModal(err instanceof ApiError
            ? `Erro ${err.status}: ${err.message}`
            : 'Falha ao registrar alerta. Tente novamente.');
    } finally {
        btnRegistrar.disabled = false;
        btnRegistrar.textContent = 'Registrar';
    }
}

/* ------------------------------------------------------------------ */
/*  RESOLVER ALERTA                                                     */
/* ------------------------------------------------------------------ */

async function resolverAlerta(id) {
    try {
        await apiPatch(`/alertas/${id}/resolver`, {});
        await fetchAlertas();
    } catch (err) {
        // Exibe erro simples na lista sem travar a UI
        const msgErro = err instanceof ApiError
            ? `Erro ${err.status}: ${err.message}`
            : 'Falha ao resolver alerta.';
        mostrarErroInline(id, msgErro);
    }
}

/** Exibe mensagem de erro inline no card do alerta */
function mostrarErroInline(alertaId, msg) {
    const card = elLista.querySelector(`[data-alerta-id="${alertaId}"]`);
    if (!card) return;
    let erroEl = card.querySelector('.alerta-card-erro');
    if (!erroEl) {
        erroEl = document.createElement('div');
        erroEl.className = 'alerta-card-erro';
        erroEl.style.cssText = 'color:var(--accent);font-size:0.8rem;margin-top:0.5rem;';
        card.appendChild(erroEl);
    }
    erroEl.textContent = msg;
}

/* ------------------------------------------------------------------ */
/*  RENDERIZAÇÃO                                                        */
/* ------------------------------------------------------------------ */

/** Aplica o filtro ativo sobre a lista completa */
function aplicarFiltro(lista) {
    switch (filtroAtivo) {
        case 'preso':      return lista.filter(a => !a.resolvido && a.tipo === 'PRESO');
        case 'amostral':   return lista.filter(a => !a.resolvido && a.tipo === 'AMOSTRAL');
        case 'resolvidos': return lista.filter(a => a.resolvido);
        case 'ativos':
        default:           return lista.filter(a => !a.resolvido);
    }
}

/**
 * Renderiza os cards e atualiza os KPIs.
 * @param {Array} lista — lista completa de AlertaRead
 */
function renderAlertas(lista) {
    // Calcula KPIs sobre o total (independente do filtro)
    const presosAtivos   = lista.filter(a => !a.resolvido && a.tipo === 'PRESO').length;
    const amostraisAtivos = lista.filter(a => !a.resolvido && a.tipo === 'AMOSTRAL').length;
    const resolvidos      = lista.filter(a => a.resolvido).length;

    elStatPresos.textContent = presosAtivos;
    elStatAmostr.textContent = amostraisAtivos;
    elStatResol.textContent  = resolvidos;

    const visivel = aplicarFiltro(lista);

    if (visivel.length === 0) {
        const mensagens = {
            ativos:     { icon: '✅', title: 'Nenhum alerta ativo', sub: 'Ótimo! Não há presos nem amostrais no momento.' },
            preso:      { icon: '✅', title: 'Nenhum PRESO ativo', sub: 'Não há ônibus presos registrados.' },
            amostral:   { icon: '✅', title: 'Nenhum AMOSTRAL ativo', sub: 'Não há ônibus amostrais registrados.' },
            resolvidos: { icon: '📋', title: 'Nenhum alerta resolvido', sub: 'Os alertas resolvidos aparecerão aqui.' },
        };
        const m = mensagens[filtroAtivo] ?? mensagens.ativos;
        elLista.innerHTML = `
            <div class="placeholder-msg">
                <div class="icon">${m.icon}</div>
                <div class="title">${m.title}</div>
                <div class="sub">${m.sub}</div>
            </div>`;
        return;
    }

    elLista.innerHTML = visivel.map(a => buildCard(a)).join('');

    // Vincula eventos dos botões "Resolver"
    elLista.querySelectorAll('.btn-resolver-alerta').forEach(btn => {
        btn.addEventListener('click', () => resolverAlerta(btn.dataset.id));
    });
}

/**
 * Formata ISO datetime para exibição curta: "dd/MM HH:MM"
 * @param {string} iso
 */
function fmtDataHora(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    const dia  = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
    const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
    return `${dia} ${hora}`;
}

/**
 * Monta o HTML de um card de alerta.
 * @param {Object} alerta — AlertaRead do backend
 */
function buildCard(alerta) {
    const frota = mapIdParaFrota.get(alerta.onibus_id) ?? alerta.onibus_id.slice(0, 8);

    const isPreso   = alerta.tipo === 'PRESO';
    const cardClass = isPreso ? 'alerta-card-preso' : 'alerta-card-amostral';
    const badgeClass = isPreso ? 'alerta-badge-preso' : 'alerta-badge-amostral';
    const badgeLabel = isPreso ? 'PRESO' : 'AMOSTRAL';

    const motivoHtml = alerta.motivo
        ? `<div class="remanejo-defeito">${alerta.motivo}</div>`
        : '';

    const resolvidoInfo = alerta.resolvido
        ? `<div style="font-size:0.8rem;color:var(--text-muted);margin-top:0.4rem;">Resolvido em ${fmtDataHora(alerta.resolvido_em)}</div>`
        : '';

    const btnResolver = !alerta.resolvido
        ? `<button class="btn btn-ghost btn-resolver-alerta" data-id="${alerta.id}">Resolver</button>`
        : '';

    return `
        <div class="alerta-card ${cardClass}" data-alerta-id="${alerta.id}">
            <div class="remanejo-card-header">
                <div class="remanejo-frota">
                    <span class="chip-frota">${frota}</span>
                    <span class="${badgeClass}">${badgeLabel}</span>
                </div>
                <div style="font-size:0.8rem;color:var(--text-muted);">
                    ${fmtDataHora(alerta.criado_em)}
                </div>
            </div>
            ${motivoHtml}
            ${resolvidoInfo}
            ${btnResolver}
        </div>`;
}

/* ------------------------------------------------------------------ */
/*  FOOTER DE STATUS                                                    */
/* ------------------------------------------------------------------ */

function setFooter(online, msg) {
    elPollDot.className    = `status-dot ${online ? 'online' : 'offline'}`;
    elPollStatus.textContent = msg;
    elPollTime.textContent   = new Date().toLocaleTimeString('pt-BR');
}

/* ------------------------------------------------------------------ */
/*  POLLING / FETCH                                                     */
/* ------------------------------------------------------------------ */

async function fetchAlertas() {
    try {
        // Traz todos (ativos + resolvidos) para filtrar client-side
        const [ativos, resolvidos] = await Promise.all([
            apiGet('/alertas?resolvido=false'),
            apiGet('/alertas?resolvido=true'),
        ]);
        todosAlertas = [...ativos, ...resolvidos];
        renderAlertas(todosAlertas);
        setFooter(true, 'Atualizado');
    } catch (err) {
        setFooter(false, err instanceof ApiError
            ? `Erro ${err.status}: ${err.message}`
            : 'Sem conexão com o servidor');
        // Mantém o último estado visível em caso de falha
    }
}

/* ------------------------------------------------------------------ */
/*  BOOTSTRAP                                                           */
/* ------------------------------------------------------------------ */

initHeader();
initFiltros();
initModal();

// Pre-load da frota antes da primeira busca de alertas
preloadOnibus().then(() => {
    fetchAlertas();
    setInterval(fetchAlertas, POLL_INTERVALO_MS);
});
