"""Bloco G — correção pelo fiscal (sem tocar no NIMER), auditoria e prévia do fechamento.

Comportamento ERRADO que os testes barram: correção sem justificativa (422),
fiscal corrigindo linha fora do turno dele (403), correção apagando o dado
do NIMER, fechamento contando a perda de trânsito como contador.
⛔ Sem dado pessoal real: REs fictícios.
"""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import Turno, TurnoLinha
from app.models.monitoramento import LinhaMonitoradaCadastro, NimerViagem, ViagemCorrecao
from app.routers import monitoramento as router_mod
from app.services import monitoramento_acesso as acesso
from tests.ambiente_monitoramento import criar_engine

DIA = date(2026, 11, 10)
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)  # 09:00 SP
FISCAL_A = Funcionario(id=uuid4(), re="70001", nome="Fiscal A")
FISCAL_B = Funcionario(id=uuid4(), re="70002", nome="Fiscal B")
COORD = Funcionario(id=uuid4(), re="70003", nome="Coordenador")


def _dep(annotated):
    return annotated.__metadata__[0].dependency


@pytest.fixture
def ambiente(monkeypatch):
    eng = criar_engine()
    with Session(eng) as db:
        for f in (FISCAL_A, FISCAL_B, COORD):
            db.add(Funcionario(id=f.id, re=f.re, nome=f.nome, status="ATIVO"))
        db.add(LinhaMonitoradaCadastro(linha_codigo="8012-10", nimer_line_id=99, ativo=True))
        turno = Turno(id=uuid4(), funcionario_id=FISCAL_A.id, fiscal_re=FISCAL_A.re, terminal="TP", periodo="1",
                      data_referencia=DIA, tipo_dia="UTIL", status="ABERTO", aberto_em=T0 - timedelta(hours=5),
                      refeicao_inicio=None, refeicao_fim=None)
        db.add(turno)
        db.add(TurnoLinha(id=uuid4(), turno_id=turno.id, linha_codigo="1726-10"))
        base = dict(data_servico=DIA, linha_codigo="1726-10", sentido=1, motorista_re="90011", cobrador_re="90012")
        db.add(NimerViagem(id=1, nimer_departure_id="1", tabela="032104", status="lost", prog_inicio=T0,
                           carro="2412", motivo_perda_texto="1 - Falta de Motorista", **base))
        db.add(NimerViagem(id=2, nimer_departure_id="2", tabela="032116", status="lost",
                           prog_inicio=T0 + timedelta(minutes=30), carro="2293",
                           motivo_perda_texto="4- Recolhe Anormal / SOS", **base))
        db.add(NimerViagem(id=3, nimer_departure_id="3", tabela="032207", status="done",
                           prog_inicio=T0 + timedelta(minutes=90), real_inicio=T0 + timedelta(minutes=91),
                           carro="2293", **base))
        db.add(NimerViagem(id=4, nimer_departure_id="4", tabela="032108", status="lost",
                           prog_inicio=T0 + timedelta(minutes=60), carro="2500", motivo_perda_texto="3 - Trânsito",
                           **base))
        db.add(NimerViagem(id=5, nimer_departure_id="5", tabela="080101", status="lost", prog_inicio=T0,
                           carro="2700", **dict(base, linha_codigo="8012-10")))
        db.commit()
        turno_id = turno.id

    def _db():
        with Session(eng) as db:
            yield db

    estado = {"usuario": FISCAL_A}
    monkeypatch.setattr(acesso, "tem_acesso", lambda db, fid, recurso, escrever=False:
                        recurso == "fiscalizacao_painel" and fid == COORD.id)
    app.dependency_overrides[get_db] = _db
    for dep in (_dep(router_mod.EscritaFiscal), _dep(router_mod.Leitura), _dep(router_mod.LeituraPainel)):
        app.dependency_overrides[dep] = lambda: estado["usuario"]
    try:
        yield {"client": TestClient(app), "engine": eng, "turno_id": turno_id, "como": lambda f: estado.update(usuario=f)}
    finally:
        app.dependency_overrides.clear()


def _corrigir(cli, viagem_id, campo, valor, justificativa="conferido no terminal"):
    return cli.post(f"/monitoramento/viagens/{viagem_id}/correcoes",
                    json={"campo": campo, "valor": valor, "justificativa": justificativa})


def test_correcao_sem_justificativa_422(ambiente):
    cli = ambiente["client"]
    assert _corrigir(cli, 1, "status", "done", justificativa="").status_code == 422
    r = _corrigir(cli, 1, "status", "done", justificativa="   ")
    assert r.status_code == 422 and "erro" in r.json()
    r = cli.post("/monitoramento/viagens/1/correcoes", json={"campo": "status", "valor": "done"})
    assert r.status_code == 422
    with Session(ambiente["engine"]) as db:
        assert db.execute(select(ViagemCorrecao)).scalars().all() == []


