/*
 * ocorrencia.vocabulario.js — Rótulos e opções fixas do módulo Coordenadoria
 * -----------------------------------------------------------------------------
 * Espelha os CHECK constraints de coordenadoria.ocorrencia_analise e as
 * regiões de avaria da migration 012. Centralizado aqui porque o formulário
 * (ocorrencia.form.js) e a impressão (ocorrencia.imprimir.js) precisam dos
 * mesmos rótulos em português — duplicar essas ~70 opções nos dois arquivos
 * seria o tipo de coisa que desalinha silenciosamente com o tempo.
 */

// Espelha o seed de coordenadoria.tipo_ocorrencia (migration 012) — usado
// pela impressão (item 6) pra montar a matriz "todas as opções com
// quadradinho" (3A) da Capa. O formulário continua lendo os tipos da API
// (GET /ocorrencias/catalogos), que é a fonte de verdade; esta lista é só
// pros rótulos fixos do impresso, que precisa mostrar as 12 opções mesmo
// que a ocorrência só tenha uma marcada.
export const TIPOS_OCORRENCIA = [
    ['ACIDENTE_COM_VITIMA', 'Acidente com Vítima'],
    ['ACIDENTE_SEM_VITIMA', 'Acidente sem Vítima'],
    ['ATROPELAMENTO', 'Atropelamento'],
    ['QUEDA_DE_PASSAGEIRO', 'Queda de Passageiro'],
    ['INCIDENTE', 'Incidente'],
    ['VANDALISMO', 'Vandalismo'],
    ['AVARIA_NO_PATIO', 'Avaria no Pátio'],
    ['FURTO_DE_EQUIPAMENTOS', 'Furto de Equipamentos'],
    ['CHOQUE', 'Choque'],
    ['FURTO_DE_VEICULOS', 'Furto de Veículos'],
    ['INCENDIO_DO_VEICULO', 'Incêndio do Veículo'],
    ['OUTROS', 'Outros'],
];

// "Registro / local" do papel: 6 marcadores booleanos da via (colunas de
// coordenadoria.ocorrencia) + o sentido, que no papel também é só duas
// caixinhas (TP-TS / TS-TP) apesar de no banco ser texto livre — ver
// SENTIDO_OPCOES logo abaixo.
export const REGISTRO_LOCAL = [
    ['via_urbana', 'Via Urbana'],
    ['via_rodoviaria', 'Via Rodoviária'],
    ['area_interna', 'Área Interna'],
    ['corredor', 'Corredor'],
    ['tem_fotos', 'Fotos'],
    ['monitoramento', 'Monitoramento'],
];

export const SENTIDO_OPCOES = ['TP-TS', 'TS-TP'];

