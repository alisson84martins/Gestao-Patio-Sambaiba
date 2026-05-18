/*
 * Tela de Manutenção — Frontend V3
 * ----------------------------------
 * Fase 5.6 — Fichas de manutenção da frota.
 *
 * Fluxo principal:
 *   - Carrega em paralelo: ônibus, tipos de defeito e fichas de manutenção
 *   - Filtra as fichas client-side por status
 *   - Permite criar nova ficha (POST /manutencao) via modal
 *   - Permite atualizar status/descricao de ficha existente (PATCH /manutencao/{id}) via modal
 *   - Polling a cada 30s busca apenas GET /manutencao para manter a lista fresca
 *
 * Regras de UX:
 *   - Erros sempre inline nos modais (divs de erro), nunca alert()
 *   - Fichas concluídas aparecem visualmente apagadas (.manut-card-concluida)
 *   - Clicar num card abre o modal de atualização pré-preenchido
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, apiPatch, ApiError } from './api.js';

// Guard de sessão — redireciona pro login se não autenticado
if (!requireAuth()) throw new Error('Não autenticado');

/* ------------------------------------------------------------------ */
/*  ESTADO LOCAL                                                        */
/* ------------------------------------------------------------------ */

/** Lista completa de fichas recebida da API */
let fichas = [];

/** Lista de ônibus: usada para resolver onibus_id → numero_frota */
let onibus = [];

/** Tipos de defeito disponíveis */
let tiposDefeito = [];

/** Filtro ativo: 'todas' | 'aberta' | 'em_andamento' | 'concluida' */
let filtroAtivo = 'todas';

/** ID da ficha sendo editada no modal de atualização */
let fichaEditandoId = null;

/* ------------------------------------------------------------------ */
/*  SELETORES DOM                                                       */
/* ------------------------------------------------------------------ */

const elLista         = document.getElementById('manut-lista');
const elStatAbertas   = document.getElementById('stat-abertas');
const elStatAndamento = document.getElementById('stat-andamento');
const elStatConcluidas = document.getElementById('stat-concluidas');
const elPollDot       = document.getElementById('poll-dot');
const elPollStatus    = document.getElementById('poll-status');
const elPollTime      = document.getElementById('poll-time');
const elUserName      = document.getElementById('user-name');
const elUserMeta      = document.getElementById('user-meta');

// Modal Nova Ficha
const elModalNova        = document.getElementById('modal-nova-ficha');
const elNovaFrota        = document.getElementById('nova-frota');
const elNovaTipo         = document.getElementById('nova-tipo-defeito');
const elNovaDescricao    = document.getElementById('nova-descricao');
const elNovaErro         = document.getElementById('modal-nova-erro');

// Modal Atualizar Ficha
const elModalAtualizar   = document.getElementById('modal-atualizar-ficha');
const elAtualizarResumo  = document.getElementById('atualizar-resumo');
const elAtualizarStatus  = document.getElementById('atualizar-status');
const elAtualizarDescricao = document.getElementById('atualizar-descricao');
const elAtualizarErro    = document.getElementById('modal-atualizar-erro');

/* ------------------------------------------------------------------ */
/*  HELPERS DE RESOLUÇÃO                                                */
/* ------------------------------------------------------------------ */

/**
 * Resolve um onibus_id para o número de frota.
 * @param {string} onibus_id — UUID
 * @returns {string} numero_frota como string, ou '???' se não encontrado
 */
function resolverFrota(onibus_id) {
    const bus = onibus.find(o => o.id === onibus_id);
    return bus ? String(bus.numero_frota ?? bus.prefixo ?? bus.frota ?? '???') : '???';
}

/**
 * Resolve um tipo_defeito_id para o nome do tipo.
 * @param {string} tipo_defeito_id — UUID
 * @returns {string} nome do tipo, ou '—' se não encontrado
 */
function resolverDefeito(tipo_defeito_id) {
    const tipo = tiposDefeito.find(t => t.id === tipo_defeito_id);
    return tipo ? (tipo.nome ?? tipo.descricao ?? '—') : '—';
}

