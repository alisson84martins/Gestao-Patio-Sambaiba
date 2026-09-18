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
import { apiGet, apiPatch, apiPost, apiDelete, ApiError } from './api.js';
import { podeEscrever } from './sessao.js';
import { escapeHtml } from './escape.js';
import { API_BASE_URL, TOKEN_KEY } from './config.js';
import { aplicarMascara } from './mascaras.js';
import { preencherSelectEmpresas, cadastrarEmpresa } from './portaria-empresas.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

// P1 — qual select retomar quando #modal-nova-empresa salva com sucesso.
let novaEmpresaRetorno = null;
let fichaVeiculoAtual = null; // VeiculoRead do modal de ficha aberto no momento
let situacaoAlvo = null;      // 'AUTORIZADO' | 'SUSPENSO' | 'BAIXADO' — ação pendente de motivo

// Bloco E — QR do veículo
const selecionados = new Set();  // ids marcados na aba Todos, pra imprimir etiquetas
let credencialAtual = null;      // CredencialRead ativa da ficha aberta, ou null
let credencialImgUrl = null;     // blob: URL da última imagem de QR — revogar antes de trocar

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

// D18 — badge de propriedade no item da lista (classes de portaria-badge já
// existentes, neutras — a cor que importa é a de situação).
function badgePropriedade(propriedade) {
    const mapa = { PARTICULAR: 'Particular', EMPRESA: 'Frota', TERCEIRO: 'Terceiro' };
    const label = mapa[propriedade] || propriedade;
    return `<span class="portaria-badge portaria-badge-propriedade">${escapeHtml(label)}</span>`;
}

// Complemento à base limpa de placa (migration 031): nunca barra o
// cadastro, só sinaliza pra revisão manual depois — ver filtro da aba Todos.
function badgeAtipica(v) {
    return v.placa_atipica
        ? ' <span class="portaria-badge portaria-badge-pendente" title="Placa fora do padrão AAA-0000/Mercosul — cadastrada assim mesmo, revisar depois">Placa atípica</span>'
        : '';
}

// Gestão de cadastro (17/09/2026) — ativo=false só existe hoje por conserto
// manual (SQL direto, caso do guincho) ou, daqui pra frente, por decisão
// futura fora deste bloco; aqui só sinaliza pra quem pode reativar achar.
function badgeInativo(v) {
    return v.ativo
        ? ''
        : ' <span class="portaria-badge portaria-badge-baixado" title="ativo=false — reative em Gestão de cadastro (ADMIN), na ficha">Inativo</span>';
}

function donoTexto(v) {
    if (v.propriedade === 'TERCEIRO') return v.empresa_terceira_nome || 'Terceiro';
    // D18 — "Frota · MOTO" em vez do genérico "Veículo da empresa", mesmo
    // rótulo de renderEstadoConfirmacao() em portaria.page.js.
    if (v.propriedade === 'EMPRESA') return `Frota · ${v.tipo}`;
    if (v.funcionario_nome) return v.funcionario_re ? `${v.funcionario_nome} · RE ${v.funcionario_re}` : v.funcionario_nome;
    // C1 (migration 039): RE digitado que não resolveu — regra número um,
    // o cadastro não foi recusado, mas o dono ainda não é funcionário.
    if (v.re_dono_texto) return `RE ${v.re_dono_texto} (não cadastrado)`;
    return '—';
}

// ─── Permissões (só decoram a UI — a trava real é o backend) ───────────
function aplicarPermissoes() {
    const podeAutorizar = podeEscrever('autorizacao_veicular');
    const podeCadastrar = podeEscrever('veiculo_portaria');
    document.getElementById('btn-bloquear-re').style.display = podeAutorizar ? 'block' : 'none';
    document.getElementById('btn-novo-veiculo').style.display = podeCadastrar ? 'block' : 'none';
    document.getElementById('btn-nova-empresa').style.display = podeCadastrar ? 'block' : 'none';
    // Gestão de cadastro (17/09/2026) — mesmo recurso ("usuarios" escrever)
    // que já restringe Cadastros e Permissões a ADMIN (GerenciaUsuarios no
    // backend). Sem este filtro não há caminho pela tela até um veículo
    // ativo=false (carregarTodos() sempre mandava apenas_ativos=true).
    document.getElementById('filtro-mostrar-inativos-wrap').style.display =
        podeEscrever('usuarios') ? 'flex' : 'none';
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
            if (btn.dataset.tab !== 'todos') {
                // Seleção de etiquetas (Bloco E) só faz sentido na aba Todos.
                selecionados.clear();
                atualizarBarraImpressao();
            }
            if (btn.dataset.tab === 'pendentes') carregarPendentes();
            if (btn.dataset.tab === 'todos') carregarTodos();
            if (btn.dataset.tab === 'divergencias') carregarDivergencias();
        });
    });
}

