/*
 * ocorrencia.excluir.js — Exclusão de ocorrência (soft delete)
 * -----------------------------------------------------------------------------
 * O soft delete de verdade (grava excluida_em, nunca apaga a linha) e a
 * trava de autoria (só o autor ou ADMIN) são do backend — este módulo só
 * evita o toque acidental no celular com uma confirmação que mostra
 * número, data e tipo antes de mandar o DELETE, e mostra a mensagem de
 * erro da API tal como veio quando o backend responde 403 (nunca um
 * texto genérico inventado aqui).
 * Reaproveitado por ocorrencias.page.js (lista) e ocorrencia.form.js
 * (formulário), mesmo padrão de ocorrencia.sinistro.js: modal com id
 * fixo, presente nas duas páginas, módulo com estado próprio.
 */
import { apiDelete } from './api.js';

let modalInicializado = false;
let pendente = null; // { id, onSuccess }

function abrirModal() {
    document.getElementById('modal-excluir-oc')?.classList.add('open');
}

function fecharModal() {
    document.getElementById('modal-excluir-oc')?.classList.remove('open');
    pendente = null;
}

function exibirErro(msg) {
    const el = document.getElementById('excluir-oc-erro');
    if (!el) return;
    el.textContent = msg || '';
    el.style.display = msg ? 'block' : 'none';
}

function fmtData(iso) {
    if (!iso) return '—';
    const [ano, mes, dia] = iso.split('-');
    return `${dia}/${mes}/${ano}`;
}

async function confirmar() {
    if (!pendente) return;
    const { id, onSuccess } = pendente;
    const btn = document.getElementById('btn-excluir-oc-confirmar');
    btn.disabled = true;
    exibirErro('');
    try {
        await apiDelete(`/ocorrencias/${id}`);
        fecharModal();
        if (onSuccess) onSuccess();
    } catch (err) {
        // Mensagem da API direto na tela (ex: 403 "registrada por outro
        // coordenador") — não inventa texto genérico no lugar dela.
        exibirErro(err.message);
    } finally {
        btn.disabled = false;
    }
}

function initModal() {
    document.getElementById('modal-excluir-oc-fechar')?.addEventListener('click', fecharModal);
    document.getElementById('btn-excluir-oc-cancelar')?.addEventListener('click', fecharModal);
    document.getElementById('modal-excluir-oc')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-excluir-oc') fecharModal();
    });
    document.getElementById('btn-excluir-oc-confirmar')?.addEventListener('click', confirmar);
}

/**
 * Abre a confirmação de exclusão de uma ocorrência.
 * @param {{id: string, numero: number, data_ocorrencia: string, tipo_nome: string}} ocorrencia
 * @param {() => void} onSuccess - roda só depois do DELETE confirmado pela API.
 */
export function confirmarExclusaoOcorrencia(ocorrencia, onSuccess) {
    if (!modalInicializado) {
        initModal();
        modalInicializado = true;
    }
    pendente = { id: ocorrencia.id, onSuccess };
    exibirErro('');
    const resumo = document.getElementById('excluir-oc-resumo');
    if (resumo) {
        resumo.textContent = `Nº ${ocorrencia.numero} — ${fmtData(ocorrencia.data_ocorrencia)} — ${ocorrencia.tipo_nome || ''}`;
    }
    abrirModal();
}
