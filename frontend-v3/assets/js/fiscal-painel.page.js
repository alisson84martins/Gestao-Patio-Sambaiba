/*
 * fiscal-painel.page.js — Painel do coordenador (Bloco E do módulo Fiscalização)
 * -------------------------------------------------------------------------------
 * Ordem na tela, de cima para baixo — o que decide primeiro (§6 do prompt):
 *   1. A bacia hoje: ICV ponderado (D22), meta ao lado (D29)
 *   2. Cascata agora (D24) — vazio = nada na tela
 *   3. Ranking por perda absoluta (D23), com divergência de denominador (D28)
 *   4. A linha aberta (clique numa linha do ranking): horários do dia, quatro
 *      estados (D12), reaproveita GET /fiscalizacao/painel/{linha} do Bloco B
 *   5. Ações da coordenação (D26): registrar e listar
 *   6. Motivos livres mais frequentes (D27)
 * + importação da planilha de ICV (D20/D25/D28, §5) — sem seção numerada no
 *   prompt, mas é o jeito de o coordenador colocar dado na tela.
 *
 * Polling no padrão do Pátio (config.js::POLLING_INTERVAL_MS). Molde:
 * portaria-consulta.page.js + patio.page.js.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { API_BASE_URL, TOKEN_KEY, POLLING_INTERVAL_MS } from './config.js';
import { podeEscrever } from './sessao.js';
import { escapeHtml } from './escape.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

let pollHandle = null;
let linhaAberta = null;

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

// ─── Helpers ────────────────────────────────────────────────────────────
function hojeISO() {
    return new Date().toISOString().slice(0, 10);
}

function dataSelecionada() {
    return document.getElementById('fp-data').value || hojeISO();
}

function baciaSelecionada() {
    return document.getElementById('fp-bacia').value || null;
}

function fmtPercentual(v) {
    return (v === null || v === undefined) ? '—' : `${Number(v).toFixed(2)}%`;
}

function fmtHora(hhmmss) {
    return hhmmss ? hhmmss.slice(0, 5) : '—';
}

function exibirErro(msg) {
    const el = document.getElementById('fp-erro');
    el.textContent = msg;
    el.style.display = 'block';
}

function ignoravel(err) {
    return err instanceof ApiError && err.status === 401;
}

// ─── 0. Catálogo de bacias ──────────────────────────────────────────────
async function carregarBacias() {
    const select = document.getElementById('fp-bacia');
    try {
        const bacias = await apiGet('/fiscalizacao/bacias');
        if (bacias.length === 0) {
            select.innerHTML = '<option value="">Nenhuma bacia cadastrada</option>';
            return;
        }
        select.innerHTML = bacias.map(b => `<option value="${escapeHtml(b.codigo)}">${escapeHtml(b.nome)}</option>`).join('');
    } catch (err) {
        if (ignoravel(err)) return;
        exibirErro('Erro ao carregar bacias: ' + err.message);
    }
}

// ─── 1. A bacia hoje (D22, D29) ─────────────────────────────────────────
async function carregarBaciaResumo() {
    const bacia = baciaSelecionada();
    const cardIcv = document.getElementById('fp-icv-ponderado').closest('.stat-card');
    if (!bacia) {
        document.getElementById('fp-icv-ponderado').textContent = '—';
        document.getElementById('fp-meta').textContent = '—';
        document.getElementById('fp-totais').textContent = '—';
        cardIcv.classList.remove('stat-card-warn');
        return;
    }
    try {
        const dados = await apiGet(`/fiscalizacao/icv/bacia/${encodeURIComponent(bacia)}?data=${dataSelecionada()}`);
        document.getElementById('fp-icv-ponderado').textContent = fmtPercentual(dados.icv_ponderado);
        document.getElementById('fp-meta').textContent = `${Number(dados.meta_icv).toFixed(2)}%`;
        document.getElementById('fp-totais').textContent = `${dados.realizadas} / ${dados.programadas}`;
        // Comparação sempre contra a meta (D29) — o corte de cor da planilha
        // (~94,3%) não é a meta e não entra aqui.
        const abaixoDaMeta = dados.icv_ponderado !== null && dados.icv_ponderado < dados.meta_icv;
        cardIcv.classList.toggle('stat-card-warn', abaixoDaMeta);
    } catch (err) {
        if (ignoravel(err)) return;
        exibirErro('Erro ao carregar a bacia: ' + err.message);
    }
}

// ─── 2. Cascata agora (D24) ─────────────────────────────────────────────
async function carregarCascata() {
    const secao = document.getElementById('fp-secao-cascata');
    const lista = document.getElementById('fp-cascata-lista');
    try {
        const params = new URLSearchParams({ data: dataSelecionada() });
        const itens = await apiGet(`/fiscalizacao/icv/cascata?${params}`);
        if (itens.length === 0) {
            secao.style.display = 'none';
            lista.innerHTML = '';
            return;
        }
        secao.style.display = '';
        lista.innerHTML = itens.map(i => `
            <div>
                <span class="remanejo-badge badge-atrasado">CASCATA</span>
                Linha ${escapeHtml(i.linha_codigo)} — ${i.quantidade} perdas na faixa das
                ${String(i.faixa_hora).padStart(2, '0')}h
            </div>
        `).join('');
    } catch (err) {
        if (ignoravel(err)) return;
    }
}

// ─── 3. Ranking por perda absoluta (D23, D28) ───────────────────────────
async function carregarRanking() {
    const corpo = document.getElementById('fp-ranking-corpo');
    try {
        const params = new URLSearchParams({ data: dataSelecionada() });
        const bacia = baciaSelecionada();
        if (bacia) params.set('bacia_codigo', bacia);
        const itens = await apiGet(`/fiscalizacao/icv/ranking?${params}`);
        if (itens.length === 0) {
            corpo.innerHTML = '<tr><td colspan="6" class="oc-vazio">Nenhum dado de ICV para esta data.</td></tr>';
            return;
        }
        corpo.innerHTML = itens.map(i => `
            <tr style="cursor:pointer" data-linha="${escapeHtml(i.linha_codigo)}">
                <td>${escapeHtml(i.linha_codigo)}
                    ${i.suspeito ? ' <span class="remanejo-badge badge-urgente" title="Contadores repetidos do dia anterior — conferir (D25)">SUSPEITO</span>' : ''}
                </td>
                <td>${fmtPercentual(i.icv_oficial)}</td>
                <td>${fmtPercentual(i.icv_campo)}</td>
                <td>${i.perda_absoluta ?? '—'}</td>
                <td>${i.fonte_perda_absoluta ?? '—'}</td>
                <td>${i.divergencia_denominador !== null && i.divergencia_denominador !== undefined
                    ? `<span class="remanejo-badge badge-urgente" title="Conferir antes de usar este número em reunião (D28)">⚠️ ${i.divergencia_denominador}</span>`
                    : '—'}
                </td>
            </tr>
        `).join('');
        corpo.querySelectorAll('tr[data-linha]').forEach(tr => {
            tr.addEventListener('click', () => abrirLinha(tr.dataset.linha));
        });
    } catch (err) {
        if (ignoravel(err)) return;
        corpo.innerHTML = '';
        exibirErro('Erro ao carregar o ranking: ' + err.message);
    }
}

// ─── 4. A linha aberta (D12) ────────────────────────────────────────────
const ESTADO_LABEL = { REALIZADA: 'Saiu', PERDIDA: 'Não saiu', ATRASADA: 'Atrasada', AGUARDANDO: 'Aguardando' };
const ESTADO_BADGE = { PERDIDA: 'badge-atrasado', ATRASADA: 'badge-urgente' };

async function abrirLinha(linhaCodigo) {
    linhaAberta = linhaCodigo;
    document.getElementById('fp-secao-linha').style.display = '';
    document.getElementById('fp-linha-codigo').textContent = linhaCodigo;
    document.getElementById('fp-acao-linha').value = linhaCodigo;
    await Promise.all([carregarPainelLinha(), carregarAcoes()]);
}

async function carregarPainelLinha() {
    if (!linhaAberta) return;
    const corpo = document.getElementById('fp-linha-corpo');
    corpo.innerHTML = '<tr><td colspan="6" class="oc-vazio">Carregando…</td></tr>';
    try {
        const params = new URLSearchParams({ data: dataSelecionada() });
        const dados = await apiGet(`/fiscalizacao/painel/${encodeURIComponent(linhaAberta)}?${params}`);
        if (dados.partidas.length === 0) {
            corpo.innerHTML = '<tr><td colspan="6" class="oc-vazio">Sem partidas programadas para esta linha/data.</td></tr>';
            return;
        }
        corpo.innerHTML = dados.partidas.map(p => `
            <tr>
                <td>${fmtHora(p.horario_programado)}</td>
                <td>${p.numero_tabela}</td>
                <td>${escapeHtml(p.terminal)}</td>
                <td><span class="remanejo-badge ${ESTADO_BADGE[p.estado] || ''}">${ESTADO_LABEL[p.estado] || p.estado}</span></td>
                <td>${escapeHtml(p.motivo || '—')}</td>
                <td>${p.recolhida_momento ? `${p.recolhida_avaliacao || '—'} (${p.recolhida_prazo_minutos ?? '—'} min)` : '—'}</td>
            </tr>
        `).join('');
    } catch (err) {
        if (ignoravel(err)) return;
        corpo.innerHTML = '';
        exibirErro('Erro ao carregar a linha: ' + err.message);
    }
}

// ─── 5. Ações da coordenação (D26) ──────────────────────────────────────
async function carregarAcoes() {
    const lista = document.getElementById('fp-acoes-lista');
    if (!linhaAberta) {
        lista.innerHTML = '<div class="oc-vazio">Selecione uma linha no ranking para ver as ações.</div>';
        return;
    }
    try {
        const params = new URLSearchParams({ linha_codigo: linhaAberta });
        const acoes = await apiGet(`/fiscalizacao/acoes?${params}`);
        if (acoes.length === 0) {
            lista.innerHTML = '<div class="oc-vazio">Nenhuma ação registrada para esta linha.</div>';
            return;
        }
        lista.innerHTML = acoes.map(a => `
            <div class="stat-card" style="align-items:flex-start">
                <div class="stat-label">${escapeHtml(a.data_referencia)}${(a.faixa_hora !== null && a.faixa_hora !== undefined) ? ` · ${String(a.faixa_hora).padStart(2, '0')}h` : ''}</div>
                <div>${escapeHtml(a.descricao)}</div>
                ${a.resultado_observado ? `<div style="color:var(--muted);font-size:0.85rem">→ ${escapeHtml(a.resultado_observado)}</div>` : ''}
            </div>
        `).join('');
    } catch (err) {
        if (ignoravel(err)) return;
    }
}

function abrirModalAcao() {
    document.getElementById('fp-acao-linha').value = linhaAberta || '';
    document.getElementById('fp-acao-data').value = dataSelecionada();
    document.getElementById('fp-acao-faixa').value = '';
    document.getElementById('fp-acao-descricao').value = '';
    document.getElementById('fp-acao-resultado').value = '';
    document.getElementById('fp-modal-acao').classList.add('open');
}

function fecharModalAcao() {
    document.getElementById('fp-modal-acao').classList.remove('open');
}

async function salvarAcao() {
    const linha = document.getElementById('fp-acao-linha').value.trim();
    const data = document.getElementById('fp-acao-data').value;
    const faixaRaw = document.getElementById('fp-acao-faixa').value;
    const descricao = document.getElementById('fp-acao-descricao').value.trim();
    const resultadoObs = document.getElementById('fp-acao-resultado').value.trim();
    if (!linha || !data || !descricao) {
        alert('Preencha linha, data e o que foi feito.');
        return;
    }
    try {
        await apiPost('/fiscalizacao/acoes', {
            linha_codigo: linha,
            data_referencia: data,
            faixa_hora: faixaRaw === '' ? null : Number(faixaRaw),
            descricao,
            resultado_observado: resultadoObs || null,
        });
        fecharModalAcao();
        if (linhaAberta === linha) await carregarAcoes();
    } catch (err) {
        alert('Erro ao salvar a ação: ' + err.message);
    }
}

// ─── 6. Motivos livres mais frequentes (D27) ────────────────────────────
async function carregarMotivosLivres() {
    const lista = document.getElementById('fp-motivos-lista');
    try {
        const fim = dataSelecionada();
        const inicio = new Date(`${fim}T00:00:00`);
        inicio.setDate(inicio.getDate() - 6);
        const params = new URLSearchParams({ data_inicio: inicio.toISOString().slice(0, 10), data_fim: fim });
        const itens = await apiGet(`/fiscalizacao/icv/motivos-livres?${params}`);
        if (itens.length === 0) {
            lista.innerHTML = '<div class="oc-vazio">Nenhum motivo livre nos últimos 7 dias.</div>';
            return;
        }
        lista.innerHTML = itens.map(i => `
            <div>${escapeHtml(i.texto)} <span style="color:var(--muted)">× ${i.quantidade}</span></div>
        `).join('');
    } catch (err) {
        if (ignoravel(err)) return;
    }
}

// ─── Importação da planilha de ICV (D20/D25/D28, §5) ────────────────────
// Upload multipart não passa por api.js (que sempre seta Content-Type:
// application/json) — mesmo padrão local de importacao.js/ocorrencia.form.js.
async function apiUpload(path, formData) {
    const url = `${API_BASE_URL}${path}`;
    const token = localStorage.getItem(TOKEN_KEY);
    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    let response;
    try {
        response = await fetch(url, { method: 'POST', headers, body: formData });
    } catch (networkErr) {
        throw new ApiError(0, 'Não foi possível conectar à API.', { cause: networkErr.message });
    }
    if (response.status === 401) {
        logout();
        window.location.replace('index.html');
        throw new ApiError(401, 'Sessão expirada. Faça login novamente.', null);
    }
    let payload = null;
    try { payload = await response.json(); } catch { /* sem corpo JSON */ }
    if (!response.ok) {
        const detail = payload?.erro || payload?.detail || payload?.message || response.statusText;
        throw new ApiError(response.status, typeof detail === 'string' ? detail : 'Erro no upload', payload);
    }
    return payload;
}

