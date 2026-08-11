/*
 * Pré-ocorrência — abrir autorização, acompanhar, converter.
 * ------------------------------------------------------------
 * CCO só vê status (decisão 4 — nunca conteúdo); coordenador/ADMIN/
 * gerência veem a fila completa. A trava real é no backend (RBAC +
 * checagem de destinatário) — isto aqui só ajusta o que a tela mostra,
 * pra ninguém bater numa porta fechada sem saber por quê (mesmo padrão
 * de cadastros.js/permissoes.js).
 */
import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { podeLer, temFuncao } from './sessao.js';
import { escapeHtml } from './escape.js';
import { aplicarMascara } from './mascaras.js';

if (!requireAuth()) {
    throw new Error('Sessao nao autenticada');
}

const ehCCO = temFuncao('CCO');
const veFila = podeLer('pre_ocorrencia'); // CCO não tem — decisão 4

document.addEventListener('DOMContentLoaded', () => {
    setupHeader();
    setupModalAbrir();
    setupModalDetalhe();
    carregarMinhasAutorizacoes();
    if (veFila) {
        document.getElementById('secao-fila').style.display = '';
        carregarFila();
    }
});

function setupHeader() {
    const user = getCurrentUser();
    document.getElementById('user-name').textContent = user?.nome || '—';
    document.getElementById('user-meta').textContent = (user?.re || '—').toUpperCase();
    document.getElementById('btn-logout').addEventListener('click', () => {
        logout();
        window.location.replace('index.html');
    });
}

// ─── Minhas autorizações ────────────────────────────────────────────────

