/*
 * Controlador de confirmar-identidade.html
 * ----------------------------------------
 * 1º acesso de login PROVISORIA — o motorista ganhou acesso a partir dos
 * dados de uma ocorrência (backend/app/services/acesso_motorista.py). A
 * terceira conferência é a própria pessoa (D6, 02/11/2026):
 *
 *   [Sou eu]      → cria a senha própria e entra no sistema;
 *   [Não sou eu]  → o login é desativado (o backend registra no log) e a
 *                   sessão local é apagada.
 *
 * Até a resposta, o backend recusa TODA rota com 403 IDENTIDADE_PENDENTE —
 * api.js traz a pessoa de volta para cá de qualquer página.
 */

import { apiPost, ApiError } from './api.js';
import {
    isAuthenticated, identidadePendente, limparIdentidadePendente, carregarUsuario, logout,
} from './auth.js';
import { destinoAposLogin } from './sessao.js';

const pendente = identidadePendente();

// Sem sessão, ou sem o nome a confirmar (sessão antiga/limpa): a pergunta
// não faz sentido sem o nome — volta para o login, que grava de novo.
if (!isAuthenticated() || !pendente) {
    logout();
    window.location.replace('index.html');
    throw new Error('Sem identidade pendente para confirmar');
}

const passos = ['passo-pergunta', 'passo-senha', 'passo-negar', 'passo-bloqueado'];

function mostrar(passo) {
    passos.forEach(id => { document.getElementById(id).hidden = id !== passo; });
}

function alerta(id, mensagem, tipo = 'error') {
    const el = document.getElementById(id);
    el.className = mensagem ? `login-alert show ${tipo}` : 'login-alert';
    el.textContent = mensagem || '';
}

function ocupado(botao, sim, texto) {
    botao.disabled = sim;
    if (texto) botao.textContent = texto;
}

// textContent — nunca innerHTML: o nome veio digitado numa ocorrência.
document.getElementById('identidade-nome').textContent = pendente.nome || '—';
document.getElementById('identidade-re').textContent = pendente.re || '—';

// ─── Sou eu ──────────────────────────────────────────────────────
const inputSenha = document.getElementById('input-senha-nova');
const inputRepetida = document.getElementById('input-senha-repetida');
const btnSalvar = document.getElementById('btn-salvar-senha');

document.getElementById('btn-sou-eu').addEventListener('click', () => {
    alerta('alerta-senha', '');
    mostrar('passo-senha');
    inputSenha.focus();
});

document.getElementById('btn-voltar-senha').addEventListener('click', () => {
    inputSenha.value = '';
    inputRepetida.value = '';
    mostrar('passo-pergunta');
});

document.getElementById('passo-senha').addEventListener('submit', async (e) => {
    e.preventDefault();
    alerta('alerta-senha', '');

    const senha = inputSenha.value;
    if (senha.length < 6) {
        return alerta('alerta-senha', 'A senha precisa ter pelo menos 6 caracteres.', 'warning');
    }
    if (senha !== inputRepetida.value) {
        inputRepetida.value = '';
        inputRepetida.focus();
        return alerta('alerta-senha', 'As duas senhas não são iguais.', 'warning');
    }

    ocupado(btnSalvar, true, 'Salvando...');
    try {
        await apiPost('/auth/confirmar-identidade', { sou_eu: true, senha_nova: senha });
        limparIdentidadePendente();
        await carregarUsuario();
        window.location.replace(destinoAposLogin());
    } catch (err) {
        ocupado(btnSalvar, false, 'Salvar e entrar');
        alerta('alerta-senha', err instanceof ApiError && err.message
            ? err.message
            : 'Não foi possível salvar. Tente novamente.');
    }
});

// ─── Não sou eu ──────────────────────────────────────────────────
const btnConfirmarNegar = document.getElementById('btn-confirmar-negar');

document.getElementById('btn-nao-sou-eu').addEventListener('click', () => {
    alerta('alerta-negar', '');
    mostrar('passo-negar');
});

document.getElementById('btn-voltar-negar').addEventListener('click', () => mostrar('passo-pergunta'));

btnConfirmarNegar.addEventListener('click', async () => {
    ocupado(btnConfirmarNegar, true, 'Bloqueando...');
    try {
        await apiPost('/auth/confirmar-identidade', { sou_eu: false });
        logout();
        mostrar('passo-bloqueado');
    } catch (err) {
        ocupado(btnConfirmarNegar, false, 'Sim, não sou eu');
        alerta('alerta-negar', err instanceof ApiError && err.message
            ? err.message
            : 'Não foi possível concluir. Tente novamente.');
    }
});
