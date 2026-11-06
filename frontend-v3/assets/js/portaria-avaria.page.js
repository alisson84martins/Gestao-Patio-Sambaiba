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
 * backend): carro vazio, RE vazio (o motorista está na frente) e carrinho
 * vazio.
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
 *
 * 06/11/2026 — decisão do Alisson: um ônibus de até 23 m tem várias
 * avarias, e repetir Local → Zona → Tipo → REGISTRAR pra cada uma deixava o
 * motorista esperando. Agora é um CARRINHO: vários TIPOS na mesma zona e
 * várias ZONAS no mesmo carro, tudo num envio só (FINALIZAR). O 3º toque
 * liga/desliga o par zona+tipo; trocar local/zona não apaga nada. A caixa
 * "já marcada" + "CONFIRMAR QUE JÁ ESTAVA" virou etiqueta no item ("já
 * estava marcada · vista Nx"), com o mesmo significado: enviar o item é a
 * confirmação (o upsert soma vezes_vista). O envio é UM POR UM, em
 * sequência, com o mesmo POST /portaria/avarias de antes — ⛔ não
 * Promise.all: são várias escritas no mesmo carro e o upsert por zona+tipo
 * não deve concorrer consigo mesmo. Falhou algum? Os gravados saem do
 * carrinho e só os que falharam ficam pra TENTAR DE NOVO.
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
// Chave do chip "Outro" no 1º nível — não é uma vista do catálogo.
const LOCAL_OUTRO = '__OUTRO__';

let catalogo = { zonas: [], tipos: [] };
let linhasCache = [];
let linhaSelecionada = null;      // { codigo, nome }
let linhaEscolhidaManualmente = false;
let avariasAbertas = [];          // GET /avarias/mapa — só ABERTA, deste carro
let vistaAtual = null;
let zonaAtual = null;             // zona com os tipos abertos agora (não é o carrinho)
// Carrinho (06/11/2026): pares zona+tipo escolhidos e ainda não gravados.
// { zona, tipo, observacao, estado: 'novo'|'gravando'|'ok'|'erro', erro }
let carrinho = [];
let enviando = false;
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
        // ⛔ O carrinho fica (o controlador pode ter errado o carro); só as
        // etiquetas "já estava marcada" somem.
        renderCarrinho();
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
        // Prefixo trocado com itens no carrinho: os itens ficam e as
        // etiquetas "já estava marcada" são recalculadas pro carro novo.
        renderCarrinho();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        avariasAbertas = [];
        renderCarrinho();
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
        btn.dataset.local = vista;
        btn.dataset.rotulo = VISTA_LABELS[vista] || vista;
        btn.addEventListener('click', () => selecionarVista(vista, btn));
        el.appendChild(btn);
    }
    if (zonas.some((z) => z.codigo === ZONA_ESCAPE_CODIGO)) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.dataset.local = LOCAL_OUTRO;
        btn.dataset.rotulo = 'Outro';
        btn.addEventListener('click', () => selecionarLocalOutro(btn));
        el.appendChild(btn);
    }
    atualizarContadores();
}

function selecionarVista(vista, btnAtivo) {
    if (enviando) return;
    vistaAtual = vista;
    document.querySelectorAll('#av-locais .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');

    // Trocar de local fecha a zona aberta, mas ⛔ nunca mexe no carrinho.
    zonaAtual = null;
    document.getElementById('av-tipos').innerHTML = '';
    atualizarSelecionadoTexto();

    renderZonas();
}

// ─── Item 3b — "Outro" é chip próprio no 1º nível, sem 2º nível (a zona já
// é uma só: ZONA_ESCAPE_CODIGO). Pula direto pro TIPO.
function selecionarLocalOutro(btnAtivo) {
    if (enviando) return;
    vistaAtual = null;
    document.querySelectorAll('#av-locais .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');
    document.getElementById('av-zonas').innerHTML = '';

    zonaAtual = ZONA_ESCAPE_CODIGO;
    atualizarSelecionadoTexto();
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
        btn.dataset.zona = zona.codigo;
        btn.dataset.rotulo = zona.nome;
        btn.addEventListener('click', () => selecionarZona(zona.codigo, btn));
        el.appendChild(btn);
    }
    atualizarContadores();
}

