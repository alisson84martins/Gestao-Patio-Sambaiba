/*
 * portaria-veiculos.page.js — Cadastro e autorização de veículos
 * -----------------------------------------------------------
 * Três abas: Pendentes (padrão), Todos, Divergências (D13). Botões de
 * autorizar/suspender/baixar só aparecem pra quem tem autorizacao_veicular
 * escrever (checado por /auth/me via podeEscrever()) — a trava de verdade
 * é sempre o backend, isto aqui só evita mostrar um botão que ia dar 403.
 *
 * 🔴 D6: cadastrar (veiculo_portaria) e autorizar (autorizacao_veicular)
 * são atos e recursos diferentes — esta tela nunca manda `situacao` pelo
 * PATCH de cadastro, só pelo PATCH /situacao.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPatch, apiPost, ApiError } from './api.js';
import { podeEscrever } from './sessao.js';
import { escapeHtml } from './escape.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

let empresasCache = null;
let fichaVeiculoAtual = null; // VeiculoRead do modal de ficha aberto no momento
let situacaoAlvo = null;      // 'AUTORIZADO' | 'SUSPENSO' | 'BAIXADO' — ação pendente de motivo

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

// ─── Utilidades ─────────────────────────────────────────────────────────
function abrirModal(id) { document.getElementById(id).classList.add('open'); }
function fecharModal(id) { document.getElementById(id).classList.remove('open'); }

function fmtDataHora(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function badgeSituacao(situacao) {
    const mapa = {
        PENDENTE: ['Pendente', 'portaria-badge-pendente'],
        AUTORIZADO: ['Autorizado', 'portaria-badge-autorizado'],
        SUSPENSO: ['Suspenso', 'portaria-badge-suspenso'],
        BAIXADO: ['Baixado', 'portaria-badge-baixado'],
    };
    const [label, classe] = mapa[situacao] || [situacao, 'portaria-badge-baixado'];
    return `<span class="portaria-badge ${classe}">${escapeHtml(label)}</span>`;
}

function donoTexto(v) {
    if (v.propriedade === 'TERCEIRO') return v.empresa_terceira_nome || 'Terceiro';
    if (v.propriedade === 'EMPRESA') return 'Veículo da empresa';
    if (v.funcionario_nome) return v.funcionario_re ? `${v.funcionario_nome} · RE ${v.funcionario_re}` : v.funcionario_nome;
    return '—';
}

async function preencherSelectEmpresas(selectId) {
    const select = document.getElementById(selectId);
    if (!empresasCache) {
        try {
            empresasCache = await apiGet('/portaria/empresas?apenas_ativas=true');
        } catch (err) {
            if (err instanceof ApiError && err.status === 401) return;
            console.error('[portaria-veiculos] erro ao carregar empresas:', err);
            empresasCache = [];
        }
    }
    select.innerHTML = '<option value="">Selecione…</option>';
    for (const emp of empresasCache) {
        const opt = document.createElement('option');
        opt.value = emp.id;
        opt.textContent = emp.nome;
        select.appendChild(opt);
    }
}

// ─── Permissões (só decoram a UI — a trava real é o backend) ───────────
function aplicarPermissoes() {
    const podeAutorizar = podeEscrever('autorizacao_veicular');
    const podeCadastrar = podeEscrever('veiculo_portaria');
    document.getElementById('btn-bloquear-re').style.display = podeAutorizar ? 'block' : 'none';
    document.getElementById('btn-novo-veiculo').style.display = podeCadastrar ? 'block' : 'none';
    document.getElementById('btn-nova-empresa').style.display = podeCadastrar ? 'block' : 'none';
}

// ─── Tabs ───────────────────────────────────────────────────────────────
function initTabs() {
    const botoes = document.querySelectorAll('#veiculos-tabs .filtro-btn');
    botoes.forEach(btn => {
        btn.addEventListener('click', () => {
            botoes.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('.oc-tab-section').forEach(s => s.classList.remove('active'));
            const alvo = document.getElementById(`tab-${btn.dataset.tab}`);
            alvo.classList.add('active');
            if (btn.dataset.tab === 'pendentes') carregarPendentes();
            if (btn.dataset.tab === 'todos') carregarTodos();
            if (btn.dataset.tab === 'divergencias') carregarDivergencias();
        });
    });
}

// ─── Renderização de listas ─────────────────────────────────────────────
function renderLista(containerId, veiculos, { vazio, extra } = {}) {
    const el = document.getElementById(containerId);
    if (veiculos.length === 0) {
        el.innerHTML = `<div class="oc-vazio">${escapeHtml(vazio || 'Nenhum veículo.')}</div>`;
        return;
    }
    el.innerHTML = '';
    for (const v of veiculos) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'portaria-item';
        btn.style.marginBottom = '8px';
        const linhaExtra = extra ? extra(v) : '';
        btn.innerHTML = `
            <div>
                <div class="portaria-item-placa">${escapeHtml(v.placa)}</div>
                <div class="portaria-item-sub">${escapeHtml(donoTexto(v))}${linhaExtra}</div>
            </div>
            <div class="portaria-item-hora">${badgeSituacao(v.situacao)}</div>
        `;
        btn.addEventListener('click', () => abrirFicha(v.id));
        el.appendChild(btn);
    }
}

async function carregarPendentes() {
    try {
        const veiculos = await apiGet('/portaria/veiculos/pendentes');
        renderLista('lista-pendentes', veiculos, { vazio: 'Nenhum veículo pendente de autorização.' });
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('lista-pendentes').innerHTML =
            `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

function lerFiltrosTodos() {
    return {
        propriedade: document.getElementById('filtro-propriedade').value || null,
        situacao: document.getElementById('filtro-situacao').value || null,
        texto: document.getElementById('filtro-texto').value.trim().toLowerCase(),
    };
}

let todosCache = [];

async function carregarTodos() {
    const filtros = lerFiltrosTodos();
    const params = new URLSearchParams({ apenas_ativos: 'true', limit: '500' });
    if (filtros.propriedade) params.set('propriedade', filtros.propriedade);
    if (filtros.situacao) params.set('situacao', filtros.situacao);
    try {
        todosCache = await apiGet(`/portaria/veiculos?${params.toString()}`);
        renderTodosFiltrado();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('lista-todos').innerHTML =
            `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

function renderTodosFiltrado() {
    const { texto } = lerFiltrosTodos();
    const filtrados = texto
        ? todosCache.filter(v => `${v.placa} ${donoTexto(v)}`.toLowerCase().includes(texto))
        : todosCache;
    renderLista('lista-todos', filtrados, { vazio: 'Nenhum veículo encontrado.' });
}

function initFiltrosTodos() {
    document.getElementById('filtro-propriedade').addEventListener('change', carregarTodos);
    document.getElementById('filtro-situacao').addEventListener('change', carregarTodos);
    let handle = null;
    document.getElementById('filtro-texto').addEventListener('input', () => {
        clearTimeout(handle);
        handle = setTimeout(renderTodosFiltrado, 200);
    });
}

async function carregarDivergencias() {
    try {
        const veiculos = await apiGet('/portaria/veiculos/divergencias');
        renderLista('lista-divergencias', veiculos, {
            vazio: 'Nenhuma divergência — todo AUTORIZADO particular tem dono ATIVO.',
            extra: v => ` · status do funcionário: ${escapeHtml(v.funcionario_status)}`,
        });
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('lista-divergencias').innerHTML =
            `<div class="oc-vazio" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

// ─── Ficha do veículo ────────────────────────────────────────────────────
async function abrirFicha(veiculoId) {
    fichaVeiculoAtual = null;
    situacaoAlvo = null;
    document.getElementById('ficha-motivo-wrap').style.display = 'none';
    document.getElementById('ficha-motivo').value = '';
    document.getElementById('ficha-erro').style.display = 'none';
    document.getElementById('ficha-historico').innerHTML = '<div class="oc-vazio">Carregando…</div>';
    abrirModal('modal-ficha');

    try {
        const [veiculo, historico] = await Promise.all([
            apiGet(`/portaria/veiculos/${veiculoId}`),
            apiGet(`/portaria/veiculos/${veiculoId}/historico`),
        ]);
        fichaVeiculoAtual = veiculo;
        renderFicha(veiculo);
        renderHistorico(historico);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('ficha-erro').textContent = 'Erro ao carregar ficha: ' + err.message;
        document.getElementById('ficha-erro').style.display = 'block';
    }
}

function renderFicha(v) {
    document.getElementById('ficha-placa').textContent = v.placa;
    document.getElementById('ficha-tipo').textContent =
        [v.tipo, v.marca_modelo, v.cor].filter(Boolean).join(' · ') || '—';
    document.getElementById('ficha-dono').textContent = donoTexto(v);
    document.getElementById('ficha-situacao-badge').innerHTML = badgeSituacao(v.situacao);

    const detalhe = document.getElementById('ficha-situacao-detalhe');
    if (v.situacao === 'SUSPENSO' || v.situacao === 'BAIXADO') {
        const partes = [];
        if (v.situacao_em) partes.push(`desde ${fmtDataHora(v.situacao_em)}`);
        if (v.situacao_por_nome) partes.push(`por ${v.situacao_por_nome}`);
        if (v.situacao_motivo) partes.push(`— motivo: ${v.situacao_motivo}`);
        detalhe.textContent = partes.join(' ');
    } else {
        detalhe.textContent = '';
    }
    document.getElementById('ficha-observacao').textContent = v.observacao ? `Observação: ${v.observacao}` : '';

    const podeAutorizar = podeEscrever('autorizacao_veicular');
    document.getElementById('ficha-acoes').style.display = podeAutorizar ? 'block' : 'none';
    document.getElementById('btn-ficha-autorizar').disabled = v.situacao === 'AUTORIZADO';
    document.getElementById('btn-ficha-suspender').disabled = v.situacao === 'SUSPENSO';
    document.getElementById('btn-ficha-baixar').disabled = v.situacao === 'BAIXADO';
}

function renderHistorico(historico) {
    const el = document.getElementById('ficha-historico');
    if (historico.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Sem mudanças de situação registradas.</div>';
        return;
    }
    el.innerHTML = '';
    for (const h of historico) {
        const div = document.createElement('div');
        div.className = 'portaria-hist-item';
        const de = h.situacao_de ? badgeSituacao(h.situacao_de) : '<span class="portaria-ficha-linha">cadastro</span>';
        div.innerHTML = `
            <div>${de} → ${badgeSituacao(h.situacao_para)} <span class="portaria-ficha-linha">${fmtDataHora(h.decidido_em)}${h.decidido_por_nome ? ' · ' + escapeHtml(h.decidido_por_nome) : ''}</span></div>
            ${h.motivo ? `<div class="portaria-hist-motivo">${escapeHtml(h.motivo)}</div>` : ''}
        `;
        el.appendChild(div);
    }
}

function initFicha() {
    document.getElementById('fechar-ficha').addEventListener('click', () => fecharModal('modal-ficha'));

    document.getElementById('btn-ficha-autorizar').addEventListener('click', () => {
        // AUTORIZADO tem motivo opcional (D11) — confirma direto, sem sub-painel.
        confirmarMudancaSituacao('AUTORIZADO', null);
    });
    document.getElementById('btn-ficha-suspender').addEventListener('click', () => abrirPainelMotivo('SUSPENSO'));
    document.getElementById('btn-ficha-baixar').addEventListener('click', () => abrirPainelMotivo('BAIXADO'));
    document.getElementById('btn-ficha-cancelar-motivo').addEventListener('click', () => {
        situacaoAlvo = null;
        document.getElementById('ficha-motivo-wrap').style.display = 'none';
    });
    document.getElementById('btn-ficha-confirmar-situacao').addEventListener('click', () => {
        const motivo = document.getElementById('ficha-motivo').value.trim();
        if (!motivo) {
            document.getElementById('ficha-erro').textContent = 'Motivo é obrigatório.';
            document.getElementById('ficha-erro').style.display = 'block';
            return;
        }
        confirmarMudancaSituacao(situacaoAlvo, motivo);
    });
}

function abrirPainelMotivo(situacao) {
    situacaoAlvo = situacao;
    document.getElementById('ficha-motivo').value = '';
    document.getElementById('ficha-erro').style.display = 'none';
    document.getElementById('ficha-motivo-wrap').style.display = 'block';
}

async function confirmarMudancaSituacao(situacao, motivo) {
    if (!fichaVeiculoAtual) return;
    const erro = document.getElementById('ficha-erro');
    erro.style.display = 'none';
    try {
        const atualizado = await apiPatch(`/portaria/veiculos/${fichaVeiculoAtual.id}/situacao`, { situacao, motivo: motivo || null });
        fichaVeiculoAtual = atualizado;
        situacaoAlvo = null;
        document.getElementById('ficha-motivo-wrap').style.display = 'none';
        renderFicha(atualizado);
        const historico = await apiGet(`/portaria/veiculos/${atualizado.id}/historico`);
        renderHistorico(historico);
        atualizarAbaAtiva();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    }
}

function atualizarAbaAtiva() {
    const ativa = document.querySelector('#veiculos-tabs .filtro-btn.active')?.dataset.tab;
    if (ativa === 'pendentes') carregarPendentes();
    if (ativa === 'todos') carregarTodos();
    if (ativa === 'divergencias') carregarDivergencias();
}

// ─── Bloquear por RE (D12) — mostra a lista ANTES de confirmar ─────────
let bloqueioPreview = [];

function initBloquearPorRe() {
    document.getElementById('btn-bloquear-re').addEventListener('click', () => {
        document.getElementById('bloq-re').value = '';
        document.getElementById('bloq-motivo').value = '';
        document.getElementById('bloq-preview').innerHTML = '';
        document.getElementById('bloq-erro').style.display = 'none';
        document.getElementById('btn-bloq-buscar').style.display = 'block';
        document.getElementById('btn-bloq-confirmar').style.display = 'none';
        bloqueioPreview = [];
        abrirModal('modal-bloquear-re');
    });
    document.getElementById('fechar-bloquear-re').addEventListener('click', () => fecharModal('modal-bloquear-re'));
    document.getElementById('btn-bloq-cancelar').addEventListener('click', () => fecharModal('modal-bloquear-re'));
    document.getElementById('btn-bloq-buscar').addEventListener('click', buscarPreviewBloqueio);
    document.getElementById('btn-bloq-confirmar').addEventListener('click', confirmarBloqueioPorRe);
}

async function buscarPreviewBloqueio() {
    const erro = document.getElementById('bloq-erro');
    erro.style.display = 'none';
    const re = document.getElementById('bloq-re').value.trim();
    const motivo = document.getElementById('bloq-motivo').value.trim();
    if (!re) { erro.textContent = 'Digite o RE.'; erro.style.display = 'block'; return; }
    if (!motivo) { erro.textContent = 'Motivo é obrigatório.'; erro.style.display = 'block'; return; }

    try {
        // Só leitura (veiculo_portaria) — o preview não bloqueia nada
        // ainda. O POST /bloquear-por-re só roda quando a pessoa confirma
        // olhando pra esta lista.
        const veiculos = await apiGet('/portaria/veiculos?apenas_ativos=true&limit=500');
        bloqueioPreview = veiculos.filter(v => v.funcionario_re === re && v.situacao !== 'BAIXADO');
        const preview = document.getElementById('bloq-preview');
        if (bloqueioPreview.length === 0) {
            preview.innerHTML = '<div class="oc-vazio">Nenhum veículo ativo (não baixado) encontrado para este RE.</div>';
            document.getElementById('btn-bloq-confirmar').style.display = 'none';
            return;
        }
        preview.innerHTML = `<div class="portaria-ficha-linha" style="margin-bottom:8px">Serão suspensos:</div>` +
            bloqueioPreview.map(v => `
                <div class="portaria-linha-lista">
                    <span class="portaria-item-placa">${escapeHtml(v.placa)}</span>
                    ${badgeSituacao(v.situacao)}
                </div>
            `).join('');
        document.getElementById('btn-bloq-confirmar').style.display = 'block';
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    }
}

async function confirmarBloqueioPorRe() {
    const erro = document.getElementById('bloq-erro');
    erro.style.display = 'none';
    const re = document.getElementById('bloq-re').value.trim();
    const motivo = document.getElementById('bloq-motivo').value.trim();
    const btn = document.getElementById('btn-bloq-confirmar');
    btn.disabled = true;
    try {
        const resp = await apiPost('/portaria/veiculos/bloquear-por-re', { re, motivo });
        fecharModal('modal-bloquear-re');
        const partes = [`${resp.veiculos_suspensos.length} veículo(s) suspenso(s)`];
        if (resp.ja_suspensos.length > 0) partes.push(`${resp.ja_suspensos.length} já estava(m) suspenso(s)`);
        alert(`${resp.funcionario_nome}: ${partes.join(' · ')}.`);
        atualizarAbaAtiva();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Cadastrar veículo ────────────────────────────────────────────────
let donoResolvidoId = null;

function initNovoVeiculo() {
    document.getElementById('btn-novo-veiculo').addEventListener('click', () => {
        document.getElementById('nv-placa').value = '';
        document.getElementById('nv-propriedade').value = 'PARTICULAR';
        document.getElementById('nv-re-dono').value = '';
        document.getElementById('nv-dono-nome').textContent = '';
        document.getElementById('nv-tipo').value = 'CARRO';
        document.getElementById('nv-cor').value = '';
        document.getElementById('nv-marca-modelo').value = '';
        document.getElementById('novo-veiculo-erro').style.display = 'none';
        donoResolvidoId = null;
        atualizarCamposPropriedadeNv();
        abrirModal('modal-novo-veiculo');
    });
    document.getElementById('fechar-novo-veiculo').addEventListener('click', () => fecharModal('modal-novo-veiculo'));
    document.getElementById('btn-cancelar-novo-veiculo').addEventListener('click', () => fecharModal('modal-novo-veiculo'));
    document.getElementById('nv-propriedade').addEventListener('change', atualizarCamposPropriedadeNv);

    let handle = null;
    document.getElementById('nv-re-dono').addEventListener('input', () => {
        clearTimeout(handle);
        handle = setTimeout(resolverDonoPorRe, 250);
    });

    document.getElementById('btn-salvar-novo-veiculo').addEventListener('click', salvarNovoVeiculo);
}

function atualizarCamposPropriedadeNv() {
    const prop = document.getElementById('nv-propriedade').value;
    document.getElementById('nv-dono-wrap').style.display = prop === 'PARTICULAR' ? 'block' : 'none';
    document.getElementById('nv-empresa-wrap').style.display = prop === 'TERCEIRO' ? 'block' : 'none';
    if (prop === 'TERCEIRO') preencherSelectEmpresas('nv-empresa');
}

async function resolverDonoPorRe() {
    const re = document.getElementById('nv-re-dono').value.trim();
    const nomeEl = document.getElementById('nv-dono-nome');
    donoResolvidoId = null;
    nomeEl.textContent = '';
    if (re.length < 2) return;
    try {
        const resultados = await apiGet(`/portaria/funcionarios/busca?q=${encodeURIComponent(re)}`);
        const exato = resultados.find(f => f.re === re);
        if (exato) {
            donoResolvidoId = exato.id;
            nomeEl.textContent = exato.nome;
            nomeEl.style.color = 'var(--accent3)';
        } else if (resultados.length > 0) {
            nomeEl.textContent = `${resultados.length} funcionário(s) encontrados — digite o RE completo`;
            nomeEl.style.color = 'var(--muted)';
        } else {
            nomeEl.textContent = 'Nenhum funcionário encontrado com este RE';
            nomeEl.style.color = 'var(--accent)';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-veiculos] erro ao resolver dono por RE:', err);
    }
}

async function salvarNovoVeiculo() {
    const erro = document.getElementById('novo-veiculo-erro');
    erro.style.display = 'none';
    const placa = document.getElementById('nv-placa').value.trim();
    const propriedade = document.getElementById('nv-propriedade').value;
    if (!placa) { erro.textContent = 'Digite a placa.'; erro.style.display = 'block'; return; }

    const payload = {
        propriedade,
        placa,
        tipo: document.getElementById('nv-tipo').value,
        marca_modelo: document.getElementById('nv-marca-modelo').value.trim() || null,
        cor: document.getElementById('nv-cor').value.trim() || null,
    };
    if (propriedade === 'PARTICULAR') {
        if (!donoResolvidoId) {
            erro.textContent = 'Informe um RE válido pra resolver o dono.';
            erro.style.display = 'block';
            return;
        }
        payload.funcionario_id = donoResolvidoId;
    } else if (propriedade === 'TERCEIRO') {
        const empresaId = document.getElementById('nv-empresa').value;
        if (!empresaId) { erro.textContent = 'Selecione a empresa terceira.'; erro.style.display = 'block'; return; }
        payload.empresa_terceira_id = empresaId;
    }

    const btn = document.getElementById('btn-salvar-novo-veiculo');
    btn.disabled = true;
    try {
        await apiPost('/portaria/veiculos', payload);
        fecharModal('modal-novo-veiculo');
        atualizarAbaAtiva();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Cadastrar empresa terceira ─────────────────────────────────────────
function initNovaEmpresa() {
    document.getElementById('btn-nova-empresa').addEventListener('click', () => {
        document.getElementById('ne-nome').value = '';
        document.getElementById('ne-cnpj').value = '';
        document.getElementById('ne-observacao').value = '';
        document.getElementById('nova-empresa-erro').style.display = 'none';
        abrirModal('modal-nova-empresa');
    });
    document.getElementById('fechar-nova-empresa').addEventListener('click', () => fecharModal('modal-nova-empresa'));
    document.getElementById('btn-cancelar-nova-empresa').addEventListener('click', () => fecharModal('modal-nova-empresa'));
    document.getElementById('btn-salvar-nova-empresa').addEventListener('click', salvarNovaEmpresa);
}

async function salvarNovaEmpresa() {
    const erro = document.getElementById('nova-empresa-erro');
    erro.style.display = 'none';
    const nome = document.getElementById('ne-nome').value.trim();
    if (!nome) { erro.textContent = 'Digite o nome.'; erro.style.display = 'block'; return; }

    const btn = document.getElementById('btn-salvar-nova-empresa');
    btn.disabled = true;
    try {
        await apiPost('/portaria/empresas', {
            nome,
            cnpj: document.getElementById('ne-cnpj').value.trim() || null,
            observacao: document.getElementById('ne-observacao').value.trim() || null,
        });
        empresasCache = null; // força recarregar na próxima abertura de select
        fecharModal('modal-nova-empresa');
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
initHeader();
aplicarPermissoes();
initTabs();
initFiltrosTodos();
initFicha();
initBloquearPorRe();
initNovoVeiculo();
initNovaEmpresa();
carregarPendentes();
