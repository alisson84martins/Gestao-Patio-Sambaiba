/*
 * linhas.seletor.js — Seletor de linhas do catálogo (Bloco D §3)
 * -------------------------------------------------------------------------------
 * Escrito uma vez só, compartilhado por fiscal.html (várias linhas, no
 * cadastro de ponto) e fiscal-painel.html (uma linha, em "Minhas linhas").
 *
 * Origem: uma R.A registrada pelo fiscal não apareceu no painel do
 * coordenador porque o ponto foi cadastrado com a linha "1726" e as linhas
 * do coordenador são "1726-10" — nenhum erro, o registro só sumiu. Se o
 * fiscal escolhe a linha de uma lista (GET /fiscalizacao/catalogo/linhas),
 * não existe o que digitar errado.
 *
 * ⛔ Nunca cai de volta para digitação livre: catálogo vazio ou chamada que
 * falhou mostram a mesma mensagem clara, nunca um campo em branco.
 */

import { apiGet } from './api.js';
import { escapeHtml } from './escape.js';

/**
 * @param {object} args
 * @param {HTMLElement} args.containerLista — onde a lista selecionável entra
 * @param {HTMLInputElement} [args.campoBusca] — input de filtro (código ou nome)
 * @param {boolean} [args.multiplo=false] — true: toque alterna (várias linhas);
 *   false: toque escolhe uma só e desmarca as demais
 * @param {(selecao: Set<string>) => void} [args.onMudar]
 * @returns {{ carregar: (selecionadasIniciais?: Iterable<string>) => Promise<void>, getSelecao: () => Set<string> }}
 */
export function criarSeletorLinhas({ containerLista, campoBusca, multiplo = false, onMudar }) {
    let catalogo = [];
    let catalogoOk = true;
    let termoBusca = '';
    let selecao = new Set();

    function filtrar() {
        const termo = termoBusca.trim().toLowerCase();
        if (!termo) return catalogo;
        return catalogo.filter(l =>
            l.codigo.toLowerCase().includes(termo) || l.nome.toLowerCase().includes(termo)
        );
    }

    function render() {
        if (!catalogoOk || catalogo.length === 0) {
            containerLista.innerHTML =
                '<div class="oc-vazio" style="color:var(--accent)">Não consegui carregar a lista de linhas.</div>';
            return;
        }
        const filtradas = filtrar();
        if (filtradas.length === 0) {
            containerLista.innerHTML = '<div class="oc-vazio">Nenhuma linha encontrada para esta busca.</div>';
            return;
        }
        containerLista.innerHTML = filtradas.map(l => `
            <button type="button" class="linhas-seletor-item ${selecao.has(l.codigo) ? 'active' : ''}" data-linha="${escapeHtml(l.codigo)}">
                <span class="linhas-seletor-codigo">${escapeHtml(l.codigo)}</span>
                <span class="linhas-seletor-nome">${escapeHtml(l.nome)}</span>
            </button>
        `).join('');
        containerLista.querySelectorAll('[data-linha]').forEach(btn => {
            btn.addEventListener('click', () => {
                const codigo = btn.dataset.linha;
                if (multiplo) {
                    if (selecao.has(codigo)) selecao.delete(codigo);
                    else selecao.add(codigo);
                } else {
                    selecao = new Set([codigo]);
                }
                render();
                if (onMudar) onMudar(new Set(selecao));
            });
        });
    }

    if (campoBusca) {
        campoBusca.addEventListener('input', () => {
            termoBusca = campoBusca.value;
            render();
        });
    }

    return {
        async carregar(selecionadasIniciais) {
            selecao = new Set(selecionadasIniciais || []);
            termoBusca = '';
            if (campoBusca) campoBusca.value = '';
            containerLista.innerHTML = '<div class="patio-loading">Carregando…</div>';
            try {
                catalogo = await apiGet('/fiscalizacao/catalogo/linhas');
                catalogoOk = true;
            } catch {
                catalogo = [];
                catalogoOk = false;
            }
            render();
        },
        getSelecao: () => new Set(selecao),
    };
}
