/*
 * Tela de seleção de módulo — Frontend V3
 * ------------------------------------------
 * Mostra um cartão por módulo liberado (vw_modulos_usuario). Quem só tem
 * um módulo com página pronta é redirecionado direto, sem passar por
 * aqui — esta tela só aparece pra quem precisa escolher (ou pra quem
 * caiu num módulo cuja página ainda não existe, com aviso).
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { modulos, paginaModulo, setModuloAtual } from './sessao.js';

if (!requireAuth()) throw new Error('Não autenticado');

const NOMES_MODULO = {
    PATIO: 'Pátio',
    COORDENADORIA: 'Coordenadoria',
    FISCALIZACAO: 'Fiscalização',
    ADMINISTRACAO: 'Administração',
    MANUTENCAO: 'Manutenção',
};

function initHeader() {
    const user = getCurrentUser();
    const elUserName = document.getElementById('user-name');
    const elUserMeta = document.getElementById('user-meta');
    if (user) {
        elUserName.textContent = user.nome ?? user.re ?? '—';
        elUserMeta.textContent = user.re ?? '—';
    }
    document.getElementById('btn-logout').addEventListener('click', () => {
        logout();
        window.location.replace('index.html');
    });
}

function mostrarAvisoIndisponivel() {
    const params = new URLSearchParams(window.location.search);
    const codigo = params.get('indisponivel');
    if (!codigo) return;

    const elAviso = document.getElementById('modulos-aviso');
    const nome = NOMES_MODULO[codigo] ?? codigo;
    elAviso.textContent = `O módulo ${nome} ainda está em construção. Escolha outro módulo abaixo.`;
    elAviso.style.display = '';
}

function renderCards() {
    const elGrid = document.getElementById('modulos-grid');
    const lista = modulos();

    if (lista.length === 0) {
        elGrid.innerHTML = '<p class="patio-loading">Nenhum módulo liberado. Procure o administrador.</p>';
        return;
    }

    elGrid.innerHTML = '';
    for (const m of lista) {
        const card = document.createElement('a');
        card.className = 'modulo-card';
        card.href = paginaModulo(m.modulo);
        // Grava a escolha ANTES de navegar — antes só o login gravava
        // (destinoAposLogin), então quem trocava de módulo por aqui saía
        // com moduloAtual() apontando pro módulo antigo, e a barra de
        // navegação (nav.js) filtrava errado até o próximo login.
        card.addEventListener('click', () => setModuloAtual(m.modulo));

        const nome = document.createElement('div');
        nome.className = 'modulo-card-nome';
        nome.textContent = m.modulo_nome;
        card.appendChild(nome);

        if (m.modulo_descricao) {
            const desc = document.createElement('div');
            desc.className = 'modulo-card-desc';
            desc.textContent = m.modulo_descricao;
            card.appendChild(desc);
        }

        if (!m.pode_escrever) {
            const badge = document.createElement('span');
            badge.className = 'modulo-card-badge';
            badge.textContent = 'Somente leitura';
            card.appendChild(badge);
        }

        elGrid.appendChild(card);
    }
}

function init() {
    initHeader();

    // Só um módulo com página pronta → nem mostra a tela, entra direto.
    const lista = modulos();
    if (lista.length === 1) {
        const destino = paginaModulo(lista[0].modulo);
        if (!destino.startsWith('modulos.html')) {
            window.location.replace(destino);
            return;
        }
    }

    mostrarAvisoIndisponivel();
    renderCards();
}

init();
