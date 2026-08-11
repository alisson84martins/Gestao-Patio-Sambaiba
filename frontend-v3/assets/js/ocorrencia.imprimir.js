/*
 * ocorrencia.imprimir.js — Impressão fiel ao Relatório de Ocorrências de papel
 * -----------------------------------------------------------------------------
 * Reescrito em 01/08/2026 seguindo _handoff-claude/MOCKUP-impressao-ocorrencia.html
 * ao pé da letra (especificação visual, não sugestão). Combinação aprovada por
 * Alisson:
 *   1C  híbrido — identidade visual do papel, só o conteúdo que existe
 *   2B  seção sem dado some, vira uma linha discreta "não registrado"
 *   3A  todas as opções aparecem com quadradinho; a marcada, preenchida
 *   5A  quatro páginas fixas, cada seção em folha nova
 * Dados pessoais (vítima, terceiro) SEMPRE completos — o impresso alimenta o
 * processo de sinistro, não pode sair mascarado.
 *
 * Antes (338 linhas, testado 31/07): saía em 5 folhas numa ocorrência simples
 * porque 3 linhas fabricavam blocos vazios (2 veículos + 3 testemunhas +
 * 3 vítimas mesmo sem dado) — 8 blocos de "—" empurrando as assinaturas pra
 * 5ª folha, com o rodapé mentindo "Página 4 de 4". Aqui: só imprime o que
 * existe; a ausência de item vira UMA linha de aviso (2B), não blocos em
 * branco.
 *
 * O nome e RE do coordenador na assinatura vêm de quem está logado no
 * momento da impressão (não de quem originalmente registrou) — é assim que
 * o formulário em papel funciona hoje: quem imprime assina.
 *
 * ⚠️ Não mexe no @media print do pátio (style.css:1489) nem reaproveita
 * suas classes — todo seletor novo usa o prefixo oc-print-.
 */
import { getCurrentUser } from './auth.js';
import { escapeHtml } from './escape.js';
import {
    ANALISE_GRUPOS, REGIOES_AVARIA, TIPOS_ANEXO, DANOS_VEICULO,
    TIPOS_OCORRENCIA, REGISTRO_LOCAL, SENTIDO_OPCOES,
} from './ocorrencia.vocabulario.js';

const SLOT_VEICULOS = 2;
const SLOT_TESTEMUNHAS = 3;
const SLOT_VITIMAS = 3;

// ─── Helpers genéricos ──────────────────────────────────────────────────────
// escapeHtml agora vem de ./escape.js — módulo único (SEV-07).

function fmtData(iso) {
    if (!iso) return '—';
    const [ano, mes, dia] = iso.split('-');
    return `${dia}/${mes}/${ano}`;
}

function fmtHora(hhmmss) {
    if (!hhmmss) return '—';
    const [h, m] = hhmmss.split(':');
    return `${h}:${m}`;
}

function valOrDash(v) {
    return (v === null || v === undefined || v === '') ? '—' : escapeHtml(String(v));
}

/** Campo com rótulo fixo à esquerda e linha de preenchimento — bloco `.f`. */
function campo(rotulo, valor, largura = 'w33') {
    return `<div class="oc-print-f oc-print-${largura}">
        <span class="oc-print-rot">${escapeHtml(rotulo)}</span>
        <span class="oc-print-val">${valOrDash(valor)}</span>
    </div>`;
}

/** Um quadradinho — preenchido (marcado) ou vazio. */
function opSpan(label, marcado, margemEsq = null) {
    const estilo = margemEsq !== null ? ` style="margin-left:${margemEsq}px"` : '';
    return `<span class="oc-print-op${marcado ? ' marcado' : ''}"${estilo}>` +
        `<i class="oc-print-bx${marcado ? ' on' : ''}"></i>${escapeHtml(label)}</span>`;
}

/** Par Sim/Não embutido dentro de um `.f` (ex: "Direção defensiva? ☑Sim ☐Não"). */
function simNao(valor) {
    return opSpan('Sim', valor === true, 4) + opSpan('Não', valor === false, 6);
}

