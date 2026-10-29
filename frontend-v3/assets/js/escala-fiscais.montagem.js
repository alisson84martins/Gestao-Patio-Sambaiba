/*
 * Escala de Fiscais — sub-aba "Montar escala" (Fase 4)
 * ------------------------------------------------------------------
 * Monta a escala de UM dia espelhando a planilha: bloco TP e bloco TS,
 * cada posto em duas linhas (Início e Término), na ordem do modelo.
 *
 * O backend é a fonte da verdade (app/services/escala_fiscais_regras.py).
 * Esta tela repete só o que dá para avisar mais rápido, na hora em que o RE
 * é digitado:
 *   - RN05: RE de férias/atestado/afastado → bloqueia e desfaz a célula;
 *   - RN03: RE de folga no dia → a célula fica amarela (dobra);
 *   - RN04: dobra de novo quem dobrou no fim de semana anterior → pergunta
 *     [Cancelar] [Sim, escalar]; o "sim" vai junto no salvar e o backend
 *     grava quem confirmou e quando.
 * Ao salvar, o backend avalia tudo de novo: 422 = bloqueio (nada foi
 * salvo); 409 = pergunta (RN04, acúmulo sem ponto final, RN11) — a tela
 * pergunta uma a uma e reenvia com as confirmações.
 *
 * Dia novo = PRÉVIA (nada gravado até Salvar rascunho). O modelo vem
 * sugerido pela paridade do mês (RN01 — fim de semana segue o mês do
 * DOMINGO) e só pode ser trocado antes de salvar. Datas com dataLocalISO(),
 * 🔴 nunca toISOString().
 *
 * FECHAR FIM DE SEMANA (29/10/2026): GET /dias/{data}/fechamento devolve o
 * dia já preenchido (mesmo formato de GET /dias/{data}) + o bloco
 * `fechamento`. A grade recebe a sugestão como MUDANÇA NÃO SALVA, as células
 * que mudaram ficam marcadas, e o painel mostra o que mudou, o que ficou em
 * branco e a conta do quadro. Salvar segue o fluxo de sempre.
 */
