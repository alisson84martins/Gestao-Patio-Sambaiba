/*
 * importacao.js — Tela de Importação de Escala
 * ---------------------------------------------
 * Responsabilidades:
 *   1. Guard de autenticação (requireAuth)
 *   2. Preencher cabeçalho com dados do usuário logado
 *   3. Upload de arquivo .xlsx via multipart/form-data (sem Content-Type manual)
 *   4. Renderizar resultado do upload (stats + erros por linha)
 *   5. Listar histórico de importações e permitir reversão
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, ApiError } from './api.js';
import { API_BASE_URL, TOKEN_KEY } from './config.js';

// ─── Guard de autenticação ────────────────────────────────────────────────────
if (!requireAuth()) {
    // requireAuth já redireciona — nada mais a fazer aqui
    throw new Error('Não autenticado');
}

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initHeader();
    initFormUpload();
    // Define data default como hoje
    const inputData = document.getElementById('input-data-escala');
    if (inputData) {
        inputData.value = new Date().toISOString().slice(0, 10);
    }
    fetchHistorico();
});

// ─── Header ───────────────────────────────────────────────────────────────────

/**
 * Preenche o cabeçalho com nome e cargo do usuário corrente.
 * Associa o botão de logout.
 */
function initHeader() {
    const user = getCurrentUser();
    if (user) {
        const elNome = document.getElementById('user-name');
        const elMeta = document.getElementById('user-meta');
        if (elNome) elNome.textContent = user.nome || user.re || '—';
        if (elMeta) elMeta.textContent = user.cargo || user.perfil || '—';
    }

    const btnLogout = document.getElementById('btn-logout');
    if (btnLogout) {
        btnLogout.addEventListener('click', () => {
            logout();
            window.location.replace('index.html');
        });
    }
}

// ─── Upload (multipart) ───────────────────────────────────────────────────────

/**
 * Função de upload multipart/form-data.
 * NÃO seta Content-Type — o browser precisa setar automaticamente com o boundary.
 * Trata 401 redirecionando pro login, erros de rede e respostas não-JSON.
 *
 * @param {string} path — caminho relativo da API (ex: '/importacoes/escala')
 * @param {FormData} formData — dados do formulário com o arquivo
 * @returns {Promise<any>} — payload JSON da resposta
 * @throws {ApiError}
 */
async function apiUpload(path, formData) {
    const url = `${API_BASE_URL}${path}`;
    const token = localStorage.getItem(TOKEN_KEY);

    const headers = {};
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    // Propositalmente SEM Content-Type — o browser injeta multipart/form-data + boundary

    let response;
    try {
        response = await fetch(url, {
            method: 'POST',
            headers,
            body: formData,
        });
    } catch (networkErr) {
        throw new ApiError(
            0,
            'Não foi possível conectar à API. Verifique se o servidor está rodando.',
            { cause: networkErr.message },
        );
    }

    // Sessão expirada → redireciona
    if (response.status === 401) {
        logout();
        window.location.replace('index.html');
        throw new ApiError(401, 'Sessão expirada. Faça login novamente.', null);
    }

    let payload = null;
    try {
        payload = await response.json();
    } catch {
        // Resposta sem corpo JSON
    }

    if (!response.ok) {
        const detail = payload?.detail || payload?.message || response.statusText;
        const msg = typeof detail === 'string' ? detail : 'Erro inesperado no upload';
        throw new ApiError(response.status, msg, payload);
    }

    return payload;
}

// ─── Formulário de upload ─────────────────────────────────────────────────────

/**
 * Registra o listener de submit no formulário de importação.
 */
function initFormUpload() {
    const form = document.getElementById('form-importacao');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await importarEscala();
    });
}

/**
 * Executa o fluxo completo de importação:
 *   1. Valida campos obrigatórios
 *   2. Monta FormData
 *   3. Desabilita form + mostra spinner
 *   4. POST via apiUpload
 *   5. Renderiza resultado
 *   6. Recarrega histórico
 *   7. Re-habilita form e limpa arquivo selecionado
 */
