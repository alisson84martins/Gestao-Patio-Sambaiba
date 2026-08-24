/*
 * Navegação principal — Frontend V3
 * -----------------------------------
 * Reconstrói o <nav class="app-nav"> a partir do acesso efetivo da pessoa
 * logada (sessao.js). Substitui a lista fixa de 6 links que cada página
 * duplicava, mais o <script> inline que só cuidava de revelar "Cadastros"
 * pra ADMIN — ele não escalava pras 4 funções que agora existem.
 *
 * Um link só aparece se a pessoa tiver leitura no recurso correspondente
 * E o link pertencer ao módulo da PÁGINA ATUAL — não ao módulo salvo em
 * localStorage (moduloAtual()), que envelhece: quem abre uma página por
 * favorito/link direto não passou por modulos.html nem pela troca de
 * módulo, então localStorage podia estar apontando pro módulo errado (ou
 * vazio) e a barra saía com os 16 links de quem tem tudo (ADMIN), quebrando
 * em duas linhas. Derivar do próprio arquivo HTML aberto é sempre
 * verdadeiro, nunca fica desatualizado.
 *
 * Quem tem mais de um módulo ganha um link extra pra voltar à tela de
 * seleção e trocar.
 */

import { podeEscrever, podeLer, modulos } from './sessao.js';
import './modal.util.js';

// Página -> módulo dono da barra nesta tela. Cobre toda página que
// renderiza <nav class="app-nav"> (conferido em 23/08: 16 páginas, exatas
// às deste mapa). `ativo` é só pra páginas que não têm link próprio na
// LINKS abaixo mas devem acender o link de outra — caso de
// ocorrencia-form.html, que abre a partir de "Ocorrências".
const PAGINAS = {
    'patio.html':                  { modulo: 'PATIO' },
    'remanejamento.html':          { modulo: 'PATIO' },
    'alertas.html':                { modulo: 'PATIO' },
    // Migration 037 (Bloco I, 24/08) — Manutenção saiu do Pátio, virou
    // módulo próprio. Decisão consciente (ver cabeçalho da migration): o
    // atalho não volta a aparecer na barra do Pátio.
    'manutencao.html':             { modulo: 'MANUTENCAO' },
    'importacao.html':             { modulo: 'PATIO' },
    'ocorrencias.html':            { modulo: 'COORDENADORIA' },
    'ocorrencia-form.html':        { modulo: 'COORDENADORIA', ativo: 'ocorrencias.html' },
    'pre-ocorrencias.html':        { modulo: 'COORDENADORIA' },
    'fiscal-painel.html':          { modulo: 'FISCALIZACAO' },
    'fiscal.html':                 { modulo: 'FISCALIZACAO' },
    'portaria.html':               { modulo: 'PORTARIA' },
    'portaria-veiculos.html':      { modulo: 'PORTARIA' },
    'portaria-consulta.html':      { modulo: 'PORTARIA' },
    // Bloco C (2026-08-24) — Recolhida virou botão dentro de portaria.html,
    // não tem mais link próprio na barra (ver LINKS). `ativo` mantém o link
    // "Portaria" aceso quando esta página está aberta, mesmo padrão de
    // ocorrencia-form.html acima.
    'portaria-recolhida.html':     { modulo: 'PORTARIA', ativo: 'portaria.html' },
    'cadastros.html':              { modulo: 'ADMINISTRACAO' },
    'permissoes.html':             { modulo: 'ADMINISTRACAO' },
};

