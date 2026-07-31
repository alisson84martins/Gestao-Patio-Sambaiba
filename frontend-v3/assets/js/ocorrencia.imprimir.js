/*
 * ocorrencia.imprimir.js — Impressão fiel das 4 páginas do relatório
 * -----------------------------------------------------------------------------
 * Sempre imprime as 4 páginas completas (Capa, Veículos, Análise,
 * Testemunhas), mesmo com campos em branco — é assim que o Alisson entrega
 * hoje. Usa window.print() + CSS @media print (.oc-print-*, em style.css),
 * igual ao resto do sistema (ver menu.js _print()).
 *
 * O nome e RE do coordenador na assinatura vêm de quem está logado no
 * momento da impressão (não de quem originalmente registrou) — é assim que
 * o formulário em papel funciona hoje: quem imprime assina.
 */
import { getCurrentUser } from './auth.js';
import { ANALISE_GRUPOS, REGIOES_AVARIA, TIPOS_ANEXO } from './ocorrencia.vocabulario.js';

function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

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

function fmtBool(v) {
    if (v === true) return 'Sim';
    if (v === false) return 'Não';
    return '—';
}

function campo(label, valor) {
    return `<div class="oc-print-field">
        <span class="oc-print-label">${escapeHtml(label)}</span>
        <span class="oc-print-valor">${valor === null || valor === undefined || valor === '' ? '—' : escapeHtml(String(valor))}</span>
    </div>`;
}

function cabecalhoPagina(subtitulo) {
    return `<div class="oc-print-header">
        <div class="oc-print-empresa">Sambaíba Transportes Urbanos — Garagem 3</div>
        <div class="oc-print-titulo">RELATÓRIO DE OCORRÊNCIAS</div>
        <div class="oc-print-sub">${escapeHtml(subtitulo)}</div>
    </div>`;
}

// ── Página 1 — Capa ─────────────────────────────────────────────────────
function paginaCapa(oc) {
    const telefones = `
        <div class="oc-print-field" style="grid-column:1 / -1">
            <span class="oc-print-label">Telefones úteis</span>
            <span class="oc-print-valor" style="font-weight:400;font-size:9px">
                CET 1188 · Polícia Militar 190 · Pronto Socorro 192 · Bombeiros/Resgate 193 ·
                Eletropaulo 0800 7272195 · Sabesp 195
            </span>
        </div>`;

    return `<div class="oc-print-page">
        ${cabecalhoPagina('Página 1 de 4 — Capa')}
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Nº', oc.numero)}
            ${campo('Data do Acidente', fmtData(oc.data_ocorrencia))}
            ${campo('Horário', fmtHora(oc.hora_ocorrencia))}
            ${campo('Tipo de Ocorrência', oc.tipo_ocorrencia?.nome)}
            ${campo('Prefixo', oc.prefixo)}
            ${campo('Placa', oc.placa)}
            ${campo('Linha', oc.linha_codigo)}
            ${campo('Status', oc.status)}
            ${campo('Sentido', oc.sentido)}
        </div>

        <div class="oc-print-section-titulo">Condutor</div>
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Nome do Condutor', oc.condutor_nome)}
            ${campo('RE', oc.condutor_re)}
            ${campo('Direção Defensiva', fmtBool(oc.direcao_defensiva))}
            ${campo('Função', oc.condutor_funcao)}
            ${campo('CNH Nº', oc.condutor_cnh)}
            ${campo('RG Nº', oc.condutor_rg)}
            ${campo('CPF Nº', oc.condutor_cpf)}
        </div>

        <div class="oc-print-section-titulo">Cobrador</div>
        <div class="oc-print-grid">
            ${campo('Nome do Cobrador', oc.cobrador_nome)}
            ${campo('RE', oc.cobrador_re)}
        </div>

        <div class="oc-print-section-titulo">Velocidades e atendimento</div>
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Velocidade da Via', oc.velocidade_via != null ? `${oc.velocidade_via} km/h` : null)}
            ${campo('Velocidade do Ônibus', oc.velocidade_onibus != null ? `${oc.velocidade_onibus} km/h` : null)}
            ${campo('Foi ao Local?', fmtBool(oc.foi_ao_local))}
            ${campo('Confirmado?', fmtBool(oc.confirmado))}
        </div>

        <div class="oc-print-section-titulo">Marcadores da via</div>
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Via Urbana', fmtBool(oc.via_urbana))}
            ${campo('Via Rodoviária', fmtBool(oc.via_rodoviaria))}
            ${campo('Área Interna', fmtBool(oc.area_interna))}
            ${campo('Corredor', fmtBool(oc.corredor))}
            ${campo('Fotos', fmtBool(oc.tem_fotos))}
            ${campo('Monitoramento', fmtBool(oc.monitoramento))}
        </div>

        <div class="oc-print-section-titulo">Local</div>
        <div class="oc-print-grid">
            ${campo('Local do Acidente', oc.local_ocorrido)}
            ${campo('Nº', oc.numero_local)}
            ${campo('Bairro', oc.bairro)}
            ${campo('Cidade', oc.cidade)}
        </div>
        ${telefones}

        <div class="oc-print-section-titulo">Contagem e manutenção</div>
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Quant. Acidentes', oc.quant_acidentes)}
            ${campo('Isentos', oc.isentos)}
            ${campo('Culpados', oc.culpados)}
            ${campo('Problemas Mecânicos?', fmtBool(oc.problemas_mecanicos))}
            ${campo('Qual?', oc.problemas_mecanicos_qual)}
            ${campo('Condutor avisou a manutenção?', fmtBool(oc.condutor_avisou_manutencao))}
            ${campo('Nome de quem avisou', oc.manutencao_avisado_nome)}
        </div>

        <div class="oc-print-section-titulo">Descrição do Coordenador</div>
        <div class="oc-print-texto">${escapeHtml(oc.descricao_coordenador) || '—'}</div>
        <div class="oc-print-section-titulo">Descrição do Motorista</div>
        <div class="oc-print-texto">${escapeHtml(oc.descricao_motorista) || '—'}</div>
        <div class="oc-print-section-titulo">Descrição do Terceiro</div>
        <div class="oc-print-texto">${escapeHtml(oc.descricao_terceiro) || '—'}</div>
    </div>`;
}

