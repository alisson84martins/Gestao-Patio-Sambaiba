/*
 * manutencao.avarias.js — aba "Avarias" dentro de manutencao.html
 * -----------------------------------------------------------------
 * Fase 2 (item [4], 17/09/2026): fila de avarias ABERTAS pra funilaria dar
 * baixa. Sem isso nenhuma avaria fecha — o carro vai pra funilaria, volta
 * consertado, a avaria continua ABERTA, e o próximo motorista que ralar o
 * mesmo curvão clica "já estava" e fica protegido por um dano que ele
 * mesmo causou. Ver _handoff-claude/PROMPT-avaria-mapa-visual-2026-09-15.md,
 * Fase 2 ("A baixa da funilaria").
 *
 * POST /portaria/avarias/{id}/encerrar JÁ existia (Fase 1, exige
 * `manutencao` escrever — quem conserta não é quem confere a saída na
 * guarita). O que NÃO existia: como a manutenção LÊ a fila. GET
 * /avarias/catalogo e /avarias/historico agora aceitam acesso_veicular OU
 * manutencao (LeituraAcessoOuManutencao/exige_qualquer, backend) — sem
 * isso o MECANICO (sem acesso_veicular desde a migration 037) não
 * conseguia listar nada aqui. Nenhuma migration: só RBAC de leitura,
 * mesmo padrão já usado por GET /portaria/recolhidas
 * (LeituraRecolhidaOuTratativa).
 *
 * Agrupamento por carro — mesmo critério do item [2] desta sessão
 * (portaria-avaria.page.js): um cartão .oc-card por prefixo, mais recente
 * primeiro, sempre expandido.
 */

import { requireAuth } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { podeEscrever } from './sessao.js';
import { escapeHtml } from './escape.js';
import { POLLING_INTERVAL_MS } from './config.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da aba Avarias');
}

let catalogo = { zonas: [], tipos: [] };
let pollHandle = null;

function _dataBr(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleDateString('pt-BR');
    } catch {
        return iso;
    }
}

function _diasAberta(primeiraVezEm) {
    const inicio = new Date(primeiraVezEm);
    if (Number.isNaN(inicio.getTime())) return null;
    return Math.max(0, Math.floor((Date.now() - inicio.getTime()) / 86_400_000));
}

// ─── Agrupamento por carro — mesmo critério do item [2] ─────────────────
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

function _mostrarErro(msg) {
    const el = document.getElementById('manut-av-erro');
    el.textContent = msg;
    el.style.display = 'block';
}

function _limparErro() {
    document.getElementById('manut-av-erro').style.display = 'none';
}

// ─── Dar baixa — POST /avarias/{id}/encerrar (Fase 1, EscritaManutencao) ─
async function encerrar(avariaId, encerramento, btnsDoItem) {
    _limparErro();
    btnsDoItem.forEach((b) => { b.disabled = true; });
    try {
        await apiPost(`/portaria/avarias/${avariaId}/encerrar`, { encerramento });
        await carregar();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        // 409 = alguém já deu baixa nesta avaria (outro mecânico, outra
        // aba) — trata como conflito normal, recarrega em vez de travar.
        if (err instanceof ApiError && err.status === 409) {
            await carregar();
            return;
        }
        _mostrarErro(err.message);
        btnsDoItem.forEach((b) => { b.disabled = false; });
    }
}

async function encerrarTodasDoCartao(avarias, btnLote) {
    _limparErro();
    btnLote.disabled = true;
    btnLote.textContent = 'Consertando…';
    try {
        for (const item of avarias) {
            await apiPost(`/portaria/avarias/${item.id}/encerrar`, { encerramento: 'REPARADA' });
        }
        await carregar();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        _mostrarErro(err.message);
        await carregar();
    }
}

// ─── Renderização ────────────────────────────────────────────────────────
function _cartaoCarro({ prefixo, avarias }) {
    const podeDarBaixa = podeEscrever('manutencao');
    const card = document.createElement('div');
    card.className = 'oc-card';

    const header = document.createElement('div');
    header.className = 'oc-card-header';
    header.innerHTML = `
        <div class="oc-card-titulo">${escapeHtml(prefixo)}</div>
        <div class="portaria-item-hora">${avarias.length} aberta${avarias.length === 1 ? '' : 's'}</div>
    `;
    card.appendChild(header);

    for (const item of avarias) {
        card.appendChild(_linhaAvaria(item, podeDarBaixa));
    }

    // Baixa em lote — "o carro todo voltou consertado da funilaria" é o
    // caso comum; NÃO EXISTE fica só por avaria (motivo pode variar entre
    // elas, não faz sentido em lote).
    if (podeDarBaixa && avarias.length > 1) {
        const btnLote = document.createElement('button');
        btnLote.type = 'button';
        btnLote.className = 'btn btn-ghost btn-full';
        btnLote.style.marginTop = '4px';
        btnLote.textContent = `Consertadas — dar baixa nas ${avarias.length} do carro`;
        btnLote.addEventListener('click', () => encerrarTodasDoCartao(avarias, btnLote));
        card.appendChild(btnLote);
    }

    return card;
}

