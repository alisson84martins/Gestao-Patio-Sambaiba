/*
 * manutencao.recolhidas.js — aba "RA" dentro de manutencao.html
 * ----------------------------------------------------------------
 * Antes era página própria (manutencao-recolhidas.page.js / manutencao-
 * recolhidas.html) — virou aba da Manutenção (prompt de 23/08, Fase 4).
 * A aba só aparece pra quem tem leitura em `recolhida_anormal`; quem não
 * tem, nunca vê o botão nem carrega nada daqui (auto-guarda no init()).
 *
 * Duas listas em "Em aberto": AGUARDANDO (falta avaliar) e AVALIADA (falta
 * encerrar) — o backend já devolve as duas juntas em /recolhidas/pendentes
 * (migration 032 ampliou o filtro, ver routers/portaria_recolhidas.py).
 * "Encerradas hoje" é uma leitura à parte, só do dia, pra conferência.
 *
 * Fechamento do ciclo (novo nesta fase): tocar num item AVALIADA abre o
 * encerramento — Sem defeito / Serviço feito, com o que a avaliação disse
 * visível, pro mecânico não encerrar às cegas.
 */

import { requireAuth } from './auth.js';
import { apiGet, apiPatch, ApiError } from './api.js';
import { podeLer } from './sessao.js';
import { escapeHtml } from './escape.js';
import { POLLING_INTERVAL_MS } from './config.js';

// Bloco G: rótulos do motivo — só DEFEITO tem tipo_defeito_codigo.
const MOTIVO_LABEL = {
    DEFEITO: 'Defeito',
    COLISAO: 'Colisão',
    FALTA_MOTORISTA: 'Falta motorista',
    FALTA_COBRADOR: 'Falta cobrador',
    OUTRO: 'Outro',
};

// Migration 032 — rótulos em português corrido na tela, nunca os códigos.
const DESFECHO_LABEL = {
    SEM_DEFEITO: 'Sem defeito',
    SERVICO_EXECUTADO: 'Serviço feito',
};

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da aba RA');
}

let pollHandle = null;
let recolhidaAvaliando = null;    // RecolhidaRead aberta no modal de avaliação
let avaliacaoEscolhida = null;    // 'LIBERADO' | 'RETIDO'
let prazoMinutos = null;
let recolhidaEncerrando = null;   // RecolhidaRead aberta no modal de encerramento

function abrirModal(id) { document.getElementById(id).classList.add('open'); }
function fecharModal(id) { document.getElementById(id).classList.remove('open'); }

function fmtHora(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// ⚠️ NÃO usar toISOString().slice(0,10) — vira UTC e desalinha de
// data_referencia (que o backend grava em FUSO_OPERACAO/America-São_Paulo,
// ver app/core/config.py). Confirmado rodando esta aba de noite: às 22h
// local o dia em UTC já virou amanhã, e "Encerradas hoje" ficava vazio na
// hora em que a fila mais fecha recolhida. getFullYear/getMonth/getDate
// leem a hora LOCAL do navegador — o mesmo horário que toLocaleString
// já usa em todo o resto da tela (fmtHora acima).
function hojeISO() {
    const d = new Date();
    const ano = d.getFullYear();
    const mes = String(d.getMonth() + 1).padStart(2, '0');
    const dia = String(d.getDate()).padStart(2, '0');
    return `${ano}-${mes}-${dia}`;
}

// ─── Em aberto (AGUARDANDO + AVALIADA) + contador ───────────────────────
async function carregarAbertas() {
    try {
        const [contagem, abertas] = await Promise.all([
            apiGet('/portaria/recolhidas/contagem-pendentes'),
            apiGet('/portaria/recolhidas/pendentes'),
        ]);
        document.getElementById('rec-contador-numero').textContent = String(contagem.total);
        renderAbertas(abertas);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[manutencao.recolhidas] erro ao carregar em aberto:', err);
    }
}

function renderAbertas(itens) {
    const el = document.getElementById('rec-lista-abertas');
    if (itens.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Nenhuma recolhida em aberto.</div>';
        return;
    }
    el.innerHTML = '';
    for (const r of itens) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'portaria-item';
        btn.style.marginBottom = '8px';
        const sub = [MOTIVO_LABEL[r.motivo] || r.motivo, r.tipo_defeito_codigo, r.linha_codigo]
            .filter(Boolean).join(' · ');
        const situacao = r.status === 'AGUARDANDO' ? 'Falta avaliar' : 'Falta encerrar';
        btn.innerHTML = `
            <div>
                <div class="portaria-item-placa">${escapeHtml(r.prefixo)}</div>
                <div class="portaria-item-sub">${escapeHtml(sub)}${r.relato ? ' — ' + escapeHtml(r.relato) : ''}</div>
                <div class="portaria-item-sub" style="opacity:0.7">${situacao}</div>
            </div>
            <div class="portaria-item-hora">${fmtHora(r.momento)}</div>
        `;
        btn.addEventListener('click', () => {
            if (r.status === 'AGUARDANDO') abrirAvaliacao(r);
            else abrirEncerramento(r);
        });
        el.appendChild(btn);
    }
}

