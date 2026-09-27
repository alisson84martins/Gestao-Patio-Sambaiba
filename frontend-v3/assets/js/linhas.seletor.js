/*
 * linhas.seletor.js — Seletor de linhas do catálogo (Bloco D §3)
 * -------------------------------------------------------------------------------
 * Escrito uma vez só, compartilhado por fiscal-painel.html (uma linha, em
 * "Minhas linhas") e escala-fiscais.html (várias linhas, no posto).
 *
 * Origem: uma R.A registrada pelo fiscal não apareceu no painel do
 * coordenador porque o ponto foi cadastrado com a linha "1726" e as linhas
 * do coordenador são "1726-10" — nenhum erro, o registro só sumiu. Se a
 * pessoa escolhe a linha de uma lista do cadastro único (public.linha,
 * migration 048), não existe o que digitar errado.
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
 * @param {string} [args.url='/fiscalizacao/catalogo/linhas'] — de onde ler o
 *   catálogo. Cada módulo lê a MESMA tabela pela própria porta (menor
 *   privilégio): /fiscalizacao/linhas, /escala-fiscais/linhas,
 *   /portaria/catalogo/linhas.
 * @param {'codigo'|'id'} [args.chave='codigo'] — campo que identifica a linha
 *   na seleção (o posto da Escala grava pelo id).
 * @param {(selecao: Set<string>) => void} [args.onMudar]
 * @returns {{ carregar: (selecionadasIniciais?: Iterable<string>) => Promise<void>,
 *             recarregar: () => Promise<void>, marcar: (valor: string) => void,
 *             getSelecao: () => Set<string>, getCatalogo: () => object[] }}
 */
export function criarSeletorLinhas({
    containerLista, campoBusca, multiplo = false, url = '/fiscalizacao/catalogo/linhas',
    chave = 'codigo', onMudar,
}) {
    let catalogo = [];
    let catalogoOk = true;
    let termoBusca = '';
    let selecao = new Set();

    function filtrar() {
        const termo = termoBusca.trim().toLowerCase();
        if (!termo) return catalogo;
        return catalogo.filter(l =>
            l.codigo.toLowerCase().includes(termo) || (l.nome || '').toLowerCase().includes(termo)
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
            <button type="button" class="linhas-seletor-item ${selecao.has(String(l[chave])) ? 'active' : ''}" data-linha="${escapeHtml(String(l[chave]))}">
                <span class="linhas-seletor-codigo">${escapeHtml(l.codigo)}</span>
                ${l.nome ? `<span class="linhas-seletor-nome">${escapeHtml(l.nome)}</span>` : ''}
            </button>
        `).join('');
        containerLista.querySelectorAll('[data-linha]').forEach(btn => {
            btn.addEventListener('click', () => {
                const valor = btn.dataset.linha;
                if (multiplo) {
                    if (selecao.has(valor)) selecao.delete(valor);
                    else selecao.add(valor);
                } else {
                    selecao = new Set([valor]);
                }
                render();
                if (onMudar) onMudar(new Set(selecao));
            });
        });
    }

    async function lerCatalogo() {
        try {
            catalogo = await apiGet(url);
            catalogoOk = true;
        } catch {
            catalogo = [];
            catalogoOk = false;
        }
    }

    if (campoBusca) {
        campoBusca.addEventListener('input', () => {
            termoBusca = campoBusca.value;
            render();
        });
    }

    return {
        async carregar(selecionadasIniciais) {
            selecao = new Set([...(selecionadasIniciais || [])].map(String));
            termoBusca = '';
            if (campoBusca) campoBusca.value = '';
            containerLista.innerHTML = '<div class="patio-loading">Carregando…</div>';
            await lerCatalogo();
            render();
        },
        // Relê o catálogo sem perder a seleção nem a busca (linha recém-cadastrada).
        async recarregar() {
            await lerCatalogo();
            render();
        },
        marcar(valor) {
            if (multiplo) selecao.add(String(valor));
            else selecao = new Set([String(valor)]);
            render();
            if (onMudar) onMudar(new Set(selecao));
        },
        getSelecao: () => new Set(selecao),
        getCatalogo: () => catalogo.slice(),
    };
}
