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