// ─── Encerradas hoje — só conferência, sem clique ───────────────────────
async function carregarEncerradas() {
    try {
        const encerradas = await apiGet(`/portaria/recolhidas?status=ENCERRADA&data=${hojeISO()}`);
        renderEncerradas(encerradas);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[manutencao.recolhidas] erro ao carregar encerradas:', err);
    }
}

function renderEncerradas(itens) {
    const el = document.getElementById('rec-lista-encerradas');
    if (itens.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Nenhuma recolhida encerrada hoje.</div>';
        return;
    }
    el.innerHTML = itens.map((r) => {
        const sub = [MOTIVO_LABEL[r.motivo] || r.motivo, r.tipo_defeito_codigo, r.linha_codigo]
            .filter(Boolean).join(' · ');
        const desfecho = DESFECHO_LABEL[r.desfecho] || r.desfecho || '—';
        return `
            <div class="portaria-item" style="margin-bottom:8px;cursor:default">
                <div>
                    <div class="portaria-item-placa">${escapeHtml(r.prefixo)}</div>
                    <div class="portaria-item-sub">${escapeHtml(sub)}</div>
                    <div class="portaria-item-sub" style="opacity:0.7">${escapeHtml(desfecho)}${r.encerramento_relato ? ' — ' + escapeHtml(r.encerramento_relato) : ''}</div>
                </div>
                <div class="portaria-item-hora">${fmtHora(r.encerrado_em)}</div>
            </div>
        `;
    }).join('');
}

function carregarTudo() {
    return Promise.all([carregarAbertas(), carregarEncerradas()]);
}

// ─── Avaliação (status AGUARDANDO) — mesmo fluxo de antes ───────────────
function abrirAvaliacao(recolhida) {
    recolhidaAvaliando = recolhida;
    avaliacaoEscolhida = null;
    prazoMinutos = null;

    document.getElementById('aval-prefixo').textContent = recolhida.prefixo;
    document.getElementById('aval-linha').textContent = recolhida.linha_codigo ? `Linha ${recolhida.linha_codigo}` : '';
    document.getElementById('aval-defeito').textContent = recolhida.tipo_defeito_codigo
        ? `${MOTIVO_LABEL[recolhida.motivo] || recolhida.motivo} — ${recolhida.tipo_defeito_codigo}`
        : (MOTIVO_LABEL[recolhida.motivo] || recolhida.motivo);
    document.getElementById('aval-relato').textContent = recolhida.relato || '';
    document.getElementById('aval-ficha').textContent = recolhida.ficha_id
        ? 'Ficha de manutenção aberta.'
        : (recolhida.ficha_falhou_motivo || 'Ficha não foi aberta automaticamente.');

    document.getElementById('aval-prazo-wrap').style.display = 'none';
    document.getElementById('aval-prazo-input').style.display = 'none';
    document.getElementById('aval-prazo-input').value = '';
    document.querySelectorAll('#aval-prazo-atalhos .recolhida-chip').forEach((b) => b.classList.remove('active'));
    document.getElementById('aval-observacao').value = '';
    document.getElementById('aval-erro').style.display = 'none';
    document.getElementById('btn-confirmar-avaliacao').style.display = 'none';

    abrirModal('modal-avaliacao');
}

