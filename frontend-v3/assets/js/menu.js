/*
 * Menu de ações — Frontend V3 Sambaíba
 * ──────────────────────────────────────
 * Fornece o "três pontinhos" (⋮) com:
 *   • Ver Escala     — lista ônibus alocados com linha + horário
 *   • Imprimir       — imprime estado atual do pátio
 *   • Relatórios CSV — exporta CSV do estado atual
 *   • Dados          — exporta snapshot JSON
 *   • Limpar         — remove todas as alocações ativas via API
 *
 * Adaptado da V2 para trabalhar com o payload `lastFilas` do backend.
 * Não usa localStorage — o estado vem do polling.
 */

import { apiDelete } from './api.js';

// ────────────────────────────────────────────────────────────────
// INICIALIZAÇÃO — conecta eventos ao DOM
// ────────────────────────────────────────────────────────────────
export function initMenu({ getFilasSnapshot, onSuccess }) {
    const btnMenu  = document.getElementById('btn-menu-dots');
    const dropdown = document.getElementById('menu-dots-dropdown');
    if (!btnMenu || !dropdown) return;

    // Abre/fecha dropdown
    btnMenu.addEventListener('click', (e) => {
        e.stopPropagation();
        dropdown.classList.toggle('open');
    });
    document.addEventListener('click', () => dropdown.classList.remove('open'));

    // Fecha modal escala
    document.getElementById('modal-escala-fechar')
        ?.addEventListener('click', () => closeModal('modal-escala'));
    document.getElementById('modal-escala')
        ?.addEventListener('click', (e) => {
            if (e.target.id === 'modal-escala') closeModal('modal-escala');
        });

    // Busca na escala
    document.getElementById('search-escala')
        ?.addEventListener('input', (e) => renderListaEscala(getFilasSnapshot(), e.target.value));

    // Wire-up dos itens
    document.getElementById('menu-ver-escala')?.addEventListener('click', () => {
        closeDropdown();
        verEscala(getFilasSnapshot());
    });
    document.getElementById('menu-imprimir')?.addEventListener('click', () => {
        closeDropdown();
        imprimirPatio(getFilasSnapshot());
    });
    document.getElementById('menu-excel')?.addEventListener('click', () => {
        closeDropdown();
        exportarExcel(getFilasSnapshot());
    });
    document.getElementById('menu-dados')?.addEventListener('click', () => {
        closeDropdown();
        exportarDados(getFilasSnapshot());
    });
    document.getElementById('menu-limpar')?.addEventListener('click', () => {
        closeDropdown();
        limparPatio(getFilasSnapshot(), onSuccess);
    });

    function closeDropdown() {
        dropdown.classList.remove('open');
    }
}

// ────────────────────────────────────────────────────────────────
// HELPERS INTERNOS
// ────────────────────────────────────────────────────────────────
function openModal(id)  { document.getElementById(id)?.classList.add('open'); }
function closeModal(id) { document.getElementById(id)?.classList.remove('open'); }

/** Achata todas as filas num array de ônibus ordenado por frota. */
function todosOnibus(filas) {
    const lista = [];
    for (const fila of filas || []) {
        for (const o of fila.onibus || []) {
            lista.push({
                ...o,
                fila_nome:   fila.fila_nome,
                fila_tipo:   fila.fila_tipo,
                fila_numero: fila.fila_numero,
            });
        }
    }
    return lista.sort((a, b) => a.numero_frota - b.numero_frota);
}

/** "04:30:00" → "04:30". Retorna "—" se vazio. */
function formatHora(hhmmss) {
    if (!hhmmss) return '—';
    const [h, m] = hhmmss.split(':');
    return `${h}:${m}`;
}

/** Nome legível da fila: numéricas viram "Fila 01", especiais usam o nome. */
function nomeFila(o) {
    if (o.fila_tipo === 'NUMERICA' && o.fila_numero != null) {
        return `Fila ${String(o.fila_numero).padStart(2, '0')}`;
    }
    return o.fila_nome || '—';
}

// ────────────────────────────────────────────────────────────────
// VER ESCALA
// ────────────────────────────────────────────────────────────────
function renderListaEscala(filas, busca) {
    const q    = (busca || '').toLowerCase().trim();
    const todos = todosOnibus(filas);
    // Mostra todos os alocados — mesmo sem hora/linha (pode ser carregou sem escala)
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
                        <td style="padding:4px 8px;font-family:monospace">${formatHora(o.horario_saida)}</td>
                        <td style="padding:4px 8px">${o.linha_codigo || '—'}</td>
                        <td style="padding:4px 8px;color:#888;font-size:11px">${nomeFila(o)}</td>
                    </tr>`).join('')}
            </tbody>
        </table>`;
}

function verEscala(filas) {
    const searchEl = document.getElementById('search-escala');
    if (searchEl) searchEl.value = '';
    renderListaEscala(filas, '');
    openModal('modal-escala');
}

