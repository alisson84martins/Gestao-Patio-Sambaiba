/*
 * portaria.page.js — Tela do controlador de acesso
 * -----------------------------------------------------------
 * Celular, uma mão, à noite, com carro esperando. Metas: saída em 1
 * toque, entrada em <=8s (DESENHO-modulo-portaria.md §4.1).
 *
 * 🔴 Regra número um (§1.1): o sistema NUNCA impede um registro. Nenhum
 * fluxo daqui bloqueia o POST /portaria/movimentos por causa da situação
 * do veículo — o card de confirmação avisa, exige observação quando
 * SUSPENSO/BAIXADO, mas sempre deixa confirmar.
 *
 * §3.6-C: GET /portaria/buscar devolve `candidatos` + `exato`, nunca um
 * palpite. exato=true (placa completa bateu) -> direto ao card. Senão,
 * até 8 candidatos pra TOCAR — nunca seleciona o primeiro sozinho.
 *
 * Card de confirmação único cobre os 4 estados do semáforo (🟢🟡⚪🔴):
 * veículo não encontrado (⚪) troca o botão "Confirmar" por "Cadastrar
 * agora" / "Registrar avulso" e libera o campo de placa pra digitação.
 */

import { requireAuth, getCurrentUser, logout } from './auth.js';
import { apiGet, apiPost, apiUpload, ApiError } from './api.js';
import { escapeHtml } from './escape.js';
import { POLLING_INTERVAL_MS } from './config.js';
import { aplicarMascara } from './mascaras.js';
import { podeEscrever } from './sessao.js';
import { buscarPorRe } from './identidade.js';

if (!requireAuth()) {
    throw new Error('Sessão não autenticada — interrompendo carga da página');
}

const HORAS_DENTRO = 36;

// Contexto do card de confirmação aberto no momento — null quando fechado.
// { veiculo: VeiculoRead|null, ultimoMovimento: MovimentoRead|null,
//   sentido: 'ENTRADA'|'SAIDA', movimentoEntradaId: string|null,
//   placaChute: string, origem: 'MANUAL'|'CAMERA', placaLidaBruta: string|null }
let contexto = null;
let pollHandle = null;
let empresasCache = null;
// RE do condutor (C2) — só relevante quando o carro é da empresa: achou
// funcionário -> vai por funcionario_id; não achou -> vai por texto livre
// (re_registrado/nome_registrado). Nunca em veiculo.funcionario_id (D2).
let condutorFuncionarioId = null;

// Leitura de placa por câmera (P13) — a busca disparada pela confirmação
// da leitura (confirmarPlacaLida) cai na MESMA executarBusca() da digitação
// manual, então esta informação não cabe nos parâmetros dela. Fica aqui,
// "pendurada", e abrirConfirmacaoParaCandidato/abrirConfirmacaoNaoEncontrado
// consomem (e zeram) assim que o card de confirmação de sempre nasce — ver
// _consumirLeituraCamera(). Qualquer digitação na busca visível também zera
// (initBusca), pra um camera_origem não vazar numa busca manual seguinte.
let origemProximaConfirmacao = 'MANUAL';
let placaLidaBrutaAtual = null;
// Mesma ideia, só que atravessando o modal de cadastro rápido (P6): entre
// "Cadastrar agora" e o card de confirmação nascer de novo depois do
// cadastro, `contexto` é recriado do zero em salvarCadastroRapido().
let origemPendenteCadastro = 'MANUAL';
let placaLidaBrutaPendenteCadastro = null;

function _consumirLeituraCamera() {
    const origem = origemProximaConfirmacao;
    const placaLidaBruta = placaLidaBrutaAtual;
    origemProximaConfirmacao = 'MANUAL';
    placaLidaBrutaAtual = null;
    return { origem, placaLidaBruta };
}

// ─── Header ─────────────────────────────────────────────────────────────
function initHeader() {
    const user = getCurrentUser();
    if (user) {
        document.getElementById('user-name').textContent = user.nome || '—';
        document.getElementById('user-meta').textContent = (user.re || '—').toUpperCase();
    }
    document.getElementById('btn-logout').addEventListener('click', () => {
        logout();
        window.location.replace('index.html');
    });
}

// ─── Utilidades ─────────────────────────────────────────────────────────
function normalizarPlacaFrontend(v) {
    return (v || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

function pareceComPlaca(termo) {
    // Exige letra E dígito — placas sempre têm as duas (antiga AAA9999,
    // Mercosul AAA9A99). Sem isso um RE puramente numérico de 5-8 dígitos
    // (comum) caía aqui achando que era placa incompleta.
    const norm = normalizarPlacaFrontend(termo);
    return norm.length >= 5 && norm.length <= 8 && /[A-Z]/.test(norm) && /\d/.test(norm);
}

function fmtHora(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function abrirModal(id) {
    document.getElementById(id).classList.add('open');
}

function fecharModal(id) {
    document.getElementById(id).classList.remove('open');
}

// Aviso não-bloqueante no topo (avisos da regra número um após um POST
// bem-sucedido — nunca um erro, o registro já aconteceu).
function mostrarAvisoTopo(msg) {
    const el = document.getElementById('portaria-busca-erro');
    el.style.color = '#fcd34d';
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; el.style.color = 'var(--accent)'; }, 6000);
}

function mostrarErroTopo(msg) {
    const el = document.getElementById('portaria-busca-erro');
    el.style.color = 'var(--accent)';
    el.textContent = msg;
    el.style.display = 'block';
}

// ─── "Dentro agora" + "sem_saida" (D17/§1.4) ───────────────────────────
async function carregarDentro() {
    try {
        const resp = await apiGet(`/portaria/dentro?horas=${HORAS_DENTRO}`);
        document.getElementById('portaria-contador-numero').textContent = String(resp.dentro.length);
        renderListaDentro(resp.dentro);
        renderListaSemSaida(resp.sem_saida);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria] erro ao carregar dentro:', err);
    }
}

