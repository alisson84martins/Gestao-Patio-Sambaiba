/*
 * ao-vivo.fiscal.js — a tela do fiscal ao vivo (v4.0 fase 3, bloco E)
 * -------------------------------------------------------------------------------
 * O topo de ao-vivo.html, de cima para baixo (celular, 360 px):
 *   1. cabeçalho — linha · terminal · fiscal/período · selo "NIMER ✓ 14:13"
 *      (ou "Previsto"); ponto com várias linhas = abas de linha no topo;
 *   2. caixinha de alertas — pequena, no canto, número de alertas abertos
 *      piscando (com prefers-reduced-motion: borda vermelha fixa). Toque abre a
 *      lista: carro (chip na cor do sentido), texto, ação sugerida e a baixa
 *      (Alarme falso · Atitude tomada · Contatei CCO/coordenação — T4);
 *   3. números em tamanho normal — ICV (cor pela meta), perdidas, "A conferir"
 *      (toque abre a lista com o motivo e o botão de correção que já existe) e
 *      "Faltam X viagens para a meta";
 *   4. Saindo daqui — 2 últimas + 4 próximas do sentido que parte do terminal;
 *   5. Chegando aqui — carros a caminho, pela previsão, com rua e trânsito;
 *   6. bloco "Robô" (recolhido) — saúde da coleta. Mapa e Fechamento seguem
 *      nas abas de baixo (ao-vivo.abas.js).
 * Tudo por sentido (T10): sentido 1 = TP → TS azul royal, 2 = TS → TP laranja,
 * sempre com o carro de 4 dígitos.
 *
 * Rotas: GET /monitoramento/linhas/{linha}/fiscal-ao-vivo?terminal=TS (30 s)
 *        POST /monitoramento/alertas/baixa
 * Texto da API só entra em innerHTML via escapeHtml().
 */

