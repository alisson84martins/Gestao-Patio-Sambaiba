/*
 * dataLocalISO — data local em AAAA-MM-DD, sem passar por UTC.
 * -----------------------------------------------------------------------
 * 🔴 `toISOString().slice(0, 10)` está proibido pra montar "hoje": ele
 * converte pra UTC antes de cortar a string, e São Paulo está 3h atrás.
 * Depois das 21h (UTC-3), `new Date().toISOString()` já é o dia seguinte
 * em UTC — a tela abre mostrando amanhã enquanto o turno da Fiscalização
 * (que vai até 00h20) ainda está no dia de hoje. É a armadilha
 * `armadilha_fuso_toisostring`, e ela já mordeu outras telas do V3.
 *
 * Monta a data a partir dos componentes LOCAIS do Date (getFullYear,
 * getMonth, getDate) — nunca converte pra UTC. Usado pelas duas páginas
 * da Fiscalização (fiscal.html e fiscal-painel.html); não duplique esta
 * função nos dois arquivos.
 */
export function dataLocalISO(d = new Date()) {
    const ano = d.getFullYear();
    const mes = String(d.getMonth() + 1).padStart(2, '0');
    const dia = String(d.getDate()).padStart(2, '0');
    return `${ano}-${mes}-${dia}`;
}

/*
 * dataServicoISO — data de SERVIÇO do pátio (ciclo que vira às 20h).
 * -----------------------------------------------------------------------
 * O backend decide "de que dia é a escala/alocação" com get_data_servico()
 * (backend/app/routers/alocacoes.py): antes das 20h no relógio de Brasília
 * é hoje, às 20h ou depois já é amanhã (o pátio começa a operar o próximo
 * ciclo). Essa função espelha exatamente esse corte no front — os dois
 * lados têm que concordar sempre, senão a escala importada aponta pra um
 * dia e a alocação do pátio pra outro, e o chip aparece "pelado" (sem
 * linha/horário) porque o outerjoin não encontra a escala.
 *
 * Construída em cima de dataLocalISO() (nunca toISOString) — mesma razão
 * do aviso no topo deste arquivo: passar por UTC desloca o corte em 3h.
 */
export function dataServicoISO(d = new Date()) {
    if (d.getHours() >= 20) {
        const amanha = new Date(d);
        amanha.setDate(amanha.getDate() + 1);
        return dataLocalISO(amanha);
    }
    return dataLocalISO(d);
}
