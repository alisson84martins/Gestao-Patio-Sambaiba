/*
 * painel-gerencial.page.js — Painel Gerencial (migration 046)
 * -------------------------------------------------------------------------------
 * Tudo que a Portaria e o Pátio registram, ao vivo e histórico. SÓ LEITURA:
 * nenhuma chamada aqui escreve nada.
 *
 * Quatro abas: Visão geral · Portaria · Pátio · Linha do tempo. Tudo vem de
 * duas rotas: /painel-gerencial/resumo (números e gráficos, numa chamada) e
 * /painel-gerencial/eventos (a linha do tempo). Gráficos em SVG feito à mão —
 * sem biblioteca externa.
 *
 * Dia = dia do RELÓGIO em São Paulo. As datas saem de dataLocalISO()
 * (⛔ nunca toISOString().slice(0,10) — erra o dia depois das 21h); quem corta
 * o dia em UTC é o backend (intervalo_utc). Horas exibidas sempre com
 * timeZone 'America/Sao_Paulo'.
 *
 * Tempo real só quando o período inclui hoje: eventos a cada
 * POLLING_INTERVAL_MS, resumo a cada 30 s, tudo pausado com a aba escondida.
 * Período passado carrega uma vez, sem polling.
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

const DIAS_SEMANA = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];  // isodow 1..7

// ─── Estado ─────────────────────────────────────────────────────────────
const estado = {
    preset: 'hoje',
    deCustom: null,
    ateCustom: null,
    aba: 'geral',
    resumo: null,
    periodo: null,          // o que o backend resolveu (de, ate, inclui_hoje)
    eventos: [],
    chaves: new Set(),
    temMais: false,
    filtros: { modulo: '', tipo: '', busca: '' },
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

function autorDe(ev) {
    if (ev.autor_nome) return `por ${ev.autor_nome}${ev.autor_re ? ` (RE ${ev.autor_re})` : ''}`;
    if (ev.tipo === 'RETIRADA') return 'Limpeza geral do pátio';
    return '';
}

function periodoMultiDia() {
    return estado.periodo && estado.periodo.de !== estado.periodo.ate;
}

function mostrarErro(msg) {
    const el = document.getElementById('pg-erro');
    el.textContent = msg;
    el.hidden = !msg;
}

function ignoravel(err) {
    return err instanceof ApiError && err.status === 401;
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

// ─── Gráficos em SVG ────────────────────────────────────────────────────
// Cores vêm dos tokens do style.css (var(--…)) para herdar o tema.

function svgVazio(msg = 'Sem registro no período.') {
    return `<div class="pg-vazio">${escapeHtml(msg)}</div>`;
}

/** Barras horizontais ordenadas; com `pareto`, linha de % acumulado por cima. */
function barrasHorizontais(itens, { pareto = false, rotulo = (i) => i.chave, cor = 'var(--accent)' } = {}) {
    if (!itens || itens.length === 0) return svgVazio();
    const lista = itens.slice(0, 12);
    const total = itens.reduce((s, i) => s + i.total, 0);
    const max = Math.max(...lista.map((i) => i.total), 1);
    const W = 360, esq = 120, dir = pareto ? 64 : 40, alt = 24, H = lista.length * alt + 6;
    const larg = W - esq - dir;
    let acumulado = 0;
    const pontos = [];
    const linhas = lista.map((item, idx) => {
        const y = idx * alt + 4;
        const w = Math.max(2, (item.total / max) * larg);
        acumulado += item.total;
        const pct = total ? (acumulado * 100) / total : 0;
        pontos.push(`${esq + (pct / 100) * larg},${y + 9}`);
        const nome = String(rotulo(item) ?? '—');
        const nomeCurto = nome.length > 18 ? `${nome.slice(0, 17)}…` : nome;
        return `
            <text x="${esq - 6}" y="${y + 13}" class="pg-svg-rot" text-anchor="end"><title>${escapeHtml(nome)}</title>${escapeHtml(nomeCurto)}</text>
            <rect x="${esq}" y="${y + 2}" width="${w}" height="14" rx="3" fill="${cor}"></rect>
            <text x="${esq + w + 4}" y="${y + 13}" class="pg-svg-val">${fmtNumero(item.total)}</text>
            ${pareto ? `<text x="${W - 2}" y="${y + 13}" class="pg-svg-pct" text-anchor="end">${pct.toFixed(0)}%</text>` : ''}`;
    }).join('');
    const linhaPareto = pareto && lista.length > 1
        ? `<polyline points="${pontos.join(' ')}" class="pg-svg-pareto"></polyline>
           ${pontos.map((p) => { const [x, y] = p.split(','); return `<circle cx="${x}" cy="${y}" r="2.5" class="pg-svg-pareto-ponto"></circle>`; }).join('')}`
        : '';
    return `<svg viewBox="0 0 ${W} ${H}" class="pg-svg" role="img" aria-label="Gráfico de barras">${linhas}${linhaPareto}</svg>`;
}

