/*
 * Configuração global do Frontend V3 — Sambaíba Transportes
 * --------------------------------------------------------
 * Define a URL base da API conforme o ambiente em que o V3 está rodando.
 *
 * Como funciona o auto-detect:
 *   - Se a página está aberta em localhost / 127.0.0.1 / 0.0.0.0
 *       → API local (FastAPI rodando no PC do dev via `fastapi dev`)
 *   - Caso contrário (GitHub Pages, servidor hospedado, IP de LAN)
 *       → API de produção
 *
 * Quando você contratar o servidor (Fase 6 do projeto), troque APENAS
 * a constante PROD_API_URL abaixo. Não precisa mexer em mais lugar
 * nenhum do código — todos os módulos importam daqui.
 */

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '0.0.0.0', '']);

// URL da API quando rodando local (via fastapi dev)
const LOCAL_API_URL = 'http://127.0.0.1:8000';

// URL da API quando hospedada — TROCAR AQUI quando contratar o servidor.
// Exemplos do que pode entrar aqui no futuro:
//   - 'https://api.gestao-patio-sambaiba.com.br'
//   - 'https://gestao-patio-api.onrender.com'
//   - 'https://gestao-patio.up.railway.app'
const PROD_API_URL = 'https://api.gestao-patio-sambaiba.com.br'; // placeholder — atualizar na Fase 6

const hostname = window.location.hostname;
const isLocal = LOCAL_HOSTS.has(hostname);

export const API_BASE_URL = isLocal ? LOCAL_API_URL : PROD_API_URL;
export const IS_LOCAL = isLocal;
export const APP_VERSION = '3.0.0-alpha';

// Tempo de polling padrão (ms) para telas que atualizam em tempo real.
// Será usado nas próximas fases (visualização do pátio).
export const POLLING_INTERVAL_MS = 5000;

// Chave usada no localStorage para o JWT
export const TOKEN_KEY = 'patio_v3_jwt';
// Chave usada no localStorage para o usuário corrente
export const USER_KEY = 'patio_v3_user';

// Log discreto pra facilitar debug em campo (operador F12 → Console)
console.info(
    `[Sambaíba V3 ${APP_VERSION}] Ambiente: ${isLocal ? 'LOCAL' : 'PRODUÇÃO'} · API: ${API_BASE_URL}`,
);
