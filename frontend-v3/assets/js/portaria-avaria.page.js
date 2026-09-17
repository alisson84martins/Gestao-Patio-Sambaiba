/*
 * portaria-avaria.page.js — avaria na saída da frota (Bloco G, migration 042)
 * -----------------------------------------------------------
 * 16/09/2026 — correção depois do teste em tela com o Alisson: a versão
 * anterior (mapa clicável, avaria.mapa.js) foi vetada — rótulos cortados,
 * 59 botões, página de histórico separada. Esta versão volta ao layout
 * simples, no mesmo padrão de portaria-recolhida.page.js (chips em dois
 * níveis, como o bloco "Defeito"), com um nível a mais só porque a avaria
 * tem local (zona) E tipo de dano — LOCAL (1º toque, agrupado por `vista`)
 * → ZONA (2º toque, nome inteiro) → TIPO (3º toque). Ver
 * _handoff-claude/PROMPT-CORRIGIR-tela-avaria-2026-09-16.md.
 *
 * 17/09/2026 — decisão do Alisson: PIOROU e "não vi mais essa" saíram da
 * caixa "já marcada". O controlador não decide se o carro foi consertado
 * (quem dá baixa é a funilaria, módulo Manutenção); e se o dano sumiu,
 * ninguém vai ao portão pedir pra marcar — a contestação pelo controlador
 * não tinha gatilho real. /contestar e o `piorou` do POST /avarias
 * continuam existindo no backend, só sem botão nesta tela.
 *
 * 🔴 Regra número um: POST /portaria/avarias nunca recusa — RE que não
 * resolve não bloqueia, prefixo não cadastrado não bloqueia, linha fora do
 * catálogo não bloqueia. O que esta TELA barra (decisão de operação, não do
 * backend): RE vazio (o motorista está na frente) e avaria não escolhida.
 *
 * ⛔ av-motorista-re / -status / -nome não mudam — cópia deliberada de
 * portaria-recolhida.html (ver histórico do módulo).
 *
 * 🟢 O aviso de "já marcada" (§3 do prompt) decide só com o que já veio do
 * GET /avarias/mapa no blur do prefixo — ⛔ nenhuma chamada nova nesse
 * caminho. Por isso o aviso não mostra "marcado por Fulano": esse dado só
 * existe em /avarias/historico e /avarias/{id}, e buscar isso aqui violaria
 * o "não precisa de chamada nova" do prompt. Fica só no HISTÓRICO
 * compartilhado (aba), que é onde o Alisson pediu esse dado.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { buscarPorRe } from './identidade.js';
import { dataLocalISO, dataServicoISO } from './data.util.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

// 17/09/2026 — decisão do Alisson: a tela só tem o que o CONTROLADOR DE
// ACESSO enxerga do chão, com o motorista na frente. INTERNO (banco, piso,
// catraca…) e TETO saíram da tela — interior e teto não são marcados na
// portaria (item 3a). Vistas reais que sobram, nesta ordem:
const VISTA_LABELS = {
    FRENTE: 'Frente',
    TRASEIRA: 'Traseira',
    LATERAL_ESQ: 'Lateral esq',
    LATERAL_DIR: 'Lateral dir',
};
const VISTA_ORDEM = ['FRENTE', 'TRASEIRA', 'LATERAL_ESQ', 'LATERAL_DIR'];

// A zona-escape "Outro (descreva)" (codigo=OUTRO) é a válvula da regra
// número um — sem ela, dano sem lugar no mapa (extintor, cinto, cheiro de
// queimado) travaria o registro. Ela mora com vista=INTERNO no catálogo
// (migration 042), mas INTERNO saiu da tela (item 3a) — por isso vira chip
// PRÓPRIO no 1º nível (item 3b), fora do agrupamento por vista, pulando
// direto pro TIPO (não tem 2º nível: é uma zona só).
const ZONA_ESCAPE_CODIGO = 'OUTRO';

let catalogo = { zonas: [], tipos: [] };
let linhasCache = [];
let linhaSelecionada = null;      // { codigo, nome }
let linhaEscolhidaManualmente = false;
let avariasAbertas = [];          // GET /avarias/mapa — só ABERTA, deste carro
let vistaAtual = null;
let zonaAtual = null;
let tipoAtual = null;
let histPeriodo = 'hoje';

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

// ─── Formatação de data (só exibição) ──────────────────────────────────
function _dataBr(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleDateString('pt-BR');
    } catch {
        return iso;
    }
}

function _dataHoraBr(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch {
        return iso;
    }
}

// ─── Abas MARCAR / HISTÓRICO — mesmo padrão .oc-tabs/.oc-tab-section de ──
// manutencao.html e ocorrencia-form.html.
function initTabs() {
    document.querySelectorAll('#av-tabs .filtro-btn[data-tab]').forEach((btn) => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#av-tabs .filtro-btn[data-tab]').forEach((b) => b.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('.oc-tab-section').forEach((s) => s.classList.remove('active'));
            document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
            if (btn.dataset.tab === 'historico') carregarHistorico();
        });
    });
}

// ─── Linha (pedido do Alisson, 16/09) — cópia do padrão da recolhida,     ──
// opcional, nunca barra. Porta própria (/portaria/catalogo/linhas) porque
// CONTROLADOR_ACESSO não tem o recurso `fiscalizacao` (já filtrada por
// ativa=true no servidor, ⛔ não filtrar de novo aqui).
function initLinha() {
    const input = document.getElementById('av-linha-busca');
    let handle = null;
    input.addEventListener('input', () => {
        linhaSelecionada = null;
        atualizarLinhaSelecionada();
        clearTimeout(handle);
        handle = setTimeout(() => renderSugestoesLinha(input.value.trim()), 150);
    });
    input.addEventListener('focus', () => renderSugestoesLinha(input.value.trim()));
    input.addEventListener('blur', () => {
        setTimeout(() => { document.getElementById('av-linha-sugestoes').innerHTML = ''; }, 150);
    });
}

function renderSugestoesLinha(termo) {
    const el = document.getElementById('av-linha-sugestoes');
    const filtradas = termo
        ? linhasCache.filter((l) => `${l.codigo} ${l.nome}`.toLowerCase().includes(termo.toLowerCase()))
        : linhasCache;
    el.innerHTML = '';
    for (const linha of filtradas.slice(0, 12)) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = linha.codigo;
        btn.title = linha.nome;
        btn.addEventListener('click', () => {
            linhaSelecionada = linha;
            linhaEscolhidaManualmente = true;
            document.getElementById('av-linha-busca').value = linha.codigo;
            atualizarLinhaSelecionada();
            el.innerHTML = '';
        });
        el.appendChild(btn);
    }
}

function atualizarLinhaSelecionada() {
    document.getElementById('av-linha-selecionada').textContent =
        linhaSelecionada ? linhaSelecionada.nome : '';
}

async function aplicarSugestaoLinha(codigo, nome) {
    // 🟢 Autopreenchimento pela escala do dia — ⛔ só preenche campo vazio,
    // ⛔ nunca sobrescreve o que o controlador já escolheu.
    if (linhaEscolhidaManualmente || !codigo) return;
    if (linhaSelecionada && linhaSelecionada.codigo === codigo) return;
    linhaSelecionada = { codigo, nome };
    document.getElementById('av-linha-busca').value = codigo;
    document.getElementById('av-linha-selecionada').textContent =
        `Sugerida pela escala: ${codigo}${nome ? ' — ' + nome : ''} (editável)`;
}

// ─── RE motorista — confirmação visual (§5.3), nunca bloqueia ──────────
// 17/09 (tarde), item [A] — o campo "Nome (RE não encontrado)" saiu da
// tela: o controlador no portão não digita nome de motorista, só o RE.
// `motorista_nome` já era Optional no backend (AvariaSaidaCreate) — nunca
// foi exigido lá; era só esta tela que abria o campo.
function initIdentificacao() {
    document.getElementById('av-motorista-re').addEventListener('blur', resolverRe);
}

async function resolverRe() {
    const campoRe = document.getElementById('av-motorista-re');
    const status = document.getElementById('av-motorista-status');
    const re = campoRe.value.trim();
    status.textContent = '';
    if (re.length < 3) {
        return;
    }
    try {
        const resp = await buscarPorRe(re);
        if (resp.encontrado) {
            if (resp.ativo === false) {
                status.textContent = `${resp.nome} — desligado/inativo. Registra assim mesmo.`;
                status.style.color = '#f59e0b';
            } else {
                status.textContent = resp.nome;
                status.style.color = 'var(--accent3)';
            }
        } else {
            status.textContent = 'Não encontrado — registra assim mesmo, só com o RE.';
            status.style.color = 'var(--muted)';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-avaria] erro ao resolver RE:', err);
    }
}

// ─── Carro/prefixo — carrega as avarias ABERTAS pra comparar depois ────
async function carregarMapaCarro() {
    const prefixo = document.getElementById('av-prefixo').value.trim();
    const status = document.getElementById('av-prefixo-status');
    status.textContent = '';
    if (!prefixo) {
        avariasAbertas = [];
        limparSelecaoAvaria();
        return;
    }
    try {
        const resp = await apiGet(`/portaria/avarias/mapa?prefixo=${encodeURIComponent(prefixo)}`);
        avariasAbertas = resp.avarias;
        status.textContent = avariasAbertas.length > 0
            ? `${avariasAbertas.length} avaria(s) aberta(s) neste carro.`
            : '';
        if (resp.linha_sugerida_codigo) {
            await aplicarSugestaoLinha(resp.linha_sugerida_codigo, resp.linha_sugerida_nome);
        }
        if (zonaAtual && tipoAtual) avaliarEstado();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        avariasAbertas = [];
    }
}

// ─── Avaria — LOCAL (vista) → ZONA (nome inteiro) → TIPO, 3 toques ─────
function _zonasCatalogo() {
    // Fase 4 (migration 044) não existe nesta tela — filtra fora as zonas
    // que só aparecem pra um carro com característica (articulado/elétrico).
    return catalogo.zonas.filter((z) => !z.requer_caracteristica);
}

// 17/09 (tarde), item B3 — zona de pneu só aceita 3 tipos (Furado, Cortado,
// Roda amassada); as outras zonas continuam com os 9. Não existe mecanismo
// de restrição no banco (avaria_zona/avaria_tipo são catálogos
// independentes, conferido antes de escrever a migration 044) — filtragem
// no cliente, mesmo lugar que já filtra requer_caracteristica acima.
// `regiao_ocorrencia = 'RODADO'` já é a coluna que identifica "isto é
// zona de roda/pneu" (migration 042) — reaproveitada em vez de uma lista
// solta de códigos duplicada aqui.
const TIPOS_PNEU_CODIGOS = ['FURADO', 'CORTADO', 'RODA_AMASSADA'];

function _tiposPermitidos() {
    const zona = catalogo.zonas.find((z) => z.codigo === zonaAtual);
    if (zona && zona.regiao_ocorrencia === 'RODADO') {
        return catalogo.tipos.filter((t) => TIPOS_PNEU_CODIGOS.includes(t.codigo));
    }
    return catalogo.tipos;
}

function renderLocais() {
    const el = document.getElementById('av-locais');
    el.innerHTML = '';
    const zonas = _zonasCatalogo();
    const vistasPresentes = VISTA_ORDEM.filter((v) => zonas.some((z) => z.vista === v));
    for (const vista of vistasPresentes) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = VISTA_LABELS[vista] || vista;
        btn.addEventListener('click', () => selecionarVista(vista, btn));
        el.appendChild(btn);
    }
    if (zonas.some((z) => z.codigo === ZONA_ESCAPE_CODIGO)) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = 'Outro';
        btn.addEventListener('click', () => selecionarLocalOutro(btn));
        el.appendChild(btn);
    }
}

function selecionarVista(vista, btnAtivo) {
    vistaAtual = vista;
    document.querySelectorAll('#av-locais .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');

    zonaAtual = null;
    tipoAtual = null;
    document.getElementById('av-tipos').innerHTML = '';
    atualizarSelecionadoTexto();
    mostrarRegistrarNormal();

    renderZonas();
}

// ─── Item 3b — "Outro" é chip próprio no 1º nível, sem 2º nível (a zona já
// é uma só: ZONA_ESCAPE_CODIGO). Pula direto pro TIPO.
function selecionarLocalOutro(btnAtivo) {
    vistaAtual = null;
    document.querySelectorAll('#av-locais .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');
    document.getElementById('av-zonas').innerHTML = '';

    zonaAtual = ZONA_ESCAPE_CODIGO;
    tipoAtual = null;
    atualizarSelecionadoTexto();
    mostrarRegistrarNormal();
    renderTipos();
}

function renderZonas() {
    const el = document.getElementById('av-zonas');
    el.innerHTML = '';
    const zonas = _zonasCatalogo()
        .filter((z) => z.vista === vistaAtual)
        .sort((a, b) => a.ordem - b.ordem);
    for (const zona of zonas) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = zona.nome;
        btn.addEventListener('click', () => selecionarZona(zona.codigo, btn));
        el.appendChild(btn);
    }
}

function selecionarZona(codigo, btnAtivo) {
    zonaAtual = codigo;
    tipoAtual = null;
    document.querySelectorAll('#av-zonas .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');
    atualizarSelecionadoTexto();
    mostrarRegistrarNormal();
    renderTipos();
}

function renderTipos() {
    const el = document.getElementById('av-tipos');
    el.innerHTML = '';
    for (const tipo of _tiposPermitidos()) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = tipo.nome;
        btn.addEventListener('click', () => selecionarTipo(tipo.codigo, btn));
        el.appendChild(btn);
    }
}

function selecionarTipo(codigo, btnAtivo) {
    tipoAtual = codigo;
    document.querySelectorAll('#av-tipos .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');
    atualizarSelecionadoTexto();
    avaliarEstado();
}

function atualizarSelecionadoTexto() {
    const el = document.getElementById('av-selecionado');
    if (!zonaAtual) {
        el.textContent = '';
        return;
    }
    const zona = catalogo.zonas.find((z) => z.codigo === zonaAtual);
    let texto = zona ? zona.nome : zonaAtual;
    if (tipoAtual) {
        const tipo = catalogo.tipos.find((t) => t.codigo === tipoAtual);
        texto += ' — ' + (tipo ? tipo.nome : tipoAtual);
    }
    el.textContent = texto;
}

function limparSelecaoAvaria() {
    vistaAtual = null;
    zonaAtual = null;
    tipoAtual = null;
    document.querySelectorAll('#av-locais .recolhida-chip').forEach((b) => b.classList.remove('active'));
    document.getElementById('av-zonas').innerHTML = '';
    document.getElementById('av-tipos').innerHTML = '';
    atualizarSelecionadoTexto();
    mostrarRegistrarNormal();
}

// ─── §3 do prompt — o coração da frente: já existe ABERTA nessa zona+tipo? ──
// Decide só com avariasAbertas (já veio do GET /avarias/mapa no blur do
// prefixo) — ⛔ nenhuma chamada nova aqui.
function avaliarEstado() {
    if (!zonaAtual || !tipoAtual) {
        mostrarRegistrarNormal();
        return;
    }
    const existente = avariasAbertas.find((a) => a.zona_codigo === zonaAtual && a.tipo_codigo === tipoAtual);
    if (existente) mostrarJaMarcada(existente);
    else mostrarRegistrarNormal();
}

function mostrarJaMarcada(avaria) {
    const box = document.getElementById('av-ja-marcada');
    const zona = catalogo.zonas.find((z) => z.codigo === avaria.zona_codigo);
    const tipo = catalogo.tipos.find((t) => t.codigo === avaria.tipo_codigo);
    box.style.display = 'block';
    box.innerHTML = '';

    const aviso = document.createElement('div');
    aviso.className = 'avaria-mapa-painel-aviso';
    aviso.innerHTML = `⚠️ <strong>ESSA AVARIA JÁ ESTÁ MARCADA</strong><br>` +
        `${escapeHtml(zona ? zona.nome : avaria.zona_codigo)} — ${escapeHtml(tipo ? tipo.nome : avaria.tipo_codigo)}<br>` +
        `Marcada em ${_dataBr(avaria.primeira_vez_em)} · vista ${avaria.vezes_vista}x`;
    box.appendChild(aviso);

    const acoes = document.createElement('div');
    acoes.className = 'avaria-mapa-acoes';

    const btnConfirmar = document.createElement('button');
    btnConfirmar.type = 'button';
    btnConfirmar.className = 'btn btn-primary';
    btnConfirmar.textContent = 'CONFIRMAR QUE JÁ ESTAVA';
    btnConfirmar.addEventListener('click', () => gravar(btnConfirmar));
    acoes.appendChild(btnConfirmar);

    box.appendChild(acoes);
    document.getElementById('btn-registrar-avaria').style.display = 'none';
}

function mostrarRegistrarNormal() {
    const box = document.getElementById('av-ja-marcada');
    box.style.display = 'none';
    box.innerHTML = '';
    document.getElementById('btn-registrar-avaria').style.display = '';
}

// ─── Gravação — upsert único (POST /portaria/avarias) pros dois caminhos ──
// (registrar avaria nova / confirmar uma já marcada). PIOROU e "não vi mais
// essa" saíram desta tela (decisão do Alisson, 17/09): quem dá baixa numa
// avaria é a funilaria, não o controlador — ver item [4] do módulo
// Manutenção. /contestar e o "piorou" do backend continuam existindo, só
// não têm mais gatilho aqui.
async function gravar(btnEl) {
    const erro = document.getElementById('av-erro');
    erro.style.display = 'none';

    const prefixo = document.getElementById('av-prefixo').value.trim();
    const motoristaRe = document.getElementById('av-motorista-re').value.trim();
    if (!prefixo) {
        erro.textContent = 'Digite o carro.';
        erro.style.display = 'block';
        return;
    }
    // Única exceção à regra número um (nunca recusar registro): decisão de
    // operação — a avaria é vista com o motorista ali na frente.
    if (!motoristaRe) {
        erro.textContent = 'Digite o RE.';
        erro.style.display = 'block';
        return;
    }
    if (!zonaAtual || !tipoAtual) {
        erro.textContent = 'Escolha a avaria.';
        erro.style.display = 'block';
        return;
    }
    const observacao = document.getElementById('av-observacao').value.trim();

    const payload = {
        prefixo,
        zona_codigo: zonaAtual,
        tipo_codigo: tipoAtual,
        motorista_re: motoristaRe,
        linha_codigo: linhaSelecionada ? linhaSelecionada.codigo : null,
        observacao: observacao || null,
    };

    if (btnEl) btnEl.disabled = true;
    try {
        const resp = await apiPost('/portaria/avarias', payload);
        document.getElementById('av-observacao').value = '';
        limparSelecaoAvaria();
        await carregarMapaCarro();
        await carregarUltimas();
        if (resp.ciclo_anterior) {
            const status = document.getElementById('av-prefixo-status');
            const quando = _dataBr(resp.ciclo_anterior.encerrada_em);
            const motivo = resp.ciclo_anterior.encerramento === 'REPARADA' ? 'reparado' : 'encerrado';
            status.textContent += (status.textContent ? ' · ' : '') + `este carro já teve esse dano antes (${motivo} em ${quando}).`;
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        if (btnEl) btnEl.disabled = false;
    }
}

// ─── Suas avarias do turno — filtro no SQL (?minhas=true), não no JS ───
async function carregarUltimas() {
    const el = document.getElementById('av-lista-turno');
    try {
        const minhas = await apiGet('/portaria/avarias?minhas=true');
        if (minhas.length === 0) {
            el.innerHTML = '<div class="oc-vazio">Nenhuma avaria registrada ainda.</div>';
            return;
        }
        el.innerHTML = '';
        for (const a of minhas.slice(0, 8)) {
            const zona = catalogo.zonas.find((z) => z.codigo === a.zona_codigo);
            const tipo = catalogo.tipos.find((t) => t.codigo === a.tipo_codigo);
            const div = document.createElement('div');
            div.className = 'portaria-item';
            div.style.marginBottom = '8px';
            div.style.cursor = 'default';
            div.innerHTML = `
                <div>
                    <div class="portaria-item-placa">${escapeHtml(a.prefixo)}</div>
                    <div class="portaria-item-sub">${escapeHtml(zona ? zona.nome : a.zona_codigo)} — ${escapeHtml(tipo ? tipo.nome : a.tipo_codigo)}</div>
                </div>
            `;
            el.appendChild(div);
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        el.innerHTML = `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

// ─── Aba HISTÓRICO — compartilhado entre controladores, com quem anotou ──
function _desdePeriodo(periodo) {
    if (periodo === 'hoje') return dataServicoISO();
    const dias = periodo === '7' ? 7 : 30;
    const d = new Date();
    d.setDate(d.getDate() - dias);
    return dataLocalISO(d);
}

function initHistorico() {
    document.querySelectorAll('#av-hist-periodo .filtro-btn[data-periodo]').forEach((btn) => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#av-hist-periodo .filtro-btn[data-periodo]').forEach((b) => b.classList.remove('active'));
            btn.classList.add('active');
            histPeriodo = btn.dataset.periodo;
            carregarHistorico();
        });
    });
    let handle = null;
    document.getElementById('av-hist-prefixo').addEventListener('input', () => {
        clearTimeout(handle);
        handle = setTimeout(carregarHistorico, 300);
    });
    document.getElementById('av-modal-detalhe-fechar').addEventListener('click', fecharDetalhe);
    document.getElementById('av-modal-detalhe').addEventListener('click', (e) => {
        if (e.target.id === 'av-modal-detalhe') fecharDetalhe();
    });
}

async function carregarHistorico() {
    const el = document.getElementById('av-hist-lista');
    const prefixo = document.getElementById('av-hist-prefixo').value.trim();
    el.innerHTML = '<div class="patio-loading">Carregando…</div>';
    try {
        const desde = _desdePeriodo(histPeriodo);
        let url = `/portaria/avarias/historico?desde=${encodeURIComponent(desde)}`;
        if (prefixo) url += `&prefixo=${encodeURIComponent(prefixo)}`;
        const itens = await apiGet(url);
        if (itens.length === 0) {
            el.innerHTML = '<div class="oc-vazio">Nenhuma avaria no período.</div>';
            return;
        }
        el.innerHTML = '';
        for (const carro of _agruparPorCarro(itens)) {
            el.appendChild(_cartaoCarro(carro));
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        el.innerHTML = `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

// ─── Agrupamento por carro (17/09, item [2]) — em produção 8 carros já têm ──
// 2 avarias abertas cada; linhas soltas escondiam que era o mesmo carro.
// Um cartão por prefixo, carros e avarias dentro de cada um ordenados pela
// mais recente primeiro (ultima_vez_em, com fallback pra primeira_vez_em).
function _maisRecente(item) {
    return new Date(item.ultima_vez_em || item.primeira_vez_em).getTime();
}

function _agruparPorCarro(itens) {
    const porPrefixo = new Map();
    for (const item of itens) {
        if (!porPrefixo.has(item.prefixo)) porPrefixo.set(item.prefixo, []);
        porPrefixo.get(item.prefixo).push(item);
    }
    const carros = [...porPrefixo.entries()].map(([prefixo, avarias]) => {
        avarias.sort((a, b) => _maisRecente(b) - _maisRecente(a));
        return { prefixo, avarias };
    });
    carros.sort((a, b) => _maisRecente(b.avarias[0]) - _maisRecente(a.avarias[0]));
    return carros;
}

// Cartão nasce sempre EXPANDIDO — nada de accordion (o controlador precisa
// ver tudo de uma vez). Reusa o padrão .oc-card/.oc-card-header já usado em
// ocorrencia.form.js, ⛔ não inventa CSS novo.
function _cartaoCarro({ prefixo, avarias }) {
    const abertas = avarias.filter((a) => a.status === 'ABERTA').length;
    const card = document.createElement('div');
    card.className = 'oc-card';
    card.innerHTML = `
        <div class="oc-card-header">
            <div class="oc-card-titulo">${escapeHtml(prefixo)}</div>
            <div class="portaria-item-hora">${abertas} aberta${abertas === 1 ? '' : 's'}</div>
        </div>
    `;
    for (const item of avarias) {
        card.appendChild(_linhaHistorico(item));
    }
    return card;
}

function _linhaHistorico(item) {
    const zona = catalogo.zonas.find((z) => z.codigo === item.zona_codigo);
    const tipo = catalogo.tipos.find((t) => t.codigo === item.tipo_codigo);
    // 🔴 "qual controlador marcou" é o dado que o Alisson quer ver — visível
    // na LISTA, não só no detalhe (§4 do prompt). Vem pronto do backend
    // (registrado_por_re/registrado_por_nome), ⛔ nunca resolvido no cliente.
    const quem = item.registrado_por_nome
        ? `${item.registrado_por_nome}${item.registrado_por_re ? ' (RE ' + item.registrado_por_re + ')' : ''}`
        : null;
    const statusTxt = item.status === 'ABERTA'
        ? 'aberta'
        : `encerrada${item.encerramento ? ' — ' + item.encerramento.toLowerCase() : ''}`;
    const div = document.createElement('div');
    div.className = 'portaria-item';
    div.style.marginBottom = '8px';
    div.innerHTML = `
        <div>
            <div class="portaria-item-placa">${escapeHtml(zona ? zona.nome : item.zona_codigo)} — ${escapeHtml(tipo ? tipo.nome : item.tipo_codigo)}</div>
            <div class="portaria-item-sub">Marcada em ${_dataBr(item.primeira_vez_em)}${quem ? ' por ' + escapeHtml(quem) : ''} · vista ${item.vezes_vista}x · ${escapeHtml(statusTxt)}</div>
        </div>
    `;
    div.addEventListener('click', () => abrirDetalhe(item.id));
    return div;
}

async function abrirDetalhe(id) {
    const modal = document.getElementById('av-modal-detalhe');
    const titulo = document.getElementById('av-modal-detalhe-titulo');
    const corpo = document.getElementById('av-modal-detalhe-corpo');
    titulo.textContent = 'Carregando…';
    corpo.innerHTML = '';
    modal.classList.add('open');
    try {
        const avaria = await apiGet(`/portaria/avarias/${id}`);
        const zona = catalogo.zonas.find((z) => z.codigo === avaria.zona_codigo);
        const tipo = catalogo.tipos.find((t) => t.codigo === avaria.tipo_codigo);
        titulo.textContent = `${avaria.prefixo} · ${zona ? zona.nome : avaria.zona_codigo} — ${tipo ? tipo.nome : avaria.tipo_codigo}`;
        const linhas = (avaria.constatacoes || []).map((c) => `
            <div class="portaria-item" style="cursor:default;margin-bottom:8px;${c.anulada_em ? 'opacity:0.5' : ''}">
                <div class="portaria-item-placa">${_dataHoraBr(c.ocorrido_em)}${c.piorou ? ' · PIOROU' : ''}${c.anulada_em ? ' · ANULADA' : ''}</div>
                <div class="portaria-item-sub">
                    ${c.motorista_re ? 'RE ' + escapeHtml(c.motorista_re) : escapeHtml(c.motorista_nome || 'motorista não identificado')}
                    ${c.linha_codigo ? ' · linha ' + escapeHtml(c.linha_codigo) : ''}
                    · anotado por ${escapeHtml(c.registrado_por_nome || '—')}${c.registrado_por_re ? ' (RE ' + escapeHtml(c.registrado_por_re) + ')' : ''}
                    ${c.observacao ? ' · ' + escapeHtml(c.observacao) : ''}
                </div>
            </div>
        `).join('');
        corpo.innerHTML = linhas || '<div class="oc-vazio">Nenhuma constatação.</div>';
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        corpo.innerHTML = `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

function fecharDetalhe() {
    document.getElementById('av-modal-detalhe').classList.remove('open');
}

// ─── Registrar ───────────────────────────────────────────────────────────
function initRegistrar() {
    document.getElementById('btn-registrar-avaria').addEventListener('click', (e) => {
        gravar(e.currentTarget);
    });
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
async function bootstrap() {
    initHeader();
    initTabs();
    initHistorico();
    initLinha();
    initIdentificacao();
    initRegistrar();
    document.getElementById('av-prefixo').addEventListener('blur', carregarMapaCarro);

    try {
        catalogo = await apiGet('/portaria/avarias/catalogo');
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        catalogo = { zonas: [], tipos: [] };
    }
    renderLocais();

    try {
        linhasCache = await apiGet('/portaria/catalogo/linhas');
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        linhasCache = [];
    }

    await carregarUltimas();
}

bootstrap();
