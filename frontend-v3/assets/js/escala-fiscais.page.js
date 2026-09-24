/*
 * Escala de Fiscais — montagem do dia, cadastros e importação dos modelos
 * ------------------------------------------------------------------
 * UMA aba do módulo COORDENADORIA, com sub-abas. A primeira, "Montar
 * escala", mora em escala-fiscais.montagem.js (Fase 4); a impressão é a
 * página própria escala-fiscais-impressao.html (Fase 5). A tela do fiscal
 * ("Minha escala") NÃO mora aqui.
 *
 * Regras que atravessam o arquivo:
 *   - RE é sempre TEXTO (tem RE com zero à esquerda e com letra).
 *   - D-A (24/09): o FISCAL entra pelo RE mesmo sem cadastro em Pessoas; o
 *     nome aparece quando existir cadastro. O COORDENADOR continua exigindo
 *     cadastro (campos com data-exige-cadastro no HTML).
 *   - D-B: sem RE, a escala guarda o texto da planilha (marcador): vazio,
 *     ****, xxx, G1, DIRETO... É esse texto que a impressão vai mostrar.
 *   - Datas com dataLocalISO(), 🔴 nunca toISOString() (armadilha do fuso).
 *   - A trava real é o backend (exige("escala_fiscal")); esconder botão
 *     aqui é só pra ninguém bater numa porta fechada sem saber por quê.
 *   - Importação: SIMULAR não grava; a correção é feita no próprio JSON
 *     carregado na memória (campo editado ou linha marcada com
 *     "_descartar"), simula de novo e só então CONFIRMA.
 */
import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, apiPatch, apiDelete, apiUpload, ApiError } from './api.js';
import { podeEscrever } from './sessao.js';
import { escapeHtml } from './escape.js';
import { aplicarMascara } from './mascaras.js';
import { dataLocalISO } from './data.util.js';
import { iniciarMontagem, carregarMontagem } from './escala-fiscais.montagem.js';

if (!requireAuth()) {
    throw new Error('Sessao nao autenticada');
}

const API = '/escala-fiscais';
const escreve = podeEscrever('escala_fiscal');

const ROTULO_SITUACAO = {
    escalado: 'Escalado', outra_garagem: 'Outra garagem', descoberto: 'Descoberto', direto: 'Direto',
};
const ROTULO_AUSENCIA = { ferias: 'Férias', atestado: 'Atestado', afastado: 'Afastado' };
const ROTULO_FOLGA = { sabado: 'Sábado', domingo: 'Domingo' };
const ROTULO_TURNO = { manha: 'Manhã', tarde: 'Tarde' };

document.addEventListener('DOMContentLoaded', () => {
    setupHeader();
    setupAbas();
    document.querySelectorAll('.ef-re-campo input').forEach(ligarAutocompleteRe);
    if (!escreve) {
        document.querySelectorAll('.ef-form, .ef-rodape').forEach(el => { el.hidden = true; });
    }
    setupQuadro();
    setupCoordenadores();
    setupPontos();
    setupPostos();
    setupModelos();
    setupAusencias();
    setupTrocas();
    setupImportacao();
    iniciarMontagem({ escreve, mostrarMensagem, erro });
    abrirAba('montar');
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

// ─── Sub-abas ────────────────────────────────────────────────────────────

const CARREGAR_ABA = {
    montar: () => carregarMontagem(),
    quadro: () => carregarQuadro(),
    coordenadores: () => { carregarCoordPeriodo(); carregarCoordHorario(); },
    pontos: () => carregarPontos(),
    postos: () => { carregarPontos(); carregarPostos(); },
    modelos: () => carregarModelos(),
    ausencias: () => carregarAusencias(),
    trocas: () => carregarTrocas(),
    importar: () => {},
};

function setupAbas() {
    document.querySelectorAll('.ef-abas [data-aba]').forEach(btn => {
        btn.addEventListener('click', () => abrirAba(btn.dataset.aba));
    });
}

function abrirAba(aba) {
    document.querySelectorAll('.ef-abas [data-aba]').forEach(btn => {
        const ativa = btn.dataset.aba === aba;
        btn.classList.toggle('active', ativa);
        btn.setAttribute('aria-selected', ativa ? 'true' : 'false');
    });
    document.querySelectorAll('.ef-painel').forEach(p => { p.hidden = p.dataset.painel !== aba; });
    esconderMensagem();
    CARREGAR_ABA[aba]?.();
}

// ─── Mensagens e utilitários ─────────────────────────────────────────────

function mostrarMensagem(texto, tipo = 'ok') {
    const el = document.getElementById('ef-mensagem');
    el.textContent = texto;
    el.className = `ef-mensagem ef-mensagem-${tipo}`;
    el.hidden = false;
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function esconderMensagem() {
    document.getElementById('ef-mensagem').hidden = true;
}

function erro(err) {
    if (err instanceof ApiError && err.status === 401) return;
    const detalhes = err?.body?.detalhes?.map(d => `${d.campo}: ${d.mensagem}`).join(' · ');
    mostrarMensagem(detalhes ? `${err.message} — ${detalhes}` : (err.message || 'Erro inesperado'), 'erro');
}

// D-A: "RE — nome" quando há cadastro; senão só o RE, com a marca.
function fiscalTexto(p) {
    return p.nome
        ? `<strong>${escapeHtml(p.re)}</strong> — ${escapeHtml(p.nome)}`
        : `<strong>${escapeHtml(p.re)}</strong> <span class="ef-tag ef-tag-alerta">sem cadastro</span>`;
}

function vazio(texto) {
    return `<div class="patio-loading">${escapeHtml(texto)}</div>`;
}

function hora(h) {
    return h ? h.slice(0, 5) : '—';
}

function dataBR(iso) {
    if (!iso) return '—';
    const [a, m, d] = iso.split('-');
    return `${d}/${m}/${a}`;
}

function valor(id) {
    return document.getElementById(id).value.trim();
}

function botoesAcao(botoes) {
    if (!escreve) return '';
    return `<div class="ef-acoes">${botoes.map(b =>
        `<button type="button" class="btn ${b.classe || 'btn-ghost'} ef-btn-mini" data-acao="${b.acao}" data-id="${escapeHtml(b.id)}">${escapeHtml(b.texto)}</button>`
    ).join('')}</div>`;
}

function ligarAcoes(container, mapa) {
    container.querySelectorAll('[data-acao]').forEach(btn => {
        const fn = mapa[btn.dataset.acao];
        if (fn) btn.addEventListener('click', () => fn(btn.dataset.id, btn));
    });
}

async function confirmarEApagar(texto, rota, recarregar) {
    if (!window.confirm(texto)) return;
    try {
        await apiDelete(rota);
        mostrarMensagem('Apagado.');
        recarregar();
    } catch (err) { erro(err); }
}

// Próximo sábado (ou hoje, se já for sábado) — datas locais, sem UTC.
function proximoSabadoISO() {
    const d = new Date();
    d.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7));
    return dataLocalISO(d);
}

// ─── Autocomplete de RE (mostra RE e nome) ───────────────────────────────

function ligarAutocompleteRe(input) {
    aplicarMascara(input, 're');
    const lista = document.createElement('div');
    lista.className = 'ef-sugestoes';
    lista.hidden = true;
    const nome = document.createElement('div');
    nome.className = 'ef-re-nome';
    input.parentElement.append(lista, nome);

    let timer = null;
    let ultimaBusca = 0;
    input.addEventListener('input', () => {
        nome.textContent = '';
        clearTimeout(timer);
        const q = input.value.trim();
        if (q.length < 2) { lista.hidden = true; return; }
        timer = setTimeout(async () => {
            const minhaBusca = ++ultimaBusca;
            try {
                const itens = await apiGet(`${API}/pessoas?q=${encodeURIComponent(q)}`);
                if (minhaBusca !== ultimaBusca) return; // resposta velha
                const exato = itens.find(i => i.re === q.toUpperCase());
                if (exato) nome.textContent = exato.nome;
                lista.innerHTML = itens.length
                    ? itens.map(i => `<button type="button" data-re="${escapeHtml(i.re)}" data-nome="${escapeHtml(i.nome)}"><strong>${escapeHtml(i.re)}</strong> — ${escapeHtml(i.nome)}</button>`).join('')
                    : `<div class="ef-sugestao-vazia">${input.dataset.exigeCadastro !== undefined
                        ? 'Nenhum RE encontrado. Cadastre em Pessoas antes.'
                        : 'RE sem cadastro: entra na escala só com o RE; o nome aparece quando cadastrar em Pessoas.'}</div>`;
                lista.hidden = false;
            } catch (err) { erro(err); }
        }, 250);
    });
    lista.addEventListener('mousedown', (e) => e.preventDefault()); // não perde o foco antes do click
    lista.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-re]');
        if (!btn) return;
        input.value = btn.dataset.re;
        nome.textContent = btn.dataset.nome;
        lista.hidden = true;
    });
    input.addEventListener('blur', () => { setTimeout(() => { lista.hidden = true; }, 150); });
}