function renderListaDentro(itens) {
    const el = document.getElementById('lista-dentro');
    if (itens.length === 0) {
        el.innerHTML = '<div class="oc-vazio">Nenhum veículo dentro agora.</div>';
        return;
    }
    el.innerHTML = '';
    for (const mov of itens) {
        el.appendChild(criarItemLista(mov, `Entrou ${fmtHora(mov.momento)}`, () => iniciarSaidaDaLista(mov)));
    }
}

function renderListaSemSaida(itens) {
    const secao = document.getElementById('secao-sem-saida');
    const el = document.getElementById('lista-sem-saida');
    if (itens.length === 0) {
        secao.style.display = 'none';
        el.innerHTML = '';
        return;
    }
    secao.style.display = 'block';
    el.innerHTML = '';
    for (const mov of itens) {
        el.appendChild(criarItemLista(mov, `Entrou ${fmtHora(mov.momento)} — toque pra fechar a saída`, () => iniciarSaidaDaLista(mov)));
    }
}

function criarItemLista(mov, subtitulo, aoTocar) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'portaria-item';
    btn.style.marginBottom = '8px';
    const nome = mov.nome_registrado || (mov.cadastrado ? '' : 'não cadastrado');
    btn.innerHTML = `
        <div>
            <div class="portaria-item-placa">${escapeHtml(mov.placa_registrada)}</div>
            <div class="portaria-item-sub">${escapeHtml(nome || subtitulo)}</div>
        </div>
        <div class="portaria-item-hora">${nome ? subtitulo : ''}</div>
    `;
    btn.addEventListener('click', aoTocar);
    return btn;
}

// Toque numa linha de "dentro agora"/"sem_saida" registra a SAÍDA — busca
// o veículo de novo pra pegar situação/hodômetro atuais (podem ter mudado
// desde a entrada) e reaproveita o mesmo card de confirmação de sempre.
async function iniciarSaidaDaLista(mov) {
    let candidato = null;
    try {
        const resp = await apiGet(`/portaria/buscar?q=${encodeURIComponent(mov.placa_registrada)}`);
        // §4.5-C: guarda explícita, mesmo buscando por placa completa (que
        // só bate exato) — sem isso esta linha vira o molde que alguém
        // copia pra um contexto de prefixo, e aí é o bug da §3.6-C de novo.
        if (resp.exato && resp.candidatos.length === 1) {
            candidato = resp.candidatos[0];
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria] erro ao buscar veículo pra saída:', err);
    }
    contexto = {
        veiculo: candidato ? candidato.veiculo : null,
        ultimoMovimento: candidato ? candidato.ultimo_movimento : mov,
        sentido: 'SAIDA',
        movimentoEntradaId: mov.id,
        placaChute: mov.placa_registrada,
    };
    renderEstadoConfirmacao();
    abrirModal('modal-confirmacao');
}

// ─── Busca (placa/RE/nome) ──────────────────────────────────────────────
function initBusca() {
    const input = document.getElementById('portaria-busca');
    let handle = null;
    input.addEventListener('input', () => {
        // Digitação manual na busca visível nunca herda origem de câmera
        // de uma leitura anterior não confirmada.
        origemProximaConfirmacao = 'MANUAL';
        placaLidaBrutaAtual = null;
        clearTimeout(handle);
        const termo = input.value.trim();
        esconderResultadosBusca();
        if (termo.length < 1) return;
        handle = setTimeout(() => executarBusca(termo), 250);
    });
}

function esconderResultadosBusca() {
    document.getElementById('portaria-candidatos').style.display = 'none';
    document.getElementById('portaria-busca-vazia').style.display = 'none';
    document.getElementById('portaria-busca-erro').style.display = 'none';
}

function limparBusca() {
    document.getElementById('portaria-busca').value = '';
    esconderResultadosBusca();
}

async function executarBusca(termo) {
    try {
        const resp = await apiGet(`/portaria/buscar?q=${encodeURIComponent(termo)}`);
        if (resp.exato && resp.candidatos.length === 1) {
            abrirConfirmacaoParaCandidato(resp.candidatos[0]);
            return;
        }
        if (resp.candidatos.length === 0) {
            if (pareceComPlaca(termo)) {
                abrirConfirmacaoNaoEncontrado(normalizarPlacaFrontend(termo));
            } else {
                document.getElementById('portaria-busca-vazia').style.display = 'block';
            }
            return;
        }
        renderCandidatos(resp.candidatos);
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        mostrarErroTopo('Erro na busca: ' + err.message);
    }
}

function renderCandidatos(candidatos) {
    const el = document.getElementById('portaria-candidatos');
    el.innerHTML = '';
    el.style.display = 'flex';
    // ⛔ Nunca escolhido em silêncio — cada candidato exige um toque.
    for (const c of candidatos) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'portaria-item';
        const dono = c.veiculo.funcionario_nome || c.veiculo.empresa_terceira_nome || '—';
        const detalhe = [c.veiculo.marca_modelo, c.veiculo.cor].filter(Boolean).join(' · ') || '—';
        btn.innerHTML = `
            <div>
                <div class="portaria-item-placa">${escapeHtml(c.veiculo.placa)}</div>
                <div class="portaria-item-sub">${escapeHtml(detalhe)} · ${escapeHtml(dono)}</div>
            </div>
            <div class="portaria-item-hora">${badgeCurto(c.veiculo.situacao)}</div>
        `;
        btn.addEventListener('click', () => abrirConfirmacaoParaCandidato(c));
        el.appendChild(btn);
    }
}