function selecionarZona(codigo, btnAtivo) {
    if (enviando) return;
    zonaAtual = codigo;
    document.querySelectorAll('#av-zonas .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');
    atualizarSelecionadoTexto();
    renderTipos();
}

function renderTipos() {
    const el = document.getElementById('av-tipos');
    el.innerHTML = '';
    for (const tipo of _tiposPermitidos()) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.dataset.tipo = tipo.codigo;
        btn.textContent = tipo.nome;
        btn.addEventListener('click', () => alternarTipo(tipo.codigo));
        el.appendChild(btn);
    }
    atualizarTiposAtivos();
}

function atualizarSelecionadoTexto() {
    const el = document.getElementById('av-selecionado');
    el.textContent = zonaAtual ? _nomeZona(zonaAtual) : '';
}

function limparSelecaoAvaria() {
    vistaAtual = null;
    zonaAtual = null;
    document.querySelectorAll('#av-locais .recolhida-chip').forEach((b) => b.classList.remove('active'));
    document.getElementById('av-zonas').innerHTML = '';
    document.getElementById('av-tipos').innerHTML = '';
    atualizarSelecionadoTexto();
}

// ─── Carrinho (06/11/2026) ─────────────────────────────────────────────
function _nomeZona(codigo) {
    const zona = catalogo.zonas.find((z) => z.codigo === codigo);
    return zona ? zona.nome : codigo;
}

function _nomeTipo(codigo) {
    const tipo = catalogo.tipos.find((t) => t.codigo === codigo);
    return tipo ? tipo.nome : codigo;
}

function _itemDoCarrinho(zona, tipo) {
    return carrinho.find((i) => i.zona === zona && i.tipo === tipo);
}

// 3º toque liga/desliga o par zona+tipo — ⛔ o mesmo par nunca entra duas
// vezes (o upsert do backend é por zona+tipo: duplicar só contaria 2x).
function alternarTipo(codigo) {
    if (enviando || !zonaAtual) return;
    const existente = _itemDoCarrinho(zonaAtual, codigo);
    if (existente) {
        carrinho = carrinho.filter((i) => i !== existente);
    } else {
        carrinho.push({ zona: zonaAtual, tipo: codigo, observacao: '', estado: 'novo', erro: null });
    }
    limparResultado();
    atualizarCarrinho();
}

function removerDoCarrinho(item) {
    if (enviando) return;
    carrinho = carrinho.filter((i) => i !== item);
    limparResultado();
    atualizarCarrinho();
}

function atualizarCarrinho() {
    atualizarTiposAtivos();
    atualizarContadores();
    renderCarrinho();
    atualizarBotao();
}

function atualizarTiposAtivos() {
    document.querySelectorAll('#av-tipos .recolhida-chip').forEach((b) => {
        b.classList.toggle('active', !!_itemDoCarrinho(zonaAtual, b.dataset.tipo));
    });
}

function _rotularChip(btn, quantidade) {
    btn.textContent = btn.dataset.rotulo;
    if (quantidade > 0) {
        const contador = document.createElement('span');
        contador.className = 'avaria-chip-contador';
        contador.textContent = ` · ${quantidade}`;
        btn.appendChild(contador);
    }
    btn.classList.toggle('avaria-chip-com-itens', quantidade > 0);
}