import { apiGet, apiPut, apiPost, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { dataLocalISO } from './data.util.js';

const API = '/escala-fiscais';
const ROTULO_AUSENCIA = { ferias: 'férias', atestado: 'atestado', afastado: 'afastamento' };
const RE_MARCADOR = /^(\*+|x+|-|G\d+|DIRETO)$/i;

const estado = {
    data: null,          // 'AAAA-MM-DD'
    resp: null,          // resposta de GET /dias/{data}
    sujo: false,         // mudou algo desde o último carregar/salvar
    confirmacoes: new Set(),
    plantao: [],         // cópia editável do plantão do dia
    escreve: false,
    fechamento: null,    // bloco `fechamento` da última sugestão (até sair do dia)
};

let aviso = () => {};    // mostrarMensagem da página
let tratarErro = () => {};

export function iniciarMontagem({ escreve, mostrarMensagem, erro }) {
    estado.escreve = escreve;
    aviso = mostrarMensagem;
    tratarErro = erro;
    document.getElementById('ef-mt-ant').addEventListener('click', () => pularFimDeSemana(-7));
    document.getElementById('ef-mt-prox').addEventListener('click', () => pularFimDeSemana(7));
    document.getElementById('ef-mt-sab').addEventListener('click', () => irPara(diaDoFimDeSemana(6)));
    document.getElementById('ef-mt-dom').addEventListener('click', () => irPara(diaDoFimDeSemana(0)));
    document.getElementById('ef-mt-data').addEventListener('change', (e) => { if (e.target.value) irPara(e.target.value); });
    document.getElementById('ef-mt-modelo').addEventListener('change', (e) => trocarModelo(e.target.value));
    document.getElementById('ef-mt-salvar').addEventListener('click', () => salvar());
    document.getElementById('ef-mt-publicar').addEventListener('click', () => publicar());
    document.getElementById('ef-mt-imprimir').addEventListener('click', () => imprimir());
    document.getElementById('ef-mt-fechar').addEventListener('click', () => fecharFimDeSemana());
    document.getElementById('ef-mt-fechamento').addEventListener('click', aoClicarFechamento);
    document.getElementById('ef-mt-resumo').addEventListener('click', abrirDetalheResumo);
    const grade = document.getElementById('ef-mt-grade');
    grade.addEventListener('change', aoMudarCelula);
    grade.addEventListener('input', aoDigitarRe);
    grade.addEventListener('focusout', (e) => {
        if (e.target.matches('.ef-mt-cel')) setTimeout(() => fecharSugestoes(e.target), 150);
    });
    document.getElementById('ef-mt-plantao').addEventListener('change', aoMudarPlantao);
    document.getElementById('ef-mt-plantao').addEventListener('click', aoClicarPlantao);
    // A página esconde .ef-rodape de quem só lê; Imprimir vale para todos.
    document.querySelector('.ef-mt-rodape').hidden = false;
    window.addEventListener('beforeunload', (e) => {
        if (estado.sujo) { e.preventDefault(); e.returnValue = ''; }
    });
}

export function carregarMontagem() {
    if (estado.data) return carregar(estado.data);
    // Abre no próximo sábado (ou hoje, se hoje é fim de semana).
    const hoje = new Date();
    const d = new Date(hoje);
    const dow = hoje.getDay();
    if (dow !== 6 && dow !== 0) d.setDate(d.getDate() + (6 - dow));
    return carregar(dataLocalISO(d));
}

// ─── Datas (sempre locais) ───────────────────────────────────────────────

function dataDe(iso) {
    const [a, m, d] = iso.split('-').map(Number);
    return new Date(a, m - 1, d);
}

function ddmm(iso) {
    const [, m, d] = iso.split('-');
    return `${d}/${m}`;
}

function sabadoDe(iso) {
    const d = dataDe(iso);
    const dow = d.getDay();
    if (dow === 0) d.setDate(d.getDate() - 1);
    else if (dow !== 6) d.setDate(d.getDate() + (6 - dow));
    return d;
}

function diaDoFimDeSemana(dow) {
    const sab = sabadoDe(estado.data);
    if (dow === 0) sab.setDate(sab.getDate() + 1);
    return dataLocalISO(sab);
}

function pularFimDeSemana(dias) {
    const d = sabadoDe(estado.data);
    d.setDate(d.getDate() + dias);
    if (dataDe(estado.data).getDay() === 0) d.setDate(d.getDate() + 1);
    irPara(dataLocalISO(d));
}

async function irPara(iso) {
    if (iso === estado.data) return;
    if (estado.sujo && !(await perguntar('Sair sem salvar?', '<p>As mudanças deste dia ainda não foram salvas.</p>', 'Sair sem salvar', 'Ficar'))) {
        document.getElementById('ef-mt-data').value = estado.data;
        return;
    }
    carregar(iso);
}

async function trocarModelo(modeloId) {
    if (estado.resp?.status !== 'novo') return;
    if (estado.sujo && !(await perguntar('Trocar o modelo?', '<p>A prévia é montada de novo com o outro modelo e o que você mudou nela se perde.</p>', 'Trocar', 'Cancelar'))) {
        document.getElementById('ef-mt-modelo').value = estado.resp.modelo?.id || '';
        return;
    }
    carregar(estado.data, modeloId);
}

// ─── Carregar e desenhar ─────────────────────────────────────────────────

async function carregar(iso, modeloId = null) {
    estado.data = iso;
    const grade = document.getElementById('ef-mt-grade');
    grade.innerHTML = '<div class="patio-loading">Carregando…</div>';
    try {
        const q = modeloId ? `?modelo_id=${encodeURIComponent(modeloId)}` : '';
        aplicar(await apiGet(`${API}/dias/${iso}${q}`));
    } catch (err) {
        grade.innerHTML = '';
        tratarErro(err);
    }
}

function aplicar(resp, { fechamento = null } = {}) {
    estado.resp = resp;
    estado.fechamento = fechamento;
    estado.sujo = !!fechamento;
    estado.confirmacoes = new Set();
    estado.plantao = resp.plantao.map(p => ({ turno: p.turno, re: p.re, nome: p.nome, hora_inicio: p.hora_inicio, hora_fim: p.hora_fim }));
    desenhar();
}

function podeEditarPeriodo(periodo) {
    const r = estado.resp;
    return estado.escreve && r.modelo && r.periodos_editaveis.includes(periodo);
}

function desenhar() {
    const r = estado.resp;
    document.getElementById('ef-mt-data').value = r.data;
    const sab = r.fim_de_semana ? r.fim_de_semana.sabado : dataLocalISO(sabadoDe(r.data));
    const dom = r.fim_de_semana ? r.fim_de_semana.domingo : dataLocalISO((() => { const d = sabadoDe(r.data); d.setDate(d.getDate() + 1); return d; })());
    const bSab = document.getElementById('ef-mt-sab');
    const bDom = document.getElementById('ef-mt-dom');
    bSab.textContent = `Sábado ${ddmm(sab)}`;
    bDom.textContent = `Domingo ${ddmm(dom)}`;
    bSab.classList.toggle('active', r.data === sab);
    bDom.classList.toggle('active', r.data === dom);

    const sel = document.getElementById('ef-mt-modelo');
    sel.innerHTML = r.modelos.map(m => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.nome)}${r.modelo_sugerido?.id === m.id ? ' (sugerido)' : ''}</option>`).join('')
        || '<option value="">— nenhum modelo importado —</option>';
    sel.value = r.modelo?.id || '';
    sel.disabled = r.status !== 'novo' || !estado.escreve;
    sel.title = r.status === 'novo' ? 'RN01: sugerido pela paridade do mês (fim de semana = mês do domingo); troque antes de salvar' : 'O modelo não muda depois de salvo';

    desenharEstado();
    desenharAvisos();
    desenharResumo();
    desenharGrade();
    desenharPlantao();
    desenharBotoes();
    desenharFechamento();
}

function desenharEstado() {
    const r = estado.resp;
    const el = document.getElementById('ef-mt-estado');
    const rotulo = {
        novo: '<span class="ef-tag ef-mt-tag-novo">Prévia — nada salvo ainda</span>',
        rascunho: '<span class="ef-tag ef-mt-tag-rascunho">Rascunho — o fiscal não vê</span>',
        publicada: `<span class="ef-tag ef-mt-tag-publicada">Publicada · versão ${r.versao}</span>`,
    }[r.status] || '';
    const sujo = estado.sujo ? ' <span class="ef-tag ef-tag-alerta">mudanças não salvas</span>' : '';
    const quem = r.status === 'publicada' && r.publicada_por ? ` <span class="ef-meta">por ${escapeHtml(r.publicada_por)}</span>` : '';
    const periodos = r.periodos_editaveis.length === 2 ? 'Você edita os dois períodos'
        : r.periodos_editaveis.length ? `Você edita só o ${r.periodos_editaveis[0]}º período`
            : 'Você não edita nenhum período (aba Coordenadores)';
    el.innerHTML = `${rotulo}${sujo}${quem}<div class="ef-meta">${escapeHtml(periodos)}</div>`;
}

function desenharAvisos() {
    const r = estado.resp;
    const blocos = [];
    if (r.virada_mes) blocos.push(`<div class="ef-mensagem ef-mensagem-alerta ef-mt-virada">⚠️ ${escapeHtml(r.virada_mes.mensagem)}</div>`);
    if (!r.modelo) blocos.push('<div class="ef-mensagem ef-mensagem-alerta">Nenhum modelo para este tipo de dia. Importe a planilha ou crie o modelo na aba Modelos.</div>');
    for (const b of r.validacao.bloqueios) {
        blocos.push(`<div class="ef-mensagem ef-mensagem-erro">⛔ ${escapeHtml(b.mensagem)}</div>`);
    }
    document.getElementById('ef-mt-avisos').innerHTML = blocos.join('');
}

// ─── Painel de resumo ────────────────────────────────────────────────────

function listaAusentes() {
    const saida = [];
    for (const [tipo, porPeriodo] of Object.entries(estado.resp.resumo.ausentes)) {
        for (const [periodo, lista] of Object.entries(porPeriodo)) {
            for (const a of lista) saida.push({ ...a, tipo, periodo });
        }
    }
    return saida;
}

function desenharResumo() {
    const res = estado.resp.resumo;
    const perguntas = estado.resp.validacao.perguntas;
    const itens = [
        ['descobertos', 'Descobertos', res.descobertos.length, ''],
        ['dobras', 'Dobras', res.dobras.length, 'ef-mt-chip-dobra'],
        ['acumulos', 'Acúmulos', res.acumulos.length, ''],
        ['sumidos', 'Sumidos', res.sumidos.length, res.sumidos.length ? 'ef-mt-chip-alerta' : ''],
        ['folga1', 'Folga 1º', res.folgas['1'].length, ''],
        ['folga2', 'Folga 2º', res.folgas['2'].length, ''],
        ['ausentes', 'Ausentes', listaAusentes().length, ''],
        ['perguntas', 'A confirmar', perguntas.filter(p => p.pendente).length, perguntas.some(p => p.pendente) ? 'ef-mt-chip-alerta' : ''],
        ['sembase', 'Folga não confirmada', res.folga_nao_confirmada.length, ''],
    ];
    document.getElementById('ef-mt-resumo').innerHTML = itens.map(([chave, rotulo, n, cls]) =>
        `<button type="button" class="ef-mt-chip ${cls}" data-resumo="${chave}"><span class="ef-mt-chip-n">${n}</span>${escapeHtml(rotulo)}</button>`,
    ).join('');
    const det = document.getElementById('ef-mt-detalhe');
    if (!det.hidden && det.dataset.aberto) mostrarDetalhe(det.dataset.aberto);
}

function abrirDetalheResumo(e) {
    const btn = e.target.closest('[data-resumo]');
    if (!btn) return;
    const det = document.getElementById('ef-mt-detalhe');
    if (!det.hidden && det.dataset.aberto === btn.dataset.resumo) { det.hidden = true; det.dataset.aberto = ''; return; }
    mostrarDetalhe(btn.dataset.resumo);
}

function fiscal(re, nome) {
    return nome ? `<strong>${escapeHtml(re)}</strong> ${escapeHtml(nome)}` : `<strong>${escapeHtml(re)}</strong>`;
}

function mostrarDetalhe(chave) {
    const res = estado.resp.resumo;
    const lista = (itens) => itens.length ? `<ul>${itens.map(i => `<li>${i}</li>`).join('')}</ul>` : '<p class="ef-meta">Nenhum.</p>';
    const periodoTxt = (p) => (p === '0' ? 'sem período no quadro' : `${p}º período`);
    const conteudo = {
        descobertos: () => lista(res.descobertos.map(d => `${escapeHtml(d.posto)} — <code>${escapeHtml(d.marcador === '' ? 'em branco' : d.marcador)}</code>`)),
        dobras: () => lista(res.dobras.map(d => `${fiscal(d.re, d.nome)} — ${escapeHtml(d.posto)}`)),
        acumulos: () => lista(res.acumulos.map(a => `${fiscal(a.re)} — ${a.postos.map(escapeHtml).join(' + ')} · ${a.ponto_final ? escapeHtml(a.ponto_final) : '<em>sem ponto final</em>'}`)),
        sumidos: () => `<p class="ef-meta">Do quadro, sem posto, sem folga e sem ausência neste dia.</p>${lista(res.sumidos.map(s => `${fiscal(s.re, s.nome)} · ${s.periodo}º período${s.folga_desconhecida ? ' · folga base não confirmada' : ''}`))}`,
        folga1: () => lista(res.folgas['1'].map(f => fiscal(f.re, f.nome))),
        folga2: () => lista(res.folgas['2'].map(f => fiscal(f.re, f.nome))),
        ausentes: () => lista(listaAusentes().map(a => `${fiscal(a.re, a.nome)} — ${ROTULO_AUSENCIA[a.tipo] || a.tipo} (${periodoTxt(a.periodo)})${a.data_fim ? ` até ${ddmm(a.data_fim)}` : ' sem data de volta'}`)),
        perguntas: () => lista(estado.resp.validacao.perguntas.map(p => `${escapeHtml(p.mensagem)}${p.pendente ? '' : ' <span class="ef-tag">confirmado</span>'}`)),
        sembase: () => `<p class="ef-meta">Escalados sem folga base no quadro: o sistema não calcula dobra para eles.</p>${lista(res.folga_nao_confirmada.map(r => fiscal(r)))}`,
    }[chave]?.() ?? '';
    const det = document.getElementById('ef-mt-detalhe');
    det.innerHTML = conteudo;
    det.dataset.aberto = chave;
    det.hidden = false;
}

// ─── Grade (espelho da planilha) ─────────────────────────────────────────

function celulaTexto(p) {
    if (!p) return '';
    return p.re || p.marcador || '';
}

function desenharGrade() {
    const r = estado.resp;
    const grade = document.getElementById('ef-mt-grade');
    if (!r.linhas.length) {
        grade.innerHTML = r.modelo ? '<div class="patio-loading">O modelo não tem postos.</div>' : '';
        return;
    }
    const blocos = ['TP', 'TS'].map(lado => {
        const linhas = r.linhas.filter(l => l.lado === lado);
        if (!linhas.length) return '';
        const titulo = lado === 'TP' ? r.titulo_tp : r.titulo_ts;
        return `
        <div class="ef-mt-bloco">
            <div class="ef-mt-titulo">${escapeHtml(titulo)}</div>
            <div class="ef-mt-rolagem">
            <table class="ef-mt-tabela">
                <thead><tr>
                    <th>L</th><th>Cód. JB</th><th>Lote</th>
                    <th>Linhas</th><th>1º Período</th><th colspan="2">${lado}</th>
                    <th>Linhas</th><th>2º Período</th><th colspan="2">${lado}</th>
                </tr></thead>
                <tbody>${linhas.map(l => linhaHtml(l, lado)).join('')}</tbody>
            </table>
            </div>
        </div>`;
    });
    grade.innerHTML = blocos.join('');
    marcarSugeridas();
}

function linhaHtml(l, lado) {
    const cab = `<td rowspan="2" class="ef-mt-l">${lado}</td>
        <td rowspan="2">${escapeHtml(l.cod_jb || '')}</td>
        <td rowspan="2">${escapeHtml(l.lote || '')}</td>`;
    const [a1, b1] = periodoHtml(l.p1, 1);
    const [a2, b2] = periodoHtml(l.p2, 2);
    return `<tr class="ef-mt-ini">${cab}${a1}${a2}</tr><tr class="ef-mt-fim">${b1}${b2}</tr>`;
}

function periodoHtml(p, periodo) {
    if (!p) {
        return [
            '<td rowspan="2" class="ef-mt-vazio"></td><td rowspan="2" class="ef-mt-vazio"></td><td class="ef-mt-rot">Início</td><td></td>',
            '<td class="ef-mt-rot">Término</td><td></td>',
        ];
    }
    const pode = podeEditarPeriodo(periodo);
    const dis = pode ? '' : ' disabled';
    const chave = `${p.posto_id}|${periodo}`;
    const classes = ['ef-mt-re'];
    if (p.dobra) classes.push('ef-mt-dobra');
    if (p.ausencia) classes.push('ef-mt-ausente');
    const tags = [];
    if (p.acumulo) {
        // "acúmulo c/ <linhas do outro posto>": até 2 linhas na etiqueta, a lista
        // completa no title. Sem acumulo_com (estado antigo) fica só "acúmulo".
        const com = p.acumulo_com || [];
        const pf = p.ponto_final || 'sem ponto final';
        const texto = com.length ? `acúmulo c/ ${com.slice(0, 2).join(' ')}${com.length > 2 ? ' …' : ''}` : 'acúmulo';
        const title = com.length ? `${com.join(' ')} · ${pf}` : pf;
        tags.push(`<span class="ef-tag" title="${escapeHtml(title)}">${escapeHtml(texto)}</span>`);
    }
    if (p.dobra) tags.push('<span class="ef-tag ef-mt-tag-dobra">dobra</span>');
    if (p.dobra_seguida_confirmada_por) tags.push(`<span class="ef-tag" title="Confirmado por ${escapeHtml(p.dobra_seguida_confirmada_por)}">2º fds confirmado</span>`);
    if (p.situacao === 'escalado' && !p.no_quadro) tags.push('<span class="ef-tag ef-tag-alerta">fora do quadro</span>');
    const nota = p.nota ? `<div class="ef-mt-nota">${escapeHtml(p.nota)}</div>` : '';
    const nome = p.nome ? escapeHtml(p.nome) : (p.re ? '<span class="ef-meta">sem cadastro</span>' : '');
    return [
        `<td rowspan="2" class="ef-mt-linhas">${p.linhas.map(escapeHtml).join('<br>')}</td>
         <td rowspan="2" class="${classes.join(' ')}" data-chave="${chave}">
            <input type="text" class="ef-mt-cel" value="${escapeHtml(celulaTexto(p))}" maxlength="20" autocomplete="off"
                   aria-label="RE ou marcador do ${periodo}º período" data-chave="${chave}"${dis}>
            <div class="ef-mt-nome">${nome}</div>${tags.length ? `<div class="ef-mt-tags">${tags.join(' ')}</div>` : ''}${nota}
         </td>
         <td class="ef-mt-rot">Início</td>
         <td><input type="time" class="ef-mt-hora" data-chave="${chave}" data-campo="hora_inicio" value="${p.hora_inicio || ''}" aria-label="Início ${periodo}º período"${dis}></td>`,
        `<td class="ef-mt-rot">Término</td>
         <td><input type="time" class="ef-mt-hora" data-chave="${chave}" data-campo="hora_termino" value="${p.hora_termino || ''}" aria-label="Término ${periodo}º período"${dis}></td>`,
    ];
}

function itemPorChave(chave) {
    const [posto, periodo] = chave.split('|');
    for (const l of estado.resp.linhas) {
        const p = l[`p${periodo}`];
        if (p && p.posto_id === posto) return p;
    }
    return null;
}

function marcarSujo() {
    if (!estado.sujo) {
        estado.sujo = true;
        desenharEstado();
    }
}

// Mapa RE → ausência do dia (RN05 na tela).
function ausenciaDe(re) {
    return listaAusentes().find(a => a.re === re) || null;
}

function deFolgaHoje(re) {
    const f = estado.resp.resumo.folgas;
    return [...f['1'], ...f['2']].some(x => x.re === re);
}

async function aoMudarCelula(e) {
    const alvo = e.target;
    if (alvo.matches('.ef-mt-hora')) {
        const p = itemPorChave(alvo.dataset.chave);
        p[alvo.dataset.campo] = alvo.value || null;
        if (p.hora_inicio && p.hora_termino && p.hora_termino <= p.hora_inicio) {
            aviso('O término tem que ser depois do início: posto de fiscal não passa da meia-noite.', 'erro');
        }
        marcarSujo();
        return;
    }
    if (!alvo.matches('.ef-mt-cel')) return;
    const p = itemPorChave(alvo.dataset.chave);
    const anterior = celulaTexto(p);
    const texto = alvo.value.trim();
    const [, periodo] = alvo.dataset.chave.split('|');
    if (texto.toUpperCase() === 'G3') {
        aviso('G3 é a própria garagem: não pode ser "outra garagem".', 'erro');
        alvo.value = anterior;
        return;
    }
    if (!texto || RE_MARCADOR.test(texto)) {
        Object.assign(p, { re: null, nome: null, marcador: texto, dobra: false, ausencia: null, acumulo: false, acumulo_com: [] });
        p.situacao = /^G\d+$/i.test(texto) ? 'outra_garagem' : /^DIRETO$/i.test(texto) ? 'direto' : 'descoberto';
    } else {
        const re = texto.toUpperCase();
        const aus = ausenciaDe(re);
        if (aus) {
            await perguntar('Não dá para escalar', `<p>${escapeHtml(mensagemAusencia(aus))}.</p>`, null, 'Entendi');
            alvo.value = anterior;
            return;
        }
        const dobra = deFolgaHoje(re);
        const dobrouAntes = estado.resp.dobras_fim_de_semana_anterior?.[re];
        if (dobra && dobrouAntes) {
            const dia = dataDe(dobrouAntes).getDay() === 6 ? 'sábado' : 'domingo';
            const ok = await perguntar('Dobra em dois fins de semana seguidos',
                `<p>RE ${escapeHtml(re)} dobrou no ${dia} ${ddmm(dobrouAntes)}. Deseja mesmo escalar de novo neste fim de semana?</p>`,
                'Sim, escalar', 'Cancelar');
            if (!ok) { alvo.value = anterior; return; }
            estado.confirmacoes.add(`RN04|${p.posto_id}|${periodo}|${re}`);
        }
        Object.assign(p, { re, marcador: null, situacao: 'escalado', dobra, ausencia: null, nome: p.re === re ? p.nome : null });
        alvo.value = re;
    }
    const td = alvo.closest('td');
    td.classList.remove('ef-mt-sugerido');
    td.classList.toggle('ef-mt-dobra', !!p.dobra);
    td.querySelector('.ef-mt-nome').innerHTML = p.nome ? escapeHtml(p.nome) : '';
    marcarSujo();
}

function mensagemAusencia(a) {
    const tipo = ROTULO_AUSENCIA[a.tipo] || a.tipo;
    if (!a.data_fim) return `RE ${a.re} está de ${tipo} desde ${ddmm(a.data_inicio)}, sem data de volta`;
    if (a.data_fim === a.data_inicio) return `RE ${a.re} está de ${tipo} em ${ddmm(a.data_inicio)}`;
    return `RE ${a.re} está de ${tipo} de ${ddmm(a.data_inicio)} a ${ddmm(a.data_fim)}`;
}

// ─── Autocomplete do RE na célula ────────────────────────────────────────

let timerBusca = null;
let ultimaBusca = 0;

function aoDigitarRe(e) {
    const input = e.target;
    if (!input.matches('.ef-mt-cel')) return;
    const q = input.value.trim();
    clearTimeout(timerBusca);
    if (q.length < 2 || RE_MARCADOR.test(q) || q.startsWith('*')) { fecharSugestoes(input); return; }
    timerBusca = setTimeout(async () => {
        const minha = ++ultimaBusca;
        try {
            const itens = await apiGet(`${API}/pessoas?q=${encodeURIComponent(q)}`);
            if (minha !== ultimaBusca) return;
            let lista = input.parentElement.querySelector('.ef-sugestoes');
            if (!lista) {
                lista = document.createElement('div');
                lista.className = 'ef-sugestoes';
                lista.addEventListener('mousedown', ev => ev.preventDefault());
                lista.addEventListener('click', ev => {
                    const b = ev.target.closest('button[data-re]');
                    if (!b) return;
                    input.value = b.dataset.re;
                    const p = itemPorChave(input.dataset.chave);
                    fecharSugestoes(input);
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    p.nome = b.dataset.nome;
                    input.closest('td').querySelector('.ef-mt-nome').textContent = b.dataset.nome;
                });
                input.parentElement.appendChild(lista);
            }
            lista.innerHTML = itens.length
                ? itens.map(i => `<button type="button" data-re="${escapeHtml(i.re)}" data-nome="${escapeHtml(i.nome)}"><strong>${escapeHtml(i.re)}</strong> — ${escapeHtml(i.nome)}</button>`).join('')
                : '<div class="ef-sugestao-vazia">RE sem cadastro: entra só com o RE.</div>';
            lista.hidden = false;
        } catch (err) { tratarErro(err); }
    }, 250);
}

function fecharSugestoes(input) {
    const lista = input.parentElement?.querySelector('.ef-sugestoes');
    if (lista) lista.hidden = true;
}

// ─── Plantão do dia ──────────────────────────────────────────────────────

function desenharPlantao() {
    const pode = estado.escreve && estado.resp.periodos_editaveis.length > 0 && estado.resp.modelo;
    const dis = pode ? '' : ' disabled';
    const linhas = estado.plantao.map((p, i) => `
        <div class="ef-mt-plantao-linha" data-i="${i}">
            <select class="form-select ef-campo-mini" data-campo="turno"${dis}>
                <option value="manha"${p.turno === 'manha' ? ' selected' : ''}>Manhã</option>
                <option value="tarde"${p.turno === 'tarde' ? ' selected' : ''}>Tarde</option>
            </select>
            <input type="text" class="form-input ef-campo-mini" data-campo="re" value="${escapeHtml(p.re || '')}" placeholder="RE do coordenador" aria-label="RE do coordenador"${dis}>
            <input type="time" class="form-input ef-campo-mini" data-campo="hora_inicio" value="${p.hora_inicio || ''}" aria-label="Início"${dis}>
            <input type="time" class="form-input ef-campo-mini" data-campo="hora_fim" value="${p.hora_fim || ''}" aria-label="Fim"${dis}>
            <span class="ef-meta">${escapeHtml(p.nome || '')}${p.hora_fim && p.hora_inicio && p.hora_fim < p.hora_inicio ? ' · termina no dia seguinte' : ''}</span>
            ${pode ? '<button type="button" class="btn btn-ghost ef-btn-mini" data-acao="remover">Tirar</button>' : ''}
        </div>`).join('');
    document.getElementById('ef-mt-plantao').innerHTML = (linhas || '<p class="ef-meta">Nenhum coordenador de plantão. Cadastre o horário padrão na aba Coordenadores.</p>')
        + (pode ? '<button type="button" class="btn btn-ghost ef-btn-mini" data-acao="adicionar">+ Coordenador</button>' : '');
}

function aoMudarPlantao(e) {
    const linha = e.target.closest('[data-i]');
    if (!linha) return;
    const p = estado.plantao[Number(linha.dataset.i)];
    const campo = e.target.dataset.campo;
    p[campo] = campo === 're' ? e.target.value.trim().toUpperCase() : e.target.value;
    if (campo === 're') p.nome = '';
    marcarSujo();
}

function aoClicarPlantao(e) {
    const btn = e.target.closest('[data-acao]');
    if (!btn) return;
    if (btn.dataset.acao === 'adicionar') estado.plantao.push({ turno: 'manha', re: '', hora_inicio: '', hora_fim: '' });
    else estado.plantao.splice(Number(btn.closest('[data-i]').dataset.i), 1);
    marcarSujo();
    desenharPlantao();
}

// ─── Salvar, publicar, imprimir ──────────────────────────────────────────

function desenharBotoes() {
    const r = estado.resp;
    const pode = estado.escreve && r.modelo && r.periodos_editaveis.length > 0;
    const bFechar = document.getElementById('ef-mt-fechar');
    document.getElementById('ef-mt-fechar-box').hidden = !estado.escreve || r.tipo_dia === 'util';
    bFechar.disabled = !pode || r.status === 'publicada';
    bFechar.title = r.status === 'publicada' ? 'Escala publicada: o fechamento automático só mexe em prévia e rascunho' : '';
    document.getElementById('ef-mt-salvar').disabled = !pode;
    document.getElementById('ef-mt-salvar').textContent = r.status === 'publicada' ? 'Salvar (nova versão)' : 'Salvar rascunho';
    document.getElementById('ef-mt-publicar').disabled = !pode || r.status === 'publicada';
    document.getElementById('ef-mt-imprimir').disabled = !r.modelo;
}

function corpoSalvar() {
    const alocacoes = [];
    for (const l of estado.resp.linhas) {
        for (const p of [l.p1, l.p2]) {
            if (!p) continue;
            alocacoes.push({
                posto_id: p.posto_id, periodo: p.periodo, re: p.re || null,
                marcador: p.re ? null : (p.marcador ?? ''),
                hora_inicio: p.hora_inicio || null, hora_termino: p.hora_termino || null,
            });
        }
    }
    const plantao = estado.plantao.filter(p => p.re).map(p => ({ turno: p.turno, re: p.re, hora_inicio: p.hora_inicio, hora_fim: p.hora_fim }));
    return { modelo_id: estado.resp.modelo.id, alocacoes, plantao, confirmacoes: [...estado.confirmacoes] };
}

// Envia, e a cada 409 pergunta uma a uma; "Cancelar" em qualquer uma para tudo.
async function enviarComPerguntas(enviar) {
    for (let tentativa = 0; tentativa < 6; tentativa++) {
        try {
            return await enviar();
        } catch (err) {
            const corpo = err instanceof ApiError ? err.body?.erro : null;
            if (err instanceof ApiError && err.status === 409 && corpo?.perguntas) {
                for (const p of corpo.perguntas) {
                    const titulo = { RN04: 'Dobra em dois fins de semana seguidos', RN08: 'Acúmulo sem ponto final', RN11: 'Mesmo fiscal nos dois períodos' }[p.codigo] || 'Confirmar';
                    const sim = p.codigo === 'RN04' ? 'Sim, escalar' : 'Sim, confirmar';
                    if (!(await perguntar(titulo, `<p>${escapeHtml(p.mensagem)}</p>`, sim, 'Cancelar'))) {
                        aviso('Nada foi salvo.', 'alerta');
                        return null;
                    }
                    estado.confirmacoes.add(p.chave);
                }
                continue;
            }
            if (err instanceof ApiError && err.status === 422 && corpo?.bloqueios) {
                await perguntar('Não dá para salvar', `<ul>${corpo.bloqueios.map(b => `<li>⛔ ${escapeHtml(b.mensagem)}</li>`).join('')}</ul><p class="ef-meta">Nada foi salvo.</p>`, null, 'Entendi');
                return null;
            }
            tratarErro(err);
            return null;
        }
    }
    return null;
}

async function salvar({ silencioso = false } = {}) {
    if (!estado.resp?.modelo) return null;
    const btn = document.getElementById('ef-mt-salvar');
    btn.disabled = true;
    try {
        const resp = await enviarComPerguntas(() => apiPut(`${API}/dias/${estado.data}`, corpoSalvar()));
        if (resp) {
            aplicar(resp);
            if (!silencioso) aviso(resp.status === 'publicada' ? `Salvo: escala publicada agora na versão ${resp.versao}.` : 'Rascunho salvo. O fiscal ainda não vê.');
        }
        return resp;
    } finally {
        desenharBotoes();
    }
}

async function publicar() {
    if (estado.sujo || estado.resp.status === 'novo') {
        const salvo = await salvar({ silencioso: true });
        if (!salvo) return;
    }
    const r = estado.resp;
    // RN04 pendentes: uma pergunta por fiscal, [Cancelar] [Sim, escalar].
    for (const p of r.validacao.perguntas.filter(x => x.pendente && x.codigo === 'RN04')) {
        if (!(await perguntar('Dobra em dois fins de semana seguidos', `<p>${escapeHtml(p.mensagem)}</p>`, 'Sim, escalar', 'Cancelar'))) {
            aviso('Não publicado.', 'alerta');
            return;
        }
        estado.confirmacoes.add(p.chave);
    }
    const res = r.resumo;
    const linhas = [
        ...r.validacao.avisos.map(a => `⚠️ ${escapeHtml(a.mensagem)}`),
        `Descobertos: ${res.descobertos.length} · Dobras: ${res.dobras.length} · Acúmulos: ${res.acumulos.length}`,
    ];
    const ok = await perguntar(`Publicar ${ddmm(r.data)}?`,
        `<p>Depois de publicada o fiscal passa a ver a escala, e cada alteração vira uma nova versão com registro.</p><ul>${linhas.map(l => `<li>${l}</li>`).join('')}</ul>`,
        'Publicar', 'Cancelar');
    if (!ok) return;
    const resp = await enviarComPerguntas(() => apiPost(`${API}/dias/${estado.data}/publicar`, { confirmacoes: [...estado.confirmacoes] }));
    if (resp) {
        aplicar(resp);
        aviso(`Escala de ${ddmm(resp.data)} publicada.`);
    }
}

async function imprimir() {
    if (estado.sujo) {
        const salvarAntes = await perguntar('Salvar antes de imprimir?', '<p>A impressão mostra o que está gravado. As mudanças desta tela ainda não foram salvas.</p>', 'Salvar e imprimir', 'Imprimir sem salvar');
        if (salvarAntes && !(await salvar({ silencioso: true }))) return;
    }
    window.open(`escala-fiscais-impressao.html?data=${encodeURIComponent(estado.data)}`, '_blank', 'noopener');
}

// ─── Fechar fim de semana (sugestão automática) ──────────────────────────

const TIPO_MUDANCA = {
    livre: 'estava livre',
    remanejado: 'veio do TS',
    acumulo: 'acúmulo',
    dobra: 'dobra sugerida',
    branco: 'em branco',
};

async function fecharFimDeSemana() {
    const r = estado.resp;
    if (!r || r.tipo_dia === 'util') return;
    if (estado.sujo && !(await perguntar('Substituir o que você mudou?',
        '<p>A sugestão é montada a partir do que está salvo (ou do modelo). As mudanças desta tela que ainda não foram salvas se perdem.</p>',
        'Montar a sugestão', 'Cancelar'))) return;
    const modo = document.getElementById('ef-mt-modo').value;
    const btn = document.getElementById('ef-mt-fechar');
    btn.disabled = true;
    btn.textContent = 'Montando…';
    try {
        const resp = await apiGet(`${API}/dias/${estado.data}/fechamento?modo=${encodeURIComponent(modo)}`);
        aplicar(resp, { fechamento: resp.fechamento });
        const f = resp.fechamento;
        const dia = f[r.tipo_dia];
        const vagas = dia?.vagas?.length || 0;
        aviso(`Sugestão pronta: ${f.mudancas.length} mudança(s)${vagas ? `, ${vagas} posto(s) obrigatório(s) em branco` : ''}. Confira e clique em Salvar rascunho.`, vagas ? 'alerta' : 'ok');
        document.getElementById('ef-mt-fechamento').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
        tratarErro(err);
    } finally {
        btn.textContent = 'Fechar fim de semana';
        desenharBotoes();
    }
}

function marcarSugeridas() {
    const f = estado.fechamento;
    if (!f) return;
    for (const m of f.mudancas) {
        const td = document.querySelector(`#ef-mt-grade td[data-chave="${m.posto_id}|${m.periodo}"]`);
        if (td) {
            td.classList.add('ef-mt-sugerido');
            td.title = m.nota || '';
        }
    }
}

