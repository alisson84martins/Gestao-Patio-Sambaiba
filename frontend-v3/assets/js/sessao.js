/*
 * Módulo de sessão do Frontend V3
 * --------------------------------
 * Camada única sobre o retorno de /auth/me (guardado em localStorage por
 * auth.js, chave `patio_v3_user`). Todo código que precisa saber "o que
 * essa pessoa pode fazer" ou "em que módulo ela está" passa por aqui —
 * nunca lê `patio_v3_user` nem `perfil` diretamente.
 *
 * Duas coisas que o backend agora trata como separadas (migration 013):
 *   - a QUAIS módulos a pessoa tem acesso  → modulos() / temModulo()
 *   - em QUAL módulo ela aterrissa ao entrar → moduloPadrao() / destinoAposLogin()
 * Quem tem acesso a mais de um módulo sempre pode trocar pelo menu,
 * independentemente de ter entrado direto (ver moduloAtual/setModuloAtual).
 */

import { USER_KEY } from './config.js';

const MODULO_ATUAL_KEY = 'patio_v3_modulo_atual';

// Página inicial de cada módulo. Só os módulos com página pronta entram
// em PAGINAS_PRONTAS — os demais caem em modulos.html com aviso, nunca
// numa tela de erro 404 (ver paginaModulo()).
const PAGINA_INICIAL = {
    PATIO: 'patio.html',
    COORDENADORIA: 'ocorrencias.html',   // ainda não existe — Etapa 6
    FISCALIZACAO: 'fiscal-painel.html',   // painel do coordenador (Bloco E)
    FISCALIZACAO_FISCAL: 'fiscal.html',   // registro do fiscal em campo (Bloco D)
    ADMINISTRACAO: 'cadastros.html',
    // 🔴 Sem isto (e sem 'PORTARIA' em PAGINAS_PRONTAS logo abaixo), o
    // controlador loga e cai em modulos.html?indisponivel=PORTARIA — porque
    // funcao.modulo_padrao='PORTARIA' já foi gravado pela migration 024, e
    // paginaModulo() só resolve o que está em PAGINAS_PRONTAS (§4.4).
    PORTARIA: 'portaria.html',
};

const PAGINAS_PRONTAS = new Set(['PATIO', 'ADMINISTRACAO', 'COORDENADORIA', 'PORTARIA', 'FISCALIZACAO']);

// Módulos cuja página inicial depende do acesso da pessoa. FISCALIZACAO tem
// duas portas: quem tem `fiscalizacao_painel` (o coordenador) aterrissa no
// painel (Bloco E); quem só tem escrita em `fiscalizacao` (o fiscal)
// aterrissa em fiscal.html (Bloco D). Quem tem os dois vai para o painel.
// Sem nenhum dos dois, cai no aviso de indisponível, como antes.
const PAGINA_CONDICIONAL = {
    FISCALIZACAO: () => {
        if (podeLer('fiscalizacao_painel')) return PAGINA_INICIAL.FISCALIZACAO;
        if (podeEscrever('fiscalizacao')) return PAGINA_INICIAL.FISCALIZACAO_FISCAL;
        return null;
    },
};

function _dadosSessao() {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
        return JSON.parse(raw);
    } catch {
        return null;
    }
}

/** Dados básicos da pessoa logada: { id, re, nome, funcionario_id }. */
export function usuario() {
    const dados = _dadosSessao();
    if (!dados) return null;
    const { id, re, nome, funcionario_id } = dados;
    return { id, re, nome, funcionario_id };
}

/** true se a pessoa tem leitura no recurso (acesso efetivo vindo de /auth/me). */
export function podeLer(recurso) {
    return Boolean(_dadosSessao()?.acesso?.[recurso]?.ler);
}

/** true se a pessoa tem escrita no recurso. */
export function podeEscrever(recurso) {
    return Boolean(_dadosSessao()?.acesso?.[recurso]?.escrever);
}

/** true se a pessoa tem a função (código) entre suas funções ativas. */
export function temFuncao(codigo) {
    return Boolean(_dadosSessao()?.funcoes?.includes(codigo));
}

/** Lista de módulos liberados — para a nav e para a tela de seleção. */
export function modulos() {
    return _dadosSessao()?.modulos ?? [];
}

/** true se a pessoa tem acesso a pelo menos um recurso do módulo. */
export function temModulo(codigo) {
    return modulos().some((m) => m.modulo === codigo);
}

/** Módulo de destino do login (vw_destino_login). Nulo = tela de escolha. */
export function moduloPadrao() {
    return _dadosSessao()?.modulo_padrao ?? null;
}

/** Módulo em que a pessoa está navegando agora (escolha feita em modulos.html). */
export function moduloAtual() {
    return localStorage.getItem(MODULO_ATUAL_KEY);
}

/** Guarda a escolha de módulo corrente. */
export function setModuloAtual(codigo) {
    localStorage.setItem(MODULO_ATUAL_KEY, codigo);
}

/**
 * Página inicial do módulo, ou modulos.html com aviso se a página ainda
 * não existir. Usado por destinoAposLogin(), pela troca de módulo em
 * nav.js e pelos cartões de modulos.page.js — não duplique esta lista.
 */
export function paginaModulo(codigo) {
    const condicional = PAGINA_CONDICIONAL[codigo]?.();
    if (condicional) return condicional;

    return PAGINAS_PRONTAS.has(codigo)
        ? PAGINA_INICIAL[codigo]
        : `modulos.html?indisponivel=${encodeURIComponent(codigo)}`;
}

/**
 * Resolve pra onde mandar a pessoa depois do login, nesta ordem:
 *   1. modulo_padrao preenchido → página inicial daquele módulo
 *   2. só um módulo liberado    → página inicial dele
 *   3. senão                    → modulos.html (tela de escolha)
 */
export function destinoAposLogin() {
    const padrao = moduloPadrao();
    if (padrao) {
        setModuloAtual(padrao);
        return paginaModulo(padrao);
    }

    const lista = modulos();
    if (lista.length === 1) {
        setModuloAtual(lista[0].modulo);
        return paginaModulo(lista[0].modulo);
    }

    return 'modulos.html';
}
