/*
 * ao-vivo.page.js — Ao vivo da linha (migration 053)
 * -------------------------------------------------------------------------------
 * O robô da coordenação (extensão no SIM SPTrans) manda as posições ao
 * backend a cada minuto; esta tela mostra a linha para o fiscal em campo e
 * para a coordenação, no celular. Abre com ?linha=1726-10.
 *
 * Fase 2: as abas Partidas · Alertas · Fechamento, o placar e a saúde dos
 * robôs (SIM e NIMER) ficam em ao-vivo.abas.js. Quem vê qual linha é regra
 * do backend (fiscal: só as do turno aberto).
 *
 * Rotas: GET /monitoramento/linhas/{linha}/tracado (uma vez),
 * /monitoramento/linhas/{linha}/ao-vivo (a cada 30 s, pausado com a aba
 * escondida) e /monitoramento/linhas/{linha}/carros/{prefixo}/rastro
 * (só do carro selecionado).
 *
 * Chips do mapa (decisão do Alisson): cor = SENTIDO, sempre com o número do
 * carro de 4 dígitos — sentido 1 (TP → TS) azul royal #1F4FD8, sentido 2
 * (TS → TP) laranja #EF6C00. Alerta acende contorno vermelho e "⚠ "; atenção,
 * contorno amarelo; sumiu, 45% de opacidade.
 *
 * Texto da API só entra em innerHTML via escapeHtml().
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, ApiError } from './api.js';
import { podeLer } from './sessao.js';
import { escapeHtml } from './escape.js';
import { atualizarTempoReal, iniciarAbas } from './ao-vivo.abas.js';

if (!requireAuth()) throw new Error('Não autenticado');

if (!podeLer('fiscalizacao') && !podeLer('fiscalizacao_painel')) {
    window.location.replace('modulos.html');
    throw new Error('Sem acesso a fiscalizacao/fiscalizacao_painel');
}

const L = window.L;
const FUSO = 'America/Sao_Paulo';
const INTERVALO_MS = 30000;
const RASTRO_MIN = 90;
const AVISO_COLETA_MIN = 3;   // amarelo: última coleta OK com mais de 3 min
const ERRO_COLETA_MIN = 10;   // vermelho: mais de 10 min sem coleta OK
const COR_SENTIDO = { 1: '#1F4FD8', 2: '#EF6C00' };

const parametros = new URLSearchParams(location.search);
// Sem ?linha (o fiscal chega aqui direto do login): a 1ª linha que o
// backend deixa ver — a do turno, aberto sozinho pela foto do NIMER.
let linhaCodigo = (parametros.get('linha') || '').trim();

let mapa = null;
let tracado = null;
let dados = null;
let selecionado = null;      // prefixo (5 dígitos, como vem do SIM)
let camadaRastro = null;
let timer = null;
const marcadores = new Map();

// ─── Helpers ────────────────────────────────────────────────────────────

function hora(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: FUSO });
}

function minutosDesde(iso) {
    return iso ? (Date.now() - new Date(iso).getTime()) / 60000 : Infinity;
}

function ha(iso) {
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    return s < 90 ? `${s} s` : `${Math.round(s / 60)} min`;
}

let avisoFixo = ''; // ex.: linha sem traçado — continua visível quando o erro some

function mostrarErro(msg) {
    const el = document.getElementById('av-erro');
    el.textContent = msg || avisoFixo;
    el.hidden = !(msg || avisoFixo);
}

function nomeTerminal(codigo) {
    return tracado?.terminais.find((t) => t.codigo === codigo)?.nome || codigo;
}

function nomeDestino(sentido) {
    return tracado?.sentidos.find((s) => s.sentido === sentido)?.destino_nome || `sentido ${sentido}`;
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

// ─── Mapa ───────────────────────────────────────────────────────────────

function criarMapa() {
    mapa = L.map('av-mapa', { zoomControl: true });
    const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '© OpenStreetMap',
    });
    const esri = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19, attribution: 'Tiles © Esri',
    });
    // Fundo OpenStreetMap; se os tiles falharem, troca para a Esri (uma vez).
    let falhas = 0;
    osm.on('tileerror', () => {
        falhas += 1;
        if (falhas === 3 && mapa.hasLayer(osm)) {
            mapa.removeLayer(osm);
            esri.addTo(mapa);
        }
    });
    osm.addTo(mapa);
    L.control.layers({ 'OpenStreetMap': osm, 'Esri (ruas)': esri }, null, { position: 'topright' }).addTo(mapa);
}

function desenharTracado() {
    const rotas = [];
    for (const s of tracado.sentidos) {
        rotas.push(L.polyline(s.pontos, { color: COR_SENTIDO[s.sentido] || '#888', weight: 4, opacity: 0.4 })
            .addTo(mapa)
            .bindTooltip(`Sentido ${s.sentido} → ${s.destino_nome}`));
    }
    for (const t of tracado.terminais) {
        L.circle(t.centros[0], { radius: t.raio_m, color: '#a855f7', weight: 2, fillOpacity: 0.12 })
            .addTo(mapa).bindTooltip(t.nome);
    }
    const g = tracado.garagem;
    L.circle(g.centro, { radius: g.raio_m, color: '#9ca3af', weight: 2, fillOpacity: 0.12 })
        .addTo(mapa).bindTooltip(g.nome);
    if (rotas.length) mapa.fitBounds(L.featureGroup(rotas).getBounds(), { padding: [16, 16] });
    else mapa.setView(g.centro, 13);
}

function iconeCarro(c) {
    const extra = ['alerta', 'atencao', 'sumiu'].includes(c.classe) ? ` av-chip-${c.classe}` : '';
    const sel = c.prefixo === selecionado ? ' av-chip-sel' : '';
    const texto = `${c.classe === 'alerta' ? '⚠ ' : ''}${escapeHtml(c.prefixo_patio)}`;
    return L.divIcon({
        className: '',
        iconSize: null,
        iconAnchor: [22, 12],
        html: `<span class="av-chip av-chip-s${c.sentido === 2 ? 2 : 1}${extra}${sel}">${texto}</span>`,
    });
}

function atualizarMarcadores() {
    const vistos = new Set();
    for (const c of dados.carros) {
        vistos.add(c.prefixo);
        const pos = [c.lat, c.lon];
        let m = marcadores.get(c.prefixo);
        if (!m) {
            m = L.marker(pos, { icon: iconeCarro(c), keyboard: false })
                .addTo(mapa)
                .on('click', () => selecionar(c.prefixo, false));
            marcadores.set(c.prefixo, m);
        } else {
            m.setLatLng(pos).setIcon(iconeCarro(c));
        }
        m.setZIndexOffset(c.classe === 'alerta' ? 1000 : c.prefixo === selecionado ? 900 : 0);
        m.unbindTooltip().bindTooltip(`${c.prefixo_patio} · ${descreverPosicao(c)}`);
    }
    for (const [p, m] of marcadores) {
        if (!vistos.has(p)) {
            mapa.removeLayer(m);
            marcadores.delete(p);
        }
    }
}

async function desenharRastro() {
    // O rastro antigo só sai quando o novo chega (sem piscar a cada 30 s);
    // se o carro foi desmarcado ou trocado, sai na hora.
    if (camadaRastro && camadaRastro._prefixo !== selecionado) {
        mapa.removeLayer(camadaRastro);
        camadaRastro = null;
    }
    if (!selecionado) return;
    const prefixo = selecionado;
    let r;
    try {
        r = await apiGet(`/monitoramento/linhas/${encodeURIComponent(linhaCodigo)}/carros/${encodeURIComponent(prefixo)}/rastro?minutos=${RASTRO_MIN}`);
    } catch (err) {
        if (!(err instanceof ApiError && err.status === 401)) mostrarErro('Não foi possível carregar o rastro: ' + err.message);
        return;
    }
    if (selecionado !== prefixo) return;  // trocou de carro enquanto carregava
    if (camadaRastro) mapa.removeLayer(camadaRastro);
    camadaRastro = L.layerGroup();
    camadaRastro._prefixo = prefixo;
    const pts = r.pontos;
    for (let i = 1; i < pts.length; i++) {
        const fora = pts[i].zona === 'fora';
        L.polyline([[pts[i - 1].lat, pts[i - 1].lon], [pts[i].lat, pts[i].lon]], {
            color: fora ? '#ef4444' : '#10b981', weight: 4, dashArray: '6 5', opacity: 0.9,
        }).addTo(camadaRastro);
    }
    camadaRastro.addTo(mapa);
}

function selecionar(prefixo, centralizar = true) {
    selecionado = selecionado === prefixo ? null : prefixo;
    if (selecionado && centralizar) {
        const m = marcadores.get(selecionado);
        if (m) mapa.setView(m.getLatLng(), 16);
        document.getElementById('av-mapa').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (dados) {
        atualizarMarcadores();
        renderLista();
    }
    desenharRastro();
}

// ─── Status (faixa do topo) ─────────────────────────────────────────────

function renderStatus() {
    const el = document.getElementById('av-status');
    const c = dados.coleta;
    const minOk = minutosDesde(c.ultima_ok_em);
    let classe;
    let texto;
    if (c.status !== 'ok' || minOk > ERRO_COLETA_MIN) {
        classe = 'av-status-erro';
        texto = `Robô sem dados desde ${hora(c.ultima_ok_em)} — avise a coordenação`;
        if (c.status === 'sessao') texto += ' (login do SIM caiu)';
    } else if (minOk > AVISO_COLETA_MIN) {
        classe = 'av-status-aviso';
        texto = `SIM sem atualizar desde ${hora(c.ultima_ok_em)}`;
    } else {
        classe = 'av-status-ok';
        texto = `● Ao vivo — SIM às ${hora(c.ultima_ok_em)}`;
    }
    el.className = `av-status ${classe}`;
    el.textContent = texto;
}

// ─── Lista ──────────────────────────────────────────────────────────────

function descreverPosicao(c) {
    let onde;
    if (c.zona === 'garagem') onde = 'Na G3';
    else if (c.zona === 'TP' || c.zona === 'TS') onde = nomeTerminal(c.zona);
    else onde = c.local || (c.zona === 'fora' && c.distancia_rota_m != null ? `${c.distancia_rota_m} m fora da rota` : 'Na rota');
    const rumo = c.zona === 'garagem' ? '' : ` · → ${c.chegou ? 'chegou' : (c.destino || '—')}`;
    const vel = c.velocidade != null ? ` · ${c.velocidade} km/h` : '';
    return `${onde}${rumo}${vel} · há ${ha(c.posicao_em)}`;
}

function cartaoCarro(c) {
    const sel = c.prefixo === selecionado ? ' av-cartao-sel' : '';
    return `<button type="button" class="av-cartao av-cartao-${c.classe}${sel}" data-p="${escapeHtml(c.prefixo)}">
        <span class="av-cartao-topo"><i class="av-bolinha av-s${c.sentido === 2 ? 2 : 1}"></i><b>${escapeHtml(c.prefixo_patio)}</b> <small>${escapeHtml(c.prefixo)}</small></span>
        <span class="av-cartao-texto">${escapeHtml(descreverPosicao(c))}</span>
        ${c.zona === 'rota' && c.eta_texto ? `<span class="av-cartao-eta">🕒 ${escapeHtml(c.eta_texto)}</span>` : ''}
    </button>`;
}

function renderLista() {
    const { carros, alertas_agora: alertas, ocorrencias_dia: ocorrencias, contadores } = dados;

    document.getElementById('av-contadores').innerHTML = [
        `🚌 Rodando ${contadores.rodando}`,
        `🅿 Terminal ${contadores.terminal}`,
        `🏠 G3 ${contadores.garagem}`,
        contadores.sumiram ? `👻 Sumiram ${contadores.sumiram}` : '',
    ].filter(Boolean).map((t) => `<span>${t}</span>`).join('');

    const nA = document.getElementById('av-n-alertas');
    nA.textContent = alertas.length;
    nA.className = 'av-pilula' + (alertas.length ? '' : ' av-pilula-zero');
    document.getElementById('av-alertas').innerHTML = alertas.length
        ? alertas.map((a) => `<button type="button" class="av-cartao av-cartao-${a.nivel}" data-p="${escapeHtml(a.prefixo)}">
            <span class="av-cartao-topo"><b>${a.nivel === 'alerta' ? '⚠ ' : ''}${escapeHtml(a.prefixo_patio)}</b> <small>${escapeHtml(a.prefixo)}</small></span>
            <span class="av-cartao-texto">${escapeHtml(a.texto)}</span>
        </button>`).join('')
        : '<div class="av-vazio">Nenhuma anormalidade agora.</div>';

    const grupos = [
        [`TP → TS · ${nomeDestino(1)}`, (c) => !c.sumiu && c.zona !== 'garagem' && c.sentido === 1],
        [`TS → TP · ${nomeDestino(2)}`, (c) => !c.sumiu && c.zona !== 'garagem' && c.sentido === 2],
        ['Na Garagem 3', (c) => !c.sumiu && c.zona === 'garagem'],
        ['Sumiram da consulta do SIM (trocou de linha, desligou ou recolheu)', (c) => c.sumiu],
    ];
    let html = '';
    for (const [titulo, filtro] of grupos) {
        const lista = carros.filter(filtro);
        if (!lista.length) continue;
        html += `<div class="av-grupo">${escapeHtml(titulo)} (${lista.length})</div>` + lista.map(cartaoCarro).join('');
    }
    document.getElementById('av-carros').innerHTML = html
        || '<div class="av-vazio">Nenhum carro desta linha nas últimas 4 h.</div>';

    document.getElementById('av-ocorrencias').innerHTML = ocorrencias.length
        ? ocorrencias.map((o) => `<button type="button" class="av-log${o.nivel === 'alerta' ? '' : ' av-log-info'}" data-p="${escapeHtml(o.prefixo)}">
            <span class="av-log-hora">${hora(o.inicio_em)}</span> · <b>${escapeHtml(o.prefixo_patio)}</b> — ${escapeHtml(o.texto)}
        </button>`).join('')
        : `<div class="av-vazio">Sem ocorrências hoje (histórico das últimas ${dados.janela_horas} h).</div>`;
}

// ─── Ciclo ──────────────────────────────────────────────────────────────

async function atualizar() {
    atualizarTempoReal();   // fase 2: partidas, ICV e alertas (em paralelo com o mapa)
    try {
        dados = await apiGet(`/monitoramento/linhas/${encodeURIComponent(linhaCodigo)}/ao-vivo`);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;  // api.js já levou ao login
        const el = document.getElementById('av-status');
        el.className = 'av-status av-status-aviso';
        el.textContent = err.status === 0
            ? 'Sem conexão com o sistema — tentando de novo em 30 s'
            : `Erro ao atualizar: ${err.message}`;
        return;
    }
    if (selecionado && !dados.carros.some((c) => c.prefixo === selecionado)) {
        selecionado = null;
    }
    mostrarErro('');
    renderStatus();
    renderLista();
    atualizarMarcadores();
    desenharRastro();  // atualiza o do selecionado, ou tira o de quem saiu da tela
}

function iniciarPolling() {
    pararPolling();
    timer = setInterval(atualizar, INTERVALO_MS);
}

function pararPolling() {
    if (timer) clearInterval(timer);
    timer = null;
}

document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        pararPolling();
    } else {
        atualizar();
        iniciarPolling();
    }
});

document.querySelector('.av-painel').addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-p]');
    if (el) selecionar(el.dataset.p);
});

async function iniciar() {
    initHeader();
    if (!linhaCodigo) {
        try {
            const linhas = await apiGet('/monitoramento/linhas');
            linhaCodigo = linhas[0]?.codigo || '';
        } catch (err) {
            if (err instanceof ApiError && err.status === 401) return;
        }
    }
    if (!linhaCodigo) {
        // Sem turno (nem pelo NIMER): o fluxo manual de "Abrir turno" continua valendo.
        document.getElementById('av-status').innerHTML =
            'Nenhuma linha ao vivo para você agora. <a href="fiscal.html">Abrir turno</a>';
        return;
    }
    criarMapa();
    try {
        tracado = await apiGet(`/monitoramento/linhas/${encodeURIComponent(linhaCodigo)}/tracado`);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        const el = document.getElementById('av-status');
        el.className = 'av-status av-status-erro';
        el.textContent = err.message;
        return;
    }
    document.title = `Ao vivo ${tracado.codigo} — Sambaíba V3`;
    document.getElementById('av-linha-nome').textContent = tracado.nome;
    const s1 = tracado.sentidos.find((s) => s.sentido === 1);
    const s2 = tracado.sentidos.find((s) => s.sentido === 2);
    if (s1) document.getElementById('av-leg-s1').textContent = `TP → TS (${s1.destino_nome})`;
    if (s2) document.getElementById('av-leg-s2').textContent = `TS → TP (${s2.destino_nome})`;
    desenharTracado();
    iniciarAbas({ linha: linhaCodigo, aoMudarAba: (aba) => { if (aba === 'mapa') mapa.invalidateSize(); } });
    if (!tracado.tem_mapa) {
        // Linha monitorada sem traçado (bloco C): só o que não depende do mapa.
        avisoFixo = 'Esta linha ainda não tem traçado: desvio, virada e parado fora do terminal estão desligados. Posição, sem transmissão e partidas funcionam.';
        mostrarErro('');
    }
    await atualizar();
    iniciarPolling();
}

iniciar();
