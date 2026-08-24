/*
 * fiscal-painel.page.js — Painel do coordenador (Fiscalização)
 * -------------------------------------------------------------------------------
 * D39 — a tela abre no AO VIVO das linhas do coordenador logado, não no ICV.
 * Ordem na tela, de cima para baixo:
 *   1. Agora na rua — o que os fiscais registraram hoje nas minhas linhas
 *   2. Turnos abertos — quem está em campo e há quanto tempo não registra nada
 *   3. Minhas linhas (D38, D40) — atribuir/remover, com convite quando vazio
 *   4. ICV do dia (D22 ponderado, D29 meta) — sem seletor de bacia
 *   5. Ranking por perda absoluta (D23, D28) → abre a linha (D12) ao tocar
 *   6. Cascata agora (D24) — vazio = nada na tela
 *   7. Ações da coordenação (D26)
 *   8. Motivos livres mais frequentes (D27)
 *   + importação da planilha de ICV (D20/D25/D28, §5)
 * A lógica do Bloco E (ICV, ponderação D22, perda absoluta D23) não muda —
 * só a ordem e a origem do dado (linhas do coordenador, não bacia).
 *
 * Polling no padrão do Pátio (config.js::POLLING_INTERVAL_MS). Molde:
 * portaria-consulta.page.js + patio.page.js.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiDelete, apiGet, apiPost, ApiError } from './api.js';
import { API_BASE_URL, TOKEN_KEY, POLLING_INTERVAL_MS } from './config.js';
import { podeEscrever } from './sessao.js';
import { escapeHtml } from './escape.js';
import { dataLocalISO } from './data.util.js';
import { imprimirPlacarLinha } from './fiscal-placar.imprimir.js';
import { criarSeletorLinhas } from './linhas.seletor.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

let pollHandle = null;
let linhaAberta = null;
let minhasLinhasCache = [];
let periodoNovaLinha = '1';

// D38 §3 — seletor de linhas do catálogo (linhas.seletor.js), compartilhado
// com fiscal.page.js. Aqui em modo único: o coordenador atribui uma linha
// por vez a si mesmo.
let seletorNovaLinha = null;

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
function dataSelecionada() {
    return document.getElementById('fp-data').value || dataLocalISO();
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

// ─── 1. Agora na rua (D39) ───────────────────────────────────────────────
const TIPO_LABEL = {
    FALTA_OPERADORES: 'Falta de operadores', RA: 'R.A', SOS: 'S.O.S',
    ATRASO_GARAGEM: 'Atraso de garagem', TROCA_OPERACIONAL: 'Troca operacional',
    VIAGEM_EXTRA: 'Viagem extra', OUTRO: 'Outro', REALIZADA: 'Saiu',
};

async function carregarAoVivo() {
    const lista = document.getElementById('fp-ao-vivo-lista');
    try {
        const params = new URLSearchParams({ data: dataSelecionada() });
        const itens = await apiGet(`/fiscalizacao/painel/ao-vivo?${params}`);
        if (itens.length === 0) {
            lista.innerHTML = '<div class="oc-vazio">Nenhum registro hoje nas suas linhas.</div>';
            return;
        }
        lista.innerHTML = itens.map(i => `
            <div class="portaria-item" style="cursor:default">
                <div>
                    <div class="portaria-item-placa">${escapeHtml(i.linha_codigo)}${i.numero_tabela ? ` · Tabela ${i.numero_tabela}` : ''}</div>
                    <div class="portaria-item-sub">${escapeHtml(TIPO_LABEL[i.tipo] || i.tipo)}${i.custou_viagem ? ' · viagem perdida' : ''} · ${escapeHtml(i.ponto_codigo)} · RE ${escapeHtml(i.fiscal_re)}</div>
                </div>
                <div class="portaria-item-hora">${i.minutos_atras <= 0 ? 'agora' : `há ${i.minutos_atras} min`}</div>
            </div>
        `).join('');
    } catch (err) {
        if (ignoravel(err)) return;
        exibirErro('Erro ao carregar o ao vivo: ' + err.message);
    }
}

// ─── 2. Turnos abertos (D39) ─────────────────────────────────────────────
async function carregarTurnosAbertos() {
    const corpo = document.getElementById('fp-turnos-corpo');
    try {
        const params = new URLSearchParams({ data: dataSelecionada() });
        const itens = await apiGet(`/fiscalizacao/painel/turnos?${params}`);
        if (itens.length === 0) {
            corpo.innerHTML = '<tr><td colspan="7" class="oc-vazio">Nenhum turno aberto hoje.</td></tr>';
            return;
        }
        corpo.innerHTML = itens.map(t => `
            <tr>
                <td>${escapeHtml(t.fiscal_nome)} <span style="color:var(--muted)">RE ${escapeHtml(t.fiscal_re)}</span></td>
                <td>${escapeHtml(t.ponto_codigo)}</td>
                <td>${escapeHtml(t.terminal)}</td>
                <td>${escapeHtml(t.periodo)}º</td>
                <td>${escapeHtml((t.linhas || []).join(', ') || '—')}</td>
                <td>${t.aberto_em ? new Date(t.aberto_em).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '—'}</td>
                <td>${(t.minutos_sem_registrar === null || t.minutos_sem_registrar === undefined) ? '—' : `${t.minutos_sem_registrar} min`}</td>
            </tr>
        `).join('');
    } catch (err) {
        if (ignoravel(err)) return;
        corpo.innerHTML = '';
        exibirErro('Erro ao carregar turnos abertos: ' + err.message);
    }
}

// ─── 3. Minhas linhas (D38, D40) ─────────────────────────────────────────
async function carregarMinhasLinhas() {
    const lista = document.getElementById('fp-minhas-linhas-lista');
    try {
        minhasLinhasCache = await apiGet('/fiscalizacao/minhas-linhas');
        if (minhasLinhasCache.length === 0) {
            lista.innerHTML = '<div class="oc-vazio">Você ainda não tem linhas atribuídas.</div>' +
                '<button type="button" class="btn btn-primary" id="fp-btn-atribuir-primeira" style="margin-top:8px">+ Atribuir linha</button>';
            document.getElementById('fp-btn-atribuir-primeira')?.addEventListener('click', abrirModalLinhas);
            return;
        }
        lista.innerHTML = minhasLinhasCache.map(l =>
            `<span class="recolhida-chip active" style="cursor:default;display:inline-block;margin:0 6px 6px 0">${escapeHtml(l.linha_codigo)} · ${escapeHtml(l.periodo)}º período</span>`
        ).join('');
    } catch (err) {
        if (ignoravel(err)) return;
        lista.innerHTML = `<div class="oc-vazio" style="color:var(--accent)">Erro: ${escapeHtml(err.message)}</div>`;
    }
}

function renderModalLinhas() {
    const el = document.getElementById('fp-modal-linhas-lista');
    if (minhasLinhasCache.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Nenhuma linha atribuída ainda.</div>';
        return;
    }
    el.innerHTML = minhasLinhasCache.map(l => `
        <div class="portaria-item" style="cursor:default">
            <div class="portaria-item-placa">${escapeHtml(l.linha_codigo)} · ${escapeHtml(l.periodo)}º período</div>
            <button type="button" class="btn-acao-lista" data-remover-linha="${escapeHtml(l.linha_codigo)}" data-remover-periodo="${escapeHtml(l.periodo)}" title="Remover">✕</button>
        </div>
    `).join('');
    el.querySelectorAll('[data-remover-linha]').forEach(btn => {
        btn.addEventListener('click', () => removerMinhaLinha(btn.dataset.removerLinha, btn.dataset.removerPeriodo));
    });
}

async function abrirModalLinhas() {
    document.getElementById('fp-modal-linhas-erro').style.display = 'none';
    renderModalLinhas();
    document.getElementById('fp-modal-linhas').classList.add('open');
    await seletorNovaLinha.carregar();
}

function fecharModalLinhas() {
    document.getElementById('fp-modal-linhas').classList.remove('open');
}

function initPeriodoNovaLinha() {
    document.querySelectorAll('#fp-nova-linha-periodo .recolhida-chip').forEach(btn => {
        btn.addEventListener('click', () => {
            periodoNovaLinha = btn.dataset.periodo;
            document.querySelectorAll('#fp-nova-linha-periodo .recolhida-chip').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });
}

async function adicionarMinhaLinha() {
    const erro = document.getElementById('fp-modal-linhas-erro');
    erro.style.display = 'none';
    const [linha] = seletorNovaLinha.getSelecao();
    if (!linha) {
        erro.textContent = 'Escolha a linha na lista.';
        erro.style.display = 'block';
        return;
    }
    try {
        await apiPost('/fiscalizacao/minhas-linhas', { linha_codigo: linha, periodo: periodoNovaLinha });
        await seletorNovaLinha.carregar();
        await carregarMinhasLinhas();
        renderModalLinhas();
        await carregarTudo();
    } catch (err) {
        erro.textContent = err.message;
        erro.style.display = 'block';
    }
}

async function removerMinhaLinha(linha, periodo) {
    try {
        await apiDelete(`/fiscalizacao/minhas-linhas/${encodeURIComponent(linha)}?periodo=${encodeURIComponent(periodo)}`);
        await carregarMinhasLinhas();
        renderModalLinhas();
        await carregarTudo();
    } catch (err) {
        exibirErro('Erro ao remover linha: ' + err.message);
    }
}

// ─── 4. ICV do dia (D22, D29) — sem bacia, direto do coordenador logado ──
async function carregarIcvDoDia() {
    const cardIcv = document.getElementById('fp-icv-ponderado').closest('.stat-card');
    try {
        const dados = await apiGet(`/fiscalizacao/icv/coordenador?data=${dataSelecionada()}`);
        document.getElementById('fp-icv-ponderado').textContent = fmtPercentual(dados.icv_ponderado);
        document.getElementById('fp-meta').textContent = fmtPercentual(dados.icv_meta);
        document.getElementById('fp-totais').textContent = `${dados.realizadas} / ${dados.programadas}`;
        // Comparação sempre contra a meta (D29) — nunca o corte de aceitável.
        const abaixoDaMeta = dados.icv_ponderado !== null && dados.icv_meta !== null && dados.icv_ponderado < dados.icv_meta;
        cardIcv.classList.toggle('stat-card-warn', abaixoDaMeta);
    } catch (err) {
        if (ignoravel(err)) return;
        exibirErro('Erro ao carregar o ICV do dia: ' + err.message);
    }
}

// ─── 5. Ranking por perda absoluta (D23, D28) ────────────────────────────
async function carregarRanking() {
    const corpo = document.getElementById('fp-ranking-corpo');
    try {
        const params = new URLSearchParams({ data: dataSelecionada() });
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

// ─── A linha aberta (D12), revelada ao tocar numa linha do ranking ──────
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
                <td>${p.numero_tabela ?? '—'}</td>
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

// ─── Placar impresso por linha (§7) — molde de ocorrencia.imprimir.js ──
async function imprimirPlacar() {
    if (!linhaAberta) return;
    try {
        const params = new URLSearchParams({ data: dataSelecionada() });
        const dados = await apiGet(`/fiscalizacao/icv/placar/${encodeURIComponent(linhaAberta)}?${params}`);
        imprimirPlacarLinha(dados);
    } catch (err) {
        if (ignoravel(err)) return;
        exibirErro('Erro ao montar o placar: ' + err.message);
    }
}

// ─── 6. Cascata agora (D24) ───────────────────────────────────────────────
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

// ─── 7. Ações da coordenação (D26) ───────────────────────────────────────
async function carregarAcoes() {
    const lista = document.getElementById('fp-acoes-lista');
    if (!linhaAberta) {
        lista.innerHTML = '<div class="oc-vazio">Toque numa linha do ranking para ver as ações.</div>';
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

// ─── 8. Motivos livres mais frequentes (D27) ─────────────────────────────
async function carregarMotivosLivres() {
    const lista = document.getElementById('fp-motivos-lista');
    try {
        const fim = dataSelecionada();
        // 🔴 Antes convertia a data pra UTC pra montar o início da janela de
        // 7 dias — a armadilha do fuso fazia a janela pegar 8 dias depois
        // das 21h. dataLocalISO() monta a data sem passar por UTC.
        const inicioDate = new Date(`${fim}T00:00:00`);
        inicioDate.setDate(inicioDate.getDate() - 6);
        const params = new URLSearchParams({ data_inicio: dataLocalISO(inicioDate), data_fim: fim });
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
        await Promise.all([carregarIcvDoDia(), carregarRanking(), carregarCascata()]);
    } catch (err) {
        resultado.textContent = 'Erro: ' + err.message;
    }
}

// ─── Carga geral + polling (padrão do Pátio) ────────────────────────────
async function carregarTudo() {
    ocultarErroSeVazio();
    await Promise.all([
        carregarAoVivo(),
        carregarTurnosAbertos(),
        carregarIcvDoDia(),
        carregarRanking(),
        carregarCascata(),
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
    document.getElementById('fp-data').value = dataLocalISO();

    document.getElementById('fp-data').addEventListener('change', carregarTudo);

    document.getElementById('fp-btn-editar-linhas').addEventListener('click', abrirModalLinhas);
    document.getElementById('fp-modal-linhas-fechar').addEventListener('click', fecharModalLinhas);
    document.getElementById('fp-modal-linhas-concluir').addEventListener('click', fecharModalLinhas);
    document.getElementById('fp-nova-linha-adicionar').addEventListener('click', adicionarMinhaLinha);
    initPeriodoNovaLinha();
    seletorNovaLinha = criarSeletorLinhas({
        containerLista: document.getElementById('fp-nova-linha-lista'),
        campoBusca: document.getElementById('fp-nova-linha-busca'),
        multiplo: false,
    });

    document.getElementById('fp-btn-nova-acao').addEventListener('click', abrirModalAcao);
    document.getElementById('fp-modal-acao-fechar').addEventListener('click', fecharModalAcao);
    document.getElementById('fp-acao-cancelar').addEventListener('click', fecharModalAcao);
    document.getElementById('fp-acao-salvar').addEventListener('click', salvarAcao);

    document.getElementById('fp-icv-btn-upload').addEventListener('click', importarIcv);
    document.getElementById('fp-btn-imprimir-placar').addEventListener('click', imprimirPlacar);

    await carregarMinhasLinhas();
    await carregarTudo();
    iniciarPolling();
}

window.addEventListener('beforeunload', pararPolling);

iniciar();