/**
 * Resolve numero_frota digitado pelo usuário para o onibus_id (UUID).
 * @param {string|number} frota — número de frota digitado
 * @returns {string|null} UUID do ônibus ou null se não encontrado
 */
function resolverOnibusId(frota) {
    const num = Number(frota);
    const bus = onibus.find(o =>
        Number(o.numero_frota ?? o.prefixo ?? o.frota) === num
    );
    return bus ? bus.id : null;
}

/**
 * Formata um datetime ISO para exibição em pt-BR.
 * @param {string|null} iso
 * @returns {string}
 */
function fmtData(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('pt-BR', {
        day: '2-digit',
        month: '2-digit',
        year: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    });
}

/* ------------------------------------------------------------------ */
/*  HEADER + LOGOUT                                                     */
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
/*  FOOTER DE POLLING                                                   */
/* ------------------------------------------------------------------ */

/**
 * Atualiza o rodapé com status de conexão e hora.
 * @param {boolean} online
 * @param {string} msg
 */
function setFooter(online, msg) {
    elPollDot.className = `status-dot ${online ? 'online' : 'offline'}`;
    elPollStatus.textContent = msg;
    elPollTime.textContent = new Date().toLocaleTimeString('pt-BR');
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
            renderFichas(fichas);
        });
    });
}

/* ------------------------------------------------------------------ */
/*  RENDERIZAÇÃO                                                        */
/* ------------------------------------------------------------------ */

/**
 * Renderiza a lista de fichas respeitando o filtro ativo.
 * Também atualiza os KPIs.
 * @param {Array} lista — todas as fichas
 */
function renderFichas(lista) {
    // Contadores para KPIs (sempre sobre a lista completa)
    const qtdAbertas   = lista.filter(f => f.status === 'ABERTA').length;
    const qtdAndamento = lista.filter(f => f.status === 'EM_ANDAMENTO').length;
    const qtdConcluidas = lista.filter(f => f.status === 'CONCLUIDA').length;

    elStatAbertas.textContent    = qtdAbertas;
    elStatAndamento.textContent  = qtdAndamento;
    elStatConcluidas.textContent = qtdConcluidas;

    // Aplica filtro de exibição
    const visivel = filtroAtivo === 'todas'
        ? lista
        : lista.filter(f => f.status.toLowerCase() === filtroAtivo);

    // Estado vazio
    if (lista.length === 0) {
        elLista.innerHTML = `
            <div class="placeholder-msg">
                <div class="icon">🔧</div>
                <div class="title">Nenhuma ficha de manutenção</div>
                <div class="sub">Clique em "+ Nova Ficha" para registrar a primeira ocorrência.</div>
            </div>`;
        return;
    }

    if (visivel.length === 0) {
        elLista.innerHTML = `
            <div class="placeholder-msg">
                <div class="icon">🔍</div>
                <div class="title">Nenhuma ficha neste filtro</div>
                <div class="sub">Selecione "Todas" para ver todos os registros.</div>
            </div>`;
        return;
    }

    elLista.innerHTML = visivel.map(f => buildCard(f)).join('');

    // Vincula evento de clique em cada card para abrir o modal de atualização
    elLista.querySelectorAll('.manut-card').forEach(card => {
        card.addEventListener('click', () => {
            const id = card.dataset.id;
            const ficha = fichas.find(f => f.id === id);
            if (ficha) abrirModalAtualizar(ficha);
        });
    });

    // Botões de ação rápida "Em Andamento" e "Concluir"
    elLista.querySelectorAll('.btn-acao-andamento').forEach(btn => {
        btn.addEventListener('click', e => {
            e.stopPropagation(); // não abre o modal ao clicar no botão
            atualizarFicha(btn.dataset.id, { status: 'EM_ANDAMENTO' });
        });
    });

    elLista.querySelectorAll('.btn-acao-concluir').forEach(btn => {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            atualizarFicha(btn.dataset.id, { status: 'CONCLUIDA' });
        });
    });
}

/**
 * Constrói o HTML de um card de ficha de manutenção.
 * @param {Object} ficha — FichaManutencaoRead do backend
 * @returns {string} HTML do card
 */