/** Linha `.opts` — rótulo fixo + todas as opções com quadradinho (3A). */
function linhaOpcoes(rotulo, opcoes, ehMarcado, estiloExtra = '') {
    const spans = opcoes.map(([codigo, label]) => opSpan(label, ehMarcado(codigo))).join('');
    return `<div class="oc-print-opts"${estiloExtra ? ` style="${estiloExtra}"` : ''}>
        <span class="oc-print-rot">${escapeHtml(rotulo)}</span>${spans}
    </div>`;
}

/** Linha `.mtz` — mesma ideia de linhaOpcoes, layout mais estreito (página de análise). */
function linhaMatriz(rotulo, opcoes, valorAtual) {
    const spans = opcoes.map(([codigo, label]) => opSpan(label, codigo === valorAtual)).join('');
    return `<div class="oc-print-mtz">
        <span class="oc-print-rot">${escapeHtml(rotulo)}</span>
        <span class="oc-print-ops">${spans}</span>
    </div>`;
}

function cabecalho(oc) {
    return `<div class="oc-print-cab">
        <div class="oc-print-logo"><span class="oc-print-eu">Eu ♥</span>Sambaíba</div>
        <h1>RELATÓRIO DE OCORRÊNCIAS</h1>
        <div class="oc-print-num">Nº <span>${oc.numero ?? '—'}</span></div>
    </div>`;
}

function subcabCapa(oc) {
    const agora = new Date();
    const emitido = `${String(agora.getDate()).padStart(2, '0')}/${String(agora.getMonth() + 1).padStart(2, '0')}/${agora.getFullYear()}` +
        ` às ${String(agora.getHours()).padStart(2, '0')}:${String(agora.getMinutes()).padStart(2, '0')}`;
    return `<div class="oc-print-subcab">
        <span>SAMBAÍBA TRANSPORTES URBANOS — GARAGEM 3</span>
        <span>Emitido em ${emitido} · Status: ${escapeHtml(oc.status)}</span>
    </div>`;
}

function subcabPagina(oc, secao) {
    return `<div class="oc-print-subcab">
        <span>Ocorrência nº ${oc.numero ?? '—'} · ${fmtData(oc.data_ocorrencia)} · Prefixo ${escapeHtml(oc.prefixo || '—')}</span>
        <span>${escapeHtml(secao)}</span>
    </div>`;
}

function rodape(oc, secaoComPagina) {
    return `<div class="oc-print-pe">
        <span>Relatório de Ocorrências nº ${oc.numero ?? '—'}</span>
        <span>${escapeHtml(secaoComPagina)}</span>
    </div>`;
}

/** 2B — nome do tipo, com "Outros — <descrição>" quando aplicável (item 3d). */
function nomeTipoExibicao(oc) {
    const tipo = oc.tipo_ocorrencia;
    if (!tipo) return null;
    if (tipo.codigo === 'OUTROS' && oc.tipo_outros_descricao) {
        return `${tipo.nome} — ${oc.tipo_outros_descricao}`;
    }
    return tipo.nome;
}

/**
 * 2B — aviso de ausência para listas com "slots" do papel (veículo,
 * testemunha, vítima): nenhuma linha de bloco vazio, só um aviso discreto
 * quando falta preencher algum dos slots esperados.
 */
function avisoAusencia(qtd, esperado, singular, plural, fraseZero, feminino) {
    if (qtd >= esperado) return '';
    const particip = feminino ? 'registrada' : 'registrado';
    if (qtd === 0) {
        const artigo = feminino ? 'Nenhuma' : 'Nenhum';
        return `<div class="oc-print-vazio">${artigo} ${fraseZero} ${particip} nesta ocorrência.</div>`;
    }
    const faltantes = [];
    for (let i = qtd + 1; i <= esperado; i++) faltantes.push(i);
    if (faltantes.length === 1) {
        return `<div class="oc-print-vazio">${singular} nº ${faltantes[0]} — não ${particip} nesta ocorrência.</div>`;
    }
    const lista = `${faltantes.slice(0, -1).join(', ')} e ${faltantes[faltantes.length - 1]}`;
    return `<div class="oc-print-vazio">${plural} ${lista} — não ${particip}s nesta ocorrência.</div>`;
}

// ─── Página 1 — Capa ────────────────────────────────────────────────────────

