/*
 * Cadastros — Fase 5.8 / Etapa 5 (RBAC)
 * ---------------------------------------
 * Tela de gestão de entidades base, acesso restrito a quem escreve em "usuarios".
 *
 * Abas: Funcionários | Ônibus | Motoristas | Linhas | Filas | Tipos de Defeito
 *       | Pré-cadastros (Bloco F — só aparece pra quem tem `pre_cadastro`)
 * (a chave interna da primeira aba continua 'usuarios' — só o rótulo mudou —
 * pra não precisar tocar no roteamento das outras abas, que não mudam.)
 *
 * A aba Filas é só leitura + edição da abreviação/status — o catálogo de
 * filas (nome, tipo, número) vem das migrations, não se cria fila nova
 * por aqui (botão "+ Novo" some nessa aba).
 *
 * Cada aba segue o mesmo padrão:
 *   carregar() → renderTabela() → clicar linha → abrirModal() → salvar()
 *
 * A aba Funcionários lida com múltiplas funções por pessoa: quem tem
 * acesso a "usuarios" pode atribuir/remover funções (POST e DELETE
 * /funcionarios/{id}/funcoes) e criar acesso ao sistema separadamente
 * (POST /funcionarios/{id}/login). A função principal é calculada pelo
 * banco (fn_ajustar_funcao_principal) pela hierarquia — não é escolhida
 * manualmente aqui.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, apiPatch, apiDelete, ApiError } from './api.js';
import { podeEscrever, podeLer } from './sessao.js';
import { aplicarMascara } from './mascaras.js';
import { escapeHtml } from './escape.js';

// --- Guard: quem lê "usuarios" entra; quem só lê não edita (ver abrir()) ---
if (!requireAuth()) {
    throw new Error('Sessao nao autenticada');
}
if (!podeLer('usuarios')) {
    window.location.replace('patio.html');
    throw new Error('Sem acesso de leitura ao recurso usuarios');
}

// Quem só lê "usuarios" (Gerência, e Coordenador de Tráfego desde a
// migration 020) entra, olha os cadastros, mas não cria, edita nem exclui
// nada — os botões continuam visíveis, só desabilitados com title (§6).
const somenteLeitura = !podeEscrever('usuarios');

// ─── Estado global da aba ────────────────────────────────────────
let abaAtiva = 'usuarios';
let dadosCache = [];   // último resultado carregado da API
let buscaAtual = '';

// ─── Estado da aba Funcionários ───────────────────────────────────
let funcoesCatalogo = [];         // catálogo de funções (GET /funcoes), carregado uma vez
let funcionarioIdEmEdicao = null; // usado pra não acusar conflito com o próprio registro
let funcionarioVinculosAtuais = []; // vínculos de função do registro aberto no modal
let verificacaoDebounce = null;

// ─── Boot ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    setupHeader();
    setupTabs();
    setupBusca();
    setupModais();
    carregarAba();
    carregarFuncoesCatalogo();
    atualizarVisibilidadeBtnNovo();

    // RE só dígito (zero à esquerda preservado — nunca type="number").
    aplicarMascara(document.getElementById('usuario-re'), 're');
    aplicarMascara(document.getElementById('motorista-re'), 're');
    aplicarMascara(document.getElementById('usuario-cnh'), 'cnh');
    aplicarMascara(document.getElementById('usuario-rg'), 'rg');

    // Bloco F — mesmas máscaras da aba Funcionários. ⛔ CPF fica SEM máscara
    // de propósito: promover cria um Funcionario, e a aba Funcionários grava
    // CPF cru. Formatar só aqui criaria dois formatos do mesmo CPF no
    // cadastro central e a verificação de duplicata deixaria de casar.
    aplicarMascara(document.getElementById('pc-cnh'), 'cnh');
    aplicarMascara(document.getElementById('pc-rg'), 'rg');

    aplicarVisibilidadeAbaPreCadastro();
});

// Filas são um catálogo fixo (seedado pelas migrations) — esta tela só
// edita a abreviação/status de uma fila existente, nunca cria uma nova.
// "Filas" é ausência de funcionalidade (some de verdade); falta de permissão
// para escrever em "usuarios" é outra coisa — o botão continua visível e
// disabled, com title explicando, pra quem só lê não precisar adivinhar
// que a função existe (mesmo padrão do Imprimir/Sinistro, ver §6).
function atualizarVisibilidadeBtnNovo() {
    const btnNovo = document.getElementById('btn-novo');
    if (!btnNovo) return;
    // Filas e Pré-cadastros não têm "+ Novo" — nos dois casos é ausência de
    // funcionalidade, não falta de permissão: fila vem das migrations, e
    // pré-cadastro nasce da operação (services/pre_cadastro.py), nunca da mão.
    if (abaAtiva === 'filas' || abaAtiva === 'pre-cadastros') {
        btnNovo.style.display = 'none';
        return;
    }
    btnNovo.style.display = '';
    btnNovo.disabled = somenteLeitura;
    btnNovo.title = somenteLeitura ? 'Você não tem permissão para criar cadastros.' : '';
    btnNovo.setAttribute('aria-disabled', String(somenteLeitura));
}

async function carregarFuncoesCatalogo() {
    try {
        funcoesCatalogo = await apiGet('/funcoes');
    } catch (err) {
        funcoesCatalogo = [];
    }
}

// ─── HEADER ──────────────────────────────────────────────────────
function setupHeader() {
    const user = getCurrentUser();
    document.getElementById('user-name').textContent = user?.nome || '—';
    document.getElementById('user-meta').textContent = (user?.re || '—').toUpperCase();
    document.getElementById('btn-logout').addEventListener('click', () => {
        logout();
        window.location.replace('index.html');
    });
}

// ─── ABAS ────────────────────────────────────────────────────────
function setupTabs() {
    document.querySelectorAll('#cadastros-tabs .filtro-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tab === abaAtiva) return;
            abaAtiva = btn.dataset.tab;
            document.querySelectorAll('#cadastros-tabs .filtro-btn').forEach(b =>
                b.classList.toggle('active', b.dataset.tab === abaAtiva));
            document.getElementById('cadastros-busca').value = '';
            buscaAtual = '';
            carregarAba();
            atualizarVisibilidadeBtnNovo();
        });
    });

    document.getElementById('btn-novo').addEventListener('click', () => abrirModalNovo());
}

// ─── BUSCA ───────────────────────────────────────────────────────
function setupBusca() {
    let debounce;
    document.getElementById('cadastros-busca').addEventListener('input', e => {
        clearTimeout(debounce);
        buscaAtual = e.target.value.trim().toLowerCase();
        debounce = setTimeout(() => renderTabela(filtrar(dadosCache)), 200);
    });
}

function filtrar(dados) {
    if (!buscaAtual) return dados;
    return dados.filter(item => {
        const campos = Object.values(item).join(' ').toLowerCase();
        return campos.includes(buscaAtual);
    });
}

// ─── CARREGAR ABA ────────────────────────────────────────────────
async function carregarAba() {
    const lista = document.getElementById('cadastros-lista');
    lista.innerHTML = '<div class="patio-loading">Carregando…</div>';
    try {
        dadosCache = await fetchAba(abaAtiva);
        renderTabela(filtrar(dadosCache));
    } catch (err) {
        lista.innerHTML = `<div class="patio-loading" style="color:var(--accent)">
            Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

async function fetchAba(aba) {
    switch (aba) {
        case 'usuarios':     return await apiGet('/funcionarios?limit=500');
        case 'onibus':       return await apiGet('/onibus?limit=1000');
        case 'motoristas':   return await apiGet('/motoristas?limit=1000');
        case 'linhas':       return await apiGet('/linhas?limit=500');
        case 'filas':        return await apiGet('/filas?limit=200');
        case 'tipos-defeito':return await apiGet('/tipos-defeito?limit=200');
        // Só a fila de trabalho: PROMOVIDO e DESCARTADO saem da lista, como a
        // pré-ocorrência convertida some da fila do coordenador. O histórico
        // continua na tabela (nada é apagado) — só não polui a tela.
        case 'pre-cadastros': return await apiGet('/pre-cadastros?status=PENDENTE');
        default: return [];
    }
}

// ─── RENDER TABELA ───────────────────────────────────────────────
function renderTabela(dados) {
    const lista = document.getElementById('cadastros-lista');

    if (!dados || dados.length === 0) {
        lista.innerHTML = '<div class="patio-loading">Nenhum registro encontrado.</div>';
        return;
    }

    let html = '';

    switch (abaAtiva) {
        case 'usuarios':
            html = tabelaFuncionarios(dados);
            break;
        case 'onibus':
            html = tabelaOnibus(dados);
            break;
        case 'motoristas':
            html = tabelaMotoristas(dados);
            break;
        case 'linhas':
            html = tabelaLinhas(dados);
            break;
        case 'filas':
            html = tabelaFilas(dados);
            break;
        case 'tipos-defeito':
            html = tabelaTiposDefeito(dados);
            break;
        case 'pre-cadastros':
            html = tabelaPreCadastros(dados);
            break;
    }

    lista.innerHTML = html;

    // Clique em linha da tabela → abrir modal de edição
    lista.querySelectorAll('tr[data-id]').forEach(tr => {
        tr.style.cursor = 'pointer';
        tr.addEventListener('click', () => {
            const item = dados.find(d => d.id === tr.dataset.id);
            if (item) abrirModalEditar(item);
        });
    });
}

// ─── TABELAS ─────────────────────────────────────────────────────

function _table(cabecalho, linhas) {
    return `
        <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:0.875rem">
            <thead>
                <tr style="background:var(--surface2);text-align:left">
                    ${cabecalho.map(c => `<th style="padding:8px 12px;white-space:nowrap">${c}</th>`).join('')}
                </tr>
            </thead>
            <tbody>${linhas}</tbody>
        </table>
        </div>`;
}

function _badge(texto, cor) {
    const cores = {
        verde:  'background:#1a3a1a;color:#4caf50',
        cinza:  'background:#2a2a2a;color:#888',
        amarelo:'background:#3a2e00;color:#ffd600',
        vermelho:'background:#3a1a1a;color:#f44336',
        azul:   'background:#1a2a3a;color:#42a5f5',
    };
    // escapeHtml aqui dentro cobre todo chamador, inclusive os que passam
    // dado do banco (ex.: l.setor) em vez de rótulo fixo — escapar rótulo
    // fixo ('Ativo' etc.) é inofensivo, não muda a saída.
    return `<span style="${cores[cor] || cores.cinza};padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:700">${escapeHtml(texto)}</span>`;
}

function _tr(id, celulas, extra = '') {
    return `<tr data-id="${id}" style="border-bottom:1px solid var(--surface2);transition:background 0.1s" ${extra}
            onmouseover="this.style.background='var(--surface2)'"
            onmouseout="this.style.background=''">
        ${celulas.map(c => `<td style="padding:8px 12px">${c ?? '—'}</td>`).join('')}
    </tr>`;
}

function tabelaFuncionarios(dados) {
    const linhas = dados.map(f => _tr(f.id, [
        `<strong style="font-family:var(--mono)">${escapeHtml(f.re)}</strong>`,
        escapeHtml(f.nome),
        _chipsFuncoes(f.vinculos),
        f.tem_login ? _badge('Com acesso', 'verde') : _badge('Sem acesso', 'cinza'),
        _badgeStatusFuncionario(f.status),
    ])).join('');
    return _table(['RE', 'Nome', 'Funções', 'Acesso', 'Status'], linhas);
}

function _chipsFuncoes(vinculos) {
    const ativos = (vinculos || []).filter(v => v.ativo);
    if (ativos.length === 0) return '<span style="color:var(--muted)">—</span>';
    return ativos
        .map(v => `<span class="remanejo-badge ${v.principal ? 'badge-funcao-principal' : 'badge-funcao'}">${escapeHtml(v.funcao.nome)}</span>`)
        .join(' ');
}

function _badgeStatusFuncionario(status) {
    const mapa = {
        ATIVO:      ['Ativo', 'verde'],
        AFASTADO:   ['Afastado', 'amarelo'],
        FERIAS:     ['Férias', 'azul'],
        DESLIGADO:  ['Desligado', 'cinza'],
    };
    const [label, cor] = mapa[status] || [status, 'cinza'];
    return _badge(label, cor);
}

function tabelaOnibus(dados) {
    const linhas = dados.map(o => _tr(o.id, [
        `<strong style="font-family:monospace">${escapeHtml(o.numero_frota)}</strong>`,
        escapeHtml(o.setor) || '—',
        _badgeStatusOnibus(o.status),
    ])).join('');
    return _table(['Frota', 'Setor', 'Status'], linhas);
}

function _badgeStatusOnibus(status) {
    const mapa = {
        ATIVO:      ['Ativo', 'verde'],
        MANUTENCAO: ['Manutenção', 'vermelho'],
        RESERVA:    ['Reserva', 'amarelo'],
        INATIVO:    ['Inativo', 'cinza'],
    };
    const [label, cor] = mapa[status] || [status, 'cinza'];
    return _badge(label, cor);
}

function tabelaMotoristas(dados) {
    const linhas = dados.map(m => _tr(m.id, [
        `<strong style="font-family:monospace">${escapeHtml(m.re)}</strong>`,
        escapeHtml(m.nome),
        escapeHtml(m.cpf) || '—',
        _badgeStatusMotorista(m.status),
    ])).join('');
    return _table(['RE', 'Nome', 'CPF', 'Status'], linhas);
}

function _badgeStatusMotorista(status) {
    const mapa = {
        ATIVO:      ['Ativo', 'verde'],
        AFASTADO:   ['Afastado', 'amarelo'],
        FERIAS:     ['Férias', 'azul'],
        DESLIGADO:  ['Desligado', 'cinza'],
    };
    const [label, cor] = mapa[status] || [status, 'cinza'];
    return _badge(label, cor);
}

function tabelaLinhas(dados) {
    const linhas = dados.map(l => _tr(l.id, [
        `<strong style="font-family:monospace">${escapeHtml(l.codigo)}</strong>`,
        escapeHtml(l.nome),
        _badge(l.setor, l.setor === 'E2' ? 'azul' : 'verde'),
        l.ativa ? _badge('Ativa', 'verde') : _badge('Inativa', 'cinza'),
    ])).join('');
    return _table(['Código', 'Nome', 'Setor', 'Status'], linhas);
}

function _nomeTipoFila(tipo) {
    const mapa = {
        NUMERICA: 'Numérica', ESPECIAL: 'Especial',
        ESPECIAL_REMOTA: 'Fora da garagem', MANUTENCAO: 'Manutenção',
    };
    return mapa[tipo] || tipo;
}

function tabelaFilas(dados) {
    const ordenadas = [...dados].sort((a, b) => {
        if (a.tipo !== b.tipo) return a.tipo.localeCompare(b.tipo);
        return (a.ordem_exibicao ?? 0) - (b.ordem_exibicao ?? 0);
    });
    const linhas = ordenadas.map(f => _tr(f.id, [
        `<strong>${f.numero != null ? String(f.numero).padStart(2, '0') : escapeHtml(f.nome)}</strong>`,
        _nomeTipoFila(f.tipo),
        f.abreviacao
            ? `<span style="font-family:var(--mono)">${escapeHtml(f.abreviacao)}</span>`
            : '<span style="color:var(--muted)">— usa o nome —</span>',
        f.ativa ? _badge('Ativa', 'verde') : _badge('Inativa', 'cinza'),
    ])).join('');
    return _table(['Fila', 'Tipo', 'Abreviação', 'Status'], linhas);
}

function tabelaTiposDefeito(dados) {
    const linhas = dados.map(t => _tr(t.id, [
        escapeHtml(t.nome),
    ])).join('');
    return _table(['Nome'], linhas);
}

// ─── MODAIS ──────────────────────────────────────────────────────

function setupModais() {
    // Funcionário
    document.getElementById('modal-usuario-fechar').addEventListener('click', () => fechar('modal-usuario'));
    document.getElementById('btn-cancelar-usuario').addEventListener('click', () => fechar('modal-usuario'));
    document.getElementById('modal-usuario').addEventListener('click', e => {
        if (e.target.id === 'modal-usuario') fechar('modal-usuario');
    });
    document.getElementById('btn-salvar-usuario').addEventListener('click', salvarFuncionario);
    document.getElementById('btn-abrir-existente').addEventListener('click', abrirCadastroExistente);
    document.getElementById('btn-criar-acesso').addEventListener('click', criarAcesso);
    document.getElementById('usuario-re').addEventListener('blur', () => agendarVerificacao('re'));
    document.getElementById('usuario-cpf').addEventListener('blur', () => agendarVerificacao('cpf'));

    // Ônibus
    document.getElementById('modal-onibus-fechar').addEventListener('click', () => fechar('modal-onibus'));
    document.getElementById('btn-cancelar-onibus').addEventListener('click', () => fechar('modal-onibus'));
    document.getElementById('modal-onibus').addEventListener('click', e => {
        if (e.target.id === 'modal-onibus') fechar('modal-onibus');
    });
    document.getElementById('btn-salvar-onibus').addEventListener('click', salvarOnibus);

    // Motorista
    document.getElementById('modal-motorista-fechar').addEventListener('click', () => fechar('modal-motorista'));
    document.getElementById('btn-cancelar-motorista').addEventListener('click', () => fechar('modal-motorista'));
    document.getElementById('modal-motorista').addEventListener('click', e => {
        if (e.target.id === 'modal-motorista') fechar('modal-motorista');
    });
    document.getElementById('btn-salvar-motorista').addEventListener('click', salvarMotorista);

    // Linha
    document.getElementById('modal-linha-fechar').addEventListener('click', () => fechar('modal-linha'));
    document.getElementById('btn-cancelar-linha').addEventListener('click', () => fechar('modal-linha'));
    document.getElementById('modal-linha').addEventListener('click', e => {
        if (e.target.id === 'modal-linha') fechar('modal-linha');
    });
    document.getElementById('btn-salvar-linha').addEventListener('click', salvarLinha);
    document.getElementById('btn-excluir-linha').addEventListener('click', excluirLinha);

    // Fila
    document.getElementById('modal-fila-fechar').addEventListener('click', () => fechar('modal-fila'));
    document.getElementById('btn-cancelar-fila').addEventListener('click', () => fechar('modal-fila'));
    document.getElementById('modal-fila').addEventListener('click', e => {
        if (e.target.id === 'modal-fila') fechar('modal-fila');
    });
    document.getElementById('btn-salvar-fila').addEventListener('click', salvarFila);

    // Tipo de defeito
    document.getElementById('modal-tipo-fechar').addEventListener('click', () => fechar('modal-tipo-defeito'));
    document.getElementById('btn-cancelar-tipo').addEventListener('click', () => fechar('modal-tipo-defeito'));
    document.getElementById('modal-tipo-defeito').addEventListener('click', e => {
        if (e.target.id === 'modal-tipo-defeito') fechar('modal-tipo-defeito');
    });
    document.getElementById('btn-salvar-tipo').addEventListener('click', salvarTipoDefeito);

    // Pré-cadastro (Bloco F)
    document.getElementById('modal-pre-cadastro-fechar').addEventListener('click', () => fechar('modal-pre-cadastro'));
    document.getElementById('btn-cancelar-pre-cadastro').addEventListener('click', () => fechar('modal-pre-cadastro'));
    document.getElementById('modal-pre-cadastro').addEventListener('click', e => {
        if (e.target.id === 'modal-pre-cadastro') fechar('modal-pre-cadastro');
    });
    document.getElementById('btn-promover-pre-cadastro').addEventListener('click', promoverPreCadastro);
    document.getElementById('btn-descartar-pre-cadastro').addEventListener('click', abrirDescartePreCadastro);
    document.getElementById('btn-confirmar-descarte').addEventListener('click', confirmarDescartePreCadastro);
    document.getElementById('btn-cancelar-descarte').addEventListener('click', cancelarDescartePreCadastro);

    // Esc fecha qualquer modal aberto
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
        }
    });
}

function abrir(id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.add('open');
    if (somenteLeitura) aplicarSomenteLeituraModal(modal);
}
function fechar(id) { document.getElementById(id)?.classList.remove('open'); }

/**
 * Desabilita todo campo e botão de ação dentro do modal, mantendo só o
 * fechar/cancelar utilizáveis — usado quando a pessoa só tem leitura em
 * "usuarios" (gerência, e agora Coordenador de Tráfego desde a migration
 * 020): entra e olha o cadastro, não altera nada. Os botões continuam
 * visíveis e ganham title explicando — nunca display:none aqui, mesma
 * razão do Imprimir/Sinistro em ocorrencia-form.js: função escondida não
 * dá pra descobrir, desabilitada dá.
 */
