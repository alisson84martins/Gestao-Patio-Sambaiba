/*
 * ao-vivo.abas.js — fase 2 (NIMER + SIM): as abas Partidas · Alertas ·
 * Fechamento da tela ao vivo. (Fase 3: o placar e a saúde dos robôs viraram
 * a linha de números e o bloco "Robô" do topo — ao-vivo.fiscal.js.)
 * -------------------------------------------------------------------------------
 * Rotas:
 *   GET  /monitoramento/linhas/{linha}/tempo-real      (a cada 30 s, junto com o mapa)
 *   POST /monitoramento/viagens/{id}/correcoes        (fiscal com turno aberto na linha)
 *   GET  /fiscalizacao/turnos/ativo                    (o turno de quem está logado)
 *   GET  /monitoramento/turnos/{turno}/fechamento-previa
 *
 * ⛔ A correção NUNCA escreve no NIMER (só leitura) — a tela diz isso.
 * O ICV do SIM é a NOSSA leitura das saídas, não o número oficial (decisão 4).
 * RE de motorista/cobrador é "como escalado no NIMER" — não prova quem faltou.
 * Texto da API só entra em innerHTML via escapeHtml().
 */

import { apiGet, apiPost, ApiError } from './api.js';
import { podeEscrever, podeLer } from './sessao.js';
import { escapeHtml } from './escape.js';

const FUSO = 'America/Sao_Paulo';
const AVISO_MIN = { sim: 3, nimer: 8 };    // amarelo
const ERRO_MIN = { sim: 10, nimer: 15 };   // vermelho
const ROTULO_STATUS = { done: 'Realizada', lost: 'Perdida', missing: 'Faltante', future: 'Programada' };
const ROTULO_SIM = { sim: 'SIM viu', nao: 'SIM não viu', sem_dado: 'sem dado', aguardando: '…' };
const ROTULO_MOTIVO = {
    FALTA_OPERADORES: 'Falta de operadores', RA: 'R.A', SOS: 'S.O.S', ATRASO_GARAGEM: 'Atraso de garagem',
    TROCA_OPERACIONAL: 'Troca operacional', TRANSITO: 'Trânsito (OBS)', OUTRO: 'Outro (OBS)',
};
const ROTULO_RECOLHE = { SOS: 'S.O.S (voltou a rodar)', RA: 'R.A (não voltou)', RA_PROVISORIO: 'R.A (provisório)' };

let linha = '';
let dados = null;
let turnoAtivo;          // undefined = ainda não buscou; null = sem turno
let corrigindo = null;   // id da viagem com o formulário aberto
let soAnormais = false;
let aoMudarAba = () => {};

function hora(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: FUSO });
}

function minutosDesde(iso) {
    return iso ? (Date.now() - new Date(iso).getTime()) / 60000 : Infinity;
}

function podeCorrigir() {
    const codigo = dados?.linha?.codigo || linha;   // código canônico (o da URL pode vir "1726")
    return podeEscrever('fiscalizacao') && turnoAtivo && (turnoAtivo.linhas || []).includes(codigo);
}

// ─── Saúde dos robôs (SIM e NIMER separados) ────────────────────────────

export function faixaRobo(nome, ultimaOk, statusUltimo, limites = { aviso: AVISO_MIN, erro: ERRO_MIN }, chave = 'sim') {
    const m = minutosDesde(ultimaOk);
    let classe = 'av-status-ok';
    let texto = `✅ ${nome} às ${hora(ultimaOk)}`;
    if (!ultimaOk) {
        classe = 'av-status-erro';
        texto = `🔴 ${nome} sem dados hoje`;
    } else if (statusUltimo === 'sessao' || m > limites.erro[chave]) {
        classe = 'av-status-erro';
        texto = `🔴 ${nome} sem dados desde ${hora(ultimaOk)}${statusUltimo === 'sessao' ? ' (login caiu)' : ''}`;
    } else if (m > limites.aviso[chave]) {
        classe = 'av-status-aviso';
        texto = `🟡 ${nome} sem atualizar desde ${hora(ultimaOk)}`;
    }
    return `<span class="av-robo ${classe}">${escapeHtml(texto)}</span>`;
}

// renderSaude/renderPlacar saíram na fase 3 (v4.0): a linha de números e o
// bloco "Robô" do topo da tela moram em ao-vivo.fiscal.js.

// ─── Partidas ───────────────────────────────────────────────────────────