function paginaCapa(oc) {
    const tipo = oc.tipo_ocorrencia;

    return `<div class="oc-print-folha">
        ${cabecalho(oc)}
        ${subcabCapa(oc)}

        <div class="oc-print-faixa">Dados da ocorrência</div>
        <div class="oc-print-linhas">
            ${campo('Data', fmtData(oc.data_ocorrencia), 'w25')}
            ${campo('Horário', fmtHora(oc.hora_ocorrencia), 'w25')}
            ${campo('Prefixo', oc.prefixo, 'w25')}
            ${campo('Placa', oc.placa, 'w25')}
            ${campo('Linha', oc.linha_codigo, 'w33')}
            ${campo('Velocidade da via', oc.velocidade_via != null ? `${oc.velocidade_via} km/h` : null, 'w33')}
            ${campo('Velocidade do ônibus', oc.velocidade_onibus != null ? `${oc.velocidade_onibus} km/h` : null, 'w33')}
        </div>

        ${linhaOpcoes('Tipo de ocorrência', TIPOS_OCORRENCIA.map(([codigo, nome]) => {
                const marcadaComDescricao = codigo === 'OUTROS' && codigo === tipo?.codigo;
                return [codigo, marcadaComDescricao ? (nomeTipoExibicao(oc) || nome) : nome];
            }),
            codigo => tipo?.codigo === codigo, 'margin-top:3px')}
        <div class="oc-print-opts">
            <span class="oc-print-rot">Registro / local</span>
            ${REGISTRO_LOCAL.map(([campoNome, label]) => opSpan(label, oc[campoNome] === true)).join('')}
            ${SENTIDO_OPCOES.map(op => opSpan(op, oc.sentido === op)).join('')}
        </div>

        <div class="oc-print-faixa">Condutor e cobrador</div>
        <div class="oc-print-linhas">
            ${campo('Nome do condutor', oc.condutor_nome, 'w50')}
            ${campo('RE', oc.condutor_re, 'w25')}
            ${campo('Função', oc.condutor_funcao, 'w25')}
            ${campo('CNH nº', oc.condutor_cnh, 'w33')}
            ${campo('RG nº', oc.condutor_rg, 'w33')}
            ${campo('CPF nº', oc.condutor_cpf, 'w33')}
            ${campo('Nome do cobrador', oc.cobrador_nome, 'w50')}
            ${campo('RE', oc.cobrador_re, 'w25')}
            <div class="oc-print-f oc-print-w25">
                <span class="oc-print-rot">Direção defensiva</span>
                ${simNao(oc.direcao_defensiva)}
            </div>
        </div>

        <div class="oc-print-faixa">Local</div>
        <div class="oc-print-linhas">
            ${campo('Local do acidente', oc.local_ocorrido, 'w100')}
            ${campo('Bairro', oc.bairro, 'w50')}
            ${campo('Cidade', oc.cidade, 'w50')}
            ${campo('Qtd. acidentes', oc.quant_acidentes, 'w33')}
            ${campo('Isentos', oc.isentos, 'w33')}
            ${campo('Culpados', oc.culpados, 'w33')}
        </div>
        <div class="oc-print-linhas">
            <div class="oc-print-f oc-print-w50">
                <span class="oc-print-rot">Foi ao local?</span>${simNao(oc.foi_ao_local)}
                <span class="oc-print-rot" style="margin-left:14px">Confirmado?</span>${simNao(oc.confirmado)}
            </div>
            <div class="oc-print-f oc-print-w50">
                <span class="oc-print-rot">Problemas mecânicos?</span>${simNao(oc.problemas_mecanicos)}
                <span class="oc-print-rot" style="margin-left:10px">Qual</span>
                <span class="oc-print-val">${valOrDash(oc.problemas_mecanicos_qual)}</span>
            </div>
            <div class="oc-print-f oc-print-w100">
                <span class="oc-print-rot">Condutor avisou a manutenção?</span>${simNao(oc.condutor_avisou_manutencao)}
                <span class="oc-print-rot" style="margin-left:12px">Nome</span>
                <span class="oc-print-val">${valOrDash(oc.manutencao_avisado_nome)}</span>
            </div>
        </div>

        <div class="oc-print-faixa">Descrição do coordenador</div>
        <div class="oc-print-txt">${escapeHtml(oc.descricao_coordenador) || '—'}</div>

        <div class="oc-print-faixa">Descrição do motorista</div>
        <div class="oc-print-txt">${escapeHtml(oc.descricao_motorista) || '—'}</div>

        <div class="oc-print-faixa">Descrição do terceiro</div>
        <div class="oc-print-txt">${escapeHtml(oc.descricao_terceiro) || '—'}</div>

        ${rodape(oc, 'Página 1 de 4 — Capa')}
    </div>`;
}