const LINKS = [
    { href: 'patio.html', texto: 'Pátio', recurso: 'alocacao', modulo: 'PATIO' },
    { href: 'remanejamento.html', texto: 'Remanejamento', recurso: 'alocacao', modulo: 'PATIO' },
    { href: 'alertas.html', texto: 'Alertas', recurso: 'alerta', modulo: 'PATIO' },
    { href: 'importacao.html', texto: 'Importação', recurso: 'escala', modulo: 'PATIO' },
    { href: 'ocorrencias.html', texto: 'Ocorrências', recurso: 'ocorrencia', modulo: 'COORDENADORIA' },
    // CCO só tem pode_escrever em pre_ocorrencia (abre/roteia, nunca lê
    // conteúdo — decisão 4) — qualquerAcesso mostra o link pra quem tem
    // ler OU escrever, não só ler como os outros.
    { href: 'pre-ocorrencias.html', texto: 'Pré-ocorrências', recurso: 'pre_ocorrencia', qualquerAcesso: true, modulo: 'COORDENADORIA' },
    { href: 'cadastros.html', texto: 'Cadastros', recurso: 'usuarios', modulo: 'ADMINISTRACAO' },
    { href: 'permissoes.html', texto: 'Permissões', recurso: 'usuarios', modulo: 'ADMINISTRACAO' },
    // Módulo Portaria (migration 024) — cada link só aparece com podeLer()
    // no recurso, igual aos demais. Não usar qualquerAcesso aqui: esse
    // parâmetro existe pro CCO, que escreve sem ler; não é o caso do
    // controlador de acesso.
    { href: 'portaria.html', texto: 'Portaria', recurso: 'acesso_veicular', modulo: 'PORTARIA' },
    { href: 'portaria-veiculos.html', texto: 'Veículos', recurso: 'veiculo_portaria', modulo: 'PORTARIA' },
    { href: 'portaria-consulta.html', texto: 'Histórico', recurso: 'acesso_veicular', modulo: 'PORTARIA' },
    // Bloco C (2026-08-24) — Recolhida saiu daqui e virou botão dentro de
    // portaria.html (id btn-recolhida, em portaria.page.js). Continua
    // mapeada em PAGINAS acima, só não tem mais link de barra próprio.
    // Fiscalização, Bloco E (migration 030) — painel do coordenador. O
    // fiscal não tem fiscalizacao_painel (D — só registra, não vê
    // agregado), então nunca vê este link.
    { href: 'fiscal-painel.html', texto: 'Fiscalização', recurso: 'fiscalizacao_painel', modulo: 'FISCALIZACAO' },
    // Fiscalização, Bloco D — registro do fiscal em campo. O coordenador
    // tem `fiscalizacao_painel`, não `fiscalizacao` (D — ele acompanha,
    // não registra), então cada um vê só o link que é dele.
    { href: 'fiscal.html', texto: 'Registro do fiscal', recurso: 'fiscalizacao', modulo: 'FISCALIZACAO' },
    // Módulo Manutenção (migration 037, Bloco I, 24/08) — saiu do Pátio,
    // virou módulo próprio. UMA entrada só (decisão consciente, não
    // duplicar "por conveniência" — ver cabeçalho da migration).
    { href: 'manutencao.html', texto: 'Manutenção', recurso: 'manutencao', modulo: 'MANUTENCAO' },
];

export function initNav() {
    const nav = document.querySelector('nav.app-nav');
    if (!nav) return;

    const paginaAtual = location.pathname.split('/').pop() || 'patio.html';
    const paginaAtiva = PAGINAS[paginaAtual]?.ativo ?? paginaAtual;
    const moduloDaPagina = PAGINAS[paginaAtual]?.modulo ?? null;
    nav.innerHTML = '';

    for (const link of LINKS) {
        const temAcesso = link.qualquerAcesso
            ? (podeLer(link.recurso) || podeEscrever(link.recurso))
            : podeLer(link.recurso);
        if (!temAcesso) continue;
        // Só filtra por módulo quando a página atual está mapeada. Página
        // fora do mapa (esquecimento, página nova) cai no comportamento de
        // antes — mostra todo link com acesso, sem filtrar. Melhor
        // mostrar link demais do que deixar alguém sem navegação.
        if (moduloDaPagina && link.modulo !== moduloDaPagina) continue;
        const a = document.createElement('a');
        a.href = link.href;
        a.className = 'app-nav-link' + (link.href === paginaAtiva ? ' active' : '');
        a.textContent = link.texto;
        nav.appendChild(a);
    }

    if (modulos().length > 1) {
        const trocar = document.createElement('a');
        trocar.href = 'modulos.html';
        trocar.className = 'app-nav-link app-nav-trocar-modulo';
        trocar.textContent = '⇄ Trocar módulo';
        nav.appendChild(trocar);
    }
}

initNav();
