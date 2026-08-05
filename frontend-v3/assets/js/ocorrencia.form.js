/*
 * ocorrencia.form.js — Formulário de Registro de Ocorrências
 * -----------------------------------------------------------------------------
 * Reproduz as 4 páginas do papel (Capa → Veículos → Análise → Testemunhas)
 * como abas de um único formulário. Rascunho automático: assim que os 4
 * campos mínimos da capa (tipo, data, hora, prefixo) existem, a primeira
 * interação já cria a ocorrência como RASCUNHO; toda mudança depois disso
 * dispara um PATCH debounced — o coordenador nunca precisa lembrar de salvar.
 *
 * Leitura de campos é genérica via atributo `name` (ver lerValorCampo /
 * preencherValorCampo) — com ~150 campos no papel, ligar cada input à mão
 * seria a maior fonte de bugs deste arquivo.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, apiPatch, apiDelete, ApiError } from './api.js';
import { API_BASE_URL, TOKEN_KEY } from './config.js';
import { podeEscrever, podeLer, usuario, temFuncao } from './sessao.js';
import { ANALISE_GRUPOS, REGIOES_AVARIA, TIPOS_ANEXO, DANOS_VEICULO } from './ocorrencia.vocabulario.js';
import { imprimirOcorrencia } from './ocorrencia.imprimir.js';
import { abrirMensagemSinistro } from './ocorrencia.sinistro.js';
import { confirmarExclusaoOcorrencia } from './ocorrencia.excluir.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}
if (!podeLer('ocorrencia')) {
    window.location.replace('modulos.html');
    throw new Error('Sem acesso de leitura ao recurso ocorrencia');
}

// `let`, não `const` — vira true também quando a ocorrência já existe e
// não é desta pessoa (nem ADMIN). Ver podeAgirNestaOcorrencia() e o boot,
// que resolve isso ANTES de montar os campos e ligar o autosave.
let somenteLeitura = !podeEscrever('ocorrencia');

const params = new URLSearchParams(window.location.search);
let ocorrenciaId = params.get('id');

if (somenteLeitura && !ocorrenciaId) {
    // Quem só lê não cria ocorrência nova — não há o que ver numa capa vazia.
    window.location.replace('ocorrencias.html');
    throw new Error('Sem acesso de escrita — nada a exibir sem um id de ocorrência');
}

// Containers cujos campos [name] NÃO fazem parte da leitura genérica da capa
// (são lidos/escritos por rotinas próprias: análise e as 4 listas dinâmicas).
const SELETOR_EXCLUIR_TOPO =
    '#lista-veiculos, #lista-testemunhas, #lista-vitimas, #lista-autoridades, ' +
    '#grid-analise-tipo, #grid-analise-via, #grid-analise-sinalizacao, #grid-analise-local';
const SELETOR_ANALISE =
    '#grid-analise-tipo [name], #grid-analise-via [name], #grid-analise-sinalizacao [name], #grid-analise-local [name]';

// ─── Estado ────────────────────────────────────────────────────────────────
let catalogoTipos = [];
let catalogoOrgaos = [];
let dadosAtuais = null;
let autosaveTimer = null;

let veiculosTerceiro = [];
let vitimas = [];
let testemunhas = [];
let autoridades = [];

// ─── Helpers genéricos de campo ─────────────────────────────────────────────

function lerValorCampo(el) {
    if (el.type === 'checkbox') return el.checked;
    const tipo = el.dataset.tipo;
    if (tipo === 'bool3') return el.value === '' ? null : el.value === 'true';
    if (tipo === 'bool3-strict') return el.value === 'true';
    if (el.type === 'number') return el.value === '' ? null : Number(el.value);
    return el.value === '' ? null : el.value;
}

function preencherValorCampo(el, valor) {
    if (el.type === 'checkbox') { el.checked = Boolean(valor); return; }
    const tipo = el.dataset.tipo;
    if (tipo === 'bool3' || tipo === 'bool3-strict') {
        el.value = valor === null || valor === undefined ? '' : String(valor);
        return;
    }
    el.value = valor ?? '';
}

function lerCapaEFechamento() {
    const dados = {};
    document.querySelectorAll('main [name]').forEach(el => {
        if (el.closest(SELETOR_EXCLUIR_TOPO)) return;
        dados[el.name] = lerValorCampo(el);
    });
    return dados;
}

function preencherCapaEFechamento(dados) {
    document.querySelectorAll('main [name]').forEach(el => {
        if (el.closest(SELETOR_EXCLUIR_TOPO)) return;
        preencherValorCampo(el, dados[el.name]);
    });
}

function lerAnalise() {
    const dados = {};
    document.querySelectorAll(SELETOR_ANALISE).forEach(el => { dados[el.name] = lerValorCampo(el); });
    return dados;
}

function algumaAnaliseTemDado() {
    return Object.values(lerAnalise()).some(v => v !== null && v !== '');
}

/**
 * Regra de Alisson (01/08/2026): cada coordenador só mexe no que
 * registrou; só ADMIN edita/exclui a de outro. Ocorrência nova (sem
 * dados ainda) sempre pode — quem cria é sempre o autor.
 */
