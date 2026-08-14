/*
 * Pré-ocorrência do motorista — formulário público, SEM login.
 * ---------------------------------------------------------------
 * Quem preenche acabou de se envolver num acidente: em pé, na rua,
 * no celular, possivelmente abalado, com sinal ruim. Tudo aqui se
 * subordina a isso — ver PROMPT-pre-ocorrencia-motorista.md §3.1.
 *
 * ⛔ Nada aqui é obrigatório. Nada bloqueia envio. O token nunca vai pro
 * path de nenhuma chamada de API — só no cabeçalho X-Pre-Ocorrencia-Token
 * (ver _handoff-claude/DESENHO-pre-ocorrencia.md, item 6). O token chega
 * na URL desta PÁGINA (?token=...), porque é assim que um link de uso
 * único funciona — mas depois de lido daqui, só viaja em cabeçalho.
 */
import { API_BASE_URL } from './config.js';
import { aplicarMascara } from './mascaras.js';

const token = new URLSearchParams(window.location.search).get('token');

const elCarregando = document.getElementById('pre-oc-carregando');
const elInvalido = document.getElementById('pre-oc-invalido');
const elErroConexao = document.getElementById('pre-oc-erro-conexao');
const elErroConexaoTexto = document.getElementById('pre-oc-erro-conexao-texto');
const elEnviado = document.getElementById('pre-oc-enviado');
const elForm = document.getElementById('pre-oc-form');
const elStatus = document.getElementById('pre-oc-status');
const elErroEnvio = document.getElementById('pre-oc-erro-envio');
const elErroAnexo = document.getElementById('pre-oc-anexo-erro');

const MSG_SEM_CONEXAO = 'Sem conexão com o servidor. Verifique o sinal e tente de novo — o link continua válido.';
const MSG_ERRO_NEUTRO = 'Algo deu errado. Tente de novo em instantes.';

const CAMPOS = [
    'motorista_re', 'motorista_nome', 'motorista_telefone', 'motorista_cpf', 'motorista_rg', 'motorista_cnh',
    'cobrador_re', 'cobrador_nome', 'cobrador_telefone', 'prefixo',
    'data_ocorrencia', 'hora_ocorrencia',
    'local_logradouro', 'local_numero', 'local_bairro', 'local_cidade', 'local_referencia',
    'relato',
    'terceiro_placa', 'terceiro_modelo_cor', 'terceiro_nome', 'terceiro_telefone', 'terceiro_seguradora',
];

const CHAVE_LOCAL = `pre_oc_rascunho_${token || 'sem-token'}`;

// ─── Fetch helper próprio — nunca usa api.js (que sempre manda o JWT) ────
// Item 1: a exceção precisa dizer SE deu pra falar com o servidor. `fetch`
// que lança (rede indisponível, DNS, timeout) é erro diferente de uma
// resposta HTTP que veio e disse "não" — quem lê o catch decide a mensagem
// certa a partir dessa diferença, nunca tratando as duas como a mesma coisa.
async function apiPublica(caminho, { method = 'GET', body } = {}) {
    let resp;
    try {
        resp = await fetch(`${API_BASE_URL}${caminho}`, {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-Pre-Ocorrencia-Token': token,
            },
            body: body ? JSON.stringify(body) : undefined,
        });
    } catch {
        const erro = new Error('Falha de rede');
        erro.rede = true;
        throw erro;
    }
    if (!resp.ok) {
        const erro = new Error(`Erro ${resp.status}`);
        erro.status = resp.status;
        throw erro;
    }
    return resp.status === 204 ? null : resp.json();
}

function lerRascunhoLocal() {
    try {
        return JSON.parse(localStorage.getItem(CHAVE_LOCAL) || '{}');
    } catch {
        return {};
    }
}

function salvarRascunhoLocal(dados) {
    try {
        localStorage.setItem(CHAVE_LOCAL, JSON.stringify(dados));
    } catch {
        // localStorage indisponível (modo privado, cota cheia) — autosave
        // no servidor continua sendo a rede de segurança principal.
    }
}

