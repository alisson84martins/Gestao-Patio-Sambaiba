/**
 * config.js — Configuração global da API
 * Auto-detecta ambiente: localhost → dev, qualquer outro host → prod
 */

const _host = window.location.hostname;

const API_BASE_URL = (_host === 'localhost' || _host === '127.0.0.1')
  ? 'http://localhost:8000'   // dev local
  : 'https://api.seudominio.com.br';   // TODO: substituir pelo domínio real no deploy

// Chave do token no localStorage
export const TOKEN_KEY = 'sambaiba_token';
export const PERFIL_KEY = 'sambaiba_perfil';
export const NOME_KEY   = 'sambaiba_nome';

export { API_BASE_URL };

/**
 * Salva o resultado do login no localStorage.
 * @param {Object} tokenResponse - resposta de POST /auth/login
 */
export function salvarSessao(tokenResponse, nome) {
  localStorage.setItem(TOKEN_KEY, tokenResponse.access_token);
  localStorage.setItem(PERFIL_KEY, tokenResponse.perfil ?? '');
  localStorage.setItem(NOME_KEY, nome ?? '');
}

/**
 * Limpa a sessão (logout).
 */
export function limparSessao() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(PERFIL_KEY);
  localStorage.removeItem(NOME_KEY);
}

/**
 * Retorna o token salvo ou null.
 */
export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

/**
 * Faz fetch autenticado para a API.
 * Lança erro se resposta não for 2xx.
 */
export async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...(options.headers ?? {}),
  };
  const res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (res.status === 401) {
    limparSessao();
    window.location.href = '/login.html';
    return;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Erro ${res.status}`);
  }
  if (res.status === 204) return null;
  return res.json();
}