// ─── Renderização de listas ─────────────────────────────────────────────
// `selecionavel` liga a caixa de marcação usada só na aba Todos, pra juntar
// veículos e imprimir as etiquetas de QR deles de uma vez (Bloco E).
function renderLista(containerId, veiculos, { vazio, extra, selecionavel } = {}) {
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
        const checkboxHtml = selecionavel
            ? `<input type="checkbox" class="portaria-item-check" ${selecionados.has(v.id) ? 'checked' : ''}>`
            : '';
        btn.innerHTML = `
            ${checkboxHtml}
            <div>
                <div class="portaria-item-placa">${escapeHtml(v.placa)}</div>
                <div class="portaria-item-sub">${escapeHtml(donoTexto(v))}${linhaExtra}</div>
            </div>
            <div class="portaria-item-hora">${badgePropriedade(v.propriedade)} ${badgeSituacao(v.situacao)}</div>
        `;
        btn.addEventListener('click', (e) => {
            if (e.target.classList.contains('portaria-item-check')) return;
            abrirFicha(v.id);
        });
        if (selecionavel) {
            const check = btn.querySelector('.portaria-item-check');
            check.addEventListener('click', (e) => e.stopPropagation());
            check.addEventListener('change', (e) => {
                if (e.target.checked) selecionados.add(v.id); else selecionados.delete(v.id);
                atualizarBarraImpressao();
            });
        }
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
        placaAtipica: document.getElementById('filtro-placa-atipica').checked,
    };
}

let todosCache = [];

async function carregarTodos() {
    const filtros = lerFiltrosTodos();
    // Gestão de cadastro (17/09/2026) — só ADMIN vê a caixa (aplicarPermissoes
    // esconde pros demais); pra eles o checkbox nunca existe marcado, então
    // o comportamento de sempre (só ativos) não muda.
    const mostrarInativos = document.getElementById('filtro-mostrar-inativos').checked;
    const params = new URLSearchParams({ apenas_ativos: mostrarInativos ? 'false' : 'true', limit: '500' });
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
    const { texto, placaAtipica } = lerFiltrosTodos();
    let filtrados = texto
        ? todosCache.filter(v => `${v.placa} ${donoTexto(v)}`.toLowerCase().includes(texto))
        : todosCache;
    if (placaAtipica) filtrados = filtrados.filter(v => v.placa_atipica);
    renderLista('lista-todos', filtrados, {
        vazio: 'Nenhum veículo encontrado.',
        // P11 do PROMPT-leitura-placa.md (Bloco 4, 04/09) — seleção
        // múltipla pra imprimir etiquetas de QR escondida junto com o QR
        // da ficha. barra-imprimir-etiquetas nunca aparece sem checkbox
        // nenhuma marcada; endpoint de etiquetas (abrirEtiquetas) intacto.
        selecionavel: false,
        extra: v => badgeAtipica(v) + badgeInativo(v),
    });
}