function etiquetaPontualidade(pt) {
    if (!pt) return '';
    const d = pt.desvio_min;
    const texto = {
        pontual: 'pontual',
        atrasada: `atrasada +${d}`,
        adiantada: `adiantada ${d}`,
        perdida: 'perdida',
        sem_dado: 'sem hora',
    }[pt.classe] || pt.classe;
    return ` <small class="av-tag av-pont-${escapeHtml(pt.classe)}">${escapeHtml(texto)}</small>`;
}

function anormal(p) {
    return p.status_efetivo !== 'done' || p.sim_viu === 'nao' || p.carro_trocado_sim || p.corrigida || p.duplicada_nimer
        || (p.pontualidade && !['pontual', 'perdida'].includes(p.pontualidade.classe));
}

function linhaPartida(p) {
    const status = ROTULO_STATUS[p.status_efetivo] || p.status_efetivo || '—';
    const corr = p.corrigida ? ` <small class="av-tag av-tag-corr" title="NIMER: ${escapeHtml(ROTULO_STATUS[p.status_nimer] || p.status_nimer || '—')}">corrigida</small>` : '';
    const carro = p.carro_trocado_sim
        ? `${escapeHtml(p.carro_programado || '—')} → <b>${escapeHtml(p.carro_real_sim)}</b> (SIM)`
        : escapeHtml(p.carro_programado || '—');
    const motivo = p.contador ? (ROTULO_MOTIVO[p.contador] || p.contador) : (p.motivo ? ROTULO_MOTIVO[p.motivo] || p.motivo : '');
    const recolhe = p.recolhe ? ` · ${ROTULO_RECOLHE[p.recolhe]}` : '';
    const motivoTexto = p.status_efetivo === 'done' ? '' : [motivo, p.motivo_nimer && !p.contador ? `NIMER: ${p.motivo_nimer}` : ''].filter(Boolean).join(' · ');
    const pessoa = p.escalado_nimer.sem_motorista ? 'SEM MOTORISTA' : `mot. ${p.escalado_nimer.motorista_re || '—'}`;
    const botao = podeCorrigir() ? `<button type="button" class="av-btn-corrigir" data-corrigir="${p.id}">Corrigir</button>` : '';
    const form = corrigindo === p.id ? formCorrecao(p) : '';
    return `<div class="av-partida av-partida-${escapeHtml(p.status_efetivo || 'x')}${p.sim_viu === 'nao' ? ' av-partida-naoviu' : ''}">
        <div class="av-partida-topo">
            <span class="av-partida-hora">${escapeHtml(p.programado || '—')}${p.horario_cheio ? ' <small title="Horário cheio: não sai antes">⏱</small>' : ''}</span>
            <i class="av-bolinha av-s${p.sentido === 2 ? 2 : 1}"></i>
            <span>Tab. ${escapeHtml(p.tabela || '—')}</span>
            <span class="av-partida-status">${escapeHtml(status)}${corr}${p.status_efetivo === 'done' ? etiquetaPontualidade(p.pontualidade) : ''}</span>
            ${botao}
        </div>
        <div class="av-partida-det">
            Carro ${carro} · saída ${escapeHtml(p.saida_real || '—')}${p.fonte_inicio ? ` (${escapeHtml(p.fonte_inicio)})` : ''}
            · <span class="av-sim-${escapeHtml(p.sim_viu)}">${escapeHtml(ROTULO_SIM[p.sim_viu] || p.sim_viu)}${p.sim_saida ? ' ' + escapeHtml(p.sim_saida) : ''}</span>
            ${p.duplicada_nimer ? ' · <b>duplicada no NIMER (não conta)</b>' : ''}
        </div>
        ${motivoTexto || recolhe ? `<div class="av-partida-det">${escapeHtml(motivoTexto + recolhe)}</div>` : ''}
        <div class="av-partida-det av-partida-re">Escalado no NIMER: ${escapeHtml(pessoa)} · cob. ${escapeHtml(p.escalado_nimer.cobrador_re || '—')}</div>
        ${form}
    </div>`;
}

function opcoes(lista, atual) {
    return lista.map(([v, t]) => `<option value="${escapeHtml(v)}"${v === atual ? ' selected' : ''}>${escapeHtml(t)}</option>`).join('');
}