function limparRe(input) {
    input.value = '';
    const nome = input.parentElement.querySelector('.ef-re-nome');
    if (nome) nome.textContent = '';
}

// ─── Fiscais do quadro ───────────────────────────────────────────────────

function setupQuadro() {
    document.getElementById('form-quadro').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            const r = await apiPost(`${API}/quadro`, {
                re: valor('quadro-re'),
                periodo: Number(valor('quadro-periodo')),
                folga_base: valor('quadro-folga') || null,
            });
            mostrarMensagem(r.nome ? `${r.re} — ${r.nome} entrou no quadro.` : `${r.re} entrou no quadro (sem cadastro em Pessoas ainda).`);
            limparRe(document.getElementById('quadro-re'));
            carregarQuadro();
        } catch (err) { erro(err); }
    });
}

async function carregarQuadro() {
    const container = document.getElementById('lista-quadro');
    try {
        const itens = await apiGet(`${API}/quadro`);
        if (!itens.length) { container.innerHTML = vazio('Nenhum fiscal no quadro ainda.'); return; }
        container.innerHTML = [1, 2].map(periodo => {
            const doPeriodo = itens.filter(i => i.periodo === periodo);
            return `<h3 class="ef-subtitulo">${periodo}º período · ${doPeriodo.length}</h3>` + doPeriodo.map(i => `
                <div class="remanejo-card ef-card${i.ativo ? '' : ' ef-inativo'}">
                    <div class="ef-card-linha">
                        <span>${fiscalTexto(i)}</span>
                        <span class="remanejo-badge">${escapeHtml(i.folga_base ? `Folga ${ROTULO_FOLGA[i.folga_base]}` : 'Folga a definir')}</span>
                    </div>
                    ${i.ativo ? '' : '<div class="ef-meta">Inativo</div>'}
                    ${escreve ? `
                    <div class="ef-acoes">
                        <select class="form-select ef-mini" data-campo="folga_base" data-id="${escapeHtml(i.re)}" aria-label="Folga base">
                            <option value="" ${!i.folga_base ? 'selected' : ''}>Folga a definir</option>
                            <option value="sabado" ${i.folga_base === 'sabado' ? 'selected' : ''}>Folga sábado</option>
                            <option value="domingo" ${i.folga_base === 'domingo' ? 'selected' : ''}>Folga domingo</option>
                        </select>
                        <select class="form-select ef-mini" data-campo="periodo" data-id="${escapeHtml(i.re)}" aria-label="Período">
                            <option value="1" ${i.periodo === 1 ? 'selected' : ''}>1º período</option>
                            <option value="2" ${i.periodo === 2 ? 'selected' : ''}>2º período</option>
                        </select>
                        <button type="button" class="btn btn-ghost ef-btn-mini" data-acao="ativo" data-id="${escapeHtml(i.re)}" data-ativo="${i.ativo}">${i.ativo ? 'Desativar' : 'Reativar'}</button>
                        <button type="button" class="btn btn-danger ef-btn-mini" data-acao="remover" data-id="${escapeHtml(i.re)}">Tirar do quadro</button>
                    </div>` : ''}
                </div>`).join('');
        }).join('');
        container.querySelectorAll('select[data-campo]').forEach(sel => {
            sel.addEventListener('change', async () => {
                const v = sel.value;
                const corpo = { [sel.dataset.campo]: sel.dataset.campo === 'periodo' ? Number(v) : (v || null) };
                try { await apiPatch(`${API}/quadro/${encodeURIComponent(sel.dataset.id)}`, corpo); mostrarMensagem('Quadro atualizado.'); carregarQuadro(); } catch (err) { erro(err); }
            });
        });
        ligarAcoes(container, {
            ativo: async (id, btn) => {
                try { await apiPatch(`${API}/quadro/${encodeURIComponent(id)}`, { ativo: btn.dataset.ativo !== 'true' }); carregarQuadro(); } catch (err) { erro(err); }
            },
            remover: (id) => confirmarEApagar('Tirar este fiscal do quadro? (Desativar mantém o histórico.)', `${API}/quadro/${encodeURIComponent(id)}`, carregarQuadro),
        });
    } catch (err) { container.innerHTML = ''; erro(err); }
}

