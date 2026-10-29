# ruff: noqa: F811  (a fixture `amb` vem de test_escala_fiscais_montagem)
"""Escala de Fiscais — fechamento automático do fim de semana (29/10/2026).

Parte 1: o motor (funções puras de escala_fiscais_fechamento.py), um caso por
regra — quem folga sai, quem está livre entra, TS vai para o TP, rendição não
fica vazia, acúmulo só quando falta gente, dobra só no modo "com_dobra" e com
rodízio, ausente nunca entra, período de outro coordenador não é mexido.

Parte 2: a rota GET /escala-fiscais/dias/{data}/fechamento, com o mesmo banco
fictício de test_escala_fiscais_montagem.py — nada é gravado, a sugestão salva
pelo PUT de sempre, escala publicada não é mexida.

⛔ REs, linhas e postos FICTÍCIOS (repositório público).
"""
from collections import Counter
from datetime import date, time, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.escala_fiscais import EscalaFiscalAusencia, EscalaFiscalDia, EscalaFiscalPosto
from app.services import escala_fiscais_fechamento as fech
from app.services import escala_fiscais_regras as regras
from app.services.escala_fiscais_regras import Aloc
from tests.test_escala_fiscais_montagem import (  # noqa: F401
    _abrir_e_salvar,
    _linha,
    _payload,
    _quadro,
    amb,
)

# Novembro/2026 = mês ímpar. Folga base "sabado" → folga no sábado.
SAB = date(2026, 11, 7)
DOM = date(2026, 11, 8)
PADROES = {1: (time(5), time(14)), 2: (time(14), time(23))}
PF_A, PF_B = uuid4(), uuid4()


def _posto(lado, linha, ponto=None, obrig=False):
    pid = uuid4()
    return regras.PostoInfo(id=pid, lado=lado, cod_jb=None, lote=None, linhas=[linha],
                            ponto_final_id=ponto, ponto_final_nome="PF" if ponto else None,
                            obrigatorio_fds=obrig)


def _aloc(posto, periodo, re_=None, situacao=None, ini=None, fim=None, marcador=None):
    if situacao is None:
        situacao = "escalado" if re_ else "descoberto"
    return Aloc(posto_id=posto.id, periodo=periodo, situacao=situacao, re=re_,
                hora_inicio=ini, hora_termino=fim, marcador=marcador if marcador is not None else ("" if not re_ else None))


def _ctx(d, postos, quadro, ausencias=(), trocas=(), anteriores=None):
    return regras.Contexto(
        data=d, postos={p.id: p for p in postos},
        quadro={f.re: f for f in quadro}, ausencias=list(ausencias), trocas=list(trocas),
        padroes=PADROES, dobras_anteriores=anteriores or {},
    )


def _f(re_, periodo, folga):
    return regras.FiscalQuadro(re=re_, periodo=periodo, folga_base=folga)


def _dia(ctx, alocs, ordem=None, padrao=None):
    return fech.DiaEntrada(ctx=ctx, alocs=alocs, ordem=ordem or {}, padrao_modelo=padrao or {})


def _sugerir(entrada, modo="sem_dobra", editaveis=(1, 2), fam=None, hist=None, nao_dobrar=()):
    return fech.sugerir_dia(entrada, modo=modo, editaveis=set(editaveis), familiaridade=fam or fech.Familiaridade(),
                            historico_dobras=hist or Counter(), nao_dobrar=set(nao_dobrar))


def _re(s, posto, periodo):
    return next(a.re for a in s.alocs if a.posto_id == posto.id and a.periodo == periodo)


# ─── Parte 1 · motor ─────────────────────────────────────────────────────────

def test_quem_folga_sai_do_posto_e_quem_esta_livre_entra():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70001", 2, "sabado"), _f("70002", 2, "domingo")]
    s = _sugerir(_dia(_ctx(SAB, [tp], quadro), [_aloc(tp, 2, "70001")]))
    assert _re(s, tp, 2) == "70002"
    assert s.liberados and s.liberados[0]["re"] == "70001"
    assert s.mudancas[0]["antes"] == "70001" and s.mudancas[0]["depois"] == "70002"
    assert not s.dobras and not s.vagas