// ─── Página 2 — Veículos e avarias ──────────────────────────────────────────

function blocoVeiculo(v, idx) {
    return `<div class="oc-print-bloco">
        <div class="oc-print-bloco-tit">Veículo nº ${idx + 1}</div>
        ${linhaOpcoes('Danos', DANOS_VEICULO, codigo => v.danos === codigo)}
        <div class="oc-print-linhas">
            ${campo('Marca', v.marca, 'w33')}
            ${campo('Modelo', v.modelo, 'w33')}
            ${campo('Ano', v.ano, 'w33')}
            ${campo('Cor', v.cor, 'w25')}
            ${campo('Placa', v.placa, 'w25')}
            ${campo('Cidade', v.cidade_placa, 'w25')}
            ${campo('UF', v.estado_placa, 'w25')}
            ${campo('Renavam', v.renavam, 'w33')}
            ${campo('Proprietário/Motorista', v.proprietario, 'w50')}
            ${campo('Fones', v.fones, 'w50')}
            ${campo('E-mail', v.email, 'w50')}
            ${campo('Endereço', v.endereco, 'w100')}
            ${campo('RG', v.rg, 'w33')}
            ${campo('CPF', v.cpf, 'w33')}
            ${campo('CNH', v.cnh, 'w33')}
            ${campo('Seguradora', v.seguradora, 'w33')}
            ${campo('Fone seguradora', v.seguradora_fone, 'w33')}
            ${campo('Sinistro nº', v.sinistro_numero, 'w33')}
            ${campo('Partes avariadas', v.partes_avariadas, 'w100')}
        </div>
    </div>`;
}

function paginaVeiculos(oc) {
    const veiculos = oc.veiculos_terceiro || [];
    const avarias = oc.avarias || [];
    const avariasPorRegiao = new Map(avarias.map(a => [a.regiao, a]));

    const descricoesAvaria = REGIOES_AVARIA
        .map(([codigo, label]) => [label, avariasPorRegiao.get(codigo)])
        .filter(([, a]) => a?.descricao)
        .map(([label, a]) => campo(label, a.descricao, 'w100'))
        .join('');

    return `<div class="oc-print-folha">
        ${cabecalho(oc)}
        ${subcabPagina(oc, 'Veículos')}

        <div class="oc-print-faixa">Veículos de terceiro</div>
        ${veiculos.map(blocoVeiculo).join('')}
        ${avisoAusencia(veiculos.length, SLOT_VEICULOS, 'Veículo', 'Veículos', 'veículo de terceiro', false)}

        <div class="oc-print-faixa">Avarias do nosso veículo — por região</div>
        <div class="oc-print-mtz">
            <span class="oc-print-rot">Regiões atingidas</span>
            <span class="oc-print-ops">${REGIOES_AVARIA.map(([codigo, label]) => opSpan(label, avariasPorRegiao.has(codigo))).join('')}</span>
        </div>
        ${avarias.length ? `
            <div class="oc-print-sub">Descrição das avarias marcadas</div>
            <div class="oc-print-linhas">${descricoesAvaria || campo('Descrição', null, 'w100')}</div>
        ` : '<div class="oc-print-vazio">Nenhuma avaria registrada no nosso veículo.</div>'}

        ${rodape(oc, 'Página 2 de 4 — Veículos')}
    </div>`;
}

// ─── Página 3 — Análise do acidente ─────────────────────────────────────────