// ────────────────────────────────────────────────────────────────
// IMPRIMIR PÁTIO
// ────────────────────────────────────────────────────────────────
function imprimirPatio(filas) {
    const now  = new Date();
    const meta = `Gerado em ${now.toLocaleDateString('pt-BR')} às `
               + `${now.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
    const lista = todosOnibus(filas);

    const html = `
        <h2 style="margin:0 0 4px;font-size:14px">Estado do Pátio — Sambaíba</h2>
        <p style="font-size:10px;color:#888;margin:0 0 8px">${meta} · ${lista.length} veículos alocados</p>
        <table style="width:100%;border-collapse:collapse;font-size:11px">
            <thead>
                <tr style="background:#000;color:#fff">
                    <th style="padding:4px 6px;text-align:left">Frota</th>
                    <th style="padding:4px 6px;text-align:left">Setor</th>
                    <th style="padding:4px 6px;text-align:left">Fila</th>
                    <th style="padding:4px 6px;text-align:left">Pos</th>
                    <th style="padding:4px 6px;text-align:left">Linha</th>
                    <th style="padding:4px 6px;text-align:left">Hora</th>
                    <th style="padding:4px 6px;text-align:left">Status</th>
                </tr>
            </thead>
            <tbody>
                ${lista.map((o, i) => {
                    const st       = o.alerta_tipo || '';
                    const stStyle  = st === 'PRESO' ? 'color:red;font-weight:800' : '';
                    return `<tr style="${i % 2 ? 'background:#f5f5f5' : ''}">
                        <td style="padding:3px 6px;font-weight:800;font-family:monospace">${o.numero_frota}</td>
                        <td style="padding:3px 6px">${o.setor || '—'}</td>
                        <td style="padding:3px 6px">${nomeFila(o)}</td>
                        <td style="padding:3px 6px">${o.posicao || '—'}</td>
                        <td style="padding:3px 6px">${o.linha_codigo || '—'}</td>
                        <td style="padding:3px 6px;font-family:monospace">${formatHora(o.horario_saida)}</td>
                        <td style="padding:3px 6px;${stStyle}">${st || '—'}</td>
                    </tr>`;
                }).join('')}
            </tbody>
        </table>`;

    let printArea = document.getElementById('print-content');
    if (!printArea) {
        printArea = document.createElement('div');
        printArea.id = 'print-content';
        document.body.appendChild(printArea);
    }
    printArea.innerHTML = html;
    window.print();
}

// ────────────────────────────────────────────────────────────────
// RELATÓRIOS CSV (abre como Excel)
// ────────────────────────────────────────────────────────────────
function exportarExcel(filas) {
    const now  = new Date();
    const nome = 'Patio_' + now.toLocaleDateString('pt-BR').replace(/\//g, '-');

    const cabecalho = ['Frota', 'Setor', 'Fila', 'Posição', 'Linha', 'Horário', 'Status'];
    const dados = todosOnibus(filas).map(o => [
        o.numero_frota,
        o.setor || '',
        nomeFila(o),
        o.posicao || '',
        o.linha_codigo || '',
        formatHora(o.horario_saida),
        o.alerta_tipo || '',
    ]);

    // BOM para Excel reconhecer UTF-8; separador ; para pt-BR
    const bom = '﻿';
    const csv = bom + [cabecalho, ...dados]
        .map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(';'))
        .join('\r\n');

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = nome + '.csv';
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
    a.href     = url;
    a.download = 'patio_sambaiba_' + new Date().toLocaleDateString('pt-BR').replace(/\//g, '-') + '.json';
    a.click();
    URL.revokeObjectURL(url);
}

// ────────────────────────────────────────────────────────────────
// LIMPAR PÁTIO
// ────────────────────────────────────────────────────────────────
async function limparPatio(filas, onSuccess) {
    const ids = (filas || [])
        .flatMap(f => (f.onibus || []).map(o => o.alocacao_id))
        .filter(Boolean);

    if (ids.length === 0) {
        alert('Pátio já está vazio.');
        return;
    }

    const confirmado = confirm(
        `LIMPAR PÁTIO\n\nRemove ${ids.length} veículo(s) de todas as filas.\nEsta ação não pode ser desfeita.\n\nConfirma?`
    );
    if (!confirmado) return;

    let erros = 0;
    for (const id of ids) {
        try {
            await apiDelete(`/alocacoes/${id}`);
        } catch (e) {
            erros++;
            console.error('[menu] erro ao remover alocação:', id, e?.message);
        }
    }

    const msg = erros > 0
        ? `Concluído com ${erros} erro(s). Verifique o pátio.`
        : `${ids.length} veículo(s) removido(s) com sucesso.`;
    alert(msg);
    onSuccess();
}