/** Barras verticais agrupadas: series = [{ chave, cor }], dados = [{ rotulo, valores: {chave: n} }]. */
function barrasVerticais(dados, series, { rotuloACada = 1 } = {}) {
    if (!dados || dados.length === 0) return svgVazio();
    const W = 600, H = 190, base = 160, topo = 12, esq = 30;
    const max = Math.max(1, ...dados.flatMap((d) => series.map((s) => d.valores[s.chave] || 0)));
    const passo = (W - esq - 4) / dados.length;
    const largBarra = Math.max(1, (passo * 0.8) / series.length);
    const escala = (v) => ((base - topo) * v) / max;
    const grade = [0, 0.5, 1].map((f) => {
        const y = base - (base - topo) * f;
        return `<line x1="${esq}" x2="${W}" y1="${y}" y2="${y}" class="pg-svg-grade"></line>
                <text x="${esq - 4}" y="${y + 3}" class="pg-svg-eixo" text-anchor="end">${fmtNumero(Math.round(max * f))}</text>`;
    }).join('');
    const barras = dados.map((d, i) => {
        const x0 = esq + i * passo + passo * 0.1;
        const cols = series.map((s, j) => {
            const v = d.valores[s.chave] || 0;
            const h = escala(v);
            return `<rect x="${x0 + j * largBarra}" y="${base - h}" width="${largBarra}" height="${h}" fill="${s.cor}"><title>${escapeHtml(d.rotulo)} · ${escapeHtml(s.nome)}: ${fmtNumero(v)}</title></rect>`;
        }).join('');
        const rot = (i % rotuloACada === 0 || i === dados.length - 1)
            ? `<text x="${esq + i * passo + passo / 2}" y="${base + 14}" class="pg-svg-eixo" text-anchor="middle">${escapeHtml(d.rotulo)}</text>`
            : '';
        return cols + rot;
    }).join('');
    return `<svg viewBox="0 0 ${W} ${H}" class="pg-svg" role="img" aria-label="Gráfico de barras por período">${grade}${barras}</svg>`;
}

/** Mapa de calor 7 × 24 (dia da semana × hora, SP). */
function mapaDeCalor(porHora) {
    if (!porHora || porHora.length === 0) return svgVazio();
    const matriz = Array.from({ length: 7 }, () => Array(24).fill(0));
    for (const c of porHora) matriz[c.dow - 1][c.hora] = c.total;
    const max = Math.max(1, ...matriz.flat());
    const cel = 22, esq = 34, topo = 16, W = esq + 24 * cel, H = topo + 7 * cel + 2;
    let svg = '';
    for (let h = 0; h < 24; h += 3) {
        svg += `<text x="${esq + h * cel + cel / 2}" y="11" class="pg-svg-eixo" text-anchor="middle">${h}h</text>`;
    }
    matriz.forEach((linha, d) => {
        svg += `<text x="${esq - 6}" y="${topo + d * cel + 15}" class="pg-svg-eixo" text-anchor="end">${DIAS_SEMANA[d]}</text>`;
        linha.forEach((v, h) => {
            const op = v === 0 ? 0 : 0.12 + 0.88 * (v / max);
            svg += `<rect x="${esq + h * cel + 1}" y="${topo + d * cel + 1}" width="${cel - 2}" height="${cel - 2}" rx="3"
                      class="${v === 0 ? 'pg-calor-zero' : 'pg-calor'}" fill-opacity="${op.toFixed(2)}"><title>${DIAS_SEMANA[d]} ${h}h: ${fmtNumero(v)}</title></rect>`;
        });
    });
    return `<svg viewBox="0 0 ${W} ${H}" class="pg-svg" role="img" aria-label="Mapa de calor por dia da semana e hora">${svg}</svg>`;
}