async function importarEscala() {
    ocultarErroUpload();

    const inputArquivo = document.getElementById('input-arquivo');
    const inputData    = document.getElementById('input-data-escala');
    const selectTipo   = document.getElementById('input-tipo-default');
    const checkSubst   = document.getElementById('input-substituir');

    // Validação básica
    if (!inputArquivo.files || inputArquivo.files.length === 0) {
        exibirErroUpload('Selecione um arquivo .xlsx para importar.');
        return;
    }
    if (!inputData.value) {
        exibirErroUpload('Informe a data da escala.');
        return;
    }

    const arquivo = inputArquivo.files[0];

    // Monta FormData — o browser cuidará do boundary
    const formData = new FormData();
    formData.append('arquivo', arquivo);
    formData.append('data_escala', inputData.value);
    formData.append('tipo_default', selectTipo.value);
    formData.append('substituir_existentes', checkSubst.checked ? 'true' : 'false');

    // Desabilita form + ativa spinner
    setFormBusy(true);

    try {
        const resp = await apiUpload('/importacoes/escala', formData);
        renderResultado(resp);
        // Limpa o file input após upload bem-sucedido, mantém a data
        inputArquivo.value = '';
        // Recarrega histórico em background
        await fetchHistorico();
    } catch (err) {
        exibirErroUpload(err.message || 'Erro ao importar escala.');
        // Esconde resultado anterior caso exista
        const divResultado = document.getElementById('import-resultado');
        if (divResultado) divResultado.style.display = 'none';
    } finally {
        setFormBusy(false);
    }
}

/**
 * Habilita ou desabilita os controles do formulário e alterna o spinner do botão.
 * @param {boolean} busy
 */
function setFormBusy(busy) {
    const form    = document.getElementById('form-importacao');
    const btnTexto   = document.getElementById('btn-importar-texto');
    const btnSpinner = document.getElementById('btn-importar-spinner');
    const btnImportar = document.getElementById('btn-importar');

    if (!form) return;

    // Desabilita todos os inputs/selects/checkboxes do form
    const controles = form.querySelectorAll('input, select, button');
    controles.forEach(el => { el.disabled = busy; });

    if (btnTexto)   btnTexto.style.display   = busy ? 'none' : '';
    if (btnSpinner) btnSpinner.style.display  = busy ? 'inline-block' : 'none';
    if (btnImportar) btnImportar.disabled = busy;
}

// ─── Resultado do upload ──────────────────────────────────────────────────────

/**
 * Renderiza o box de resultado após upload.
 * Verde  → nenhum erro
 * Amarelo → parcial (alguns erros)
 * Vermelho → tudo com erro (inseridos = 0)
 *
 * @param {object} resp — ImportacaoUploadResponse do backend
 */
function renderResultado(resp) {
    const div = document.getElementById('import-resultado');
    if (!div) return;

    const { total_lidos, total_inseridos, total_erros, substituidas, erros } = resp;

    // Define classe de cor conforme resultado
    let classeResultado = 'import-result import-result-ok';
    if (total_erros > 0 && total_inseridos === 0) {
        classeResultado = 'import-result import-result-erro';
    } else if (total_erros > 0) {
        classeResultado = 'import-result import-result-aviso';
    }

    // Monta stats
    const statsHtml = `
        <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:12px;">
            <div>
                <div class="import-stat">${total_lidos ?? 0}</div>
                <div class="import-stat-label">Lidos</div>
            </div>
            <div>
                <div class="import-stat">${total_inseridos ?? 0}</div>
                <div class="import-stat-label">Inseridos</div>
            </div>
            <div>
                <div class="import-stat">${total_erros ?? 0}</div>
                <div class="import-stat-label">Erros</div>
            </div>
            ${substituidas > 0 ? `
            <div>
                <div class="import-stat">${substituidas}</div>
                <div class="import-stat-label">Substituídos</div>
            </div>` : ''}
        </div>
    `;

    // Lista de linhas com erro (se houver)
    let errosHtml = '';
    if (erros && erros.length > 0) {
        const itens = erros.map(e =>
            `<div class="import-erro-item">
                <strong>Linha ${e.linha}</strong>: ${escapeHtml(e.motivo)}
             </div>`
        ).join('');
        errosHtml = `<div class="import-erros-list">${itens}</div>`;
    }

    div.className = classeResultado;
    div.innerHTML = statsHtml + errosHtml;
    div.style.display = 'block';

    // Atualiza footer como sinal de sucesso
    setFooter('online');
}

// ─── Histórico ────────────────────────────────────────────────────────────────

/**
 * Busca a lista de importações no backend e renderiza o histórico.
 */
async function fetchHistorico() {
    try {
        const lista = await apiGet('/importacoes');
        renderHistorico(lista);
        setFooter('online');
    } catch (err) {
        const div = document.getElementById('historico-lista');
        if (div) {
            div.innerHTML = `<div class="login-alert login-alert error show">
                Não foi possível carregar o histórico: ${escapeHtml(err.message)}
            </div>`;
        }
        setFooter('offline');
    }
}

/**
 * Renderiza a lista de importações no div#historico-lista.
 * Cada item exibe: arquivo, data_escala, sucesso/total, data de importação
 * e botão "Reverter" (quando registros_sucesso > 0).
 *
 * @param {Array} lista — lista de ImportacaoEscalaRead
 */