def test_fiscal_sem_turno_aberto_403(ambiente):
    ambiente["como"](FISCAL_B)
    r = _corrigir(ambiente["client"], 1, "status", "done")
    assert r.status_code == 403 and r.json()["erro"] == "Abra o turno para ver a linha ao vivo"


def test_fiscal_corrigindo_linha_fora_do_turno_403(ambiente):
    r = _corrigir(ambiente["client"], 5, "status", "done")
    assert r.status_code == 403 and r.json()["erro"] == "Esta linha não está no seu turno aberto"


def test_coordenador_nao_corrige_viagem(ambiente):
    ambiente["como"](COORD)  # sem turno aberto: a correção é do fiscal da linha
    assert _corrigir(ambiente["client"], 1, "status", "done").status_code == 403


def test_correcao_muda_o_status_efetivo_e_nao_toca_no_nimer(ambiente):
    cli = ambiente["client"]
    r = _corrigir(cli, 1, "status", "done", "carro saiu, NIMER não registrou")
    assert r.status_code == 201, r.text
    assert r.json()["valor_nimer"] == "lost" and r.json()["valor_corrigido"] == "done"
    assert "não foi alterado" in r.json()["aviso"]
    with Session(ambiente["engine"]) as db:
        assert db.get(NimerViagem, 1).status == "lost"   # o dado do NIMER fica como veio
    j = cli.get("/monitoramento/linhas/1726-10/tempo-real?data=2026-11-10").json()
    p = {x["id"]: x for x in j["partidas"]}[1]
    assert p["status_nimer"] == "lost" and p["status_efetivo"] == "done" and p["corrigida"]


def test_vale_a_correcao_mais_recente_e_nenhuma_e_apagada(ambiente):
    cli = ambiente["client"]
    _corrigir(cli, 1, "carro", "2413", "primeira leitura")
    _corrigir(cli, 1, "carro", "22414", "era o 2414 (prefixo do SIM)")
    with Session(ambiente["engine"]) as db:
        assert len(db.execute(select(ViagemCorrecao)).scalars().all()) == 2
    p = {x["id"]: x for x in cli.get("/monitoramento/linhas/1726-10/tempo-real?data=2026-11-10").json()["partidas"]}[1]
    assert p["carro_programado"] == "2414" and p["carro_nimer"] == "2412"


def test_valores_invalidos_422(ambiente):
    cli = ambiente["client"]
    assert _corrigir(cli, 1, "status", "talvez").status_code == 422
    assert _corrigir(cli, 1, "motivo", "INVENTADO").status_code == 422
    assert _corrigir(cli, 1, "carro", "abc").status_code == 422
    assert _corrigir(cli, 1, "real_inicio", "25:99").status_code == 422
    assert _corrigir(cli, 1, "campo_que_nao_existe", "x").status_code == 422
    assert _corrigir(cli, 999, "status", "done").status_code == 404


def test_saida_real_corrigida_em_hh_mm_vira_instante_do_dia_de_servico(ambiente):
    r = _corrigir(ambiente["client"], 1, "real_inicio", "09:04")
    assert r.status_code == 201
    assert datetime.fromisoformat(r.json()["valor_corrigido"]) == T0 + timedelta(minutes=4)


def test_coordenacao_ve_a_auditoria_de_quem_quando_de_para(ambiente):
    cli = ambiente["client"]
    _corrigir(cli, 2, "motivo", "TROCA_OPERACIONAL", "carro trocado no TP")
    ambiente["como"](COORD)
    (a,) = cli.get("/monitoramento/correcoes?data=2026-11-10").json()
    assert a["autor"] == {"re": "70001", "nome": "Fiscal A"}
    assert a["de"] == "4- Recolhe Anormal / SOS" and a["para"] == "TROCA_OPERACIONAL"
    assert a["tabela"] == "032116" and a["programado"] == "09:30" and a["justificativa"] == "carro trocado no TP"
    assert cli.get("/monitoramento/correcoes?data=2026-11-10&linha_codigo=8012").json() == []


