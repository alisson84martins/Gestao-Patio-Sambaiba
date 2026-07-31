/*
 * Menu de ações — Frontend V3 Sambaíba
 * ──────────────────────────────────────
 * Reproduz o menu "três pontinhos" da V2 com submenus colapsáveis.
 *
 * Itens:
 *   • Ver Escala       — lista ônibus alocados com linha + horário
 *   • Imprimir ›       — submenu: Pátio | Escala do dia | Frota completa
 *   • Relatórios Excel ›— submenu: Pátio | Escala do dia | Frota completa
 *   • Dados ›          — submenu: Exportar JSON | Ir para Importação
 *   • Limpar           — remove todas as alocações ativas (com confirm)
 */

import { apiDelete } from './api.js';

// ────────────────────────────────────────────────────────────────
// INICIALIZAÇÃO — conecta todos os eventos ao DOM
// ────────────────────────────────────────────────────────────────
export function initMenu({ getFilasSnapshot, onSuccess }) {
    const btnMenu  = document.getElementById('btn-menu-dots');
    const dropdown = document.getElementById('menu-dots-dropdown');
    if (!btnMenu || !dropdown) return;

    // Abre/fecha o dropdown principal
    btnMenu.addEventListener('click', (e) => {
        e.stopPropagation();
        dropdown.classList.toggle('open');
        // Fecha todos os submenus ao fechar o dropdown
        if (!dropdown.classList.contains('open')) _fecharSubmenus();
    });

    // Clique fora fecha tudo
    document.addEventListener('click', () => {
        dropdown.classList.remove('open');
        _fecharSubmenus();
    });
    dropdown.addEventListener('click', (e) => e.stopPropagation());

    // ── Ver Escala ────────────────────────────────────────────────
    document.getElementById('menu-ver-escala')
        ?.addEventListener('click', () => { _fecharMenu(); verEscala(getFilasSnapshot()); });

    // Busca dentro do modal de escala
    document.getElementById('search-escala')
        ?.addEventListener('input', (e) => renderListaEscala(getFilasSnapshot(), e.target.value));

    // Fecha modal escala
    document.getElementById('modal-escala-fechar')
        ?.addEventListener('click', () => _closeModal('modal-escala'));
    document.getElementById('modal-escala')
        ?.addEventListener('click', (e) => {
            if (e.target.id === 'modal-escala') _closeModal('modal-escala');
        });

    // ── Submenus (toggle) ────────────────────────────────────────
    _bindSubmenu('menu-imprimir-parent', 'sub-imprimir');
    _bindSubmenu('menu-excel-parent',    'sub-excel');
    _bindSubmenu('menu-dados-parent',    'sub-dados');

    // ── Imprimir → sub-itens ──────────────────────────────────────
    document.getElementById('menu-imp-patio')
        ?.addEventListener('click', () => { _fecharMenu(); imprimirPatio(getFilasSnapshot()); });
    document.getElementById('menu-imp-escala')
        ?.addEventListener('click', () => { _fecharMenu(); imprimirRelatorio('escala', getFilasSnapshot()); });
    document.getElementById('menu-imp-frota')
        ?.addEventListener('click', () => { _fecharMenu(); imprimirRelatorio('frota', getFilasSnapshot()); });

    // ── Relatórios Excel → sub-itens ─────────────────────────────
    document.getElementById('menu-xls-patio')
        ?.addEventListener('click', () => { _fecharMenu(); exportarExcel('patio', getFilasSnapshot()); });
    document.getElementById('menu-xls-escala')
        ?.addEventListener('click', () => { _fecharMenu(); exportarExcel('escala', getFilasSnapshot()); });
    document.getElementById('menu-xls-frota')
        ?.addEventListener('click', () => { _fecharMenu(); exportarExcel('frota', getFilasSnapshot()); });

    // ── Dados → sub-itens ────────────────────────────────────────
    document.getElementById('menu-dados-exportar')
        ?.addEventListener('click', () => { _fecharMenu(); exportarDados(getFilasSnapshot()); });
    document.getElementById('menu-dados-importar')
        ?.addEventListener('click', () => { _fecharMenu(); window.location.href = 'importacao.html'; });

    // ── Limpar Escala ────────────────────────────────────────────
    document.getElementById('menu-limpar-escala')
        ?.addEventListener('click', () => { _fecharMenu(); limparEscala(onSuccess); });

    // ── Limpar Pátio ─────────────────────────────────────────────
    document.getElementById('menu-limpar')
        ?.addEventListener('click', () => { _fecharMenu(); limparPatio(getFilasSnapshot(), onSuccess); });
}

