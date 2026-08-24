/*
 * fiscal.page.js — Tela do fiscal em campo (Bloco D)
 * -------------------------------------------------------------------------------
 * 🎯 Regra de ouro (e ela vence qualquer requisito): marcar cabe num toque,
 * com uma mão só, sem tirar o olho da rua.
 *
 * D32 — primeiro corte: o fiscal abre o turno, confirma as linhas e
 * registra O QUE FUROU. Quando a linha tem grade (escala importada), os
 * horários aparecem em cards com Saiu/Não saiu (D7/D12); sem grade, só a
 * lista do que já foi registrado hoje. Uma linha pode estar em cada modo
 * no mesmo turno — normal do período de transição (D32).
 *
 * D33 — anormalidade É um registro_partida (fora da grade quando não há
 * horário programado correspondente); "+ ANORMALIDADE" e o "Não saiu" de
 * um card de grade caem no mesmo modal, diferindo só em contexto: o card
 * de grade já sabe tabela/terminal/horário (e por isso nunca pergunta
 * "custou viagem?" — perder um horário programado sempre custa a viagem
 * daquele horário); o "+ ANORMALIDADE" do rodapé é livre.
 *
 * D3 — o medidor de prontidão avisa, nunca bloqueia: nenhuma validação
 * client-side impede abrir/fechar o turno por falta de dado opcional.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPatch, apiPost, apiPut, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { criarSeletorLinhas } from './linhas.seletor.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

// ─── Estado ───────────────────────────────────────────────────────────────
let turnoAtual = null;           // TurnoRead ou null
let pontosCache = [];
let pontoEscolhido = null;
let periodoEscolhido = null;
let linhasEscolhidas = new Set();
let abaAtiva = null;             // linha_codigo da aba visível
let partidasPorLinha = {};       // { linha_codigo: PartidaEstadoItem[] }
let contextoRegistro = null;     // não-nulo = "Não saiu" veio de um card de grade
let motivoSelecionado = null;
let viagemEscolhida = null;      // 'sim' | 'nao' | null
let terminalNovoPonto = 'TP';
let mensagensGeradas = [];

// D37 §3 — seletor de linhas do catálogo (linhas.seletor.js), compartilhado
// com fiscal-painel.js. Aqui em modo múltiplo: o ponto pode ter mais de
// uma linha (D9).
let seletorPontoLinhas = null;

const TIPO_LABEL = {
    FALTA_OPERADORES: 'Falta de operadores', RA: 'R.A', SOS: 'S.O.S',
    ATRASO_GARAGEM: 'Atraso de garagem', TROCA_OPERACIONAL: 'Troca operacional',
    VIAGEM_EXTRA: 'Viagem extra', OUTRO: 'Outro',
};

const PENDENCIA_TEXTO = {
    PARTIDAS_SEM_RESPOSTA: (p) => `falta responder ${p.quantidade} partida(s)`,
    CONTAGEM_NAO_INFORMADA: (p) => `falta a contagem de ${(p.linhas || []).join(', ')}`,
    BAITA_FALTANDO: (p) => `BAITA não informado (${(p.linhas || []).join(', ')})`,
    ANTI_BAITA_FALTANDO: (p) => `ANTI-BAITA não informado (${(p.linhas || []).join(', ')})`,
    PASTAS_NAO_INFORMADAS: () => 'pastas não informadas',
    REFEICAO_NAO_INFORMADA: () => 'refeição não informada',
};

// ─── Helpers ────────────────────────────────────────────────────────────
function ignoravel(err) {
    return err instanceof ApiError && err.status === 401;
}

function exibirErro(msg) {
    const el = document.getElementById('fis-erro');
    el.textContent = msg;
    el.style.display = 'block';
}

function cssId(linha) {
    return String(linha).replace(/[^a-zA-Z0-9_-]/g, '_');
}

function horaAgora() {
    const d = new Date();
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
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

// ─── D3 — medidor de prontidão ───────────────────────────────────────────
function renderProntidao(dados) {
    const texto = dados.pronto
        ? 'Fechamento: pronto para gerar.'
        : 'Fechamento: ' + dados.pendencias.map(p => (PENDENCIA_TEXTO[p.tipo] || (() => p.tipo))(p)).join(' · ');
    const classe = dados.pronto ? 'fis-prontidao-pronto' : 'fis-prontidao-pendente';
    [document.getElementById('fis-prontidao'), document.getElementById('fis-fechar-prontidao')].forEach(el => {
        if (!el) return;
        el.textContent = texto;
        el.className = `fis-prontidao ${classe}`;
        el.style.display = '';
    });
}

async function atualizarProntidao() {
    if (!turnoAtual) return;
    try {
        const dados = await apiGet(`/fiscalizacao/turnos/${turnoAtual.id}/prontidao`);
        renderProntidao(dados);
    } catch (err) {
        if (ignoravel(err)) return;
    }
}

// ============================================================================
// ABERTURA DE TURNO (D37, D8, D11)
// ============================================================================

async function iniciarAbertura() {
    document.getElementById('fis-abertura').style.display = '';
    document.getElementById('fis-turno').style.display = 'none';
    document.getElementById('fis-prontidao').style.display = 'none';
    document.getElementById('fis-passo-periodo').style.display = 'none';
    document.getElementById('fis-passo-linhas').style.display = 'none';
    pontoEscolhido = null;
    periodoEscolhido = null;
    linhasEscolhidas = new Set();
    await carregarPontos();
}

async function carregarPontos() {
    const lista = document.getElementById('fis-lista-pontos');
    try {
        pontosCache = await apiGet('/fiscalizacao/pontos');
        if (pontosCache.length === 0) {
            lista.innerHTML = '<div class="oc-vazio">Nenhum ponto cadastrado ainda — cadastre um abaixo.</div>';
            return;
        }
        lista.innerHTML = pontosCache.map(p => `
            <div class="fis-ponto-item" data-ponto="${escapeHtml(p.codigo)}">
                <div>
                    <div class="fis-ponto-nome">${escapeHtml(p.nome)}</div>
                    <div class="fis-ponto-sub">${escapeHtml(p.codigo)} · ${escapeHtml(p.terminal)} · ${(p.linhas || []).length} linha(s)</div>
                </div>
                <span style="color:var(--muted)">›</span>
            </div>
        `).join('');
        lista.querySelectorAll('[data-ponto]').forEach(el => {
            el.addEventListener('click', () => escolherPonto(el.dataset.ponto));
        });
    } catch (err) {
        if (ignoravel(err)) return;
        lista.innerHTML = `<div class="oc-vazio" style="color:var(--accent)">Erro: ${escapeHtml(err.message)}</div>`;
    }
}

function escolherPonto(codigo) {
    pontoEscolhido = pontosCache.find(p => p.codigo === codigo);
    if (!pontoEscolhido) return;
    periodoEscolhido = null;
    document.querySelectorAll('#fis-passo-periodo .fis-btn-grande').forEach(b => b.classList.remove('active'));
    document.getElementById('fis-passo-periodo').style.display = '';
    document.getElementById('fis-passo-linhas').style.display = 'none';
    document.getElementById('fis-passo-periodo').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function initPassoPeriodo() {
    document.querySelectorAll('#fis-passo-periodo .fis-btn-grande').forEach(btn => {
        btn.addEventListener('click', () => {
            periodoEscolhido = btn.dataset.periodo;
            document.querySelectorAll('#fis-passo-periodo .fis-btn-grande').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderPassoLinhas();
        });
    });
}

function renderPassoLinhas() {
    const el = document.getElementById('fis-linhas-checklist');
    linhasEscolhidas = new Set(pontoEscolhido.linhas);
    el.innerHTML = pontoEscolhido.linhas.map(l =>
        `<button type="button" class="recolhida-chip active" data-linha="${escapeHtml(l)}">${escapeHtml(l)}</button>`
    ).join('');
    el.querySelectorAll('[data-linha]').forEach(btn => {
        btn.addEventListener('click', () => {
            const l = btn.dataset.linha;
            if (linhasEscolhidas.has(l)) {
                linhasEscolhidas.delete(l);
                btn.classList.remove('active');
            } else {
                linhasEscolhidas.add(l);
                btn.classList.add('active');
            }
        });
    });
    document.getElementById('fis-passo-linhas').style.display = '';
    document.getElementById('fis-passo-linhas').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function abrirTurno() {
    document.getElementById('fis-erro').style.display = 'none';
    if (linhasEscolhidas.size === 0) {
        exibirErro('Escolha ao menos uma linha.');
        return;
    }
    const btn = document.getElementById('fis-btn-abrir-turno');
    btn.disabled = true;
    try {
        turnoAtual = await apiPost('/fiscalizacao/turnos', {
            ponto_codigo: pontoEscolhido.codigo,
            periodo: periodoEscolhido,
            linhas: Array.from(linhasEscolhidas),
        });
        await entrarNoTurno();
    } catch (err) {
        exibirErro(err.message);
    } finally {
        btn.disabled = false;
    }
}

// ============================================================================
// TURNO ABERTO — abas por linha (D11)
// ============================================================================

async function entrarNoTurno() {
    document.getElementById('fis-erro').style.display = 'none';
    document.getElementById('fis-abertura').style.display = 'none';
    document.getElementById('fis-turno').style.display = '';
    abaAtiva = null;
    await recarregarPartidas();
    await atualizarProntidao();
}

async function recarregarPartidas() {
    try {
        partidasPorLinha = await apiGet(`/fiscalizacao/turnos/${turnoAtual.id}/partidas`);
        renderTabs();
    } catch (err) {
        if (ignoravel(err)) return;
        exibirErro('Erro ao carregar as partidas: ' + err.message);
    }
}

function renderTabs() {
    const tabsEl = document.getElementById('fis-tabs');
    const panelsEl = document.getElementById('fis-tab-panels');
    const linhas = turnoAtual.linhas;
    if (!abaAtiva || !linhas.includes(abaAtiva)) abaAtiva = linhas[0];

    tabsEl.innerHTML = linhas.map(l => {
        const atrasadas = (partidasPorLinha[l] || []).filter(i => i.estado === 'ATRASADA').length;
        return `<button type="button" class="filtro-btn ${l === abaAtiva ? 'active' : ''}" data-linha="${escapeHtml(l)}">${escapeHtml(l)}${atrasadas ? `<span class="fis-tab-badge">${atrasadas}</span>` : ''}</button>`;
    }).join('');
    tabsEl.querySelectorAll('[data-linha]').forEach(btn => {
        btn.addEventListener('click', () => {
            abaAtiva = btn.dataset.linha;
            renderTabs();
        });
    });

    panelsEl.innerHTML = linhas.map(l => `
        <section class="oc-tab-section ${l === abaAtiva ? 'active' : ''}" id="fis-tab-${cssId(l)}">
            <div id="fis-painel-${cssId(l)}"></div>
        </section>
    `).join('');
    linhas.forEach(renderPainelLinha);
}

function renderPainelLinha(linha) {
    const painel = document.getElementById(`fis-painel-${cssId(linha)}`);
    if (!painel) return;
    const itens = (partidasPorLinha[linha] || []).slice();
    if (itens.length === 0) {
        painel.innerHTML = '<div class="oc-vazio">Nada registrado ainda nesta linha.</div>';
        return;
    }
    const temGrade = itens.some(i => !i.fora_da_grade);
    if (temGrade) {
        // D7 — atrasadas sobem ao topo, em vermelho.
        itens.sort((a, b) => {
            const pa = a.estado === 'ATRASADA' ? 0 : 1;
            const pb = b.estado === 'ATRASADA' ? 0 : 1;
            if (pa !== pb) return pa - pb;
            return a.horario_programado.localeCompare(b.horario_programado);
        });
        painel.innerHTML = itens.map(i => (i.fora_da_grade ? renderCardRegistroSimples(i) : renderCardGrade(i))).join('');
    } else {
        // Sem grade: só o que já foi registrado, mais recente em cima.
        itens.sort((a, b) => {
            const momentoA = (a.registro && (a.registro.atualizado_em || a.registro.registrado_em)) || '';
            const momentoB = (b.registro && (b.registro.atualizado_em || b.registro.registrado_em)) || '';
            return momentoB.localeCompare(momentoA);
        });
        painel.innerHTML = itens.map(renderCardRegistroSimples).join('');
    }
    painel.querySelectorAll('[data-marcar-saiu]').forEach(btn => {
        btn.addEventListener('click', () => marcarSaiu(JSON.parse(btn.dataset.marcarSaiu)));
    });
    painel.querySelectorAll('[data-marcar-nao-saiu]').forEach(btn => {
        btn.addEventListener('click', () => abrirModalAnormalidade(JSON.parse(btn.dataset.marcarNaoSaiu)));
    });
}

function renderCardGrade(item) {
    const atrasada = item.estado === 'ATRASADA';
    const hora = item.horario_programado.slice(0, 5);
    const contexto = {
        numero_tabela: item.numero_tabela, terminal: item.terminal,
        horario_programado: item.horario_programado, partida_programada_id: item.partida_programada_id,
    };
    const contextoJson = escapeHtml(JSON.stringify(contexto));
    const motivoTxt = item.registro && item.registro.motivo ? (TIPO_LABEL[item.registro.motivo] || item.registro.motivo) : '';
    return `
        <div class="fis-card-horario ${atrasada ? 'fis-atrasada' : ''}">
            <div class="fis-card-info">
                <div class="fis-card-hora">${hora}</div>
                <div class="fis-card-meta">Tabela ${item.numero_tabela ?? '—'} · ${escapeHtml(item.terminal)}${motivoTxt ? ' · ' + escapeHtml(motivoTxt) : ''}</div>
            </div>
            <div class="fis-card-acoes">
                <button type="button" class="fis-btn-resultado fis-saiu ${item.estado === 'REALIZADA' ? 'active' : ''}" data-marcar-saiu='${contextoJson}'>Saiu</button>
                <button type="button" class="fis-btn-resultado fis-nao-saiu ${item.estado === 'PERDIDA' ? 'active' : ''}" data-marcar-nao-saiu='${contextoJson}'>Não saiu</button>
            </div>
        </div>
    `;
}

function renderCardRegistroSimples(item) {
    const hora = item.horario_programado.slice(0, 5);
    const registro = item.registro;
    const resultadoTxt = item.estado === 'REALIZADA' ? 'Saiu' : 'Não saiu';
    const motivoTxt = registro && registro.motivo ? (TIPO_LABEL[registro.motivo] || registro.motivo) : '';
    return `
        <div class="fis-registro-item">
            <div class="fis-card-hora" style="font-size:18px">${hora} — ${escapeHtml(resultadoTxt)}</div>
            <div class="fis-card-meta">${item.numero_tabela ? `Tabela ${item.numero_tabela} · ` : ''}${escapeHtml(item.terminal)}${motivoTxt ? ' · ' + escapeHtml(motivoTxt) : ''}</div>
        </div>
    `;
}

async function marcarSaiu(contexto) {
    try {
        await apiPut(`/fiscalizacao/turnos/${turnoAtual.id}/partidas`, {
            linha_codigo: abaAtiva,
            numero_tabela: contexto.numero_tabela,
            terminal: contexto.terminal,
            horario_programado: contexto.horario_programado,
            partida_programada_id: contexto.partida_programada_id,
            resultado: 'REALIZADA',
        });
        await recarregarPartidas();
        await atualizarProntidao();
    } catch (err) {
        exibirErro('Erro ao marcar: ' + err.message);
    }
}

// ============================================================================
// MODAL — cadastrar ponto (D37)
// ============================================================================

function initModalPonto() {
    document.getElementById('fis-btn-novo-ponto').addEventListener('click', abrirModalPonto);
    document.getElementById('fis-modal-ponto-fechar').addEventListener('click', fecharModalPonto);
    document.getElementById('fis-ponto-cancelar').addEventListener('click', fecharModalPonto);
    document.getElementById('fis-ponto-salvar').addEventListener('click', salvarPonto);
    document.querySelectorAll('#fis-ponto-terminal .recolhida-chip').forEach(btn => {
        btn.addEventListener('click', () => {
            terminalNovoPonto = btn.dataset.terminal;
            document.querySelectorAll('#fis-ponto-terminal .recolhida-chip').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });
    seletorPontoLinhas = criarSeletorLinhas({
        containerLista: document.getElementById('fis-ponto-linhas-lista'),
        campoBusca: document.getElementById('fis-ponto-linhas-busca'),
        multiplo: true,
    });
}

async function abrirModalPonto() {
    document.getElementById('fis-ponto-codigo').value = '';
    document.getElementById('fis-ponto-nome').value = '';
    document.getElementById('fis-ponto-erro').style.display = 'none';
    terminalNovoPonto = 'TP';
    document.querySelectorAll('#fis-ponto-terminal .recolhida-chip').forEach((b, i) => b.classList.toggle('active', i === 0));
    document.getElementById('fis-modal-ponto').classList.add('open');
    await seletorPontoLinhas.carregar();
}

function fecharModalPonto() {
    document.getElementById('fis-modal-ponto').classList.remove('open');
}

async function salvarPonto() {
    const erro = document.getElementById('fis-ponto-erro');
    erro.style.display = 'none';
    const codigo = document.getElementById('fis-ponto-codigo').value.trim();
    const nome = document.getElementById('fis-ponto-nome').value.trim();
    const linhas = Array.from(seletorPontoLinhas.getSelecao());
    if (!codigo || !nome || linhas.length === 0) {
        erro.textContent = 'Preencha código, nome e ao menos uma linha.';
        erro.style.display = 'block';
        return;
    }
    const btn = document.getElementById('fis-ponto-salvar');
    btn.disabled = true;
    try {
        await apiPost('/fiscalizacao/pontos', { codigo, nome, terminal: terminalNovoPonto, linhas });
        fecharModalPonto();
        await carregarPontos();
    } catch (err) {
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ============================================================================
// MODAL — anormalidade (a tela mais usada do módulo)
// ============================================================================

function initModalAnormalidade() {
    document.getElementById('fis-modal-anormalidade-fechar').addEventListener('click', fecharModalAnormalidade);
    document.getElementById('fis-anorm-cancelar').addEventListener('click', fecharModalAnormalidade);
    document.getElementById('fis-anorm-salvar').addEventListener('click', salvarAnormalidade);
    document.querySelectorAll('#fis-anorm-motivos .fis-motivo-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            motivoSelecionado = btn.dataset.motivo;
            document.querySelectorAll('#fis-anorm-motivos .fis-motivo-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            atualizarCamposCondicionaisAnormalidade();
        });
    });
    document.querySelectorAll('.fis-viagem-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            viagemEscolhida = btn.dataset.viagem;
            document.querySelectorAll('.fis-viagem-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });
}

function atualizarCamposCondicionaisAnormalidade() {
    document.getElementById('fis-anorm-prefixo-campo').style.display =
        (motivoSelecionado === 'RA' || motivoSelecionado === 'SOS') ? '' : 'none';
    document.getElementById('fis-anorm-re-campo').style.display =
        motivoSelecionado === 'FALTA_OPERADORES' ? '' : 'none';
    document.getElementById('fis-anorm-obs-obrigatoria').style.display =
        motivoSelecionado === 'OUTRO' ? '' : 'none';
    // D4 — VIAGEM_EXTRA nunca pergunta "custou viagem?" (nunca é motivo de
    // partida perdida); no contexto de um card de grade a pergunta some
    // inteira (perder um horário programado sempre custa aquela viagem).
    if (!contextoRegistro) {
        document.getElementById('fis-anorm-viagem-campo').style.display =
            motivoSelecionado === 'VIAGEM_EXTRA' ? 'none' : '';
    }
}

function abrirModalAnormalidade(contexto) {
    contextoRegistro = contexto || null;
    motivoSelecionado = null;
    viagemEscolhida = null;
    document.getElementById('fis-anorm-linha-label').textContent = abaAtiva;
    document.getElementById('fis-anorm-erro').style.display = 'none';
    document.getElementById('fis-anorm-observacao').value = '';
    document.getElementById('fis-anorm-prefixo').value = '';
    document.getElementById('fis-anorm-re').value = '';
    document.querySelectorAll('#fis-anorm-motivos .fis-motivo-btn').forEach(b => {
        b.classList.remove('active');
        b.style.display = (b.dataset.motivo === 'VIAGEM_EXTRA' && contextoRegistro) ? 'none' : '';
    });
    document.querySelectorAll('.fis-viagem-btn').forEach(b => b.classList.remove('active'));

    const campoHora = document.getElementById('fis-anorm-hora');
    const campoTabela = document.getElementById('fis-anorm-tabela');
    if (contextoRegistro) {
        campoHora.value = contextoRegistro.horario_programado.slice(0, 5);
        campoHora.disabled = true;
        campoTabela.value = contextoRegistro.numero_tabela ?? '';
        campoTabela.disabled = true;
    } else {
        campoHora.value = horaAgora();
        campoHora.disabled = false;
        campoTabela.value = '';
        campoTabela.disabled = false;
    }
    atualizarCamposCondicionaisAnormalidade();
    document.getElementById('fis-modal-anormalidade').classList.add('open');
}

function fecharModalAnormalidade() {
    document.getElementById('fis-modal-anormalidade').classList.remove('open');
    contextoRegistro = null;
}

async function salvarAnormalidade() {
    const erro = document.getElementById('fis-anorm-erro');
    erro.style.display = 'none';

    if (!motivoSelecionado) { erro.textContent = 'Escolha o tipo.'; erro.style.display = 'block'; return; }
    const observacaoValor = document.getElementById('fis-anorm-observacao').value.trim();
    if (motivoSelecionado === 'OUTRO' && !observacaoValor) {
        erro.textContent = 'Descreva o que aconteceu.'; erro.style.display = 'block'; return;
    }
    const prefixoValor = document.getElementById('fis-anorm-prefixo').value.trim();
    if ((motivoSelecionado === 'RA' || motivoSelecionado === 'SOS') && !prefixoValor) {
        erro.textContent = 'Informe o prefixo.'; erro.style.display = 'block'; return;
    }
    const ehGrade = contextoRegistro !== null;
    if (!ehGrade && motivoSelecionado !== 'VIAGEM_EXTRA' && !viagemEscolhida) {
        erro.textContent = 'Diga se custou viagem.'; erro.style.display = 'block'; return;
    }

    const horaValor = document.getElementById('fis-anorm-hora').value || null;
    const tabelaRaw = document.getElementById('fis-anorm-tabela').value;
    const tabelaValor = tabelaRaw ? Number(tabelaRaw) : null;
    const reValor = document.getElementById('fis-anorm-re').value.trim();
    const custouViagem = ehGrade ? true : (motivoSelecionado === 'VIAGEM_EXTRA' ? false : viagemEscolhida === 'sim');

    // Fora da grade, o horário programado vem SÓ daqui — sem ele o backend
    // recusa com 422 e o fiscal leva um erro técnico na cara, em pé no ponto.
    // O campo nasce preenchido com a hora atual; isto cobre quem apagou pra
    // redigitar e salvou no meio.
    if (custouViagem && !ehGrade && !horaValor) {
        erro.textContent = 'Informe a hora da viagem que não saiu.';
        erro.style.display = 'block';
        return;
    }

    const btn = document.getElementById('fis-anorm-salvar');
    btn.disabled = true;
    try {
        if (motivoSelecionado === 'VIAGEM_EXTRA') {
            // D4 — sempre evento avulso, nunca partida perdida.
            await apiPost(`/fiscalizacao/turnos/${turnoAtual.id}/eventos`, {
                linha_codigo: abaAtiva, tipo: 'VIAGEM_EXTRA', horario: horaValor,
                numero_tabela: tabelaValor, prefixo: null, observacao: observacaoValor || null,
            });
        } else if (custouViagem) {
            // Backend cria o evento vinculado sozinho (_sincronizar_evento_vinculado)
            // — não postar evento também, senão o contador soma duas vezes.
            const payload = {
                linha_codigo: abaAtiva,
                numero_tabela: ehGrade ? contextoRegistro.numero_tabela : tabelaValor,
                terminal: ehGrade ? contextoRegistro.terminal : turnoAtual.terminal,
                horario_programado: ehGrade ? contextoRegistro.horario_programado : horaValor,
                partida_programada_id: ehGrade ? contextoRegistro.partida_programada_id : null,
                resultado: 'PERDIDA',
                motivo: motivoSelecionado,
                motivo_outro: motivoSelecionado === 'OUTRO' ? observacaoValor : null,
                prefixo: (motivoSelecionado === 'RA' || motivoSelecionado === 'SOS') ? prefixoValor : null,
                operador_re: motivoSelecionado === 'FALTA_OPERADORES' ? (reValor || null) : null,
            };
            await apiPut(`/fiscalizacao/turnos/${turnoAtual.id}/partidas`, payload);
            if (motivoSelecionado === 'OUTRO') {
                // Escape hatch do D5 — sempre grava a observação também.
                await apiPost(`/fiscalizacao/turnos/${turnoAtual.id}/observacoes`, {
                    linha_codigo: abaAtiva, numero_tabela: payload.numero_tabela,
                    horario: payload.horario_programado, texto: observacaoValor,
                });
            }
        } else if (motivoSelecionado === 'OUTRO') {
            // Não custou viagem: TipoEvento não tem OUTRO — vira só a
            // observação, sem contador nem perda (D5, o escape hatch puro).
            await apiPost(`/fiscalizacao/turnos/${turnoAtual.id}/observacoes`, {
                linha_codigo: abaAtiva, numero_tabela: tabelaValor, horario: horaValor, texto: observacaoValor,
            });
        } else {
            // Ocorrência avulsa (D4): aconteceu, não custou viagem.
            let obsFinal = observacaoValor || null;
            if (motivoSelecionado === 'FALTA_OPERADORES' && reValor) {
                obsFinal = `RE ${reValor}` + (observacaoValor ? ` — ${observacaoValor}` : '');
            }
            await apiPost(`/fiscalizacao/turnos/${turnoAtual.id}/eventos`, {
                linha_codigo: abaAtiva, tipo: motivoSelecionado, horario: horaValor, numero_tabela: tabelaValor,
                prefixo: (motivoSelecionado === 'RA' || motivoSelecionado === 'SOS') ? (prefixoValor || null) : null,
                observacao: obsFinal,
            });
        }
        fecharModalAnormalidade();
        await recarregarPartidas();
        await atualizarProntidao();
    } catch (err) {
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ============================================================================
// MODAL — observação livre (D5)
// ============================================================================

function initModalObservacao() {
    document.getElementById('fis-modal-observacao-fechar').addEventListener('click', fecharModalObservacao);
    document.getElementById('fis-obs-cancelar').addEventListener('click', fecharModalObservacao);
    document.getElementById('fis-obs-salvar').addEventListener('click', salvarObservacao);
}

function abrirModalObservacao() {
    const select = document.getElementById('fis-obs-linha');
    select.innerHTML = '<option value="">Turno inteiro</option>' +
        turnoAtual.linhas.map(l => `<option value="${escapeHtml(l)}" ${l === abaAtiva ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('');
    document.getElementById('fis-obs-tabela').value = '';
    document.getElementById('fis-obs-hora').value = '';
    document.getElementById('fis-obs-texto').value = '';
    document.getElementById('fis-obs-erro').style.display = 'none';
    document.getElementById('fis-modal-observacao').classList.add('open');
}

function fecharModalObservacao() {
    document.getElementById('fis-modal-observacao').classList.remove('open');
}

async function salvarObservacao() {
    const erro = document.getElementById('fis-obs-erro');
    erro.style.display = 'none';
    const texto = document.getElementById('fis-obs-texto').value.trim();
    if (!texto) {
        erro.textContent = 'Escreva o texto da observação.';
        erro.style.display = 'block';
        return;
    }
    const linha = document.getElementById('fis-obs-linha').value || null;
    const tabelaRaw = document.getElementById('fis-obs-tabela').value;
    const hora = document.getElementById('fis-obs-hora').value || null;
    const btn = document.getElementById('fis-obs-salvar');
    btn.disabled = true;
    try {
        await apiPost(`/fiscalizacao/turnos/${turnoAtual.id}/observacoes`, {
            linha_codigo: linha, numero_tabela: tabelaRaw ? Number(tabelaRaw) : null, horario: hora, texto,
        });
        fecharModalObservacao();
    } catch (err) {
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ============================================================================
// MODAL — fechar turno (D2, D13, D14, D15, D35)
// ============================================================================

function initModalFechar() {
    document.getElementById('fis-modal-fechar-fechar').addEventListener('click', fecharModalFechar);
    document.getElementById('fis-fechar-cancelar').addEventListener('click', fecharModalFechar);
    document.getElementById('fis-fechar-gerar').addEventListener('click', gerarFechamento);
    document.getElementById('fis-fechar-concluir').addEventListener('click', concluirFechamento);
}

async function abrirModalFechar() {
    document.getElementById('fis-fechar-erro').style.display = 'none';
    document.getElementById('fis-fechar-mensagens').innerHTML = '';
    document.getElementById('fis-fechar-concluir').style.display = 'none';
    document.getElementById('fis-fechar-refeicao-inicio').value = turnoAtual.refeicao_inicio ? turnoAtual.refeicao_inicio.slice(0, 5) : '';
    document.getElementById('fis-fechar-refeicao-fim').value = turnoAtual.refeicao_fim ? turnoAtual.refeicao_fim.slice(0, 5) : '';
    document.getElementById('fis-fechar-pastas').value = turnoAtual.pastas_prefixo || '';
    renderContagemPorLinha();
    renderBaitaPorLinha();
    await atualizarProntidao();
    document.getElementById('fis-modal-fechar').classList.add('open');
}

function fecharModalFechar() {
    document.getElementById('fis-modal-fechar').classList.remove('open');
}

function renderContagemPorLinha() {
    // Prévia só leitura pra linha com grade: conta os itens que já vieram
    // de GET .../partidas. ⚠️ Pode divergir em 1 do texto gerado numa
    // borda rara — partida com periodo NULL (parser não decidiu o período,
    // ver importacao_escala_fiscal.py) entra nesta listagem mas não no
    // "programadas" que o backend soma no fechamento (_totais_da_linha
    // filtra periodo == turno.periodo, sem OR NULL). O texto GERADO
    // continua sempre correto — só esta prévia pode subestimar/superestimar
    // por 1 nesse caso raro de transição.
    const container = document.getElementById('fis-fechar-contagem');
    container.innerHTML = turnoAtual.linhas.map(linha => {
        const itens = partidasPorLinha[linha] || [];
        const temGrade = itens.some(i => !i.fora_da_grade);
        if (temGrade) {
            const programadas = itens.filter(i => !i.fora_da_grade).length;
            const realizadas = itens.filter(i => !i.fora_da_grade && i.estado === 'REALIZADA').length;
            return `
                <div class="fis-linha-fechamento">
                    <div class="fis-card-hora" style="font-size:16px">${escapeHtml(linha)}</div>
                    <div class="fis-campo-leitura">Programadas: ${programadas} · Realizadas: ${realizadas}</div>
                    <div class="fis-campo-fonte">contado pela escala</div>
                </div>
            `;
        }
        const id = cssId(linha);
        return `
            <div class="fis-linha-fechamento">
                <div class="fis-card-hora" style="font-size:16px">${escapeHtml(linha)}</div>
                <div class="oc-grid">
                    <div class="form-group"><label class="form-label">Programadas</label><input type="number" class="form-input" min="0" id="fis-contagem-prog-${id}"></div>
                    <div class="form-group"><label class="form-label">Realizadas</label><input type="number" class="form-input" min="0" id="fis-contagem-real-${id}"></div>
                    <div class="form-group"><label class="form-label">Extras</label><input type="number" class="form-input" min="0" id="fis-contagem-extra-${id}"></div>
                </div>
                <div class="fis-campo-fonte">informado por você</div>
                <button type="button" class="btn btn-ghost" data-salvar-contagem="${escapeHtml(linha)}" style="margin-top:8px">Salvar contagem</button>
            </div>
        `;
    }).join('');
    container.querySelectorAll('[data-salvar-contagem]').forEach(btn => {
        btn.addEventListener('click', () => salvarContagem(btn.dataset.salvarContagem));
    });
}

async function salvarContagem(linha) {
    const id = cssId(linha);
    const payload = {};
    const prog = document.getElementById(`fis-contagem-prog-${id}`).value;
    const real = document.getElementById(`fis-contagem-real-${id}`).value;
    const extra = document.getElementById(`fis-contagem-extra-${id}`).value;
    if (prog !== '') payload.programadas_informadas = Number(prog);
    if (real !== '') payload.realizadas_informadas = Number(real);
    if (extra !== '') payload.extras_informadas = Number(extra);
    try {
        await apiPatch(`/fiscalizacao/turnos/${turnoAtual.id}/linhas/${encodeURIComponent(linha)}`, payload);
        await atualizarProntidao();
    } catch (err) {
        exibirErro('Erro ao salvar a contagem: ' + err.message);
    }
}

function renderBaitaPorLinha() {
    const container = document.getElementById('fis-fechar-baita');
    container.innerHTML = turnoAtual.linhas.map(linha => `
        <div class="fis-linha-fechamento">
            <div class="fis-card-hora" style="font-size:16px">${escapeHtml(linha)}</div>
            ${renderBaitaForm(linha, 'ANTI_BAITA', 'ANTI-BAITA')}
            ${renderBaitaForm(linha, 'BAITA', 'BAITA')}
        </div>
    `).join('');
    container.querySelectorAll('[data-salvar-baita]').forEach(btn => {
        btn.addEventListener('click', () => salvarBaita(btn.dataset.salvarBaitaLinha, btn.dataset.salvarBaita));
    });
    container.querySelectorAll('[data-circular]').forEach(chk => {
        chk.addEventListener('change', () => {
            const alvo = document.getElementById(chk.dataset.circular);
            if (!alvo) return;
            alvo.disabled = chk.checked;
            if (chk.checked) alvo.value = '';
        });
    });
}

function renderBaitaForm(linha, tipo, label) {
    const id = `${cssId(linha)}-${tipo}`;
    return `
        <div style="margin:10px 0;padding:10px;border:1px solid var(--border);border-radius:8px">
            <div style="font-weight:700;margin-bottom:6px">${label}</div>
            <div class="oc-grid">
                <div class="form-group"><label class="form-label">Carro</label><input type="text" class="form-input" inputmode="numeric" id="fis-baita-carro-${id}"></div>
                <div class="form-group"><label class="form-label">Motorista RE</label><input type="text" class="form-input" inputmode="numeric" id="fis-baita-mot-${id}"></div>
                <div class="form-group"><label class="form-label">Cobrador RE</label><input type="text" class="form-input" inputmode="numeric" id="fis-baita-cob-${id}"></div>
                <div class="form-group"><label class="form-label">Saída TP</label><input type="time" class="form-input" id="fis-baita-tp-${id}"></div>
                <div class="form-group">
                    <label class="form-label">Saída TS</label>
                    <input type="time" class="form-input" id="fis-baita-ts-${id}">
                    <label style="display:flex;align-items:center;gap:6px;margin-top:6px;font-size:12px;color:var(--muted)">
                        <input type="checkbox" id="fis-baita-circular-${id}" data-circular="fis-baita-ts-${id}"> circular (sem TS)
                    </label>
                </div>
            </div>
            <button type="button" class="btn btn-ghost" data-salvar-baita="${tipo}" data-salvar-baita-linha="${escapeHtml(linha)}" style="margin-top:6px">Salvar ${escapeHtml(label.toLowerCase())}</button>
        </div>
    `;
}

async function salvarBaita(linha, tipo) {
    const id = `${cssId(linha)}-${tipo}`;
    const prefixo = document.getElementById(`fis-baita-carro-${id}`).value.trim();
    if (!prefixo) {
        exibirErro(`Informe o carro do ${tipo === 'BAITA' ? 'BAITA' : 'ANTI-BAITA'} da linha ${linha}.`);
        return;
    }
    const circular = document.getElementById(`fis-baita-circular-${id}`).checked;
    const saidaTs = document.getElementById(`fis-baita-ts-${id}`).value;
    try {
        await apiPut(`/fiscalizacao/turnos/${turnoAtual.id}/baita`, {
            linha_codigo: linha, tipo, prefixo,
            motorista_re: document.getElementById(`fis-baita-mot-${id}`).value.trim() || null,
            cobrador_re: document.getElementById(`fis-baita-cob-${id}`).value.trim() || null,
            saida_tp: document.getElementById(`fis-baita-tp-${id}`).value || null,
            saida_ts: circular ? null : (saidaTs || null),
            ts_circular: circular,
        });
        await atualizarProntidao();
    } catch (err) {
        exibirErro('Erro ao salvar: ' + err.message);
    }
}

async function gerarFechamento() {
    const erro = document.getElementById('fis-fechar-erro');
    erro.style.display = 'none';
    const inicio = document.getElementById('fis-fechar-refeicao-inicio').value || null;
    const fim = document.getElementById('fis-fechar-refeicao-fim').value || null;
    const pastas = document.getElementById('fis-fechar-pastas').value.trim() || null;
    try {
        turnoAtual = await apiPatch(`/fiscalizacao/turnos/${turnoAtual.id}`, {
            refeicao_inicio: inicio, refeicao_fim: fim, pastas_prefixo: pastas,
        });
        mensagensGeradas = await apiGet(`/fiscalizacao/turnos/${turnoAtual.id}/fechamento`);
        const container = document.getElementById('fis-fechar-mensagens');
        container.innerHTML = mensagensGeradas.map((texto, i) => `
            <div class="oc-mensagem-box">${escapeHtml(texto)}</div>
            <button type="button" class="btn btn-ghost btn-full" data-copiar="${i}" style="margin-bottom:16px">COPIAR</button>
        `).join('');
        container.querySelectorAll('[data-copiar]').forEach(btn => {
            btn.addEventListener('click', () => copiarMensagem(Number(btn.dataset.copiar), btn));
        });
        document.getElementById('fis-fechar-concluir').style.display = '';
        await atualizarProntidao();
    } catch (err) {
        erro.textContent = err.message;
        erro.style.display = 'block';
    }
}

async function copiarMensagem(indice, btn) {
    try {
        await navigator.clipboard.writeText(mensagensGeradas[indice]);
        const original = btn.textContent;
        btn.textContent = '✔ Copiado!';
        setTimeout(() => { btn.textContent = original; }, 1500);
    } catch {
        exibirErro('Não foi possível copiar automaticamente. Selecione o texto e copie manualmente.');
    }
}

async function concluirFechamento() {
    try {
        await apiPost(`/fiscalizacao/turnos/${turnoAtual.id}/fechar`);
        fecharModalFechar();
        turnoAtual = null;
        await iniciarAbertura();
    } catch (err) {
        exibirErro('Erro ao fechar o turno: ' + err.message);
    }
}

// ============================================================================
// Bootstrap
// ============================================================================

async function iniciar() {
    initHeader();
    initPassoPeriodo();
    initModalPonto();
    initModalAnormalidade();
    initModalObservacao();
    initModalFechar();

    document.getElementById('fis-btn-abrir-turno').addEventListener('click', abrirTurno);
    document.getElementById('fis-btn-anormalidade').addEventListener('click', () => abrirModalAnormalidade(null));
    document.getElementById('fis-btn-observacao').addEventListener('click', abrirModalObservacao);
    document.getElementById('fis-btn-fechar-turno').addEventListener('click', abrirModalFechar);

    let ativo = null;
    try {
        ativo = await apiGet('/fiscalizacao/turnos/ativo');
    } catch (err) {
        if (!ignoravel(err)) exibirErro('Erro ao verificar turno ativo: ' + err.message);
    }
    if (ativo) {
        turnoAtual = ativo;
        await entrarNoTurno();
    } else {
        await iniciarAbertura();
    }
}

iniciar();