function aplicarSomenteLeituraModal(modal) {
    modal.querySelectorAll('input, select, textarea').forEach(el => { el.disabled = true; });
    modal.querySelectorAll('button').forEach(el => {
        if (el.classList.contains('modal-close') || el.id.startsWith('btn-cancelar-')) return;
        el.disabled = true;
        el.title = 'Você não tem permissão para esta ação.';
        el.setAttribute('aria-disabled', 'true');
    });
}
function erroModal(id, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = msg;
    el.style.display = msg ? '' : 'none';
}

// ─── ABRIR NOVO ──────────────────────────────────────────────────
function abrirModalNovo() {
    switch (abaAtiva) {
        case 'usuarios':     abrirModalUsuario(null);     break;
        case 'onibus':       abrirModalOnibus(null);      break;
        case 'motoristas':   abrirModalMotorista(null);   break;
        case 'linhas':       abrirModalLinha(null);       break;
        case 'tipos-defeito':abrirModalTipoDefeito(null); break;
    }
}

// ─── ABRIR EDITAR ────────────────────────────────────────────────
function abrirModalEditar(item) {
    switch (abaAtiva) {
        case 'usuarios':     abrirModalUsuario(item);     break;
        case 'onibus':       abrirModalOnibus(item);      break;
        case 'motoristas':   abrirModalMotorista(item);   break;
        case 'linhas':       abrirModalLinha(item);       break;
        case 'filas':        abrirModalFila(item);        break;
        case 'tipos-defeito':abrirModalTipoDefeito(item); break;
        case 'pre-cadastros':abrirModalPreCadastro(item); break;
    }
}