function initFiltrosTodos() {
    document.getElementById('filtro-propriedade').addEventListener('change', carregarTodos);
    document.getElementById('filtro-situacao').addEventListener('change', carregarTodos);
    document.getElementById('filtro-placa-atipica').addEventListener('change', renderTodosFiltrado);
    // apenas_ativos é parâmetro de servidor (não dá pra filtrar client-side
    // como placa_atipica) — precisa de um carregarTodos() novo, não só um
    // re-render.
    document.getElementById('filtro-mostrar-inativos').addEventListener('change', carregarTodos);
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
            vazio: 'Nenhuma divergência — todo AUTORIZADO particular tem dono ATIVO e cadastrado.',
            // C1 (migration 039): NAO_CADASTRADO é o RE digitado (re_dono_texto)
            // que nunca virou funcionário — motivo diferente do funcionário inativo.
            extra: v => v.funcionario_status === 'NAO_CADASTRADO'
                ? ' · dono ainda não é funcionário (RE não promovido — ver Pré-cadastros)'
                : ` · status do funcionário: ${escapeHtml(v.funcionario_status)}`,
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
        await carregarCredencial(veiculoId);
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
    document.getElementById('ficha-situacao-badge').innerHTML = badgeSituacao(v.situacao) + badgeAtipica(v) + badgeInativo(v);

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

    // Gestão de cadastro (17/09/2026) — só ADMIN (mesma trava de
    // filtro-mostrar-inativos-wrap acima). Reativar só faz sentido pro
    // veículo que já está ativo=false.
    const souAdmin = podeEscrever('usuarios');
    document.getElementById('ficha-gestao-admin').style.display = souAdmin ? 'block' : 'none';
    document.getElementById('btn-ficha-reativar').style.display = (souAdmin && !v.ativo) ? 'block' : 'none';
    // Item 3 (18/09/2026) — só faz sentido pra PARTICULAR ainda sem
    // funcionario_id (dono só por re_dono_texto provisório).
    document.getElementById('btn-ficha-completar-dono').style.display =
        (souAdmin && v.propriedade === 'PARTICULAR' && !v.funcionario_id) ? 'block' : 'none';
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

// ─── QR do veículo (Bloco E) ─────────────────────────────────────────────
// "Gerar QR"/"Reemitir" usam o recurso `veiculo_portaria` (mesmo do
// cadastro) — emitir/revogar credencial é ato de quem cuida do cadastro,
// não de autorização.

// Rota protegida por Bearer — <img src> puro não manda o header, então
// busca o SVG via fetch autenticado e converte pra blob: URL (mesmo padrão
// de ocorrencia.form.js/baixarAnexo).
async function buscarImagemQr(veiculoId) {
    const token = localStorage.getItem(TOKEN_KEY);
    try {
        const resp = await fetch(`${API_BASE_URL}/portaria/veiculos/${veiculoId}/credencial.svg`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!resp.ok) return null;
        const blob = await resp.blob();
        return URL.createObjectURL(blob);
    } catch (err) {
        console.error('[portaria-veiculos] erro ao buscar imagem do QR:', err);
        return null;
    }
}

async function carregarCredencial(veiculoId) {
    try {
        credencialAtual = await apiGet(`/portaria/veiculos/${veiculoId}/credencial`);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-veiculos] erro ao carregar credencial:', err);
        credencialAtual = null;
    }
    await renderCredencial();
}

async function renderCredencial() {
    // P11 do PROMPT-leitura-placa.md (Bloco 4, 04/09) — o QR sai da TELA,
    // não do banco: endpoint, tabela portaria.credencial e etiquetas
    // continuam intactos (emitirCredencial/buscarImagemQr/initFichaQr
    // abaixo, código dormindo não custa nada), só a seção da ficha fica
    // escondida. A câmera (Bloco 1/3) substitui esta aceleração.
    document.getElementById('ficha-qr-wrap').style.display = 'none';
}

async function abrirEtiquetas(ids) {
    const token = localStorage.getItem(TOKEN_KEY);
    try {
        const resp = await fetch(`${API_BASE_URL}/portaria/credenciais/etiquetas?ids=${encodeURIComponent(ids)}`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!resp.ok) throw new Error('Falha ao gerar etiquetas');
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
        alert('Não foi possível abrir as etiquetas: ' + err.message);
    }
}

function initFichaQr() {
    document.getElementById('btn-gerar-qr').addEventListener('click', () => emitirCredencial(null));
    document.getElementById('btn-imprimir-qr-ficha').addEventListener('click', () => {
        if (!fichaVeiculoAtual) return;
        abrirEtiquetas(fichaVeiculoAtual.id);
    });
    document.getElementById('btn-reemitir-qr').addEventListener('click', () => {
        document.getElementById('ficha-qr-motivo').value = '';
        document.getElementById('ficha-qr-erro').style.display = 'none';
        document.getElementById('ficha-qr-motivo-wrap').style.display = 'block';
    });
    document.getElementById('btn-cancelar-qr-motivo').addEventListener('click', () => {
        document.getElementById('ficha-qr-motivo-wrap').style.display = 'none';
    });
    document.getElementById('btn-confirmar-qr-motivo').addEventListener('click', () => {
        const motivo = document.getElementById('ficha-qr-motivo').value.trim();
        if (!motivo) {
            document.getElementById('ficha-qr-erro').textContent = 'Motivo é obrigatório para reemitir.';
            document.getElementById('ficha-qr-erro').style.display = 'block';
            return;
        }
        emitirCredencial(motivo);
    });
}

async function emitirCredencial(motivo) {
    if (!fichaVeiculoAtual) return;
    const erro = document.getElementById('ficha-qr-erro');
    erro.style.display = 'none';
    try {
        credencialAtual = await apiPost(`/portaria/veiculos/${fichaVeiculoAtual.id}/credencial`, { motivo: motivo || null });
        document.getElementById('ficha-qr-motivo-wrap').style.display = 'none';
        await renderCredencial();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    }
}