// ─── Render: Visão geral ────────────────────────────────────────────────
// Direção "ruim" de cada indicador: sobe = vermelho para recolhida, avaria e
// movimentação (retrabalho de pátio); entradas, saídas e alocações são
// volume, sem juízo — o indicador fica neutro.
const KPIS = [
    { chave: 'entradas', rotulo: 'Entradas', ruim: null },
    { chave: 'saidas', rotulo: 'Saídas', ruim: null },
    { chave: 'dentro', rotulo: 'Dentro agora', ruim: null },
    { chave: 'recolhidas', rotulo: 'Recolhidas', ruim: 'sobe' },
    { chave: 'avarias', rotulo: 'Avarias', ruim: 'sobe' },
    { chave: 'alocacoes', rotulo: 'Alocações', ruim: null },
    { chave: 'movimentacoes', rotulo: 'Movimentações', ruim: 'sobe' },
];

function indicador(k, def) {
    if (!k || k.variacao_pct === null || k.variacao_pct === undefined) {
        return '<span class="pg-kpi-var pg-neutro">— sem base anterior</span>';
    }
    const pct = k.variacao_pct;
    if (pct === 0) return '<span class="pg-kpi-var pg-neutro">= igual ao período anterior</span>';
    const seta = pct > 0 ? '▲' : '▼';
    let classe = 'pg-neutro';
    if (def.ruim === 'sobe') classe = pct > 0 ? 'pg-ruim' : 'pg-bom';
    return `<span class="pg-kpi-var ${classe}">${seta} ${Math.abs(pct).toLocaleString('pt-BR')}%</span>`;
}

function renderKpis(r) {
    const el = document.getElementById('pg-kpis');
    el.innerHTML = KPIS.map((def) => {
        if (def.chave === 'dentro') {
            const v = r.dentro_agora;
            return `<div class="pg-kpi">
                <div class="pg-kpi-rot">${def.rotulo}</div>
                <div class="pg-kpi-num">${v === null || v === undefined ? '—' : fmtNumero(v)}</div>
                <span class="pg-kpi-var pg-neutro">${v === null || v === undefined ? 'só no período de hoje' : 'veículos na garagem'}</span>
            </div>`;
        }
        const k = r.kpis[def.chave];
        const extra = def.chave === 'recolhidas'
            ? `<span class="pg-kpi-sub">${fmtNumero(k.abertas)} abertas · ${fmtNumero(k.encerradas)} encerradas</span>`
            : '';
        return `<div class="pg-kpi">
            <div class="pg-kpi-rot">${def.rotulo}</div>
            <div class="pg-kpi-num">${fmtNumero(k.valor)}</div>
            ${indicador(k, def)}
            ${extra}
        </div>`;
    }).join('');
}

function renderGeral(r) {
    renderKpis(r);

    const blocoDia = document.getElementById('pg-bloco-por-dia');
    blocoDia.hidden = !periodoMultiDia();
    if (periodoMultiDia()) {
        // Preenche os dias sem registro com zero — senão o eixo "encolhe".
        const porDia = new Map((r.por_dia || []).map((d) => [d.dia, d]));
        const dados = [];
        for (let dia = r.periodo.de; dia <= r.periodo.ate; dia = somarDias(dia, 1)) {
            const d = porDia.get(dia) || { portaria: 0, patio: 0 };
            dados.push({ rotulo: fmtDiaISO(dia).slice(0, 5), valores: { portaria: d.portaria, patio: d.patio } });
        }
        document.getElementById('pg-graf-dia').innerHTML = barrasVerticais(dados, [
            { chave: 'portaria', nome: 'Portaria', cor: 'var(--accent)' },
            { chave: 'patio', nome: 'Pátio', cor: 'var(--accent4)' },
        ], { rotuloACada: Math.max(1, Math.ceil(dados.length / 8)) });
    }

    document.getElementById('pg-graf-calor').innerHTML = mapaDeCalor(r.por_hora);
}