// ─── MODAL FUNCIONÁRIO ───────────────────────────────────────────
function abrirModalUsuario(u) {
    const editando = !!u;
    funcionarioIdEmEdicao = u?.id || null;
    funcionarioVinculosAtuais = u?.vinculos || [];

    document.getElementById('modal-usuario-titulo').textContent = editando ? 'Editar Funcionário' : 'Novo Funcionário';
    document.getElementById('usuario-id').value     = u?.id || '';
    document.getElementById('usuario-re').value     = u?.re || '';
    document.getElementById('usuario-re').disabled  = editando; // RE não pode mudar
    document.getElementById('usuario-nome').value   = u?.nome || '';
    document.getElementById('usuario-cpf').value    = u?.cpf || '';
    document.getElementById('usuario-rg').value     = u?.rg || '';
    document.getElementById('usuario-cnh').value    = u?.cnh || '';
    document.getElementById('usuario-status').value = u?.status || 'ATIVO';

    document.getElementById('usuario-ativo-group').style.display = editando ? '' : 'none';

    renderFuncoesLista(funcionarioVinculosAtuais);

    const resultadoAcesso = document.getElementById('usuario-acesso-resultado');
    resultadoAcesso.style.display = 'none';
    resultadoAcesso.textContent = '';
    document.getElementById('btn-criar-acesso').style.display = '';
    document.getElementById('usuario-acesso-area').style.display = editando && !u?.tem_login ? '' : 'none';

    limparConflito();
    erroModal('modal-usuario-erro', '');
    abrir('modal-usuario');
    document.getElementById(editando ? 'usuario-nome' : 'usuario-re').focus();
}

