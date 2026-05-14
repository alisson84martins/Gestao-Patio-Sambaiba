/*
 * Controlador da tela de login (index.html)
 * -----------------------------------------
 * - Verifica se o usuário já tem sessão válida; se sim, manda pra patio.html
 * - Faz health check da API e mostra o status no rodapé
 * - Trata o submit do formulário
 */

import { login, redirectIfAuthenticated, ApiError } from './auth.js';
import { checkApiHealth } from './api.js';
import { API_BASE_URL, IS_LOCAL, APP_VERSION } from './config.js';

// Se já tem sessão válida, redireciona antes mesmo de mostrar a tela
redirectIfAuthenticated();

// ────────── Referências ──────────
const form = document.getElementById('login-form');
const inputRe = document.getElementById('input-re');
const inputSenha = document.getElementById('input-senha');
const btnEntrar = document.getElementById('btn-entrar');
const alertBox = document.getElementById('login-alert');
const toggleSenha = document.getElementById('toggle-senha');
const apiStatusDot = document.getElementById('api-status-dot');
const apiStatusText = document.getElementById('api-status-text');
const apiEnvText = document.getElementById('api-env-text');

// ────────── Helpers de UI ──────────

function showAlert(message, kind = 'error') {
    alertBox.className = `login-alert show ${kind}`;
    alertBox.textContent = message;
}

function clearAlert() {
    alertBox.className = 'login-alert';
    alertBox.textContent = '';
}

function setLoading(isLoading) {
    btnEntrar.disabled = isLoading;
    inputRe.disabled = isLoading;
    inputSenha.disabled = isLoading;
    btnEntrar.innerHTML = isLoading
        ? '<span class="spinner"></span>Entrando...'
        : 'Entrar';
}

// ────────── Mostrar/esconder senha ──────────
toggleSenha.addEventListener('click', () => {
    const isPwd = inputSenha.type === 'password';
    inputSenha.type = isPwd ? 'text' : 'password';
    toggleSenha.textContent = isPwd ? '🙈' : '👁';
});

// ────────── Submit do formulário ──────────
form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearAlert();

    const re = inputRe.value.trim();
    const senha = inputSenha.value;

    if (!re || !senha) {
        showAlert('Preencha RE e senha.', 'warning');
        return;
    }

    setLoading(true);
    try {
        const user = await login(re, senha);
        // Sucesso — mensagem rápida e redireciono
        showAlert(`Bem-vindo, ${user.nome.split(' ')[0]}.`, 'success');
        setTimeout(() => {
            window.location.href = 'patio.html';
        }, 400);
    } catch (err) {
        setLoading(false);
        if (err instanceof ApiError) {
            if (err.status === 0) {
                showAlert('Servidor offline. Verifique sua conexão ou avise o suporte.', 'error');
            } else if (err.status === 401) {
                showAlert('RE ou senha incorretos. Tente novamente.', 'error');
                inputSenha.value = '';
                inputSenha.focus();
            } else if (err.status === 403) {
                showAlert(err.message || 'Acesso bloqueado. Procure o administrador.', 'error');
            } else {
                showAlert(err.message || 'Erro inesperado. Tente novamente.', 'error');
            }
        } else {
            showAlert('Erro inesperado. Tente novamente.', 'error');
            console.error(err);
        }
    }
});

// ────────── Health check no carregamento ──────────
(async () => {
    apiEnvText.textContent = `${IS_LOCAL ? 'AMBIENTE LOCAL' : 'PRODUÇÃO'} · ${API_BASE_URL.replace(/^https?:\/\//, '')}`;
    const result = await checkApiHealth();
    if (result.ok) {
        apiStatusDot.classList.add('online');
        const totalTab = result.data?.total_tabelas;
        apiStatusText.textContent = `Servidor online${totalTab ? ` · ${totalTab} tabelas` : ''}`;
    } else {
        apiStatusDot.classList.add('offline');
        apiStatusText.textContent = 'Servidor offline';
        showAlert(
            IS_LOCAL
                ? 'Backend local não respondeu. Rode `fastapi dev app/main.py` no terminal e atualize a página.'
                : 'Não foi possível alcançar o servidor. Verifique sua internet.',
            'warning',
        );
    }
})();
