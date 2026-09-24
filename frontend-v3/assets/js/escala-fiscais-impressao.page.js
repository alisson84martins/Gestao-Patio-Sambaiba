/*
 * Escala de Fiscais — impressão igual à planilha (Fase 5)
 * ------------------------------------------------------------------
 * Lê GET /escala-fiscais/dias/{data} (a escala gravada; num dia ainda não
 * salvo, a prévia do modelo) e desenha a folha como a planilha: título,
 * bloco TP à esquerda e TS à direita, cada posto em duas linhas (Início /
 * Término) com as células mescladas, dobra com fundo #FFFF00 (vinda do
 * cálculo do backend; congelada se publicada), marcador exatamente como
 * escrito e o rodapé (plantão, férias, folgas, atestado, afastados, trocas).
 *
 * Cabe numa folha A4: depois de desenhar, reduz o conteúdo (zoom) até caber
 * na área útil — o mesmo "ajustar a ~70%" da planilha.
 * Data da URL (?data=AAAA-MM-DD) — nunca toISOString().
 */
import { requireAuth } from './auth.js';
import { apiGet, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { dataLocalISO } from './data.util.js';

if (!requireAuth()) {
    throw new Error('Sessao nao autenticada');
}

// Área útil da A4 retrato com as margens do @page (198mm x 281mm) em px CSS.
const MM = 96 / 25.4;
const LARGURA_UTIL = 198 * MM;
const ALTURA_UTIL = 281 * MM;

const params = new URLSearchParams(location.search);
const data = /^\d{4}-\d{2}-\d{2}$/.test(params.get('data') || '') ? params.get('data') : dataLocalISO();

document.getElementById('efi-imprimir').addEventListener('click', () => window.print());
carregar();

async function carregar() {
    const estado = document.getElementById('efi-estado');
    try {
        const r = await apiGet(`/escala-fiscais/dias/${data}`);
        desenhar(r);
        const situacao = { novo: 'PRÉVIA do modelo — ainda não salva', rascunho: 'Rascunho', publicada: `Publicada · versão ${r.versao}` }[r.status];
        estado.innerHTML = r.status === 'novo' ? `<span class="efi-aviso">${escapeHtml(situacao)}</span>` : escapeHtml(situacao);
        document.title = r.titulo_tp.replace(/ - TP$/, '');
        ajustarNaFolha();
        document.body.dataset.pronto = '1';
        if (params.get('auto') === '1') window.print();
    } catch (err) {
        estado.innerHTML = `<span class="efi-aviso">${escapeHtml(err instanceof ApiError ? err.message : 'Erro ao carregar')}</span>`;
    }
}

function ajustarNaFolha() {
    const el = document.getElementById('efi-conteudo');
    el.style.zoom = '1';
    const escala = Math.min(1, LARGURA_UTIL / el.scrollWidth, ALTURA_UTIL / el.scrollHeight);
    el.style.zoom = String(Math.floor(escala * 1000) / 1000);
}

// ─── Blocos TP / TS ──────────────────────────────────────────────────────

function textoCelula(p) {
    if (!p) return '';
    return p.re ?? p.marcador ?? '';
}

function periodo(p) {
    if (!p) {
        return ['<td rowspan="2"></td><td rowspan="2"></td><td class="efi-rot">Início</td><td></td>',
            '<td class="efi-rot">Término</td><td></td>'];
    }
    const dobra = p.dobra ? ' efi-dobra' : '';
    return [
        `<td rowspan="2" class="efi-linhas">${p.linhas.map(escapeHtml).join(' ')}</td>
         <td rowspan="2" class="efi-re${dobra}">${escapeHtml(textoCelula(p))}</td>
         <td class="efi-rot">Início</td><td>${escapeHtml(p.hora_inicio || '')}</td>`,
        `<td class="efi-rot">Término</td><td>${escapeHtml(p.hora_termino || '')}</td>`,
    ];
}

function bloco(r, lado) {
    const linhas = r.linhas.filter(l => l.lado === lado);
    const titulo = lado === 'TP' ? r.titulo_tp : r.titulo_ts;
    const corpo = linhas.map(l => {
        const [a1, b1] = periodo(l.p1);
        const [a2, b2] = periodo(l.p2);
        return `<tr><td rowspan="2">${lado}</td><td rowspan="2">${escapeHtml(l.cod_jb || '')}</td>
                    <td rowspan="2">${escapeHtml(l.lote || '')}</td>${a1}${a2}</tr>
                <tr class="efi-fim">${b1}${b2}</tr>`;
    }).join('');
    return `<table class="efi">
        <thead>
            <tr><th colspan="11" class="efi-titulo">${escapeHtml(titulo)}</th></tr>
            <tr><th>L</th><th>Cód. JB</th><th>LOTE</th><th>LINHAS</th><th>1º PERÍODO</th><th colspan="2">${lado}</th>
                <th>LINHAS</th><th>2º PERÍODO</th><th colspan="2">${lado}</th></tr>
        </thead>
        <tbody>${corpo}</tbody>
    </table>`;
}

// ─── Rodapé ──────────────────────────────────────────────────────────────

// "4HS", "15HS", "02:30HS" — como a planilha escreve.
function horaHS(hhmm) {
    const [h, m] = hhmm.split(':');
    return m === '00' ? `${Number(h)}HS` : `${h}:${m}HS`;
}

function plantao(r, turno) {
    return r.plantao.filter(p => p.turno === turno).map(p => {
        const nome = (p.nome || p.re || '').split(/\s+/)[0].toUpperCase();
        return `<span>${escapeHtml(nome)} ${horaHS(p.hora_inicio)} / ${horaHS(p.hora_fim)}</span>`;
    }).join(' ') || '—';
}

function res(lista) {
    return lista.length ? lista.map(x => `<span>${escapeHtml(x.re)}</span>`).join('') : '<span>—</span>';
}

function porPeriodo(titulo, grupos) {
    const linhas = [['1°', grupos['1'] || []], ['2°', grupos['2'] || []]];
    if ((grupos['0'] || []).length) linhas.push(['', grupos['0']]);
    return `<div><h3>${escapeHtml(titulo)}</h3>${linhas.map(([rot, l]) =>
        `<div class="efi-res">${rot ? `<span class="efi-per">${rot}</span>` : ''}${res(l)}</div>`).join('')}</div>`;
}

function todos(grupos) {
    return [...(grupos['0'] || []), ...(grupos['1'] || []), ...(grupos['2'] || [])];
}

function rodape(r) {
    const s = r.resumo;
    const partes = [
        `<div class="efi-inteiro"><h3>COORDENADORES PLANTÃO</h3>
            <div class="efi-plantao"><div><b>MANHÃ</b>${plantao(r, 'manha')}</div><div><b>TARDE</b>${plantao(r, 'tarde')}</div></div></div>`,
        porPeriodo('DESPACHANTES DE FOLGA', s.folgas),
        porPeriodo('DESPACHANTES FÉRIAS', s.ausentes.ferias),
        `<div><h3>ATESTADO MÉDICO</h3><div class="efi-res">${res(todos(s.ausentes.atestado))}</div></div>`,
    ];
    const afastados = todos(s.ausentes.afastado);
    if (afastados.length || r.tipo_dia === 'util') {
        partes.push(`<div><h3>DESPACHANTES AFASTADOS</h3><div class="efi-res">${res(afastados)}</div></div>`);
    }
    if (s.trocas.length) {
        partes.push(`<div class="efi-inteiro efi-trocas">${s.trocas.map(t =>
            `<div>${t.tipo === '2x2' ? 'FOLGA 2X2' : 'Folga 1x1'} ${escapeHtml(t.re_a)} ${escapeHtml(t.re_b)}</div>`).join('')}</div>`);
    }
    return `<div class="efi-rodape">${partes.join('')}</div>`;
}

function desenhar(r) {
    document.getElementById('efi-conteudo').innerHTML =
        `<div class="efi-blocos">${bloco(r, 'TP')}${bloco(r, 'TS')}</div>${rodape(r)}`;
}
