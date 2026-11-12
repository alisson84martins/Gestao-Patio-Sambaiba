/*
 * fiscalizacao-central.page.js — Central da Fiscalização (fase 2, bloco I)
 * -------------------------------------------------------------------------------
 * A coordenação acompanha as linhas dela (ou todas) em tempo real e age a
 * tempo: placar por perda absoluta (D23) com farol pela meta de
 * fiscalizacao.parametro (D29), fila de ação com "registrar ação" (D26) e
 * "resolvido", Pareto do dia, cobertura dos pontos, correções dos fiscais e
 * saúde dos robôs. Pensada para o PC, funciona no celular.
 *
 * v4.0 fase 3 (bloco F): no topo, "Ao vivo · minhas linhas" — um cartão por
 * linha de Minhas linhas (fiscalizacao.linha_coordenador; ADMIN: todas), a
 * fila de ação delas com a baixa (alarme falso · atitude tomada · contatei
 * CCO) e o bloco Robô recolhido. Toque no cartão abre a tela ao vivo.
 *
 * Rotas: GET /monitoramento/central e /monitoramento/central/minhas-linhas
 * (a cada 30 s, pausado com a aba escondida), POST /monitoramento/central/alertas
 * e POST /monitoramento/alertas/baixa.
 * Texto da API só entra em innerHTML via escapeHtml().
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, ApiError } from './api.js';
import { podeEscrever, podeLer } from './sessao.js';
import { escapeHtml } from './escape.js';

if (!requireAuth()) throw new Error('Não autenticado');

if (!podeLer('fiscalizacao_painel')) {
    window.location.replace('modulos.html');
    throw new Error('Sem acesso a fiscalizacao_painel');
}

const FUSO = 'America/Sao_Paulo';
const INTERVALO_MS = 30000;
const ROTULO_FAROL = { verde: '🟢', amarelo: '🟡', vermelho: '🔴', sem_dado: '⚪' };
const ROTULO_TIPO = {
    sem_motorista: 'Tabela sem motorista', partida_atrasada: 'Partida atrasada', carro_trocado_linha: 'Carro em outra linha',
    buraco_intervalo: 'Buraco de intervalo', nao_contabilizada: 'Não contabilizada', desvio: 'Desvio', virada: 'Virada',
    retorno: 'Retorno', parado: 'Parado', semtx: 'Sem transmissão', recolhida: 'Recolhida', terminal: 'Parado no terminal',
    nao_liberar_antes: 'Não liberar antes', saiu_adiantado: 'Saiu adiantado',
    saida_adiantada: 'Saída adiantada', troca_sem_baixa: 'Troca sem baixa', indo_garagem: 'Indo para a G3',
};
const SETA = { 1: 'TP→TS', 2: 'TS→TP' };
const BAIXAS = [['alarme_falso', 'Alarme falso'], ['atitude_tomada', 'Atitude tomada'], ['contatou_cco', 'Contatei CCO/coordenação']];

let aoVivo = null;   // /monitoramento/central/minhas-linhas

let dados = null;
let timer = null;
let acaoAberta = null;   // chave do alerta com o formulário aberto

function hora(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: FUSO });
}

function minutosDesde(iso) {
    return iso ? (Date.now() - new Date(iso).getTime()) / 60000 : Infinity;
}

function pct(v) {
    return v == null ? '—' : `${v.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`;
}

function initHeader() {
    const user = getCurrentUser();
    if (user) {
        document.getElementById('user-name').textContent = user.nome || '—';
        document.getElementById('user-meta').textContent = (user.re || '—').toUpperCase();
    }
    document.getElementById('btn-logout').addEventListener('click', () => {
        logout();
        window.location.replace('index.html');
    });
}

// ─── Render ─────────────────────────────────────────────────────────────

function renderAgregado() {
    const el = document.getElementById('fc-agregado');
    const a = dados.agregado;
    if (!a) {
        el.hidden = true;
        return;
    }
    const blocos = [
        // v4.0 fase 3 (T3): número real (NIMER × GPS confirmados); o ICV/IPP do
        // robô sozinho saiu da tela.
        [`ICV ${ROTULO_FAROL[a.farol] || ''}`, pct(a.icv_real_ate_agora), `${a.realizadas_reais_ate_agora} de ${a.programadas_reais} confirmadas`],
        ['IPP', pct(a.ipp_real_ate_agora), 'pontualidade de partida'],
        ['Perdidas', String(a.perdidas_reais ?? '—'), `a conferir: ${a.a_conferir ?? 0}`],
        ['Meta', pct(dados.meta.icv_meta), `aceitável ${pct(dados.meta.icv_aceitavel)}`],
    ];
    el.innerHTML = blocos.map(([t, v, s]) => `<div class="av-placar-item">
        <span class="av-placar-titulo">${escapeHtml(t)}</span><b class="av-placar-valor">${escapeHtml(v)}</b>
        <small>${escapeHtml(s)}</small></div>`).join('');
    el.hidden = false;
}

function renderPlacar() {
    const el = document.getElementById('fc-placar');
    if (!dados.placar.length) {
        el.innerHTML = '<div class="av-vazio">Nenhuma linha monitorada no seu escopo.</div>';
        return;
    }
    el.innerHTML = `<div class="fc-tabela" role="table">
        <div class="fc-linha fc-cab" role="row"><span>Linha</span><span>ICV</span><span>NIMER</span><span>IPP</span><span>Perdidas</span><span>Fecha em</span><span></span></div>
        ${dados.placar.map((p) => `<div class="fc-linha fc-farol-${escapeHtml(p.farol)}" role="row">
            <span><b>${ROTULO_FAROL[p.farol] || ''} ${escapeHtml(p.linha_codigo)}</b>${p.tem_nimer ? '' : ' <small>(sem NIMER)</small>'}</span>
            <span>${pct(p.icv_real)}</span><span title="ICV pelo NIMER (conferência)">${pct(p.icv_nimer)}</span><span>${pct(p.ipp_real)}</span>
            <span><b>${escapeHtml(String(p.perdidas_reais ?? '—'))}</b> <small>de ${escapeHtml(String(p.programadas_ate_agora))}</small></span>
            <span>${pct(p.projecao)}</span>
            <span><a class="av-entrada fc-abrir" href="ao-vivo.html?linha=${encodeURIComponent(p.linha_codigo)}">🛰 Ao vivo</a></span>
        </div>`).join('')}
    </div>`;
}

function formAcao(a) {
    return `<form class="av-form-corr" data-chave="${escapeHtml(a.chave)}">
        <label>O que foi feito <textarea name="descricao" rows="2" maxlength="1000" placeholder="Ex.: liguei para o plantão, carro reserva saindo"></textarea></label>
        <label class="av-filtro"><input type="checkbox" name="resolver" checked> Marcar como resolvido</label>
        <div class="av-form-erro" hidden></div>
        <div class="av-form-acoes"><button type="submit">Salvar</button> <button type="button" data-cancelar>Cancelar</button></div>
    </form>`;
}

function renderFila() {
    const el = document.getElementById('fc-fila');
    const n = document.getElementById('fc-n-fila');
    const pendentes = dados.fila.filter((a) => !a.resolvido && a.ativo);
    n.textContent = pendentes.length;
    n.className = 'av-pilula' + (pendentes.length ? '' : ' av-pilula-zero');
    const escreve = podeEscrever('fiscalizacao_painel');
    el.innerHTML = dados.fila.length ? dados.fila.map((a) => `<div class="av-cartao av-cartao-${a.resolvido ? 'sumiu' : a.nivel === 'alerta' ? 'alerta' : 'atencao'}">
        <span class="av-cartao-topo"><b>${a.nivel === 'alerta' && !a.resolvido ? '⚠ ' : ''}${escapeHtml(a.linha)} · ${escapeHtml(ROTULO_TIPO[a.tipo] || a.tipo)}</b>
            <small>${escapeHtml([a.inicio, a.tabela ? `tab. ${a.tabela}` : '', a.carro ? `carro ${a.carro}` : ''].filter(Boolean).join(' · '))}</small></span>
        <span class="av-cartao-texto">${escapeHtml(a.texto)}</span>
        ${a.escalado_nimer && (a.escalado_nimer.motorista_re || a.escalado_nimer.cobrador_re)
            ? `<span class="av-cartao-eta">Escalado no NIMER: mot. ${escapeHtml(a.escalado_nimer.motorista_re || '—')} · cob. ${escapeHtml(a.escalado_nimer.cobrador_re || '—')}</span>` : ''}
        <span class="av-cartao-eta">👉 ${escapeHtml(a.acao_sugerida)}</span>
        ${a.resolvido ? `<span class="av-cartao-eta">✔ Resolvido às ${escapeHtml(a.resolvido_em || '')}</span>`
            : escreve ? `<span class="fc-acoes"><button type="button" class="av-btn-corrigir" data-acao="${escapeHtml(a.chave)}">Registrar ação</button>
                <button type="button" class="av-btn-corrigir" data-resolver="${escapeHtml(a.chave)}">Resolvido</button></span>` : ''}
        ${acaoAberta === a.chave ? formAcao(a) : ''}
    </div>`).join('') : '<div class="av-vazio">Nenhum alerta agora. 👏</div>';
}

function renderPareto() {
    const el = document.getElementById('fc-pareto');
    if (!dados.pareto.length) {
        el.innerHTML = '<div class="av-vazio">Nenhuma perda até agora.</div>';
        return;
    }
    const max = Math.max(...dados.pareto.map((p) => p.perdas));
    el.innerHTML = dados.pareto.map((p) => `<div class="fc-pareto">
        <span class="fc-pareto-rotulo">${escapeHtml(p.motivo)}</span>
        <span class="fc-barra"><i style="width:${Math.round(p.perdas / max * 100)}%"></i></span>
        <span class="fc-pareto-num"><b>${p.perdas}</b> <small>${p.pct}% · acum. ${p.acumulado_pct}%</small></span>
    </div>`).join('');
}

function renderFaixas() {
    // ICV por faixa usa a hora REAL de saída: quem sai antes da hora cheia
    // (06:58 no lugar de 07:00) cai na faixa anterior e a faixa perde a viagem.
    const lista = dados.adiantamentos_de_faixa || [];
    document.getElementById('fc-faixas').innerHTML = lista.length
        ? lista.map((a) => `<div class="av-log"><b>${escapeHtml(a.linha_codigo)}</b> — ${escapeHtml(a.texto)}</div>`).join('')
        : '<div class="av-vazio">Nenhuma faixa perdeu viagem por saída adiantada hoje.</div>';
}

function renderCobertura() {
    document.getElementById('fc-cobertura').innerHTML = dados.cobertura.map((c) => `<div class="fc-cob ${c.coberta ? '' : 'fc-cob-falta'}">
        <b>${c.coberta ? '👮' : '⚠'} ${escapeHtml(c.linha_codigo)}</b>
        ${c.coberta ? c.fiscais.map((f) => `<span>${escapeHtml(f.re)} ${escapeHtml(f.nome || '')}${f.ponto ? ` · ${escapeHtml(f.ponto)}` : ''}</span>`).join('')
            : '<span>sem fiscal com turno aberto</span>'}
    </div>`).join('') || '<div class="av-vazio">—</div>';
}

function renderCorrecoes() {
    document.getElementById('fc-correcoes').innerHTML = dados.correcoes.length ? dados.correcoes.map((c) => `<div class="av-log av-log-info">
        <span class="av-log-hora">${escapeHtml(c.hora || '')}</span> · <b>${escapeHtml(c.autor.re)}</b> ${escapeHtml(c.autor.nome || '')} —
        ${escapeHtml(c.linha_codigo)} tab. ${escapeHtml(c.tabela || '—')} (${escapeHtml(c.programado || '—')}):
        ${escapeHtml(c.campo)} ${escapeHtml(c.de || '—')} → <b>${escapeHtml(c.para || '—')}</b>. “${escapeHtml(c.justificativa)}”
    </div>`).join('') : '<div class="av-vazio">Nenhuma correção hoje.</div>';
}

function faixa(nome, iso, sessao, aviso, erro) {
    const m = minutosDesde(iso);
    if (!iso) return `<span class="av-robo av-status-erro">🔴 ${nome} sem dados hoje</span>`;
    if (sessao || m > erro) return `<span class="av-robo av-status-erro">🔴 ${nome} desde ${hora(iso)}${sessao ? ' (login caiu)' : ''}</span>`;
    if (m > aviso) return `<span class="av-robo av-status-aviso">🟡 ${nome} às ${hora(iso)}</span>`;
    return `<span class="av-robo av-status-ok">✅ ${nome} às ${hora(iso)}</span>`;
}

function renderSaude() {
    document.getElementById('fc-saude').innerHTML = dados.saude.map((s) => `<div class="fc-cob">
        <b>${escapeHtml(s.linha_codigo)}</b>
        ${faixa('SIM', s.sim_ultima_ok_em, false, 3, 10)}
        ${s.tem_nimer ? faixa('NIMER', s.nimer_ultima_ok_em, s.nimer_status === 'sessao', 8, 15) : '<span class="av-robo av-status-aviso">NIMER não cadastrado</span>'}
    </div>`).join('');
}

function renderStatus() {
    const el = document.getElementById('fc-status');
    el.className = 'av-status av-status-ok';
    el.textContent = `● Atualizado às ${hora(dados.atualizado_em)} — ${dados.escopo === 'coordenador' ? 'suas linhas' : 'todas as linhas'}`;
}

function render() {
    renderStatus();
    renderAgregado();
    renderPlacar();
    if (acaoAberta === null) renderFila();
    renderPareto();
    renderFaixas();
    renderCobertura();
    renderCorrecoes();
    renderSaude();
}

// ─── Ao vivo · minhas linhas (fase 3, bloco F) ──────────────────────────

function chip(carro, sentido) {
    return `<span class="av-chip av-chip-s${sentido === 2 ? 2 : 1}">${escapeHtml(carro || '—')}</span>`;
}

function renderCartoes() {
    const el = document.getElementById('fc-cartoes');
    document.getElementById('fc-av-sub').textContent = aoVivo ? `atualizado às ${hora(aoVivo.atualizado_em)}` : '';
    if (!aoVivo.cartoes.length) {
        el.innerHTML = '<div class="av-vazio">Nenhuma linha em <a href="fiscal-painel.html">Minhas linhas</a> — marque as suas para acompanhar aqui.</div>';
        return;
    }
    el.innerHTML = aoVivo.cartoes.map((c) => {
        const sentidos = Object.entries(c.por_sentido).filter(([, b]) => b.programadas)
            .map(([s, b]) => `<span><i class="av-bolinha av-s${s}"></i>${escapeHtml(SETA[s] || s)} ${b.realizadas}/${b.programadas} · ${b.perdidas} perd.</span>`).join('');
        const fiscais = c.fiscais.length
            ? c.fiscais.map((f) => `<span>👮 ${escapeHtml(f.re)} ${escapeHtml(f.terminal)} · ${escapeHtml(f.selo)}</span>`).join('')
            : '<span class="fc-sem-fiscal">sem fiscal</span>';
        return `<a class="fc-cartao${c.alertas_abertos ? ' fc-cartao-alerta' : ''}" href="ao-vivo.html?linha=${encodeURIComponent(c.linha_codigo)}">
            <span class="fc-cartao-topo"><b>${escapeHtml(c.linha_codigo)}</b>
                <span class="fv-caixinha fc-caixinha${c.alertas_abertos ? '' : ' fv-caixinha-zero'}${c.tem_alerta ? ' fv-caixinha-pisca' : ''}"
                      aria-label="${c.alertas_abertos} alertas abertos">🔔 ${c.alertas_abertos}</span></span>
            <span class="fc-cartao-icv">ICV <b class="fv-farol-${escapeHtml(c.farol)}">${pct(c.icv)}</b>
                ${c.a_conferir ? ` · A conferir: <b>${c.a_conferir}</b>` : ''}</span>
            <span class="fc-cartao-sentidos">${sentidos || '<span>sem partidas do NIMER</span>'}</span>
            <span class="fc-cartao-fiscais">${fiscais}</span>
        </a>`;
    }).join('');
}

function renderFilaAoVivo() {
    const el = document.getElementById('fc-fila-av');
    const n = document.getElementById('fc-n-fila-av');
    const fila = aoVivo.fila;
    n.textContent = fila.length;
    n.className = 'av-pilula' + (fila.length ? '' : ' av-pilula-zero');
    const escreve = podeEscrever('fiscalizacao_painel');
    el.innerHTML = fila.length ? fila.map((a) => `<div class="av-cartao av-cartao-${a.nivel === 'alerta' ? 'alerta' : 'atencao'}">
        <span class="av-cartao-topo">${chip(a.carro, a.sentido)} <b>${escapeHtml(a.linha_codigo)}</b>
            <small>${escapeHtml([a.inicio, ROTULO_TIPO[a.tipo] || a.tipo, a.tabela ? `tab ${a.tabela}` : ''].filter(Boolean).join(' · '))}</small>
            ${a.reaberto ? '<small class="fv-reaberto">voltou</small>' : ''}</span>
        <span class="av-cartao-texto">${escapeHtml(a.texto)}</span>
        <span class="av-cartao-eta">👉 ${escapeHtml(a.acao_sugerida)}</span>
        ${escreve ? `<span class="fv-baixas">${BAIXAS.map(([m, t]) => `<button type="button" data-baixa="${escapeHtml(m)}"
            data-chave="${escapeHtml(a.chave)}" data-linha="${escapeHtml(a.linha_codigo)}">${escapeHtml(t)}</button>`).join('')}</span>` : ''}
    </div>`).join('') : '<div class="av-vazio">Nenhum alerta aberto nas suas linhas. 👏</div>';
}

function renderRoboAoVivo() {
    const r = aoVivo.robo;
    const saude = r.saude.map((s) => `<div class="fc-cob"><b>${escapeHtml(s.linha_codigo)}</b>
        ${faixa('SIM', s.sim_ultima_ok_em, false, 3, 10)}
        ${s.tem_nimer ? faixa('NIMER', s.nimer_ultima_ok_em, s.nimer_status === 'sessao', 8, 15) : '<span class="av-robo av-status-aviso">NIMER não cadastrado</span>'}
    </div>`).join('');
    const falsos = r.alarmes_falsos.length
        ? r.alarmes_falsos.map((a) => `<span class="av-robo">${escapeHtml(ROTULO_TIPO[a.tipo] || a.tipo)}: ${a.quantidade}</span>`).join('')
        : '<span class="av-vazio">nenhum alarme falso hoje</span>';
    document.getElementById('fc-robo-av').innerHTML = `${saude}<div class="fc-cob"><b>Alarmes falsos hoje</b> ${falsos}</div>`;
}

async function atualizarAoVivo() {
    try {
        aoVivo = await apiGet('/monitoramento/central/minhas-linhas');
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        document.getElementById('fc-cartoes').innerHTML = `<div class="av-vazio">${escapeHtml(err.message)}</div>`;
        return;
    }
    renderCartoes();
    renderFilaAoVivo();
    renderRoboAoVivo();
}

document.getElementById('fc-fila-av').addEventListener('click', async (ev) => {
    const b = ev.target.closest('[data-baixa]');
    if (!b) return;
    const botoes = b.parentElement.querySelectorAll('button');
    for (const x of botoes) x.disabled = true;
    try {
        await apiPost('/monitoramento/alertas/baixa', { linha_codigo: b.dataset.linha, chave: b.dataset.chave, motivo: b.dataset.baixa });
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        for (const x of botoes) x.disabled = false;
        b.parentElement.insertAdjacentHTML('afterend', `<span class="av-form-erro">${escapeHtml(err.message)}</span>`);
        return;
    }
    await atualizar();
});

// ─── Ciclo ──────────────────────────────────────────────────────────────

async function atualizar() {
    atualizarAoVivo();   // fase 3: minhas linhas ao vivo (em paralelo)
    const todas = document.getElementById('fc-todas').checked;
    try {
        dados = await apiGet(`/monitoramento/central${todas ? '?todas=true' : ''}`);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        const el = document.getElementById('fc-status');
        el.className = 'av-status av-status-aviso';
        el.textContent = err.status === 0 ? 'Sem conexão com o sistema — tentando de novo em 30 s' : `Erro: ${err.message}`;
        return;
    }
    render();
}

async function tratar(corpo, erroEl) {
    try {
        await apiPost('/monitoramento/central/alertas', corpo);
    } catch (err) {
        if (erroEl) {
            erroEl.textContent = err.message;
            erroEl.hidden = false;
        }
        return false;
    }
    acaoAberta = null;
    await atualizar();
    return true;
}

function alertaPorChave(chave) {
    return dados.fila.find((a) => a.chave === chave);
}

document.getElementById('fc-fila').addEventListener('click', async (ev) => {
    const abrir = ev.target.closest('[data-acao]');
    if (abrir) {
        acaoAberta = acaoAberta === abrir.dataset.acao ? null : abrir.dataset.acao;
        renderFila();
        return;
    }
    const resolver = ev.target.closest('[data-resolver]');
    if (resolver) {
        const a = alertaPorChave(resolver.dataset.resolver);
        if (a) await tratar({ chave: a.chave, linha_codigo: a.linha, inicio_em: a.inicio_em, resolver: true });
        return;
    }
    if (ev.target.closest('[data-cancelar]')) {
        acaoAberta = null;
        renderFila();
    }
});

document.getElementById('fc-fila').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    const a = alertaPorChave(form.dataset.chave);
    const erro = form.querySelector('.av-form-erro');
    const descricao = form.descricao.value.trim();
    if (!descricao && !form.resolver.checked) {
        erro.textContent = 'Escreva o que foi feito ou marque como resolvido.';
        erro.hidden = false;
        return;
    }
    if (a) await tratar({ chave: a.chave, linha_codigo: a.linha, inicio_em: a.inicio_em, descricao, resolver: form.resolver.checked }, erro);
});

document.getElementById('fc-todas').addEventListener('change', atualizar);

function iniciarPolling() {
    pararPolling();
    timer = setInterval(atualizar, INTERVALO_MS);
}

function pararPolling() {
    if (timer) clearInterval(timer);
    timer = null;
}

document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        pararPolling();
    } else {
        atualizar();
        iniciarPolling();
    }
});

initHeader();
atualizar();
iniciarPolling();