function badgeCurto(situacao) {
    const mapa = { PENDENTE: '🟡', AUTORIZADO: '🟢', SUSPENSO: '🔴', BAIXADO: '🔴' };
    return mapa[situacao] || '';
}

function abrirConfirmacaoParaCandidato(candidato) {
    const { origem, placaLidaBruta } = _consumirLeituraCamera();
    contexto = {
        veiculo: candidato.veiculo,
        ultimoMovimento: candidato.ultimo_movimento,
        sentido: candidato.dentro ? 'SAIDA' : 'ENTRADA',
        movimentoEntradaId: (candidato.dentro && candidato.ultimo_movimento) ? candidato.ultimo_movimento.id : null,
        placaChute: candidato.veiculo.placa,
        origem, placaLidaBruta,
    };
    renderEstadoConfirmacao();
    abrirModal('modal-confirmacao');
}

function abrirConfirmacaoNaoEncontrado(placaChute) {
    const { origem, placaLidaBruta } = _consumirLeituraCamera();
    contexto = {
        veiculo: null,
        ultimoMovimento: null,
        sentido: 'ENTRADA',
        movimentoEntradaId: null,
        placaChute: placaChute || '',
        origem, placaLidaBruta,
    };
    renderEstadoConfirmacao();
    abrirModal('modal-confirmacao');
}

// ─── Card de confirmação — um card, quatro estados de semáforo ─────────
function renderEstadoConfirmacao() {
    const textoPlaca = document.getElementById('confirmacao-placa-texto');
    const inputPlaca = document.getElementById('confirmacao-placa-input');
    const dono = document.getElementById('confirmacao-dono');
    const semaforo = document.getElementById('confirmacao-semaforo');
    const sentidoSelect = document.getElementById('confirmacao-sentido');
    const hodWrap = document.getElementById('confirmacao-hodometro-wrap');
    const hodInput = document.getElementById('confirmacao-hodometro');
    const kmEntrada = document.getElementById('confirmacao-km-entrada');
    const obsObrigatoria = document.getElementById('confirmacao-obs-obrigatoria');
    const obsInput = document.getElementById('confirmacao-observacao');
    const condutorWrap = document.getElementById('confirmacao-condutor-wrap');
    const condutorRe = document.getElementById('confirmacao-condutor-re');
    const condutorStatus = document.getElementById('confirmacao-condutor-status');
    const condutorNome = document.getElementById('confirmacao-condutor-nome');
    const erro = document.getElementById('confirmacao-erro');
    const btnConfirmar = document.getElementById('btn-confirmar-movimento');
    const btnCadastrar = document.getElementById('btn-cadastrar-agora');
    const btnAvulso = document.getElementById('btn-registrar-avulso');

    erro.style.display = 'none';
    erro.textContent = '';
    obsInput.value = '';
    hodInput.value = '';
    kmEntrada.textContent = '';
    condutorWrap.style.display = 'none';
    condutorRe.value = '';
    condutorStatus.textContent = '';
    condutorNome.value = '';
    condutorNome.style.display = 'none';
    condutorFuncionarioId = null;
    document.getElementById('confirmacao-titulo').textContent =
        contexto.sentido === 'SAIDA' ? 'Confirmar saída' : 'Confirmar entrada';
    sentidoSelect.value = contexto.sentido;

    const v = contexto.veiculo;

    if (!v) {
        // ⚪ não encontrado — regra número um: cadastra ou registra avulso.
        textoPlaca.style.display = 'none';
        inputPlaca.style.display = 'block';
        inputPlaca.value = contexto.placaChute || '';
        dono.textContent = '';
        semaforo.className = 'portaria-semaforo portaria-semaforo-cinza';
        semaforo.textContent = '⚪ Veículo não cadastrado — cadastre agora ou registre a passagem avulsa.';
        hodWrap.style.display = 'none';
        obsObrigatoria.style.display = 'none';
        btnConfirmar.style.display = 'none';
        btnCadastrar.style.display = 'block';
        btnAvulso.style.display = 'block';
        setTimeout(() => inputPlaca.focus(), 50);
        return;
    }

    textoPlaca.style.display = 'block';
    inputPlaca.style.display = 'none';
    textoPlaca.textContent = v.placa;

    let donoTexto = '—';
    if (v.propriedade === 'TERCEIRO') {
        donoTexto = v.empresa_terceira_nome || 'Terceiro';
    } else if (v.propriedade === 'EMPRESA') {
        donoTexto = 'Veículo da empresa';
        condutorWrap.style.display = 'block';
    } else if (v.funcionario_nome) {
        donoTexto = v.funcionario_re ? `${v.funcionario_nome} · RE ${v.funcionario_re}` : v.funcionario_nome;
    } else if (v.re_dono_texto) {
        // C1 (migration 039): RE digitado que não resolveu — regra número
        // um, o cadastro não foi recusado, mas o dono ainda não é funcionário.
        donoTexto = `RE ${v.re_dono_texto} (não cadastrado)`;
    }
    dono.textContent = donoTexto;

    btnConfirmar.style.display = 'block';
    btnCadastrar.style.display = 'none';
    btnAvulso.style.display = 'none';

    if (v.situacao === 'SUSPENSO' || v.situacao === 'BAIXADO') {
        semaforo.className = 'portaria-semaforo portaria-semaforo-vermelho';
        const partes = [`🔴 Veículo ${v.situacao}`];
        if (v.situacao_em) partes.push(`desde ${fmtHora(v.situacao_em)}`);
        if (v.situacao_por_nome) partes.push(`por ${v.situacao_por_nome}`);
        if (v.situacao_motivo) partes.push(`— motivo: ${v.situacao_motivo}`);
        semaforo.textContent = partes.join(' ');
        obsObrigatoria.style.display = 'inline';
        btnConfirmar.disabled = true;
    } else if (v.situacao === 'PENDENTE') {
        semaforo.className = 'portaria-semaforo portaria-semaforo-amarelo';
        semaforo.textContent = '🟡 Cadastrado, aguardando autorização.';
        obsObrigatoria.style.display = 'none';
        btnConfirmar.disabled = false;
    } else {
        semaforo.className = 'portaria-semaforo portaria-semaforo-verde';
        semaforo.textContent = '🟢 Autorizado.';
        obsObrigatoria.style.display = 'none';
        btnConfirmar.disabled = false;
    }

    if (v.exige_hodometro) {
        hodWrap.style.display = 'block';
        if (contexto.sentido === 'SAIDA' && contexto.ultimoMovimento && contexto.ultimoMovimento.hodometro_km != null) {
            kmEntrada.textContent = `Km na entrada: ${contexto.ultimoMovimento.hodometro_km}`;
        }
    } else {
        hodWrap.style.display = 'none';
    }

    atualizarCondutorObrigatorio();
}