function initAvaliacao() {
    document.getElementById('fechar-avaliacao').addEventListener('click', () => fecharModal('modal-avaliacao'));
    document.getElementById('btn-cancelar-avaliacao').addEventListener('click', () => fecharModal('modal-avaliacao'));

    document.getElementById('btn-liberado').addEventListener('click', () => {
        avaliacaoEscolhida = 'LIBERADO';
        document.getElementById('aval-prazo-wrap').style.display = 'block';
        document.getElementById('btn-confirmar-avaliacao').style.display = prazoMinutos ? 'block' : 'none';
    });
    document.getElementById('btn-retido').addEventListener('click', () => {
        avaliacaoEscolhida = 'RETIDO';
        document.getElementById('aval-prazo-wrap').style.display = 'none';
        document.getElementById('btn-confirmar-avaliacao').style.display = 'block';
    });

    document.getElementById('aval-prazo-atalhos').addEventListener('click', (e) => {
        const btn = e.target.closest('.recolhida-chip');
        if (!btn) return;
        document.querySelectorAll('#aval-prazo-atalhos .recolhida-chip').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const inputMin = document.getElementById('aval-prazo-input');
        if (btn.dataset.min === 'outro') {
            inputMin.style.display = 'block';
            inputMin.focus();
            prazoMinutos = null;
            document.getElementById('btn-confirmar-avaliacao').style.display = 'none';
        } else {
            inputMin.style.display = 'none';
            prazoMinutos = Number(btn.dataset.min);
            document.getElementById('btn-confirmar-avaliacao').style.display = 'block';
        }
    });

    document.getElementById('aval-prazo-input').addEventListener('input', (e) => {
        const valor = Number(e.target.value.trim());
        prazoMinutos = valor > 0 ? valor : null;
        document.getElementById('btn-confirmar-avaliacao').style.display = prazoMinutos ? 'block' : 'none';
    });

    document.getElementById('btn-confirmar-avaliacao').addEventListener('click', confirmarAvaliacao);
}