// ────────────────────────────────────────────────────────────────
// HELPERS DE UI
// ────────────────────────────────────────────────────────────────
function _fecharMenu() {
    document.getElementById('menu-dots-dropdown')?.classList.remove('open');
    _fecharSubmenus();
}

function _fecharSubmenus() {
    document.querySelectorAll('.menu-dots-submenu').forEach(s => s.classList.remove('open'));
    document.querySelectorAll('.menu-dots-arrow').forEach(a => a.textContent = '›');
}

function _bindSubmenu(parentId, subId) {
    document.getElementById(parentId)?.addEventListener('click', (e) => {
        e.stopPropagation();
        const sub   = document.getElementById(subId);
        const arrow = document.querySelector(`#${parentId} .menu-dots-arrow`);
        const isOpen = sub?.classList.contains('open');

        // Fecha outros submenus antes de abrir este
        _fecharSubmenus();

        if (!isOpen) {
            sub?.classList.add('open');
            if (arrow) arrow.textContent = '⌄';
        }
    });
}

function _openModal(id)  { document.getElementById(id)?.classList.add('open'); }
function _closeModal(id) { document.getElementById(id)?.classList.remove('open'); }

// ────────────────────────────────────────────────────────────────
// HELPERS DE DADOS
// ────────────────────────────────────────────────────────────────
/** Achata todas as filas num array de ônibus, ordenado por frota. */
function _todosOnibus(filas) {
    const lista = [];
    for (const fila of filas || []) {
        for (const o of fila.onibus || []) {
            lista.push({
                ...o,
                fila_nome:       fila.fila_nome,
                fila_tipo:       fila.fila_tipo,
                fila_numero:     fila.fila_numero,
                fila_abreviacao: fila.fila_abreviacao,
            });
        }
    }
    return lista.sort((a, b) => a.numero_frota - b.numero_frota);
}

/** Rótulo curto da fila pro impresso: abreviação quando existir, senão o nome. */
function _rotuloFilaImpresso(o) {
    if (o.fila_abreviacao) return o.fila_abreviacao;
    if (o.fila_tipo === 'NUMERICA' && o.fila_numero != null) {
        return String(o.fila_numero).padStart(2, '0');
    }
    return o.fila_nome || '—';
}

/** "04:30:00" → "04:30". Retorna "—" se vazio. */
function _fmtHora(hhmmss) {
    if (!hhmmss) return '—';
    const [h, m] = hhmmss.split(':');
    return `${h}:${m}`;
}

/** Nome legível da fila. */
function _nomeFila(o) {
    if (o.fila_tipo === 'NUMERICA' && o.fila_numero != null) {
        return `Fila ${String(o.fila_numero).padStart(2, '0')}`;
    }
    return o.fila_nome || '—';
}

function _dataHoje() {
    return new Date().toLocaleDateString('pt-BR');
}