import { apiGet, apiPost, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { abrirCorrecao, faixaRobo } from './ao-vivo.abas.js';

const SETA = { 1: 'TP → TS', 2: 'TS → TP' };
const BAIXAS = [
    ['alarme_falso', 'Alarme falso'],
    ['atitude_tomada', 'Atitude tomada'],
    ['contatou_cco', 'Contatei CCO/coordenação'],
];
const ROTULO_TRANSITO = { livre: '🟢 livre', intenso: '🟡 intenso', parado: '🔴 parado' };

let linha = '';
let terminal = '';
let dados = null;
let listaAlertasAberta = false;
let listaConferirAberta = false;

const $ = (id) => document.getElementById(id);

function pct(v) {
    return v == null ? '—' : `${v.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`;
}

function chip(carro, sentido) {
    return `<span class="av-chip av-chip-s${sentido === 2 ? 2 : 1}">${escapeHtml(carro || '—')}</span>`;
}

// ─── 1. Cabeçalho ───────────────────────────────────────────────────────

/** Cada aba com o terminal DA SUA linha (o mesmo ponto é TP de uma linha e TS de outra). */
function linkTerminal(c, l) {
    const t = c.terminais_do_ponto?.[l];
    return t ? `&terminal=${encodeURIComponent(t)}` : '';
}

function renderCabecalho() {
    const c = dados.cabecalho;
    const abas = c.linhas_do_ponto.length > 1
        ? c.linhas_do_ponto.map((l) => `<a role="tab" aria-selected="${l === c.linha}" class="fv-aba-linha${l === c.linha ? ' fv-aba-linha-ativa' : ''}"
            href="ao-vivo.html?linha=${encodeURIComponent(l)}${linkTerminal(c, l)}">${escapeHtml(l)}</a>`).join('')
        : '';
    $('fv-linhas').innerHTML = abas;
    $('fv-linhas').hidden = !abas;
    $('fv-titulo').textContent = `${c.linha} · ${c.terminal} ${c.terminal_nome !== c.terminal ? c.terminal_nome : ''}`.trim();
    const fiscal = c.fiscal ? `Fiscal ${c.fiscal.re}${c.fiscal.periodo ? ` · ${c.fiscal.periodo}º período` : ''}` : 'Coordenação';
    $('fv-fiscal').textContent = fiscal;
    const selo = $('fv-selo');
    selo.textContent = c.selo?.texto || '';
    selo.className = `fv-selo fv-selo-${c.selo?.tipo || 'nenhum'}`;
    selo.hidden = !c.selo;
    const previsto = $('fv-previsto');
    previsto.textContent = dados.faixa_previsto || '';
    previsto.hidden = !dados.faixa_previsto;
}

// ─── 2. Caixinha de alertas ─────────────────────────────────────────────

function renderCaixinha() {
    const n = dados.alertas.length;
    const algumAlerta = dados.alertas.some((a) => a.nivel === 'alerta');
    const btn = $('fv-caixinha');
    $('fv-n-alertas').textContent = n;
    btn.classList.toggle('fv-caixinha-pisca', algumAlerta);
    btn.classList.toggle('fv-caixinha-zero', n === 0);
    btn.setAttribute('aria-expanded', listaAlertasAberta ? 'true' : 'false');
    btn.setAttribute('aria-label', `${n} alerta${n === 1 ? '' : 's'} aberto${n === 1 ? '' : 's'}`);

    const el = $('fv-alertas');
    el.hidden = !listaAlertasAberta;
    if (!listaAlertasAberta) return;
    const podeBaixar = dados.cabecalho.pode_baixar;
    el.innerHTML = (n ? dados.alertas.map((a) => `<div class="av-cartao av-cartao-${a.nivel === 'alerta' ? 'alerta' : 'atencao'}">
        <span class="av-cartao-topo">${chip(a.carro, a.sentido)} <small>${escapeHtml([a.inicio, a.tabela ? `tab ${a.tabela}` : ''].filter(Boolean).join(' · '))}</small>
            ${a.reaberto ? '<small class="fv-reaberto">voltou</small>' : ''}</span>
        <span class="av-cartao-texto">${escapeHtml(a.texto)}</span>
        <span class="av-cartao-eta">👉 ${escapeHtml(a.acao_sugerida)}</span>
        ${podeBaixar ? `<span class="fv-baixas">${BAIXAS.map(([m, t]) => `<button type="button" data-baixa="${escapeHtml(m)}" data-chave="${escapeHtml(a.chave)}">${escapeHtml(t)}</button>`).join('')}</span>` : ''}
    </div>`).join('') : '<div class="av-vazio">Nenhum alerta aberto. 👏</div>')
        + (dados.alertas_com_baixa ? `<p class="av-rodape">${dados.alertas_com_baixa} com baixa (voltam a piscar em 15 min se continuarem).</p>` : '');
}

async function darBaixa(botao) {
    const corpo = { linha_codigo: dados.cabecalho.linha, chave: botao.dataset.chave, motivo: botao.dataset.baixa };
    for (const b of botao.parentElement.querySelectorAll('button')) b.disabled = true;
    try {
        await apiPost('/monitoramento/alertas/baixa', corpo);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        for (const b of botao.parentElement.querySelectorAll('button')) b.disabled = false;
        botao.parentElement.insertAdjacentHTML('afterend', `<span class="av-form-erro">${escapeHtml(err.message)}</span>`);
        return;
    }
    await atualizarFiscal();
}

// ─── 3. Números ─────────────────────────────────────────────────────────

function renderNumeros() {
    const t = dados.numeros.total;
    const sentidos = Object.entries(dados.numeros.por_sentido)
        .filter(([, b]) => b.programadas_dia)
        .map(([s, b]) => `<span class="fv-sentido"><i class="av-bolinha av-s${s}"></i>${escapeHtml(SETA[s] || s)}
            <b class="fv-farol-${escapeHtml(b.farol)}">${pct(b.icv)}</b> <small>${b.realizadas}/${b.programadas_ate_agora} · ${b.perdidas} perd.</small></span>`)
        .join('');
    $('fv-numeros').innerHTML = `
        <div class="fv-num-linha">
            <span>ICV <b class="fv-farol-${escapeHtml(t.farol)}">${pct(t.icv)}</b></span>
            <span>Perdidas <b>${t.perdidas}</b></span>
            <span>IPP <b>${pct(t.ipp)}</b></span>
            <button type="button" class="fv-conferir-btn" id="fv-conferir-btn" aria-expanded="${listaConferirAberta}"
                ${t.a_conferir ? '' : 'disabled'}>A conferir: <b>${t.a_conferir}</b></button>
        </div>
        <div class="fv-num-sentidos">${sentidos}</div>
        ${t.faltam_para_meta != null ? `<div class="fv-meta">${t.faltam_para_meta
            ? `Faltam <b>${t.faltam_para_meta}</b> viagens para a meta (${pct(dados.numeros.meta)})`
            : `Meta do dia garantida (${pct(dados.numeros.meta)})`}</div>` : ''}`;
    renderConferir();
}

function renderConferir() {
    const el = $('fv-conferir');
    el.hidden = !listaConferirAberta;
    if (!listaConferirAberta) return;
    const podeCorrigir = dados.cabecalho.pode_corrigir;
    el.innerHTML = dados.a_conferir.length ? dados.a_conferir.map((p) => `<div class="av-cartao av-cartao-atencao">
        <span class="av-cartao-topo">${chip(p.carro_real || p.carro_programado, p.sentido)} <b>${escapeHtml(p.programado || '—')}</b>
            <small>tab ${escapeHtml(p.tabela || '—')}</small></span>
        <span class="av-cartao-texto">${escapeHtml(p.a_conferir)}</span>
        ${podeCorrigir ? `<button type="button" class="av-btn-corrigir" data-corrigir-id="${p.id}">Corrigir</button>` : ''}
    </div>`).join('') : '<div class="av-vazio">Nada a conferir.</div>';
}

// ─── 4 e 5. Saindo daqui · Chegando aqui ────────────────────────────────

function renderSaindo() {
    const { sentido, partidas } = dados.saindo;
    $('fv-saindo-titulo').innerHTML = `<i class="av-bolinha av-s${sentido === 2 ? 2 : 1}"></i> Saindo daqui · ${escapeHtml(SETA[sentido] || '')}`;
    $('fv-saindo').innerHTML = partidas.length ? partidas.map((p) => `<div class="fv-partida${p.passada ? ' fv-passada' : ''}">
        <span class="fv-hora">${escapeHtml(p.programado)}</span>
        ${chip(p.carro, p.sentido)}${p.trocado ? ` <small title="programado no NIMER">(${escapeHtml(p.carro_programado)})</small>` : ''}
        <span class="fv-sit fv-cor-${escapeHtml(p.cor)}">${escapeHtml(p.texto)}</span>
    </div>`).join('') : '<div class="av-vazio">Sem partidas do NIMER neste sentido.</div>';
}

function renderChegando() {
    const { sentido, carros } = dados.chegando;
    $('fv-chegando-titulo').innerHTML = `<i class="av-bolinha av-s${sentido === 2 ? 2 : 1}"></i> Chegando aqui · ${escapeHtml(SETA[sentido] || '')}`;
    $('fv-chegando').innerHTML = carros.length ? carros.map((c) => `<div class="fv-partida">
        ${chip(c.carro, c.sentido)}
        <span class="fv-rua">${escapeHtml(c.rua || '—')}</span>
        <span class="fv-eta">${c.minutos != null ? `~${c.minutos} min` : 'sem previsão'}</span>
        ${c.transito ? `<span class="fv-transito fv-transito-${escapeHtml(c.transito)}">${escapeHtml(ROTULO_TRANSITO[c.transito])}</span>` : ''}
    </div>`).join('') : `<div class="av-vazio">${dados.cabecalho.tem_mapa ? 'Nenhum carro a caminho agora.' : 'Linha sem traçado: sem rua, previsão e trânsito.'}</div>`;
}

// ─── 6. Robô ────────────────────────────────────────────────────────────

function renderRobo() {
    const r = dados.robo;
    $('fv-robo-corpo').innerHTML = [
        faixaRobo('SIM', r.sim_ultima_ok_em, null, undefined, 'sim'),
        dados.cabecalho.tem_nimer ? faixaRobo('NIMER', r.nimer_ultima_ok_em, r.nimer_status, undefined, 'nimer')
            : '<span class="av-robo av-status-aviso">NIMER — linha sem LineId cadastrado</span>',
        `<span class="av-robo">Rastro: ${r.rastro_ultimo_em ? new Date(r.rastro_ultimo_em).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo' }) : 'nenhum hoje'}</span>`,
        `<span class="av-robo">${r.carros_em_duvida} carro${r.carros_em_duvida === 1 ? '' : 's'} em dúvida</span>`,
    ].join('');
}

// ─── Ciclo ──────────────────────────────────────────────────────────────

export async function atualizarFiscal() {
    const q = terminal ? `?terminal=${encodeURIComponent(terminal)}` : '';
    try {
        dados = await apiGet(`/monitoramento/linhas/${encodeURIComponent(linha)}/fiscal-ao-vivo${q}`);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        $('fv-numeros').innerHTML = `<div class="av-vazio">${escapeHtml(err.message)}</div>`;
        $('fv').hidden = false;
        return;
    }
    terminal = dados.cabecalho.terminal;
    renderCabecalho();
    renderCaixinha();
    renderNumeros();
    renderSaindo();
    renderChegando();
    renderRobo();
    $('fv').hidden = false;
}

export function iniciarFiscal(opcoes) {
    linha = opcoes.linha;
    terminal = opcoes.terminal || '';
    $('fv-caixinha').addEventListener('click', () => {
        listaAlertasAberta = !listaAlertasAberta;
        if (dados) renderCaixinha();
    });
    $('fv-alertas').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-baixa]');
        if (b) darBaixa(b);
    });
    $('fv-numeros').addEventListener('click', (ev) => {
        if (!ev.target.closest('#fv-conferir-btn')) return;
        listaConferirAberta = !listaConferirAberta;
        if (dados) renderNumeros();
    });
    $('fv-conferir').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-corrigir-id]');
        if (b) abrirCorrecao(b.dataset.corrigirId);
    });
}