// ─── Render: Portaria ───────────────────────────────────────────────────
function renderPortaria(r) {
    document.getElementById('pg-pareto-motivo').innerHTML =
        barrasHorizontais(r.recolhidas_por_motivo, { pareto: true, rotulo: (i) => rotuloCategoria(i.chave) });
    document.getElementById('pg-pareto-defeito').innerHTML =
        barrasHorizontais(r.recolhidas_por_defeito, { pareto: true });
    document.getElementById('pg-avarias-zona').innerHTML = barrasHorizontais(r.avarias_por_zona);
    document.getElementById('pg-top-veiculos').innerHTML = barrasHorizontais(r.top_veiculos_portaria);
    document.getElementById('pg-top-recolhida').innerHTML = barrasHorizontais(r.top_carros_recolhida);
    document.getElementById('pg-registrante-portaria').innerHTML = barrasHorizontais(
        (r.por_registrante || []).filter((p) => p.modulo === 'PORTARIA'),
        { rotulo: (i) => i.autor_nome || 'Sem autor' },
    );
    const p = r.permanencia_terceiros_min || {};
    document.getElementById('pg-permanencia').innerHTML = p.amostras
        ? `<div><span class="pg-num-grande">${fmtNumero(Number(p.media))}</span><span class="pg-num-rot">min em média</span></div>
           <div><span class="pg-num-grande">${fmtNumero(Number(p.mediana))}</span><span class="pg-num-rot">min na mediana</span></div>
           <div class="pg-ajuda">${fmtNumero(p.amostras)} saídas de terceiro com entrada correspondente.</div>`
        : svgVazio('Nenhuma saída de terceiro com entrada correspondente no período.');
}

// ─── Render: Pátio ──────────────────────────────────────────────────────
function renderPatio(r) {
    const porHora = new Map((r.patio_por_hora || []).map((h) => [h.hora, h]));
    const dados = Array.from({ length: 24 }, (_, h) => {
        const x = porHora.get(h) || {};
        return { rotulo: `${h}h`, valores: { alocacoes: x.alocacoes || 0, movimentacoes: x.movimentacoes || 0 } };
    });
    const temAlgo = dados.some((d) => d.valores.alocacoes || d.valores.movimentacoes);
    document.getElementById('pg-graf-patio-hora').innerHTML = temAlgo
        ? barrasVerticais(dados, [
            { chave: 'alocacoes', nome: 'Alocações', cor: 'var(--accent4)' },
            { chave: 'movimentacoes', nome: 'Movimentações', cor: 'var(--accent)' },
        ], { rotuloACada: 3 })
        : svgVazio();
    document.getElementById('pg-top-movimentados').innerHTML =
        barrasHorizontais(r.top_carros_movimentados_patio, { cor: 'var(--accent4)' });
    document.getElementById('pg-registrante-patio').innerHTML = barrasHorizontais(
        (r.por_registrante || []).filter((p) => p.modulo === 'PATIO'),
        { rotulo: (i) => i.autor_nome || 'Limpeza geral do pátio', cor: 'var(--accent4)' },
    );
}

// ─── Topo: selo e "dados desde" ─────────────────────────────────────────
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
    const periodoTxt = estado.periodo
        ? (estado.periodo.de === estado.periodo.ate
            ? fmtDiaISO(estado.periodo.de)
            : `${fmtDiaISO(estado.periodo.de)} a ${fmtDiaISO(estado.periodo.ate)}`)
        : '';
    document.getElementById('pg-desde').textContent =
        [periodoTxt && `Período: ${periodoTxt}`, partes.join(' · ')].filter(Boolean).join('  —  ') || '—';
}

// ─── Carga do resumo ────────────────────────────────────────────────────
async function carregarResumo() {
    const geracao = estado.geracao;
    const p = periodoDoPreset();
    try {
        const r = await apiGet(`/painel-gerencial/resumo?${qs(p)}`);
        if (geracao !== estado.geracao) return;
        estado.resumo = r;
        estado.periodo = r.periodo;
        mostrarErro('');
        renderTopo();
        renderGeral(r);
        renderPortaria(r);
        renderPatio(r);
    } catch (err) {
        if (ignoravel(err)) return;
        mostrarErro(err.message || 'Falha ao carregar o resumo.');
    }
}