function podeAgirNestaOcorrencia(dados) {
    if (!dados) return true;
    return dados.registrado_por === usuario()?.funcionario_id || temFuncao('ADMIN');
}

function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ─── Header ─────────────────────────────────────────────────────────────
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

// ─── Abas ─────────────────────────────────────────────────────────────────
function initTabs() {
    document.querySelectorAll('#oc-tabs .filtro-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#oc-tabs .filtro-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('.oc-tab-section').forEach(s => s.classList.remove('active'));
            document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
        });
    });
}

// ─── Análise (página 3) — grids gerados a partir do vocabulário ───────────
function montarGridsAnalise() {
    const containers = [
        'grid-analise-tipo', 'grid-analise-via', 'grid-analise-sinalizacao', 'grid-analise-local',
    ];
    ANALISE_GRUPOS.forEach((grupo, i) => {
        const container = document.getElementById(containers[i]);
        container.innerHTML = grupo.campos.map(campo => {
            if (campo.texto) {
                return `<div class="form-group">
                    <label class="form-label">${campo.label}</label>
                    <input type="text" class="form-input" name="${campo.name}" maxlength="120">
                </div>`;
            }
            const opts = campo.opcoes.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
            return `<div class="form-group">
                <label class="form-label">${campo.label}</label>
                <select class="form-select" name="${campo.name}">
                    <option value="">Não informado</option>
                    ${opts}
                </select>
            </div>`;
        }).join('');
    });
}

// ─── Avarias (página 2) — checklist fixo de 10 regiões ────────────────────
function montarAvarias() {
    const container = document.getElementById('lista-avarias');
    container.innerHTML = REGIOES_AVARIA.map(([codigo, label]) => `
        <div class="oc-avaria-row" data-regiao="${codigo}">
            <label class="form-check oc-avaria-regiao">
                <input type="checkbox" class="avaria-check" data-regiao="${codigo}" ${somenteLeitura ? 'disabled' : ''}>
                <span class="form-check-label">${label}</span>
            </label>
            <input type="text" class="form-input avaria-desc" data-regiao="${codigo}" placeholder="Descrição do dano" disabled>
        </div>
    `).join('');

    container.querySelectorAll('.avaria-check').forEach(chk => {
        chk.addEventListener('change', () => {
            const desc = container.querySelector(`.avaria-desc[data-regiao="${chk.dataset.regiao}"]`);
            desc.disabled = !chk.checked || somenteLeitura;
            if (!chk.checked) desc.value = '';
            agendarAutosave();
        });
    });
    container.querySelectorAll('.avaria-desc').forEach(inp => inp.addEventListener('input', agendarAutosave));
}

function lerAvarias() {
    const linhas = [];
    document.querySelectorAll('#lista-avarias .avaria-check').forEach(chk => {
        if (chk.checked) {
            const desc = document.querySelector(`#lista-avarias .avaria-desc[data-regiao="${chk.dataset.regiao}"]`);
            linhas.push({ regiao: chk.dataset.regiao, descricao: desc.value || null });
        }
    });
    return linhas;
}

function preencherAvarias(avarias) {
    document.querySelectorAll('#lista-avarias .avaria-check').forEach(chk => { chk.checked = false; });
    document.querySelectorAll('#lista-avarias .avaria-desc').forEach(inp => { inp.disabled = true; inp.value = ''; });
    (avarias || []).forEach(a => {
        const chk = document.querySelector(`#lista-avarias .avaria-check[data-regiao="${a.regiao}"]`);
        const desc = document.querySelector(`#lista-avarias .avaria-desc[data-regiao="${a.regiao}"]`);
        if (chk) chk.checked = true;
        if (desc) { desc.disabled = somenteLeitura; desc.value = a.descricao || ''; }
    });
}

// ─── Listas dinâmicas (veículos, testemunhas, vítimas, autoridades) ──────

