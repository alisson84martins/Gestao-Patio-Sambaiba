/*
 * Módulo de autenticação do Frontend V3
 * -------------------------------------
 * Encapsula login, logout, persistência do JWT e checagem de sessão.
 *
 * Estado é guardado no localStorage:
 *   - patio_v3_jwt   → token JWT (Bearer)
 *   - patio_v3_user  → JSON com dados do usuário corrente (de /auth/me)
 *
 * Decisões:
 *   - O backend devolve `expires_in` (8h por padrão). A gente armazena
 *     `expires_at` (timestamp absoluto) pra conseguir checar validade
 *     sem precisar decodificar o JWT no front.
 *   - Toda página protegida deve chamar requireAuth() no topo.
 */

import { apiPost, apiGet, ApiError } from './api.js';
import { TOKEN_KEY, USER_KEY, IDENTIDADE_PENDENTE_KEY, PAGINA_CONFIRMAR_IDENTIDADE } from './config.js';
import { destinoAposLogin } from './sessao.js';

const EXPIRES_AT_KEY = 'patio_v3_expires_at';

/**
 * Login: chama POST /auth/login, guarda token + dados do usuário.
 * Retorna o usuário em caso de sucesso. Lança ApiError em caso de falha.
 *
 * Login provisório (motorista criado pelo sistema, `confirmar_identidade`):
 * NÃO busca /auth/me (o backend devolve 403 IDENTIDADE_PENDENTE até a
 * confirmação) — guarda { nome, re } e devolve { identidadePendente: true }
 * para a tela de login mandar para confirmar-identidade.html.
 */
export async function login(re, senha) {
    // 1. Pega o token
    const tokenResp = await apiPost('/auth/login', { re, senha });

    const { access_token, expires_in } = tokenResp;
    const expiresAt = Date.now() + (expires_in * 1000);

    // Grava o token ANTES de buscar /auth/me — porque /auth/me precisa do header
    localStorage.setItem(TOKEN_KEY, access_token);
    localStorage.setItem(EXPIRES_AT_KEY, String(expiresAt));

    if (tokenResp.confirmar_identidade) {
        localStorage.removeItem(USER_KEY);
        localStorage.setItem(IDENTIDADE_PENDENTE_KEY, JSON.stringify({
            nome: tokenResp.nome, re: tokenResp.re,
        }));
        return { identidadePendente: true, nome: tokenResp.nome ?? '' };
    }
    localStorage.removeItem(IDENTIDADE_PENDENTE_KEY);

    return carregarUsuario();
}

/**
 * Busca /auth/me e guarda na sessão. Usado no login e logo depois do
 * "Sou eu" da confirmação de identidade.
 */
export async function carregarUsuario() {
    try {
        const user = await apiGet('/auth/me');
        localStorage.setItem(USER_KEY, JSON.stringify(user));
        return user;
    } catch (err) {
        // Se /auth/me falhar logo após login, algo tá estranho — limpa tudo
        logout();
        throw err;
    }
}

/** { nome, re } do login provisório ainda não confirmado, ou null. */
export function identidadePendente() {
    const raw = localStorage.getItem(IDENTIDADE_PENDENTE_KEY);
    if (!raw) return null;
    try {
        return JSON.parse(raw);
    } catch {
        return null;
    }
}

export function limparIdentidadePendente() {
    localStorage.removeItem(IDENTIDADE_PENDENTE_KEY);
}

/**
 * Apaga toda a sessão local. Não precisa chamar nada no backend
 * (JWT é stateless — basta o cliente esquecer o token).
 */
export function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(EXPIRES_AT_KEY);
    localStorage.removeItem(IDENTIDADE_PENDENTE_KEY);
}

/**
 * Retorna o usuário corrente (do localStorage) ou null se não autenticado.
 */
export function getCurrentUser() {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
        return JSON.parse(raw);
    } catch {
        return null;
    }
}

/**
 * true se o token existe E ainda não expirou (pelo cálculo local).
 * A validação real é feita pelo backend a cada chamada (401 derruba a sessão).
 */
export function isAuthenticated() {
    const token = localStorage.getItem(TOKEN_KEY);
    const expiresAt = Number(localStorage.getItem(EXPIRES_AT_KEY));
    if (!token || !expiresAt) return false;
    return Date.now() < expiresAt;
}

/**
 * Uso em páginas protegidas: chama no topo do <script>.
 * Se não tá logado, redireciona pra tela de login.
 */
export function requireAuth() {
    if (!isAuthenticated()) {
        window.location.replace('index.html');
        return false;
    }
    // Login provisório ainda sem "Sou eu": nenhum módulo antes da confirmação.
    if (identidadePendente()) {
        window.location.replace(PAGINA_CONFIRMAR_IDENTIDADE);
        return false;
    }
    return true;
}

/**
 * Se já tá logado, redireciona pro destino da pessoa (destinoAposLogin()).
 * Usado na própria tela de login pra evitar exibir o form pra quem já tem sessão.
 */
export function redirectIfAuthenticated() {
    if (isAuthenticated()) {
        window.location.replace(identidadePendente() ? PAGINA_CONFIRMAR_IDENTIDADE : destinoAposLogin());
    }
}

// Re-exporta ApiError pra quem importar auth.js poder tratar erros
export { ApiError };