function renderFuncoesLista(vinculosAtuais) {
    const idsAtivos = new Set(vinculosAtuais.filter(v => v.ativo).map(v => v.funcao.id));
    const container = document.getElementById('usuario-funcoes-lista');

    if (funcoesCatalogo.length === 0) {
        container.innerHTML = '<span style="color:var(--muted)">Carregando funções…</span>';
        return;
    }

    container.innerHTML = funcoesCatalogo.map(f => `
        <label class="form-check">
            <input type="checkbox" value="${f.id}" ${idsAtivos.has(f.id) ? 'checked' : ''}>
            <span class="form-check-label">${escapeHtml(f.nome)}</span>
        </label>
    `).join('');
}

function lerFuncoesSelecionadas() {
    return Array.from(document.querySelectorAll('#usuario-funcoes-lista input[type="checkbox"]:checked'))
        .map(input => input.value);
}

async function sincronizarFuncoes(funcionarioId, idsSelecionados, vinculosAtuais) {
    const ativosAtuais = (vinculosAtuais || []).filter(v => v.ativo);
    const idsAtuais = new Set(ativosAtuais.map(v => v.funcao.id));
    const idsNovos = new Set(idsSelecionados);

    for (const v of ativosAtuais) {
        if (!idsNovos.has(v.funcao.id)) {
            await apiDelete(`/funcionarios/${funcionarioId}/funcoes/${v.funcao.id}`);
        }
    }
    for (const funcaoId of idsSelecionados) {
        if (!idsAtuais.has(funcaoId)) {
            await apiPost(`/funcionarios/${funcionarioId}/funcoes`, { funcao_id: funcaoId });
        }
    }
}