function campoHtml(campo, valor) {
    const val = valor ?? '';
    if (campo.tipo === 'select') {
        const opts = campo.opcoes.map(([v, l]) => `<option value="${v}" ${v === val ? 'selected' : ''}>${l}</option>`).join('');
        return `<select class="form-select" data-campo="${campo.name}"><option value="">—</option>${opts}</select>`;
    }
    if (campo.tipo === 'select-orgao') {
        const opts = catalogoOrgaos.map(o => `<option value="${o.id}" ${o.id === val ? 'selected' : ''}>${escapeHtml(o.nome)}</option>`).join('');
        return `<select class="form-select" data-campo="${campo.name}"><option value="">Selecione…</option>${opts}</select>`;
    }
    if (campo.tipo === 'textarea') {
        return `<textarea class="form-textarea" data-campo="${campo.name}" rows="2">${escapeHtml(val)}</textarea>`;
    }
    if (campo.tipo === 'bool3') {
        return `<select class="form-select" data-campo="${campo.name}">
            <option value="" ${val === '' ? 'selected' : ''}>Não informado</option>
            <option value="true" ${val === true ? 'selected' : ''}>Sim</option>
            <option value="false" ${val === false ? 'selected' : ''}>Não</option>
        </select>`;
    }
    if (campo.tipo === 'number') {
        return `<input type="number" class="form-input" data-campo="${campo.name}" value="${val}">`;
    }
    return `<input type="text" class="form-input" data-campo="${campo.name}" value="${escapeHtml(String(val))}" ${campo.tamanho ? `maxlength="${campo.tamanho}"` : ''}>`;
}

function renderListaDinamica({ containerId, itens, campos, titulo, vazio, rerender }) {
    const container = document.getElementById(containerId);
    if (!itens.length) {
        container.innerHTML = `<div class="oc-vazio">${vazio}</div>`;
        return;
    }
    container.innerHTML = itens.map((item, idx) => `
        <div class="oc-card" data-idx="${idx}" style="background:var(--surface2)">
            <div class="oc-card-header">
                <div class="oc-card-titulo">${escapeHtml(titulo(item, idx))}</div>
                ${somenteLeitura ? '' : `<button type="button" class="btn btn-danger oc-btn-remover" data-idx="${idx}" style="padding:4px 10px;font-size:0.75rem">Remover</button>`}
            </div>
            <div class="oc-grid">
                ${campos.map(c => `
                    <div class="form-group${c.full ? ' oc-grid-full' : ''}">
                        <label class="form-label">${c.label}</label>
                        ${campoHtml(c, item[c.name])}
                    </div>
                `).join('')}
            </div>
        </div>
    `).join('');

    if (somenteLeitura) {
        container.querySelectorAll('[data-campo]').forEach(el => { el.disabled = true; });
        return;
    }

    container.querySelectorAll('[data-campo]').forEach(el => {
        const card = el.closest('[data-idx]');
        const idx = Number(card.dataset.idx);
        const campo = campos.find(c => c.name === el.dataset.campo);
        const evento = el.tagName === 'SELECT' ? 'change' : 'input';
        el.addEventListener(evento, () => {
            let valor = el.value === '' ? null : el.value;
            if (campo.tipo === 'bool3') valor = el.value === '' ? null : el.value === 'true';
            if (campo.tipo === 'number') valor = el.value === '' ? null : Number(el.value);
            itens[idx][campo.name] = valor;
            // Atualiza só o título do card (sem re-renderizar tudo e perder o foco)
            const tituloEl = card.querySelector('.oc-card-titulo');
            if (tituloEl) tituloEl.textContent = titulo(itens[idx], idx);
            agendarAutosave();
        });
    });

    container.querySelectorAll('.oc-btn-remover').forEach(btn => {
        btn.addEventListener('click', () => {
            itens.splice(Number(btn.dataset.idx), 1);
            rerender();
            agendarAutosave();
        });
    });
}

const CAMPOS_VEICULO = [
    { name: 'danos', label: 'Danos', tipo: 'select', opcoes: DANOS_VEICULO },
    { name: 'marca', label: 'Marca' },
    { name: 'modelo', label: 'Modelo' },
    { name: 'ano', label: 'Ano', tamanho: 9 },
    { name: 'cor', label: 'Cor' },
    { name: 'placa', label: 'Placa' },
    { name: 'cidade_placa', label: 'Cidade (placa)' },
    { name: 'estado_placa', label: 'UF', tamanho: 2 },
    { name: 'renavam', label: 'Renavam' },
    { name: 'proprietario', label: 'Proprietário/Motorista' },
    { name: 'fones', label: 'Fones' },
    { name: 'email', label: 'E-mail' },
    { name: 'endereco', label: 'Endereço', full: true },
    { name: 'cidade', label: 'Cidade' },
    { name: 'rg', label: 'RG' },
    { name: 'cpf', label: 'CPF' },
    { name: 'cnh', label: 'CNH' },
    { name: 'seguradora', label: 'Seguradora' },
    { name: 'seguradora_fone', label: 'Fone seguradora' },
    { name: 'sinistro_numero', label: 'Sinistro Nº' },
    { name: 'partes_avariadas', label: 'Partes avariadas', tipo: 'textarea', full: true },
];