def test_previa_do_fechamento_pelo_status_efetivo(ambiente):
    cli = ambiente["client"]
    (linha,) = cli.get(f"/monitoramento/turnos/{ambiente['turno_id']}/fechamento-previa").json()
    assert linha["fonte"] == "NIMER" and linha["criterio_periodo"] == "TURNO"
    t = linha["texto"]
    assert "PARTIDAS PROGRAMADAS: 1° período 4" in t and "PARTIDAS REALIZADAS: 1° período 1" in t
    assert " S.O.S: 01" in t          # 2293 perdeu por recolhe e voltou na 032207
    assert " R.A: 00" in t
    assert " FALTA DE OPERADORES: 00" in t
    assert " FALTA DE MOTORISTA: 01" in t    # 11/11: motivo fora da tabela vira linha própria, não OBS
    assert "Falta de Motorista, viagem" not in t
    assert "Tabela 032108, trânsito, viagem das 10:00 não realizada." in t   # D27: fora dos contadores
    # o fiscal corrige e a prévia acompanha
    _corrigir(cli, 1, "motivo", "FALTA_OPERADORES", "motorista faltou, conferido com a escala")
    t2 = cli.get(f"/monitoramento/turnos/{ambiente['turno_id']}/fechamento-previa").json()[0]["texto"]
    assert " FALTA DE OPERADORES: 01" in t2 and "FALTA DE MOTORISTA" not in t2


def test_previa_de_turno_de_outro_fiscal_403(ambiente):
    ambiente["como"](FISCAL_B)
    assert ambiente["client"].get(f"/monitoramento/turnos/{ambiente['turno_id']}/fechamento-previa").status_code == 403
    ambiente["como"](COORD)
    assert ambiente["client"].get(f"/monitoramento/turnos/{ambiente['turno_id']}/fechamento-previa").status_code == 200


# ─── Motivos do NIMER pelo padrão do NIMER (11/11, item 6) ─────────────────

def test_cada_motivo_da_tabela_vai_para_o_lugar_certo_no_fechamento():
    from app.services.fechamento_fiscal import contagem_tempo_real, texto_fechamento_linha
    from tests.cenario_tempo_real import calcular, viagem
    viagens = [
        viagem("A", 1, 10, "lost", carro="3001", motivo="1 - Falta de Operador"),
        viagem("B", 1, 20, "lost", carro="3002", motivo="6- Atraso de Garagem - FALTA DE CARRO"),
        viagem("C", 1, 30, "lost", carro="3003", motivo="6 - atraso de garagem-falta de carro"),   # mesmo motivo
        viagem("D", 1, 40, "lost", carro="2293", motivo="4- Recolhe Anormal / SOS"),
        viagem("D2", 2, 120, "done", carro="2293", real_min=121),                                # voltou → S.O.S
        viagem("E", 1, 50, "lost", carro="3005", motivo="4 - Recolhe Anormal / SOS"),            # não voltou → R.A
        viagem("F", 1, 60, "lost", carro="3006", motivo="3 - Trânsito"),
        viagem("G", 1, 70, "lost", carro="3007", motivo="9- Quebra no Itinerário"),              # desconhecido
        viagem("H", 1, 80, "lost", carro="3008", motivo="9 - QUEBRA NO ITINERÁRIO"),
    ]
    res = calcular(viagens, [], agora_min=300)
    programadas, realizadas, contagem, obs, proprios = contagem_tempo_real(res["viagens"])
    assert contagem["FALTA_OPERADORES"] == 1 and contagem["ATRASO_GARAGEM"] == 2
    assert contagem["SOS"] == 1 and contagem["RA"] == 1
    assert proprios == {"QUEBRA NO ITINERÁRIO": 2}
    assert len(obs) == 1 and "trânsito" in obs[0]                     # só o trânsito vai para o OBS (D27)
    turno = Turno(fiscal_re="70001", periodo="1", data_referencia=DIA)
    texto = texto_fechamento_linha(turno, "1726-10", programadas, realizadas, contagem, obs, {}, [],
                                   contadores_proprios=proprios)
    linhas = texto.splitlines()
    assert " QUEBRA NO ITINERÁRIO: 02" in linhas
    assert linhas.index(" QUEBRA NO ITINERÁRIO: 02") < linhas.index("OBS:")   # contado, fora do OBS
    assert " ATRASO DE GARAGEM 02" in linhas and " S.O.S: 01" in linhas and " R.A: 01" in linhas


def test_fiscal_ainda_pode_trocar_o_motivo_do_desconhecido(ambiente):
    cli = ambiente["client"]
    _corrigir(cli, 1, "motivo", "ATRASO_GARAGEM", "era falta de carro")
    t = cli.get(f"/monitoramento/turnos/{ambiente['turno_id']}/fechamento-previa").json()[0]["texto"]
    assert " ATRASO DE GARAGEM 01" in t and "FALTA DE MOTORISTA" not in t
