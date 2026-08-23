/*
 * Navegação principal — Frontend V3
 * -----------------------------------
 * Reconstrói o <nav class="app-nav"> a partir do acesso efetivo da pessoa
 * logada (sessao.js). Substitui a lista fixa de 6 links que cada página
 * duplicava, mais o <script> inline que só cuidava de revelar "Cadastros"
 * pra ADMIN — ele não escalava pras 4 funções que agora existem.
 *
 * Um link só aparece se a pessoa tiver leitura no recurso correspondente.
 * Quem tem mais de um módulo ganha um link extra pra voltar à tela de
 * seleção e trocar.
 */

import { podeEscrever, podeLer, modulos } from './sessao.js';
import './modal.util.js';

const LINKS = [
    { href: 'patio.html', texto: 'Pátio', recurso: 'alocacao' },
    { href: 'remanejamento.html', texto: 'Remanejamento', recurso: 'alocacao' },
    { href: 'alertas.html', texto: 'Alertas', recurso: 'alerta' },
    { href: 'manutencao.html', texto: 'Manutenção', recurso: 'manutencao' },
    { href: 'importacao.html', texto: 'Importação', recurso: 'escala' },
    { href: 'ocorrencias.html', texto: 'Ocorrências', recurso: 'ocorrencia' },
    // CCO só tem pode_escrever em pre_ocorrencia (abre/roteia, nunca lê
    // conteúdo — decisão 4) — qualquerAcesso mostra o link pra quem tem
    // ler OU escrever, não só ler como os outros.
    { href: 'pre-ocorrencias.html', texto: 'Pré-ocorrências', recurso: 'pre_ocorrencia', qualquerAcesso: true },
    { href: 'cadastros.html', texto: 'Cadastros', recurso: 'usuarios' },
    { href: 'permissoes.html', texto: 'Permissões', recurso: 'usuarios' },
    // Módulo Portaria (migration 024) — cada link só aparece com podeLer()
    // no recurso, igual aos demais. Não usar qualquerAcesso aqui: esse
    // parâmetro existe pro CCO, que escreve sem ler; não é o caso do
    // controlador de acesso.
    { href: 'portaria.html', texto: 'Portaria', recurso: 'acesso_veicular' },
    { href: 'portaria-veiculos.html', texto: 'Veículos', recurso: 'veiculo_portaria' },
    { href: 'portaria-consulta.html', texto: 'Histórico', recurso: 'acesso_veicular' },
    // Bloco F (migration 026) — os dois usam o mesmo recurso mas servem
    // gente diferente: Recolhida é o registro do controlador, Fila é a
    // avaliação da manutenção (+ análise gerencial, se a pessoa tiver
    // recolhida_gerencial). Rótulos registrados como pergunta em aberto no
    // handoff — ver §4.2 do prompt.
    { href: 'portaria-recolhida.html', texto: 'Recolhida', recurso: 'recolhida_anormal' },
    { href: 'manutencao-recolhidas.html', texto: 'Fila de Recolhidas', recurso: 'recolhida_anormal' },
    // Fiscalização, Bloco E (migration 030) — painel do coordenador. O
    // fiscal não tem fiscalizacao_painel (D — só registra, não vê
    // agregado), então nunca vê este link. fiscal.html (Bloco D) ainda
    // não existe e não entra aqui.
    { href: 'fiscal-painel.html', texto: 'Fiscalização', recurso: 'fiscalizacao_painel' },
];

export function initNav() {
    const nav = document.querySelector('nav.app-nav');
    if (!nav) return;

    const paginaAtual = location.pathname.split('/').pop() || 'patio.html';
    nav.innerHTML = '';

    for (const link of LINKS) {
        const temAcesso = link.qualquerAcesso
            ? (podeLer(link.recurso) || podeEscrever(link.recurso))
            : podeLer(link.recurso);
        if (!temAcesso) continue;
        const a = document.createElement('a');
        a.href = link.href;
        a.className = 'app-nav-link' + (link.href === paginaAtual ? ' active' : '');
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