async function salvarFuncionario() {
    const id = document.getElementById('usuario-id').value;
    const editando = !!id;
    erroModal('modal-usuario-erro', '');

    const re     = document.getElementById('usuario-re').value.trim();
    const nome   = document.getElementById('usuario-nome').value.trim();
    const cpf    = document.getElementById('usuario-cpf').value.trim();
    const rg     = document.getElementById('usuario-rg').value.trim();
    const cnh    = document.getElementById('usuario-cnh').value.trim();
    const status = document.getElementById('usuario-status').value;
    const funcoesSelecionadas = lerFuncoesSelecionadas();

    if (!re)   return erroModal('modal-usuario-erro', 'RE é obrigatório.');
    if (!nome) return erroModal('modal-usuario-erro', 'Nome é obrigatório.');
    if (!editando && !cpf) return erroModal('modal-usuario-erro', 'CPF é obrigatório.');
    if (funcoesSelecionadas.length === 0) return erroModal('modal-usuario-erro', 'Selecione ao menos uma função.');

    try {
        let funcionarioId = id;

        if (editando) {
            await apiPatch(`/funcionarios/${id}`, { nome, cpf: cpf || undefined, rg: rg || undefined, cnh: cnh || undefined, status });
        } else {
            const criado = await apiPost('/funcionarios', { re, nome, cpf, rg: rg || undefined, cnh: cnh || undefined, status });
            funcionarioId = criado.id;
        }

        await sincronizarFuncoes(funcionarioId, funcoesSelecionadas, funcionarioVinculosAtuais);

        fechar('modal-usuario');
        carregarAba();
    } catch (err) {
        if (err instanceof ApiError && err.status === 409 && err.body?.conflito) {
            mostrarConflito(err.body.conflito);
        } else {
            erroModal('modal-usuario-erro', err.message);
        }
    }
}

// ─── VERIFICAÇÃO DE RE/CPF DUPLICADO ──────────────────────────────
function agendarVerificacao(campo) {
    const valor = document.getElementById(`usuario-${campo}`).value.trim();
    clearTimeout(verificacaoDebounce);
    if (!valor) { limparConflito(); return; }
    verificacaoDebounce = setTimeout(() => verificarDuplicata(campo, valor), 250);
}

async function verificarDuplicata(campo, valor) {
    try {
        const params = new URLSearchParams({ [campo]: valor });
        const conflito = await apiGet(`/funcionarios/verificar?${params}`);
        if (conflito && conflito.funcionario_id !== funcionarioIdEmEdicao) {
            mostrarConflito(conflito);
        } else {
            limparConflito();
        }
    } catch (_) {
        // verificação é best-effort — não bloqueia o preenchimento
    }
}

function mostrarConflito(conflito) {
    document.getElementById('modal-usuario-conflito-texto').textContent =
        `Já existe cadastro com este ${conflito.campo.toUpperCase()}: ${conflito.nome} (RE ${conflito.re}).`;
    document.getElementById('modal-usuario-conflito').dataset.funcionarioId = conflito.funcionario_id;
    document.getElementById('modal-usuario-conflito').style.display = '';
}

function limparConflito() {
    const el = document.getElementById('modal-usuario-conflito');
    el.style.display = 'none';
    delete el.dataset.funcionarioId;
}

async function abrirCadastroExistente() {
    const funcionarioId = document.getElementById('modal-usuario-conflito').dataset.funcionarioId;
    if (!funcionarioId) return;
    try {
        const funcionario = await apiGet(`/funcionarios/${funcionarioId}`);
        fechar('modal-usuario');
        abrirModalUsuario(funcionario);
    } catch (err) {
        erroModal('modal-usuario-erro', err.message);
    }
}

// ─── CRIAR ACESSO AO SISTEMA ───────────────────────────────────────
async function criarAcesso() {
    const id = document.getElementById('usuario-id').value;
    if (!id) return;
    erroModal('modal-usuario-erro', '');
    try {
        await apiPost(`/funcionarios/${id}/login`);
        document.getElementById('btn-criar-acesso').style.display = 'none';

        const cpfDigits = document.getElementById('usuario-cpf').value.replace(/\D/g, '');
        const resultado = document.getElementById('usuario-acesso-resultado');
        resultado.textContent = `Acesso criado. Senha inicial: ${cpfDigits.slice(-4)} (4 últimos dígitos do CPF).`;
        resultado.style.display = '';

        carregarAba();
    } catch (err) {
        erroModal('modal-usuario-erro', err.message);
    }
}

// ─── MODAL ÔNIBUS ────────────────────────────────────────────────
function abrirModalOnibus(o) {
    const editando = !!o;
    document.getElementById('modal-onibus-titulo').textContent = editando ? `Frota ${o.numero_frota}` : 'Novo Ônibus';
    document.getElementById('onibus-id').value      = o?.id || '';
    document.getElementById('onibus-frota').value   = o?.numero_frota || '';
    document.getElementById('onibus-frota').disabled = editando;
    document.getElementById('onibus-status').value  = o?.status || 'ATIVO';
    document.getElementById('onibus-status-group').style.display = editando ? '' : 'none';

    const setor = o?.setor ? `Setor ${o.setor}` : 'Calculado automaticamente pelo banco';
    document.getElementById('onibus-setor-display').textContent = setor;

    erroModal('modal-onibus-erro', '');
    abrir('modal-onibus');
    document.getElementById('onibus-frota').focus();
}

async function salvarOnibus() {
    const id      = document.getElementById('onibus-id').value;
    const editando = !!id;
    erroModal('modal-onibus-erro', '');

    if (editando) {
        const status = document.getElementById('onibus-status').value;
        try {
            await apiPatch(`/onibus/${id}`, { status });
            fechar('modal-onibus');
            carregarAba();
        } catch (err) {
            erroModal('modal-onibus-erro', err.message);
        }
    } else {
        const frota = Number(document.getElementById('onibus-frota').value);
        if (!frota || frota < 1000 || frota > 9999) {
            return erroModal('modal-onibus-erro', 'Frota deve ter 4 dígitos (1000–9999).');
        }
        try {
            await apiPost('/onibus', { numero_frota: frota });
            fechar('modal-onibus');
            carregarAba();
        } catch (err) {
            erroModal('modal-onibus-erro', err.message);
        }
    }
}

// ─── MODAL MOTORISTA ─────────────────────────────────────────────
function abrirModalMotorista(m) {
    const editando = !!m;
    document.getElementById('modal-motorista-titulo').textContent = editando ? `Editar Motorista` : 'Novo Motorista';
    document.getElementById('motorista-id').value     = m?.id || '';
    document.getElementById('motorista-re').value     = m?.re || '';
    document.getElementById('motorista-re').disabled  = editando;
    document.getElementById('motorista-nome').value   = m?.nome || '';
    document.getElementById('motorista-cpf').value    = m?.cpf || '';
    document.getElementById('motorista-status').value = m?.status || 'ATIVO';
    document.getElementById('motorista-status-group').style.display = editando ? '' : 'none';

    erroModal('modal-motorista-erro', '');
    abrir('modal-motorista');
    document.getElementById(editando ? 'motorista-nome' : 'motorista-re').focus();
}

