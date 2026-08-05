/*
 * mascaras.js — Formatação e validação de campos do formulário de ocorrência
 * -----------------------------------------------------------------------------
 * Regra que não pode ser violada: MÁSCARA NUNCA IMPEDE DE SALVAR. Formulário
 * preenchido em pé, no local do acidente, no celular. Perder a ocorrência
 * porque um CPF ficou com um dígito errado é muito pior que um CPF errado —
 * então isto aqui só formata enquanto a pessoa digita e avisa quando algo
 * está incompleto ou inválido (borda + mensagem curta). Nunca bloqueia
 * salvamento nem rascunho automático, nunca apaga o que foi digitado.
 *
 * Módulo único de propósito: os campos vivem em dois lugares (os fixos do
 * HTML em ocorrencia-form.html e os cards gerados a partir de CAMPOS_VEICULO/
 * CAMPOS_TESTEMUNHA/CAMPOS_VITIMA em ocorrencia.form.js). Máscara escrita
 * duas vezes diverge em silêncio — mesma razão de existir o
 * ocorrencia.vocabulario.js. Toda tela consome só aplicarMascara(el, tipo).
 */

function somenteDigitos(valor) {
    return (valor || '').replace(/\D/g, '');
}

// ─── CPF — 000.000.000-00 ──────────────────────────────────────────────────

export function formatarCPF(valor) {
    const d = somenteDigitos(valor).slice(0, 11);
    if (d.length > 9) return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9)}`;
    if (d.length > 6) return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6)}`;
    if (d.length > 3) return `${d.slice(0, 3)}.${d.slice(3)}`;
    return d;
}

export function cpfValido(valor) {
    const d = somenteDigitos(valor);
    if (d.length !== 11) return false;
    if (/^(\d)\1{10}$/.test(d)) return false; // 000.000.000-00, 111.111.111-11 etc — inválidos por definição
    const dv = base => {
        let soma = 0;
        for (let i = 0; i < base.length; i++) soma += Number(base[i]) * (base.length + 1 - i);
        const resto = (soma * 10) % 11;
        return resto === 10 ? 0 : resto;
    };
    const dv1 = dv(d.slice(0, 9));
    const dv2 = dv(d.slice(0, 9) + dv1);
    return d[9] === String(dv1) && d[10] === String(dv2);
}

// ─── Placa — AAA-0000 (antiga) ou AAA0A00 (Mercosul) ───────────────────────
// Detecta pelo 5º caractere: letra → Mercosul (sem separador), dígito →
// antiga (hífen depois das 3 letras). Maiúscula sempre, inclusive colagem —
// o listener de 'input' cobre paste porque colar dispara 'input' também.