// ─── Coordenadores ───────────────────────────────────────────────────────

function setupCoordenadores() {
    document.getElementById('form-coord-periodo').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            await apiPost(`${API}/coordenadores/periodos`, { re: valor('coordp-re'), periodo: Number(valor('coordp-periodo')) });
            mostrarMensagem('Coordenador ligado ao período.');
            limparRe(document.getElementById('coordp-re'));
            carregarCoordPeriodo();
        } catch (err) { erro(err); }
    });

    document.getElementById('form-coord-horario').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = valor('coordh-id');
        const corpo = { hora_inicio: valor('coordh-inicio'), hora_fim: valor('coordh-fim') };
        try {
            if (id) {
                await apiPatch(`${API}/coordenadores/horarios/${id}`, corpo);
            } else {
                await apiPost(`${API}/coordenadores/horarios`, { ...corpo, re: valor('coordh-re'), turno: valor('coordh-turno') });
            }
            mostrarMensagem('Horário salvo.');
            sairEdicaoHorario();
            carregarCoordHorario();
        } catch (err) { erro(err); }
    });
    document.getElementById('btn-coordh-cancelar').addEventListener('click', sairEdicaoHorario);
}

function sairEdicaoHorario() {
    document.getElementById('form-coord-horario').reset();
    document.getElementById('coordh-id').value = '';
    document.getElementById('coordh-re').disabled = false;
    document.getElementById('coordh-turno').disabled = false;
    document.getElementById('btn-coordh-cancelar').hidden = true;
    document.getElementById('btn-coordh-salvar').textContent = 'Salvar horário';
    limparRe(document.getElementById('coordh-re'));
}

async function carregarCoordPeriodo() {
    const container = document.getElementById('lista-coord-periodo');
    try {
        const itens = await apiGet(`${API}/coordenadores/periodos`);
        container.innerHTML = itens.length ? itens.map(c => `
            <div class="remanejo-card ef-card">
                <div class="ef-card-linha">
                    <span><strong>${escapeHtml(c.re)}</strong> — ${escapeHtml(c.nome)}</span>
                    <span class="remanejo-badge">${c.periodo}º período</span>
                </div>
                ${botoesAcao([{ acao: 'remover', id: `${c.funcionario_id}/${c.periodo}`, texto: 'Desligar', classe: 'btn-danger' }])}
            </div>`).join('') : vazio('Nenhum coordenador ligado a período.');
        ligarAcoes(container, {
            remover: (id) => confirmarEApagar('Desligar este coordenador do período?', `${API}/coordenadores/periodos/${id}`, carregarCoordPeriodo),
        });
    } catch (err) { erro(err); }
}

async function carregarCoordHorario() {
    const container = document.getElementById('lista-coord-horario');
    try {
        const itens = await apiGet(`${API}/coordenadores/horarios`);
        container.innerHTML = itens.length ? itens.map(h => `
            <div class="remanejo-card ef-card${h.ativo ? '' : ' ef-inativo'}">
                <div class="ef-card-linha">
                    <span><strong>${escapeHtml(h.re)}</strong> — ${escapeHtml(h.nome)}</span>
                    <span class="remanejo-badge">${escapeHtml(ROTULO_TURNO[h.turno] || h.turno)}</span>
                </div>
                <div class="ef-meta">${hora(h.hora_inicio)} às ${hora(h.hora_fim)}${h.passa_meia_noite ? ' <span class="ef-tag">termina no dia seguinte</span>' : ''}</div>
                ${botoesAcao([
                    { acao: 'editar', id: h.id, texto: 'Editar horário' },
                    { acao: 'remover', id: h.id, texto: 'Apagar', classe: 'btn-danger' },
                ])}
            </div>`).join('') : vazio('Nenhum horário de plantão cadastrado.');
        ligarAcoes(container, {
            editar: (id) => {
                const h = itens.find(x => x.id === id);
                document.getElementById('coordh-id').value = h.id;
                document.getElementById('coordh-re').value = h.re;
                document.getElementById('coordh-re').disabled = true;
                document.getElementById('coordh-turno').value = h.turno;
                document.getElementById('coordh-turno').disabled = true;
                document.getElementById('coordh-inicio').value = hora(h.hora_inicio);
                document.getElementById('coordh-fim').value = hora(h.hora_fim);
                document.getElementById('btn-coordh-cancelar').hidden = false;
                document.getElementById('btn-coordh-salvar').textContent = `Salvar horário de ${h.nome}`;
                document.getElementById('coordh-inicio').focus();
            },
            remover: (id) => confirmarEApagar('Apagar este horário de plantão?', `${API}/coordenadores/horarios/${id}`, carregarCoordHorario),
        });
    } catch (err) { erro(err); }
}

// ─── Pontos finais ───────────────────────────────────────────────────────

let pontosCache = [];

function setupPontos() {
    document.getElementById('form-ponto').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            await apiPost(`${API}/pontos-finais`, { nome: valor('ponto-nome') });
            mostrarMensagem('Ponto final cadastrado.');
            document.getElementById('form-ponto').reset();
            carregarPontos();
        } catch (err) { erro(err); }
    });
}

async function carregarPontos() {
    const container = document.getElementById('lista-pontos');
    try {
        pontosCache = await apiGet(`${API}/pontos-finais`);
        const sel = document.getElementById('posto-ponto');
        const atual = sel.value;
        sel.innerHTML = '<option value="">— nenhum —</option>' + pontosCache
            .filter(p => p.ativo || p.id === atual)
            .map(p => `<option value="${p.id}">${escapeHtml(p.nome)}</option>`).join('');
        sel.value = atual;
        container.innerHTML = pontosCache.length ? pontosCache.map(p => `
            <div class="remanejo-card ef-card${p.ativo ? '' : ' ef-inativo'}">
                <div class="ef-card-linha">
                    <span><strong>${escapeHtml(p.nome)}</strong></span>
                    <span class="remanejo-badge">${p.qtd_postos} posto(s)</span>
                </div>
                ${botoesAcao([
                    { acao: 'renomear', id: p.id, texto: 'Renomear' },
                    { acao: 'ativo', id: p.id, texto: p.ativo ? 'Desativar' : 'Reativar' },
                ])}
            </div>`).join('') : vazio('Nenhum ponto final cadastrado.');
        ligarAcoes(container, {
            renomear: async (id) => {
                const p = pontosCache.find(x => x.id === id);
                const nome = window.prompt('Novo nome do ponto final:', p.nome);
                if (!nome || nome.trim() === p.nome) return;
                try { await apiPatch(`${API}/pontos-finais/${id}`, { nome }); carregarPontos(); } catch (err) { erro(err); }
            },
            ativo: async (id) => {
                const p = pontosCache.find(x => x.id === id);
                try { await apiPatch(`${API}/pontos-finais/${id}`, { ativo: !p.ativo }); carregarPontos(); } catch (err) { erro(err); }
            },
        });
    } catch (err) { erro(err); }
}