// ── Página 2 — Veículos ─────────────────────────────────────────────────
function blocoVeiculo(v, idx) {
    v = v || {};
    return `<div class="oc-print-bloco">
        <div class="oc-print-bloco-titulo">Veículo nº ${idx + 1}</div>
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Danos', v.danos)}
            ${campo('Marca', v.marca)}
            ${campo('Modelo', v.modelo)}
            ${campo('Ano', v.ano)}
            ${campo('Cor', v.cor)}
            ${campo('Placa', v.placa)}
            ${campo('Cidade', v.cidade_placa)}
            ${campo('Estado', v.estado_placa)}
            ${campo('Renavam', v.renavam)}
            ${campo('Proprietário/Motorista', v.proprietario)}
            ${campo('Fones', v.fones)}
            ${campo('E-mail', v.email)}
            ${campo('Endereço', v.endereco)}
            ${campo('Cidade', v.cidade)}
            ${campo('RG', v.rg)}
            ${campo('CPF', v.cpf)}
            ${campo('CNH', v.cnh)}
            ${campo('Seguradora', v.seguradora)}
            ${campo('Fone Seguradora', v.seguradora_fone)}
            ${campo('Sinistro Nº', v.sinistro_numero)}
        </div>
        <div class="oc-print-label" style="margin-top:4px">Partes avariadas</div>
        <div class="oc-print-texto" style="min-height:20px">${escapeHtml(v.partes_avariadas) || '—'}</div>
    </div>`;
}

function paginaVeiculos(oc) {
    const veiculos = oc.veiculos_terceiro && oc.veiculos_terceiro.length ? oc.veiculos_terceiro : [{}, {}];
    const blocosVeiculos = veiculos.map((v, i) => blocoVeiculo(v, i)).join('');

    const avariasPorRegiao = new Map((oc.avarias || []).map(a => [a.regiao, a]));
    const linhasAvaria = REGIOES_AVARIA.map(([codigo, label]) => {
        const a = avariasPorRegiao.get(codigo);
        return `<div class="oc-print-field">
            <span class="oc-print-label">${label}${a ? ' ☑' : ' ☐'}</span>
            <span class="oc-print-valor" style="font-weight:400">${a?.descricao ? escapeHtml(a.descricao) : '—'}</span>
        </div>`;
    }).join('');

    return `<div class="oc-print-page">
        ${cabecalhoPagina('Página 2 de 4 — Veículos de Terceiro')}
        ${blocosVeiculos}
        <div class="oc-print-section-titulo">Avarias do nosso veículo — por região</div>
        <div class="oc-print-grid oc-print-grid-3">${linhasAvaria}</div>
    </div>`;
}

// ── Página 3 — Análise ──────────────────────────────────────────────────
function paginaAnalise(oc) {
    const a = oc.analise || {};
    const grupos = ANALISE_GRUPOS.map(grupo => {
        const campos = grupo.campos.map(c => {
            if (c.texto) return campo(c.label, a[c.name]);
            const par = (c.opcoes || []).find(([codigo]) => codigo === a[c.name]);
            return campo(c.label, par ? par[1] : null);
        }).join('');
        return `<div class="oc-print-section-titulo">${escapeHtml(grupo.titulo)}</div>
            <div class="oc-print-grid oc-print-grid-3">${campos}</div>`;
    }).join('');

    const anexos = oc.anexos || [];
    const listaAnexos = anexos.length
        ? anexos.map(x => `<div class="oc-print-field"><span class="oc-print-label">${TIPOS_ANEXO[x.tipo] || x.tipo}</span><span class="oc-print-valor" style="font-weight:400">${escapeHtml(x.nome_original || x.caminho)}</span></div>`).join('')
        : `<div class="oc-print-texto">Nenhum anexo enviado.</div>`;

    return `<div class="oc-print-page">
        ${cabecalhoPagina('Página 3 de 4 — Análise do Acidente')}
        ${grupos}
        <div class="oc-print-section-titulo">Anexos — croqui, fotos e B.O.</div>
        <div class="oc-print-grid">${listaAnexos}</div>

        <div class="oc-print-rodape">
            <strong>Comunicação de Acidente Sambaíba</strong>
            www.sambaibasp.com.br/sinistro &nbsp;·&nbsp; (11) 2990-4445 — ramais 4475 e 4467
        </div>
    </div>`;
}