export const ANALISE_GRUPOS = [
    {
        titulo: 'Tipo de colisão / acidente',
        campos: [
            { name: 'colisao', label: 'Colisão', opcoes: [
                ['FRONTAL', 'Frontal'], ['TRASEIRA', 'Traseira'], ['LATERAL', 'Lateral'],
            ] },
            { name: 'acidente', label: 'Acidente', opcoes: [
                ['CAPOTAMENTO', 'Capotamento'], ['TOMBAMENTO', 'Tombamento'], ['ENGAVETAMENTO', 'Engavetamento'],
            ] },
            { name: 'condicoes', label: 'Condições', opcoes: [
                ['TRANSITANDO', 'Transitando'], ['MANOBRANDO', 'Manobrando'], ['PARADO', 'Parado'],
            ] },
            { name: 'deslocamento', label: 'Deslocamento', opcoes: [
                ['EM_FRENTE', 'Em Frente'], ['EM_RE', 'Em Ré'], ['REBOCADO', 'Rebocado'],
            ] },
        ],
    },
    {
        titulo: 'Perfil da via / pista',
        campos: [
            { name: 'reta', label: 'Reta', opcoes: [
                ['EM_PLANO', 'Em Plano'], ['EM_ACLIVE', 'Em Aclive'], ['EM_DECLIVE', 'Em Declive'],
            ] },
            { name: 'curva', label: 'Curva', opcoes: [
                ['EM_PLANO', 'Em Plano'], ['EM_ACLIVE', 'Em Aclive'], ['EM_DECLIVE', 'Em Declive'],
                ['DEPRESSAO', 'Depressão'], ['LOMBADA', 'Lombada'], ['NORMAL', 'Normal'],
            ] },
            { name: 'via', label: 'Via', opcoes: [
                ['TREVO', 'Trevo'], ['CRUZAMENTO', 'Cruzamento'], ['BIFURCACAO', 'Bifurcação'], ['NORMAL', 'Normal'],
            ] },
            { name: 'numero_faixas', label: 'Nº de faixas', opcoes: [
                ['UMA', 'Uma'], ['DUAS', 'Duas'], ['TRES', 'Três'], ['MAIS_DE_TRES', 'Mais de três'],
            ] },
            { name: 'mao_direcao', label: 'Mão de direção', opcoes: [
                ['UNICA', 'Única'], ['DUPLA', 'Dupla'], ['PRIVATIVA_COLETIVO', 'Privativa coletivo'],
            ] },
            { name: 'preferencial', label: 'Preferencial', opcoes: [
                ['SIM', 'Sim'], ['NAO', 'Não'], ['NAO_SE_APLICA', 'Não se aplica'],
            ] },
            { name: 'condicao_pista', label: 'Condições de pista', opcoes: [
                ['SECA', 'Seca'], ['MOLHADA', 'Molhada'], ['OLEOSA', 'Oleosa'], ['ENLAMEADA', 'Enlameada'],
            ] },
            { name: 'pavimentacao', label: 'Pavimentação', opcoes: [
                ['ASFALTO', 'Asfalto'], ['CONCRETO', 'Concreto'], ['PARALELEPIPEDO', 'Paralelepípedo'], ['OUTROS', 'Outros'],
            ] },
            { name: 'conservacao', label: 'Estado de conservação', opcoes: [
                ['BOM', 'Bom'], ['DANIFICADO', 'Danificado'], ['EM_OBRAS', 'Em obras'],
            ] },
        ],
    },
    {
        titulo: 'Perfil de sinalização',
        campos: [
            { name: 'sinal_horizontal', label: 'Sinal horizontal (solo)', opcoes: [
                ['NAO_EXISTE', 'Não existe'], ['FAIXA_SIMPLES', 'Faixa simples'], ['FAIXA_DUPLA', 'Faixa dupla'],
                ['TRAV_PEDESTRE', 'Trav. pedestre'], ['PARE', 'Pare'], ['OUTROS', 'Outros'],
            ] },
            { name: 'sinal_horizontal_outros', label: 'Outros (horizontal) — descreva', texto: true },
            { name: 'sinal_vertical', label: 'Sinal vertical (placas)', opcoes: [
                ['NAO_EXISTE', 'Não existe'], ['PARE', 'Pare'], ['ESCOLA', 'Escola'], ['MAO_DE_DIRECAO', 'Mão de direção'],
                ['VELOCIDADE', 'Velocidade'], ['PREFERENCIAL', 'Preferencial'], ['OUTROS', 'Outros'],
            ] },
            { name: 'sinal_vertical_outros', label: 'Outros (vertical) — descreva', texto: true },
            { name: 'dispositivos_aux', label: 'Dispositivos auxiliares', opcoes: [
                ['NAO_EXISTE', 'Não existe'], ['NORMAL', 'Normal'], ['DESLIGADO', 'Desligado'],
                ['COM_DEFEITO', 'C/ defeito'], ['ATENCAO', 'Atenção'],
            ] },
        ],
    },
    {
        titulo: 'Perfil do local',
        campos: [
            { name: 'iluminacao', label: 'Iluminação', opcoes: [
                ['DIA', 'Dia'], ['NOITE_COM_ILUMINACAO', 'Noite c/ iluminação artificial'],
                ['NOITE_SEM_ILUMINACAO', 'Noite s/ iluminação artificial'],
                ['ANOITECER_AMANHECER', 'Anoitecer/Amanhecer'], ['OUTROS', 'Outros'],
            ] },
            { name: 'tempo', label: 'Tempo', opcoes: [
                ['BOM', 'Bom'], ['NUBLADO', 'Nublado'], ['CHUVA', 'Chuva'], ['GAROA', 'Garoa'], ['NEBLINA', 'Neblina'],
            ] },
            { name: 'visibilidade', label: 'Visibilidade', opcoes: [
                ['BOM', 'Bom'], ['REGULAR', 'Regular'], ['MA', 'Má'],
            ] },
        ],
    },
];

export const REGIOES_AVARIA = [
    ['FRENTE', 'Frente'],
    ['TRASEIRA', 'Traseira'],
    ['LATERAL_ESQUERDA', 'Lateral Esquerda'],
    ['LATERAL_DIREITA', 'Lateral Direita'],
    ['TETO', 'Teto'],
    ['INTERIOR', 'Interior'],
    ['RODADO', 'Rodado'],
    ['RETROVISOR', 'Retrovisor'],
    ['PARABRISA', 'Para-brisa'],
    ['OUTRO', 'Outro'],
];

export const TIPOS_ANEXO = {
    FOTO_ACIDENTE: 'Foto do acidente',
    FOTO_RELATORIO: 'Foto do relatório',
    CROQUI: 'Croqui',
    BO_PDF: 'PDF do B.O.',
    OUTRO: 'Outro',
};

export const DANOS_VEICULO = [
    ['GRANDE', 'Grande'], ['MEDIO', 'Médio'], ['PEQUENO', 'Pequeno'],
];

/** Busca o rótulo de um valor dentro de um grupo de opções [[codigo, rotulo], ...]. */
export function rotulo(opcoes, valor) {
    if (!valor) return null;
    const par = opcoes.find(([codigo]) => codigo === valor);
    return par ? par[1] : valor;
}