function itemFiscal(x) {
    return fiscal(x.re, x.nome);
}

function desenharFechamento() {
    const painel = document.getElementById('ef-mt-fechamento');
    const f = estado.fechamento;
    if (!f) { painel.hidden = true; painel.innerHTML = ''; return; }
    const r = estado.resp;
    const lista = (itens) => itens.length ? `<ul>${itens.map(i => `<li>${i}</li>`).join('')}</ul>` : '<p class="ef-meta">Nenhum.</p>';
    const resumoDia = (nome, rotulo) => {
        const d = f[nome];
        if (!d) return '';
        const atual = d.data === r.data ? ' ef-mt-fc-dia-atual' : '';
        if (!d.incluido) {
            const porque = d.motivo === 'publicada' ? 'já publicada — não foi mexida' : 'sem modelo';
            return `<div class="ef-mt-fc-dia${atual}"><strong>${rotulo} ${ddmm(d.data)}</strong><span class="ef-meta">${porque}</span></div>`;
        }
        const chip = (n, txt, cls = '') => `<span class="ef-mt-fc-num${cls}">${n}</span> ${txt}`;
        return `<div class="ef-mt-fc-dia${atual}">
            <strong>${rotulo} ${ddmm(d.data)}</strong>
            <span>${chip(d.mudancas, 'mudança(s)')}</span>
            <span>${chip(d.vagas.length, 'obrigatório(s) em branco', d.vagas.length ? ' ef-mt-fc-ruim' : ' ef-mt-fc-bom')}</span>
            <span>${chip(d.dobras.length, 'dobra(s)', d.dobras.length ? ' ef-mt-fc-dobra' : '')}</span>
            <span>${chip(d.acumulos.length, 'acúmulo(s)')}</span>
            <span>${chip(d.sobrando.length, 'sobrando')}</span>
        </div>`;
    };
    const dia = f[r.tipo_dia] || {};
    const outro = r.tipo_dia === 'sabado' ? 'domingo' : 'sabado';
    const mudancas = f.mudancas.map(m => `<strong>${escapeHtml(m.posto)}</strong>: ${escapeHtml(m.antes)} → <strong>${escapeHtml(m.depois)}</strong> <span class="ef-tag${m.tipo === 'dobra' ? ' ef-mt-tag-dobra' : m.tipo === 'branco' ? ' ef-tag-alerta' : ''}">${escapeHtml(TIPO_MUDANCA[m.tipo] || m.tipo)}</span>${m.nota ? `<div class="ef-meta">${escapeHtml(m.nota)}</div>` : ''}`);
    const conta = (f.conta || []).map(c => `
        <tr><td>${c.periodo}º</td><td>${c.postos_obrigatorios}</td><td>${c.fiscais_no_quadro}</td>
            <td>${c.falta_sem_dobra}</td><td>${c.quadro_ideal}</td><td><strong>${c.contratar}</strong></td></tr>`).join('');
    painel.innerHTML = `
        <div class="ef-mt-fc-topo">
            <div>
                <div class="ef-mt-fc-titulo">Sugestão do fechamento · ${f.modo === 'com_dobra' ? 'com dobra' : 'sem dobra'}</div>
                <div class="ef-meta">Células com borda verde foram mudadas pela sugestão. Nada foi salvo ainda.</div>
            </div>
            <button type="button" class="btn btn-ghost ef-btn-mini" data-fc="descartar">Descartar sugestão</button>
        </div>
        <div class="ef-mt-fc-dias">${resumoDia('sabado', 'Sábado')}${resumoDia('domingo', 'Domingo')}</div>
        ${f[outro]?.incluido ? `<p class="ef-meta">Depois de salvar este dia, abra o ${outro === 'sabado' ? 'sábado' : 'domingo'} e clique de novo em Fechar fim de semana: a conta dos dois dias é feita junto (quem dobrar num dia não dobra no outro).</p>` : ''}
        <details class="ef-detalhes" open>
            <summary>O que mudou neste dia (${f.mudancas.length})</summary>${lista(mudancas)}
        </details>
        ${dia.vagas?.length ? `<details class="ef-detalhes" open><summary>Obrigatórios em branco (${dia.vagas.length})</summary>${lista(dia.vagas.map(v => escapeHtml(v.posto)))}<p class="ef-meta">Sem fiscal livre${f.modo === 'sem_dobra' ? '. Para cobrir, troque para "Sugerir dobra" ou digite o RE na célula.' : ' e sem quem possa dobrar (ninguém que folga pela base, sem dobra no fim de semana anterior).'}</p></details>` : ''}
        ${dia.dobras?.length ? `<details class="ef-detalhes"><summary>Dobras sugeridas (${dia.dobras.length})</summary>${lista(dia.dobras.map(x => `${itemFiscal(x)} — ${escapeHtml(x.posto)} · dobrou ${x.vezes_antes}x nas últimas 4 semanas`))}</details>` : ''}
        ${dia.liberados?.length ? `<details class="ef-detalhes"><summary>Saíram do modelo por folga (${dia.liberados.length})</summary>${lista(dia.liberados.map(x => `${fiscal(x.re)} — ${escapeHtml(x.posto)}`))}</details>` : ''}
        ${dia.sobrando?.length ? `<details class="ef-detalhes"><summary>Livres que sobraram (${dia.sobrando.length})</summary>${lista(dia.sobrando.map(itemFiscal))}<p class="ef-meta">Trabalham neste dia e não precisaram entrar. Podem cobrir um TS comum ou reforço.</p></details>` : ''}
        ${dia.fora_do_periodo?.length ? `<details class="ef-detalhes"><summary>Obrigatórios vagos no período de outro coordenador (${dia.fora_do_periodo.length})</summary>${lista(dia.fora_do_periodo.map(v => escapeHtml(v.posto)))}</details>` : ''}
        ${conta ? `<details class="ef-detalhes">
            <summary>Conta do quadro deste fim de semana</summary>
            <div class="ef-mt-rolagem"><table class="ef-mt-fc-conta">
                <thead><tr><th>Período</th><th>Postos obrigatórios (sáb + dom)</th><th>Fiscais no quadro</th><th>Faltam sem dobra</th><th>Quadro ideal (10% de reserva)</th><th>Pedir para contratar</th></tr></thead>
                <tbody>${conta}</tbody>
            </table></div>
            <p class="ef-meta">Cada fiscal trabalha um dos dois dias, então o que pesa é a soma de sábado e domingo. "Faltam sem dobra" é o número de dobras por fim de semana com o quadro de hoje.</p>
        </details>` : ''}`;
    painel.hidden = false;
}