// ─── Postos ──────────────────────────────────────────────────────────────

let postosCache = [];

function lerLinhas(texto) {
    return texto.split(/[\s,;]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
}

function setupPostos() {
    document.getElementById('form-posto').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = valor('posto-id');
        const corpo = {
            lado: valor('posto-lado'),
            cod_jb: valor('posto-codjb') || null,
            lote: valor('posto-lote') || null,
            ponto_final_id: valor('posto-ponto') || null,
            linhas: lerLinhas(valor('posto-linhas')),
        };
        try {
            if (id) await apiPatch(`${API}/postos/${id}`, corpo);
            else await apiPost(`${API}/postos`, corpo);
            mostrarMensagem(id ? 'Posto alterado.' : 'Posto cadastrado.');
            sairEdicaoPosto();
            carregarPostos();
        } catch (err) { erro(err); }
    });
    document.getElementById('btn-posto-cancelar').addEventListener('click', sairEdicaoPosto);
    document.getElementById('posto-filtro').addEventListener('input', renderPostos);
}

function sairEdicaoPosto() {
    document.getElementById('form-posto').reset();
    document.getElementById('posto-id').value = '';
    document.getElementById('btn-posto-cancelar').hidden = true;
    document.getElementById('btn-posto-salvar').textContent = 'Cadastrar posto';
}

async function carregarPostos() {
    try {
        postosCache = await apiGet(`${API}/postos`);
        renderPostos();
    } catch (err) { erro(err); }
}

function descreverPosto(p) {
    return `${p.lado} · ${p.linhas.join(' / ')}`;
}

function renderPostos() {
    const container = document.getElementById('lista-postos');
    const filtro = valor('posto-filtro').toUpperCase();
    const itens = postosCache.filter(p => !filtro
        || p.linhas.some(l => l.includes(filtro))
        || (p.cod_jb || '').toUpperCase().includes(filtro)
        || (p.ponto_final_nome || '').toUpperCase().includes(filtro));
    container.innerHTML = itens.length ? itens.map(p => `
        <div class="remanejo-card ef-card${p.ativo ? '' : ' ef-inativo'}">
            <div class="ef-card-linha">
                <span><strong>${escapeHtml(descreverPosto(p))}</strong></span>
                <span class="remanejo-badge">${escapeHtml(p.lote || 'sem lote')}</span>
            </div>
            <div class="ef-meta">Cód. JB ${escapeHtml(p.cod_jb || '—')} · Ponto final: ${escapeHtml(p.ponto_final_nome || 'não ligado')}</div>
            ${botoesAcao([
                { acao: 'editar', id: p.id, texto: 'Editar' },
                { acao: 'ativo', id: p.id, texto: p.ativo ? 'Desativar' : 'Reativar' },
            ])}
        </div>`).join('') : vazio(postosCache.length ? 'Nenhum posto com esse filtro.' : 'Nenhum posto cadastrado.');
    ligarAcoes(container, {
        editar: (id) => {
            const p = postosCache.find(x => x.id === id);
            document.getElementById('posto-id').value = p.id;
            document.getElementById('posto-lado').value = p.lado;
            document.getElementById('posto-codjb').value = p.cod_jb || '';
            document.getElementById('posto-lote').value = p.lote || '';
            document.getElementById('posto-ponto').value = p.ponto_final_id || '';
            document.getElementById('posto-linhas').value = p.linhas.join(', ');
            document.getElementById('btn-posto-cancelar').hidden = false;
            document.getElementById('btn-posto-salvar').textContent = 'Salvar posto';
            document.getElementById('form-posto').scrollIntoView({ behavior: 'smooth' });
        },
        ativo: async (id) => {
            const p = postosCache.find(x => x.id === id);
            try { await apiPatch(`${API}/postos/${id}`, { ativo: !p.ativo }); carregarPostos(); } catch (err) { erro(err); }
        },
    });
}

// ─── Modelos ─────────────────────────────────────────────────────────────

function setupModelos() {
    document.getElementById('modelo-sel').addEventListener('change', carregarModeloDetalhe);
    document.getElementById('form-modelo').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            const m = await apiPost(`${API}/modelos`, {
                tipo_dia: valor('modelo-tipo'), paridade: valor('modelo-paridade') || null, nome: valor('modelo-nome'),
            });
            mostrarMensagem(`Modelo "${m.nome}" criado.`);
            document.getElementById('form-modelo').reset();
            await carregarModelos();
            document.getElementById('modelo-sel').value = m.id;
            carregarModeloDetalhe();
        } catch (err) { erro(err); }
    });
}

async function carregarModelos() {
    try {
        const [modelos] = await Promise.all([apiGet(`${API}/modelos`), carregarPostosSilencioso()]);
        const sel = document.getElementById('modelo-sel');
        const atual = sel.value;
        sel.innerHTML = '<option value="">— escolha —</option>' + modelos
            .map(m => `<option value="${m.id}">${escapeHtml(m.nome)} (${m.qtd_postos})</option>`).join('');
        sel.value = atual;
        if (atual) carregarModeloDetalhe();
        else document.getElementById('modelo-detalhe').innerHTML = modelos.length ? '' : vazio('Nenhum modelo ainda. Crie um ou importe a planilha.');
    } catch (err) { erro(err); }
}

async function carregarPostosSilencioso() {
    try { postosCache = await apiGet(`${API}/postos`); } catch { /* aba Postos mostra o erro */ }
}

function opcoesSituacao(atual) {
    return Object.entries(ROTULO_SITUACAO)
        .map(([v, t]) => `<option value="${v}" ${v === atual ? 'selected' : ''}>${t}</option>`).join('');
}

