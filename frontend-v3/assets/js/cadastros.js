/*
 * Cadastros — Fase 5.8 / Etapa 5 (RBAC)
 * ---------------------------------------
 * Tela de gestão de entidades base, acesso restrito a quem escreve em "usuarios".
 *
 * Abas: Funcionários | Ônibus | Motoristas | Linhas | Filas | Tipos de Defeito
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

// --- Guard: quem lê "usuarios" entra; quem só lê não edita (ver abrir()) ---
if (!requireAuth()) {
    throw new Error('Sessao nao autenticada');
}
if (!podeLer('usuarios')) {
    window.location.replace('patio.html');
    throw new Error('Sem acesso de leitura ao recurso usuarios');
}

// Gerência (GERENTE_GERAL/GERENTE_OPERACIONAL) só lê "usuarios" — entra,
// olha os cadastros e permissões, mas não cria, edita nem exclui nada.
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
});

// Filas são um catálogo fixo (seedado pelas migrations) — esta tela só
// edita a abreviação/status de uma fila existente, nunca cria uma nova.
function atualizarVisibilidadeBtnNovo() {
    const btnNovo = document.getElementById('btn-novo');
    if (!btnNovo) return;
    btnNovo.style.display = (somenteLeitura || abaAtiva === 'filas') ? 'none' : '';
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
            Erro ao carregar: ${err.message}</div>`;
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
    return `<span style="${cores[cor] || cores.cinza};padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:700">${texto}</span>`;
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
        `<strong style="font-family:var(--mono)">${f.re}</strong>`,
        f.nome,
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
        .map(v => `<span class="remanejo-badge ${v.principal ? 'badge-funcao-principal' : 'badge-funcao'}">${v.funcao.nome}</span>`)
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
        `<strong style="font-family:monospace">${o.numero_frota}</strong>`,
        o.setor || '—',
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
        `<strong style="font-family:monospace">${m.re}</strong>`,
        m.nome,
        m.cpf || '—',
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
        `<strong style="font-family:monospace">${l.codigo}</strong>`,
        l.nome,
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
        `<strong>${f.numero != null ? String(f.numero).padStart(2, '0') : f.nome}</strong>`,
        _nomeTipoFila(f.tipo),
        f.abreviacao
            ? `<span style="font-family:var(--mono)">${f.abreviacao}</span>`
            : '<span style="color:var(--muted)">— usa o nome —</span>',
        f.ativa ? _badge('Ativa', 'verde') : _badge('Inativa', 'cinza'),
    ])).join('');
    return _table(['Fila', 'Tipo', 'Abreviação', 'Status'], linhas);
}

function tabelaTiposDefeito(dados) {
    const linhas = dados.map(t => _tr(t.id, [
        t.nome,
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
 * "usuarios" (gerência): entra e olha o cadastro, não altera nada.
 */
function aplicarSomenteLeituraModal(modal) {
    modal.querySelectorAll('input, select, textarea').forEach(el => { el.disabled = true; });
    modal.querySelectorAll('button').forEach(el => {
        if (el.classList.contains('modal-close') || el.id.startsWith('btn-cancelar-')) return;
        el.style.display = 'none';
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
            <span class="form-check-label">${f.nome}</span>
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
    const status = document.getElementById('usuario-status').value;
    const funcoesSelecionadas = lerFuncoesSelecionadas();

    if (!re)   return erroModal('modal-usuario-erro', 'RE é obrigatório.');
    if (!nome) return erroModal('modal-usuario-erro', 'Nome é obrigatório.');
    if (!editando && !cpf) return erroModal('modal-usuario-erro', 'CPF é obrigatório.');
    if (funcoesSelecionadas.length === 0) return erroModal('modal-usuario-erro', 'Selecione ao menos uma função.');

    try {
        let funcionarioId = id;

        if (editando) {
            await apiPatch(`/funcionarios/${id}`, { nome, cpf: cpf || undefined, status });
        } else {
            const criado = await apiPost('/funcionarios', { re, nome, cpf, status });
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