function lerFormulario() {
    const dados = {};
    for (const campo of CAMPOS) {
        const el = document.querySelector(`[name="${campo}"]`);
        if (el) dados[campo] = el.value || null;
    }
    return dados;
}

function preencherFormulario(dados) {
    for (const campo of CAMPOS) {
        const el = document.querySelector(`[name="${campo}"]`);
        if (el && dados[campo] != null) el.value = dados[campo];
    }
}

// ─── Autosave: local a cada tecla, servidor com debounce ──────────────────
let debounceServidor = null;
let salvando = false;

function marcarStatus(texto, tipo = '') {
    elStatus.textContent = texto;
    elStatus.className = `pre-oc-status ${tipo}`;
}

function agendarAutosave() {
    salvarRascunhoLocal(lerFormulario());
    marcarStatus('Digitando…');
    clearTimeout(debounceServidor);
    debounceServidor = setTimeout(salvarNoServidor, 1200);
}

async function salvarNoServidor() {
    if (salvando) return;
    salvando = true;
    marcarStatus('Salvando…');
    try {
        await apiPublica('/pre-ocorrencias/publico', { method: 'PATCH', body: lerFormulario() });
        marcarStatus('Tudo salvo ✓', 'pre-oc-status-ok');
    } catch (err) {
        // Autosave nunca trava o formulário — sinal ruim em local de
        // acidente é a regra, não a exceção. O rascunho já está no
        // localStorage; tenta de novo no próximo campo digitado.
        if (err.status === 404) {
            // Token expirou/foi usado no meio do preenchimento — diferente
            // de rede: continuar digitando não adianta, ninguém mais recebe.
            marcarStatus('Este link expirou — seu texto está salvo neste aparelho, mas não chega mais ao coordenador. Peça um link novo.', 'pre-oc-status-erro');
        } else {
            marcarStatus('Sem conexão — seu texto está salvo neste aparelho', 'pre-oc-status-erro');
        }
    } finally {
        salvando = false;
    }
}

function ligarAutosave() {
    for (const campo of CAMPOS) {
        const el = document.querySelector(`[name="${campo}"]`);
        if (!el) continue;
        const evento = el.tagName === 'TEXTAREA' || el.type === 'text' || el.type === 'tel' ? 'input' : 'change';
        el.addEventListener(evento, agendarAutosave);
    }
}

// ─── Anexos ────────────────────────────────────────────────────────────
async function enviarAnexo(inputEl, tipo, elStatusAnexo) {
    const arquivo = inputEl.files?.[0];
    if (!arquivo) return;

    elErroAnexo.style.display = 'none';
    elStatusAnexo.textContent = 'Enviando…';

    const formData = new FormData();
    formData.append('arquivo', arquivo);
    formData.append('tipo', tipo);

    try {
        const resp = await fetch(`${API_BASE_URL}/pre-ocorrencias/publico/anexos`, {
            method: 'POST',
            headers: { 'X-Pre-Ocorrencia-Token': token },
            body: formData,
        });
        if (!resp.ok) {
            // O FastAPI devolve {"detail": "..."} — nunca {"erro": ...}.
            // corpo.erro fica de reserva (nunca existe hoje, mas não custa
            // nada) e `Erro ${status}` é o último recurso.
            const corpo = await resp.json().catch(() => ({}));
            throw new Error(corpo.detail || corpo.erro || `Erro ${resp.status}`);
        }
        elStatusAnexo.textContent = '✓ enviado';
    } catch (err) {
        elStatusAnexo.textContent = '';
        // textContent já não interpreta HTML — escapeHtml aqui só faria o
        // motorista ver "&lt;" literal na tela por cima da mensagem real.
        elErroAnexo.textContent = `Não deu pra enviar: ${err.message}. Pode tentar de novo, ou seguir sem — o relato continua valendo.`;
        elErroAnexo.style.display = '';
    }
}