// Obrigatório na SAÍDA, opcional na ENTRADA (carro pode voltar com outro
// motorista, ou rebocado — exigir na entrada travaria a portaria).
function atualizarCondutorObrigatorio() {
    const wrap = document.getElementById('confirmacao-condutor-wrap');
    const obrigatorio = document.getElementById('confirmacao-condutor-obrigatorio');
    const sentido = document.getElementById('confirmacao-sentido').value;
    obrigatorio.style.display = (wrap.style.display !== 'none' && sentido === 'SAIDA') ? 'inline' : 'none';
}

async function resolverCondutorRe() {
    const campoRe = document.getElementById('confirmacao-condutor-re');
    const status = document.getElementById('confirmacao-condutor-status');
    const campoNome = document.getElementById('confirmacao-condutor-nome');
    const re = campoRe.value.trim();
    status.textContent = '';
    condutorFuncionarioId = null;
    if (re.length < 3) {
        campoNome.style.display = 'none';
        return;
    }
    try {
        const resp = await buscarPorRe(re);
        if (resp.encontrado) {
            condutorFuncionarioId = resp.id;
            campoNome.style.display = 'none';
            campoNome.value = '';
            if (resp.ativo === false) {
                status.textContent = `${resp.nome} — desligado/inativo. Registra assim mesmo.`;
                status.style.color = '#f59e0b';
            } else {
                status.textContent = resp.nome;
                status.style.color = 'var(--accent3)';
            }
        } else {
            status.textContent = 'Não encontrado — pode informar o nome.';
            status.style.color = 'var(--muted)';
            campoNome.style.display = 'block';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria] erro ao resolver RE do condutor:', err);
    }
}

function initConfirmacao() {
    document.getElementById('fechar-confirmacao').addEventListener('click', () => fecharModal('modal-confirmacao'));
    document.getElementById('btn-cancelar-confirmacao').addEventListener('click', () => fecharModal('modal-confirmacao'));
    // A1: máscara + aviso visual, nunca bloqueia (D10) — mesmo padrão de ocorrencia.form.js.
    aplicarMascara(document.getElementById('confirmacao-placa-input'), 'placa');

    document.getElementById('confirmacao-observacao').addEventListener('input', (e) => {
        if (!contexto || !contexto.veiculo) return;
        if (contexto.veiculo.situacao === 'SUSPENSO' || contexto.veiculo.situacao === 'BAIXADO') {
            document.getElementById('btn-confirmar-movimento').disabled = !e.target.value.trim();
        }
    });

    document.getElementById('confirmacao-sentido').addEventListener('change', atualizarCondutorObrigatorio);
    document.getElementById('confirmacao-condutor-re').addEventListener('blur', resolverCondutorRe);

    document.getElementById('btn-confirmar-movimento').addEventListener('click', submeterMovimentoConhecido);
    document.getElementById('btn-registrar-avulso').addEventListener('click', submeterMovimentoAvulso);
    document.getElementById('btn-cadastrar-agora').addEventListener('click', () => {
        const placa = document.getElementById('confirmacao-placa-input').value.trim();
        const sentido = document.getElementById('confirmacao-sentido').value;
        // P13 — o card de confirmação atual (contexto) morre aqui; guarda a
        // origem antes que o cadastro rápido recrie `contexto` do zero.
        origemPendenteCadastro = (contexto && contexto.origem) || 'MANUAL';
        placaLidaBrutaPendenteCadastro = (contexto && contexto.placaLidaBruta) || null;
        fecharModal('modal-confirmacao');
        abrirCadastroRapido(placa, sentido);
    });
}

async function submeterMovimentoConhecido() {
    const erro = document.getElementById('confirmacao-erro');
    erro.style.display = 'none';
    const v = contexto.veiculo;
    const sentido = document.getElementById('confirmacao-sentido').value;
    const observacao = document.getElementById('confirmacao-observacao').value.trim();
    if ((v.situacao === 'SUSPENSO' || v.situacao === 'BAIXADO') && !observacao) {
        erro.textContent = 'Observação é obrigatória pra registrar este veículo.';
        erro.style.display = 'block';
        return;
    }

    // Condutor (D2/C2) — só existe pra carro da empresa; dono pessoa física
    // (PARTICULAR) já é o funcionario_id do veículo. ⛔ Nunca grava em
    // veiculo.funcionario_id — quem dirige é do movimento, não do cadastro.
    const condutorPayload = {};
    if (v.propriedade === 'EMPRESA') {
        const condutorRe = document.getElementById('confirmacao-condutor-re').value.trim();
        if (sentido === 'SAIDA' && !condutorRe) {
            erro.textContent = 'RE do condutor é obrigatório na saída.';
            erro.style.display = 'block';
            return;
        }
        if (condutorRe) {
            if (condutorFuncionarioId) {
                condutorPayload.funcionario_id = condutorFuncionarioId;
            } else {
                condutorPayload.re_registrado = condutorRe;
                condutorPayload.nome_registrado = document.getElementById('confirmacao-condutor-nome').value.trim() || null;
            }
        }
    }

    const hodometroStr = document.getElementById('confirmacao-hodometro').value.trim();
    await enviarMovimento({
        sentido,
        placa: v.placa,
        observacao: observacao || null,
        hodometro_km: hodometroStr ? Number(hodometroStr) : null,
        movimento_entrada_id: contexto.movimentoEntradaId || null,
        ...condutorPayload,
    });
}