// ─── Seleção múltipla + impressão de etiquetas (Bloco E) ────────────────
function atualizarBarraImpressao() {
    const barra = document.getElementById('barra-imprimir-etiquetas');
    document.getElementById('qtd-selecionados').textContent = String(selecionados.size);
    barra.style.display = selecionados.size > 0 ? 'block' : 'none';
}

function initImprimirEtiquetas() {
    document.getElementById('btn-imprimir-etiquetas').addEventListener('click', () => {
        if (selecionados.size === 0) return;
        abrirEtiquetas(Array.from(selecionados).join(','));
    });
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
    // A1: máscara + aviso visual, nunca bloqueia (D10) — placa fora do
    // padrão ainda cadastra, só nasce com placa_atipica=true (ver
    // renderLista/badgeAtipica e o filtro da aba Todos).
    aplicarMascara(document.getElementById('nv-placa'), 'placa');
    document.getElementById('btn-novo-veiculo').addEventListener('click', () => {
        document.getElementById('nv-placa').value = '';
        document.getElementById('nv-propriedade').value = 'PARTICULAR';
        document.getElementById('nv-re-dono').value = '';
        document.getElementById('nv-dono-nome').textContent = '';
        document.getElementById('nv-dono-auto-aviso').style.display = 'none';
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

    // P1 — "+ Nova" resolve sem sair do modal (a empresa continua
    // obrigatória pra TERCEIRO, o CHECK exige — só o cadastro da empresa
    // deixa de exigir trocar de tela).
    document.getElementById('btn-nv-nova-empresa').style.display = '';
    document.getElementById('btn-nv-nova-empresa').addEventListener('click', () => {
        abrirNovaEmpresa({ selectId: 'nv-empresa', modalParaReabrir: 'modal-novo-veiculo' });
    });
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
    const avisoEl = document.getElementById('nv-dono-auto-aviso');
    donoResolvidoId = null;
    nomeEl.textContent = '';
    avisoEl.style.display = 'none';
    if (re.length < 2) return;
    try {
        const resultados = await apiGet(`/portaria/funcionarios/busca?q=${encodeURIComponent(re)}`);
        const exato = resultados.find(f => f.re === re);
        if (exato) {
            donoResolvidoId = exato.id;
            nomeEl.textContent = exato.nome;
            nomeEl.style.color = 'var(--accent3)';
            // Bloco D: dono com função de gestão (funcao.veiculo_auto_autorizado)
            // não passa por PENDENTE — avisar antes de salvar.
            avisoEl.style.display = exato.auto_autorizado ? 'block' : 'none';
        } else if (resultados.length > 0) {
            nomeEl.textContent = `${resultados.length} funcionário(s) encontrados — digite o RE completo`;
            nomeEl.style.color = 'var(--muted)';
        } else {
            // C1 (migration 039): RE não encontrado NÃO bloqueia — regra
            // número um. salvarNovoVeiculo manda esse RE em re_dono_texto.
            nomeEl.textContent = 'RE não encontrado no cadastro. O veículo será cadastrado assim mesmo e ficará em Divergências.';
            nomeEl.style.color = 'var(--muted)';
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
        const reDono = document.getElementById('nv-re-dono').value.trim();
        if (!donoResolvidoId && !reDono) {
            erro.textContent = 'Informe o RE do dono.';
            erro.style.display = 'block';
            return;
        }
        // C1 (migration 039): RE que não resolveu não bloqueia — vai como
        // re_dono_texto (snapshot) e o veículo fica na fila de Divergências
        // até alguém promover essa pessoa a funcionário.
        if (donoResolvidoId) {
            payload.funcionario_id = donoResolvidoId;
        } else {
            payload.re_dono_texto = reDono;
        }
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
// P1 — mesmo desenho de portaria.page.js: "+ Empresa" do topo cadastra
// solto (selectId/modalParaReabrir nulos); "+ Nova" de dentro do cadastro
// de veículo (#nv-empresa) fecha modal-novo-veiculo, cadastra, e volta com
// a empresa nova já selecionada.
function initNovaEmpresa() {
    document.getElementById('btn-nova-empresa').addEventListener('click', () => abrirNovaEmpresa());
    document.getElementById('fechar-nova-empresa').addEventListener('click', fecharNovaEmpresaEVoltar);
    document.getElementById('btn-cancelar-nova-empresa').addEventListener('click', fecharNovaEmpresaEVoltar);
    document.getElementById('btn-salvar-nova-empresa').addEventListener('click', salvarNovaEmpresa);
}

function fecharNovaEmpresaEVoltar() {
    fecharModal('modal-nova-empresa');
    if (novaEmpresaRetorno && novaEmpresaRetorno.modalParaReabrir) abrirModal(novaEmpresaRetorno.modalParaReabrir);
    novaEmpresaRetorno = null;
}

function abrirNovaEmpresa({ selectId = null, modalParaReabrir = null } = {}) {
    novaEmpresaRetorno = { selectId, modalParaReabrir };
    document.getElementById('ne-nome').value = '';
    document.getElementById('ne-cnpj').value = '';
    document.getElementById('ne-observacao').value = '';
    document.getElementById('nova-empresa-erro').style.display = 'none';
    if (modalParaReabrir) fecharModal(modalParaReabrir);
    abrirModal('modal-nova-empresa');
}

async function salvarNovaEmpresa() {
    const erro = document.getElementById('nova-empresa-erro');
    erro.style.display = 'none';
    const nome = document.getElementById('ne-nome').value.trim();
    if (!nome) { erro.textContent = 'Digite o nome.'; erro.style.display = 'block'; return; }

    const btn = document.getElementById('btn-salvar-nova-empresa');
    btn.disabled = true;
    try {
        const nova = await cadastrarEmpresa({
            nome,
            cnpj: document.getElementById('ne-cnpj').value.trim() || null,
            observacao: document.getElementById('ne-observacao').value.trim() || null,
        });
        fecharModal('modal-nova-empresa');
        if (novaEmpresaRetorno && novaEmpresaRetorno.selectId) {
            const { selectId, modalParaReabrir } = novaEmpresaRetorno;
            await preencherSelectEmpresas(selectId);
            document.getElementById(selectId).value = nova.id;
            if (modalParaReabrir) abrirModal(modalParaReabrir);
        }
        novaEmpresaRetorno = null;
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Gestão de cadastro — ADMIN (17/09/2026) ────────────────────────────
// Editar cadastro completo (inclusive propriedade/dono), excluir de
// verdade e reativar. Nasceu de um caso real: guincho da frota cadastrado
// como PARTICULAR sem jeito de corrigir pela API, e "Baixar" não liberava
// a placa porque só troca `situacao`, nunca `ativo`.
let eaVeiculoId = null;
let eaDonoResolvidoId = null;

function initGestaoAdmin() {
    document.getElementById('btn-ficha-editar-admin').addEventListener('click', abrirEditarAdmin);
    document.getElementById('fechar-editar-admin').addEventListener('click', fecharEditarAdminEVoltar);
    document.getElementById('btn-cancelar-editar-admin').addEventListener('click', fecharEditarAdminEVoltar);
    document.getElementById('ea-propriedade').addEventListener('change', atualizarCamposPropriedadeEa);
    let handle = null;
    document.getElementById('ea-re-dono').addEventListener('input', () => {
        clearTimeout(handle);
        handle = setTimeout(resolverDonoEa, 250);
    });
    document.getElementById('btn-salvar-editar-admin').addEventListener('click', salvarEditarAdmin);

    document.getElementById('btn-ficha-reativar').addEventListener('click', reativarVeiculoAtual);

    document.getElementById('btn-ficha-excluir').addEventListener('click', abrirExcluirAdmin);
    document.getElementById('fechar-excluir-admin').addEventListener('click', () => fecharModal('modal-excluir-admin'));
    document.getElementById('btn-cancelar-excluir-admin').addEventListener('click', () => fecharModal('modal-excluir-admin'));
    document.getElementById('excluir-admin-ciente').addEventListener('change', (e) => {
        document.getElementById('btn-confirmar-excluir-admin').disabled = !e.target.checked;
    });
    document.getElementById('btn-confirmar-excluir-admin').addEventListener('click', confirmarExcluirAdmin);
}

function abrirEditarAdmin() {
    if (!fichaVeiculoAtual) return;
    const v = fichaVeiculoAtual;
    eaVeiculoId = v.id;
    eaDonoResolvidoId = v.funcionario_id || null;
    document.getElementById('ea-placa').value = v.placa;
    document.getElementById('ea-propriedade').value = v.propriedade;
    document.getElementById('ea-re-dono').value = v.funcionario_re || v.re_dono_texto || '';
    document.getElementById('ea-dono-nome').textContent = v.funcionario_nome || '';
    document.getElementById('ea-dono-nome').style.color = 'var(--muted)';
    document.getElementById('ea-tipo').value = v.tipo;
    document.getElementById('ea-cor').value = v.cor || '';
    document.getElementById('ea-marca-modelo').value = v.marca_modelo || '';
    document.getElementById('editar-admin-erro').style.display = 'none';
    atualizarCamposPropriedadeEa();
    if (v.propriedade === 'TERCEIRO' && v.empresa_terceira_id) {
        preencherSelectEmpresas('ea-empresa').then(() => {
            document.getElementById('ea-empresa').value = v.empresa_terceira_id;
        });
    }
    fecharModal('modal-ficha');
    abrirModal('modal-editar-admin');
}

function fecharEditarAdminEVoltar() {
    fecharModal('modal-editar-admin');
    if (eaVeiculoId) abrirFicha(eaVeiculoId);
}

function atualizarCamposPropriedadeEa() {
    const prop = document.getElementById('ea-propriedade').value;
    document.getElementById('ea-dono-wrap').style.display = prop === 'PARTICULAR' ? 'block' : 'none';
    document.getElementById('ea-empresa-wrap').style.display = prop === 'TERCEIRO' ? 'block' : 'none';
    if (prop === 'TERCEIRO') preencherSelectEmpresas('ea-empresa');
}

// Mesma lógica de resolverDonoPorRe (cadastro), duplicada de propósito: são
// dois formulários/modais independentes (cadastrar × corrigir), e um RE
// resolvido num nunca deveria vazar estado pro outro.
async function resolverDonoEa() {
    const re = document.getElementById('ea-re-dono').value.trim();
    const nomeEl = document.getElementById('ea-dono-nome');
    eaDonoResolvidoId = null;
    nomeEl.textContent = '';
    if (re.length < 2) return;
    try {
        const resultados = await apiGet(`/portaria/funcionarios/busca?q=${encodeURIComponent(re)}`);
        const exato = resultados.find(f => f.re === re);
        if (exato) {
            eaDonoResolvidoId = exato.id;
            nomeEl.textContent = exato.nome;
            nomeEl.style.color = 'var(--accent3)';
        } else if (resultados.length > 0) {
            nomeEl.textContent = `${resultados.length} funcionário(s) encontrados — digite o RE completo`;
            nomeEl.style.color = 'var(--muted)';
        } else {
            nomeEl.textContent = 'RE não encontrado no cadastro. Fica salvo como texto (re_dono_texto).';
            nomeEl.style.color = 'var(--muted)';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-veiculos] erro ao resolver dono (admin):', err);
    }
}

async function salvarEditarAdmin() {
    const erro = document.getElementById('editar-admin-erro');
    erro.style.display = 'none';
    const placa = document.getElementById('ea-placa').value.trim();
    const propriedade = document.getElementById('ea-propriedade').value;
    if (!placa) { erro.textContent = 'Digite a placa.'; erro.style.display = 'block'; return; }

    const payload = {
        propriedade,
        placa,
        tipo: document.getElementById('ea-tipo').value,
        marca_modelo: document.getElementById('ea-marca-modelo').value.trim() || null,
        cor: document.getElementById('ea-cor').value.trim() || null,
    };
    if (propriedade === 'PARTICULAR') {
        const reDono = document.getElementById('ea-re-dono').value.trim();
        if (!eaDonoResolvidoId && !reDono) {
            erro.textContent = 'Informe o RE do dono.';
            erro.style.display = 'block';
            return;
        }
        // Mesma regra número um do cadastro normal (C1, migration 039): RE
        // que não resolveu não bloqueia a correção, vira re_dono_texto.
        if (eaDonoResolvidoId) {
            payload.funcionario_id = eaDonoResolvidoId;
        } else {
            payload.re_dono_texto = reDono;
        }
    } else if (propriedade === 'TERCEIRO') {
        const empresaId = document.getElementById('ea-empresa').value;
        if (!empresaId) { erro.textContent = 'Selecione a empresa terceira.'; erro.style.display = 'block'; return; }
        payload.empresa_terceira_id = empresaId;
    }
    // EMPRESA: nem funcionario_id nem empresa_terceira_id vão no payload —
    // ambos ficam ausentes (backend recebe None nos dois, exatamente o que
    // ck_veiculo_dono exige pro guincho da frota).

    const btn = document.getElementById('btn-salvar-editar-admin');
    btn.disabled = true;
    try {
        await apiPatch(`/portaria/veiculos/${eaVeiculoId}/cadastro-admin`, payload);
        fecharModal('modal-editar-admin');
        await abrirFicha(eaVeiculoId);
        atualizarAbaAtiva();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

async function reativarVeiculoAtual() {
    if (!fichaVeiculoAtual) return;
    if (!confirm(`Reativar o veículo placa ${fichaVeiculoAtual.placa}?`)) return;
    const erro = document.getElementById('ficha-erro');
    erro.style.display = 'none';
    try {
        const atualizado = await apiPost(`/portaria/veiculos/${fichaVeiculoAtual.id}/reativar`);
        fichaVeiculoAtual = atualizado;
        renderFicha(atualizado);
        atualizarAbaAtiva();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        // 409 (placa em uso por outro veículo ativo) chega com mensagem
        // pronta pra mostrar — ver reativar_veiculo no backend.
        erro.textContent = err.message;
        erro.style.display = 'block';
    }
}

async function abrirExcluirAdmin() {
    if (!fichaVeiculoAtual) return;
    document.getElementById('excluir-admin-ciente').checked = false;
    document.getElementById('btn-confirmar-excluir-admin').disabled = true;
    document.getElementById('excluir-admin-erro').style.display = 'none';
    document.getElementById('excluir-admin-impacto').textContent = 'Calculando o que será apagado…';
    abrirModal('modal-excluir-admin');
    try {
        // Trava obrigatória (decisão do Alisson, 17/09/2026): a tela NUNCA
        // chama o DELETE sem mostrar antes quanto vai junto.
        const impacto = await apiGet(`/portaria/veiculos/${fichaVeiculoAtual.id}/exclusao`);
        document.getElementById('excluir-admin-impacto').textContent =
            `Serão apagados junto: ${impacto.movimentos} movimento(s), ${impacto.credenciais} credencial(is), `
            + `${impacto.historico_situacao} registro(s) de histórico de situação.`;
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('excluir-admin-impacto').textContent = '';
        document.getElementById('excluir-admin-erro').textContent = 'Erro ao calcular impacto: ' + err.message;
        document.getElementById('excluir-admin-erro').style.display = 'block';
    }
}

async function confirmarExcluirAdmin() {
    if (!fichaVeiculoAtual) return;
    const erro = document.getElementById('excluir-admin-erro');
    erro.style.display = 'none';
    const btn = document.getElementById('btn-confirmar-excluir-admin');
    btn.disabled = true;
    try {
        await apiDelete(`/portaria/veiculos/${fichaVeiculoAtual.id}?confirmar=true`);
        fecharModal('modal-excluir-admin');
        fecharModal('modal-ficha');
        atualizarAbaAtiva();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
        btn.disabled = false;
    }
}

// ─── Completar dono (ADMIN, Item 3 — 18/09/2026) ────────────────────────
// Fecha a lacuna que promover_pre_cadastro deixava aberta: o veículo com
// RE provisório (re_dono_texto) nunca virava dono de verdade sozinho.
// Sempre UM carro por chamada — o Alisson confere a placa física antes de
// ligar mais de um veículo com o mesmo RE provisório.
let cdVeiculoId = null;
let cdReResolvidoNome = null; // nome do funcionário já cadastrado com o RE digitado, ou null
let cdReHandle = null;
let cdNomeHandle = null;

function abrirCompletarDono() {
    if (!fichaVeiculoAtual) return;
    const v = fichaVeiculoAtual;
    cdVeiculoId = v.id;
    cdReResolvidoNome = null;
    document.getElementById('cd-placa').textContent = v.placa;
    document.getElementById('cd-re-provisorio').textContent = v.re_dono_texto
        ? `RE provisório atual: ${v.re_dono_texto}`
        : 'Sem RE provisório registrado.';
    document.getElementById('cd-re').value = '';
    document.getElementById('cd-nome').value = '';
    document.getElementById('cd-re-status').textContent = '';
    document.getElementById('cd-candidatos').innerHTML = '';
    document.getElementById('cd-erro').style.display = 'none';
    atualizarBotaoCompletarDono();
    fecharModal('modal-ficha');
    abrirModal('modal-completar-dono');
}

function fecharCompletarDonoEVoltar() {
    fecharModal('modal-completar-dono');
    if (cdVeiculoId) abrirFicha(cdVeiculoId);
}

function atualizarBotaoCompletarDono() {
    document.getElementById('btn-salvar-completar-dono').textContent =
        cdReResolvidoNome ? 'Completar dono' : 'Cadastrar pessoa e ligar';
}

async function resolverReCompletarDono() {
    const re = document.getElementById('cd-re').value.trim();
    const statusEl = document.getElementById('cd-re-status');
    cdReResolvidoNome = null;
    statusEl.textContent = '';
    if (re.length >= 2) {
        try {
            const resultados = await apiGet(`/portaria/funcionarios/busca?q=${encodeURIComponent(re)}`);
            const exato = resultados.find(f => f.re === re);
            if (exato) {
                cdReResolvidoNome = exato.nome;
                statusEl.textContent = `Encontrado: ${exato.nome}`;
                statusEl.style.color = 'var(--accent3)';
            } else {
                statusEl.textContent = 'RE não encontrado — informe o nome para cadastrar.';
                statusEl.style.color = 'var(--muted)';
            }
        } catch (err) {
            if (err instanceof ApiError && err.status === 401) return;
            console.error('[portaria-veiculos] erro ao resolver RE (completar dono):', err);
        }
    }
    atualizarBotaoCompletarDono();
}

// 🔴 Antes de criar pessoa nova, mostrar quem já existe — o sr. Reginaldo
// está cadastrado como "994011"; digitar "A4011" sem ver isso nasceria a
// mesma pessoa duas vezes.
async function buscarCandidatosCompletarDono() {
    const nome = document.getElementById('cd-nome').value.trim();
    const el = document.getElementById('cd-candidatos');
    if (nome.length < 3) { el.innerHTML = ''; return; }
    try {
        const resultados = await apiGet(`/portaria/funcionarios/busca?q=${encodeURIComponent(nome)}`);
        renderCandidatosCompletarDono(resultados);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria-veiculos] erro ao buscar candidatos (completar dono):', err);
    }
}

function renderCandidatosCompletarDono(candidatos) {
    const el = document.getElementById('cd-candidatos');
    el.innerHTML = '';
    for (const c of candidatos) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'portaria-item';
        btn.innerHTML = `
            <div>
                <div class="portaria-item-placa">${escapeHtml(c.nome)}</div>
                <div class="portaria-item-sub">RE ${escapeHtml(c.re)}</div>
            </div>
        `;
        // Tocar num candidato preenche o RE dele — nunca escolhido em silêncio.
        btn.addEventListener('click', () => {
            document.getElementById('cd-re').value = c.re;
            el.innerHTML = '';
            resolverReCompletarDono();
        });
        el.appendChild(btn);
    }
}

async function salvarCompletarDono() {
    if (!cdVeiculoId) return;
    const erro = document.getElementById('cd-erro');
    erro.style.display = 'none';
    const re = document.getElementById('cd-re').value.trim();
    const nome = document.getElementById('cd-nome').value.trim();
    if (!re) { erro.textContent = 'Digite o RE.'; erro.style.display = 'block'; return; }
    if (!cdReResolvidoNome) {
        if (!nome) {
            erro.textContent = 'Informe o nome para cadastrar a pessoa.';
            erro.style.display = 'block';
            return;
        }
        if (!confirm(`Vai criar: ${re} — ${nome}`)) return;
    }

    const btn = document.getElementById('btn-salvar-completar-dono');
    btn.disabled = true;
    try {
        await apiPost(`/portaria/veiculos/${cdVeiculoId}/completar-dono`, { re, nome: nome || null });
        fecharModal('modal-completar-dono');
        await abrirFicha(cdVeiculoId);
        atualizarAbaAtiva();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

function initCompletarDono() {
    aplicarMascara(document.getElementById('cd-re'), 're');
    document.getElementById('btn-ficha-completar-dono').addEventListener('click', abrirCompletarDono);
    document.getElementById('fechar-completar-dono').addEventListener('click', fecharCompletarDonoEVoltar);
    document.getElementById('btn-cancelar-completar-dono').addEventListener('click', fecharCompletarDonoEVoltar);
    document.getElementById('cd-re').addEventListener('input', () => {
        clearTimeout(cdReHandle);
        cdReHandle = setTimeout(resolverReCompletarDono, 250);
    });
    document.getElementById('cd-nome').addEventListener('input', () => {
        clearTimeout(cdNomeHandle);
        cdNomeHandle = setTimeout(buscarCandidatosCompletarDono, 250);
    });
    document.getElementById('btn-salvar-completar-dono').addEventListener('click', salvarCompletarDono);
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
initHeader();
aplicarPermissoes();
initTabs();
initFiltrosTodos();
initFicha();
initFichaQr();
initImprimirEtiquetas();
initBloquearPorRe();
initNovoVeiculo();
initNovaEmpresa();
initGestaoAdmin();
initCompletarDono();
carregarPendentes();
