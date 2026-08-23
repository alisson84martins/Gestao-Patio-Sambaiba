/*
 * identidade.js — busca por RE compartilhada entre módulos (Bloco A2)
 * -----------------------------------------------------------------------
 * Consome GET /identidade/re/{re} (nome, origem, funções, veículo
 * particular — ⛔ nunca cpf/rg/cnh/telefone). Um RE, um lugar de busca:
 * nenhum módulo deveria reimplementar "digitar RE, ver nome" com a própria
 * chamada — quem já tem autopreenchimento mais rico e próprio (ver
 * ocorrencia.form.js::autopreencherPessoa, que também traz documentos via
 * /ocorrencias/autopreencher/pessoa) continua com o endpoint dele.
 *
 * ⚠️ Exige sessão autenticada (JWT via api.js) — não funciona nas páginas
 * públicas por token (pre-ocorrencia.page.js), que não têm apiGet.
 */
import { apiGet, ApiError } from './api.js';

export async function buscarPorRe(re) {
    const limpo = (re || '').trim();
    if (!limpo) return null;
    return apiGet(`/identidade/re/${encodeURIComponent(limpo)}`);
}

/**
 * Liga a busca por RE a um input: dispara no blur, preenche só campo
 * vazio (nunca sobrescreve o que a pessoa digitou) e deixa tudo editável
 * — mesma regra do autopreenchimento da Ocorrência (10/08/2026).
 *
 * @param {HTMLInputElement} inputRe
 * @param {object} [opcoes]
 * @param {(dados: object) => void} [opcoes.onEncontrado] — dados = IdentidadeReResponse
 * @param {() => void} [opcoes.onNaoEncontrado]
 * @param {Record<string, HTMLInputElement>} [opcoes.campos] — nome do campo
 *   da resposta (hoje só faz sentido pra "nome") -> input a preencher se
 *   estiver vazio. Campos compostos (funcoes, veiculo_particular) são
 *   responsabilidade do onEncontrado, não deste mapeamento.
 * @param {(re: string) => Promise<object|null>} [opcoes.buscar] — sobrescreve
 *   a busca padrão; permite reusar este fiação (blur + "só campo vazio")
 *   com um endpoint mais rico (caso da Ocorrência).
 * @param {number} [opcoes.minimoDigitos]
 */
export function ligarAutopreenchimentoRe(inputRe, opcoes = {}) {
    const {
        onEncontrado, onNaoEncontrado, campos = {}, buscar = buscarPorRe, minimoDigitos = 3,
    } = opcoes;

    async function disparar() {
        const re = inputRe.value.trim();
        if (re.length < minimoDigitos) return;
        let dados;
        try {
            dados = await buscar(re);
        } catch (err) {
            if (err instanceof ApiError && err.status === 401) return;
            console.error('[identidade] erro ao buscar por RE:', err);
            return;
        }
        if (!dados || !dados.encontrado) {
            if (onNaoEncontrado) onNaoEncontrado();
            return;
        }
        for (const [campo, el] of Object.entries(campos)) {
            const valor = dados[campo];
            if (el && !el.value.trim() && typeof valor === 'string' && valor) {
                el.value = valor;
            }
        }
        if (onEncontrado) onEncontrado(dados);
    }

    inputRe.addEventListener('blur', disparar);
}