// Contador no chip de LOCAL e de ZONA (ex.: "Para-choque · 2").
function atualizarContadores() {
    document.querySelectorAll('#av-locais .recolhida-chip').forEach((btn) => {
        const local = btn.dataset.local;
        const n = carrinho.filter((i) => {
            if (local === LOCAL_OUTRO) return i.zona === ZONA_ESCAPE_CODIGO;
            if (i.zona === ZONA_ESCAPE_CODIGO) return false;
            const zona = catalogo.zonas.find((z) => z.codigo === i.zona);
            return !!zona && zona.vista === local;
        }).length;
        _rotularChip(btn, n);
    });
    document.querySelectorAll('#av-zonas .recolhida-chip').forEach((btn) => {
        _rotularChip(btn, carrinho.filter((i) => i.zona === btn.dataset.zona).length);
    });
}

// "já estava marcada" decide só com avariasAbertas (já veio do GET
// /avarias/mapa no blur do prefixo) — ⛔ nenhuma chamada nova aqui. Enviar
// o item É a confirmação: o upsert soma vezes_vista.
function renderCarrinho() {
    const el = document.getElementById('av-carrinho');
    el.innerHTML = '';
    if (carrinho.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Nenhuma avaria escolhida ainda.</div>';
        return;
    }
    for (const item of carrinho) {
        const div = document.createElement('div');
        div.className = 'avaria-carrinho-item';
        if (item.estado === 'erro') div.classList.add('avaria-carrinho-erro');
        if (item.estado === 'ok') div.classList.add('avaria-carrinho-ok');

        const cabeca = document.createElement('div');
        cabeca.className = 'avaria-carrinho-cabeca';
        const textos = document.createElement('div');
        textos.className = 'avaria-carrinho-textos';
        const titulo = document.createElement('div');
        titulo.className = 'avaria-carrinho-titulo';
        titulo.textContent = `${item.estado === 'ok' ? '✓ ' : ''}${_nomeZona(item.zona)} — ${_nomeTipo(item.tipo)}`;
        textos.appendChild(titulo);
        const aberta = avariasAbertas.find((a) => a.zona_codigo === item.zona && a.tipo_codigo === item.tipo);
        if (aberta) {
            const etiqueta = document.createElement('div');
            etiqueta.className = 'avaria-carrinho-etiqueta';
            etiqueta.textContent = `já estava marcada · vista ${aberta.vezes_vista}x`;
            textos.appendChild(etiqueta);
        }
        cabeca.appendChild(textos);

        const remover = document.createElement('button');
        remover.type = 'button';
        remover.className = 'avaria-carrinho-remover';
        remover.textContent = '✕';
        remover.setAttribute('aria-label', 'Remover');
        remover.disabled = enviando;
        remover.addEventListener('click', () => removerDoCarrinho(item));
        cabeca.appendChild(remover);
        div.appendChild(cabeca);

        const obs = document.createElement('input');
        obs.type = 'text';
        obs.className = 'form-input avaria-carrinho-obs';
        obs.autocomplete = 'off';
        obs.placeholder = item.zona === ZONA_ESCAPE_CODIGO ? 'Descreva o dano' : 'Observação (opcional)';
        obs.value = item.observacao;
        obs.readOnly = enviando;
        obs.addEventListener('input', () => { item.observacao = obs.value; });
        div.appendChild(obs);

        if (item.estado === 'erro' && item.erro) {
            const msg = document.createElement('div');
            msg.className = 'avaria-carrinho-msg-erro';
            msg.textContent = item.erro;
            div.appendChild(msg);
        }
        el.appendChild(div);
    }
}

function atualizarBotao() {
    if (enviando) return;
    const btn = document.getElementById('btn-finalizar-avaria');
    const n = carrinho.length;
    const temErro = carrinho.some((i) => i.estado === 'erro');
    btn.textContent = temErro ? `TENTAR DE NOVO (${n})` : `FINALIZAR (${n})`;
    btn.disabled = n === 0;
}

function travarChips(travar) {
    document.querySelectorAll('#av-locais button, #av-zonas button, #av-tipos button')
        .forEach((b) => { b.disabled = travar; });
}

