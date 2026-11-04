/*
 * sugestao-cidade.js — Sugestão de cidade enquanto digita (base IBGE local)
 * -----------------------------------------------------------------------------
 * Lê frontend-v3/assets/data/localidades-ibge.json (gerado por
 * backend/scripts/gerar_localidades_ibge.py) UMA vez e guarda em memória.
 * ⛔ Nunca chama o IBGE em tempo de uso.
 *
 * Por que componente próprio e não <datalist>: o Chrome do Android filtra as
 * opções do datalist sozinho e NÃO ignora acento — "sao" esconderia
 * "São Paulo", justo o caso mais comum. Aqui a lista é nossa: acento e
 * maiúscula não importam, a partir de 2 letras, no máximo 15 sugestões.
 *
 * Regras que não podem quebrar:
 *   - Texto livre continua aceito (distrito, cidade estrangeira, grafia do
 *     documento). A sugestão só ajuda, nunca troca nem barra o que foi digitado.
 *   - Falha ao carregar o JSON → o campo segue texto livre, sem erro na tela
 *     (mesmo princípio do initBuscaFuncionario: autocomplete é conveniência).
 *   - Escolher uma sugestão dispara 'input' no campo — quem já ouve o campo
 *     (estado da lista dinâmica, autosave) recebe como se fosse digitação.
 *   - Teclado do celular cobre a metade de baixo da tela: ao focar, o campo
 *     rola para o topo da área visível, deixando espaço para a lista abaixo.
 */

import { UFS } from './catalogos-veiculo.js';
import { escapeHtml } from './escape.js';

const URL_JSON = new URL('../data/localidades-ibge.json', import.meta.url);
const MINIMO_LETRAS = 2;
const MAXIMO_SUGESTOES = 15;

let promessaIndice = null;

export function chaveBusca(texto) {
    return (texto || '')
        .normalize('NFD')
        .replace(/[̀-ͯ]/g, '')
        .toLowerCase()
        .replace(/\s+/g, ' ')
        .trim();
}

/** Promise do índice [{nome, uf, chave}] — null se o JSON não carregar. */
export function carregarLocalidades() {
    if (!promessaIndice) {
        promessaIndice = fetch(URL_JSON)
            .then(resp => {
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                return resp.json();
            })
            .then(dados => {
                const indice = [];
                Object.entries(dados).forEach(([uf, nomes]) => {
                    nomes.forEach(nome => indice.push({ nome, uf, chave: chaveBusca(nome) }));
                });
                return indice;
            })
            .catch(err => {
                console.warn('[sugestao-cidade] base de municípios indisponível — campo segue texto livre:', err);
                return null;
            });
    }
    return promessaIndice;
}

/**
 * Nome oficial (IBGE, com acento) de uma cidade escrita de outro jeito
 * ("FORTALEZA" → "Fortaleza"). Sem correspondência exata → null.
 */
export async function nomeOficialCidade(texto, uf) {
    const indice = await carregarLocalidades();
    if (!indice || !texto) return null;
    const chave = chaveBusca(texto);
    const ufNorm = UFS.includes((uf || '').toUpperCase()) ? uf.toUpperCase() : null;
    const achados = indice.filter(c => c.chave === chave && (!ufNorm || c.uf === ufNorm));
    return achados.length === 1 ? achados[0].nome : null;
}

function filtrar(indice, termo, uf, priorizarUf) {
    const chave = chaveBusca(termo);
    if (chave.length < MINIMO_LETRAS) return [];
    const comeca = [];
    const contem = [];
    for (const c of indice) {
        if (uf && c.uf !== uf) continue;
        if (c.chave.startsWith(chave)) comeca.push(c);
        else if (c.chave.includes(chave)) contem.push(c);
    }
    const prioridade = c => (priorizarUf && c.uf === priorizarUf ? 0 : 1);
    const ordenar = lista => lista.sort((a, b) => prioridade(a) - prioridade(b));
    return [...ordenar(comeca), ...ordenar(contem)].slice(0, MAXIMO_SUGESTOES);
}

/**
 * Liga a sugestão num input de cidade.
 * @param {HTMLInputElement} input
 * @param {{ uf?: () => (string|null|undefined), priorizarUf?: string }} opcoes
 *   uf: devolve a UF escolhida no campo ao lado (filtra por ela); sem UF
 *   válida, sugere de todas mostrando a UF. priorizarUf: UF que vem primeiro.
 */
export function ligarSugestaoCidade(input, { uf, priorizarUf } = {}) {
    if (!input || input.dataset.sugestaoCidade) return;
    input.dataset.sugestaoCidade = '1';
    input.setAttribute('autocomplete', 'off');

    let caixa = input.parentElement;
    if (!caixa.classList.contains('sug-cidade')) {
        caixa = document.createElement('div');
        caixa.className = 'sug-cidade';
        input.insertAdjacentElement('beforebegin', caixa);
        caixa.appendChild(input);
    }
    const lista = document.createElement('ul');
    lista.className = 'sug-cidade-lista';
    lista.setAttribute('role', 'listbox');
    lista.hidden = true;
    caixa.appendChild(lista);

    let itens = [];
    let ativo = -1;
    let escolhendo = false;

    const fechar = () => { lista.hidden = true; ativo = -1; };

    const marcarAtivo = novo => {
        ativo = novo;
        lista.querySelectorAll('li').forEach((li, i) => li.classList.toggle('ativo', i === ativo));
    };

    const escolher = idx => {
        const c = itens[idx];
        if (!c) return;
        escolhendo = true;
        input.value = c.nome;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        escolhendo = false;
        fechar();
    };

    const atualizar = async () => {
        if (escolhendo) return;
        const indice = await carregarLocalidades();
        if (!indice) return;
        const ufAtual = (uf?.() || '').toUpperCase();
        const ufFiltro = UFS.includes(ufAtual) ? ufAtual : null;
        itens = filtrar(indice, input.value, ufFiltro, priorizarUf);
        // Só a cidade exata já digitada → nada a sugerir.
        if (!itens.length || (itens.length === 1 && itens[0].chave === chaveBusca(input.value))) {
            fechar();
            return;
        }
        lista.innerHTML = itens.map((c, i) => `
            <li role="option" data-i="${i}">${escapeHtml(c.nome)}${ufFiltro ? '' : ` <span class="sug-cidade-uf">${c.uf}</span>`}</li>
        `).join('');
        ativo = -1;
        lista.hidden = false;
    };

    input.addEventListener('input', atualizar);
    input.addEventListener('focus', () => {
        carregarLocalidades();
        // Depois que o teclado abriu (a área visível encolhe), sobe o campo.
        setTimeout(() => {
            if (document.activeElement === input) input.scrollIntoView({ block: 'start', behavior: 'smooth' });
        }, 300);
    });
    input.addEventListener('blur', () => setTimeout(fechar, 150));
    input.addEventListener('keydown', e => {
        if (lista.hidden) return;
        if (e.key === 'ArrowDown') { e.preventDefault(); marcarAtivo(Math.min(ativo + 1, itens.length - 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); marcarAtivo(Math.max(ativo - 1, 0)); }
        else if (e.key === 'Enter' && ativo >= 0) { e.preventDefault(); escolher(ativo); }
        else if (e.key === 'Escape') fechar();
    });
    // pointerdown (não click): no celular o blur do input vem antes do
    // click e fecharia a lista antes da escolha.
    lista.addEventListener('pointerdown', e => {
        const li = e.target.closest('li[data-i]');
        if (!li) return;
        e.preventDefault();
        escolher(Number(li.dataset.i));
    });
}
