/*
 * ocorrencia.sinistro.js — Mensagem do grupo do sinistro
 * -----------------------------------------------------------------------------
 * Busca o texto pronto em GET /ocorrencias/{id}/mensagem-sinistro e mostra
 * numa caixa com dois botões: Copiar (clipboard) e Compartilhar
 * (navigator.share quando disponível; senão wa.me com o texto codificado).
 * Sem integração com WhatsApp de verdade — o botão só abre o app com a
 * mensagem pronta, o coordenador escolhe o grupo.
 */
import { apiGet } from './api.js';

let textoAtual = '';

function abrirModal() {
    document.getElementById('modal-sinistro')?.classList.add('open');
}

function fecharModal() {
    document.getElementById('modal-sinistro')?.classList.remove('open');
}

function initModal() {
    document.getElementById('modal-sinistro-fechar')?.addEventListener('click', fecharModal);
    document.getElementById('modal-sinistro')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-sinistro') fecharModal();
    });

    document.getElementById('btn-sinistro-copiar')?.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(textoAtual);
            const btn = document.getElementById('btn-sinistro-copiar');
            const textoOriginal = btn.textContent;
            btn.textContent = '✔ Copiado!';
            setTimeout(() => { btn.textContent = textoOriginal; }, 1500);
        } catch {
            exibirErro('Não foi possível copiar automaticamente. Selecione o texto e copie manualmente.');
        }
    });

    document.getElementById('btn-sinistro-compartilhar')?.addEventListener('click', async () => {
        if (navigator.share) {
            try {
                await navigator.share({ text: textoAtual });
                return;
            } catch {
                // usuário cancelou o share nativo — cai no fallback do wa.me
            }
        }
        const url = `https://wa.me/?text=${encodeURIComponent(textoAtual)}`;
        window.open(url, '_blank');
    });
}

let modalInicializado = false;

function exibirErro(msg) {
    const el = document.getElementById('sinistro-erro');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
}

/**
 * Busca e exibe a mensagem do sinistro de uma ocorrência.
 * @param {string} ocorrenciaId
 */
export async function abrirMensagemSinistro(ocorrenciaId) {
    if (!modalInicializado) {
        initModal();
        modalInicializado = true;
    }

    document.getElementById('sinistro-erro').style.display = 'none';
    document.getElementById('sinistro-conteudo').style.display = 'none';
    document.getElementById('sinistro-carregando').style.display = 'block';
    abrirModal();

    try {
        const resp = await apiGet(`/ocorrencias/${ocorrenciaId}/mensagem-sinistro`);
        textoAtual = resp.texto || '';
        document.getElementById('sinistro-texto').textContent = textoAtual;
        document.getElementById('sinistro-carregando').style.display = 'none';
        document.getElementById('sinistro-conteudo').style.display = 'block';
    } catch (err) {
        document.getElementById('sinistro-carregando').style.display = 'none';
        exibirErro(`Erro ao gerar mensagem: ${err.message}`);
    }
}