function linhaEditavelModelo(i) {
    const dis = escreve ? '' : 'disabled';
    return `
        <div class="remanejo-card ef-card ef-modelo-item" data-item="${i.id}">
            <div class="ef-card-linha">
                <span><strong>#${i.ordem} · ${escapeHtml(descreverPosto(i))}</strong></span>
                <span class="remanejo-badge">${i.periodo}º período</span>
            </div>
            <div class="ef-meta">Cód. JB ${escapeHtml(i.cod_jb || '—')} · Lote ${escapeHtml(i.lote || '—')}</div>
            <div class="ef-grade ef-grade-compacta">
                <label class="ef-campo-mini">Início<input type="time" class="form-input" data-campo="hora_inicio" value="${i.hora_inicio ? hora(i.hora_inicio) : ''}" ${dis}></label>
                <label class="ef-campo-mini">Término<input type="time" class="form-input" data-campo="hora_termino" value="${i.hora_termino ? hora(i.hora_termino) : ''}" ${dis}></label>
                <label class="ef-campo-mini">Situação<select class="form-select" data-campo="situacao_padrao" ${dis}>${opcoesSituacao(i.situacao_padrao)}</select></label>
                <label class="ef-campo-mini">RE padrão<input type="text" class="form-input" data-campo="re_padrao" value="${escapeHtml(i.re_padrao || '')}" inputmode="numeric" maxlength="20" ${dis}></label>
                <label class="ef-campo-mini">Garagem<input type="text" class="form-input" data-campo="outra_garagem" value="${escapeHtml(i.outra_garagem || '')}" maxlength="3" placeholder="G1" ${dis}></label>
                <label class="ef-campo-mini">Texto na escala<input type="text" class="form-input" data-campo="marcador" value="${escapeHtml(i.marcador || '')}" maxlength="10" placeholder="em branco" ${dis}></label>
            </div>
            <div class="ef-meta">${i.re_padrao
                ? (i.nome_padrao ? `Fiscal padrão: ${escapeHtml(i.nome_padrao)}` : '<span class="ef-tag ef-tag-alerta">RE sem cadastro</span>')
                : `Na escala aparece: <strong>${escapeHtml(i.marcador || '(em branco)')}</strong>`}</div>
            ${botoesAcao([
                { acao: 'salvar', id: i.id, texto: 'Salvar', classe: 'btn-primary' },
                { acao: 'remover', id: i.id, texto: 'Tirar do modelo', classe: 'btn-danger' },
            ])}
        </div>`;
}

async function carregarModeloDetalhe() {
    const container = document.getElementById('modelo-detalhe');
    const modeloId = document.getElementById('modelo-sel').value;
    if (!modeloId) { container.innerHTML = ''; return; }
    try {
        const m = await apiGet(`${API}/modelos/${modeloId}`);
        const formAdicionar = escreve ? `
            <details class="ef-detalhes">
                <summary>Pôr um posto neste modelo</summary>
                <form class="ef-form" id="form-modelo-posto" autocomplete="off">
                    <div class="ef-grade">
                        <div class="form-group"><label class="form-label" for="mp-posto">Posto *</label>
                            <select class="form-select" id="mp-posto" required>${postosCache.filter(p => p.ativo)
                                .map(p => `<option value="${p.id}">${escapeHtml(descreverPosto(p))}</option>`).join('')}</select></div>
                        <div class="form-group"><label class="form-label" for="mp-ordem">Ordem *</label>
                            <input type="number" class="form-input" id="mp-ordem" min="1" value="${m.postos.length + 1}" required></div>
                        <div class="form-group"><label class="form-label" for="mp-periodo">Período *</label>
                            <select class="form-select" id="mp-periodo"><option value="1">1º</option><option value="2">2º</option></select></div>
                        <div class="form-group"><label class="form-label" for="mp-inicio">Início</label>
                            <input type="time" class="form-input" id="mp-inicio"></div>
                        <div class="form-group"><label class="form-label" for="mp-termino">Término</label>
                            <input type="time" class="form-input" id="mp-termino"></div>
                        <div class="form-group"><label class="form-label" for="mp-situacao">Situação *</label>
                            <select class="form-select" id="mp-situacao">${opcoesSituacao('escalado')}</select></div>
                        <div class="form-group"><label class="form-label" for="mp-re">RE padrão</label>
                            <input type="text" class="form-input" id="mp-re" inputmode="numeric" maxlength="20"></div>
                        <div class="form-group"><label class="form-label" for="mp-garagem">Garagem</label>
                            <input type="text" class="form-input" id="mp-garagem" maxlength="3" placeholder="G1"></div>
                    </div>
                    <div class="ef-rodape"><button type="submit" class="btn btn-primary btn-full">Pôr no modelo</button></div>
                </form>
            </details>` : '';
        container.innerHTML = formAdicionar + (m.postos.length
            ? m.postos.map(linhaEditavelModelo).join('')
            : vazio('Modelo sem postos.'));

        const form = document.getElementById('form-modelo-posto');
        if (form) {
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                try {
                    await apiPost(`${API}/modelos/${modeloId}/postos`, {
                        posto_id: valor('mp-posto'), ordem: Number(valor('mp-ordem')), periodo: Number(valor('mp-periodo')),
                        hora_inicio: valor('mp-inicio') || null, hora_termino: valor('mp-termino') || null,
                        situacao_padrao: valor('mp-situacao'), re_padrao: valor('mp-re') || null,
                        outra_garagem: valor('mp-garagem') || null,
                    });
                    mostrarMensagem('Posto incluído no modelo.');
                    carregarModeloDetalhe();
                } catch (err) { erro(err); }
            });
        }

        ligarAcoes(container, {
            salvar: async (id) => {
                const card = container.querySelector(`[data-item="${id}"]`);
                const ler = (c) => card.querySelector(`[data-campo="${c}"]`).value.trim();
                const situacao = ler('situacao_padrao');
                try {
                    await apiPatch(`${API}/modelos/${modeloId}/postos/${id}`, {
                        hora_inicio: ler('hora_inicio') || null,
                        hora_termino: ler('hora_termino') || null,
                        situacao_padrao: situacao,
                        // Fiscal só vale para "escalado"; garagem só para "outra garagem".
                        re_padrao: situacao === 'escalado' ? (ler('re_padrao') || null) : null,
                        outra_garagem: situacao === 'outra_garagem' ? (ler('outra_garagem') || null) : null,
                        // D-B: vazio = posto em branco. Escalado não tem marcador.
                        marcador: situacao === 'escalado' ? null : ler('marcador'),
                    });
                    mostrarMensagem('Posto do modelo salvo.');
                    carregarModeloDetalhe();
                } catch (err) { erro(err); }
            },
            remover: (id) => confirmarEApagar('Tirar este posto do modelo?', `${API}/modelos/${modeloId}/postos/${id}`, carregarModeloDetalhe),
        });
    } catch (err) { erro(err); }
}