async function importarIcv() {
    const input = document.getElementById('fp-icv-arquivo');
    const resultado = document.getElementById('fp-icv-resultado');
    if (!input.files || input.files.length === 0) {
        resultado.textContent = 'Selecione um arquivo .xlsx.';
        return;
    }
    const formData = new FormData();
    formData.append('file', input.files[0]);
    resultado.textContent = 'Importando…';
    try {
        const resp = await apiUpload('/fiscalizacao/icv/upload', formData);
        const partes = [
            `${resp.linhas_lidas} linha(s) lida(s)`,
            `${resp.linhas_gravadas} nova(s)`,
            `${resp.linhas_atualizadas} atualizada(s)`,
            `${resp.suspeitas.length} suspeita(s) (D25)`,
        ];
        if (resp.divergentes_percentual.length) partes.push(`${resp.divergentes_percentual.length} divergência(s) de %`);
        if (resp.codigos_ilegiveis.length) partes.push(`${resp.codigos_ilegiveis.length} código(s) ilegível(is)`);
        if (resp.erros.length) partes.push(`${resp.erros.length} erro(s)`);
        resultado.textContent = `Data ${resp.data_referencia} — ${partes.join(' · ')}`;
        input.value = '';
        await Promise.all([carregarBacias(), carregarBaciaResumo(), carregarRanking(), carregarCascata()]);
    } catch (err) {
        resultado.textContent = 'Erro: ' + err.message;
    }
}