// ─── Enviar ────────────────────────────────────────────────────────────
async function enviar() {
    const btn = document.getElementById('btn-enviar');
    btn.disabled = true;
    btn.textContent = 'Enviando…';
    elErroEnvio.style.display = 'none';

    // Última garantia: manda o que está na tela antes de finalizar, caso
    // o autosave em andamento ainda não tenha terminado.
    try {
        await apiPublica('/pre-ocorrencias/publico', { method: 'PATCH', body: lerFormulario() });
        const resultado = await apiPublica('/pre-ocorrencias/publico/enviar', { method: 'POST' });

        elForm.style.display = 'none';
        elEnviado.style.display = '';
        document.getElementById('pre-oc-protocolo').textContent = resultado.protocolo;
        localStorage.removeItem(CHAVE_LOCAL);
    } catch (err) {
        btn.disabled = false;
        btn.textContent = 'Enviar relato';
        elErroEnvio.textContent = err.status === 400
            ? 'Conte pelo menos um pouco do que aconteceu antes de enviar.'
            : 'Não deu pra enviar agora. Seu texto está salvo neste aparelho — tente de novo em instantes.';
        elErroEnvio.style.display = '';
    }
}

// ─── Boot ──────────────────────────────────────────────────────────────
async function iniciar() {
    if (!token) {
        elCarregando.style.display = 'none';
        elInvalido.style.display = '';
        return;
    }

    elInvalido.style.display = 'none';
    elErroConexao.style.display = 'none';
    elCarregando.style.display = '';

    try {
        const dados = await apiPublica('/pre-ocorrencias/publico');
        elCarregando.style.display = 'none';

        if (dados.status !== 'AGUARDANDO') {
            // Já enviado antes (reabriu o link) — mostra confirmação, não
            // deixa editar de novo (regra 7: escrita para depois de enviar).
            elEnviado.style.display = '';
            document.getElementById('pre-oc-protocolo').textContent = 'já enviado anteriormente';
            return;
        }

        // Mescla servidor + rascunho local: servidor manda, mas campo que
        // ficou vazio no servidor (autosave anterior pode ter falhado) e
        // tem valor salvo neste aparelho usa o local — nada se perde.
        const local = lerRascunhoLocal();
        const mesclado = { ...dados };
        for (const campo of CAMPOS) {
            if (!mesclado[campo] && local[campo]) mesclado[campo] = local[campo];
        }
        preencherFormulario(mesclado);

        elForm.style.display = '';
        ligarAutosave();

        aplicarMascara(document.getElementById('f-mot-re'), 're');
        aplicarMascara(document.getElementById('f-cob-re'), 're');
        aplicarMascara(document.getElementById('f-mot-cpf'), 'cpf');
        aplicarMascara(document.getElementById('f-mot-telefone'), 'telefone');
        aplicarMascara(document.getElementById('f-cob-telefone'), 'telefone');
        aplicarMascara(document.getElementById('f-mot-cnh'), 'cnh');
        aplicarMascara(document.getElementById('f-mot-rg'), 'rg');
        aplicarMascara(document.getElementById('f-terc-placa'), 'placa');
        aplicarMascara(document.getElementById('f-terc-telefone'), 'telefone');

        document.getElementById('f-anexo-cnh').addEventListener('change', (e) =>
            enviarAnexo(e.target, 'CNH', document.getElementById('status-anexo-cnh')));
        document.getElementById('f-anexo-doc').addEventListener('change', (e) =>
            enviarAnexo(e.target, 'DOC_VEICULO', document.getElementById('status-anexo-doc')));

        document.getElementById('btn-enviar').addEventListener('click', enviar);
    } catch (err) {
        elCarregando.style.display = 'none';
        // Item 1: sinal ruim em local de acidente é a regra — falha de rede
        // ou 429 (rate limit) NUNCA pode virar "link inválido" na tela,
        // senão o motorista liga pro coordenador, que queima outro token à
        // toa e o link novo falha pelo mesmo motivo.
        if (err.rede || err.status === 429) {
            elErroConexaoTexto.textContent = MSG_SEM_CONEXAO;
            elErroConexao.style.display = '';
        } else if (err.status === 404) {
            elInvalido.style.display = '';
        } else {
            elErroConexaoTexto.textContent = MSG_ERRO_NEUTRO;
            elErroConexao.style.display = '';
        }
    }
}

document.getElementById('btn-tentar-novo').addEventListener('click', () => {
    elErroConexao.style.display = 'none';
    iniciar();
});

iniciar();