// ─── Ausências ───────────────────────────────────────────────────────────

function setupAusencias() {
    document.getElementById('aus-inicio').value = dataLocalISO();
    document.getElementById('aus-dia').value = dataLocalISO();
    document.getElementById('aus-dia').addEventListener('change', carregarAusencias);
    document.getElementById('form-ausencia').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            await apiPost(`${API}/ausencias`, {
                re: valor('aus-re'), tipo: valor('aus-tipo'), data_inicio: valor('aus-inicio'),
                data_fim: valor('aus-fim') || null, observacao: valor('aus-obs') || null,
            });
            mostrarMensagem('Ausência lançada.');
            limparRe(document.getElementById('aus-re'));
            document.getElementById('aus-fim').value = '';
            document.getElementById('aus-obs').value = '';
            carregarAusencias();
        } catch (err) { erro(err); }
    });
}

async function carregarAusencias() {
    const container = document.getElementById('lista-ausencias');
    const dia = valor('aus-dia');
    try {
        const itens = await apiGet(`${API}/ausencias${dia ? `?data=${dia}` : ''}`);
        container.innerHTML = itens.length ? itens.map(a => `
            <div class="remanejo-card ef-card">
                <div class="ef-card-linha">
                    <span>${fiscalTexto(a)}</span>
                    <span class="remanejo-badge">${escapeHtml(ROTULO_AUSENCIA[a.tipo] || a.tipo)}</span>
                </div>
                <div class="ef-meta">${dataBR(a.data_inicio)} até ${a.data_fim ? `${dataBR(a.data_fim)} (inclusive)` : 'sem previsão'}${a.observacao ? ` · ${escapeHtml(a.observacao)}` : ''}</div>
                ${botoesAcao([{ acao: 'remover', id: a.id, texto: 'Apagar', classe: 'btn-danger' }])}
            </div>`).join('') : vazio(dia ? `Ninguém fora em ${dataBR(dia)}.` : 'Nenhuma ausência vigente.');
        ligarAcoes(container, {
            remover: (id) => confirmarEApagar('Apagar esta ausência?', `${API}/ausencias/${id}`, carregarAusencias),
        });
    } catch (err) { erro(err); }
}

// ─── Trocas ──────────────────────────────────────────────────────────────

function setupTrocas() {
    document.getElementById('troca-sabado').value = proximoSabadoISO();
    document.getElementById('form-troca').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            await apiPost(`${API}/trocas`, {
                tipo: valor('troca-tipo'), re_a: valor('troca-re-a'), re_b: valor('troca-re-b'),
                data_sabado: valor('troca-sabado'), observacao: valor('troca-obs') || null,
            });
            mostrarMensagem('Troca lançada.');
            limparRe(document.getElementById('troca-re-a'));
            limparRe(document.getElementById('troca-re-b'));
            document.getElementById('troca-obs').value = '';
            carregarTrocas();
        } catch (err) { erro(err); }
    });
}

async function carregarTrocas() {
    const container = document.getElementById('lista-trocas');
    try {
        const itens = await apiGet(`${API}/trocas`);
        container.innerHTML = itens.length ? itens.map(t => `
            <div class="remanejo-card ef-card">
                <div class="ef-card-linha">
                    <span>${fiscalTexto(t.a)} ⇄ ${fiscalTexto(t.b)}</span>
                    <span class="remanejo-badge">${escapeHtml(t.tipo)}</span>
                </div>
                <div class="ef-meta">Fim de semana do sábado ${dataBR(t.data_sabado)}${t.observacao ? ` · ${escapeHtml(t.observacao)}` : ''}</div>
                ${botoesAcao([{ acao: 'remover', id: t.id, texto: 'Apagar', classe: 'btn-danger' }])}
            </div>`).join('') : vazio('Nenhuma troca daqui pra frente.');
        ligarAcoes(container, {
            remover: (id) => confirmarEApagar('Apagar esta troca?', `${API}/trocas/${id}`, carregarTrocas),
        });
    } catch (err) { erro(err); }
}

// ─── Importar planilha ───────────────────────────────────────────────────

const imp = { dados: null, relatorio: null, alterado: false };

const ROTULO_PROBLEMA = {
    horario_invalido: 'Horário inválido',
    termino_antes_inicio: 'Término antes do início',
    g3_no_re: 'G3 no lugar do RE',
    texto_rodape: 'Texto do rodapé dentro do posto',
    re_desconhecido: 'RE não reconhecido',
    posto_sem_linha: 'Posto sem linha',
    lado_invalido: 'Ponta inválida',
    modelo_ja_importado: 'Modelo já importado',
    aba_repetida: 'Aba repetida',
    arquivo_invalido: 'Arquivo inválido',
    lote_normalizado: 'Lote corrigido para E2/AR2',
    lote_invalido: 'Valor que não é lote',
    em_branco: 'Posto em branco (sem RE)',
    ajustado_padrao: 'AJUSTADO PARA O PADRÃO',
    ponta_anotada: 'TS/TP anotado no campo de linhas',
    posto_no_rodape: 'Posto que ficou no rodapé',
    posto_divergente: 'Mesmo posto com cód. JB/lote diferente',
    lado_divergente: 'Coluna L diferente do bloco',
    campo_ausente: 'Campo ausente no arquivo',
};

function setupImportacao() {
    const inputArquivo = document.getElementById('imp-arquivo');
    inputArquivo.addEventListener('change', async () => {
        const arquivo = inputArquivo.files?.[0];
        imp.dados = null;
        imp.relatorio = null;
        document.getElementById('imp-relatorio').innerHTML = '';
        atualizarBotoesImportacao();
        if (!arquivo) return;
        try {
            imp.dados = JSON.parse(await arquivo.text());
            imp.alterado = false;
            mostrarMensagem('Arquivo carregado. Clique em Simular — nada é gravado.');
        } catch {
            mostrarMensagem('O arquivo não é um JSON válido.', 'erro');
        }
        atualizarBotoesImportacao();
    });
    document.getElementById('btn-imp-simular').addEventListener('click', simular);
    document.getElementById('btn-imp-confirmar').addEventListener('click', confirmarImportacao);
}

