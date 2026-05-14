/*
 * Cliente HTTP centralizado do Frontend V3
 * ----------------------------------------
 * Todas as chamadas à API FastAPI passam por aqui. Responsabilidades:
 *
 *   1. Injetar o header `Authorization: Bearer <token>` automaticamente
 *      quando há JWT salvo no localStorage.
 *   2. Tratar 401 (token expirado ou inválido) limpando a sessão e
 *      redirecionando pro login.
 *   3. Padronizar o tratamento de erro (extrai `detail` do FastAPI).
 *   4. Expor `apiGet`, `apiPost`, `apiPut`, `apiPatch`, `apiDelete`.
 *
 * NÃO usar fetch() direto em outros módulos — sempre passar por aqui.
 */

import { API_BASE_URL, TOKEN_KEY, USER_KEY } from './config.js';

/**
 * Erro padronizado da API. Mantém o status HTTP e a mensagem do backend.
 */
export class ApiError extends Error {
    constructor(status, message, body) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.body = body;
    }
}

function getToken() {
    return localStorage.getItem(TOKEN_KEY);
}

function clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
}

/**
 * Redireciona pra tela de login preservando, se possível, a página atual
 * pra voltar pra ela depois do login. Em V3 só temos login + patio por
 * enquanto, mas já fica preparado.
 */
function redirectToLogin() {
    clearSession();
    // Evita loop infinito se já estiver no login
    const isLoginPage = window.location.pathname.endsWith('/index.html')
        || window.location.pathname.endsWith('/');
    if (!isLoginPage) {
        window.location.href = 'index.html';
    }
}

/**
 * Função base. Recebe path relativo (ex: '/auth/login') e options do fetch.
 * Retorna o JSON parseado ou lança ApiError.
 */
async function request(path, options = {}) {
    const url = `${API_BASE_URL}${path}`;

    const headers = {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
    };

    const token = getToken();
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    let response;
    try {
        response = await fetch(url, { ...options, headers });
    } catch (networkErr) {
        // Erro de rede (servidor offline, DNS, CORS bloqueado etc.)
        throw new ApiError(
            0,
            'Não foi possível conectar à API. Verifique se o servidor está rodando.',
            { cause: networkErr.message },
        );
    }

    // 401 → token inválido/expirado: limpa sessão e manda pro login
    if (response.status === 401) {
        redirectToLogin();
        throw new ApiError(401, 'Sessão expirada. Faça login novamente.', null);
    }

    // 204 No Content — comum em DELETE
    if (response.status === 204) {
        return null;
    }

    let payload = null;
    try {
        payload = await response.json();
    } catch {
        // Resposta sem corpo JSON (raro)
    }

    if (!response.ok) {
        const detail = payload?.detail || payload?.message || response.statusText;
        const msg = typeof detail === 'string'
            ? detail
            : 'Erro inesperado da API';
        throw new ApiError(response.status, msg, payload);
    }

    return payload;
}

export const apiGet = (path) => request(path, { method: 'GET' });
export const apiPost = (path, body) => request(path, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
});
export const apiPut = (path, body) => request(path, {
    method: 'PUT',
    body: JSON.stringify(body ?? {}),
});
export const apiPatch = (path, body) => request(path, {
    method: 'PATCH',
    body: JSON.stringify(body ?? {}),
});
export const apiDelete = (path) => request(path, { method: 'DELETE' });

/**
 * Health check rápido — usado na tela de login pra mostrar status
 * do servidor antes do operador tentar logar.
 */
export async function checkApiHealth() {
    try {
        const data = await apiGet('/health');
        return { ok: true, data };
    } catch (err) {
        return { ok: false, error: err.message };
    }
}