function formCorrecao(p) {
    return `<form class="av-form-corr" data-viagem="${p.id}">
        <p class="av-form-aviso">A correção vale só aqui no sistema. ⛔ O NIMER não é alterado.</p>
        <label>O que corrigir
            <select name="campo">${opcoes([['status', 'Status'], ['motivo', 'Motivo'], ['carro', 'Carro'], ['real_inicio', 'Hora da saída']], 'status')}</select>
        </label>
        <label class="av-form-valor">Novo valor
            <select name="valor">${opcoes([['done', 'Realizada'], ['lost', 'Perdida']], p.status_efetivo === 'done' ? 'lost' : 'done')}</select>
        </label>
        <label>Justificativa (obrigatória)
            <textarea name="justificativa" rows="2" maxlength="500" required placeholder="Ex.: carro saiu às 13:12, conferido no terminal"></textarea>
        </label>
        <div class="av-form-erro" hidden></div>
        <div class="av-form-acoes"><button type="submit">Salvar correção</button> <button type="button" data-cancelar>Cancelar</button></div>
    </form>`;
}

function campoValor(campo) {
    if (campo === 'status') return `<select name="valor">${opcoes([['done', 'Realizada'], ['lost', 'Perdida']])}</select>`;
    if (campo === 'motivo') return `<select name="valor">${opcoes(Object.entries(ROTULO_MOTIVO))}</select>`;
    if (campo === 'carro') return '<input name="valor" inputmode="numeric" maxlength="5" placeholder="2412" required>';
    return '<input name="valor" type="time" required>';
}

function renderPartidas() {
    const el = document.getElementById('av-partidas');
    if (!dados) return;
    const lista = dados.partidas.filter((p) => !soAnormais || anormal(p));
    const aviso = dados.linha.tem_nimer ? '' : '<div class="av-vazio">Esta linha não tem o NIMER cadastrado: sem partidas.</div>';
    el.innerHTML = aviso + (lista.length
        ? lista.map(linhaPartida).join('')
        : '<div class="av-vazio">Nenhuma partida do NIMER para mostrar.</div>');
}

// ─── Alertas operacionais ───────────────────────────────────────────────

function renderAlertasOperacionais() {
    const el = document.getElementById('av-alertas-op');
    const n = document.getElementById('av-n-alertas-op');
    if (!dados) return;
    const lista = dados.alertas || [];
    const ativos = lista.filter((a) => a.ativo).length;
    n.textContent = ativos;
    n.className = 'av-pilula' + (ativos ? '' : ' av-pilula-zero');
    el.innerHTML = lista.length ? lista.map((a) => `<div class="av-cartao av-cartao-${a.nivel === 'alerta' ? 'alerta' : 'atencao'}${a.ativo ? '' : ' av-cartao-sumiu'}">
        <span class="av-cartao-topo"><b>${a.nivel === 'alerta' ? '⚠ ' : ''}${escapeHtml(a.inicio || '')}</b>
            <small>${escapeHtml([a.tabela ? `tab. ${a.tabela}` : '', a.carro ? `carro ${a.carro}` : ''].filter(Boolean).join(' · '))}</small></span>
        <span class="av-cartao-texto">${escapeHtml(a.texto)}</span>
        ${a.escalado_nimer && (a.escalado_nimer.motorista_re || a.escalado_nimer.cobrador_re)
            ? `<span class="av-cartao-eta">Escalado no NIMER: mot. ${escapeHtml(a.escalado_nimer.motorista_re || '—')} · cob. ${escapeHtml(a.escalado_nimer.cobrador_re || '—')}</span>` : ''}
        <span class="av-cartao-eta">👉 ${escapeHtml(a.acao_sugerida)}</span>
    </div>`).join('') : '<div class="av-vazio">Nenhum alerta operacional agora.</div>';
}

// ─── Fechamento (prévia) ────────────────────────────────────────────────

async function carregarTurno() {
    if (turnoAtivo !== undefined) return;
    if (!podeLer('fiscalizacao')) {
        turnoAtivo = null;
        return;
    }
    try {
        turnoAtivo = await apiGet('/fiscalizacao/turnos/ativo');
    } catch {
        turnoAtivo = null;
    }
}

async function renderFechamento() {
    const el = document.getElementById('av-fechamento');
    await carregarTurno();
    if (!turnoAtivo) {
        el.innerHTML = '<div class="av-vazio">A prévia do fechamento é do fiscal com turno aberto nesta linha.</div>';
        return;
    }
    el.innerHTML = '<div class="av-vazio">Montando a prévia…</div>';
    let previa;
    try {
        previa = await apiGet(`/monitoramento/turnos/${encodeURIComponent(turnoAtivo.id)}/fechamento-previa`);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        el.innerHTML = `<div class="av-vazio">${escapeHtml(err.message)}</div>`;
        return;
    }
    el.innerHTML = previa.map((p, i) => `<div class="av-fech">
        <div class="av-grupo">${escapeHtml(p.linha_codigo)} · ${p.fonte === 'NIMER'
            ? `montado pelo NIMER + correções${p.corrigidas ? ` (${p.corrigidas} corrigida${p.corrigidas === 1 ? '' : 's'})` : ''}`
            : 'registro à mão do fiscal'}</div>
        <pre class="av-fech-texto" id="av-fech-${i}">${escapeHtml(p.texto)}</pre>
        <button type="button" data-copiar="av-fech-${i}">Copiar para o WhatsApp</button>
    </div>`).join('') + '<p class="av-rodape">Revise antes de mandar. Para mudar uma perda, use "Corrigir" na aba Partidas.</p>';
}

