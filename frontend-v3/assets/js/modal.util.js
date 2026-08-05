/*
 * Comportamento genérico de modal — Frontend V3
 * -----------------------------------------------
 * Cada tela abre/fecha seu próprio modal do jeito dela (várias funções
 * abrirModal/fecharModal espalhadas por alertas.js, cadastros.js,
 * manutencao.js, mover.chip.modal.js, ocorrencia.sinistro.js...), mas
 * todas fazem a mesma coisa no fim: alternam a classe "open" num
 * elemento ".modal-overlay". Este módulo observa essa classe e cuida,
 * num lugar só, do que toda tela esquecia:
 *   - trava a rolagem do fundo enquanto o modal está aberto e restaura
 *     ao fechar (inclusive fechando pelo Esc ou clique no overlay)
 *   - rola o modal pro topo ao abrir
 *   - devolve o foco pro elemento que abriu o modal, ao fechar
 *   - mantém o campo focado visível quando o teclado do celular abre
 * Não duplica isso em cada arquivo — observa o DOM e funciona pra
 * qualquer .modal-overlay existente ou criado depois.
 */

let gatilho = null;

function modaisAbertos() {
    return document.querySelectorAll('.modal-overlay.open');
}

function aoAbrir(overlay) {
    gatilho = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.classList.add('modal-open');
    const modal = overlay.querySelector('.modal');
    if (modal) modal.scrollTop = 0;
}

function aoFechar() {
    if (modaisAbertos().length > 0) return;
    document.body.classList.remove('modal-open');
    if (gatilho && document.contains(gatilho)) gatilho.focus();
    gatilho = null;
}

function observarOverlay(overlay) {
    let estavaAberto = overlay.classList.contains('open');
    new MutationObserver(() => {
        const aberto = overlay.classList.contains('open');
        if (aberto && !estavaAberto) aoAbrir(overlay);
        if (!aberto && estavaAberto) aoFechar();
        estavaAberto = aberto;
    }).observe(overlay, { attributes: true, attributeFilter: ['class'] });
}

document.querySelectorAll('.modal-overlay').forEach(observarOverlay);

// Cobre modal inserido no DOM depois do carregamento inicial.
new MutationObserver(mutacoes => {
    for (const m of mutacoes) {
        m.addedNodes.forEach(node => {
            if (!(node instanceof HTMLElement)) return;
            if (node.classList.contains('modal-overlay')) observarOverlay(node);
            node.querySelectorAll?.('.modal-overlay').forEach(observarOverlay);
        });
    }
}).observe(document.body, { childList: true, subtree: true });

// Esc fecha qualquer modal aberto na página.
document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    modaisAbertos().forEach(overlay => overlay.classList.remove('open'));
});

// Campo em foco dentro de modal aberto fica visível com o teclado do
// celular ocupando a metade de baixo da tela.
document.addEventListener('focusin', e => {
    const alvo = e.target;
    if (!(alvo instanceof HTMLElement)) return;
    if (!alvo.matches('input, select, textarea')) return;
    if (!alvo.closest('.modal-overlay.open')) return;
    alvo.scrollIntoView({ block: 'center', behavior: 'smooth' });
});