def test_quem_trabalha_no_dia_continua_no_posto():
    tp = _posto("TP", "9101-10")
    s = _sugerir(_dia(_ctx(SAB, [tp], [_f("70002", 2, "domingo")]), [_aloc(tp, 2, "70002")]))
    assert _re(s, tp, 2) == "70002" and not s.mudancas


def test_fiscal_do_ts_vai_para_o_tp_e_o_ts_comum_fica_em_branco():
    tp = _posto("TP", "9101-10")
    ts = _posto("TS", "9102-10")
    # O 70003 é o fiscal do TS no modelo; no fim de semana o TP vem primeiro.
    quadro = [_f("70003", 2, "domingo")]
    alocs = [_aloc(tp, 2), _aloc(ts, 2, "70003")]
    s = _sugerir(_dia(_ctx(SAB, [tp, ts], quadro), alocs, ordem={(tp.id, 2): 1, (ts.id, 2): 2}))
    assert _re(s, tp, 2) == "70003"           # foi para o TP
    assert _re(s, ts, 2) is None              # TS comum em branco
    assert not s.vagas and s.remanejados[0]["re"] == "70003"


def test_rendicao_nao_e_desfeita_para_cobrir_o_tp():
    tp = _posto("TP", "9101-10")
    rend = _posto("TS", "9103-10", obrig=True)
    quadro = [_f("70008", 2, "domingo")]
    alocs = [_aloc(tp, 2), _aloc(rend, 2, "70008")]
    s = _sugerir(_dia(_ctx(SAB, [tp, rend], quadro), alocs))
    assert _re(s, rend, 2) == "70008" and _re(s, tp, 2) is None and s.vagas


def test_ts_comum_liberado_nao_toma_gente_do_tp():
    tp = _posto("TP", "9101-10")
    ts = _posto("TS", "9102-10")
    quadro = [_f("70004", 2, "sabado"), _f("70005", 2, "domingo")]
    # Modelo: TS com 70004 (folga no sábado) e TP vazio. Só 70005 livre.
    alocs = [_aloc(ts, 2, "70004"), _aloc(tp, 2)]
    s = _sugerir(_dia(_ctx(SAB, [tp, ts], quadro), alocs, ordem={(ts.id, 2): 1, (tp.id, 2): 2}))
    assert _re(s, tp, 2) == "70005"           # TP primeiro, mesmo vindo depois na ordem
    assert _re(s, ts, 2) is None              # TS comum fica em branco
    assert not s.vagas                        # TS comum não conta como vaga obrigatória


def test_rendicao_do_ts_e_obrigatoria_e_pega_fiscal_da_tarde_pelo_horario():
    rend = _posto("TS", "9103-10", obrig=True)
    # Rendição das 12:00 às 20:20 está na coluna do 1º período, como na planilha.
    alocs = [_aloc(rend, 1, ini=time(12), fim=time(20, 20))]
    quadro = [_f("70006", 1, "domingo"), _f("70007", 2, "domingo")]
    s = _sugerir(_dia(_ctx(SAB, [rend], quadro), alocs))
    assert _re(s, rend, 1) == "70007"         # fiscal da TARDE, não o da manhã
    assert fech.periodo_do_fiscal(alocs[0], PADROES) == 2


def test_prefere_quem_conhece_o_posto():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70010", 2, "domingo"), _f("70011", 2, "domingo")]
    fam = fech.Familiaridade(postos_por_re={"70011": {tp.id}})
    s = _sugerir(_dia(_ctx(SAB, [tp], quadro), [_aloc(tp, 2)]), fam=fam)
    assert _re(s, tp, 2) == "70011"