async function submeterMovimentoAvulso() {
    const erro = document.getElementById('confirmacao-erro');
    erro.style.display = 'none';
    const placa = document.getElementById('confirmacao-placa-input').value.trim();
    if (!placa) {
        erro.textContent = 'Digite a placa.';
        erro.style.display = 'block';
        return;
    }
    const observacao = document.getElementById('confirmacao-observacao').value.trim();
    await enviarMovimento({
        sentido: document.getElementById('confirmacao-sentido').value,
        placa,
        observacao: observacao || null,
        // Cobre o toque em "dentro agora"/"sem_saida" pra uma placa avulsa
        // (sem cadastro) — o vínculo é conveniência (D3), mas existindo,
        // não custa nada mandar.
        movimento_entrada_id: contexto.movimentoEntradaId || null,
    });
}

async function enviarMovimento(payload) {
    const erro = document.getElementById('confirmacao-erro');
    const botoes = ['btn-confirmar-movimento', 'btn-registrar-avulso', 'btn-cadastrar-agora'].map(id => document.getElementById(id));
    botoes.forEach(b => { b.disabled = true; });
    // P13 — origem/placa_lida_bruta vêm do contexto (câmera ou digitação
    // manual, ver _consumirLeituraCamera). Central aqui em vez de em cada
    // submeter*: cobre confirmar, avulso e o "Cadastrar agora" sem repetir.
    const corpo = {
        ...payload,
        origem: (contexto && contexto.origem) || 'MANUAL',
        placa_lida_bruta: (contexto && contexto.placaLidaBruta) || null,
    };
    try {
        const resp = await apiPost('/portaria/movimentos', corpo);
        fecharModal('modal-confirmacao');
        limparBusca();
        await carregarDentro();
        if (resp.avisos && resp.avisos.length > 0) {
            mostrarAvisoTopo(resp.avisos.join(' · '));
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        botoes.forEach(b => { b.disabled = false; });
        if (contexto && contexto.veiculo && (contexto.veiculo.situacao === 'SUSPENSO' || contexto.veiculo.situacao === 'BAIXADO')) {
            document.getElementById('btn-confirmar-movimento').disabled = !document.getElementById('confirmacao-observacao').value.trim();
        }
    }
}

// ─── "+ Entrada" — atalho pro estado ⚪, sem passar pela busca ──────────
function initEntradaAvulsa() {
    document.getElementById('btn-entrada-avulsa').addEventListener('click', () => {
        abrirConfirmacaoNaoEncontrado('');
    });
}

// ─── Cadastro rápido (D1, D6 — nasce sempre PENDENTE) ──────────────────
async function abrirCadastroRapido(placaSugestao, sentidoDepois) {
    document.getElementById('cad-placa').value = placaSugestao || '';
    document.getElementById('cad-propriedade').value = 'PARTICULAR';
    document.getElementById('cad-re-dono').value = '';
    document.getElementById('cad-dono-nome').textContent = '';
    document.getElementById('cad-tipo').value = 'CARRO';
    document.getElementById('cad-cor').value = '';
    document.getElementById('cad-marca-modelo').value = '';
    document.getElementById('cadastro-rapido-erro').style.display = 'none';
    document.getElementById('modal-cadastro-rapido').dataset.sentidoDepois = sentidoDepois || 'ENTRADA';
    atualizarCamposPropriedade();
    abrirModal('modal-cadastro-rapido');
}

function atualizarCamposPropriedade() {
    const prop = document.getElementById('cad-propriedade').value;
    document.getElementById('cad-dono-wrap').style.display = prop === 'PARTICULAR' ? 'block' : 'none';
    document.getElementById('cad-empresa-wrap').style.display = prop === 'TERCEIRO' ? 'block' : 'none';
    if (prop === 'TERCEIRO') preencherSelectEmpresas('cad-empresa');
}

let donoResolvidoId = null;

async function resolverDonoPorRe() {
    const re = document.getElementById('cad-re-dono').value.trim();
    const nomeEl = document.getElementById('cad-dono-nome');
    donoResolvidoId = null;
    nomeEl.textContent = '';
    if (re.length < 2) return;
    try {
        const resultados = await apiGet(`/portaria/funcionarios/busca?q=${encodeURIComponent(re)}`);
        const exato = resultados.find(f => f.re === re);
        if (exato) {
            donoResolvidoId = exato.id;
            nomeEl.textContent = exato.nome;
            nomeEl.style.color = 'var(--accent3)';
        } else if (resultados.length > 0) {
            nomeEl.textContent = `${resultados.length} funcionário(s) encontrados — digite o RE completo`;
            nomeEl.style.color = 'var(--muted)';
        } else {
            // C1 (migration 039): RE não encontrado NÃO bloqueia — regra
            // número um. salvarCadastroRapido manda esse RE em re_dono_texto.
            nomeEl.textContent = 'RE não encontrado no cadastro. O veículo será cadastrado assim mesmo e ficará em Divergências.';
            nomeEl.style.color = 'var(--muted)';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria] erro ao resolver dono por RE:', err);
    }
}

async function preencherSelectEmpresas(selectId) {
    const select = document.getElementById(selectId);
    if (!empresasCache) {
        try {
            empresasCache = await apiGet('/portaria/empresas?apenas_ativas=true');
        } catch (err) {
            if (err instanceof ApiError && err.status === 401) return;
            console.error('[portaria] erro ao carregar empresas:', err);
            empresasCache = [];
        }
    }
    select.innerHTML = '<option value="">Selecione…</option>';
    for (const emp of empresasCache) {
        const opt = document.createElement('option');
        opt.value = emp.id;
        opt.textContent = emp.nome;
        select.appendChild(opt);
    }
}

function initCadastroRapido() {
    document.getElementById('fechar-cadastro-rapido').addEventListener('click', () => fecharModal('modal-cadastro-rapido'));
    document.getElementById('btn-cancelar-cadastro-rapido').addEventListener('click', () => fecharModal('modal-cadastro-rapido'));
    // A1: máscara + aviso visual, nunca bloqueia (D10).
    aplicarMascara(document.getElementById('cad-placa'), 'placa');
    document.getElementById('cad-propriedade').addEventListener('change', atualizarCamposPropriedade);

    let handleRe = null;
    document.getElementById('cad-re-dono').addEventListener('input', () => {
        clearTimeout(handleRe);
        handleRe = setTimeout(resolverDonoPorRe, 250);
    });

    document.getElementById('btn-salvar-cadastro-rapido').addEventListener('click', salvarCadastroRapido);
}

async function salvarCadastroRapido() {
    const erro = document.getElementById('cadastro-rapido-erro');
    erro.style.display = 'none';

    const placa = document.getElementById('cad-placa').value.trim();
    const propriedade = document.getElementById('cad-propriedade').value;
    if (!placa) {
        erro.textContent = 'Digite a placa.';
        erro.style.display = 'block';
        return;
    }

    const payload = {
        propriedade,
        placa,
        tipo: document.getElementById('cad-tipo').value,
        marca_modelo: document.getElementById('cad-marca-modelo').value.trim() || null,
        cor: document.getElementById('cad-cor').value.trim() || null,
    };

    if (propriedade === 'PARTICULAR') {
        const reDono = document.getElementById('cad-re-dono').value.trim();
        if (!donoResolvidoId && !reDono) {
            erro.textContent = 'Informe o RE do dono (PARTICULAR exige dono).';
            erro.style.display = 'block';
            return;
        }
        // C1 (migration 039): RE que não resolveu não bloqueia — vai como
        // re_dono_texto (snapshot) e o veículo fica na fila de Divergências
        // até alguém promover essa pessoa a funcionário.
        if (donoResolvidoId) {
            payload.funcionario_id = donoResolvidoId;
        } else {
            payload.re_dono_texto = reDono;
        }
    } else if (propriedade === 'TERCEIRO') {
        const empresaId = document.getElementById('cad-empresa').value;
        if (!empresaId) {
            erro.textContent = 'Selecione a empresa terceira.';
            erro.style.display = 'block';
            return;
        }
        payload.empresa_terceira_id = empresaId;
    }

    const btn = document.getElementById('btn-salvar-cadastro-rapido');
    btn.disabled = true;
    try {
        await apiPost('/portaria/veiculos', payload);
        const sentidoDepois = document.getElementById('modal-cadastro-rapido').dataset.sentidoDepois || 'ENTRADA';
        fecharModal('modal-cadastro-rapido');
        // Recarrega a busca — agora encontra o veículo recém-cadastrado
        // (PENDENTE, 🟡) e cai no card de confirmação normal.
        const resp = await apiGet(`/portaria/buscar?q=${encodeURIComponent(placa)}`);
        // §4.5-C: guarda explícita — mesma razão do iniciarSaidaDaLista().
        // P13 — reaplica a origem guardada em btn-cadastrar-agora: o
        // cadastro criou o veículo, mas o movimento que nasce a seguir
        // pode continuar sendo crédito da leitura por câmera.
        if (resp.exato && resp.candidatos.length === 1) {
            contexto = {
                veiculo: resp.candidatos[0].veiculo,
                ultimoMovimento: resp.candidatos[0].ultimo_movimento,
                sentido: sentidoDepois,
                movimentoEntradaId: null,
                placaChute: placa,
                origem: origemPendenteCadastro,
                placaLidaBruta: placaLidaBrutaPendenteCadastro,
            };
        } else {
            // Não deveria acontecer — cadastro acabou de suceder — mas a
            // regra número um vale aqui também: nunca trava a tela.
            contexto = {
                veiculo: null, ultimoMovimento: null, sentido: sentidoDepois,
                movimentoEntradaId: null, placaChute: placa,
                origem: origemPendenteCadastro, placaLidaBruta: placaLidaBrutaPendenteCadastro,
            };
        }
        origemPendenteCadastro = 'MANUAL';
        placaLidaBrutaPendenteCadastro = null;
        renderEstadoConfirmacao();
        abrirModal('modal-confirmacao');
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Terceiro (D5) — empresa + veículo + condutor/destino do dia ───────
function initTerceiro() {
    document.getElementById('btn-terceiro').addEventListener('click', abrirModalTerceiro);
    document.getElementById('fechar-terceiro').addEventListener('click', () => fecharModal('modal-terceiro'));
    document.getElementById('btn-cancelar-terceiro').addEventListener('click', () => fecharModal('modal-terceiro'));
    // A1: máscara + aviso visual, nunca bloqueia (D10).
    aplicarMascara(document.getElementById('terc-placa'), 'placa');
    document.getElementById('btn-registrar-terceiro').addEventListener('click', registrarTerceiro);

    let handlePlaca = null;
    document.getElementById('terc-placa').addEventListener('input', () => {
        clearTimeout(handlePlaca);
        handlePlaca = setTimeout(checarPlacaTerceiro, 250);
    });
}

async function abrirModalTerceiro() {
    document.getElementById('terc-placa').value = '';
    document.getElementById('terc-placa-status').textContent = '';
    document.getElementById('terc-condutor').value = '';
    document.getElementById('terc-destino').value = '';
    document.getElementById('terc-sentido').value = 'ENTRADA';
    document.getElementById('terceiro-erro').style.display = 'none';
    await preencherSelectEmpresas('terc-empresa');
    abrirModal('modal-terceiro');
}

let veiculoTerceiroEncontrado = null;

async function checarPlacaTerceiro() {
    const placa = document.getElementById('terc-placa').value.trim();
    const statusEl = document.getElementById('terc-placa-status');
    veiculoTerceiroEncontrado = null;
    statusEl.textContent = '';
    if (!placa) return;
    try {
        const resp = await apiGet(`/portaria/buscar?q=${encodeURIComponent(placa)}`);
        const c = resp.candidatos.find(c => c.veiculo.placa === normalizarPlacaFrontend(placa));
        if (c) {
            veiculoTerceiroEncontrado = c.veiculo;
            statusEl.textContent = `Já cadastrado — ${c.veiculo.empresa_terceira_nome || 'sem empresa vinculada'} (${badgeCurto(c.veiculo.situacao)} ${c.veiculo.situacao})`;
            statusEl.style.color = 'var(--muted)';
            const empresaSelect = document.getElementById('terc-empresa');
            if (c.veiculo.empresa_terceira_id) empresaSelect.value = c.veiculo.empresa_terceira_id;
        } else {
            statusEl.textContent = 'Veículo novo — será cadastrado nesta empresa ao registrar.';
            statusEl.style.color = 'var(--muted)';
        }
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        console.error('[portaria] erro ao checar placa de terceiro:', err);
    }
}

async function registrarTerceiro() {
    const erro = document.getElementById('terceiro-erro');
    erro.style.display = 'none';

    const empresaId = document.getElementById('terc-empresa').value;
    const placa = document.getElementById('terc-placa').value.trim();
    const condutor = document.getElementById('terc-condutor').value.trim();
    const destino = document.getElementById('terc-destino').value.trim();
    const sentido = document.getElementById('terc-sentido').value;

    if (!empresaId) { erro.textContent = 'Selecione a empresa.'; erro.style.display = 'block'; return; }
    if (!placa) { erro.textContent = 'Digite a placa.'; erro.style.display = 'block'; return; }
    if (!condutor) { erro.textContent = 'Informe quem está dirigindo hoje.'; erro.style.display = 'block'; return; }

    const empresaNome = (empresasCache || []).find(e => e.id === empresaId)?.nome || '';
    const btn = document.getElementById('btn-registrar-terceiro');
    btn.disabled = true;
    try {
        // Veículo de terceiro desconhecido: cadastra na hora (nasce
        // PENDENTE, D6) antes de registrar o movimento — regra número um
        // não impede o registro, mas o cadastro fica pendurado na empresa
        // certa em vez de virar avulso perdido.
        if (!veiculoTerceiroEncontrado) {
            await apiPost('/portaria/veiculos', {
                propriedade: 'TERCEIRO',
                empresa_terceira_id: empresaId,
                placa,
                tipo: 'CARRO',
            });
        }
        const resp = await apiPost('/portaria/movimentos', {
            sentido,
            placa,
            terceiro_nome: condutor,
            terceiro_destino: destino || null,
            terceiro_empresa: empresaNome || null,
        });
        fecharModal('modal-terceiro');
        veiculoTerceiroEncontrado = null;
        await carregarDentro();
        if (resp.avisos && resp.avisos.length > 0) mostrarAvisoTopo(resp.avisos.join(' · '));
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        erro.textContent = err.message;
        erro.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ─── Leitura de placa por câmera — substitui o QR do Bloco E ───────────
// _handoff-claude/PROMPT-leitura-placa.md, P1-P14. D15 continua valendo:
// a leitura cai no MESMO card de confirmação da busca por placa (via
// executarBusca, ⛔ nunca duplicada aqui) — o QR/câmera é aceleração,
// nunca credencial de segurança nem pré-requisito.
//
// P9: captura SOB COMANDO (um toque em "Capturar"), nunca detecção
// contínua — poupa bateria e evita ler a placa errada na fila.
//
// ⚠️ `getUserMedia` só funciona em HTTPS ou localhost — não em
// http://192.168.x.x. P8: o gate agora é `navigator.mediaDevices?.
// getUserMedia`, que existe em qualquer navegador atual sob HTTPS — bem
// mais gente vê o botão do que via BarcodeDetector, então o erro caindo
// bonito no card (nunca tela branca) deixa de ser detalhe.
let cameraPlacaStream = null;

function initLeitorPlaca() {
    if (!navigator.mediaDevices?.getUserMedia) return;
    const btn = document.getElementById('btn-ler-placa');
    btn.style.display = '';
    btn.addEventListener('click', abrirCameraPlaca);
    document.getElementById('fechar-placa').addEventListener('click', fecharCameraPlaca);
    document.getElementById('btn-cancelar-placa').addEventListener('click', fecharCameraPlaca);
    document.getElementById('btn-cancelar-placa-confirma').addEventListener('click', fecharCameraPlaca);
    document.getElementById('btn-capturar-placa').addEventListener('click', capturarFramePlaca);
    document.getElementById('btn-ler-placa-de-novo').addEventListener('click', abrirCameraPlaca);
    document.getElementById('btn-confirmar-placa-lida').addEventListener('click', confirmarPlacaLida);
    // A1: máscara + aviso visual, nunca bloqueia (D10) — mesmo padrão de
    // #confirmacao-placa-input/#cad-placa.
    aplicarMascara(document.getElementById('placa-confirma-input'), 'placa');
}

function mostrarViewCameraPlaca() {
    document.getElementById('placa-camera-view').style.display = '';
    document.getElementById('placa-confirma-view').style.display = 'none';
}

function mostrarViewConfirmaPlaca() {
    document.getElementById('placa-camera-view').style.display = 'none';
    document.getElementById('placa-confirma-view').style.display = '';
}

async function abrirCameraPlaca() {
    document.getElementById('placa-camera-erro').style.display = 'none';
    mostrarViewCameraPlaca();
    abrirModal('modal-placa');
    try {
        cameraPlacaStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        const video = document.getElementById('placa-video');
        video.srcObject = cameraPlacaStream;
        await video.play();
    } catch (err) {
        // P7 — câmera negada/indisponível: mensagem no card, sem tela
        // branca. A busca por placa digitada continua funcionando normal.
        pararCameraPlaca();
        document.getElementById('placa-camera-erro').textContent = 'Não foi possível abrir a câmera: ' + err.message;
        document.getElementById('placa-camera-erro').style.display = 'block';
    }
}

function pararCameraPlaca() {
    // P10 — desliga na hora, sempre antes de qualquer chamada de rede.
    if (cameraPlacaStream) {
        cameraPlacaStream.getTracks().forEach((track) => track.stop());
        cameraPlacaStream = null;
    }
}

function fecharCameraPlaca() {
    pararCameraPlaca();
    fecharModal('modal-placa');
    origemProximaConfirmacao = 'MANUAL';
    placaLidaBrutaAtual = null;
}

async function capturarFramePlaca() {
    const video = document.getElementById('placa-video');
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    // P10 — desliga a câmera IMEDIATAMENTE, antes de qualquer chamada de
    // rede (a luz acesa a noite inteira come a bateria do plantão).
    pararCameraPlaca();

    mostrarViewConfirmaPlaca();
    const statusEl = document.getElementById('placa-confirma-status');
    const inputEl = document.getElementById('placa-confirma-input');
    const erroEl = document.getElementById('placa-confirma-erro');
    erroEl.style.display = 'none';
    inputEl.value = '';
    placaLidaBrutaAtual = null;
    statusEl.textContent = 'Lendo…';

    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
    if (!blob) {
        statusEl.textContent = '';
        erroEl.textContent = 'Não foi possível capturar a imagem — tente "Ler de novo" ou digite a placa.';
        erroEl.style.display = 'block';
        setTimeout(() => inputEl.focus(), 50);
        return;
    }

    const formData = new FormData();
    formData.append('arquivo', blob, 'placa.jpg');

    try {
        const resp = await apiUpload('/portaria/ler-placa', formData);
        // P13 — o texto CRU da leitura, guardado ANTES de qualquer edição
        // do controlador no campo abaixo (mesmo que ele corrija).
        placaLidaBrutaAtual = resp.placa_lida;
        inputEl.value = resp.placa_lida || '';
        statusEl.textContent = resp.placa_lida
            ? 'Confirme ou corrija a placa lida.'
            : 'Não conseguimos ler a placa — digite manualmente.';
    } catch (err) {
        if (err instanceof ApiError && err.status === 401) return;
        // P7 — falha de leitura (desligada, timeout, servidor fora) cai
        // pra digitação no mesmo campo, sem trocar de tela.
        statusEl.textContent = '';
        erroEl.textContent = 'Erro na leitura: ' + err.message + ' — digite a placa manualmente.';
        erroEl.style.display = 'block';
    } finally {
        setTimeout(() => inputEl.focus(), 50);
    }
}

function confirmarPlacaLida() {
    const erroEl = document.getElementById('placa-confirma-erro');
    const valor = normalizarPlacaFrontend(document.getElementById('placa-confirma-input').value.trim());
    if (!valor) {
        erroEl.textContent = 'Digite ou confirme a placa.';
        erroEl.style.display = 'block';
        return;
    }
    // P1/D15: nunca um segundo card — fecha este modal e cai na MESMA
    // busca por placa da digitação manual, que decide sozinha entre
    // achado (⛔ nunca um palpite) e "Cadastrar agora" (P6).
    origemProximaConfirmacao = 'CAMERA';
    fecharModal('modal-placa');
    executarBusca(valor);
}

// ─── Bloco C — Recolhida virou botão (saiu da barra de navegação) ──────
function initRecolhida() {
    if (!podeEscrever('recolhida_anormal')) return;
    const btn = document.getElementById('btn-recolhida');
    btn.style.display = '';
    btn.addEventListener('click', () => {
        window.location.href = 'portaria-recolhida.html';
    });
}

// ─── Bloco G — Avaria na saída da frota (mesmo botão, mesmo padrão) ────
function initAvaria() {
    if (!podeEscrever('acesso_veicular')) return;
    const btn = document.getElementById('btn-avaria');
    btn.style.display = '';
    btn.addEventListener('click', () => {
        window.location.href = 'portaria-avaria.html';
    });
}

// ─── Polling da lista "dentro agora" ────────────────────────────────────
function startPolling() {
    if (pollHandle) return;
    pollHandle = setInterval(() => {
        // Não atualiza a lista debaixo dos dedos enquanto algum modal
        // está aberto — evita perder o que a pessoa estava digitando.
        if (document.querySelector('.modal-overlay.open')) return;
        carregarDentro();
    }, POLLING_INTERVAL_MS);
}

// ─── Bootstrap ───────────────────────────────────────────────────────────
initHeader();
initBusca();
initConfirmacao();
initEntradaAvulsa();
initCadastroRapido();
initTerceiro();
initRecolhida();
initAvaria();
initLeitorPlaca();
carregarDentro();
startPolling();