function buildCard(ficha) {
    const frota   = resolverFrota(ficha.onibus_id);
    const defeito = resolverDefeito(ficha.tipo_defeito_id);
    const status  = ficha.status ?? 'ABERTA';
    const statusLower = status.toLowerCase(); // 'aberta' | 'em_andamento' | 'concluida'

    // Textos dos badges
    const badgeTexto = {
        ABERTA:       'ABERTA',
        EM_ANDAMENTO: 'EM ANDAMENTO',
        CONCLUIDA:    'CONCLUÍDA',
    }[status] ?? status;

    // Botões de ação rápida (não exibir botões desnecessários)
    let botoesAcao = '';
    if (status === 'ABERTA') {
        botoesAcao = `
            <button class="btn btn-ghost btn-acao-andamento" data-id="${ficha.id}" title="Mover para Em Andamento">Em andamento</button>
            <button class="btn btn-primary btn-acao-concluir" data-id="${ficha.id}" title="Marcar como Concluída" style="margin-left:0.5rem">Concluir</button>
        `;
    } else if (status === 'EM_ANDAMENTO') {
        botoesAcao = `
            <button class="btn btn-primary btn-acao-concluir" data-id="${ficha.id}" title="Marcar como Concluída">Concluir</button>
        `;
    }

    // Data de abertura: prioriza aberta_em, senão criado_em
    const dataAbertura = fmtData(ficha.aberta_em ?? ficha.criado_em);

    return `
        <div class="manut-card manut-card-${statusLower}" data-id="${ficha.id}" title="Clique para editar esta ficha" style="cursor:pointer">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:0.5rem;flex-wrap:wrap">
                <span class="manut-frota">${frota}</span>
                <span class="manut-badge-${statusLower}">${badgeTexto}</span>
            </div>

            <div class="manut-info">
                🔧 ${defeito}
            </div>

            ${ficha.descricao ? `<div class="manut-info" style="opacity:0.7">${ficha.descricao}</div>` : ''}

            <div class="manut-info" style="font-size:0.8rem;opacity:0.55">
                Aberta em: ${dataAbertura}
                ${ficha.concluida_em ? ` · Concluída em: ${fmtData(ficha.concluida_em)}` : ''}
            </div>

            ${botoesAcao
                ? `<div style="margin-top:0.75rem;display:flex;gap:0.25rem;flex-wrap:wrap">${botoesAcao}</div>`
                : ''}
        </div>`;
}

/* ------------------------------------------------------------------ */
/*  MODAL — NOVA FICHA                                                  */
/* ------------------------------------------------------------------ */

function initModalNova() {
    // Abre modal
    document.getElementById('btn-nova-ficha').addEventListener('click', () => {
        limparErroNova();
        elNovaFrota.value       = '';
        elNovaDescricao.value   = '';
        elNovaTipo.value        = '';
        elModalNova.classList.add('open');
        elNovaFrota.focus();
    });

    // Fecha modal pelos botões
    document.getElementById('modal-nova-fechar').addEventListener('click', fecharModalNova);
    document.getElementById('btn-nova-cancelar').addEventListener('click', fecharModalNova);

    // Fecha modal clicando fora
    elModalNova.addEventListener('click', e => {
        if (e.target === elModalNova) fecharModalNova();
    });

    // Submete nova ficha
    document.getElementById('btn-abrir-ficha').addEventListener('click', criarFicha);

    // Pre-popula o select de tipos de defeito
    popularSelectTipos();
}

function fecharModalNova() {
    elModalNova.classList.remove('open');
    limparErroNova();
}

function limparErroNova() {
    elNovaErro.style.display = 'none';
    elNovaErro.textContent   = '';
}

function mostrarErroNova(msg) {
    elNovaErro.textContent   = msg;
    elNovaErro.style.display = 'block';
}

/**
 * Preenche o select de tipos de defeito com os dados já carregados.
 * Chamada no init e também após carregar tiposDefeito.
 */
function popularSelectTipos() {
    // Remove opções antigas (exceto o placeholder)
    while (elNovaTipo.options.length > 1) {
        elNovaTipo.remove(1);
    }
    tiposDefeito.forEach(tipo => {
        const opt = document.createElement('option');
        opt.value       = tipo.id;
        opt.textContent = tipo.nome ?? tipo.descricao ?? tipo.id;
        elNovaTipo.appendChild(opt);
    });
}