// ─── Carga geral + polling (padrão do Pátio) ────────────────────────────
async function carregarTudo() {
    ocultarErroSeVazio();
    await Promise.all([
        carregarBaciaResumo(),
        carregarCascata(),
        carregarRanking(),
        carregarMotivosLivres(),
        linhaAberta ? carregarPainelLinha() : Promise.resolve(),
        linhaAberta ? carregarAcoes() : Promise.resolve(),
    ]);
    document.getElementById('fp-poll-status').textContent =
        `Atualizado às ${new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
}

function ocultarErroSeVazio() {
    document.getElementById('fp-erro').style.display = 'none';
}

function iniciarPolling() {
    pararPolling();
    pollHandle = setInterval(carregarTudo, POLLING_INTERVAL_MS);
}

function pararPolling() {
    if (pollHandle) clearInterval(pollHandle);
    pollHandle = null;
}

// ─── Escrita — só quem tem pode_escrever em fiscalizacao_painel ─────────
function aplicarPermissaoEscrita() {
    const podeEscreverPainel = podeEscrever('fiscalizacao_painel');
    document.getElementById('fp-btn-nova-acao').style.display = podeEscreverPainel ? '' : 'none';
    document.querySelector('main.patio-main > .patio-content > section:last-of-type').style.display =
        podeEscreverPainel ? '' : 'none';
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
async function iniciar() {
    initHeader();
    aplicarPermissaoEscrita();
    document.getElementById('fp-data').value = hojeISO();

    document.getElementById('fp-bacia').addEventListener('change', carregarTudo);
    document.getElementById('fp-data').addEventListener('change', carregarTudo);

    document.getElementById('fp-btn-nova-acao').addEventListener('click', abrirModalAcao);
    document.getElementById('fp-modal-acao-fechar').addEventListener('click', fecharModalAcao);
    document.getElementById('fp-acao-cancelar').addEventListener('click', fecharModalAcao);
    document.getElementById('fp-acao-salvar').addEventListener('click', salvarAcao);

    document.getElementById('fp-icv-btn-upload').addEventListener('click', importarIcv);

    await carregarBacias();
    await carregarTudo();
    iniciarPolling();
}

window.addEventListener('beforeunload', pararPolling);

iniciar();