function limparResultado() {
    const el = document.getElementById('av-resultado');
    el.textContent = '';
    el.style.display = 'none';
}

function mostrarResultado(msg) {
    const el = document.getElementById('av-resultado');
    el.textContent = msg;
    el.style.display = 'block';
}

function _diaMes(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
    } catch {
        return iso;
    }
}

// ─── Gravação — upsert único (POST /portaria/avarias), um item por vez ──
// PIOROU e "não vi mais essa" saíram desta tela (decisão do Alisson, 17/09):
// quem dá baixa numa avaria é a funilaria, não o controlador — ver item [4]
// do módulo Manutenção. /contestar e o "piorou" do backend continuam
// existindo, só não têm mais gatilho aqui.
//
// 06/11/2026 — ⛔ nada de Promise.all: são várias escritas no MESMO carro e
// o upsert por zona+tipo não deve concorrer consigo mesmo. Sequencial, e
// mapa/turno recarregados UMA vez no fim, não a cada item.
async function finalizar() {
    if (enviando) return;
    const erro = document.getElementById('av-erro');
    erro.style.display = 'none';
    limparResultado();

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
    if (carrinho.length === 0) {
        erro.textContent = 'Escolha ao menos uma avaria.';
        erro.style.display = 'block';
        return;
    }
    const linhaCodigo = linhaSelecionada ? linhaSelecionada.codigo : null;

    const btn = document.getElementById('btn-finalizar-avaria');
    const fila = [...carrinho];
    const ciclos = [];
    let gravados = 0;
    let sessaoExpirou = false;

    enviando = true;
    btn.disabled = true;
    travarChips(true);
    try {
        for (let i = 0; i < fila.length; i++) {
            const item = fila[i];
            btn.textContent = `Gravando ${i + 1} de ${fila.length}…`;
            item.estado = 'gravando';
            item.erro = null;
            renderCarrinho();
            try {
                const resp = await apiPost('/portaria/avarias', {
                    prefixo,
                    zona_codigo: item.zona,
                    tipo_codigo: item.tipo,
                    motorista_re: motoristaRe,
                    linha_codigo: linhaCodigo,
                    observacao: item.observacao.trim() || null,
                });
                item.estado = 'ok';
                gravados++;
                if (resp.ciclo_anterior) ciclos.push({ item, ciclo: resp.ciclo_anterior });
            } catch (err) {
                if (err instanceof ApiError && err.status === 401) {
                    sessaoExpirou = true;
                    item.estado = 'novo';
                    break;
                }
                item.estado = 'erro';
                item.erro = err.message;
            }
        }
    } finally {
        enviando = false;
        travarChips(false);
    }

    // Gravados saem; só fica o que falhou (ou nem chegou a ir, no 401).
    carrinho = carrinho.filter((i) => i.estado !== 'ok');
    atualizarCarrinho();
    if (sessaoExpirou) return;

    if (gravados > 0) {
        // Carro, RE e linha ficam — o mesmo ônibus pode receber mais.
        await carregarMapaCarro();
        await carregarUltimas();
    }

    if (carrinho.length === 0) {
        mostrarResultado(`${gravados} ${gravados === 1 ? 'avaria gravada' : 'avarias gravadas'} no carro ${prefixo}.`);
    } else {
        const falhas = carrinho.length;
        erro.textContent = (gravados > 0 ? `${gravados} gravada(s). ` : '')
            + `${falhas} não ${falhas === 1 ? 'gravou' : 'gravaram'} — confira em vermelho e toque em TENTAR DE NOVO.`;
        erro.style.display = 'block';
    }

    if (ciclos.length > 0) {
        const status = document.getElementById('av-prefixo-status');
        const partes = ciclos.map(({ item, ciclo }) => {
            const motivo = ciclo.encerramento === 'REPARADA' ? 'reparado' : 'encerrado';
            return `${_nomeZona(item.zona)} — ${_nomeTipo(item.tipo)} (${motivo} em ${_diaMes(ciclo.encerrada_em)})`;
        });
        status.textContent += (status.textContent ? ' · ' : '') + `este carro já teve antes: ${partes.join('; ')}.`;
    }
}