const CAMPOS_TESTEMUNHA = [
    { name: 'nome', label: 'Nome', full: true },
    { name: 'rg', label: 'RG' },
    { name: 'endereco', label: 'Endereço' },
    { name: 'numero', label: 'Nº' },
    { name: 'bairro', label: 'Bairro' },
    { name: 'cidade', label: 'Cidade' },
    { name: 'fone1', label: 'Fone 1' },
    { name: 'fone2', label: 'Fone 2' },
];

const CAMPOS_VITIMA = [
    { name: 'nome', label: 'Nome', full: true },
    { name: 'rg', label: 'RG' },
    { name: 'cpf', label: 'CPF' },
    { name: 'idade', label: 'Idade', tipo: 'number' },
    { name: 'fone', label: 'Fone' },
    { name: 'endereco', label: 'Endereço' },
    { name: 'numero', label: 'Nº' },
    { name: 'bairro', label: 'Bairro' },
    { name: 'cidade', label: 'Cidade' },
    { name: 'era_passageiro', label: 'Era passageiro do nosso ônibus?', tipo: 'bool3' },
    { name: 'destino_socorro', label: 'Socorrida para' },
    { name: 'contato_parentesco', label: 'Parentesco do contato' },
    { name: 'contato_nome', label: 'Nome do contato' },
    { name: 'contato_fone', label: 'Fone do contato' },
    { name: 'dados_pessoais', label: 'Dados pessoais (observações)', tipo: 'textarea', full: true },
];

const CAMPOS_AUTORIDADE = [
    { name: 'orgao_id', label: 'Órgão', tipo: 'select-orgao', full: true },
    { name: 'identificacao', label: 'Identificação (viatura)' },
    { name: 'responsavel', label: 'Responsável' },
    { name: 'observacao', label: 'Observação', tipo: 'textarea', full: true },
];

function renderVeiculos() {
    renderListaDinamica({
        containerId: 'lista-veiculos', itens: veiculosTerceiro, campos: CAMPOS_VEICULO,
        titulo: (item, idx) => `Veículo nº ${idx + 1}${item.placa ? ' — ' + item.placa : ''}`,
        vazio: 'Nenhum veículo de terceiro envolvido.',
        rerender: renderVeiculos,
    });
}

function renderTestemunhas() {
    renderListaDinamica({
        containerId: 'lista-testemunhas', itens: testemunhas, campos: CAMPOS_TESTEMUNHA,
        titulo: (item, idx) => item.nome || `Testemunha ${idx + 1}`,
        vazio: 'Nenhuma testemunha registrada.',
        rerender: renderTestemunhas,
    });
}

function renderVitimas() {
    renderListaDinamica({
        containerId: 'lista-vitimas', itens: vitimas, campos: CAMPOS_VITIMA,
        titulo: (item, idx) => item.nome || `Vítima ${idx + 1}`,
        vazio: 'Nenhuma vítima registrada.',
        rerender: renderVitimas,
    });
}

function renderAutoridades() {
    renderListaDinamica({
        containerId: 'lista-autoridades', itens: autoridades, campos: CAMPOS_AUTORIDADE,
        titulo: (item, idx) => {
            const orgao = catalogoOrgaos.find(o => o.id === item.orgao_id);
            return orgao ? orgao.nome : `Autoridade ${idx + 1}`;
        },
        vazio: 'Nenhuma autoridade registrada.',
        rerender: renderAutoridades,
    });
}

function initBotoesAdicionar() {
    if (somenteLeitura) {
        ['btn-add-veiculo', 'btn-add-testemunha', 'btn-add-vitima', 'btn-add-autoridade'].forEach(id => {
            document.getElementById(id).style.display = 'none';
        });
        return;
    }
    document.getElementById('btn-add-veiculo').addEventListener('click', () => {
        veiculosTerceiro.push({ ordem: veiculosTerceiro.length + 1 });
        renderVeiculos();
        agendarAutosave();
    });
    document.getElementById('btn-add-testemunha').addEventListener('click', () => {
        testemunhas.push({ ordem: testemunhas.length + 1, nome: '' });
        renderTestemunhas();
        agendarAutosave();
    });
    document.getElementById('btn-add-vitima').addEventListener('click', () => {
        vitimas.push({ ordem: vitimas.length + 1, nome: '' });
        renderVitimas();
        agendarAutosave();
    });
    document.getElementById('btn-add-autoridade').addEventListener('click', () => {
        autoridades.push({ ordem: autoridades.length + 1, orgao_id: catalogoOrgaos[0]?.id || '' });
        renderAutoridades();
        agendarAutosave();
    });
}

