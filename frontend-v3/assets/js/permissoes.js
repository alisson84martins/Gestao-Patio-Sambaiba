/*
 * Permissões — Frontend V3 (Etapa 5)
 * -------------------------------------
 * Duas abas:
 *   - Por função: edita o pacote padrão da função (grade recurso × ler/escrever).
 *     PUT /permissoes/funcao/{codigo} substitui o pacote inteiro — a grade
 *     sempre manda os 10 recursos, marcados ou não.
 *   - Por pessoa: ajusta exceções individuais. PUT /permissoes/funcionario/{id}
 *     também substitui o conjunto inteiro de overrides, então só mandamos os
 *     recursos onde o valor escolhido DIVERGE do pacote herdado da função —
 *     senão toda pessoa acabaria com 10 "exceções" idênticas ao herdado no
 *     primeiro salvamento. O pacote herdado é calculado aqui unindo (OR) os
 *     pacotes de cada função ativa da pessoa — mesma regra da vw_acesso_funcao,
 *     só que client-side porque não existe endpoint que devolva o herdado puro.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPut } from './api.js';
import { podeEscrever } from './sessao.js';

if (!requireAuth()) throw new Error('Não autenticado');
if (!podeEscrever('usuarios')) {
    window.location.replace('patio.html');
    throw new Error('Sem acesso de escrita ao recurso usuarios');
}

const NOMES_MODULO = {
    PATIO: 'Pátio',
    COORDENADORIA: 'Coordenadoria',
    FISCALIZACAO: 'Fiscalização',
    ADMINISTRACAO: 'Administração',
};

// ─── Estado ─────────────────────────────────────────────────────
let recursosCatalogo = [];               // GET /recursos, carregado uma vez
let pacoteFuncaoCache = new Map();       // codigo função → list[PermissaoRecurso]
let funcaoSelecionada = null;
let pessoaSelecionada = null;            // FuncionarioComFuncoes
let pessoaBaseline = new Map();          // recurso → {pode_ler, pode_escrever} herdado

// ─── Boot ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    setupHeader();
    setupTabs();
    setupBuscaPessoa();
    document.getElementById('btn-salvar-funcao').addEventListener('click', salvarFuncao);
    document.getElementById('btn-salvar-pessoa').addEventListener('click', salvarPessoa);
    init();
});

async function init() {
    try {
        recursosCatalogo = await apiGet('/recursos');
    } catch (err) {
        recursosCatalogo = [];
    }
    carregarFuncoesLateral();
}

function setupHeader() {
    const user = getCurrentUser();
    document.getElementById('user-name').textContent = user?.nome || '—';
    document.getElementById('user-meta').textContent = (user?.re || '—').toUpperCase();
    document.getElementById('btn-logout').addEventListener('click', () => {
        logout();
        window.location.replace('index.html');
    });
}

function setupTabs() {
    document.querySelectorAll('#permissoes-tabs .filtro-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#permissoes-tabs .filtro-btn').forEach(b => b.classList.toggle('active', b === btn));
            document.getElementById('tab-funcao').style.display = btn.dataset.tab === 'funcao' ? '' : 'none';
            document.getElementById('tab-pessoa').style.display = btn.dataset.tab === 'pessoa' ? '' : 'none';
        });
    });
}

// ─── GRADE DE RECURSOS (compartilhada pelas duas abas) ────────────
function renderGrid(container, valores, opts = {}) {
    const porModulo = {};
    for (const r of recursosCatalogo) {
        (porModulo[r.modulo_codigo] ??= []).push(r);
    }

    let html = '';
    for (const [moduloCodigo, lista] of Object.entries(porModulo)) {
        html += `<div class="permissoes-modulo-header">${NOMES_MODULO[moduloCodigo] || moduloCodigo}</div>`;
        html += '<table class="permissoes-tabela"><tbody>';
        for (const r of lista) {
            const v = valores.get(r.codigo) || { pode_ler: false, pode_escrever: false };
            const excecao = Boolean(opts.excecoes?.get(r.codigo));
            html += `
                <tr data-recurso="${r.codigo}" class="${excecao ? 'linha-excecao' : ''}">
                    <td class="permissoes-recurso-nome">${r.nome}</td>
                    <td><label class="form-check"><input type="checkbox" class="chk-ler" ${v.pode_ler ? 'checked' : ''}><span class="form-check-label">Ler</span></label></td>
                    <td><label class="form-check"><input type="checkbox" class="chk-escrever" ${v.pode_escrever ? 'checked' : ''}><span class="form-check-label">Escrever</span></label></td>
                </tr>`;
        }
        html += '</tbody></table>';
    }
    container.innerHTML = html;

    if (opts.onChange) {
        container.querySelectorAll('input[type="checkbox"]').forEach(chk => {
            chk.addEventListener('change', () => opts.onChange(container));
        });
    }
}

function lerGrid(container) {
    const valores = new Map();
    container.querySelectorAll('tr[data-recurso]').forEach(tr => {
        valores.set(tr.dataset.recurso, {
            pode_ler: tr.querySelector('.chk-ler').checked,
            pode_escrever: tr.querySelector('.chk-escrever').checked,
        });
    });
    return valores;
}

// ─── ABA POR FUNÇÃO ────────────────────────────────────────────────
async function carregarFuncoesLateral() {
    const container = document.getElementById('funcoes-lista-lateral');
    try {
        const funcoes = await apiGet('/funcoes');
        container.innerHTML = funcoes.map(f => `
            <button type="button" class="permissoes-funcao-btn" data-codigo="${f.codigo}">${f.nome}</button>
        `).join('');
        container.querySelectorAll('.permissoes-funcao-btn').forEach(btn => {
            btn.addEventListener('click', () => selecionarFuncao(btn.dataset.codigo, btn));
        });
    } catch (err) {
        container.innerHTML = `<div class="patio-loading" style="color:var(--accent)">Erro ao carregar funções: ${err.message}</div>`;
    }
}

async function pacoteFuncao(codigo) {
    if (!pacoteFuncaoCache.has(codigo)) {
        const pacote = await apiGet(`/permissoes/funcao/${codigo}`);
        pacoteFuncaoCache.set(codigo, pacote);
    }
    return pacoteFuncaoCache.get(codigo);
}

async function selecionarFuncao(codigo, btnEl) {
    funcaoSelecionada = codigo;
    document.querySelectorAll('.permissoes-funcao-btn').forEach(b => b.classList.toggle('active', b === btnEl));
    document.getElementById('permissoes-funcao-titulo').textContent = btnEl.textContent;
    document.getElementById('permissoes-funcao-erro').style.display = 'none';
    document.getElementById('permissoes-funcao-sucesso').style.display = 'none';

    const grid = document.getElementById('permissoes-funcao-grid');
    grid.innerHTML = '<div class="patio-loading">Carregando…</div>';
    try {
        const pacote = await pacoteFuncao(codigo);
        const valores = new Map(pacote.map(p => [p.recurso, { pode_ler: p.pode_ler, pode_escrever: p.pode_escrever }]));
        renderGrid(grid, valores);
        document.getElementById('btn-salvar-funcao').style.display = '';
    } catch (err) {
        grid.innerHTML = `<div class="patio-loading" style="color:var(--accent)">Erro ao carregar: ${err.message}</div>`;
    }
}

async function salvarFuncao() {
    if (!funcaoSelecionada) return;
    const erro = document.getElementById('permissoes-funcao-erro');
    const sucesso = document.getElementById('permissoes-funcao-sucesso');
    erro.style.display = 'none';
    sucesso.style.display = 'none';

    const valores = lerGrid(document.getElementById('permissoes-funcao-grid'));
    const payload = Array.from(valores.entries()).map(([recurso, v]) => ({
        recurso,
        pode_ler: v.pode_ler,
        pode_escrever: v.pode_escrever,
    }));

    try {
        const atualizado = await apiPut(`/permissoes/funcao/${funcaoSelecionada}`, payload);
        pacoteFuncaoCache.set(funcaoSelecionada, atualizado);
        sucesso.textContent = 'Permissões salvas.';
        sucesso.style.display = '';
    } catch (err) {
        erro.textContent = err.message;
        erro.style.display = '';
    }
}

// ─── ABA POR PESSOA ─────────────────────────────────────────────────
function setupBuscaPessoa() {
    let debounce;
    document.getElementById('pessoa-busca').addEventListener('input', e => {
        clearTimeout(debounce);
        const termo = e.target.value.trim();
        if (termo.length < 2) {
            document.getElementById('pessoa-resultados').innerHTML = '';
            return;
        }
        debounce = setTimeout(() => buscarPessoas(termo), 250);
    });
}

async function buscarPessoas(termo) {
    const container = document.getElementById('pessoa-resultados');
    try {
        const resultados = await apiGet(`/funcionarios?busca=${encodeURIComponent(termo)}&limit=20`);
        if (resultados.length === 0) {
            container.innerHTML = '<div class="patio-loading">Nenhum funcionário encontrado.</div>';
            return;
        }
        container.innerHTML = resultados.map(f => `
            <div class="pessoa-resultado-item" data-id="${f.id}">
                <div class="pessoa-resultado-nome">${f.nome}</div>
                <div class="pessoa-resultado-re">RE ${f.re}</div>
            </div>
        `).join('');
        container.querySelectorAll('.pessoa-resultado-item').forEach(el => {
            el.addEventListener('click', () => selecionarPessoa(el.dataset.id, el));
        });
    } catch (err) {
        container.innerHTML = `<div class="patio-loading" style="color:var(--accent)">Erro na busca: ${err.message}</div>`;
    }
}

/** Une (OR) os pacotes das funções ativas da pessoa — mesma regra da vw_acesso_funcao. */
async function calcularBaseline(vinculos) {
    const codigos = [...new Set((vinculos || []).filter(v => v.ativo).map(v => v.funcao.codigo))];
    const pacotes = await Promise.all(codigos.map(pacoteFuncao));

    const baseline = new Map(recursosCatalogo.map(r => [r.codigo, { pode_ler: false, pode_escrever: false }]));
    for (const pacote of pacotes) {
        for (const item of pacote) {
            const atual = baseline.get(item.recurso) || { pode_ler: false, pode_escrever: false };
            baseline.set(item.recurso, {
                pode_ler: atual.pode_ler || item.pode_ler,
                pode_escrever: atual.pode_escrever || item.pode_escrever,
            });
        }
    }
    return baseline;
}