// ─── Marcação = um FINALIZAR (06/11/2026) ───────────────────────────────
// Teste em campo do Alisson (06/11, ~02h): o carrinho gravou 2 avarias do
// carro 1721 num FINALIZAR só, mas o turno mostrava um cartão 1721 por avaria
// e o Histórico, dois chips dentro do cartão 1721 — parecia que o carro tinha
// sido marcado duas vezes, e nenhuma das listas dizia QUEM dirigia nem a
// linha. Regra dele: carro que sai com várias avarias marcadas naquele
// momento é UMA marcação = UM bloco, com motorista, linha e as avarias dentro.
//
// Chave = prefixo + motorista_re + RE de quem anotou + data_servico — é
// exatamente o que um FINALIZAR produz. motorista_re nulo conta como valor
// (JSON distingue null de "14249"), então "sem RE" nunca se junta com RE
// preenchido. ⛔ Esta é a ÚNICA definição de marcação na tela.
function _chaveMarcacao(item) {
    return JSON.stringify([
        item.prefixo,
        item.motorista_re ?? null,
        item.registrado_por_re ?? null,
        item.data_servico ?? null,
    ]);
}

// Hora da marcação = quando a avaria foi vista pela 1ª vez (não muda quando
// alguém confirma de novo, ao contrário de ultima_vez_em).
function _instanteMarcacao(item) {
    return new Date(item.primeira_vez_em || item.ultima_vez_em || 0).getTime();
}

// Blocos e avarias dentro deles: do mais recente pro mais antigo.
function _agruparPorMarcacao(itens) {
    const porChave = new Map();
    for (const item of itens) {
        const chave = _chaveMarcacao(item);
        if (!porChave.has(chave)) porChave.set(chave, []);
        porChave.get(chave).push(item);
    }
    const blocos = [...porChave.values()].map((avarias) => {
        avarias.sort((a, b) => _instanteMarcacao(b) - _instanteMarcacao(a));
        return avarias;
    });
    blocos.sort((a, b) => _instanteMarcacao(b[0]) - _instanteMarcacao(a[0]));
    return blocos;
}

// Um .portaria-item por marcação; cada avaria é uma LINHA de texto dentro
// dele (⛔ não um chip separado), tocável, abrindo o detalhe daquela avaria —
// o acesso às constatações não se perde.
function _blocoMarcacao(avarias) {
    const base = avarias[0];
    const n = avarias.length;
    const maisAntiga = avarias.reduce((min, a) => (_instanteMarcacao(a) < _instanteMarcacao(min) ? a : min), base);

    let motorista = 'Motorista não identificado';
    if (base.motorista_re) {
        motorista = `Motorista RE ${base.motorista_re}${base.motorista_nome ? ' — ' + base.motorista_nome : ''}`;
    }
    if (base.linha_codigo) motorista += ` · Linha ${base.linha_codigo}`;

    // 🔴 "qual controlador marcou" vem pronto do backend
    // (registrado_por_re/registrado_por_nome), ⛔ nunca resolvido no cliente.
    const quem = base.registrado_por_nome
        ? `${base.registrado_por_nome}${base.registrado_por_re ? ' (RE ' + base.registrado_por_re + ')' : ''}`
        : null;
    const quando = _dataHoraBr(maisAntiga.primeira_vez_em) + (quem ? ` · anotado por ${quem}` : '');

    const bloco = document.createElement('div');
    bloco.className = 'portaria-item avaria-marcacao';
    bloco.innerHTML = `
        <div class="avaria-marcacao-topo">
            <div class="portaria-item-placa">${escapeHtml(base.prefixo)}</div>
            <div class="portaria-item-hora">${n} ${n === 1 ? 'avaria' : 'avarias'}</div>
        </div>
        <div class="portaria-item-sub">${escapeHtml(motorista)}</div>
        <div class="portaria-item-sub">${escapeHtml(quando)}</div>
    `;
    for (const a of avarias) {
        const statusTxt = a.status === 'ABERTA'
            ? 'aberta'
            : `encerrada${a.encerramento ? ' — ' + a.encerramento.toLowerCase() : ''}`;
        const linha = document.createElement('button');
        linha.type = 'button';
        linha.className = 'avaria-marcacao-linha';
        linha.dataset.avariaId = a.id;
        linha.textContent = `${_nomeZona(a.zona_codigo)} — ${_nomeTipo(a.tipo_codigo)} · vista ${a.vezes_vista}x · ${statusTxt}`;
        linha.addEventListener('click', () => abrirDetalhe(a.id));
        bloco.appendChild(linha);
    }
    return bloco;
}