async function salvarMotorista() {
    const id      = document.getElementById('motorista-id').value;
    const editando = !!id;
    erroModal('modal-motorista-erro', '');

    const nome = document.getElementById('motorista-nome').value.trim();
    const cpf  = document.getElementById('motorista-cpf').value.trim() || null;

    if (!nome) return erroModal('modal-motorista-erro', 'Nome é obrigatório.');

    if (editando) {
        const status = document.getElementById('motorista-status').value;
        try {
            await apiPatch(`/motoristas/${id}`, { nome, cpf, status });
            fechar('modal-motorista');
            carregarAba();
        } catch (err) {
            erroModal('modal-motorista-erro', err.message);
        }
    } else {
        const re = document.getElementById('motorista-re').value.trim();
        if (!re) return erroModal('modal-motorista-erro', 'RE é obrigatório.');
        try {
            await apiPost('/motoristas', { re, nome, cpf });
            fechar('modal-motorista');
            carregarAba();
        } catch (err) {
            erroModal('modal-motorista-erro', err.message);
        }
    }
}

// ─── MODAL LINHA ─────────────────────────────────────────────────
function abrirModalLinha(l) {
    const editando = !!l;
    document.getElementById('modal-linha-titulo').textContent = editando ? `Editar Linha` : 'Nova Linha';
    document.getElementById('linha-id').value     = l?.id || '';
    document.getElementById('linha-codigo').value    = l?.codigo || '';
    document.getElementById('linha-codigo').disabled  = false; // editável em criação e edição
    document.getElementById('linha-nome').value      = l?.nome || '';
    document.getElementById('linha-setor').value     = l?.setor || 'E2';
    document.getElementById('linha-setor').disabled  = editando; // setor não muda depois
    document.getElementById('linha-ativa').value     = String(l?.ativa ?? true);
    document.getElementById('linha-ativa-group').style.display  = editando ? '' : 'none';
    document.getElementById('btn-excluir-linha').style.display  = editando ? '' : 'none';

    erroModal('modal-linha-erro', '');
    abrir('modal-linha');
    document.getElementById(editando ? 'linha-nome' : 'linha-codigo').focus();
}

async function salvarLinha() {
    const id      = document.getElementById('linha-id').value;
    const editando = !!id;
    erroModal('modal-linha-erro', '');

    const nome = document.getElementById('linha-nome').value.trim();
    if (!nome) return erroModal('modal-linha-erro', 'Nome é obrigatório.');

    if (editando) {
        const ativa = document.getElementById('linha-ativa').value === 'true';
        try {
            await apiPatch(`/linhas/${id}`, { nome, ativa });
            fechar('modal-linha');
            carregarAba();
        } catch (err) {
            erroModal('modal-linha-erro', err.message);
        }
    } else {
        const codigo = document.getElementById('linha-codigo').value.trim();
        const setor  = document.getElementById('linha-setor').value;
        if (!codigo) return erroModal('modal-linha-erro', 'Código é obrigatório.');
        try {
            await apiPost('/linhas', { codigo, nome, setor });
            fechar('modal-linha');
            carregarAba();
        } catch (err) {
            erroModal('modal-linha-erro', err.message);
        }
    }
}

async function excluirLinha() {
    const id     = document.getElementById('linha-id').value;
    const codigo = document.getElementById('linha-codigo').value || 'esta linha';
    if (!id) return;
    if (!confirm(`Excluir linha "${codigo}"?\n\nEsta ação é permanente. Se a linha estiver em uso na escala atual, a exclusão será bloqueada.`)) return;
    try {
        await apiDelete(`/linhas/${id}`);
        fechar('modal-linha');
        carregarAba();
    } catch (err) {
        erroModal('modal-linha-erro', err.message);
    }
}

// ─── MODAL FILA — só edita abreviação e status; catálogo (nome/tipo/número)
// vem das migrations, não se cria fila nova por aqui ──────────────
function abrirModalFila(f) {
    document.getElementById('modal-fila-titulo').textContent = `Fila — ${f.nome}`;
    document.getElementById('fila-id').value = f.id;
    document.getElementById('fila-nome-display').textContent =
        `${f.nome} (${_nomeTipoFila(f.tipo)}${f.numero != null ? ' nº ' + f.numero : ''})`;
    document.getElementById('fila-abreviacao').value = f.abreviacao || '';
    document.getElementById('fila-ativa').value = String(f.ativa ?? true);

    erroModal('modal-fila-erro', '');
    abrir('modal-fila');
    document.getElementById('fila-abreviacao').focus();
}

async function salvarFila() {
    const id = document.getElementById('fila-id').value;
    if (!id) return;
    erroModal('modal-fila-erro', '');

    const abreviacao = document.getElementById('fila-abreviacao').value.trim().toUpperCase();
    const ativa = document.getElementById('fila-ativa').value === 'true';

    try {
        await apiPatch(`/filas/${id}`, { abreviacao: abreviacao || null, ativa });
        fechar('modal-fila');
        carregarAba();
    } catch (err) {
        erroModal('modal-fila-erro', err.message);
    }
}

// ─── MODAL TIPO DE DEFEITO ───────────────────────────────────────
function abrirModalTipoDefeito(t) {
    const editando = !!t;
    document.getElementById('modal-tipo-titulo').textContent = editando ? 'Editar Tipo de Defeito' : 'Novo Tipo de Defeito';
    document.getElementById('tipo-id').value   = t?.id || '';
    document.getElementById('tipo-nome').value = t?.nome || '';

    erroModal('modal-tipo-erro', '');
    abrir('modal-tipo-defeito');
    document.getElementById('tipo-nome').focus();
}

async function salvarTipoDefeito() {
    const id      = document.getElementById('tipo-id').value;
    const editando = !!id;
    const nome    = document.getElementById('tipo-nome').value.trim();
    erroModal('modal-tipo-erro', '');

    if (!nome) return erroModal('modal-tipo-erro', 'Nome é obrigatório.');

    try {
        if (editando) {
            await apiPatch(`/tipos-defeito/${id}`, { nome });
        } else {
            await apiPost('/tipos-defeito', { nome });
        }
        fechar('modal-tipo-defeito');
        carregarAba();
    } catch (err) {
        erroModal('modal-tipo-erro', err.message);
    }
}