async function carregarMinhasAutorizacoes() {
    const container = document.getElementById('lista-autorizacoes');
    try {
        const lista = await apiGet('/pre-ocorrencias/autorizacoes');
        if (lista.length === 0) {
            container.innerHTML = '<div class="patio-loading">Nenhuma autorização aberta.</div>';
            return;
        }
        container.innerHTML = lista.map(a => ehCCO ? cardAutorizacaoCCO(a) : cardAutorizacaoCompleta(a)).join('');
    } catch (err) {
        container.innerHTML = `<div class="patio-loading" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

function cardAutorizacaoCCO(a) {
    // 🔴 Só status_texto — nada mais chega nesta resposta (ver
    // schemas/pre_ocorrencia.py AutorizacaoResumoCCO). Não tem o que
    // esconder na tela porque o backend já não manda.
    return `
        <div class="remanejo-card">
            <div>${escapeHtml(a.status_texto)}</div>
            <div style="font-size:0.75rem;color:var(--muted);margin-top:4px">Aberta em ${fmtDataHora(a.criada_em)}</div>
        </div>`;
}

function cardAutorizacaoCompleta(a) {
    const status = {
        AGUARDANDO: ['Aguardando o motorista', 'cinza'],
        ENVIADA: ['Enviada', 'verde'],
        EXPIRADA: ['Expirada', 'amarelo'],
        CANCELADA: ['Cancelada', 'cinza'],
    }[a.status] || [a.status, 'cinza'];
    return `
        <div class="remanejo-card">
            <div class="remanejo-card-header">
                <div>${escapeHtml(a.motorista_nome || 'Motorista não identificado')} ${a.prefixo ? `· ${escapeHtml(a.prefixo)}` : ''}</div>
                <span class="remanejo-badge">${escapeHtml(status[0])}</span>
            </div>
            <div style="font-size:0.75rem;color:var(--muted);margin-top:4px">
                Tel. ${escapeHtml(a.telefone_destino)} · aberta em ${fmtDataHora(a.criada_em)}
            </div>
        </div>`;
}

function fmtDataHora(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// ─── Fila ────────────────────────────────────────────────────────────────

async function carregarFila() {
    const container = document.getElementById('lista-fila');
    try {
        const lista = await apiGet('/pre-ocorrencias');
        if (lista.length === 0) {
            container.innerHTML = '<div class="patio-loading">Nada na fila.</div>';
            return;
        }
        container.innerHTML = lista.map(cardFila).join('');
        container.querySelectorAll('[data-id]').forEach(el => {
            el.addEventListener('click', () => abrirDetalhe(el.dataset.id));
        });
    } catch (err) {
        container.innerHTML = `<div class="patio-loading" style="color:var(--accent)">Erro ao carregar: ${escapeHtml(err.message)}</div>`;
    }
}

function cardFila(item) {
    const statusLabel = {
        AGUARDANDO: 'Aguardando motorista', ENVIADA: 'Enviada', CONVERTIDA: 'Convertida', DESCARTADA: 'Descartada',
    }[item.status] || item.status;
    const pendencias = item.pendencias.length
        ? `<span style="color:var(--accent2);font-size:0.75rem">Faltam: ${item.pendencias.map(escapeHtml).join(', ')}</span>`
        : '';
    return `
        <div class="remanejo-card" data-id="${item.id}" style="cursor:pointer">
            <div class="remanejo-card-header">
                <div>${escapeHtml(item.motorista_nome || 'Motorista não identificado')} ${item.prefixo ? `· ${escapeHtml(item.prefixo)}` : ''}</div>
                <span class="remanejo-badge">${escapeHtml(statusLabel)}</span>
            </div>
            <div style="font-size:0.75rem;color:var(--muted);margin-top:4px">${fmtDataHora(item.criado_em)}</div>
            ${pendencias}
        </div>`;
}

// ─── Modal: abrir autorização ───────────────────────────────────────────

function setupModalAbrir() {
    if (ehCCO) document.getElementById('abrir-coordenador-group').style.display = '';

    aplicarMascara(document.getElementById('abrir-telefone'), 'telefone');
    aplicarMascara(document.getElementById('abrir-re'), 're');
    if (ehCCO) aplicarMascara(document.getElementById('abrir-coordenador-re'), 're');

    document.getElementById('btn-abrir').addEventListener('click', () => abrirModal('modal-abrir'));
    document.getElementById('modal-abrir-fechar').addEventListener('click', () => fecharModal('modal-abrir'));
    document.getElementById('btn-cancelar-abrir').addEventListener('click', () => fecharModal('modal-abrir'));
    document.getElementById('btn-confirmar-abrir').addEventListener('click', confirmarAbrir);

    document.getElementById('modal-link-fechar').addEventListener('click', () => {
        fecharModal('modal-link');
        carregarMinhasAutorizacoes();
    });
    document.getElementById('btn-fechar-link').addEventListener('click', () => {
        fecharModal('modal-link');
        carregarMinhasAutorizacoes();
    });
    document.getElementById('btn-copiar-link').addEventListener('click', async () => {
        const input = document.getElementById('link-gerado');
        try {
            await navigator.clipboard.writeText(input.value);
        } catch {
            input.select(); // clipboard API pode falhar (sem HTTPS/permissão) — seleciona pra copiar manual
        }
    });
}

async function confirmarAbrir() {
    const erro = document.getElementById('modal-abrir-erro');
    erro.style.display = 'none';

    const telefone = document.getElementById('abrir-telefone').value.trim();
    if (!telefone) {
        erro.textContent = 'Informe o telefone do motorista.';
        erro.style.display = '';
        return;
    }

    const payload = {
        telefone_destino: telefone,
        prefixo: document.getElementById('abrir-prefixo').value.trim() || undefined,
        motorista_re: document.getElementById('abrir-re').value.trim() || undefined,
        motorista_nome: document.getElementById('abrir-nome').value.trim() || undefined,
    };
    if (ehCCO) {
        const re = document.getElementById('abrir-coordenador-re').value.trim();
        if (!re) {
            erro.textContent = 'Informe o RE do coordenador destino.';
            erro.style.display = '';
            return;
        }
        payload.coordenador_re = re;
    }

    try {
        const resultado = await apiPost('/pre-ocorrencias/autorizacoes', payload);
        fecharModal('modal-abrir');
        document.getElementById('link-gerado').value = resultado.link;
        abrirModal('modal-link');
        // Limpa o formulário pro próximo uso — é um toque, não deveria
        // carregar dado da chamada anterior.
        document.getElementById('abrir-telefone').value = '';
        document.getElementById('abrir-prefixo').value = '';
        document.getElementById('abrir-re').value = '';
        document.getElementById('abrir-nome').value = '';
        if (ehCCO) document.getElementById('abrir-coordenador-re').value = '';
    } catch (err) {
        erro.textContent = err instanceof ApiError ? err.message : 'Não foi possível abrir. Tente de novo.';
        erro.style.display = '';
    }
}

// ─── Modal: detalhe + converter ──────────────────────────────────────────

let preOcorrenciaAtualId = null;

function setupModalDetalhe() {
    document.getElementById('modal-detalhe-fechar').addEventListener('click', () => fecharModal('modal-detalhe'));
    document.getElementById('btn-fechar-detalhe').addEventListener('click', () => fecharModal('modal-detalhe'));
    document.getElementById('btn-converter').addEventListener('click', converter);
}

async function abrirDetalhe(id) {
    preOcorrenciaAtualId = id;
    const conteudo = document.getElementById('detalhe-conteudo');
    conteudo.innerHTML = 'Carregando…';
    document.getElementById('modal-detalhe-erro').style.display = 'none';
    abrirModal('modal-detalhe');

    try {
        const d = await apiGet(`/pre-ocorrencias/${id}`);
        conteudo.innerHTML = linhaDetalhe('Motorista', d.motorista_nome, d.motorista_re)
            + linhaDetalhe('Telefone', d.motorista_telefone)
            + linhaDetalhe('CPF', d.motorista_cpf) + linhaDetalhe('RG', d.motorista_rg) + linhaDetalhe('CNH', d.motorista_cnh)
            + linhaDetalhe('Cobrador', d.cobrador_nome, d.cobrador_re)
            + linhaDetalhe('Prefixo', d.prefixo)
            + linhaDetalhe('Data/hora', `${d.data_ocorrencia || '—'} ${d.hora_ocorrencia || ''}`)
            + linhaDetalhe('Local', [d.local_logradouro, d.local_numero, d.local_bairro, d.local_cidade].filter(Boolean).join(', ') || null)
            + `<div style="margin-top:0.75rem"><strong>Relato:</strong><br>${escapeHtml(d.relato || '—').replace(/\n/g, '<br>')}</div>`
            + (d.terceiro_nome || d.terceiro_placa ? `<div style="margin-top:0.75rem"><strong>Terceiro:</strong><br>${escapeHtml(d.terceiro_nome || '—')} · ${escapeHtml(d.terceiro_placa || '—')}</div>` : '')
            + `<div style="margin-top:0.75rem"><strong>Anexos:</strong> ${d.anexos.length ? d.anexos.map(a => escapeHtml(a.tipo)).join(', ') : 'nenhum'}</div>`;

        const btnConverter = document.getElementById('btn-converter');
        btnConverter.style.display = d.ocorrencia_id ? 'none' : '';
    } catch (err) {
        conteudo.innerHTML = '';
        document.getElementById('modal-detalhe-erro').textContent = err.message;
        document.getElementById('modal-detalhe-erro').style.display = '';
    }
}

function linhaDetalhe(rotulo, valor, extra) {
    if (!valor && !extra) return '';
    return `<div><strong>${escapeHtml(rotulo)}:</strong> ${escapeHtml(valor || '—')}${extra ? ` (RE ${escapeHtml(extra)})` : ''}</div>`;
}

async function converter() {
    if (!preOcorrenciaAtualId) return;
    const erro = document.getElementById('modal-detalhe-erro');
    erro.style.display = 'none';
    try {
        const resultado = await apiPost(`/pre-ocorrencias/${preOcorrenciaAtualId}/converter`, {});
        window.location.href = `ocorrencia-form.html?id=${resultado.ocorrencia_id}`;
    } catch (err) {
        erro.textContent = err instanceof ApiError ? err.message : 'Não foi possível converter.';
        erro.style.display = '';
    }
}

// ─── Modais — mesmo padrão de abrir/fechar via classe .open (modal.util.js
// já cuida de trava de scroll/teclado ao observar essa classe) ───────────

function abrirModal(id) {
    document.getElementById(id).classList.add('open');
}
function fecharModal(id) {
    document.getElementById(id).classList.remove('open');
}