async function confirmarAvaliacao() {
    if (!recolhidaAvaliando || !avaliacaoEscolhida) return;
    const erro = document.getElementById('aval-erro');
    erro.style.display = 'none';

    const payload = {
        avaliacao: avaliacaoEscolhida,
        prazo_minutos: avaliacaoEscolhida === 'LIBERADO' ? prazoMinutos : null,
        avaliacao_relato: document.getElementById('aval-observacao').value.trim() || null,
    };

    const btn = document.getElementById('btn-confirmar-avaliacao');
    btn.disabled = true;
    try {
        await apiPatch(`/portaria/recolhidas/${recolhidaAvaliando.id}/avaliacao`, payload);
        fecharModal('modal-avaliacao');
        await carregarAbertas();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Encerramento (status AVALIADA) — fechamento do ciclo, migration 032 ─
function abrirEncerramento(recolhida) {
    recolhidaEncerrando = recolhida;

    document.getElementById('enc-prefixo').textContent = recolhida.prefixo;
    document.getElementById('enc-linha').textContent = recolhida.linha_codigo ? `Linha ${recolhida.linha_codigo}` : '';
    document.getElementById('enc-defeito').textContent = recolhida.tipo_defeito_codigo
        ? `${MOTIVO_LABEL[recolhida.motivo] || recolhida.motivo} — ${recolhida.tipo_defeito_codigo}`
        : (MOTIVO_LABEL[recolhida.motivo] || recolhida.motivo);

    // O que a avaliação disse — visível pro mecânico não encerrar às cegas.
    const avaliacaoTexto = recolhida.avaliacao === 'LIBERADO'
        ? `Avaliação: LIBERADO${recolhida.prazo_minutos != null ? ` — prazo ${recolhida.prazo_minutos} min` : ''}`
        : recolhida.avaliacao === 'RETIDO' ? 'Avaliação: RETIDO' : '';
    document.getElementById('enc-avaliacao').textContent =
        [avaliacaoTexto, recolhida.avaliacao_relato].filter(Boolean).join(' — ');

    document.getElementById('enc-relato').value = '';
    document.getElementById('enc-erro').style.display = 'none';

    abrirModal('modal-encerramento');
}

function initEncerramento() {
    document.getElementById('fechar-encerramento').addEventListener('click', () => fecharModal('modal-encerramento'));
    document.getElementById('btn-cancelar-encerramento').addEventListener('click', () => fecharModal('modal-encerramento'));
    document.getElementById('btn-sem-defeito').addEventListener('click', () => confirmarEncerramento('SEM_DEFEITO'));
    document.getElementById('btn-servico-feito').addEventListener('click', () => confirmarEncerramento('SERVICO_EXECUTADO'));
}

async function confirmarEncerramento(desfecho) {
    if (!recolhidaEncerrando) return;
    const erro = document.getElementById('enc-erro');
    erro.style.display = 'none';

    const payload = {
        desfecho,
        encerramento_relato: document.getElementById('enc-relato').value.trim() || null,
    };

    const botoes = [document.getElementById('btn-sem-defeito'), document.getElementById('btn-servico-feito')];
    botoes.forEach((b) => { b.disabled = true; });
    try {
        await apiPatch(`/portaria/recolhidas/${recolhidaEncerrando.id}/encerramento`, payload);
        fecharModal('modal-encerramento');
        await carregarTudo();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        botoes.forEach((b) => { b.disabled = false; });
    }
}

// ─── Visão gerencial (§2.5, §2.7 do prompt original) ────────────────────
async function carregarGerencial() {
    if (!podeLer('recolhida_gerencial')) return;
    document.getElementById('secao-gerencial').style.display = 'block';
    try {
        const analise = await apiGet('/portaria/recolhidas/analise');
        renderAgregados(analise);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('gerencial-agregados').innerHTML =
            `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar análise: ${escapeHtml(err.message)}</div>`;
    }
}

function _tabela(titulo, itens) {
    if (!itens || itens.length === 0) {
        return `<div class="portaria-secao-titulo" style="margin-top:14px">${escapeHtml(titulo)}</div><div class="oc-vazio">Sem dados no período.</div>`;
    }
    const linhas = itens.map((i) => `
        <tr><td>${escapeHtml(i.chave)}</td><td>${i.total}</td></tr>
    `).join('');
    return `
        <div class="portaria-secao-titulo" style="margin-top:14px">${escapeHtml(titulo)}</div>
        <div class="portaria-tabela-wrap">
            <table class="portaria-tabela">
                <thead><tr><th>Chave</th><th>Total</th></tr></thead>
                <tbody>${linhas}</tbody>
            </table>
        </div>
    `;
}

function renderAgregados(analise) {
    const el = document.getElementById('gerencial-agregados');
    const tempo = analise.tempo_medio_avaliacao_minutos != null
        ? `<p class="portaria-ficha-linha">Tempo médio até a avaliação: <strong>${analise.tempo_medio_avaliacao_minutos} min</strong></p>`
        : '';
    el.innerHTML = tempo
        + _tabela('Por motivo', analise.por_motivo)
        + _tabela('Por prefixo', analise.por_prefixo)
        + _tabela('Por linha', analise.por_linha)
        + _tabela('Por motorista', analise.por_motorista)
        + _tabela('Por tipo de defeito', analise.por_tipo_defeito)
        + _tabela('Por faixa de horário', analise.por_faixa_horario);
}

// ─── Polling — só enquanto a aba RA está visível e sem modal aberto ─────
function startPolling() {
    if (pollHandle) return;
    pollHandle = setInterval(() => {
        if (document.querySelector('.modal-overlay.open')) return;
        if (!document.getElementById('tab-ra')?.classList.contains('active')) return;
        carregarTudo();
    }, POLLING_INTERVAL_MS);
}

// ─── Bootstrap — auto-guarda: quem não lê recolhida_anormal nem vê a aba ─
function init() {
    if (!podeLer('recolhida_anormal')) return;

    const botaoAba = document.getElementById('tab-btn-ra');
    if (botaoAba) {
        botaoAba.style.display = '';
        botaoAba.addEventListener('click', carregarTudo);
    }

    initAvaliacao();
    initEncerramento();
    carregarTudo();
    carregarGerencial();
    startPolling();
}

init();