// ─── Abas ───────────────────────────────────────────────────────────────

let abaAtual = 'mapa';

function trocarAba(aba) {
    abaAtual = aba;
    for (const b of document.querySelectorAll('[data-aba]')) {
        b.classList.toggle('av-aba-ativa', b.dataset.aba === aba);
        b.setAttribute('aria-selected', b.dataset.aba === aba ? 'true' : 'false');
    }
    document.querySelector('.av-tela').hidden = aba !== 'mapa';
    for (const id of ['partidas', 'alertas', 'fechamento']) {
        document.getElementById(`av-aba-${id}`).hidden = aba !== id;
    }
    if (aba === 'fechamento') renderFechamento();
    aoMudarAba(aba);
}

/** Abre a aba Partidas com o formulário de correção da viagem `id` (A conferir, fase 3). */
export function abrirCorrecao(id) {
    corrigindo = Number(id);
    trocarAba('partidas');
    renderPartidas();
    document.querySelector(`[data-viagem="${corrigindo}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export function iniciarAbas(opcoes) {
    linha = opcoes.linha;
    aoMudarAba = opcoes.aoMudarAba || aoMudarAba;
    carregarTurno().then(() => renderPartidas());

    document.querySelector('.av-abas').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-aba]');
        if (b) trocarAba(b.dataset.aba);
    });
    document.getElementById('av-so-anormais').addEventListener('change', (ev) => {
        soAnormais = ev.target.checked;
        renderPartidas();
    });

    const partidas = document.getElementById('av-partidas');
    partidas.addEventListener('click', (ev) => {
        const abrir = ev.target.closest('[data-corrigir]');
        if (abrir) {
            const id = Number(abrir.dataset.corrigir);
            corrigindo = corrigindo === id ? null : id;
            renderPartidas();
            return;
        }
        if (ev.target.closest('[data-cancelar]')) {
            corrigindo = null;
            renderPartidas();
        }
    });
    partidas.addEventListener('change', (ev) => {
        if (ev.target.name !== 'campo') return;
        const rotulo = ev.target.closest('form').querySelector('.av-form-valor');
        rotulo.innerHTML = `Novo valor ${campoValor(ev.target.value)}`;
    });
    partidas.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const form = ev.target;
        const erro = form.querySelector('.av-form-erro');
        const corpo = {
            campo: form.campo.value,
            valor: form.valor.value,
            justificativa: form.justificativa.value.trim(),
        };
        if (!corpo.justificativa) {
            erro.textContent = 'Escreva a justificativa.';
            erro.hidden = false;
            return;
        }
        form.querySelector('[type=submit]').disabled = true;
        try {
            await apiPost(`/monitoramento/viagens/${encodeURIComponent(form.dataset.viagem)}/correcoes`, corpo);
        } catch (err) {
            erro.textContent = err.message;
            erro.hidden = false;
            form.querySelector('[type=submit]').disabled = false;
            return;
        }
        corrigindo = null;
        await atualizarTempoReal();
    });

    document.getElementById('av-aba-fechamento').addEventListener('click', async (ev) => {
        const b = ev.target.closest('[data-copiar]');
        if (!b) return;
        const texto = document.getElementById(b.dataset.copiar).textContent;
        try {
            await navigator.clipboard.writeText(texto);
            b.textContent = 'Copiado ✓';
        } catch {
            b.textContent = 'Selecione o texto e copie';
        }
    });
}

export async function atualizarTempoReal() {
    try {
        dados = await apiGet(`/monitoramento/linhas/${encodeURIComponent(linha)}/tempo-real`);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('av-partidas').innerHTML = `<div class="av-vazio">${escapeHtml(err.message)}</div>`;
        return;
    }
    if (corrigindo === null) renderPartidas();   // não apaga o formulário no meio da digitação
    renderAlertasOperacionais();
}