// ═══════════════════════════════════════════════════════════════════
// ABA PRÉ-CADASTROS (Bloco F)
// ═══════════════════════════════════════════════════════════════════
/*
 * A fila que a operação enche sozinha. Todo RE digitado em ocorrência,
 * pré-ocorrência, recolhida anormal e avaria de saída cai em
 * pessoa_pre_cadastro (services/pre_cadastro.py, migration 028) com o que
 * aquele módulo soube informar — a portaria contribui só o RE, a
 * pré-ocorrência contribui nome, CPF, RG, CNH e telefone.
 *
 * É o carro que entra na garagem sem ficha: alguém precisa olhar a fila e
 * decidir se vira cadastro de verdade ou se some. Esta aba é a primeira
 * tela desse dado — antes dela, a fila acumulava invisível.
 *
 * 🔴 RBAC DE DUAS CABEÇAS — a parte que mais engana nesta tela:
 *     ler a fila, corrigir campos, descartar → recurso `pre_cadastro`
 *     PROMOVER (criar o funcionário)        → recurso `usuarios`, escrita
 * Promover é ato de cadastro central (RH), não de triagem. Por isso este
 * modal ⛔ NÃO passa por abrir()/aplicarSomenteLeituraModal(): aquele
 * caminho é genérico e trancaria o modal inteiro por `usuarios`, deixando
 * quem tem `pre_cadastro` sem conseguir nem corrigir um nome torto.
 * Ver aplicarPermissoesPreCadastro().
 *
 * ⚠️ Promover NUNCA cria login. Devolve o funcionario_id; o acesso ao
 * sistema continua sendo criado na aba Funcionários, como sempre foi.
 */

// Lidos uma vez no carregamento, como `somenteLeitura` lá em cima.
const podeEscreverPreCadastro = podeEscrever('pre_cadastro');
const podePromoverPreCadastro = podeEscrever('usuarios');

let preCadastroAtual = null;   // registro aberto no modal

/**
 * A aba some inteira pra quem não tem `pre_cadastro` leitura.
 * ⚠️ Aqui é display:none mesmo (e não disabled+title, como o "+ Novo"):
 * ausência de recurso é ausência de funcionalidade, mesmo caso da aba
 * Filas. Botão desabilitado serve pra avisar que a ação existe; aba de um
 * recurso que a pessoa não tem só geraria pergunta sem resposta.
 * ⛔ Nome usado pelo comentário do cadastros.html — não renomear sozinho.
 */
function aplicarVisibilidadeAbaPreCadastro() {
    const btn = document.querySelector('#cadastros-tabs .filtro-btn[data-tab="pre-cadastros"]');
    if (!btn) return;
    btn.style.display = podeLer('pre_cadastro') ? '' : 'none';
}

// ─── TABELA ──────────────────────────────────────────────────────
function tabelaPreCadastros(dados) {
    // Ordem por vezes_visto DESC: quem a operação mais encontrou é quem mais
    // vale cadastrar. O backend devolve por ultima_vez_em (fila de novidade);
    // a ordem que interessa aqui é a de prioridade, então reordenamos.
    const ordenados = [...dados].sort((a, b) => {
        if (b.vezes_visto !== a.vezes_visto) return b.vezes_visto - a.vezes_visto;
        return new Date(b.ultima_vez_em) - new Date(a.ultima_vez_em);
    });

    const linhas = ordenados.map(p => _tr(p.id, [
        `<strong style="font-family:var(--mono)">${escapeHtml(p.re)}</strong>`,
        p.nome ? escapeHtml(p.nome) : '<span style="color:var(--muted)">— sem nome —</span>',
        _badgePapelPreCadastro(p.papel_sugerido),
        `<strong>${Number(p.vezes_visto) || 0}</strong>`,
        _nomeOrigemPreCadastro(p.ultima_origem),
        _dataHoraCurta(p.ultima_vez_em),
        _retencaoPreCadastro(p.retencao_expira_em),
    ])).join('');

    return _table(
        ['RE', 'Nome', 'Papel sugerido', 'Vezes visto', 'Última origem', 'Última vez', 'Retenção'],
        linhas,
    );
}

function _badgePapelPreCadastro(papel) {
    const mapa = {
        MOTORISTA:  ['Motorista', 'azul'],
        COBRADOR:   ['Cobrador', 'verde'],
        INDEFINIDO: ['Indefinido', 'cinza'],
    };
    const [label, cor] = mapa[papel] || [papel, 'cinza'];
    return _badge(label, cor);
}

// Valores gravados por services/pre_cadastro.py — os quatro pontos de
// captura de hoje. Origem desconhecida cai no próprio código (escapado),
// nunca em "—": saber que veio de um lugar novo é informação.
function _nomeOrigemPreCadastro(origem) {
    if (!origem) return '<span style="color:var(--muted)">—</span>';
    const mapa = {
        OCORRENCIA:         'Ocorrência',
        PRE_OCORRENCIA:     'Pré-ocorrência',
        PORTARIA_RECOLHIDA: 'Recolhida',
        PORTARIA_AVARIA:    'Avaria na saída',
    };
    return escapeHtml(mapa[origem] || origem);
}

/**
 * ⚠️ LGPD visível de propósito. A migration 028 decidiu que a retenção é
 * aplicada por FILTRO na leitura, não por job — o projeto não tem
 * scheduler. Ou seja: passou da data, o registro continua no banco. Deixar
 * o prazo na tela é o que evita que isso seja esquecido.
 */
function _retencaoPreCadastro(iso) {
    if (!iso) return '<span style="color:var(--muted)">—</span>';
    const expira = new Date(iso);
    if (Number.isNaN(expira.getTime())) return '<span style="color:var(--muted)">—</span>';
    const dias = Math.ceil((expira - new Date()) / 86400000);
    if (dias < 0)  return _badge('Expirado', 'vermelho');
    if (dias <= 7) return _badge(`${dias} d`, 'amarelo');
    return `<span style="color:var(--muted)">${dias} d</span>`;
}

