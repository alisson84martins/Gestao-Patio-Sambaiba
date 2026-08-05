"""Teste de regressão do item 4 (01/08/2026): veículo de terceiro e
vítima "sumindo" da mensagem do sinistro.

Causa real, encontrada por diagnóstico de código (sem acesso ao banco de
produção nesta sessão) e confirmada isolando o mecanismo com SQLite puro
antes de mexer no router: `OcorrenciaVeiculoTerceiro`, `OcorrenciaAvaria`,
`OcorrenciaVitima` e `OcorrenciaTestemunha` têm UniqueConstraint em
(ocorrencia_id, ordem) ou (ocorrencia_id, regiao). O PATCH substitui a
coleção inteira com `.clear()` + append de objetos novos — mas o flush
do SQLAlchemy roda inserts/updates ANTES de deletes por padrão. Editar
um item que JÁ EXISTIA (mesma ordem — o caso normal de continuar
preenchendo um veículo/vítima num autosave seguinte) tentava inserir a
linha nova antes de apagar a antiga e colidia na unique constraint:
IntegrityError → 409 → a edição não salvava, e sobrava só o dado
incompleto do primeiro save. Não era falta de dado no payload (o
frontend sempre manda a coleção inteira) nem bug em
_bloco_terceiro/_bloco_vitima (que já leem exatamente os campos que a
mensagem do sinistro usa — conferido contra
MODULO-OCORRENCIAS-MENSAGEM-SINISTRO.md).

Reprodução isolada do mecanismo: ver o script descartável usado durante
o diagnóstico (SQLite em memória, tabela genérica com UniqueConstraint
igual à de produção) — não versionado aqui, só a prova final via API.

Usa SQLite em memória com as tabelas reais de coordenadoria (schema
"coordenadoria" anexado via ATTACH DATABASE) — mesmo padrão de
test_renumerar_fila.py: só funciona se a unique constraint estiver
mesmo lá, então prova a fixação de verdade (reintroduzindo o bug —
removendo os db.flush() do router — os dois PATCH abaixo voltam a
falhar no segundo).

Dados fictícios (ver R3 em PROMPT-EXECUCAO-COMPLETA.md / regra de LGPD
do SISTEMA-EM-PRODUCAO.md) — nunca dado real de vítima ou terceiro.
"""
import typing
from datetime import date, datetime, time, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.ocorrencia import (
    Ocorrencia, OcorrenciaAnalise, OcorrenciaAnexo, OcorrenciaAutoridade,
    OcorrenciaAvaria, OcorrenciaTestemunha, OcorrenciaVeiculoTerceiro,
    OcorrenciaVitima, OrgaoAutoridade, TipoOcorrencia,
)
from app.routers import ocorrencias as router_mod

_TABELAS = [
    Funcionario.__table__,  # mensagem-sinistro busca o nome de quem registrou
    TipoOcorrencia.__table__, OrgaoAutoridade.__table__, Ocorrencia.__table__,
    OcorrenciaAnalise.__table__, OcorrenciaVeiculoTerceiro.__table__, OcorrenciaAvaria.__table__,
    OcorrenciaVitima.__table__, OcorrenciaTestemunha.__table__, OcorrenciaAutoridade.__table__,
    OcorrenciaAnexo.__table__,
]