async function selecionarPessoa(funcionarioId, elResultado) {
    document.querySelectorAll('.pessoa-resultado-item').forEach(el => el.classList.toggle('active', el === elResultado));
    document.getElementById('permissoes-pessoa-erro').style.display = 'none';
    document.getElementById('permissoes-pessoa-sucesso').style.display = 'none';

    const grid = document.getElementById('permissoes-pessoa-grid');
    grid.innerHTML = '<div class="patio-loading">Carregando…</div>';
    document.getElementById('permissoes-pessoa-titulo').textContent = elResultado.querySelector('.pessoa-resultado-nome').textContent;

    try {
        const [funcionario, efetivo] = await Promise.all([
            apiGet(`/funcionarios/${funcionarioId}`),
            apiGet(`/permissoes/funcionario/${funcionarioId}`),
        ]);
        pessoaSelecionada = funcionario;
        pessoaBaseline = await calcularBaseline(funcionario.vinculos);

        const valores = new Map(pessoaBaseline);
        const excecoes = new Map();
        for (const item of efetivo) {
            valores.set(item.recurso, { pode_ler: item.pode_ler, pode_escrever: item.pode_escrever });
            excecoes.set(item.recurso, item.e_excecao);
        }

        renderGrid(grid, valores, {
            excecoes,
            onChange: (container) => atualizarMarcacaoExcecao(container),
        });
        document.getElementById('btn-salvar-pessoa').style.display = '';
    } catch (err) {
        grid.innerHTML = `<div class="patio-loading" style="color:var(--accent)">Erro ao carregar: ${err.message}</div>`;
    }
}