// ─── Catálogos ──────────────────────────────────────────────────────────

async function carregarCatalogosOcorrencia() {
    const dados = await apiGet('/ocorrencias/catalogos');
    catalogoTipos = dados.tipos || [];
    catalogoOrgaos = dados.orgaos || [];

    const selectTipo = document.getElementById('f-tipo');
    catalogoTipos.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = t.nome;
        selectTipo.appendChild(opt);
    });

    montarGridsAnalise();
    montarAvarias();
}

async function carregarCatalogosPatio() {
    try {
        const [onibus, linhas] = await Promise.all([
            apiGet('/onibus?limit=1000'),
            apiGet('/linhas?limit=500'),
        ]);

        const dlPrefixos = document.getElementById('dl-prefixos');
        onibus.forEach(o => {
            const opt = document.createElement('option');
            opt.value = String(o.numero_frota);
            dlPrefixos.appendChild(opt);
        });

        const dlLinhas = document.getElementById('dl-linhas');
        linhas.forEach(l => {
            const opt = document.createElement('option');
            opt.value = l.codigo;
            opt.textContent = l.nome;
            dlLinhas.appendChild(opt);
        });
    } catch (err) {
        // Autocomplete é conveniência, não bloqueio — a ocorrência continua editável sem ele.
        console.warn('[ocorrencia.form] catálogos do Pátio indisponíveis:', err);
    }
}

/**
 * Autocomplete de condutor/cobrador por RE ou nome, contra
 * GET /funcionarios/busca (protegido por exige("ocorrencia") — funciona
 * tanto pra Coordenador de Tráfego quanto pra Encarregado, que nem sempre
 * tem acesso a "usuarios"). Busca ao digitar em qualquer um dos dois campos
 * (RE ou nome); ao escolher uma sugestão, completa o outro campo sozinho.
 */
function initBuscaFuncionario({ inputReId, inputNomeId, datalistReId, datalistNomeId }) {
    const inputRe = document.getElementById(inputReId);
    const inputNome = document.getElementById(inputNomeId);
    const datalistRe = document.getElementById(datalistReId);
    const datalistNome = document.getElementById(datalistNomeId);
    const cachePorRe = new Map();
    let debounce = null;

    async function buscarEPreencher(termo) {
        if (!termo || termo.trim().length < 2) return;
        let resultados = [];
        try {
            resultados = await apiGet(`/funcionarios/busca?q=${encodeURIComponent(termo.trim())}`);
        } catch {
            return; // autocomplete é conveniência — falha aqui não bloqueia o preenchimento
        }
        cachePorRe.clear();
        resultados.forEach(r => cachePorRe.set(r.re, r));
        datalistRe.innerHTML = resultados.map(r => `<option value="${r.re}">${escapeHtml(r.nome)}</option>`).join('');
        datalistNome.innerHTML = resultados.map(r => `<option value="${escapeHtml(r.nome)}"></option>`).join('');
    }

    function agendarBusca(termo) {
        clearTimeout(debounce);
        debounce = setTimeout(() => buscarEPreencher(termo), 300);
    }

    inputRe.addEventListener('input', () => agendarBusca(inputRe.value));
    inputRe.addEventListener('change', () => {
        const encontrado = cachePorRe.get(inputRe.value.trim());
        if (encontrado && !inputNome.value) inputNome.value = encontrado.nome;
    });

    inputNome.addEventListener('input', () => agendarBusca(inputNome.value));
    inputNome.addEventListener('change', () => {
        const nomeDigitado = inputNome.value.trim();
        const encontrado = [...cachePorRe.values()].find(r => r.nome === nomeDigitado);
        if (encontrado && !inputRe.value) inputRe.value = encontrado.re;
    });
}

function initAutocompleteCondutorECobrador() {
    initBuscaFuncionario({
        inputReId: 'f-condutor-re', inputNomeId: 'f-condutor-nome',
        datalistReId: 'dl-condutor-re', datalistNomeId: 'dl-condutor-nome',
    });
    initBuscaFuncionario({
        inputReId: 'f-cobrador-re', inputNomeId: 'f-cobrador-nome',
        datalistReId: 'dl-cobrador-re', datalistNomeId: 'dl-cobrador-nome',
    });
}

// ─── Autosave ───────────────────────────────────────────────────────────

function marcarStatus(texto, classe) {
    const el = document.getElementById('oc-autosave-status');
    el.textContent = texto;
    el.className = `oc-autosave-status ${classe || ''}`.trim();
}

function agendarAutosave() {
    if (somenteLeitura) return;
    marcarStatus('Alterações pendentes…');
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(salvarAgora, 1200);
}