// ── Página 4 — Testemunhas ──────────────────────────────────────────────
function blocoTestemunha(t, idx) {
    t = t || {};
    return `<div class="oc-print-bloco">
        <div class="oc-print-bloco-titulo">Testemunha ${idx + 1}</div>
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Nome', t.nome)}
            ${campo('RG Nº', t.rg)}
            ${campo('Endereço', t.endereco)}
            ${campo('Nº', t.numero)}
            ${campo('Bairro', t.bairro)}
            ${campo('Cidade', t.cidade)}
            ${campo('Fone 1', t.fone1)}
            ${campo('Fone 2', t.fone2)}
        </div>
    </div>`;
}

function blocoVitima(v, idx) {
    v = v || {};
    return `<div class="oc-print-bloco">
        <div class="oc-print-bloco-titulo">Pessoa vitimada ${idx + 1}</div>
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Nome', v.nome)}
            ${campo('RG Nº', v.rg)}
            ${campo('Fone', v.fone)}
            ${campo('Endereço', v.endereco)}
            ${campo('Nº', v.numero)}
            ${campo('Bairro', v.bairro)}
            ${campo('Cidade', v.cidade)}
            ${campo('Idade', v.idade)}
            ${campo('Era passageiro do nosso ônibus?', fmtBool(v.era_passageiro))}
        </div>
        <div class="oc-print-label" style="margin-top:4px">Dados pessoais</div>
        <div class="oc-print-texto" style="min-height:16px">${escapeHtml(v.dados_pessoais) || '—'}</div>
    </div>`;
}

function paginaTestemunhas(oc) {
    const testemunhas = oc.testemunhas && oc.testemunhas.length ? oc.testemunhas : [{}, {}, {}];
    const vitimas = oc.vitimas && oc.vitimas.length ? oc.vitimas : [{}, {}, {}];
    const autoridades = oc.autoridades || [];

    const blocosAutoridades = autoridades.length
        ? autoridades.map(a => campo(a.orgao?.nome || 'Órgão', `${a.identificacao || ''} ${a.responsavel ? '— ' + a.responsavel : ''}`.trim() || null)).join('')
        : campo('Autoridades no local', null);

    const user = getCurrentUser();

    return `<div class="oc-print-page">
        ${cabecalhoPagina('Página 4 de 4 — Testemunhas e Vítimas')}

        <div class="oc-print-section-titulo">Testemunhas</div>
        ${testemunhas.map(blocoTestemunha).join('')}

        <div class="oc-print-section-titulo">Pessoas vitimadas</div>
        ${vitimas.map(blocoVitima).join('')}

        <div class="oc-print-section-titulo">Autoridades no local</div>
        <div class="oc-print-grid">${blocosAutoridades}</div>

        <div class="oc-print-section-titulo">Ocorrência policial</div>
        <div class="oc-print-grid oc-print-grid-3">
            ${campo('Ocorrência Policial?', fmtBool(oc.ocorrencia_policial))}
            ${campo('Viatura Nº', oc.viatura_numero)}
            ${campo('BPM', oc.bpm)}
            ${campo('CIA', oc.cia)}
            ${campo('Distrito', oc.distrito)}
            ${campo('Nº TO', oc.numero_to)}
            ${campo('Nº BO', oc.numero_bo)}
            ${campo('Protocolo', oc.protocolo)}
            ${campo('Houve Polícia Técnica?', fmtBool(oc.houve_policia_tecnica))}
            ${campo('Nome do Perito', oc.nome_perito)}
        </div>

        <div class="oc-print-section-titulo">Observações</div>
        <div class="oc-print-texto">${escapeHtml(oc.observacoes) || '—'}</div>
        ${campo('Relatórios entregues ao Controlador de acesso', oc.controlador_acesso)}

        <div class="oc-print-section-titulo">Assinaturas</div>
        <div class="oc-print-grid">
            <div class="oc-print-field">
                <span class="oc-print-label">Ass. e Nome do Coordenador / RE</span>
                <span class="oc-print-valor" style="font-weight:400">____________________________ &nbsp; ${escapeHtml(user?.nome || '')} / ${escapeHtml(user?.re || '')}</span>
            </div>
            <div class="oc-print-field">
                <span class="oc-print-label">Ass. e Nome do Motorista / RE</span>
                <span class="oc-print-valor" style="font-weight:400">____________________________ &nbsp; ${escapeHtml(oc.condutor_nome || '')} / ${escapeHtml(oc.condutor_re || '')}</span>
            </div>
        </div>
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