// ─── Linha do tempo ─────────────────────────────────────────────────────
function linhaEvento(ev) {
    const quando = periodoMultiDia() ? fmtDiaHora(ev.momento) : fmtHora(ev.momento);
    const cat = rotuloCategoria(ev.categoria);
    const det = ev.detalhe ? ` — ${ev.detalhe}` : '';
    const autor = autorDe(ev);
    return `
        <span class="pg-ev-marca pg-ev-${ev.modulo === 'PATIO' ? 'patio' : 'portaria'}" aria-hidden="true"></span>
        <span class="pg-ev-hora">${escapeHtml(quando)}</span>
        <span class="pg-ev-texto">
            <strong>${escapeHtml(rotuloTipo(ev.tipo).toUpperCase())}</strong>
            · <span class="pg-ev-id">${escapeHtml(ev.identificacao || '—')}</span>
            ${cat ? ` · ${escapeHtml(cat)}` : ''}${escapeHtml(det)}
            ${autor ? `<span class="pg-ev-autor"> · ${escapeHtml(autor)}</span>` : ''}
        </span>`;
}

function criarItem(ev, novo = false) {
    const li = document.createElement('li');
    li.className = `pg-ev${novo ? ' pg-ev-novo' : ''}`;
    li.tabIndex = 0;
    li.dataset.chave = chaveDe(ev);
    li.innerHTML = linhaEvento(ev);
    li.addEventListener('click', () => abrirDetalhe(ev));
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter') abrirDetalhe(ev); });
    if (novo) setTimeout(() => li.classList.remove('pg-ev-novo'), 3000);
    return li;
}

function renderLinhaInteira() {
    const ol = document.getElementById('pg-linha');
    ol.innerHTML = '';
    if (estado.eventos.length === 0) {
        ol.innerHTML = '<li class="pg-vazio">Nenhum registro com esses filtros no período.</li>';
    } else {
        const frag = document.createDocumentFragment();
        for (const ev of estado.eventos) frag.appendChild(criarItem(ev));
        ol.appendChild(frag);
    }
    document.getElementById('pg-mais').hidden = !estado.temMais;
}

function paramsEventos(extra = {}) {
    return qs({ ...periodoDoPreset(), ...estado.filtros, limit: LIMITE_PAGINA, ...extra });
}