function renderHistorico(lista) {
    const div = document.getElementById('historico-lista');
    if (!div) return;

    if (!lista || lista.length === 0) {
        div.innerHTML = '<div style="color:var(--text-muted,#888);padding:12px 0;">Nenhuma importação realizada ainda.</div>';
        return;
    }

    const itens = lista.map(item => {
        const temSucesso = (item.registros_sucesso || 0) > 0;
        const btnReverter = temSucesso
            ? `<button
                    class="btn btn-danger"
                    style="padding:4px 12px;font-size:0.78rem;"
                    data-id="${item.id}"
                    data-arquivo="${escapeHtml(item.arquivo_nome || '')}"
                    data-data="${escapeHtml(item.data_escala || '')}"
                    onclick="window._reverterImportacao(${item.id}, '${escapeHtml(item.arquivo_nome || '')}', '${escapeHtml(item.data_escala || '')}')"
               >Reverter</button>`
            : '';

        return `
            <div class="historico-item">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
                    <div>
                        <div style="font-weight:600;">${escapeHtml(item.arquivo_nome || '—')}</div>
                        <div class="historico-item-meta">
                            Escala: ${escapeHtml(item.data_escala || '—')}
                            &nbsp;·&nbsp;
                            ${item.registros_sucesso ?? 0}/${item.total_registros ?? 0} registros
                            &nbsp;·&nbsp;
                            ${fmtData(item.importado_em)}
                        </div>
                    </div>
                    ${btnReverter}
                </div>
            </div>
        `;
    }).join('');

    div.innerHTML = itens;
}

/**
 * Reverte uma importação pelo ID.
 * Exposto como window._reverterImportacao para ser chamado via onclick inline
 * (evita complexidade de event delegation no HTML gerado).
 *
 * @param {number} id — ID da importação
 * @param {string} arquivoNome — nome do arquivo (para exibir na confirmação)
 * @param {string} dataEscala — data da escala (para exibir na confirmação)
 */
window._reverterImportacao = async function (id, arquivoNome, dataEscala) {
    const confirmar = confirm(
        `Reverter importação?\n\nArquivo: ${arquivoNome}\nData: ${dataEscala}\n\n` +
        `Os registros importados serão removidos. Esta ação não pode ser desfeita.`
    );
    if (!confirmar) return;

    try {
        // POST /importacoes/{id}/reverter — sem body
        const url = `${API_BASE_URL}/importacoes/${id}/reverter`;
        const token = localStorage.getItem(TOKEN_KEY);
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const response = await fetch(url, { method: 'POST', headers });

        if (response.status === 401) {
            logout();
            window.location.replace('index.html');
            return;
        }

        if (!response.ok) {
            let payload = null;
            try { payload = await response.json(); } catch { /* sem corpo */ }
            const msg = payload?.detail || payload?.message || response.statusText || 'Erro ao reverter';
            throw new Error(msg);
        }

        // Informa sucesso e recarrega histórico
        alert('Importação revertida com sucesso.');
        await fetchHistorico();
    } catch (err) {
        alert(`Erro ao reverter: ${err.message}`);
    }
};

// ─── Helpers de UI ────────────────────────────────────────────────────────────

/**
 * Exibe mensagem de erro de upload no div#upload-erro.
 * @param {string} msg
 */
function exibirErroUpload(msg) {
    const el = document.getElementById('upload-erro');
    if (!el) return;
    el.textContent = msg;
    el.className = 'login-alert login-alert error show';
}

/**
 * Oculta o alerta de erro de upload.
 */
function ocultarErroUpload() {
    const el = document.getElementById('upload-erro');
    if (!el) return;
    el.textContent = '';
    el.className = 'login-alert';
}

/**
 * Atualiza o status dot e texto do footer.
 * @param {'online'|'offline'} status
 */
function setFooter(status) {
    const dot = document.getElementById('poll-dot');
    const txt = document.getElementById('poll-status');
    if (dot) {
        dot.className = status === 'online' ? 'status-dot online' : 'status-dot';
    }
    if (txt) {
        txt.textContent = status === 'online' ? 'Conectado' : 'Sem conexão';
    }
}

/**
 * Formata uma string ISO 8601 para data/hora no padrão pt-BR.
 * Retorna '—' se o valor for inválido.
 *
 * @param {string|null} iso
 * @returns {string}
 */
function fmtData(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString('pt-BR', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    } catch {
        return iso;
    }
}

/**
 * Escapa caracteres especiais HTML para evitar XSS ao inserir strings
 * de dados externos em innerHTML.
 *
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