function payloadAtualizacao() {
    const capa = lerCapaEFechamento();
    return {
        ...capa,
        analise: algumaAnaliseTemDado() ? lerAnalise() : null,
        veiculos_terceiro: veiculosTerceiro,
        avarias: lerAvarias(),
        vitimas,
        testemunhas,
        autoridades,
    };
}

async function salvarAgora() {
    if (somenteLeitura) return;
    const capa = lerCapaEFechamento();
    if (!capa.tipo_ocorrencia_id || !capa.data_ocorrencia || !capa.hora_ocorrencia || !capa.prefixo) {
        marcarStatus('Preencha tipo, data, hora e prefixo para começar a salvar');
        return;
    }

    marcarStatus('Salvando…', 'salvando');
    try {
        if (!ocorrenciaId) {
            const criada = await apiPost('/ocorrencias', capa);
            ocorrenciaId = criada.id;
            history.replaceState(null, '', `ocorrencia-form.html?id=${ocorrenciaId}`);
            mostrarControlesPosCriacao(true);
        }
        const atualizada = await apiPatch(`/ocorrencias/${ocorrenciaId}`, payloadAtualizacao());
        dadosAtuais = atualizada;
        atualizarTitulo(atualizada.numero);
        atualizarBotaoFinalizar(atualizada.status);
        marcarStatus('Tudo salvo', 'salvo');
    } catch (err) {
        console.error('[ocorrencia.form] erro ao salvar:', err);
        marcarStatus(`Erro ao salvar: ${err.message}`, 'erro');
    }
}

function ligarAutosaveGenerico() {
    document.querySelectorAll('main [name]').forEach(el => {
        if (el.closest(SELETOR_EXCLUIR_TOPO)) return;
        if (somenteLeitura) { el.disabled = true; return; }
        const evento = (el.tagName === 'SELECT' || el.type === 'checkbox' || el.type === 'date' || el.type === 'time') ? 'change' : 'input';
        el.addEventListener(evento, agendarAutosave);
    });
    document.querySelectorAll(SELETOR_ANALISE).forEach(el => {
        if (somenteLeitura) { el.disabled = true; return; }
        el.addEventListener('change', agendarAutosave);
    });
}

// ─── Título e botões dependentes do estado ────────────────────────────────

function atualizarTitulo(numero) {
    document.getElementById('oc-titulo').textContent = numero ? `Ocorrência #${numero}` : 'Nova Ocorrência';
}

/**
 * Destrava Imprimir e Mensagem do Sinistro — os botões já nascem visíveis
 * no HTML (ver ocorrencia-form.html), só desabilitados com um title
 * explicando o porquê. Uma função escondida é fácil de descobrir clicando;
 * uma que nunca aparece, não — por isso nunca usamos display:none aqui.
 */
function mostrarControlesPosCriacao(podeAgir = true) {
    const btnImprimir = document.getElementById('btn-imprimir');
    const btnSinistro = document.getElementById('btn-sinistro');
    btnImprimir.disabled = false;
    btnImprimir.removeAttribute('title');
    btnImprimir.setAttribute('aria-disabled', 'false');
    btnSinistro.disabled = false;
    btnSinistro.removeAttribute('title');
    btnSinistro.setAttribute('aria-disabled', 'false');

    // Excluir some (não fica só desabilitado) quando a ocorrência é de
    // outra pessoa — item 2i do prompt: em vez do botão, a nota de
    // "somente leitura" já aparece no status (ver preencherTudo).
    const btnExcluir = document.getElementById('btn-excluir');
    if (podeAgir) {
        btnExcluir.style.display = '';
        btnExcluir.disabled = false;
        btnExcluir.removeAttribute('title');
        btnExcluir.setAttribute('aria-disabled', 'false');
    } else {
        btnExcluir.style.display = 'none';
    }

    document.getElementById('anexo-aviso-sem-id').style.display = 'none';
    document.getElementById('anexo-upload-area').style.display = somenteLeitura ? 'none' : '';
}

function atualizarBotaoFinalizar(status) {
    const btn = document.getElementById('btn-finalizar');
    btn.style.display = (!somenteLeitura && ocorrenciaId && status === 'RASCUNHO') ? '' : 'none';
}

// ─── Carregar ocorrência existente ─────────────────────────────────────────