_AUTOR = Funcionario(id=uuid4(), re="30001", nome="Coordenador Teste Colecoes")


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def cliente():
    # StaticPool + check_same_thread=False: os endpoints síncronos rodam
    # via run_in_threadpool (thread diferente da que monta o fixture) e
    # o TestClient dispara a requisição noutra thread também — sem isso
    # cada conexão nova abriria um SQLite ":memory:" próprio e vazio.
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")

    Base.metadata.create_all(engine, tables=_TABELAS)

    tipo_id = uuid4()
    ocorrencia_id = uuid4()
    tipo = TipoOcorrencia(
        id=tipo_id, codigo="INCIDENTE", nome="Incidente",
        exige_vitima=False, exige_terceiro=False, exige_analise=False,
        ordem=1, ativo=True,
    )
    oc = Ocorrencia(
        id=ocorrencia_id, numero=1, tipo_ocorrencia_id=tipo_id, status="RASCUNHO",
        data_ocorrencia=date(2026, 8, 1), hora_ocorrencia=time(10, 0), prefixo="1234",
        cidade="São Paulo",
        via_urbana=False, via_rodoviaria=False, area_interna=False, corredor=False,
        tem_fotos=False, monitoramento=False, ocorrencia_policial=False,
        houve_policia_tecnica=False, registrado_por=_AUTOR.id,
        criado_em=datetime.now(timezone.utc),
    )
    with Session(engine) as setup:
        setup.add(Funcionario(id=_AUTOR.id, re=_AUTOR.re, nome=_AUTOR.nome))
        setup.add(tipo)
        setup.add(oc)
        setup.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)
    escrita_dep = _dependency_de(router_mod.EscritaOcorrencia)
    app.dependency_overrides[leitura_dep] = lambda: _AUTOR
    app.dependency_overrides[escrita_dep] = lambda: _AUTOR
    app.dependency_overrides[get_db] = _get_db_teste

    yield TestClient(app), ocorrencia_id

    app.dependency_overrides.pop(leitura_dep, None)
    app.dependency_overrides.pop(escrita_dep, None)
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def test_editar_veiculo_e_vitima_em_dois_saves_persiste_tudo(cliente):
    """Caminho de salvamento completo: cria ocorrência (fixture), salva
    veículo+vítima com poucos campos (1º autosave), edita o MESMO
    veículo+vítima com mais campos (2º autosave — é aqui que o bug
    original quebrava), relê e confirma que a segunda edição venceu."""
    http, ocorrencia_id = cliente

    # 1º autosave — só alguns campos, igual a alguém que acabou de
    # clicar "+ Adicionar veículo/vítima" e ainda está digitando.
    resp1 = http.patch(f"/ocorrencias/{ocorrencia_id}", json={
        "veiculos_terceiro": [{"ordem": 1, "marca": "Fiat"}],
        "vitimas": [{"ordem": 1, "nome": "Vítima Fictícia"}],
    })
    assert resp1.status_code == 200, resp1.text

    # 2º autosave — mesma ordem (mesmo item), mais campos preenchidos.
    # Sem os db.flush() no router, este PATCH voltava 409.
    resp2 = http.patch(f"/ocorrencias/{ocorrencia_id}", json={
        "veiculos_terceiro": [{
            "ordem": 1, "marca": "Fiat", "modelo": "Argo Drive 1.3",
            "placa": "FIC-1234", "cidade": "São Paulo",
            "proprietario": "Fulano de Tal Fictício", "fones": "(11) 90000-0000",
        }],
        "vitimas": [{
            "ordem": 1, "nome": "Vítima Fictícia", "rg": "000000",
            "endereco": "Rua Fictícia", "numero": "50", "bairro": "Bairro X",
            "cidade": "São Paulo", "fone": "(11) 91111-1111",
        }],
    })
    assert resp2.status_code == 200, resp2.text

    # Relê — precisa ter o dado do SEGUNDO save, não do primeiro.
    detalhe = http.get(f"/ocorrencias/{ocorrencia_id}")
    assert detalhe.status_code == 200, detalhe.text
    corpo = detalhe.json()

    assert len(corpo["veiculos_terceiro"]) == 1
    assert corpo["veiculos_terceiro"][0]["modelo"] == "Argo Drive 1.3"
    assert corpo["veiculos_terceiro"][0]["placa"] == "FIC-1234"

    assert len(corpo["vitimas"]) == 1
    assert corpo["vitimas"][0]["rg"] == "000000"
    assert corpo["vitimas"][0]["endereco"] == "Rua Fictícia"

    # E aparecem na mensagem do sinistro — o sintoma relatado por Alisson.
    sinistro = http.get(f"/ocorrencias/{ocorrencia_id}/mensagem-sinistro")
    assert sinistro.status_code == 200, sinistro.text
    texto = sinistro.json()["texto"]
    assert "*Dados do terceiro*" in texto
    assert "Placa: FIC-1234" in texto
    assert "*Dados da Vítima*" in texto
    assert "RG: 000000" in texto