async function carregarEventos() {
    const geracao = estado.geracao;
    try {
        const r = await apiGet(`/painel-gerencial/eventos?${paramsEventos()}`);
        if (geracao !== estado.geracao) return;
        // O resumo pode chegar depois — sem isto a primeira lista de um
        // período de vários dias sairia só com a hora, sem o dia.
        estado.periodo = estado.periodo ?? r.periodo;
        estado.eventos = r.eventos;
        estado.chaves = new Set(r.eventos.map(chaveDe));
        estado.temMais = r.tem_mais;
        renderLinhaInteira();
    } catch (err) {
        if (ignoravel(err)) return;
        mostrarErro(err.message || 'Falha ao carregar a linha do tempo.');
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
        const ol = document.getElementById('pg-linha');
        for (const ev of r.eventos) {
            const k = chaveDe(ev);
            if (estado.chaves.has(k)) continue;
            estado.chaves.add(k);
            estado.eventos.push(ev);
            ol.appendChild(criarItem(ev));
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
    const maisRecente = estado.eventos[0];
    const desde = maisRecente
        ? new Date(new Date(maisRecente.momento).getTime() - POLLING_SOBREPOSICAO_MS).toISOString()
        : null;
    const geracao = estado.geracao;
    try {
        const r = await apiGet(`/painel-gerencial/eventos?${paramsEventos(desde ? { desde } : {})}`);
        if (geracao !== estado.geracao) return;
        const novos = r.eventos.filter((ev) => !estado.chaves.has(chaveDe(ev)));
        if (novos.length === 0) return;
        const ol = document.getElementById('pg-linha');
        ol.querySelector('.pg-vazio')?.remove();
        // r.eventos vem do mais novo pro mais velho; insere de trás pra
        // frente para o mais novo terminar no topo.
        for (const ev of novos.reverse()) {
            estado.chaves.add(chaveDe(ev));
            estado.eventos.unshift(ev);
            ol.prepend(criarItem(ev, true));
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
        ['Envolvido', [ev.pessoa_nome, ev.pessoa_re && `RE ${ev.pessoa_re}`].filter(Boolean).join(' · ')],
        ['Registrado por', autorDe(ev).replace(/^por /, '')],
    ].filter(([, v]) => v);
    document.getElementById('pg-modal-titulo').textContent = `${rotuloTipo(ev.tipo)} · ${ev.identificacao || ''}`;
    document.getElementById('pg-modal-corpo').innerHTML = campos
        .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('');
    document.getElementById('pg-modal').classList.add('open');
}

function fecharDetalhe() {
    document.getElementById('pg-modal').classList.remove('open');
}

// ─── Filtros da linha do tempo ──────────────────────────────────────────
function preencherTipos() {
    const sel = document.getElementById('pg-f-tipo');
    const modulo = estado.filtros.modulo;
    const atual = estado.filtros.tipo;
    const opcoes = Object.entries(TIPOS).filter(([, t]) => !modulo || t.modulo === modulo);
    sel.innerHTML = '<option value="">Todos os tipos</option>'
        + opcoes.map(([k, t]) => `<option value="${k}">${escapeHtml(t.rotulo)}</option>`).join('');
    if (opcoes.some(([k]) => k === atual)) sel.value = atual;
    else estado.filtros.tipo = '';
}

function initFiltros() {
    preencherTipos();
    document.getElementById('pg-f-modulo').addEventListener('change', (e) => {
        estado.filtros.modulo = e.target.value;
        preencherTipos();
        recarregarEventos();
    });
    document.getElementById('pg-f-tipo').addEventListener('change', (e) => {
        estado.filtros.tipo = e.target.value;
        recarregarEventos();
    });
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
    carregarEventos();
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
    estado.geracao += 1;
    estado.resumo = null;
    estado.periodo = null;
    await Promise.all([carregarResumo(), carregarEventos()]);
    iniciarPolling();
}

// ─── Abas ───────────────────────────────────────────────────────────────
function initAbas() {
    const botoes = document.querySelectorAll('.pg-abas [data-aba]');
    botoes.forEach((b) => b.addEventListener('click', () => {
        estado.aba = b.dataset.aba;
        botoes.forEach((x) => {
            x.classList.toggle('active', x === b);
            x.setAttribute('aria-selected', x === b ? 'true' : 'false');
        });
        document.querySelectorAll('.pg-aba').forEach((s) => {
            s.hidden = s.id !== `pg-aba-${estado.aba}`;
        });
    }));
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
// Link direto não leva o Bearer — fetch + blob.
async function exportarCsv() {
    const btn = document.getElementById('pg-btn-csv');
    btn.disabled = true;
    try {
        const params = qs({ ...periodoDoPreset(), ...estado.filtros });
        const resp = await fetch(`${API_BASE_URL}/painel-gerencial/exportar.csv?${params}`, {
            headers: { Authorization: `Bearer ${localStorage.getItem(TOKEN_KEY)}` },
        });
        if (resp.status === 401) {
            logout();
            window.location.replace('index.html');
            return;
        }
        if (!resp.ok) throw new Error(`Falha ao exportar (HTTP ${resp.status}).`);
        const blob = await resp.blob();
        const nome = /filename="([^"]+)"/.exec(resp.headers.get('content-disposition') || '')?.[1]
            || 'painel-gerencial.csv';
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = nome;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
        mostrarErro(err.message || 'Falha ao exportar.');
    } finally {
        btn.disabled = false;
    }
}

// ─── Imprimir ───────────────────────────────────────────────────────────
// Mesmo mecanismo das outras impressões do V3: monta a folha em
// #print-content (o @media print global do style.css esconde o resto).
function imprimir() {
    const aba = document.getElementById(`pg-aba-${estado.aba}`);
    const titulo = document.querySelector(`.pg-abas [data-aba="${estado.aba}"]`)?.textContent || '';
    const conteudo = aba.cloneNode(true);
    conteudo.hidden = false;
    conteudo.querySelectorAll('button, select, input, .pg-filtros').forEach((n) => n.remove());
    conteudo.querySelectorAll('[id]').forEach((n) => n.removeAttribute('id'));

    const emitido = new Date().toLocaleString('pt-BR', { timeZone: FUSO });
    const periodo = document.getElementById('pg-desde').textContent;

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
                    <div class="pg-print-titulo">Painel Gerencial — ${escapeHtml(titulo)}</div>
                    <div class="pg-print-meta">${escapeHtml(periodo)}</div>
                </div>
                <div class="pg-print-meta">Sambaíba Transportes Urbanos<br>Emitido em ${escapeHtml(emitido)}</div>
            </div>
        </div>`;
    area.querySelector('.pg-print-folha').appendChild(conteudo);
    window.print();
}

// ─── Início ─────────────────────────────────────────────────────────────
function init() {
    initHeader();
    initPeriodo();
    initAbas();
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
