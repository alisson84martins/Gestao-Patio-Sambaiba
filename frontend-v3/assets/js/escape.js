/*
 * escapeHtml — módulo único (SEV-07).
 * ------------------------------------
 * Antes desta consolidação a mesma função existia copiada em 4 arquivos
 * (importacao.js, ocorrencia.form.js, ocorrencia.imprimir.js,
 * ocorrencias.page.js) e faltava em outros 12 que também montam innerHTML
 * com texto livre. Escrever a mesma máscara em mais de um lugar faz elas
 * divergirem em silêncio — mesmo precedente já registrado pra mascaras.js.
 *
 * O JWT deste sistema vive em localStorage, sem revogação — um <script>
 * injetado por um nome de funcionário ou uma observação de ocorrência
 * roda no navegador de quem abrir a tela, o dia inteiro. Use sempre que
 * texto vindo de fora (nome, CPF, endereço, observação, busca) entrar em
 * innerHTML. Não use em marcação estática montada pelo próprio código —
 * escapar isso não protege nada e só quebra o HTML.
 */
export function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