export function formatarPlaca(valor) {
    const s = (valor || '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 7);
    if (s.length < 5) return s;
    const mercosul = /[A-Z]/.test(s[4]);
    return mercosul ? s : `${s.slice(0, 3)}-${s.slice(3)}`;
}

export function placaValida(valor) {
    const s = (valor || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    return /^[A-Z]{3}\d{4}$/.test(s) || /^[A-Z]{3}\d[A-Z]\d{2}$/.test(s);
}

// ─── Telefone — (11) 9 8888-7777 (celular) / (11) 2990-4445 (fixo) ─────────

export function formatarTelefone(valor) {
    const d = somenteDigitos(valor).slice(0, 11);
    if (d.length === 0) return '';
    if (d.length <= 2) return `(${d}`;
    const ddd = d.slice(0, 2);
    const resto = d.slice(2);
    if (resto.length === 0) return `(${ddd}) `;
    if (d.length <= 10) {
        // fixo: 4 + 4
        if (resto.length <= 4) return `(${ddd}) ${resto}`;
        return `(${ddd}) ${resto.slice(0, 4)}-${resto.slice(4)}`;
    }
    // celular: 11 dígitos — o 9 fica separado, depois 4 + 4
    const nono = resto.slice(0, 1);
    const restante = resto.slice(1);
    if (restante.length <= 4) return `(${ddd}) ${nono} ${restante}`;
    return `(${ddd}) ${nono} ${restante.slice(0, 4)}-${restante.slice(4)}`;
}

export function telefoneValido(valor) {
    const d = somenteDigitos(valor);
    return d.length === 10 || d.length === 11;
}

// `fones` do terceiro pode trazer mais de um número separado por "/" —
// preserva o separador, formatando cada número individualmente.
export function formatarTelefones(valor) {
    return (valor || '').split('/').map(parte => formatarTelefone(parte)).join(' / ');
}

export function telefonesValidos(valor) {
    return (valor || '').split('/').every(parte => (somenteDigitos(parte).length === 0 || telefoneValido(parte)));
}

// ─── UF — 2 letras, restrito às 27 siglas ──────────────────────────────────

const UFS_VALIDAS = [
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS',
    'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC',
    'SP', 'SE', 'TO',
];

export function formatarUF(valor) {
    return (valor || '').toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2);
}

export function ufValida(valor) {
    return UFS_VALIDAS.includes((valor || '').toUpperCase());
}

// ─── Renavam — 11 dígitos ───────────────────────────────────────────────────

export function formatarRenavam(valor) {
    return somenteDigitos(valor).slice(0, 11);
}

export function renavamValido(valor) {
    return somenteDigitos(valor).length === 11;
}

// ─── Ano — 4 dígitos, 1950 até o ano corrente + 1 ──────────────────────────

export function formatarAno(valor) {
    return somenteDigitos(valor).slice(0, 4);
}

export function anoValido(valor) {
    if (!/^\d{4}$/.test(valor || '')) return false;
    const ano = Number(valor);
    return ano >= 1950 && ano <= new Date().getFullYear() + 1;
}

// ─── CNH — 11 dígitos ───────────────────────────────────────────────────────

export function formatarCNH(valor) {
    return somenteDigitos(valor).slice(0, 11);
}

export function cnhValida(valor) {
    return somenteDigitos(valor).length === 11;
}

// ─── RG — SEM máscara ───────────────────────────────────────────────────────
// RG não tem formato nacional: varia por estado, tem tamanhos diferentes e
// alguns terminam em letra (X). Forçar 00.000.000-0 rejeitaria RG legítimo
// de terceiro de outro estado — justo quando mais se precisa registrar.
// Só maiúsculas ao vivo; o corte de espaço nas pontas fica pro blur, senão
// a barra de espaço para de funcionar no meio da digitação.

export function formatarRG(valor) {
    return (valor || '').toUpperCase();
}

// ─── Motor genérico ─────────────────────────────────────────────────────────
// Cada tipo: formatar (sempre roda), validar (opcional — sem validar, nunca
// mostra aviso, caso do RG) e a mensagem curta quando validar() der falso.

const TIPOS = {
    cpf: { formatar: formatarCPF, validar: cpfValido, mensagem: 'CPF incompleto ou inválido' },
    placa: { formatar: formatarPlaca, validar: placaValida, mensagem: 'Placa incompleta ou fora do padrão' },
    telefone: { formatar: formatarTelefone, validar: telefoneValido, mensagem: 'Telefone incompleto' },
    'telefone-multi': { formatar: formatarTelefones, validar: telefonesValidos, mensagem: 'Telefone incompleto' },
    uf: { formatar: formatarUF, validar: ufValida, mensagem: 'UF inválida' },
    renavam: { formatar: formatarRenavam, validar: renavamValido, mensagem: 'Renavam incompleto' },
    ano: { formatar: formatarAno, validar: anoValido, mensagem: 'Ano inválido' },
    cnh: { formatar: formatarCNH, validar: cnhValida, mensagem: 'CNH incompleta' },
    rg: { formatar: formatarRG, validar: null, mensagem: '' },
};

function garantirAvisoAoLado(el) {
    let aviso = el.nextElementSibling;
    if (!aviso || !aviso.classList.contains('mascara-aviso')) {
        aviso = document.createElement('div');
        aviso.className = 'mascara-aviso';
        el.insertAdjacentElement('afterend', aviso);
    }
    return aviso;
}

function atualizarAviso(el, tipo) {
    const config = TIPOS[tipo];
    const aviso = garantirAvisoAoLado(el);
    if (!config.validar || el.value === '') {
        el.classList.remove('mascara-invalida');
        aviso.textContent = '';
        return;
    }
    const valido = config.validar(el.value);
    el.classList.toggle('mascara-invalida', !valido);
    aviso.textContent = valido ? '' : config.mensagem;
}

/**
 * Liga a formatação e o aviso de validação num campo. Nunca desabilita o
 * campo, nunca impede digitação — só reescreve o valor formatado e alterna
 * uma classe + uma linha de aviso.
 * @param {HTMLInputElement} el
 * @param {keyof TIPOS} tipo
 */
export function aplicarMascara(el, tipo) {
    const config = TIPOS[tipo];
    if (!config || !el) return;

    el.addEventListener('input', () => {
        el.value = config.formatar(el.value);
        atualizarAviso(el, tipo);
    });
    el.addEventListener('blur', () => {
        if (tipo === 'rg') el.value = el.value.trim();
        atualizarAviso(el, tipo);
    });

    // Campo já vem preenchido (edição de ocorrência existente) — formata e
    // avalia uma vez, sem esperar o primeiro toque.
    if (el.value) {
        el.value = config.formatar(el.value);
        atualizarAviso(el, tipo);
    }
}