def test_acumulo_so_quando_falta_gente_e_no_mesmo_ponto_final():
    a1 = _posto("TP", "9201-10", PF_A)
    a2 = _posto("TP", "9202-10", PF_A)
    b1 = _posto("TP", "9203-10", PF_B)
    quadro = [_f("70020", 2, "domingo"), _f("70021", 2, "domingo")]
    alocs = [_aloc(a1, 2, "70020"), _aloc(a2, 2), _aloc(b1, 2)]
    ordem = {(a1.id, 2): 1, (a2.id, 2): 2, (b1.id, 2): 3}
    # Um livre (70021) e duas vagas: a2 tem acúmulo com a1 (mesmo ponto), b1 não.
    s = _sugerir(_dia(_ctx(SAB, [a1, a2, b1], quadro), alocs, ordem=ordem))
    assert _re(s, a2, 2) == "70020"           # acúmulo
    assert _re(s, b1, 2) == "70021"           # o livre foi para quem não tem saída
    assert s.acumulos and not s.vagas


def test_com_gente_sobrando_nao_faz_acumulo():
    a1 = _posto("TP", "9201-10", PF_A)
    a2 = _posto("TP", "9202-10", PF_A)
    quadro = [_f("70020", 2, "domingo"), _f("70021", 2, "domingo")]
    s = _sugerir(_dia(_ctx(SAB, [a1, a2], quadro), [_aloc(a1, 2, "70020"), _aloc(a2, 2)]))
    assert _re(s, a2, 2) == "70021" and not s.acumulos


def test_sem_dobra_deixa_em_branco_e_conta_a_vaga():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70030", 2, "sabado")]
    s = _sugerir(_dia(_ctx(SAB, [tp], quadro), [_aloc(tp, 2, "70030")]))
    assert _re(s, tp, 2) is None
    assert len(s.vagas) == 1 and not s.dobras
    assert "posto em branco" in s.notas[(tp.id, 2)]


def test_com_dobra_sugere_quem_dobrou_menos():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70031", 2, "sabado"), _f("70032", 2, "sabado")]
    s = _sugerir(_dia(_ctx(SAB, [tp], quadro), [_aloc(tp, 2)]), modo="com_dobra",
                 hist=Counter({"70031": 2}))
    assert _re(s, tp, 2) == "70032" and s.dobras == ["70032"]
    assert s.dobras_info[0]["vezes_antes"] == 0


def test_dobra_nunca_para_quem_dobrou_no_fds_anterior_nem_folga_de_troca():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70033", 2, "sabado"), _f("70034", 2, "domingo"), _f("70035", 2, "domingo")]
    # 70034 e 70035 folgam no sábado pela troca 1x1 (não pela base).
    troca = regras.Troca(tipo="1x1", re_a="70034", re_b="70035", data_sabado=SAB)
    ctx = _ctx(SAB, [tp], quadro, trocas=[troca], anteriores={"70033": SAB - timedelta(days=7)})
    s = _sugerir(_dia(ctx, [_aloc(tp, 2)]), modo="com_dobra", nao_dobrar={"70033"})
    assert _re(s, tp, 2) is None and s.vagas and not s.dobras


def test_dobra_do_proprio_fiscal_do_modelo_aparece_nas_mudancas():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70036", 2, "sabado")]
    fam = fech.Familiaridade(postos_por_re={"70036": {tp.id}})
    s = _sugerir(_dia(_ctx(SAB, [tp], quadro), [_aloc(tp, 2, "70036")]), modo="com_dobra", fam=fam)
    assert _re(s, tp, 2) == "70036" and s.dobras == ["70036"]
    assert s.mudancas and s.mudancas[0]["tipo"] == "dobra"


def test_ausente_nunca_entra():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70040", 2, "domingo")]
    aus = regras.Ausencia(re="70040", tipo="ferias", data_inicio=SAB, data_fim=DOM)
    s = _sugerir(_dia(_ctx(SAB, [tp], quadro, ausencias=[aus]), [_aloc(tp, 2)]), modo="com_dobra")
    assert _re(s, tp, 2) is None and s.vagas


def test_periodo_que_o_usuario_nao_edita_nao_e_mexido():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70050", 1, "sabado"), _f("70051", 1, "domingo")]
    s = _sugerir(_dia(_ctx(SAB, [tp], quadro), [_aloc(tp, 1, "70050")]), editaveis=(2,))
    assert _re(s, tp, 1) == "70050"           # nem libera, nem troca
    assert not s.mudancas