function preencherTudo(dados) {
    preencherCapaEFechamento(dados);
    preencherAvarias(dados.avarias);
    if (dados.analise) {
        document.querySelectorAll(SELETOR_ANALISE).forEach(el => preencherValorCampo(el, dados.analise[el.name]));
    }

    veiculosTerceiro = (dados.veiculos_terceiro || []).map(v => ({ ...v }));
    vitimas = (dados.vitimas || []).map(v => ({ ...v }));
    testemunhas = (dados.testemunhas || []).map(t => ({ ...t }));
    autoridades = (dados.autoridades || []).map(a => ({ ...a }));
    renderVeiculos();
    renderVitimas();
    renderTestemunhas();
    renderAutoridades();
    renderAnexos(dados.anexos || []);

    atualizarTitulo(dados.numero);
    const podeAgir = podeAgirNestaOcorrencia(dados);
    mostrarControlesPosCriacao(podeAgir);
    atualizarBotaoFinalizar(dados.status);
    if (podeAgir) {
        marcarStatus('Tudo salvo', 'salvo');
    } else {
        marcarStatus(`Registrada por ${dados.registrado_por_nome || 'outro coordenador'} — somente leitura`);
    }
}

function definirPadroesNovaOcorrencia() {
    const agora = new Date();
    document.getElementById('f-data').value = agora.toISOString().slice(0, 10);
    document.getElementById('f-hora').value = `${String(agora.getHours()).padStart(2, '0')}:${String(agora.getMinutes()).padStart(2, '0')}`;
}

// ─── Finalizar ──────────────────────────────────────────────────────────

function initFinalizar() {
    document.getElementById('btn-finalizar').addEventListener('click', async () => {
        if (!ocorrenciaId) return;
        await salvarAgora();
        if (!confirm('Finalizar esta ocorrência?\n\nO registro continua editável depois, mas passa a valer como concluído.')) return;
        try {
            const atualizada = await apiPost(`/ocorrencias/${ocorrenciaId}/finalizar`);
            dadosAtuais = atualizada;
            atualizarBotaoFinalizar(atualizada.status);
            marcarStatus('Ocorrência finalizada', 'salvo');
        } catch (err) {
            mostrarErroGeral(err.message);
        }
    });
}

function mostrarErroGeral(msg) {
    const el = document.getElementById('oc-erro-geral');
    el.textContent = msg;
    el.style.display = 'block';
}

// ─── Imprimir e mensagem do sinistro ──────────────────────────────────────

function initAcoesFinais() {
    document.getElementById('btn-imprimir').addEventListener('click', async () => {
        if (!ocorrenciaId) return;
        try {
            const fresh = await apiGet(`/ocorrencias/${ocorrenciaId}`);
            imprimirOcorrencia(fresh);
        } catch (err) {
            mostrarErroGeral(`Erro ao preparar impressão: ${err.message}`);
        }
    });

    document.getElementById('btn-sinistro').addEventListener('click', () => {
        if (!ocorrenciaId) return;
        abrirMensagemSinistro(ocorrenciaId);
    });
}

function initExclusao() {
    document.getElementById('btn-excluir').addEventListener('click', () => {
        if (!ocorrenciaId || !dadosAtuais) return;
        confirmarExclusaoOcorrencia(
            {
                id: ocorrenciaId,
                numero: dadosAtuais.numero,
                data_ocorrencia: dadosAtuais.data_ocorrencia,
                tipo_nome: dadosAtuais.tipo_ocorrencia?.nome,
            },
            () => { window.location.href = 'ocorrencias.html'; },
        );
    });
}

// ─── Anexos ───────────────────────────────────────────────────────────────

async function apiUpload(path, formData) {
    const url = `${API_BASE_URL}${path}`;
    const token = localStorage.getItem(TOKEN_KEY);
    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    let response;
    try {
        response = await fetch(url, { method: 'POST', headers, body: formData });
    } catch (networkErr) {
        throw new ApiError(0, 'Não foi possível conectar à API.', { cause: networkErr.message });
    }
    if (response.status === 401) {
        logout();
        window.location.replace('index.html');
        throw new ApiError(401, 'Sessão expirada. Faça login novamente.', null);
    }
    let payload = null;
    try { payload = await response.json(); } catch { /* sem corpo */ }
    if (!response.ok) {
        const detail = payload?.erro || payload?.detail || response.statusText;
        throw new ApiError(response.status, typeof detail === 'string' ? detail : 'Erro no upload', payload);
    }
    return payload;
}

async function baixarAnexo(anexo) {
    // Rota protegida por Bearer — não dá pra usar <a href> puro (não manda o header).
    const token = localStorage.getItem(TOKEN_KEY);
    try {
        const resp = await fetch(`${API_BASE_URL}/ocorrencias/${ocorrenciaId}/anexos/${anexo.id}/arquivo`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!resp.ok) throw new Error('Falha ao baixar');
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
        alert('Não foi possível abrir o anexo.');
    }
}