/**
 * Valida os campos e faz POST /manutencao.
 */
async function criarFicha() {
    limparErroNova();

    const frotaDigitada = elNovaFrota.value.trim();
    const tipoId        = elNovaTipo.value;
    const descricao     = elNovaDescricao.value.trim();

    // Validação de frota
    if (!frotaDigitada) {
        mostrarErroNova('Informe o número da frota.');
        elNovaFrota.focus();
        return;
    }

    // Resolve frota para onibus_id
    const onibusId = resolverOnibusId(frotaDigitada);
    if (!onibusId) {
        mostrarErroNova(`Frota ${frotaDigitada} não encontrada no sistema. Verifique o número.`);
        elNovaFrota.focus();
        return;
    }

    // Validação de tipo de defeito
    if (!tipoId) {
        mostrarErroNova('Selecione o tipo de defeito.');
        elNovaTipo.focus();
        return;
    }

    // Monta payload
    const payload = {
        onibus_id:       onibusId,
        tipo_defeito_id: tipoId,
        status:          'ABERTA',
    };
    if (descricao) payload.descricao = descricao;

    // Desabilita botão para evitar duplo clique
    const btnAbrir = document.getElementById('btn-abrir-ficha');
    btnAbrir.disabled = true;
    btnAbrir.textContent = 'Abrindo…';

    try {
        await apiPost('/manutencao', payload);
        fecharModalNova();
        await carregarFichas(); // atualiza apenas as fichas
    } catch (err) {
        mostrarErroNova(err instanceof ApiError
            ? `Erro ${err.status}: ${err.message}`
            : 'Não foi possível criar a ficha. Tente novamente.');
    } finally {
        btnAbrir.disabled    = false;
        btnAbrir.textContent = 'Abrir Ficha';
    }
}

/* ------------------------------------------------------------------ */
/*  MODAL — ATUALIZAR FICHA                                             */
/* ------------------------------------------------------------------ */

function initModalAtualizar() {
    // Fecha modal pelos botões
    document.getElementById('modal-atualizar-fechar').addEventListener('click', fecharModalAtualizar);
    document.getElementById('btn-atualizar-cancelar').addEventListener('click', fecharModalAtualizar);

    // Fecha clicando fora
    elModalAtualizar.addEventListener('click', e => {
        if (e.target === elModalAtualizar) fecharModalAtualizar();
    });

    // Salva atualização
    document.getElementById('btn-salvar-atualizacao').addEventListener('click', salvarAtualizacao);
}

function fecharModalAtualizar() {
    elModalAtualizar.classList.remove('open');
    fichaEditandoId = null;
    limparErroAtualizar();
}

function limparErroAtualizar() {
    elAtualizarErro.style.display = 'none';
    elAtualizarErro.textContent   = '';
}

function mostrarErroAtualizar(msg) {
    elAtualizarErro.textContent   = msg;
    elAtualizarErro.style.display = 'block';
}

/**
 * Abre o modal de atualização pré-preenchido com os dados da ficha.
 * @param {Object} ficha — FichaManutencaoRead
 */
function abrirModalAtualizar(ficha) {
    limparErroAtualizar();
    fichaEditandoId = ficha.id;

    const frota   = resolverFrota(ficha.onibus_id);
    const defeito = resolverDefeito(ficha.tipo_defeito_id);
    const dataAbertura = fmtData(ficha.aberta_em ?? ficha.criado_em);

    // Resumo informativo da ficha
    elAtualizarResumo.innerHTML = `
        <strong>Frota ${frota}</strong> · ${defeito}<br>
        ${ficha.descricao ? `<em>${ficha.descricao}</em><br>` : ''}
        Aberta em: ${dataAbertura}
    `;

    // Pré-seleciona o status atual
    elAtualizarStatus.value = ficha.status ?? 'ABERTA';

    // Limpa a textarea de observação (não replica a descricao anterior)
    elAtualizarDescricao.value = '';

    elModalAtualizar.classList.add('open');
}

/**
 * Faz PATCH /manutencao/{id} com os dados do modal.
 */
