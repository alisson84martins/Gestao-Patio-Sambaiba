/*
 * painel-gerencial.page.js — Painel Gerencial (migration 046)
 * -------------------------------------------------------------------------------
 * Uma JANELA para o que a Portaria e o Pátio registram: ver, filtrar, buscar,
 * exportar e imprimir. SÓ LEITURA e SÓ VISUALIZAÇÃO — sem gráfico, ranking,
 * comparação nem indicador calculado (decisão do Alisson, 25/09: a análise
 * fica com a gerência).
 *
 * Rotas: /painel-gerencial/resumo (os chips do topo), /painel-gerencial/eventos
 * (a lista de registros) e /painel-gerencial/dentro-agora (a lista do chip
 * "Dentro agora"). Abas Portaria · Pátio (26/09: saiu a aba Tudo).
 *
 * Um assunto por vez (26/09): os chips do topo são filtros. Clicou, a
 * tabela mostra só aquele grupo; clicou de novo, volta tudo da aba. Um chip
 * ativo por vez; trocar de aba limpa. Entradas e Saídas abrem a fileira de
 * sub-chips de categoria (D1). Regra de ouro: o número do chip = as linhas
 * que ele mostra — por isso o backend conta e filtra pelo MESMO GRUPOS.
 *
 * Dia = dia do RELÓGIO em São Paulo. As datas saem de dataLocalISO()
 * (⛔ nunca toISOString().slice(0,10) — erra o dia depois das 21h); quem corta
 * o dia em UTC é o backend (intervalo_utc). Horas exibidas sempre com
 * timeZone 'America/Sao_Paulo'.
 *
 * Tempo real só quando o período inclui hoje: registros a cada
 * POLLING_INTERVAL_MS, contadores a cada 30 s, tudo pausado com a aba
 * escondida. Período passado carrega uma vez, sem polling.
 *
 * Texto da API só entra em innerHTML via escapeHtml().
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, ApiError } from './api.js';
import { API_BASE_URL, TOKEN_KEY, POLLING_INTERVAL_MS } from './config.js';
import { podeLer } from './sessao.js';
import { escapeHtml } from './escape.js';
import { dataLocalISO } from './data.util.js';

if (!requireAuth()) throw new Error('Não autenticado');

if (!podeLer('painel_gerencial')) {
    window.location.replace('modulos.html');
    throw new Error('Sem acesso ao recurso painel_gerencial');
}

const FUSO = 'America/Sao_Paulo';
const RESUMO_INTERVALO_MS = 30000;
// Janela de sobreposição do polling: uma transação que começou antes do
// último evento visto pode gravar depois (momento = início da transação).
// Pede de novo um pouco para trás e descarta o repetido pela chave.
const POLLING_SOBREPOSICAO_MS = 60000;
const LIMITE_PAGINA = 100;

const TIPOS = {
    ENTRADA: { rotulo: 'Entrada', modulo: 'PORTARIA' },
    SAIDA: { rotulo: 'Saída', modulo: 'PORTARIA' },
    RECOLHIDA: { rotulo: 'Recolhida', modulo: 'PORTARIA' },
    RECOLHIDA_AVALIADA: { rotulo: 'Recolhida avaliada', modulo: 'PORTARIA' },
    RECOLHIDA_ENCERRADA: { rotulo: 'Recolhida encerrada', modulo: 'PORTARIA' },
    AVARIA: { rotulo: 'Avaria', modulo: 'PORTARIA' },
    AVARIA_REVISTA: { rotulo: 'Avaria revista', modulo: 'PORTARIA' },
    AVARIA_CONTESTADA: { rotulo: 'Avaria contestada', modulo: 'PORTARIA' },
    AVARIA_ENCERRADA: { rotulo: 'Avaria encerrada', modulo: 'PORTARIA' },
    VEICULO_SITUACAO: { rotulo: 'Situação de veículo', modulo: 'PORTARIA' },
    ALOCACAO: { rotulo: 'Alocação', modulo: 'PATIO' },
    MOVIMENTACAO: { rotulo: 'Movimentação', modulo: 'PATIO' },
    RETIRADA: { rotulo: 'Retirada', modulo: 'PATIO' },
};

const CATEGORIAS = {
    RESERVADO: 'Reservado',
    FROTA_APOIO: 'Frota de apoio',
    TERCEIRO: 'Terceiro',
    FUNCIONARIO: 'Funcionário',
    DEFEITO: 'Defeito',
    COLISAO: 'Colisão',
    FALTA_MOTORISTA: 'Falta de motorista',
    FALTA_COBRADOR: 'Falta de cobrador',
    OUTRO: 'Outro',
    LIBERADO: 'Liberado',
    RETIDO: 'Retido',
    SEM_DEFEITO: 'Sem defeito',
    SERVICO_EXECUTADO: 'Serviço executado',
    REPARADA: 'Reparada',
    NAO_EXISTIA: 'Não existia',
    DUPLICADA: 'Duplicada',
    PENDENTE: 'Pendente',
    AUTORIZADO: 'Autorizado',
    SUSPENSO: 'Suspenso',
    BAIXADO: 'Baixado',
};

// Chips de cada aba — `grupo` é o que vai para a API (GRUPOS do backend);
// `chave` é onde o /resumo põe o número; `nenhum` é o começo do texto de
// lista vazia. DENTRO não é grupo da API: troca a tabela pela lista de
// /dentro-agora (D2) e só aparece quando o período inclui hoje (o backend
// devolve dentro_agora = null nos outros).
const DENTRO = 'DENTRO';
const CHIPS = {
    PORTARIA: [
        { grupo: 'ENTRADAS', chave: 'entradas', rotulo: 'Entradas', nenhum: 'Nenhuma entrada', categorias: true },
        { grupo: 'SAIDAS', chave: 'saidas', rotulo: 'Saídas', nenhum: 'Nenhuma saída', categorias: true },
        { grupo: DENTRO, chave: 'dentro_agora', rotulo: 'Dentro agora', nenhum: 'Nenhum veículo dentro agora' },
        { grupo: 'RA', chave: 'ra', rotulo: 'RA', nenhum: 'Nenhuma RA' },
        { grupo: 'AVARIAS', chave: 'avarias', rotulo: 'Avarias', nenhum: 'Nenhuma avaria' },
    ],
    PATIO: [
        { grupo: 'ALOCACOES', chave: 'alocacoes', rotulo: 'Alocações', nenhum: 'Nenhuma alocação' },
        { grupo: 'MOVIMENTACOES', chave: 'movimentacoes', rotulo: 'Movimentações', nenhum: 'Nenhuma movimentação' },
        { grupo: 'RETIRADAS', chave: 'retiradas', rotulo: 'Retiradas', nenhum: 'Nenhuma retirada' },
    ],
};

// Sub-chips de Entradas/Saídas (D1), na ordem da tela. `de` completa o texto
// de lista vazia ("Nenhuma entrada de terceiro neste período.").
const SUB_CHIPS = [
    { categoria: 'FUNCIONARIO', rotulo: 'Funcionário', de: 'de funcionário', arquivo: 'funcionario' },
    { categoria: 'FROTA_APOIO', rotulo: 'Frota de apoio', de: 'da frota de apoio', arquivo: 'frota-apoio' },
    { categoria: 'TERCEIRO', rotulo: 'Terceiro', de: 'de terceiro', arquivo: 'terceiro' },
    { categoria: 'RESERVADO', rotulo: 'Reservado', de: 'de reservado', arquivo: 'reservado' },
];

const ABAS = { PORTARIA: 'Portaria', PATIO: 'Pátio' };

const CABECALHO_EVENTOS = ['Hora', 'Tipo', 'Veículo', 'Detalhe', 'Pessoa', 'Registrado por'];
const CABECALHO_DENTRO = ['Entrou em', 'Veículo', 'Categoria', 'Pessoa', 'Há quanto tempo'];

// ─── Estado ─────────────────────────────────────────────────────────────
const estado = {
    preset: 'hoje',
    deCustom: null,
    ateCustom: null,
    resumo: null,
    periodo: null,          // o que o backend resolveu (de, ate, inclui_hoje)
    eventos: [],
    chaves: new Set(),
    temMais: false,
    dentro: [],             // lista do chip Dentro agora
    // modulo = a aba; grupo = chip ativo ('' = nenhum); categoria = sub-chip
    filtros: { modulo: 'PORTARIA', grupo: '', categoria: '', busca: '' },
    geracao: 0,             // descarta resposta atrasada de um período anterior
};

let timerEventos = null;
let timerResumo = null;

// ─── Utilidades ─────────────────────────────────────────────────────────
function somarDias(iso, dias) {
    const [a, m, d] = iso.split('-').map(Number);
    return dataLocalISO(new Date(a, m - 1, d + dias));
}

function periodoDoPreset() {
    const hoje = dataLocalISO();
    switch (estado.preset) {
        case 'ontem': { const o = somarDias(hoje, -1); return { de: o, ate: o }; }
        case '7d': return { de: somarDias(hoje, -6), ate: hoje };
        case '30d': return { de: somarDias(hoje, -29), ate: hoje };
        case 'mes': return { de: `${hoje.slice(0, 8)}01`, ate: hoje };
        case 'inicio': return { de: 'inicio', ate: hoje };
        case 'personalizado': return { de: estado.deCustom || hoje, ate: estado.ateCustom || hoje };
        default: return { de: hoje, ate: hoje };
    }
}

function qs(params) {
    const u = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
        if (v !== null && v !== undefined && v !== '') u.set(k, v);
    }
    return u.toString();
}

function fmtHora(iso) {
    return new Date(iso).toLocaleString('pt-BR', { timeZone: FUSO, hour: '2-digit', minute: '2-digit' });
}

function fmtDiaHora(iso) {
    return new Date(iso).toLocaleString('pt-BR', {
        timeZone: FUSO, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
}

function fmtDataCompleta(iso) {
    return new Date(iso).toLocaleString('pt-BR', { timeZone: FUSO, day: '2-digit', month: '2-digit', year: 'numeric' });
}

function fmtDiaISO(iso) {
    const [a, m, d] = iso.split('-');
    return `${d}/${m}/${a}`;
}

function fmtNumero(n) {
    return (n ?? 0).toLocaleString('pt-BR');
}

function chaveDe(ev) {
    return `${ev.tipo}:${ev.id}`;
}

function rotuloTipo(tipo) {
    return TIPOS[tipo]?.rotulo ?? tipo;
}

function rotuloCategoria(c) {
    return CATEGORIAS[c] ?? c ?? '';
}

function pessoaDe(ev) {
    return [ev.pessoa_nome, ev.pessoa_re && `RE ${ev.pessoa_re}`].filter(Boolean).join(' · ');
}

function autorDe(ev) {
    if (ev.autor_nome) return `${ev.autor_nome}${ev.autor_re ? ` (RE ${ev.autor_re})` : ''}`;
    if (ev.tipo === 'RETIRADA') return 'Limpeza geral do pátio';
    return '';
}

function detalheDe(ev) {
    return [rotuloCategoria(ev.categoria), ev.detalhe].filter(Boolean).join(' — ');
}

function periodoMultiDia() {
    return estado.periodo && estado.periodo.de !== estado.periodo.ate;
}

function textoPeriodo() {
    if (!estado.periodo) return '';
    const { de, ate } = estado.periodo;
    return de === ate ? fmtDiaISO(de) : `${fmtDiaISO(de)} a ${fmtDiaISO(ate)}`;
}

function mostrarErro(msg) {
    const el = document.getElementById('pg-erro');
    el.textContent = msg;
    el.hidden = !msg;
}

function ignoravel(err) {
    return err instanceof ApiError && err.status === 401;
}

function chipAtivo() {
    return CHIPS[estado.filtros.modulo].find((c) => c.grupo === estado.filtros.grupo) ?? null;
}

function subChipAtivo() {
    return SUB_CHIPS.find((s) => s.categoria === estado.filtros.categoria) ?? null;
}

function modoDentro() {
    return estado.filtros.grupo === DENTRO;
}

function diaSP(iso) {
    return new Date(iso).toLocaleDateString('en-CA', { timeZone: FUSO });
}

function fmtPermanencia(iso) {
    const minutos = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
    const h = Math.floor(minutos / 60);
    const m = minutos % 60;
    return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`;
}

// "Aba: Portaria · Filtro: Entradas · Terceiro" — usado na impressão.
function textoFiltroAtivo() {
    const chip = chipAtivo();
    const sub = subChipAtivo();
    return [
        `Aba: ${ABAS[estado.filtros.modulo]}`,
        chip && `Filtro: ${chip.rotulo}${sub ? ` · ${sub.rotulo}` : ''}`,
        estado.filtros.busca && `Busca: "${estado.filtros.busca}"`,
    ].filter(Boolean).join(' · ');
}

// ─── Header ─────────────────────────────────────────────────────────────
function initHeader() {
    const user = getCurrentUser();
    if (user) {
        document.getElementById('user-name').textContent = user.nome || '—';
        document.getElementById('user-meta').textContent = (user.re || '—').toUpperCase();
    }
    document.getElementById('btn-logout').addEventListener('click', () => {
        pararPolling();
        logout();
        window.location.replace('index.html');
    });
}

// ─── Topo: selo, "dados desde" e contadores ─────────────────────────────
function renderTopo() {
    const selo = document.getElementById('pg-selo');
    if (estado.periodo) {
        selo.hidden = false;
        selo.className = `pg-selo ${estado.periodo.inclui_hoje ? 'pg-selo-vivo' : 'pg-selo-hist'}`;
        selo.textContent = estado.periodo.inclui_hoje ? '● AO VIVO' : 'HISTÓRICO';
    }
    const pr = estado.resumo?.primeiro_registro;
    const partes = [];
    if (pr?.portaria) partes.push(`Dados da Portaria desde ${fmtDataCompleta(pr.portaria)}`);
    if (pr?.patio) partes.push(`do Pátio desde ${fmtDataCompleta(pr.patio)}`);
    const periodoTxt = textoPeriodo();
    document.getElementById('pg-desde').textContent =
        [periodoTxt && `Período: ${periodoTxt}`, partes.join(' · ')].filter(Boolean).join('  —  ') || '—';
}

// Os chips da aba atual, como botões (liga/desliga). O visual continua o de
// antes (número grande + rótulo); o ativo fica vermelho, como .filtro-btn.
// Re-renderiza a cada /resumo (30 s) — devolve o foco ao mesmo chip.
function renderContadores() {
    const caixa = document.getElementById('pg-contadores');
    const r = estado.resumo;
    if (!r) {
        caixa.innerHTML = '<div class="patio-loading">Carregando…</div>';
        renderSubChips();
        return;
    }
    const focado = document.activeElement?.closest?.('#pg-contadores [data-grupo]')?.dataset.grupo;
    const valores = { ...r.contadores, dentro_agora: r.dentro_agora };
    caixa.innerHTML = CHIPS[estado.filtros.modulo]
        .filter((c) => c.grupo !== DENTRO || r.dentro_agora !== null)
        .map((c) => {
            const ativo = c.grupo === estado.filtros.grupo;
            return `<button type="button" class="pg-contador pg-chip${ativo ? ' active' : ''}"
                    data-grupo="${c.grupo}" aria-pressed="${ativo}">
                <span class="pg-contador-num">${fmtNumero(valores[c.chave])}</span>
                <span class="pg-contador-rot">${c.rotulo}</span>
            </button>`;
        }).join('');
    if (focado) caixa.querySelector(`[data-grupo="${focado}"]`)?.focus();
    renderSubChips();
}

// Segunda fileira (D1): só com Entradas ou Saídas ativo.
function renderSubChips() {
    const caixa = document.getElementById('pg-subchips');
    const chip = chipAtivo();
    const contagem = estado.resumo?.categorias?.[chip?.chave];
    if (!chip?.categorias || !contagem) {
        caixa.hidden = true;
        caixa.innerHTML = '';
        return;
    }
    const focado = document.activeElement?.closest?.('#pg-subchips [data-categoria]')?.dataset.categoria;
    caixa.hidden = false;
    caixa.setAttribute('aria-label', `Categorias de ${chip.rotulo}`);
    caixa.innerHTML = SUB_CHIPS.map((s) => {
        const ativo = s.categoria === estado.filtros.categoria;
        return `<button type="button" class="filtro-btn pg-subchip${ativo ? ' active' : ''}"
                data-categoria="${s.categoria}" aria-pressed="${ativo}">
            ${s.rotulo} <span class="pg-subchip-num">${fmtNumero(contagem[s.categoria])}</span>
        </button>`;
    }).join('');
    if (focado) caixa.querySelector(`[data-categoria="${focado}"]`)?.focus();
}

async function carregarResumo() {
    const geracao = estado.geracao;
    try {
        const r = await apiGet(`/painel-gerencial/resumo?${qs(periodoDoPreset())}`);
        if (geracao !== estado.geracao) return;
        estado.resumo = r;
        estado.periodo = r.periodo;
        mostrarErro('');
        renderTopo();
        // Dentro agora ativo e o período deixou de incluir hoje: o chip some,
        // então o filtro também sai (senão a tabela ficaria presa nele).
        if (modoDentro() && r.dentro_agora === null) {
            estado.filtros.grupo = '';
            recarregarEventos();
        }
        renderContadores();
    } catch (err) {
        if (ignoravel(err)) return;
        mostrarErro(err.message || 'Falha ao carregar os contadores.');
    }
}

// ─── Lista de registros ─────────────────────────────────────────────────
// Uma <tr> por registro. No computador é tabela; no celular o CSS vira cada
// linha num cartão de uma linha só (hora · tipo · veículo · detalhe) — o
// resto está no detalhe, ao tocar.
function linhaEvento(ev) {
    const quando = periodoMultiDia() ? fmtDiaHora(ev.momento) : fmtHora(ev.momento);
    const modulo = ev.modulo === 'PATIO' ? 'patio' : 'portaria';
    return `
        <td class="pg-c-hora">${escapeHtml(quando)}</td>
        <td class="pg-c-tipo"><span class="pg-marca pg-marca-${modulo}" aria-hidden="true"></span>${escapeHtml(rotuloTipo(ev.tipo))}</td>
        <td class="pg-c-veiculo">${escapeHtml(ev.identificacao || '—')}</td>
        <td class="pg-c-detalhe">${escapeHtml(detalheDe(ev))}</td>
        <td class="pg-c-pessoa">${escapeHtml(pessoaDe(ev))}</td>
        <td class="pg-c-autor">${escapeHtml(autorDe(ev))}</td>`;
}

function criarLinha(ev, novo = false) {
    const tr = document.createElement('tr');
    tr.className = `pg-ev${novo ? ' pg-ev-novo' : ''}`;
    tr.tabIndex = 0;
    tr.dataset.chave = chaveDe(ev);
    tr.innerHTML = linhaEvento(ev);
    tr.addEventListener('click', () => abrirDetalhe(ev));
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') abrirDetalhe(ev); });
    if (novo) setTimeout(() => tr.classList.remove('pg-ev-novo'), 3000);
    return tr;
}

// A lista vazia fala do assunto: "Nenhuma entrada de terceiro neste período."
function textoVazio() {
    const chip = chipAtivo();
    const sub = subChipAtivo();
    const busca = estado.filtros.busca ? ' com essa busca' : '';
    if (!chip) {
        const doModulo = estado.filtros.modulo === 'PATIO' ? 'do Pátio' : 'da Portaria';
        return `Nenhum registro ${doModulo}${busca} neste período.`;
    }
    if (chip.grupo === DENTRO) return `${chip.nenhum}${busca}.`;
    return `${chip.nenhum}${sub ? ` ${sub.de}` : ''}${busca} neste período.`;
}

function linhaVazia(colunas) {
    return `<tr class="pg-vazio"><td colspan="${colunas}">${escapeHtml(textoVazio())}</td></tr>`;
}

function renderCabecalho(colunas) {
    document.getElementById('pg-cabecalho').innerHTML =
        `<tr>${colunas.map((c) => `<th scope="col">${c}</th>`).join('')}</tr>`;
}

function renderListaInteira() {
    renderCabecalho(CABECALHO_EVENTOS);
    const tbody = document.getElementById('pg-lista');
    tbody.innerHTML = '';
    if (estado.eventos.length === 0) {
        tbody.innerHTML = linhaVazia(CABECALHO_EVENTOS.length);
    } else {
        const frag = document.createDocumentFragment();
        for (const ev of estado.eventos) frag.appendChild(criarLinha(ev));
        tbody.appendChild(frag);
    }
    document.getElementById('pg-mais').hidden = !estado.temMais;
}

function paramsEventos(extra = {}) {
    const { modulo, grupo, categoria, busca } = estado.filtros;
    return qs({ ...periodoDoPreset(), modulo, grupo, categoria, busca, limit: LIMITE_PAGINA, ...extra });
}

// ─── Dentro agora (D2) ──────────────────────────────────────────────────
// Mesma regra da Portaria (D18, 36 h), calculada no backend. Lista curta:
// sem "Carregar mais"; o polling recarrega inteira (o tempo muda sozinho).
function linhaDentro(d) {
    const quando = diaSP(d.entrou_em) === dataLocalISO() ? fmtHora(d.entrou_em) : fmtDiaHora(d.entrou_em);
    const pessoa = [d.pessoa_nome, d.pessoa_re && `RE ${d.pessoa_re}`].filter(Boolean).join(' · ');
    return `
        <td class="pg-c-hora">${escapeHtml(quando)}</td>
        <td class="pg-c-veiculo">${escapeHtml(d.identificacao || '—')}</td>
        <td class="pg-c-tipo"><span class="pg-marca pg-marca-portaria" aria-hidden="true"></span>${escapeHtml(rotuloCategoria(d.categoria))}</td>
        <td class="pg-c-pessoa">${escapeHtml(pessoa)}</td>
        <td class="pg-c-detalhe pg-c-tempo">${escapeHtml(fmtPermanencia(d.entrou_em))}</td>`;
}

function renderDentro() {
    renderCabecalho(CABECALHO_DENTRO);
    const tbody = document.getElementById('pg-lista');
    tbody.innerHTML = '';
    document.getElementById('pg-mais').hidden = true;
    if (estado.dentro.length === 0) {
        tbody.innerHTML = linhaVazia(CABECALHO_DENTRO.length);
        return;
    }
    const frag = document.createDocumentFragment();
    for (const d of estado.dentro) {
        const tr = document.createElement('tr');
        tr.className = 'pg-ev';
        tr.tabIndex = 0;
        tr.innerHTML = linhaDentro(d);
        tr.addEventListener('click', () => abrirDetalheDentro(d));
        tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') abrirDetalheDentro(d); });
        frag.appendChild(tr);
    }
    tbody.appendChild(frag);
}

async function carregarDentro() {
    const geracao = estado.geracao;
    try {
        const r = await apiGet(`/painel-gerencial/dentro-agora?${qs({ busca: estado.filtros.busca })}`);
        if (geracao !== estado.geracao || !modoDentro()) return;
        estado.dentro = r.dentro;
        renderDentro();
    } catch (err) {
        if (ignoravel(err)) return;
        mostrarErro(err.message || 'Falha ao carregar quem está dentro.');
    }
}

// A tabela mostra registros ou, com Dentro agora ativo, a lista de dentro.
function carregarLista() {
    return modoDentro() ? carregarDentro() : carregarEventos();
}

async function carregarEventos() {
    const geracao = estado.geracao;
    try {
        const r = await apiGet(`/painel-gerencial/eventos?${paramsEventos()}`);
        if (geracao !== estado.geracao || modoDentro()) return;
        // Os contadores podem chegar depois — sem isto a primeira lista de um
        // período de vários dias sairia só com a hora, sem o dia.
        estado.periodo = estado.periodo ?? r.periodo;
        estado.eventos = r.eventos;
        estado.chaves = new Set(r.eventos.map(chaveDe));
        estado.temMais = r.tem_mais;
        renderListaInteira();
    } catch (err) {
        if (ignoravel(err)) return;
        mostrarErro(err.message || 'Falha ao carregar os registros.');
    }
}

async function carregarMais() {
    const ultimo = estado.eventos[estado.eventos.length - 1];
    if (!ultimo) return;
    const btn = document.getElementById('pg-mais');
    btn.disabled = true;
    const geracao = estado.geracao;
    try {
        const r = await apiGet(`/painel-gerencial/eventos?${paramsEventos({ antes: ultimo.momento, antes_chave: chaveDe(ultimo) })}`);
        if (geracao !== estado.geracao) return;
        const tbody = document.getElementById('pg-lista');
        for (const ev of r.eventos) {
            const k = chaveDe(ev);
            if (estado.chaves.has(k)) continue;
            estado.chaves.add(k);
            estado.eventos.push(ev);
            tbody.appendChild(criarLinha(ev));
        }
        estado.temMais = r.tem_mais;
        btn.hidden = !estado.temMais;
    } catch (err) {
        if (!ignoravel(err)) mostrarErro(err.message || 'Falha ao carregar mais.');
    } finally {
        btn.disabled = false;
    }
}

async function buscarNovos() {
    if (!estado.periodo?.inclui_hoje) return;
    if (modoDentro()) { await carregarDentro(); return; }
    const maisRecente = estado.eventos[0];
    const desde = maisRecente
        ? new Date(new Date(maisRecente.momento).getTime() - POLLING_SOBREPOSICAO_MS).toISOString()
        : null;
    const geracao = estado.geracao;
    try {
        const r = await apiGet(`/painel-gerencial/eventos?${paramsEventos(desde ? { desde } : {})}`);
        if (geracao !== estado.geracao || modoDentro()) return;
        const novos = r.eventos.filter((ev) => !estado.chaves.has(chaveDe(ev)));
        if (novos.length === 0) return;
        const tbody = document.getElementById('pg-lista');
        tbody.querySelector('.pg-vazio')?.remove();
        // r.eventos vem do mais novo pro mais velho; insere de trás pra
        // frente para o mais novo terminar no topo.
        for (const ev of novos.reverse()) {
            estado.chaves.add(chaveDe(ev));
            estado.eventos.unshift(ev);
            tbody.prepend(criarLinha(ev, true));
        }
        estado.eventos.sort((a, b) => (a.momento < b.momento ? 1 : -1));
    } catch (err) {
        if (!ignoravel(err)) console.warn('[painel] polling falhou:', err.message);
    }
}

// ─── Detalhe (modal só leitura) ─────────────────────────────────────────
function abrirDetalhe(ev) {
    const campos = [
        ['Quando', new Date(ev.momento).toLocaleString('pt-BR', { timeZone: FUSO })],
        ['Módulo', ev.modulo === 'PATIO' ? 'Pátio' : 'Portaria'],
        ['Tipo', rotuloTipo(ev.tipo)],
        ['Categoria', rotuloCategoria(ev.categoria)],
        [ev.modulo === 'PATIO' ? 'Ônibus' : 'Placa / prefixo', ev.identificacao],
        ['Detalhe', ev.detalhe],
        ['Pessoa', pessoaDe(ev)],
        ['Registrado por', autorDe(ev)],
    ].filter(([, v]) => v);
    document.getElementById('pg-modal-titulo').textContent = `${rotuloTipo(ev.tipo)} · ${ev.identificacao || ''}`;
    document.getElementById('pg-modal-corpo').innerHTML = campos
        .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('');
    document.getElementById('pg-modal').classList.add('open');
}

function abrirDetalheDentro(d) {
    const campos = [
        ['Entrou em', new Date(d.entrou_em).toLocaleString('pt-BR', { timeZone: FUSO })],
        ['Há quanto tempo', fmtPermanencia(d.entrou_em)],
        ['Placa / prefixo', d.identificacao],
        ['Categoria', rotuloCategoria(d.categoria)],
        ['Pessoa', [d.pessoa_nome, d.pessoa_re && `RE ${d.pessoa_re}`].filter(Boolean).join(' · ')],
        ['Detalhe', d.detalhe],
    ].filter(([, v]) => v);
    document.getElementById('pg-modal-titulo').textContent = `Dentro agora · ${d.identificacao || ''}`;
    document.getElementById('pg-modal-corpo').innerHTML = campos
        .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('');
    document.getElementById('pg-modal').classList.add('open');
}

function fecharDetalhe() {
    document.getElementById('pg-modal').classList.remove('open');
}

// ─── Abas, chips e busca ────────────────────────────────────────────────
function initAbas() {
    const botoes = document.querySelectorAll('.pg-abas [data-aba]');
    botoes.forEach((b) => b.addEventListener('click', () => {
        if (estado.filtros.modulo === b.dataset.aba) return;
        estado.filtros.modulo = b.dataset.aba;
        // Trocar de aba limpa o chip (cada aba só tem os seus).
        estado.filtros.grupo = '';
        estado.filtros.categoria = '';
        botoes.forEach((x) => {
            x.classList.toggle('active', x === b);
            x.setAttribute('aria-selected', x === b ? 'true' : 'false');
        });
        renderContadores();
        recarregarEventos();
    }));
}

// Clique liga; clique no mesmo desliga e volta tudo da aba. Um por vez.
function initChips() {
    document.getElementById('pg-contadores').addEventListener('click', (e) => {
        const b = e.target.closest('[data-grupo]');
        if (!b) return;
        estado.filtros.grupo = estado.filtros.grupo === b.dataset.grupo ? '' : b.dataset.grupo;
        estado.filtros.categoria = '';
        renderContadores();
        recarregarEventos();
    });
    document.getElementById('pg-subchips').addEventListener('click', (e) => {
        const b = e.target.closest('[data-categoria]');
        if (!b) return;
        estado.filtros.categoria = estado.filtros.categoria === b.dataset.categoria ? '' : b.dataset.categoria;
        renderSubChips();
        recarregarEventos();
    });
}

function initFiltros() {
    let espera = null;
    document.getElementById('pg-f-busca').addEventListener('input', (e) => {
        clearTimeout(espera);
        espera = setTimeout(() => {
            estado.filtros.busca = e.target.value.trim();
            recarregarEventos();
        }, 400);
    });
    document.getElementById('pg-mais').addEventListener('click', carregarMais);
}

function recarregarEventos() {
    estado.geracao += 1;
    carregarLista();
}

// ─── Período ────────────────────────────────────────────────────────────
function initPeriodo() {
    const botoes = document.querySelectorAll('#pg-periodos [data-preset]');
    const caixa = document.getElementById('pg-personalizado');
    const hoje = dataLocalISO();
    document.getElementById('pg-de').value = hoje;
    document.getElementById('pg-ate').value = hoje;
    document.getElementById('pg-de').max = hoje;
    document.getElementById('pg-ate').max = hoje;

    botoes.forEach((b) => b.addEventListener('click', () => {
        botoes.forEach((x) => x.classList.toggle('active', x === b));
        if (b.dataset.preset === 'personalizado') {
            caixa.hidden = false;
            return;
        }
        caixa.hidden = true;
        estado.preset = b.dataset.preset;
        recarregarTudo();
    }));

    document.getElementById('pg-aplicar').addEventListener('click', () => {
        const de = document.getElementById('pg-de').value;
        const ate = document.getElementById('pg-ate').value;
        if (!de || !ate) { mostrarErro('Escolha as duas datas.'); return; }
        if (de > ate) { mostrarErro('A data inicial não pode ser depois da final.'); return; }
        estado.preset = 'personalizado';
        estado.deCustom = de;
        estado.ateCustom = ate;
        recarregarTudo();
    });
}

async function recarregarTudo() {
    pararPolling();
    // Dentro agora só existe com o período incluindo hoje.
    if (modoDentro() && periodoDoPreset().ate !== dataLocalISO()) estado.filtros.grupo = '';
    estado.geracao += 1;
    estado.resumo = null;
    estado.periodo = null;
    renderContadores();
    await Promise.all([carregarResumo(), carregarLista()]);
    iniciarPolling();
}

// ─── Tempo real ─────────────────────────────────────────────────────────
function iniciarPolling() {
    pararPolling();
    if (!estado.periodo?.inclui_hoje || document.hidden) return;
    timerEventos = setInterval(buscarNovos, POLLING_INTERVAL_MS);
    timerResumo = setInterval(carregarResumo, RESUMO_INTERVALO_MS);
}

function pararPolling() {
    clearInterval(timerEventos);
    clearInterval(timerResumo);
    timerEventos = null;
    timerResumo = null;
}

document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        pararPolling();
    } else if (estado.periodo?.inclui_hoje) {
        // Voltou para a aba: atualiza já, depois retoma o ritmo normal.
        buscarNovos();
        carregarResumo();
        iniciarPolling();
    }
});

// ─── Exportar CSV ───────────────────────────────────────────────────────
// Sai exatamente o que a tabela mostra: aba + chip + sub-chip + busca; com
// Dentro agora ativo, a lista de dentro. O nome do arquivo diz o assunto:
// painel-portaria-entradas-terceiro-2026-09-26.csv
function nomeArquivoCsv() {
    const { modulo, grupo } = estado.filtros;
    const partes = ['painel', modulo === 'PATIO' ? 'patio' : 'portaria'];
    if (grupo === DENTRO) {
        partes.push('dentro-agora', dataLocalISO());
    } else {
        if (grupo) partes.push(grupo.toLowerCase());
        const sub = subChipAtivo();
        if (sub) partes.push(sub.arquivo);
        const { de, ate } = estado.periodo ?? periodoDoPreset();
        partes.push(de === ate ? de : `${de}_a_${ate}`);
    }
    return `${partes.join('-')}.csv`;
}

// Link direto não leva o Bearer — fetch + blob.
async function exportarCsv() {
    const btn = document.getElementById('pg-btn-csv');
    btn.disabled = true;
    try {
        const { modulo, grupo, categoria, busca } = estado.filtros;
        const url = modoDentro()
            ? `/painel-gerencial/dentro-agora.csv?${qs({ busca })}`
            : `/painel-gerencial/exportar.csv?${qs({ ...periodoDoPreset(), modulo, grupo, categoria, busca })}`;
        const resp = await fetch(`${API_BASE_URL}${url}`, {
            headers: { Authorization: `Bearer ${localStorage.getItem(TOKEN_KEY)}` },
        });
        if (resp.status === 401) {
            logout();
            window.location.replace('index.html');
            return;
        }
        if (!resp.ok) throw new Error(`Falha ao exportar (HTTP ${resp.status}).`);
        const blob = await resp.blob();
        const link = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = link;
        a.download = nomeArquivoCsv();
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(link), 1000);
    } catch (err) {
        mostrarErro(err.message || 'Falha ao exportar.');
    } finally {
        btn.disabled = false;
    }
}

// ─── Imprimir ───────────────────────────────────────────────────────────
// Mesmo mecanismo das outras impressões do V3: monta a folha em
// #print-content (o @media print global do style.css esconde o resto).
// Sai o cabeçalho (período, emissão, aba e filtro ativo), os chips DA ABA
// com o ativo marcado (e os sub-chips, se abertos) e a lista filtrada — a
// que está carregada na tela.
function imprimir() {
    const filtros = textoFiltroAtivo();
    const emitido = new Date().toLocaleString('pt-BR', { timeZone: FUSO });
    let nota;
    if (modoDentro()) {
        nota = `${fmtNumero(estado.dentro.length)} veículos dentro agora`;
    } else {
        const qtd = estado.eventos.length;
        nota = estado.temMais
            ? `${fmtNumero(qtd)} registros mais recentes (há mais no período — use Exportar CSV para a lista completa)`
            : `${fmtNumero(qtd)} registros`;
    }

    const contadores = document.getElementById('pg-contadores').cloneNode(true);
    contadores.removeAttribute('id');
    const subChips = document.getElementById('pg-subchips');
    const subChipsCopia = subChips.hidden ? null : subChips.cloneNode(true);
    subChipsCopia?.removeAttribute('id');
    const tabela = document.querySelector('.pg-tabela').cloneNode(true);
    tabela.querySelector('tbody').removeAttribute('id');

    let area = document.getElementById('print-content');
    if (!area) {
        area = document.createElement('div');
        area.id = 'print-content';
        document.body.appendChild(area);
    }
    area.innerHTML = `
        <div class="pg-print-folha">
            <div class="pg-print-cabecalho">
                <div>
                    <div class="pg-print-titulo">Painel Gerencial</div>
                    <div class="pg-print-meta">Período: ${escapeHtml(textoPeriodo())}</div>
                    <div class="pg-print-meta">${escapeHtml(filtros)} · ${escapeHtml(nota)}</div>
                </div>
                <div class="pg-print-meta">Sambaíba Transportes Urbanos<br>Emitido em ${escapeHtml(emitido)}</div>
            </div>
        </div>`;
    const folha = area.querySelector('.pg-print-folha');
    folha.appendChild(contadores);
    if (subChipsCopia) folha.appendChild(subChipsCopia);
    folha.appendChild(tabela);
    window.print();
}

// ─── Início ─────────────────────────────────────────────────────────────
function init() {
    initHeader();
    initPeriodo();
    initAbas();
    initChips();
    initFiltros();
    document.getElementById('pg-btn-csv').addEventListener('click', exportarCsv);
    document.getElementById('pg-btn-imprimir').addEventListener('click', imprimir);
    document.getElementById('pg-modal-fechar').addEventListener('click', fecharDetalhe);
    document.getElementById('pg-modal').addEventListener('click', (e) => {
        if (e.target.id === 'pg-modal') fecharDetalhe();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') fecharDetalhe(); });
    recarregarTudo();
}

init();