function paginaAnalise(oc) {
    const a = oc.analise || {};
    const grupos = ANALISE_GRUPOS.map(grupo => {
        const linhas = grupo.campos.map(c => {
            if (c.texto) return campo(c.label, a[c.name], 'w100');
            return linhaMatriz(c.label, c.opcoes, a[c.name] ?? null);
        }).join('');
        return `<div class="oc-print-faixa">${escapeHtml(grupo.titulo)}</div>${linhas}`;
    }).join('');

    const croquiAnexo = (oc.anexos || []).find(x => x.tipo === 'CROQUI');
    const croqui = croquiAnexo
        ? campo('Croqui anexado ao sistema', croquiAnexo.nome_original || croquiAnexo.caminho, 'w100')
        : `<div class="oc-print-croqui"></div>
           <div class="oc-print-vazio">Croqui não anexado no sistema — espaço acima disponível para desenho à mão.</div>`;

    return `<div class="oc-print-folha">
        ${cabecalho(oc)}
        ${subcabPagina(oc, 'Análise')}

        ${grupos}

        <div class="oc-print-faixa">Croqui detalhado da ocorrência</div>
        ${croqui}

        <div class="oc-print-corte"></div>
        <div class="oc-print-comunicacao">
            <div class="oc-print-logo" style="font-size:13px"><span class="oc-print-eu">Eu ♥</span>Sambaíba</div>
            <div>
                <strong>COMUNICAÇÃO DE ACIDENTE SAMBAÍBA</strong><br>
                Prezado(a), para comunicação de seu acidente junto à Sambaíba, disponibilizamos os seguintes canais de atendimento:<br>
                Site: <strong>www.sambaibasp.com.br/sinistro</strong> &nbsp;·&nbsp; Telefones: <strong>(11) 2990-4445</strong> — ramais 4475 e 4467
            </div>
        </div>

        ${rodape(oc, 'Página 3 de 4 — Análise')}
    </div>`;
}

// ─── Página 4 — Testemunhas, vítimas e fechamento ──────────────────────────

function blocoTestemunha(t, idx) {
    return `<div class="oc-print-bloco">
        <div class="oc-print-bloco-tit">Testemunha ${idx + 1}</div>
        <div class="oc-print-linhas">
            ${campo('Nome', t.nome, 'w50')}
            ${campo('RG nº', t.rg, 'w25')}
            ${campo('Fone 1', t.fone1, 'w25')}
            ${campo('Endereço', t.endereco, 'w50')}
            ${campo('Nº', t.numero, 'w25')}
            ${campo('Fone 2', t.fone2, 'w25')}
            ${campo('Bairro', t.bairro, 'w50')}
            ${campo('Cidade', t.cidade, 'w50')}
        </div>
    </div>`;
}

function blocoVitima(v, idx) {
    return `<div class="oc-print-bloco">
        <div class="oc-print-bloco-tit">Pessoa vitimada nº ${idx + 1}</div>
        <div class="oc-print-linhas">
            ${campo('Nome', v.nome, 'w50')}
            ${campo('RG nº', v.rg, 'w25')}
            ${campo('CPF', v.cpf, 'w25')}
            ${campo('Idade', v.idade, 'w25')}
            ${campo('Fone', v.fone, 'w25')}
            <div class="oc-print-f oc-print-w50">
                <span class="oc-print-rot">Era passageiro do nosso ônibus?</span>${simNao(v.era_passageiro)}
            </div>
            ${campo('Endereço', v.endereco, 'w50')}
            ${campo('Nº', v.numero, 'w25')}
            ${campo('Bairro', v.bairro, 'w25')}
            ${campo('Cidade', v.cidade, 'w33')}
            ${campo('Socorrida para', v.destino_socorro, 'w33')}
            ${campo(v.contato_parentesco || 'Contato', v.contato_nome, 'w33')}
            ${campo('Fone do contato', v.contato_fone, 'w33')}
        </div>
        ${v.dados_pessoais ? `<div class="oc-print-sub">Dados pessoais (observações)</div><div class="oc-print-txt">${escapeHtml(v.dados_pessoais)}</div>` : ''}
    </div>`;
}