function atualizarBotoesImportacao() {
    document.getElementById('btn-imp-simular').disabled = !imp.dados || !escreve;
    const confirmar = document.getElementById('btn-imp-confirmar');
    confirmar.disabled = !escreve || !imp.relatorio?.pode_confirmar || imp.alterado;
    confirmar.textContent = imp.alterado ? 'Simule de novo antes de confirmar' : 'Confirmar importação';
}

function formDoJson() {
    const fd = new FormData();
    fd.append('arquivo', new Blob([JSON.stringify(imp.dados)], { type: 'application/json' }), 'escala.json');
    return fd;
}

async function simular() {
    const btn = document.getElementById('btn-imp-simular');
    btn.disabled = true;
    try {
        imp.relatorio = await apiUpload(`${API}/importacao/simular`, formDoJson());
        imp.alterado = false;
        renderRelatorio();
        mostrarMensagem(imp.relatorio.pode_confirmar
            ? 'Simulação sem problema que impede. Nada foi gravado ainda — confira e confirme.'
            : `Simulação com ${imp.relatorio.resumo.problemas_bloqueantes} problema(s) que impedem. Nada foi gravado.`,
            imp.relatorio.pode_confirmar ? 'ok' : 'alerta');
    } catch (err) { erro(err); }
    atualizarBotoesImportacao();
}

async function confirmarImportacao() {
    const r = imp.relatorio?.resumo;
    if (!r || !window.confirm(`Gravar ${r.modelos} modelo(s), ${r.postos_novos} posto(s) novo(s) e ${r.coberturas} linha(s) de modelo?`)) return;
    const btn = document.getElementById('btn-imp-confirmar');
    btn.disabled = true;
    try {
        const res = await apiUpload(`${API}/importacao/confirmar`, formDoJson());
        mostrarMensagem(`Importado: ${res.modelos} modelo(s), ${res.postos_criados} posto(s) criado(s), ${res.coberturas_gravadas} linha(s) de modelo. Pontos finais e folga base NÃO foram gravados — faça pela tela.`);
        imp.relatorio = null;
        document.getElementById('imp-relatorio').innerHTML = '';
    } catch (err) { erro(err); }
    atualizarBotoesImportacao();
}

function marcarAlterado() {
    imp.alterado = true;
    atualizarBotoesImportacao();
}

function campoEditavel(p, indiceProblema) {
    if (!p.modelo || p.indice === undefined || p.indice === null) return '';
    const linha = imp.dados?.[p.modelo]?.postos?.[p.indice];
    if (!linha) return '';
    const partes = [];
    if (p.campo && (p.acoes || []).includes('editar')) {
        const atual = linha[p.campo] ?? '';
        partes.push(`<label class="ef-campo-mini">Corrigir ${escapeHtml(p.campo)}
            <input type="text" class="form-input" data-prob="${indiceProblema}" data-tipo-edit="campo" value="${escapeHtml(String(atual))}"></label>`);
        if (p.sugestoes?.length) {
            partes.push(`<span class="ef-meta">Sugestões: ${p.sugestoes.map(s => `<button type="button" class="btn btn-ghost ef-btn-mini" data-prob="${indiceProblema}" data-sugestao="${escapeHtml(s)}">${escapeHtml(s)}</button>`).join(' ')}</span>`);
        }
    }
    if ((p.acoes || []).includes('descartar')) {
        partes.push(`<label class="ef-check"><input type="checkbox" data-prob="${indiceProblema}" data-tipo-edit="descartar" ${linha._descartar ? 'checked' : ''}> Descartar esta linha (não é posto)</label>`);
    }
    return partes.length ? `<div class="ef-edicao">${partes.join('')}</div>` : '';
}

function cartaoProblema(p, i) {
    const onde = [p.modelo, p.bloco, p.linha_planilha ? `linha ${p.linha_planilha}` : null].filter(Boolean).join(' · ');
    return `
        <div class="remanejo-card ef-card ${p.bloqueia ? 'ef-bloqueia' : 'ef-aviso'}">
            <div class="ef-card-linha">
                <span><strong>${escapeHtml(ROTULO_PROBLEMA[p.tipo] || p.tipo)}</strong></span>
                <span class="remanejo-badge">${escapeHtml(onde || '—')}</span>
            </div>
            <div class="ef-meta">${escapeHtml(p.mensagem)}</div>
            ${campoEditavel(p, i)}
        </div>`;
}