// ─── Suas avarias do turno — mesmos dados e desenho do Histórico ────────
// 06/11/2026: lê /avarias/historico (motorista e linha vêm prontos) filtrado
// por quem anotou + dia de serviço (dataServicoISO respeita o turno que vira
// a meia-noite). Sem RE do usuário na sessão, cai no ?minhas=true antigo —
// sem linha nem RE de quem anotou, mas agrupado do mesmo jeito. Aqui SEM
// cartão por carro: lista direta de blocos, no máximo 8.
const TURNO_MAX_MARCACOES = 8;

async function _avariasDoTurno() {
    const user = getCurrentUser();
    const re = user && user.re ? String(user.re).trim() : '';
    if (re) {
        return apiGet(`/portaria/avarias/historico?desde=${encodeURIComponent(dataServicoISO())}`
            + `&registrado_por=${encodeURIComponent(re)}`);
    }
    return apiGet('/portaria/avarias?minhas=true');
}

async function carregarUltimas() {
    const el = document.getElementById('av-lista-turno');
    try {
        const itens = await _avariasDoTurno();
        if (itens.length === 0) {
            el.innerHTML = '<div class="oc-vazio">Nenhuma avaria registrada ainda.</div>';
            return;
        }
        el.innerHTML = '';
        for (const marcacao of _agruparPorMarcacao(itens).slice(0, TURNO_MAX_MARCACOES)) {
            el.appendChild(_blocoMarcacao(marcacao));
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
// 06/11/2026: o cartão por carro fica (17/09, item [2]), mas dentro dele vai
// UM BLOCO POR MARCAÇÃO (_blocoMarcacao), não um chip por avaria.
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
    for (const marcacao of _agruparPorMarcacao(avarias)) {
        card.appendChild(_blocoMarcacao(marcacao));
    }
    return card;
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

// ─── Finalizar ───────────────────────────────────────────────────────────
function initFinalizar() {
    document.getElementById('btn-finalizar-avaria').addEventListener('click', finalizar);
    // Sair (← Voltar, fechar aba) com item NÃO gravado pede confirmação.
    // Fora isso, nenhum aviso.
    window.addEventListener('beforeunload', (e) => {
        if (!carrinho.some((i) => i.estado !== 'ok')) return;
        e.preventDefault();
        e.returnValue = '';
    });
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
async function bootstrap() {
    initHeader();
    initTabs();
    initHistorico();
    initLinha();
    initIdentificacao();
    initFinalizar();
    document.getElementById('av-prefixo').addEventListener('blur', carregarMapaCarro);

    try {
        catalogo = await apiGet('/portaria/avarias/catalogo');
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        catalogo = { zonas: [], tipos: [] };
    }
    renderLocais();
    atualizarCarrinho();

    try {
        linhasCache = await apiGet('/portaria/catalogo/linhas');
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        linhasCache = [];
    }

    await carregarUltimas();
}

bootstrap();