function renderAnexos(anexos) {
    const container = document.getElementById('lista-anexos');
    if (!anexos || anexos.length === 0) {
        container.innerHTML = '<div class="oc-vazio">Nenhum anexo enviado.</div>';
        return;
    }
    container.innerHTML = anexos.map(a => `
        <div class="oc-anexo-item" data-id="${a.id}">
            <span class="oc-anexo-tipo">${TIPOS_ANEXO[a.tipo] || a.tipo}</span>
            <button type="button" class="oc-anexo-nome oc-anexo-abrir" data-id="${a.id}" style="background:none;border:none;color:var(--accent4);text-align:left;cursor:pointer;text-decoration:underline">${escapeHtml(a.nome_original || 'arquivo')}</button>
            ${somenteLeitura ? '' : `<button type="button" class="btn btn-danger oc-btn-remover-anexo" data-id="${a.id}" style="padding:4px 10px;font-size:0.75rem;flex-shrink:0">Remover</button>`}
        </div>
    `).join('');

    container.querySelectorAll('.oc-anexo-abrir').forEach(btn => {
        const anexo = anexos.find(a => a.id === btn.dataset.id);
        btn.addEventListener('click', () => baixarAnexo(anexo));
    });
    container.querySelectorAll('.oc-btn-remover-anexo').forEach(btn => {
        btn.addEventListener('click', () => removerAnexo(btn.dataset.id));
    });
}

async function removerAnexo(anexoId) {
    if (!confirm('Remover este anexo?')) return;
    try {
        await apiDelete(`/ocorrencias/${ocorrenciaId}/anexos/${anexoId}`);
        const fresh = await apiGet(`/ocorrencias/${ocorrenciaId}`);
        dadosAtuais = fresh;
        renderAnexos(fresh.anexos || []);
    } catch (err) {
        alert(`Erro ao remover anexo: ${err.message}`);
    }
}

function initUploadAnexo() {
    document.getElementById('f-anexo-arquivo').addEventListener('change', () => {
        const input = document.getElementById('f-anexo-arquivo');
        const label = document.getElementById('anexo-file-label');
        if (input.files && input.files[0]) {
            label.textContent = `📎 ${input.files[0].name}`;
            label.classList.add('has-file');
        }
    });

    document.getElementById('btn-upload-anexo').addEventListener('click', async () => {
        const inputArquivo = document.getElementById('f-anexo-arquivo');
        const tipo = document.getElementById('f-anexo-tipo').value;
        const erroEl = document.getElementById('anexo-erro');
        erroEl.style.display = 'none';
        if (!ocorrenciaId) return;
        if (!inputArquivo.files || inputArquivo.files.length === 0) {
            erroEl.textContent = 'Selecione um arquivo.';
            erroEl.style.display = 'block';
            return;
        }
        const formData = new FormData();
        formData.append('arquivo', inputArquivo.files[0]);
        formData.append('tipo', tipo);
        try {
            await apiUpload(`/ocorrencias/${ocorrenciaId}/anexos`, formData);
            inputArquivo.value = '';
            document.getElementById('anexo-file-label').textContent = '📎 Escolher arquivo (JPEG, PNG ou PDF — máx 10 MB)';
            document.getElementById('anexo-file-label').classList.remove('has-file');
            const fresh = await apiGet(`/ocorrencias/${ocorrenciaId}`);
            dadosAtuais = fresh;
            renderAnexos(fresh.anexos || []);
        } catch (err) {
            erroEl.textContent = err.message || 'Erro ao enviar anexo.';
            erroEl.style.display = 'block';
        }
    });
}

// ─── Boot ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    initHeader();

    // Busca a ocorrência (se houver id) ANTES de montar os campos e ligar
    // o autosave — é o único jeito de saber se esta pessoa pode agir nela
    // (autora ou ADMIN) a tempo de decidir se o formulário nasce travado.
    if (ocorrenciaId) {
        try {
            dadosAtuais = await apiGet(`/ocorrencias/${ocorrenciaId}`);
        } catch (err) {
            if (err instanceof ApiError && err.status === 401) return;
            mostrarErroGeral(`Erro ao carregar ocorrência: ${err.message}`);
            return;
        }
        if (!podeAgirNestaOcorrencia(dadosAtuais)) somenteLeitura = true;
    }

    initTabs();
    initBotoesAdicionar();
    initFinalizar();
    initAcoesFinais();
    initExclusao();
    initUploadAnexo();

    try {
        await carregarCatalogosOcorrencia();
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        mostrarErroGeral(`Erro ao carregar catálogos da ocorrência: ${err.message}`);
        return;
    }

    await carregarCatalogosPatio();
    initAutocompleteCondutorECobrador();

    if (ocorrenciaId && dadosAtuais) {
        preencherTudo(dadosAtuais);
    } else {
        definirPadroesNovaOcorrencia();
        marcarStatus('Preencha tipo, data, hora e prefixo para começar a salvar');
    }

    ligarAutosaveGenerico();
});