async function salvarAtualizacao() {
    limparErroAtualizar();

    if (!fichaEditandoId) {
        mostrarErroAtualizar('Nenhuma ficha selecionada.');
        return;
    }

    const novoStatus = elAtualizarStatus.value;
    const observacao = elAtualizarDescricao.value.trim();

    const payload = { status: novoStatus };
    if (observacao) payload.descricao = observacao;

    const btnSalvar = document.getElementById('btn-salvar-atualizacao');
    btnSalvar.disabled    = true;
    btnSalvar.textContent = 'Salvando…';

    try {
        await atualizarFicha(fichaEditandoId, payload);
        fecharModalAtualizar();
    } catch (err) {
        mostrarErroAtualizar(err instanceof ApiError
            ? `Erro ${err.status}: ${err.message}`
            : 'Não foi possível atualizar a ficha. Tente novamente.');
    } finally {
        btnSalvar.disabled    = false;
        btnSalvar.textContent = 'Salvar';
    }
}

/* ------------------------------------------------------------------ */
/*  AÇÕES DE API                                                        */
/* ------------------------------------------------------------------ */

/**
 * Atualiza status/descricao de uma ficha via PATCH.
 * Recarrega a lista após sucesso.
 * @param {string} id — UUID da ficha
 * @param {Object} dados — { status?, descricao? }
 */
async function atualizarFicha(id, dados) {
    await apiPatch(`/manutencao/${id}`, dados);
    await carregarFichas();
}

/* ------------------------------------------------------------------ */
/*  CARGA DE DADOS                                                      */
/* ------------------------------------------------------------------ */

/**
 * Carrega em paralelo: ônibus, tipos de defeito e fichas.
 * Chamada apenas na inicialização.
 */
async function carregarDados() {
    setFooter(false, 'Carregando dados…');
    try {
        const [resOnibus, resTipos, resFichas] = await Promise.all([
            apiGet('/onibus?limit=1000'),
            apiGet('/tipos-defeito'),
            apiGet('/manutencao?limit=200'),
        ]);

        // Normaliza: a API pode retornar lista direta ou { items: [...] }
        onibus       = Array.isArray(resOnibus)  ? resOnibus  : (resOnibus?.items  ?? []);
        tiposDefeito = Array.isArray(resTipos)   ? resTipos   : (resTipos?.items   ?? []);
        fichas       = Array.isArray(resFichas)  ? resFichas  : (resFichas?.items  ?? []);

        // Repopula o select agora que os tipos foram carregados
        popularSelectTipos();

        renderFichas(fichas);
        setFooter(true, 'Atualizado');
    } catch (err) {
        setFooter(false, err instanceof ApiError
            ? `Erro ${err.status}: ${err.message}`
            : 'Sem conexão com o servidor');
        elLista.innerHTML = `
            <div class="placeholder-msg">
                <div class="icon">⚠️</div>
                <div class="title">Erro ao carregar dados</div>
                <div class="sub">Verifique a conexão com o servidor e recarregue a página.</div>
            </div>`;
    }
}

/**
 * Busca apenas as fichas (usado pelo polling e após criar/atualizar).
 * Ônibus e tipos de defeito já estão em memória após o carregarDados inicial.
 */
async function carregarFichas() {
    try {
        const res = await apiGet('/manutencao?limit=200');
        fichas = Array.isArray(res) ? res : (res?.items ?? []);
        renderFichas(fichas);
        setFooter(true, 'Atualizado');
    } catch (err) {
        setFooter(false, err instanceof ApiError
            ? `Erro ${err.status}: ${err.message}`
            : 'Sem conexão com o servidor');
        // Mantém o último estado visível — não limpa a lista
    }
}

/* ------------------------------------------------------------------ */
/*  BOOTSTRAP                                                           */
/* ------------------------------------------------------------------ */

// Intervalo de polling (igual ao patio.page.js e remanejamento.js)
const POLL_INTERVALO_MS = 30_000;

initHeader();
initFiltros();
initModalNova();
initModalAtualizar();

// Carga inicial completa (ônibus + tipos + fichas em paralelo)
carregarDados();

// Polling: busca apenas as fichas a cada 30s
setInterval(carregarFichas, POLL_INTERVALO_MS);