function paginaTestemunhas(oc) {
    const testemunhas = oc.testemunhas || [];
    const vitimas = oc.vitimas || [];
    const autoridades = oc.autoridades || [];

    const linhasAutoridades = autoridades.map(a => {
        const detalhe = [a.identificacao, a.responsavel ? `${a.responsavel}` : null].filter(Boolean).join(' · ');
        return campo(a.orgao?.nome || 'Órgão', detalhe, 'w50');
    }).join('');

    const anexosPorTipo = new Map();
    for (const x of (oc.anexos || [])) {
        if (x.tipo === 'CROQUI') continue; // já mostrado na página 3
        anexosPorTipo.set(x.tipo, (anexosPorTipo.get(x.tipo) || 0) + 1);
    }
    const linhasAnexos = [...anexosPorTipo.entries()]
        .map(([tipo, qtd]) => campo(TIPOS_ANEXO[tipo] || tipo, `${qtd} arquivo${qtd > 1 ? 's' : ''}`, 'w50'))
        .join('');

    const user = getCurrentUser();

    return `<div class="oc-print-folha">
        ${cabecalho(oc)}
        ${subcabPagina(oc, 'Testemunhas e vítimas')}

        <div class="oc-print-faixa">Testemunhas</div>
        ${testemunhas.map(blocoTestemunha).join('')}
        ${avisoAusencia(testemunhas.length, SLOT_TESTEMUNHAS, 'Testemunha', 'Testemunhas', 'testemunha', true)}

        <div class="oc-print-faixa">Pessoas vitimadas</div>
        ${vitimas.map(blocoVitima).join('')}
        ${avisoAusencia(vitimas.length, SLOT_VITIMAS, 'Pessoa vitimada', 'Pessoas vitimadas', 'pessoa vitimada', true)}

        <div class="oc-print-faixa">Autoridades no local</div>
        ${autoridades.length
            ? `<div class="oc-print-linhas">${linhasAutoridades}</div>`
            : '<div class="oc-print-vazio">Nenhuma autoridade no local registrada nesta ocorrência.</div>'}

        <div class="oc-print-faixa">Ocorrência policial</div>
        <div class="oc-print-linhas">
            <div class="oc-print-f oc-print-w33">
                <span class="oc-print-rot">Ocorrência policial?</span>${simNao(oc.ocorrencia_policial)}
            </div>
            ${campo('Viatura nº', oc.viatura_numero, 'w33')}
            ${campo('BPM', oc.bpm, 'w33')}
            ${campo('CIA', oc.cia, 'w33')}
            ${campo('Distrito', oc.distrito, 'w33')}
            ${campo('Nº TO', oc.numero_to, 'w33')}
            ${campo('Nº BO', oc.numero_bo, 'w33')}
            ${campo('Protocolo', oc.protocolo, 'w33')}
            <div class="oc-print-f oc-print-w33">
                <span class="oc-print-rot">Houve polícia técnica?</span>${simNao(oc.houve_policia_tecnica)}
            </div>
            ${campo('Nome do perito', oc.nome_perito, 'w100')}
        </div>

        <div class="oc-print-faixa">Observações</div>
        <div class="oc-print-txt">${escapeHtml(oc.observacoes) || '—'}</div>

        <div class="oc-print-faixa">Anexos enviados ao sistema</div>
        ${anexosPorTipo.size
            ? `<div class="oc-print-linhas">${linhasAnexos}</div>`
            : '<div class="oc-print-vazio">Nenhum anexo enviado ao sistema.</div>'}

        <div class="oc-print-faixa">Relatórios entregues ao controlador de acesso</div>
        <div class="oc-print-txt" style="min-height:20px">${escapeHtml(oc.controlador_acesso) || '—'}</div>

        <div class="oc-print-assinaturas">
            <div>
                <div class="oc-print-risco"></div>
                <div class="oc-print-nome">${escapeHtml(user?.nome) || '—'}</div>
                <div>Coordenador / RE ${escapeHtml(user?.re) || '—'}</div>
            </div>
            <div>
                <div class="oc-print-risco"></div>
                <div class="oc-print-nome">${escapeHtml(oc.condutor_nome) || '—'}</div>
                <div>Motorista / RE ${escapeHtml(oc.condutor_re) || '—'}</div>
            </div>
        </div>

        ${rodape(oc, 'Página 4 de 4 — Testemunhas e vítimas')}
    </div>`;
}

/**
 * Monta as 4 páginas e chama window.print() — mesmo padrão de menu.js _print().
 * @param {object} oc — OcorrenciaCompleta (GET /ocorrencias/{id})
 */
export function imprimirOcorrencia(oc) {
    let area = document.getElementById('print-content');
    if (!area) {
        area = document.createElement('div');
        area.id = 'print-content';
        document.body.appendChild(area);
    }
    area.innerHTML = paginaCapa(oc) + paginaVeiculos(oc) + paginaAnalise(oc) + paginaTestemunhas(oc);
    window.print();
}