def test_sabado_e_domingo_juntos_quem_dobrou_sabado_nao_dobra_domingo():
    tp = _posto("TP", "9101-10")
    # 70060 folga sábado, 70061 folga domingo: um dobra em cada dia, nunca o mesmo.
    quadro = [_f("70060", 2, "sabado"), _f("70061", 2, "domingo")]
    sab = _dia(_ctx(SAB, [tp], quadro), [_aloc(tp, 2), ])
    tp2 = _posto("TP", "9104-10")
    sab.ctx.postos[tp2.id] = tp2
    sab.alocs.append(_aloc(tp2, 2))
    dom = _dia(_ctx(DOM, [tp, tp2], quadro), [_aloc(tp, 2), _aloc(tp2, 2)])
    r = fech.sugerir_fim_de_semana(sab, dom, modo="com_dobra", editaveis={2},
                                   familiaridade=fech.Familiaridade(), historico_dobras=Counter())
    assert r["sabado"].dobras == ["70060"]
    assert "70060" not in r["domingo"].dobras


def test_conta_do_quadro_soma_sabado_e_domingo():
    tp = _posto("TP", "9101-10")
    quadro = [_f("70070", 2, "domingo")]
    s = _sugerir(_dia(_ctx(SAB, [tp], quadro), [_aloc(tp, 2)]))
    d = _sugerir(_dia(_ctx(DOM, [tp], quadro), [_aloc(tp, 2)]))
    conta = fech.conta_do_quadro(s, d, quadro_por_periodo={2: 1}, periodos={2})[0]
    assert conta["postos_obrigatorios"] == 2 and conta["fiscais_no_quadro"] == 1
    assert conta["falta_sem_dobra"] == 1 and conta["quadro_ideal"] == 3 and conta["contratar"] == 2
    assert conta["vagas_em_branco"] == 1


# ─── Parte 2 · rota ──────────────────────────────────────────────────────────
# Banco de test_escala_fiscais_montagem: P1/P2/P3 TP, P4/P5 TS. Modelo:
#   P1 1º 90001 · P1 2º 90002 · P2 1º 90003 · P2 2º em branco · P5 2º 90004.
# Setembro/2026 = ímpar.

SAB_SET, DOM_SET = date(2026, 9, 26), date(2026, 9, 27)


def _quadro_basico(amb):
    amb["add"](_quadro("90001", 1, "domingo"), _quadro("90002", 2, "sabado"),
               _quadro("90003", 1, "domingo"), _quadro("90004", 2, "domingo"),
               _quadro("90005", 2, "domingo"), _quadro("90006", 2, "sabado"))


def test_rota_sugere_sem_gravar(amb):
    _quadro_basico(amb)
    http, ids = amb["http"], amb["ids"]
    r = http.get(f"/escala-fiscais/dias/{SAB_SET}/fechamento")
    assert r.status_code == 200, r.text
    resp = r.json()
    # 90002 folga no sábado (ímpar): sai do P1 2º; 90005 (livre) entra.
    # P2 2º estava em branco no modelo: TP é obrigatório → recebe alguém.
    # O 90004 está no TS P5 (comum): no fim de semana vai para o TP P2.
    assert _linha(resp, ids["P1"], 2)["re"] == "90005"
    assert _linha(resp, ids["P2"], 2)["re"] == "90004"
    assert _linha(resp, ids["P5"], 2)["re"] is None
    fe = resp["fechamento"]
    assert fe["modo"] == "sem_dobra" and fe["sabado"]["incluido"] and fe["domingo"]["incluido"]
    assert any(m["antes"] == "90002" for m in fe["mudancas"])
    assert fe["conta"][0]["periodo"] in (1, 2)
    with Session(amb["engine"]) as db:
        assert db.execute(select(EscalaFiscalDia)).first() is None