async function aoClicarFechamento(e) {
    const b = e.target.closest('[data-fc]');
    if (!b) return;
    if (b.dataset.fc === 'descartar') {
        if (!(await perguntar('Descartar a sugestão?', '<p>A grade volta para o que está salvo (ou para o modelo).</p>', 'Descartar', 'Cancelar'))) return;
        estado.sujo = false;
        carregar(estado.data);
    }
}

// ─── Modal de pergunta ───────────────────────────────────────────────────

function perguntar(titulo, corpoHtml, textoSim, textoNao = 'Cancelar') {
    return new Promise(resolve => {
        const overlay = document.getElementById('ef-mt-modal');
        const bSim = document.getElementById('ef-mt-modal-sim');
        const bNao = document.getElementById('ef-mt-modal-nao');
        document.getElementById('ef-mt-modal-titulo').textContent = titulo;
        document.getElementById('ef-mt-modal-corpo').innerHTML = corpoHtml;
        bSim.hidden = !textoSim;
        bSim.textContent = textoSim || '';
        bNao.textContent = textoNao;
        const fechar = (valor) => {
            overlay.classList.remove('open');
            bSim.onclick = bNao.onclick = null;
            observador.disconnect();
            resolve(valor);
        };
        // Esc (modal.util.js) tira a classe "open": conta como Cancelar.
        const observador = new MutationObserver(() => { if (!overlay.classList.contains('open')) fechar(false); });
        bSim.onclick = () => fechar(true);
        bNao.onclick = () => fechar(false);
        overlay.classList.add('open');
        observador.observe(overlay, { attributes: true, attributeFilter: ['class'] });
        (textoSim ? bSim : bNao).focus();
    });
}