/** Recalcula, em tempo real, quais linhas viram exceção conforme a pessoa mexe nos toggles. */
function atualizarMarcacaoExcecao(container) {
    container.querySelectorAll('tr[data-recurso]').forEach(tr => {
        const recurso = tr.dataset.recurso;
        const base = pessoaBaseline.get(recurso) || { pode_ler: false, pode_escrever: false };
        const ler = tr.querySelector('.chk-ler').checked;
        const escrever = tr.querySelector('.chk-escrever').checked;
        const divergente = ler !== base.pode_ler || escrever !== base.pode_escrever;
        tr.classList.toggle('linha-excecao', divergente);
    });
}

async function salvarPessoa() {
    if (!pessoaSelecionada) return;
    const erro = document.getElementById('permissoes-pessoa-erro');
    const sucesso = document.getElementById('permissoes-pessoa-sucesso');
    erro.style.display = 'none';
    sucesso.style.display = 'none';

    const valores = lerGrid(document.getElementById('permissoes-pessoa-grid'));
    const payload = [];
    for (const [recurso, v] of valores.entries()) {
        const base = pessoaBaseline.get(recurso) || { pode_ler: false, pode_escrever: false };
        if (v.pode_ler !== base.pode_ler || v.pode_escrever !== base.pode_escrever) {
            payload.push({ recurso, pode_ler: v.pode_ler, pode_escrever: v.pode_escrever });
        }
    }

    try {
        await apiPut(`/permissoes/funcionario/${pessoaSelecionada.id}`, payload);
        sucesso.textContent = payload.length > 0
            ? `Exceções salvas (${payload.length} recurso${payload.length > 1 ? 's' : ''}).`
            : 'Nenhuma exceção — tudo igual ao herdado da função.';
        sucesso.style.display = '';
    } catch (err) {
        erro.textContent = err.message;
        erro.style.display = '';
    }
}
