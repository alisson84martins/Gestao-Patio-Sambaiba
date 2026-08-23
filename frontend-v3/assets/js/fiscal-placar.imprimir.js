/*
 * fiscal-placar.imprimir.js — Placar impresso por linha (Bloco E, §7)
 * ----------------------------------------------------------------------
 * Uma folha A4 por linha: código, ICV da semana anterior, meta ao lado,
 * e a evolução dos últimos 7 dias. Molde de ocorrencia.imprimir.js —
 * mesmo #print-content compartilhado (a regra que esconde o resto da
 * página em @media print já é global, style.css:1525/1527).
 *
 * ⛔ Sem nome, RE ou qualquer identificação de pessoa. O placar é da
 * linha, vai afixado no ponto de apresentação — e o gargalo declarado é
 * justamente "ICV é sinônimo de advertência". Um placar com nome confirma
 * o medo em vez de desfazê-lo (§7 do prompt).
 */
import { escapeHtml } from './escape.js';

function fmtPercentual(v) {
    return (v === null || v === undefined) ? '—' : `${Number(v).toFixed(2)}%`;
}

function fmtDataCurta(iso) {
    if (!iso) return '—';
    const [, mes, dia] = iso.split('-');
    return `${dia}/${mes}`;
}

function fmtDataLonga(iso) {
    if (!iso) return '—';
    const [ano, mes, dia] = iso.split('-');
    return `${dia}/${mes}/${ano}`;
}

/**
 * @param {object} dados — payload de GET /fiscalizacao/icv/placar/{linha}:
 *   { linha_codigo, data_referencia, meta_icv, icv_semana_anterior, evolucao: [{data_referencia, icv, fonte}] }
 */
export function imprimirPlacarLinha(dados) {
    const linhasEvolucao = dados.evolucao.map(dia => `
        <tr>
            <td>${fmtDataCurta(dia.data_referencia)}</td>
            <td>${fmtPercentual(dia.icv)}</td>
            <td>${escapeHtml(dia.fonte || '—')}</td>
        </tr>
    `).join('');

    const folha = `
        <div class="fp-print-folha">
            <div class="fp-print-cab">
                <span class="fp-print-marca">Sambaíba</span>
                <h1>PLACAR DA LINHA</h1>
                <span class="fp-print-data">Emitido em ${fmtDataLonga(dados.data_referencia)}</span>
            </div>

            <div class="fp-print-linha-codigo">${escapeHtml(dados.linha_codigo)}</div>

            <div class="fp-print-metricas">
                <div class="fp-print-metrica">
                    <div class="fp-print-metrica-rot">ICV — semana anterior</div>
                    <div class="fp-print-metrica-val">${fmtPercentual(dados.icv_semana_anterior)}</div>
                </div>
                <div class="fp-print-metrica">
                    <div class="fp-print-metrica-rot">Meta</div>
                    <div class="fp-print-metrica-val">${fmtPercentual(dados.meta_icv)}</div>
                </div>
            </div>

            <div>
                <div class="fp-print-faixa">Evolução dos últimos 7 dias</div>
                <table class="fp-print-evolucao">
                    <thead><tr><th>Data</th><th>ICV</th><th>Fonte</th></tr></thead>
                    <tbody>${linhasEvolucao || '<tr><td colspan="3">Sem dado no período.</td></tr>'}</tbody>
                </table>
            </div>

            <div class="fp-print-rodape">Sambaíba Transportes Urbanos · Placar da linha, sem identificação de pessoa</div>
        </div>
    `;

    let area = document.getElementById('print-content');
    if (!area) {
        area = document.createElement('div');
        area.id = 'print-content';
        document.body.appendChild(area);
    }
    area.innerHTML = folha;
    window.print();
}
