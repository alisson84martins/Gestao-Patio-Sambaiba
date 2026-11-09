/*
 * portaria-recolhida.page.js — Tela do controlador para recolhida anormal
 * -----------------------------------------------------------
 * Ônibus que recolhe fora de hora — poucos toques, sem formulário de várias
 * seções (§2.6 do prompt: "sem muita poluição visual, sem modelos de
 * exemplo").
 *
 * 🔴 §2.9-0 (correção de escopo): o controlador DIGITA RE motorista e RE
 * cobrador — ele está com o carro na frente, é a melhor fonte do dado. A
 * escala só SUGERE o RE do motorista (pré-preenchimento, nunca trava). O
 * que esta tela nunca mostra é o ACUMULADO — histórico, contagem por
 * motorista, dias anteriores — isso é recolhida_gerencial.
 *
 * 🔧 Bloco G: motivo vem antes do defeito — só motivo=DEFEITO mostra a
 * seção de categoria/tipo de defeito.
 *
 * 08/11/2026 — decisão do Alisson: carro que recolhe com elétrica E pneu
 * não pode perder um dos dois. Mesmo princípio do carrinho da avaria
 * (portaria-avaria.page.js): o 2º toque (tipo) liga/desliga o defeito,
 * trocar de categoria não apaga nada, chip de categoria mostra contador,
 * cada defeito tem observação própria, FINALIZAR (N) no rodapé sticky e
 * aviso ao sair com defeito não gravado. ⛔ Diferença deliberada da
 * avaria: aqui é UM POST com a lista (`defeitos`, migration 052) — o carro
 * recolheu uma vez, então é 1 recolhida (1 RA na fila da Manutenção, 1 na
 * conta do motorista) com N defeitos, e o backend abre 1 ficha por defeito.
 *
 * 🔴 Regra número um: POST /portaria/recolhidas sempre registra (201) —
 * prefixo não cadastrado, sem escala, ficha que não nasceu, nada disso
 * bloqueia o registro.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { buscarPorRe } from './identidade.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

const CATEGORIAS = [
    { codigo: 'mecanica', label: 'Mecânica' },
    { codigo: 'eletrica', label: 'Elétrica' },
    { codigo: 'ar', label: 'Ar-condicionado' },
    { codigo: 'funilaria', label: 'Funilaria' },
    { codigo: 'pneus', label: 'Pneus' },
    { codigo: 'interno', label: 'Interno' },
    { codigo: 'outros', label: 'Outros' },
];

const MOTIVOS = [
    { codigo: 'DEFEITO', label: 'Defeito' },
    { codigo: 'COLISAO', label: 'Colisão' },
    { codigo: 'FALTA_MOTORISTA', label: 'Falta motorista' },
    { codigo: 'FALTA_COBRADOR', label: 'Falta cobrador' },
    { codigo: 'OUTRO', label: 'Outro' },
];

let tiposDefeitoCache = null;
let linhasCache = null;
let categoriaAtual = null;     // categoria com os tipos abertos agora (não é o carrinho)
// Carrinho (08/11/2026): defeitos escolhidos e ainda não gravados.
// { codigo, nome, categoria, observacao }
let carrinho = [];
let enviando = false;
let linhaSelecionada = null;   // { codigo, nome } do catálogo linha
let motivoSelecionado = 'DEFEITO';

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

// ─── Carro/prefixo — mostra "cadastrado" e sugere o RE do motorista ────
function initPrefixo() {
    document.getElementById('rec-prefixo').addEventListener('blur', resolverPrefixo);
}

async function resolverPrefixo() {
    const prefixo = document.getElementById('rec-prefixo').value.trim();
    const status = document.getElementById('rec-prefixo-status');
    status.textContent = '';
    if (!prefixo) return;
    try {
        const resp = await apiGet(`/portaria/recolhidas/resolver-prefixo?prefixo=${encodeURIComponent(prefixo)}`);
        if (resp.encontrado) {
            status.textContent = resp.placa ? `Cadastrado — ${resp.placa}` : 'Cadastrado.';
            status.style.color = 'var(--accent3)';
        } else {
            status.textContent = 'Não cadastrado — registra assim mesmo.';
            status.style.color = 'var(--muted)';
        }
        // §2.9-0: sugestão é só pré-preenchimento — nunca sobrescreve o que
        // o controlador já digitou.
        const campoRe = document.getElementById('rec-motorista-re');
        if (resp.motorista_re_sugerido && !campoRe.value.trim()) {
            campoRe.value = resp.motorista_re_sugerido;
            await resolverRe('motorista');
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-recolhida] erro ao resolver prefixo:', err);
    }
}

// ─── RE motorista/cobrador — confirmação visual (§5.3), nunca bloqueia ──
function initIdentificacao() {
    document.getElementById('rec-motorista-re').addEventListener('blur', () => resolverRe('motorista'));
    document.getElementById('rec-cobrador-re').addEventListener('blur', () => resolverRe('cobrador'));
}

async function resolverRe(papel) {
    const campoRe = document.getElementById(`rec-${papel}-re`);
    const status = document.getElementById(`rec-${papel}-status`);
    const campoNome = document.getElementById(`rec-${papel}-nome`);
    const re = campoRe.value.trim();
    status.textContent = '';
    if (re.length < 3) {
        campoNome.style.display = 'none';
        return;
    }
    try {
        // Bloco A2: busca por RE unificada (app/routers/identidade.py) —
        // antes cada tela chamava um endpoint próprio pra confirmar RE.
        const resp = await buscarPorRe(re);
        if (resp.encontrado) {
            campoNome.style.display = 'none';
            campoNome.value = '';
            if (resp.ativo === false) {
                status.textContent = `${resp.nome} — desligado/inativo. Registra assim mesmo.`;
                status.style.color = '#f59e0b';
            } else {
                status.textContent = resp.nome;
                status.style.color = 'var(--accent3)';
            }
        } else {
            status.textContent = 'Não encontrado — pode informar o nome.';
            status.style.color = 'var(--muted)';
            campoNome.style.display = 'block';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error(`[portaria-recolhida] erro ao resolver RE (${papel}):`, err);
    }
}

// ─── Linha — autocomplete, toque escolhe ────────────────────────────────
function initLinha() {
    const input = document.getElementById('rec-linha-busca');
    let handle = null;
    input.addEventListener('input', () => {
        linhaSelecionada = null;
        atualizarLinhaSelecionada();
        clearTimeout(handle);
        handle = setTimeout(() => renderSugestoesLinha(input.value.trim()), 150);
    });
    input.addEventListener('focus', () => renderSugestoesLinha(input.value.trim()));
}

function renderSugestoesLinha(termo) {
    const el = document.getElementById('rec-linha-sugestoes');
    const lista = (linhasCache || []).filter((l) => l.ativa);
    const filtradas = termo
        ? lista.filter((l) => `${l.codigo} ${l.nome}`.toLowerCase().includes(termo.toLowerCase()))
        : lista;
    el.innerHTML = '';
    for (const linha of filtradas.slice(0, 12)) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.textContent = linha.codigo;
        btn.title = linha.nome;
        btn.addEventListener('click', () => {
            linhaSelecionada = linha;
            document.getElementById('rec-linha-busca').value = linha.codigo;
            atualizarLinhaSelecionada();
            el.innerHTML = '';
        });
        el.appendChild(btn);
    }
}

function atualizarLinhaSelecionada() {
    document.getElementById('rec-linha-selecionada').textContent =
        linhaSelecionada ? linhaSelecionada.nome : '';
}

// ─── Motivo — 5 botões; só DEFEITO mostra a seção de defeito (Bloco G) ──
function renderMotivos() {
    const el = document.getElementById('rec-motivos');
    el.innerHTML = '';
    for (const motivo of MOTIVOS) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip' + (motivo.codigo === motivoSelecionado ? ' active' : '');
        btn.textContent = motivo.label;
        btn.addEventListener('click', () => selecionarMotivo(motivo.codigo, btn));
        el.appendChild(btn);
    }
    atualizarVisibilidadeDefeito();
}

function selecionarMotivo(codigo, btnAtivo) {
    if (enviando) return;
    motivoSelecionado = codigo;
    document.querySelectorAll('#rec-motivos .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');
    atualizarVisibilidadeDefeito();
}

function atualizarVisibilidadeDefeito() {
    const ehDefeito = motivoSelecionado === 'DEFEITO';
    document.getElementById('rec-defeito-campo').style.display = ehDefeito ? 'block' : 'none';
    // ⛔ O carrinho NÃO é apagado ao trocar de motivo (o controlador pode ter
    // tocado errado) — só fica escondido e não vai no envio.
    document.getElementById('rec-carrinho-campo').style.display = ehDefeito ? 'block' : 'none';
    atualizarBotao();
}

// ─── Defeito — CATEGORIA (1º toque) → TIPO (2º toque liga/desliga) ──────
function renderCategorias() {
    const el = document.getElementById('rec-categorias');
    el.innerHTML = '';
    for (const cat of CATEGORIAS) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.dataset.categoria = cat.codigo;
        btn.dataset.rotulo = cat.label;
        btn.addEventListener('click', () => selecionarCategoria(cat.codigo, btn));
        el.appendChild(btn);
    }
    atualizarContadores();
}

function selecionarCategoria(codigo, btnAtivo) {
    if (enviando) return;
    categoriaAtual = codigo;
    document.querySelectorAll('#rec-categorias .recolhida-chip').forEach((b) => b.classList.remove('active'));
    btnAtivo.classList.add('active');
    // Trocar de categoria fecha os tipos abertos, mas ⛔ nunca mexe no carrinho.
    atualizarDefeitoSelecionado();
    renderTipos();
}

function renderTipos() {
    const el = document.getElementById('rec-tipos');
    el.innerHTML = '';
    if (!categoriaAtual) return;
    const tipos = (tiposDefeitoCache || []).filter((t) => t.categoria === categoriaAtual && t.ativo);
    for (const tipo of tipos) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'recolhida-chip';
        btn.dataset.tipo = tipo.codigo;
        btn.textContent = tipo.nome;
        btn.addEventListener('click', () => alternarTipo(tipo));
        el.appendChild(btn);
    }
    atualizarTiposAtivos();
}

function _labelCategoria(codigo) {
    const cat = CATEGORIAS.find((c) => c.codigo === codigo);
    return cat ? cat.label : codigo;
}

function _nomeTipo(codigo) {
    const tipo = (tiposDefeitoCache || []).find((t) => t.codigo === codigo);
    return tipo ? tipo.nome : codigo;
}

function atualizarDefeitoSelecionado() {
    document.getElementById('rec-defeito-selecionado').textContent =
        categoriaAtual ? _labelCategoria(categoriaAtual) : '';
}

// ─── Carrinho (08/11/2026) — mesmo comportamento da avaria ─────────────
// 2º toque liga/desliga o defeito — ⛔ o mesmo defeito nunca entra duas
// vezes (o banco tem uq_recolhida_defeito_tipo).
function alternarTipo(tipo) {
    if (enviando) return;
    const existente = carrinho.find((i) => i.codigo === tipo.codigo);
    if (existente) {
        carrinho = carrinho.filter((i) => i !== existente);
    } else {
        carrinho.push({ codigo: tipo.codigo, nome: tipo.nome, categoria: tipo.categoria, observacao: '' });
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
    document.querySelectorAll('#rec-tipos .recolhida-chip').forEach((b) => {
        b.classList.toggle('active', carrinho.some((i) => i.codigo === b.dataset.tipo));
    });
}

// Contador no chip de CATEGORIA (ex.: "Elétrica · 2") — mesmas classes da avaria.
function atualizarContadores() {
    document.querySelectorAll('#rec-categorias .recolhida-chip').forEach((btn) => {
        const n = carrinho.filter((i) => i.categoria === btn.dataset.categoria).length;
        btn.textContent = btn.dataset.rotulo;
        if (n > 0) {
            const contador = document.createElement('span');
            contador.className = 'avaria-chip-contador';
            contador.textContent = ` · ${n}`;
            btn.appendChild(contador);
        }
        btn.classList.toggle('avaria-chip-com-itens', n > 0);
    });
}

function renderCarrinho() {
    const el = document.getElementById('rec-carrinho');
    el.innerHTML = '';
    if (carrinho.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Nenhum defeito escolhido ainda.</div>';
        return;
    }
    for (const item of carrinho) {
        const div = document.createElement('div');
        div.className = 'avaria-carrinho-item';

        const cabeca = document.createElement('div');
        cabeca.className = 'avaria-carrinho-cabeca';
        const textos = document.createElement('div');
        textos.className = 'avaria-carrinho-textos';
        const titulo = document.createElement('div');
        titulo.className = 'avaria-carrinho-titulo';
        titulo.textContent = `${_labelCategoria(item.categoria)} — ${item.nome}`;
        textos.appendChild(titulo);
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
        obs.placeholder = item.categoria === 'outros' ? 'Descreva o defeito' : 'Observação (opcional)';
        obs.value = item.observacao;
        obs.readOnly = enviando;
        obs.addEventListener('input', () => { item.observacao = obs.value; });
        div.appendChild(obs);

        el.appendChild(div);
    }
}

// Motivo DEFEITO: FINALIZAR (N), travado com carrinho vazio. Outros motivos
// não têm defeito: FINALIZAR liberado.
function atualizarBotao() {
    if (enviando) return;
    const btn = document.getElementById('btn-registrar-recolhida');
    if (motivoSelecionado === 'DEFEITO') {
        btn.textContent = `FINALIZAR (${carrinho.length})`;
        btn.disabled = carrinho.length === 0;
    } else {
        btn.textContent = 'FINALIZAR';
        btn.disabled = false;
    }
}

function travarChips(travar) {
    document.querySelectorAll('#rec-motivos button, #rec-categorias button, #rec-tipos button')
        .forEach((b) => { b.disabled = travar; });
}

function limparResultado() {
    const el = document.getElementById('rec-resultado');
    el.textContent = '';
    el.style.display = 'none';
}

function mostrarResultado(msg) {
    const el = document.getElementById('rec-resultado');
    el.textContent = msg;
    el.style.display = 'block';
}

async function carregarCatalogos() {
    try {
        [tiposDefeitoCache, linhasCache] = await Promise.all([
            apiGet('/tipos-defeito'),
            apiGet('/linhas'),
        ]);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-recolhida] erro ao carregar catálogos:', err);
        tiposDefeitoCache = tiposDefeitoCache || [];
        linhasCache = linhasCache || [];
    }
    renderCategorias();
    renderTipos();
    renderCarrinho();
}

// ─── Registrar ───────────────────────────────────────────────────────────
function initRegistrar() {
    document.getElementById('btn-registrar-recolhida').addEventListener('click', registrar);
}

async function registrar() {
    if (enviando) return;
    const erro = document.getElementById('rec-erro');
    erro.style.display = 'none';
    limparResultado();

    const prefixo = document.getElementById('rec-prefixo').value.trim();
    if (!prefixo) {
        erro.textContent = 'Digite o carro.';
        erro.style.display = 'block';
        return;
    }
    const ehDefeito = motivoSelecionado === 'DEFEITO';
    if (ehDefeito && carrinho.length === 0) {
        erro.textContent = 'Escolha ao menos um defeito.';
        erro.style.display = 'block';
        return;
    }

    const btn = document.getElementById('btn-registrar-recolhida');
    const n = carrinho.length;
    enviando = true;
    btn.disabled = true;
    btn.textContent = 'Gravando…';
    travarChips(true);
    renderCarrinho();
    try {
        // UM POST só com a lista — 1 recolhida, N defeitos (migration 052).
        await apiPost('/portaria/recolhidas', {
            prefixo,
            linha_codigo: linhaSelecionada ? linhaSelecionada.codigo : null,
            motivo: motivoSelecionado,
            defeitos: ehDefeito
                ? carrinho.map((i) => ({ tipo_defeito_codigo: i.codigo, observacao: i.observacao.trim() || null }))
                : [],
            relato: document.getElementById('rec-relato').value.trim() || null,
            motorista_re: document.getElementById('rec-motorista-re').value.trim() || null,
            motorista_nome: document.getElementById('rec-motorista-nome').value.trim() || null,
            cobrador_re: document.getElementById('rec-cobrador-re').value.trim() || null,
            cobrador_nome: document.getElementById('rec-cobrador-nome').value.trim() || null,
        });
        enviando = false;
        travarChips(false);
        limparFormulario();
        mostrarResultado(ehDefeito
            ? `Recolhida do carro ${prefixo} registrada com ${n} ${n === 1 ? 'defeito' : 'defeitos'}.`
            : `Recolhida do carro ${prefixo} registrada.`);
        await carregarUltimas();
    } catch (err) {
        enviando = false;
        travarChips(false);
        renderCarrinho();
        if (err instanceof ApiError && err.status === 401) return;
        // ⛔ Nada sai do carrinho: tudo continua na tela pra TENTAR DE NOVO.
        erro.textContent = `Não gravou: ${err.message}`;
        erro.style.display = 'block';
        atualizarBotao();
        if (ehDefeito) btn.textContent = `TENTAR DE NOVO (${n})`;
    } finally {
        enviando = false;
    }
}

function limparFormulario() {
    document.getElementById('rec-prefixo').value = '';
    document.getElementById('rec-prefixo-status').textContent = '';
    document.getElementById('rec-linha-busca').value = '';
    document.getElementById('rec-linha-sugestoes').innerHTML = '';
    linhaSelecionada = null;
    atualizarLinhaSelecionada();
    document.getElementById('rec-motorista-re').value = '';
    document.getElementById('rec-motorista-status').textContent = '';
    document.getElementById('rec-motorista-nome').value = '';
    document.getElementById('rec-motorista-nome').style.display = 'none';
    document.getElementById('rec-cobrador-re').value = '';
    document.getElementById('rec-cobrador-status').textContent = '';
    document.getElementById('rec-cobrador-nome').value = '';
    document.getElementById('rec-cobrador-nome').style.display = 'none';
    motivoSelecionado = 'DEFEITO';
    renderMotivos();
    document.querySelectorAll('#rec-categorias .recolhida-chip').forEach((b) => b.classList.remove('active'));
    document.getElementById('rec-tipos').innerHTML = '';
    categoriaAtual = null;
    carrinho = [];
    atualizarDefeitoSelecionado();
    atualizarCarrinho();
    document.getElementById('rec-relato').value = '';
    document.getElementById('rec-prefixo').focus();
}

// ─── Últimas recolhidas do próprio turno — só conferência, ⛔ sem motorista ──
function _statusTexto(r) {
    if (r.status === 'AGUARDANDO') return 'Aguardando avaliação';
    if (r.status === 'AVALIADA') {
        return r.avaliacao === 'LIBERADO' ? `Liberado (${r.prazo_minutos} min)` : 'Retido';
    }
    return 'Descartada';
}

async function carregarUltimas() {
    const el = document.getElementById('rec-lista-turno');
    const user = getCurrentUser();
    const meuId = user ? (user.funcionario_id || user.id) : null;
    try {
        const todas = await apiGet('/portaria/recolhidas');
        const minhas = todas.filter((r) => r.registrado_por === meuId).slice(0, 8);
        if (minhas.length === 0) {
            el.innerHTML = '<div class="oc-vazio">Nenhuma recolhida registrada ainda.</div>';
            return;
        }
        el.innerHTML = '';
        for (const r of minhas) {
            const div = document.createElement('div');
            div.className = 'portaria-item';
            div.style.marginBottom = '8px';
            div.style.cursor = 'default';
            // Migration 052: todos os defeitos; recolhida antiga cai no campo único.
            const codigos = (r.defeitos && r.defeitos.length)
                ? r.defeitos.map((d) => d.tipo_defeito_codigo)
                : [r.tipo_defeito_codigo].filter(Boolean);
            const defeitosTxt = codigos.map(_nomeTipo).join(', ');
            const sub = [r.motivo, defeitosTxt, r.linha_codigo].filter(Boolean).join(' · ');
            div.innerHTML = `
                <div>
                    <div class="portaria-item-placa">${escapeHtml(r.prefixo)}</div>
                    <div class="portaria-item-sub">${escapeHtml(sub)}</div>
                </div>
                <div class="portaria-item-hora">${escapeHtml(_statusTexto(r))}</div>
            `;
            el.appendChild(div);
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        el.innerHTML = `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

// Aviso ao sair com defeito escolhido e não gravado (mesmo da avaria).
function initAvisoSaida() {
    window.addEventListener('beforeunload', (e) => {
        if (carrinho.length === 0) return;
        e.preventDefault();
        e.returnValue = '';
    });
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
initHeader();
initAvisoSaida();
initPrefixo();
initIdentificacao();
initLinha();
renderMotivos();
initRegistrar();
carregarCatalogos();
carregarUltimas();