function _linhaAvaria(item, podeDarBaixa) {
    const zona = catalogo.zonas.find((z) => z.codigo === item.zona_codigo);
    const tipo = catalogo.tipos.find((t) => t.codigo === item.tipo_codigo);
    const dias = _diasAberta(item.primeira_vez_em);

    const linha = document.createElement('div');
    linha.className = 'portaria-item';
    linha.style.marginBottom = '8px';
    linha.style.cursor = 'default';
    linha.style.flexWrap = 'wrap';

    const info = document.createElement('div');
    info.innerHTML = `
        <div class="portaria-item-placa">${escapeHtml(zona ? zona.nome : item.zona_codigo)} — ${escapeHtml(tipo ? tipo.nome : item.tipo_codigo)}</div>
        <div class="portaria-item-sub">Marcada em ${_dataBr(item.primeira_vez_em)}${dias != null ? ` · aberta há ${dias} dia${dias === 1 ? '' : 's'}` : ''} · vista ${item.vezes_vista}x</div>
    `;
    linha.appendChild(info);

    if (podeDarBaixa) {
        const acoes = document.createElement('div');
        acoes.style.display = 'flex';
        acoes.style.gap = '6px';

        const btnConsertada = document.createElement('button');
        btnConsertada.type = 'button';
        btnConsertada.className = 'btn btn-primary';
        btnConsertada.textContent = 'Consertada';

        const btnNaoExiste = document.createElement('button');
        btnNaoExiste.type = 'button';
        btnNaoExiste.className = 'btn btn-ghost';
        btnNaoExiste.textContent = 'Não existe';

        const btns = [btnConsertada, btnNaoExiste];
        btnConsertada.addEventListener('click', (e) => { e.stopPropagation(); encerrar(item.id, 'REPARADA', btns); });
        btnNaoExiste.addEventListener('click', (e) => { e.stopPropagation(); encerrar(item.id, 'NAO_EXISTIA', btns); });

        acoes.appendChild(btnConsertada);
        acoes.appendChild(btnNaoExiste);
        linha.appendChild(acoes);
    }

    return linha;
}

function render(itens) {
    const el = document.getElementById('manut-av-lista');
    document.getElementById('manut-av-contador-numero').textContent = String(itens.length);
    atualizarLabelAba(itens.length);
    if (itens.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Nenhuma avaria aberta.</div>';
        return;
    }
    el.innerHTML = '';
    for (const carro of _agruparPorCarro(itens)) {
        el.appendChild(_cartaoCarro(carro));
    }
}

function atualizarLabelAba(total) {
    const botao = document.getElementById('tab-btn-avarias');
    if (!botao) return;
    botao.textContent = total > 0 ? `Avarias (${total})` : 'Avarias';
}

// ─── Carga ────────────────────────────────────────────────────────────────
async function carregar() {
    try {
        const itens = await apiGet('/portaria/avarias/historico?status=ABERTA');
        render(itens);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[manutencao.avarias] erro ao carregar fila:', err);
        document.getElementById('manut-av-lista').innerHTML =
            `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

function startPolling() {
    if (pollHandle) return;
    pollHandle = setInterval(() => {
        if (!document.getElementById('tab-avarias')?.classList.contains('active')) return;
        carregar();
    }, POLLING_INTERVAL_MS);
}

// ─── Bootstrap — sem auto-guarda de recurso: quem abre manutencao.html já ─
// tem `manutencao` (podeLer, checado no topo de manutencao.js), que é uma
// das duas pontas do exige_qualquer que os GETs desta aba exigem.
async function init() {
    const botaoAba = document.getElementById('tab-btn-avarias');
    if (botaoAba) botaoAba.addEventListener('click', carregar);

    try {
        catalogo = await apiGet('/portaria/avarias/catalogo');
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        catalogo = { zonas: [], tipos: [] };
    }

    await carregar();
    startPolling();
}

init();