function _horaAgora() {
    return new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

// ────────────────────────────────────────────────────────────────
// VER ESCALA
// ────────────────────────────────────────────────────────────────
function renderListaEscala(filas, busca) {
    const q     = (busca || '').toLowerCase().trim();
    const todos = _todosOnibus(filas);
    const lista = todos.filter(o =>
        !q ||
        String(o.numero_frota).includes(q) ||
        (o.linha_codigo || '').toLowerCase().includes(q)
    );

    const container = document.getElementById('escala-content');
    if (!container) return;

    if (!lista.length) {
        container.innerHTML =
            '<div style="padding:24px;text-align:center;color:#888">Nenhum veículo alocado no momento</div>';
        return;
    }

    container.innerHTML = `
        <div style="font-size:11px;color:#888;margin-bottom:8px;font-family:monospace">${lista.length} veículos</div>
        <table style="width:100%;border-collapse:collapse;font-size:12px">
            <thead>
                <tr style="background:#111;color:#fff">
                    <th style="padding:6px 8px;text-align:left">Frota</th>
                    <th style="padding:6px 8px;text-align:left">Hora</th>
                    <th style="padding:6px 8px;text-align:left">Linha</th>
                    <th style="padding:6px 8px;text-align:left">Fila</th>
                </tr>
            </thead>
            <tbody>
                ${lista.map((o, i) => `
                    <tr style="${i % 2 ? 'background:#f5f5f5' : ''}">
                        <td style="padding:4px 8px;font-weight:800;font-family:monospace">${o.numero_frota}</td>
                        <td style="padding:4px 8px;font-family:monospace">${_fmtHora(o.horario_saida)}</td>
                        <td style="padding:4px 8px">${o.linha_codigo || '—'}</td>
                        <td style="padding:4px 8px;color:#888;font-size:11px">${_nomeFila(o)}</td>
                    </tr>`).join('')}
            </tbody>
        </table>`;
}

function verEscala(filas) {
    const searchEl = document.getElementById('search-escala');
    if (searchEl) searchEl.value = '';
    renderListaEscala(filas, '');
    _openModal('modal-escala');
}

// ────────────────────────────────────────────────────────────────
// IMPRIMIR
// ────────────────────────────────────────────────────────────────
function _print(html) {
    let area = document.getElementById('print-content');
    if (!area) {
        area = document.createElement('div');
        area.id = 'print-content';
        document.body.appendChild(area);
    }
    area.innerHTML = html;
    window.print();
}

/*
 * Impressão do pátio — layout aprovado 31/07/2026.
 * Quem lê: o motorista, em pé, de longe, procurando o número do carro
 * dele pra saber em que fila está. Por isso CARRO e FILA saem no mesmo
 * tamanho/peso (19px negrito) — são os dois que ele lê — e a posição
 * (informação secundária) sai pequena e cinza, colada depois da fila.
 * Não existe coluna POS. Ver PROMPT-CORRECOES-PATIO.md seção 3.
 */
function imprimirPatio(filas) {
    const ROWS_PER_COL  = 28;
    const COLS_PER_PAGE = 4;
    const ROWS_PER_PAGE = ROWS_PER_COL * COLS_PER_PAGE; // 112 por folha

    // _todosOnibus já ordena por número do carro (crescente) — é assim que
    // o motorista busca, não pela fila.
    const todos = _todosOnibus(filas);

    // Fallback: se setor não vier do backend, infere pelo prefixo do número
    function _setorOf(o) {
        if (o.setor) return o.setor;
        return String(o.numero_frota).startsWith('1') ? 'E2' : 'AR2';
    }

    const e2  = todos.filter(o => _setorOf(o) === 'E2');
    const ar2 = todos.filter(o => _setorOf(o) === 'AR2');

    const dataHora = `${_dataHoje()} · ${_horaAgora()}`;

    function buildRow(o) {
        const preso = o.alerta_tipo === 'PRESO';
        const rotuloFila = _rotuloFilaImpresso(o);
        const posicao = o.posicao ?? '';
        // Rótulo e posição em spans separados dentro da célula (ver .pt-td-fila
        // no CSS): quem encolhe quando o espaço aperta é o rótulo da fila
        // (text-overflow:ellipsis), nunca a posição — ela tem largura própria,
        // fora do encolhimento.
        return `<tr${preso ? ' class="pt-row-preso"' : ''}>
            <td class="pt-td-carro">${o.numero_frota}</td>
            <td class="pt-td-fila"><span class="pt-td-fila-rotulo">${rotuloFila}${preso ? ' ⚠' : ''}</span><span class="pt-td-pos">${posicao}</span></td>
        </tr>`;
    }

    function buildSetor(lista, setor) {
        if (!lista.length) return '';
        const total = lista.length;
        const numPages = Math.max(1, Math.ceil(total / ROWS_PER_PAGE));
        let html = '';

        for (let p = 0; p < numPages; p++) {
            const pageItems = lista.slice(p * ROWS_PER_PAGE, (p + 1) * ROWS_PER_PAGE);
            html += `<div class="pt-page">
                <div class="pt-page-header">
                    <span class="pt-setor-titulo">SETOR ${setor}</span>
                    <span class="pt-page-meta">${dataHora} · pág. ${p + 1} de ${numPages}</span>
                </div>
                <div class="pt-grid">`;

            for (let c = 0; c < COLS_PER_PAGE; c++) {
                const colItems = pageItems.slice(c * ROWS_PER_COL, (c + 1) * ROWS_PER_COL);
                html += `<table class="pt-table">
                    <thead><tr>
                        <th class="pt-th-carro">Carro</th><th>Fila</th>
                    </tr></thead>
                    <tbody>${colItems.map(buildRow).join('')}</tbody>
                </table>`;
            }

            html += `</div>
                <div class="pt-page-footer">
                    <span>${pageItems.length} de ${total} carros do setor ${setor}</span>
                    <span>Gestão de Pátio · Sambaíba G3</span>
                </div>
            </div>`;
        }
        return html;
    }

    _print(buildSetor(e2, 'E2') + buildSetor(ar2, 'AR2'));
}

function imprimirRelatorio(tipo, filas) {
    const lista = _todosOnibus(filas);
    const meta = `${_dataHoje()} às ${_horaAgora()}`;
    let html = '';

    if (tipo === 'escala') {
        const com_escala = lista.filter(o => o.horario_saida || o.linha_codigo);
        html = `<h2 style="margin:0 0 4px;font-size:14px">Escala do Dia</h2>
            <p style="font-size:10px;color:#888;margin:0 0 8px">${meta} · ${com_escala.length} veículos</p>
            <table style="width:100%;border-collapse:collapse;font-size:11px">
                <thead><tr style="background:#000;color:#fff">
                    <th style="padding:4px 6px;text-align:left">Frota</th>
                    <th style="padding:4px 6px;text-align:left">Setor</th>
                    <th style="padding:4px 6px;text-align:left">Fila</th>
                    <th style="padding:4px 6px;text-align:left">Linha</th>
                    <th style="padding:4px 6px;text-align:left">Hora</th>
                </tr></thead>
                <tbody>
                    ${com_escala.map((o, i) => `<tr style="${i % 2 ? 'background:#f5f5f5' : ''}">
                        <td style="padding:3px 6px;font-weight:800;font-family:monospace">${o.numero_frota}</td>
                        <td style="padding:3px 6px">${o.setor || '—'}</td>
                        <td style="padding:3px 6px">${_nomeFila(o)}</td>
                        <td style="padding:3px 6px">${o.linha_codigo || '—'}</td>
                        <td style="padding:3px 6px;font-family:monospace">${_fmtHora(o.horario_saida)}</td>
                    </tr>`).join('')}
                </tbody>
            </table>`;
    } else if (tipo === 'frota') {
        html = `<h2 style="margin:0 0 4px;font-size:14px">Frota Completa — Pátio</h2>
            <p style="font-size:10px;color:#888;margin:0 0 8px">${meta} · ${lista.length} veículos</p>
            <table style="width:100%;border-collapse:collapse;font-size:11px">
                <thead><tr style="background:#000;color:#fff">
                    <th style="padding:4px 6px;text-align:left">Frota</th>
                    <th style="padding:4px 6px;text-align:left">Setor</th>
                    <th style="padding:4px 6px;text-align:left">Fila</th>
                    <th style="padding:4px 6px;text-align:left">Pos</th>
                    <th style="padding:4px 6px;text-align:left">Status</th>
                </tr></thead>
                <tbody>
                    ${lista.map((o, i) => `<tr style="${i % 2 ? 'background:#f5f5f5' : ''}">
                        <td style="padding:3px 6px;font-weight:800;font-family:monospace">${o.numero_frota}</td>
                        <td style="padding:3px 6px">${o.setor || '—'}</td>
                        <td style="padding:3px 6px">${_nomeFila(o)}</td>
                        <td style="padding:3px 6px">${o.posicao || '—'}</td>
                        <td style="padding:3px 6px">${o.alerta_tipo || '—'}</td>
                    </tr>`).join('')}
                </tbody>
            </table>`;
    }
    _print(html);
}

// ────────────────────────────────────────────────────────────────
// RELATÓRIOS CSV (abre como Excel)
// ────────────────────────────────────────────────────────────────
function exportarExcel(tipo, filas) {
    const lista = _todosOnibus(filas);
    const nome  = tipo === 'patio'  ? 'Patio'
                : tipo === 'escala' ? 'Escala'
                : 'Frota';

    let cabecalho, dados;

    if (tipo === 'patio') {
        cabecalho = ['Frota', 'Setor', 'Fila', 'Posição', 'Linha', 'Horário', 'Status'];
        dados = lista.map(o => [
            o.numero_frota, o.setor || '', _nomeFila(o),
            o.posicao || '', o.linha_codigo || '',
            _fmtHora(o.horario_saida), o.alerta_tipo || '',
        ]);
    } else if (tipo === 'escala') {
        cabecalho = ['Frota', 'Setor', 'Hora', 'Linha', 'Status'];
        dados = lista
            .filter(o => o.horario_saida || o.linha_codigo)
            .map(o => [
                o.numero_frota, o.setor || '',
                _fmtHora(o.horario_saida),
                o.linha_codigo || '', o.alerta_tipo || '',
            ]);
    } else { // frota
        cabecalho = ['Frota', 'Setor', 'Linha', 'Hora', 'Posição no Pátio'];
        dados = lista.map(o => [
            o.numero_frota, o.setor || '',
            o.linha_codigo || '', _fmtHora(o.horario_saida),
            `${_nomeFila(o)} pos ${o.posicao || '—'}`,
        ]);
    }

    const nomeFinal = `${nome}_${new Date().toLocaleDateString('pt-BR').replace(/\//g, '-')}`;
    const bom = '﻿';
    const csv = bom + [cabecalho, ...dados]
        .map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(';'))
        .join('\r\n');

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = nomeFinal + '.csv';
    a.click();
    URL.revokeObjectURL(url);
}

