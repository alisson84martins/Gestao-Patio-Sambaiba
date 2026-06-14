/*
 * Cadastros — Fase 5.8
 * ---------------------
 * Tela de gestão de entidades base, acesso restrito a ADMIN.
 *
 * Abas: Usuários | Ônibus | Motoristas | Linhas | Tipos de Defeito
 *
 * Cada aba segue o mesmo padrão:
 *   carregar() → renderTabela() → clicar linha → abrirModal() → salvar()
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, apiPatch, apiDelete, ApiError } from './api.js';

// --- Guard: somente ADMIN ---
if (!requireAuth()) {
    throw new Error('Sessao nao autenticada');
}
const _me = getCurrentUser();
if (_me?.perfil !== 'ADMIN') {
    window.location.replace('patio.html');
}

// ─── Estado global da aba ────────────────────────────────────────
let abaAtiva = 'usuarios';
let dadosCache = [];   // último resultado carregado da API
let buscaAtual = '';

// ─── Boot ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    setupHeader();
    setupTabs();
    setupBusca();
    setupModais();
    carregarAba();
});

// ─── HEADER ──────────────────────────────────────────────────────
function setupHeader() {
    const user = getCurrentUser();
    document.getElementById('user-name').textContent = user?.nome || '—';
    document.getElementById('user-meta').textContent =
        `${user?.re || ''} · ${user?.perfil || ''}`.toUpperCase();
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
        case 'usuarios':     return await apiGet('/usuarios?limit=500');
        case 'onibus':       return await apiGet('/onibus?limit=1000');
        case 'motoristas':   return await apiGet('/motoristas?limit=1000');
        case 'linhas':       return await apiGet('/linhas?limit=500');
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
            html = tabelaUsuarios(dados);
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
                <tr style="background:var(--surface-2);text-align:left">
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
    return `<tr data-id="${id}" style="border-bottom:1px solid var(--surface-2);transition:background 0.1s" ${extra}
            onmouseover="this.style.background='var(--surface-2)'"
            onmouseout="this.style.background=''">
        ${celulas.map(c => `<td style="padding:8px 12px">${c ?? '—'}</td>`).join('')}
    </tr>`;
}

function tabelaUsuarios(dados) {
    const linhas = dados.map(u => _tr(u.id, [
        `<strong style="font-family:monospace">${u.re}</strong>`,
        u.nome,
        _badgePerfil(u.perfil),
        u.ativo ? _badge('Ativo', 'verde') : _badge('Inativo', 'cinza'),
    ])).join('');
    return _table(['RE', 'Nome', 'Perfil', 'Status'], linhas);
}

function _badgePerfil(perfil) {
    const mapa = {
        ADMIN:          ['Admin', 'vermelho'],
        COORDENADOR:    ['Coordenador', 'azul'],
        OPERADOR_PATIO: ['Operador', 'verde'],
        MECANICO:       ['Mecânico', 'amarelo'],
        MOTORISTA:      ['Motorista', 'cinza'],
    };
    const [label, cor] = mapa[perfil] || [perfil, 'cinza'];
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

function tabelaTiposDefeito(dados) {
    const linhas = dados.map(t => _tr(t.id, [
        t.nome,
    ])).join('');
    return _table(['Nome'], linhas);
}

// ─── MODAIS ──────────────────────────────────────────────────────

function setupModais() {
    // Usuário
    document.getElementById('modal-usuario-fechar').addEventListener('click', () => fechar('modal-usuario'));
    document.getElementById('btn-cancelar-usuario').addEventListener('click', () => fechar('modal-usuario'));
    document.getElementById('modal-usuario').addEventListener('click', e => {
        if (e.target.id === 'modal-usuario') fechar('modal-usuario');
    });
    document.getElementById('btn-salvar-usuario').addEventListener('click', salvarUsuario);

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

function abrir(id) { document.getElementById(id)?.classList.add('open'); }
function fechar(id) { document.getElementById(id)?.classList.remove('open'); }
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
        case 'tipos-defeito':abrirModalTipoDefeito(item); break;
    }
}

// ─── MODAL USUÁRIO ───────────────────────────────────────────────
function abrirModalUsuario(u) {
    const editando = !!u;
    document.getElementById('modal-usuario-titulo').textContent = editando ? `Editar Usuário` : 'Novo Usuário';
    document.getElementById('usuario-id').value        = u?.id || '';
    document.getElementById('usuario-re').value        = u?.re || '';
    document.getElementById('usuario-re').disabled     = editando; // RE não pode mudar
    document.getElementById('usuario-nome').value      = u?.nome || '';
    document.getElementById('usuario-cpf').value       = '';
    document.getElementById('usuario-perfil').value    = u?.perfil || '';
    document.getElementById('usuario-ativo').value     = String(u?.ativo ?? true);
    document.getElementById('usuario-senha').value     = '';

    // Na criação: CPF obrigatório. Na edição: campo de senha opcional aparece
    document.getElementById('usuario-cpf-group').style.display  = editando ? 'none' : '';
    document.getElementById('usuario-senha-group').style.display = editando ? '' : 'none';
    document.getElementById('usuario-ativo-group').style.display = editando ? '' : 'none';

    erroModal('modal-usuario-erro', '');
    abrir('modal-usuario');
    document.getElementById(editando ? 'usuario-nome' : 'usuario-re').focus();
}

async function salvarUsuario() {
    const id    = document.getElementById('usuario-id').value;
    const editando = !!id;

    erroModal('modal-usuario-erro', '');

    if (editando) {
        // PATCH — só o que pode mudar
        const payload = {};
        const nome   = document.getElementById('usuario-nome').value.trim();
        const perfil = document.getElementById('usuario-perfil').value;
        const ativo  = document.getElementById('usuario-ativo').value === 'true';
        const senha  = document.getElementById('usuario-senha').value;

        if (!nome)   return erroModal('modal-usuario-erro', 'Nome é obrigatório.');
        if (!perfil) return erroModal('modal-usuario-erro', 'Perfil é obrigatório.');

        payload.nome   = nome;
        payload.perfil = perfil;
        payload.ativo  = ativo;
        if (senha) {
            if (senha.length < 6) return erroModal('modal-usuario-erro', 'Senha precisa ter pelo menos 6 caracteres.');
            payload.senha = senha;
        }

        try {
            await apiPatch(`/usuarios/${id}`, payload);
            fechar('modal-usuario');
            carregarAba();
        } catch (err) {
            erroModal('modal-usuario-erro', err.message);
        }
    } else {
        // POST — criação
        const re     = document.getElementById('usuario-re').value.trim();
        const nome   = document.getElementById('usuario-nome').value.trim();
        const cpf    = document.getElementById('usuario-cpf').value.trim();
        const perfil = document.getElementById('usuario-perfil').value;

        if (!re)     return erroModal('modal-usuario-erro', 'RE é obrigatório.');
        if (!nome)   return erroModal('modal-usuario-erro', 'Nome é obrigatório.');
        if (!cpf)    return erroModal('modal-usuario-erro', 'CPF é obrigatório (últimos 4 dígitos viram senha inicial).');
        if (!perfil) return erroModal('modal-usuario-erro', 'Perfil é obrigatório.');

        try {
            await apiPost('/usuarios', { re, nome, cpf, perfil });
            fechar('modal-usuario');
            carregarAba();
        } catch (err) {
            erroModal('modal-usuario-erro', err.message);
        }
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
    document.getElementById('linha-codigo').value = l?.codigo || '';
    document.getElementById('linha-codigo').disabled = editando;
    document.getElementById('linha-nome').value   = l?.nome || '';
    document.getElementById('linha-setor').value  = l?.setor || 'E2';
    document.getElementById('linha-setor').disabled = editando; // setor não muda depois
    document.getElementById('linha-ativa').value  = String(l?.ativa ?? true);
    document.getElementById('linha-ativa-group').style.display = editando ? '' : 'none';

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