def test_rota_sugestao_salva_pelo_put_de_sempre(amb):
    _quadro_basico(amb)
    http = amb["http"]
    resp = http.get(f"/escala-fiscais/dias/{SAB_SET}/fechamento").json()
    r = http.put(f"/escala-fiscais/dias/{SAB_SET}", json=_payload(resp))
    assert r.status_code == 200, r.text
    salvo = r.json()
    assert salvo["status"] == "rascunho"
    assert not salvo["resumo"]["dobras"]          # modo sem_dobra: nenhuma dobra
    assert not salvo["validacao"]["bloqueios"]


def test_rota_com_dobra_preenche_o_tp(amb):
    # Ninguém livre, ninguém no TS comum e P2 sem ponto final (sem acúmulo):
    # só uma dobra (90004 ou 90006, os dois folgam no sábado) cobre o TP vazio.
    amb["add"](_quadro("90001", 1, "domingo"), _quadro("90002", 2, "domingo"),
               _quadro("90003", 1, "domingo"), _quadro("90004", 2, "sabado"),
               _quadro("90006", 2, "sabado"))
    http, ids = amb["http"], amb["ids"]
    assert http.patch(f"/escala-fiscais/postos/{ids['P2']}", json={"ponto_final_id": None}).status_code == 200
    sem = http.get(f"/escala-fiscais/dias/{SAB_SET}/fechamento").json()
    assert _linha(sem, ids["P2"], 2)["re"] is None
    assert sem["fechamento"]["sabado"]["vagas"]
    com = http.get(f"/escala-fiscais/dias/{SAB_SET}/fechamento?modo=com_dobra").json()
    p2 = _linha(com, ids["P2"], 2)
    assert p2["re"] in ("90004", "90006") and p2["dobra"] is True
    assert com["fechamento"]["sabado"]["dobras"][0]["re"] == p2["re"]


def test_rota_nao_mexe_em_escala_publicada(amb):
    _quadro_basico(amb)
    http = amb["http"]
    _abrir_e_salvar(http, SAB_SET)
    assert http.post(f"/escala-fiscais/dias/{SAB_SET}/publicar", json={"confirmacoes": []}).status_code == 200
    r = http.get(f"/escala-fiscais/dias/{SAB_SET}/fechamento")
    assert r.status_code == 409
    # O domingo ainda pode ser fechado; o resumo diz que o sábado ficou de fora.
    dom = http.get(f"/escala-fiscais/dias/{DOM_SET}/fechamento").json()
    assert dom["fechamento"]["sabado"] == {"data": str(SAB_SET), "incluido": False, "motivo": "publicada"}


def test_rota_so_fim_de_semana_e_ausente_nao_entra(amb):
    _quadro_basico(amb)
    http = amb["http"]
    assert http.get("/escala-fiscais/dias/2026-09-28/fechamento").status_code == 422
    amb["add"](EscalaFiscalAusencia(re="90005", tipo="ferias", data_inicio=SAB_SET, data_fim=DOM_SET))
    resp = http.get(f"/escala-fiscais/dias/{SAB_SET}/fechamento").json()
    assert "90005" not in {p["re"] for linha in resp["linhas"] for p in (linha["p1"], linha["p2"]) if p}


def test_rota_respeita_o_periodo_do_coordenador(amb):
    from tests.test_escala_fiscais_montagem import _COORD1
    _quadro_basico(amb)
    amb["como"](_COORD1)                        # edita só o 1º período
    resp = amb["http"].get(f"/escala-fiscais/dias/{SAB_SET}/fechamento").json()
    # O 2º período fica exatamente como o modelo (90002 continua, mesmo de folga).
    assert _linha(resp, amb["ids"]["P1"], 2)["re"] == "90002"
    assert resp["periodos_editaveis"] == [1]


def test_posto_aceita_a_marca_obrigatorio_fds(amb):
    http, ids = amb["http"], amb["ids"]
    r = http.patch(f"/escala-fiscais/postos/{ids['P5']}", json={"obrigatorio_fds": True})
    assert r.status_code == 200, r.text
    assert r.json()["obrigatorio_fds"] is True
    with Session(amb["engine"]) as db:
        assert db.get(EscalaFiscalPosto, ids["P5"]).obrigatorio_fds is True