// ⛔ Nunca toISOString() para exibir data — erra o dia depois das 21h
// (armadilha registrada no projeto). toLocaleString respeita o fuso local.
function _dataHoraCurta(iso) {
    if (!iso) return '<span style="color:var(--muted)">—</span>';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '<span style="color:var(--muted)">—</span>';
    return d.toLocaleString('pt-BR', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
}

// ─── MODAL ───────────────────────────────────────────────────────
function abrirModalPreCadastro(p) {
    preCadastroAtual = p;

    document.getElementById('pc-id').value            = p.id;
    document.getElementById('pc-re-display').textContent = p.re;
    document.getElementById('pc-nome').value           = p.nome || '';
    document.getElementById('pc-cpf').value            = p.cpf || '';
    document.getElementById('pc-rg').value             = p.rg || '';
    document.getElementById('pc-cnh').value            = p.cnh || '';
    document.getElementById('pc-telefone').value       = p.telefone || '';
    document.getElementById('pc-papel').value          = p.papel_sugerido || 'INDEFINIDO';
    document.getElementById('pc-meta').textContent     = _metaPreCadastro(p);

    // O descarte começa fechado toda vez — abrir o modal não pode deixar um
    // motivo digitado antes à mostra.
    cancelarDescartePreCadastro();
    erroModal('modal-pre-cadastro-erro', '');

    // ⛔ De propósito NÃO usa abrir(): ver o cabeçalho desta seção.
    document.getElementById('modal-pre-cadastro').classList.add('open');
    aplicarPermissoesPreCadastro();

    if (podeEscreverPreCadastro) document.getElementById('pc-nome').focus();
}

function _metaPreCadastro(p) {
    const partes = [`Visto ${Number(p.vezes_visto) || 0}x`];
    if (p.ultima_origem) {
        const mapa = {
            OCORRENCIA: 'Ocorrência', PRE_OCORRENCIA: 'Pré-ocorrência',
            PORTARIA_RECOLHIDA: 'Recolhida', PORTARIA_AVARIA: 'Avaria na saída',
        };
        partes.push(`última via ${mapa[p.ultima_origem] || p.ultima_origem}`);
    }
    if (p.retencao_expira_em) {
        const d = new Date(p.retencao_expira_em);
        if (!Number.isNaN(d.getTime())) {
            partes.push(`retenção até ${d.toLocaleDateString('pt-BR')}`);
        }
    }
    return partes.join(' · ');
}

/**
 * 🔴 O ponto delicado do bloco: dois recursos, dois grupos de controles.
 * Quem tem só `pre_cadastro` corrige e descarta, mas não promove. Quem tem
 * só `usuarios` promove o que já está completo, sem editar a fila. Ninguém
 * fica com o modal inteiro trancado por causa do outro recurso.
 */
function aplicarPermissoesPreCadastro() {
    ['pc-nome', 'pc-cpf', 'pc-rg', 'pc-cnh', 'pc-telefone', 'pc-papel', 'pc-descarte-motivo']
        .forEach(id => {
            const el = document.getElementById(id);
            if (el) el.disabled = !podeEscreverPreCadastro;
        });

    const msgTriagem = 'Você não tem permissão de escrita em Pré-cadastros.';
    _botaoPermissao('btn-descartar-pre-cadastro', podeEscreverPreCadastro, msgTriagem);
    _botaoPermissao('btn-confirmar-descarte',     podeEscreverPreCadastro, msgTriagem);
    _botaoPermissao('btn-promover-pre-cadastro',  podePromoverPreCadastro,
        'Promover cria cadastro de funcionário — exige escrita em Usuários.');
}

// Desabilita com title, nunca esconde — mesma regra do "+ Novo" e do
// Imprimir/Sinistro: ação que existe e você não pode fazer tem que aparecer.
function _botaoPermissao(id, liberado, motivo) {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = !liberado;
    btn.title = liberado ? '' : motivo;
    btn.setAttribute('aria-disabled', String(!liberado));
}

// ─── PROMOVER ────────────────────────────────────────────────────
async function promoverPreCadastro() {
    const p = preCadastroAtual;
    if (!p) return;
    erroModal('modal-pre-cadastro-erro', '');

    const nome = document.getElementById('pc-nome').value.trim();
    if (!nome) {
        return erroModal('modal-pre-cadastro-erro',
            'Nome é obrigatório para promover — complete o cadastro antes.');
    }

    const dados = {
        nome,
        cpf:      document.getElementById('pc-cpf').value.trim() || null,
        rg:       document.getElementById('pc-rg').value.trim() || null,
        cnh:      document.getElementById('pc-cnh').value.trim() || null,
        telefone: document.getElementById('pc-telefone').value.trim() || null,
        papel_sugerido: document.getElementById('pc-papel').value,
    };

    try {
        // Dois atos, dois recursos. O PATCH só sai se alguma coisa mudou —
        // assim quem tem apenas `usuarios` promove um registro já completo
        // sem esbarrar no recurso `pre_cadastro`, que ele não tem.
        if (_preCadastroMudou(p, dados)) {
            await apiPatch(`/pre-cadastros/${p.id}`, dados);
        }
        await apiPost(`/pre-cadastros/${p.id}/promover`);
        fechar('modal-pre-cadastro');
        preCadastroAtual = null;
        carregarAba();
    } catch (err) {
        erroModal('modal-pre-cadastro-erro', err.message);
    }
}

function _preCadastroMudou(p, dados) {
    const norm = v => (v ?? '').toString().trim();
    return norm(dados.nome)     !== norm(p.nome)
        || norm(dados.cpf)      !== norm(p.cpf)
        || norm(dados.rg)       !== norm(p.rg)
        || norm(dados.cnh)      !== norm(p.cnh)
        || norm(dados.telefone) !== norm(p.telefone)
        || dados.papel_sugerido !== p.papel_sugerido;
}

// ─── DESCARTAR ───────────────────────────────────────────────────
// Motivo obrigatório: o backend grava quem descartou e quando, mas o
// porquê só existe se alguém escrever. Descarte sem motivo vira mistério
// daqui a três meses.
function abrirDescartePreCadastro() {
    erroModal('modal-pre-cadastro-erro', '');
    document.getElementById('pc-descarte-wrap').style.display = '';
    document.getElementById('pc-descarte-motivo').focus();
}

function cancelarDescartePreCadastro() {
    const wrap = document.getElementById('pc-descarte-wrap');
    if (wrap) wrap.style.display = 'none';
    const motivo = document.getElementById('pc-descarte-motivo');
    if (motivo) motivo.value = '';
}

async function confirmarDescartePreCadastro() {
    const p = preCadastroAtual;
    if (!p) return;
    const motivo = document.getElementById('pc-descarte-motivo').value.trim();
    if (!motivo) {
        return erroModal('modal-pre-cadastro-erro', 'Descreva o motivo do descarte.');
    }
    try {
        await apiPost(`/pre-cadastros/${p.id}/descartar`, { motivo });
        fechar('modal-pre-cadastro');
        preCadastroAtual = null;
        carregarAba();
    } catch (err) {
        erroModal('modal-pre-cadastro-erro', err.message);
    }
}