function renderRelatorio() {
    const r = imp.relatorio;
    const container = document.getElementById('imp-relatorio');
    const s = r.resumo;
    const problemas = r.problemas.map((p, i) => ({ p, i }));
    const bloqueantes = problemas.filter(x => x.p.bloqueia);
    const avisos = problemas.filter(x => !x.p.bloqueia);
    const avisosPorTipo = {};
    avisos.forEach(x => { (avisosPorTipo[x.p.tipo] ||= []).push(x); });

    const resumo = `
        <div class="ef-resumo">
            <div><strong>${s.modelos}</strong> modelos</div>
            <div><strong>${s.postos_distintos}</strong> postos (${s.postos_novos} novos)</div>
            <div><strong>${s.coberturas}</strong> linhas de modelo</div>
            <div><strong>${s.escalado}</strong> com RE (<strong>${s.escalado_com_cadastro}</strong> com cadastro · <strong>${s.escalado_sem_cadastro}</strong> sem)</div>
            <div><strong>${s.outra_garagem}</strong> outra garagem · <strong>${s.descoberto}</strong> descoberto (${s.em_branco} em branco) · <strong>${s.direto}</strong> direto</div>
            <div><strong>${s.ajustados_para_o_padrao}</strong> horário(s) ajustado(s) para o padrão</div>
            <div class="${s.problemas_bloqueantes ? 'ef-texto-erro' : 'ef-texto-ok'}"><strong>${s.problemas_bloqueantes}</strong> problema(s) que impedem · <strong>${s.avisos}</strong> aviso(s)</div>
        </div>
        <div class="ef-meta">Modelos: ${r.modelos.map(m => `${escapeHtml(m.nome)} (${m.coberturas})`).join(' · ')}${r.abas_ignoradas.length ? ` · Abas ignoradas: ${escapeHtml(r.abas_ignoradas.join(', '))}` : ''}</div>`;

    const blocoBloqueantes = bloqueantes.length
        ? `<h3 class="ef-subtitulo">Impedem a importação (${bloqueantes.length}) — corrija e simule de novo</h3>${bloqueantes.map(x => cartaoProblema(x.p, x.i)).join('')}`
        : '';

    const blocoAvisos = Object.entries(avisosPorTipo).map(([tipo, lista]) => `
        <details class="ef-detalhes">
            <summary>${escapeHtml(ROTULO_PROBLEMA[tipo] || tipo)} (${lista.length})</summary>
            ${lista.map(x => cartaoProblema(x.p, x.i)).join('')}
        </details>`).join('');

    const blocoCadastrar = r.re_sem_cadastro.length ? `
        <details class="ef-detalhes">
            <summary>RE sem cadastro em Pessoas (${r.re_sem_cadastro.length}) — entram assim mesmo</summary>
            <p class="ef-ajuda">A escala guarda o RE. Quando a pessoa for cadastrada em Pessoas, o nome aparece sozinho — não precisa importar de novo.</p>
            <div class="ef-lista-re">${r.re_sem_cadastro.map(c => `<span title="${escapeHtml(c.onde.map(o => `${o.modelo} ${o.bloco} linha ${o.linha_planilha} ${o.periodo}º`).join('; '))}">${escapeHtml(c.re)}</span>`).join('')}</div>
        </details>` : '';

    const blocoFolga = r.sugestao_folga.length ? `
        <details class="ef-detalhes">
            <summary>Sugestão de folga base — a confirmar (${r.sugestao_folga.length})</summary>
            <p class="ef-ajuda">Tirada das listas de folga do SÁBADO ÍMPAR e do DOMINGO ÍMPAR. Nada disso é gravado pela importação: confirme uma a uma.</p>
            ${r.sugestao_folga.map((f, i) => `
                <div class="remanejo-card ef-card">
                    <div class="ef-card-linha">
                        <span>${fiscalTexto(f)}</span>
                        <span class="remanejo-badge">${f.folga_sugerida ? `Folga ${ROTULO_FOLGA[f.folga_sugerida]}` : 'Sem sugestão'}${f.periodo_sugerido ? ` · ${f.periodo_sugerido}º` : ''}</span>
                    </div>
                    ${f.observacao ? `<div class="ef-meta">${escapeHtml(f.observacao)}</div>` : ''}
                    ${escreve && f.folga_sugerida && f.periodo_sugerido
                        ? `<div class="ef-acoes"><button type="button" class="btn btn-ghost ef-btn-mini" data-folga="${i}">Confirmar no quadro</button></div>` : ''}
                </div>`).join('')}
        </details>` : '';

    const blocoTrocas = r.trocas_encontradas.length ? `
        <details class="ef-detalhes">
            <summary>Trocas escritas no rodapé (${r.trocas_encontradas.length}) — só informação</summary>
            ${r.trocas_encontradas.map(t => `<div class="ef-meta">${escapeHtml(t.modelo)}: ${escapeHtml(t.tipo)} ${escapeHtml(t.re_a)} ⇄ ${escapeHtml(t.re_b)}</div>`).join('')}
        </details>` : '';

    const blocoDescartadas = r.linhas_descartadas.length ? `
        <div class="ef-meta">Linhas descartadas: ${r.linhas_descartadas.map(d => `${escapeHtml(d.modelo)} ${escapeHtml(d.bloco || '')} linha ${d.linha_planilha}`).join(' · ')}</div>` : '';

    container.innerHTML = resumo + blocoBloqueantes + blocoCadastrar + blocoAvisos + blocoFolga + blocoTrocas + blocoDescartadas;
    ligarEdicaoRelatorio(container);
}

function ligarEdicaoRelatorio(container) {
    const r = imp.relatorio;
    container.querySelectorAll('[data-tipo-edit="campo"]').forEach(input => {
        input.addEventListener('change', () => {
            const p = r.problemas[Number(input.dataset.prob)];
            const texto = input.value.trim();
            imp.dados[p.modelo].postos[p.indice][p.campo] = texto === '' ? null : texto;
            marcarAlterado();
        });
    });
    container.querySelectorAll('[data-sugestao]').forEach(btn => {
        btn.addEventListener('click', () => {
            const p = r.problemas[Number(btn.dataset.prob)];
            const input = container.querySelector(`[data-prob="${btn.dataset.prob}"][data-tipo-edit="campo"]`);
            // Troca só o pedaço errado, sem mexer no resto do lote ("A2 / E2").
            const partes = String(p.valor ?? '').split('/').map(x => x.trim());
            const errado = p.mensagem.match(/Lote "([^"]+)"/)?.[1];
            const novo = partes.map(x => (x === errado ? btn.dataset.sugestao : x)).join(' / ');
            input.value = novo;
            imp.dados[p.modelo].postos[p.indice][p.campo] = novo;
            marcarAlterado();
        });
    });
    container.querySelectorAll('[data-tipo-edit="descartar"]').forEach(chk => {
        chk.addEventListener('change', () => {
            const p = r.problemas[Number(chk.dataset.prob)];
            const linha = imp.dados[p.modelo].postos[p.indice];
            if (chk.checked) linha._descartar = true;
            else delete linha._descartar;
            marcarAlterado();
        });
    });
    container.querySelectorAll('[data-folga]').forEach(btn => {
        btn.addEventListener('click', () => confirmarFolga(r.sugestao_folga[Number(btn.dataset.folga)], btn));
    });
}

async function confirmarFolga(f, btn) {
    const texto = `Confirmar ${f.re}${f.nome ? ` (${f.nome})` : ''} no ${f.periodo_sugerido}º período com folga base ${ROTULO_FOLGA[f.folga_sugerida]}?`;
    if (!window.confirm(texto)) return;
    btn.disabled = true;
    try {
        const quadro = await apiGet(`${API}/quadro`);
        const existente = quadro.find(q => q.re === f.re);
        if (existente) {
            await apiPatch(`${API}/quadro/${encodeURIComponent(existente.re)}`, { periodo: f.periodo_sugerido, folga_base: f.folga_sugerida });
        } else {
            await apiPost(`${API}/quadro`, { re: f.re, periodo: f.periodo_sugerido, folga_base: f.folga_sugerida });
        }
        btn.textContent = 'Confirmado ✓';
    } catch (err) { btn.disabled = false; erro(err); }
}