// ────────────────────────────────────────────────────────────────
// EXPORTAR DADOS (snapshot JSON)
// ────────────────────────────────────────────────────────────────
function exportarDados(filas) {
    const snapshot = {
        exportadoEm: new Date().toISOString(),
        sistema: 'Gestão de Pátio Sambaíba V3',
        filas,
    };
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `patio_sambaiba_${new Date().toLocaleDateString('pt-BR').replace(/\//g, '-')}.json`;
    a.click();
    URL.revokeObjectURL(url);
}

// ────────────────────────────────────────────────────────────────
// LIMPAR PÁTIO
// ────────────────────────────────────────────────────────────────
async function limparEscala(onSuccess) {
    const ok = confirm(
        `LIMPAR ESCALA\n\nRemove toda a escala do dia atual.\n` +
        `Os ônibus continuam no pátio, mas perdem linha e horário.\n\n` +
        `Esta ação não pode ser desfeita.\n\nConfirma?`
    );
    if (!ok) return;

    try {
        const resultado = await apiDelete('/escalas');
        const removidas = resultado?.removidas ?? 0;
        alert(`Escala limpa: ${removidas} registro(s) removido(s).`);
        onSuccess();
    } catch (err) {
        alert(`Erro ao limpar escala: ${err?.message || 'Erro desconhecido'}`);
    }
}

async function limparPatio(filas, onSuccess) {
    const total = (filas || []).reduce((acc, f) => acc + (f.onibus || []).length, 0);

    if (total === 0) {
        alert('Pátio já está vazio.');
        return;
    }

    const ok = confirm(
        `LIMPAR PÁTIO\n\nRemove ${total} veículo(s) de todas as filas.\n` +
        `Esta ação não pode ser desfeita.\n\nConfirma?`
    );
    if (!ok) return;

    try {
        // Operação atômica no servidor — ou limpa tudo, ou não limpa nada
        const resultado = await apiDelete('/alocacoes');
        const removidas = resultado?.removidas ?? total;
        alert(`${removidas} veículo(s) removido(s) com sucesso.`);
        onSuccess();
    } catch (err) {
        alert(`Erro ao limpar pátio: ${err?.message || 'Erro desconhecido'}`);
    }
}
