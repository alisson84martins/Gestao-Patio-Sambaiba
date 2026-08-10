"""Testes de GET /ocorrencias/autopreencher/veiculo e /pessoa (item 4,
10/08/2026): cadastro central primeiro, última ocorrência como reserva.

O bloco mais importante aqui é a normalização de prefixo: o mesmo ônibus
tem dois números — o Pátio usa 4 dígitos (1721), a Sambaíba usa 5 com a
área na frente (21721 = área 2 + carro 1721 = E2). Um parse ingênuo do
tipo "começa com 2 → AR2" acerta o 22721 e erra o 21721 — os testes 7-9
existem justamente pra pegar esse erro.

Onibus tem coluna GERADA com sintaxe específica do Postgres (CASE ...
::setor_enum) que o SQLite não entende (mesmo motivo documentado em
test_renumerar_fila.py) — por isso a tabela é criada aqui via DDL bruto,
sem a cláusula GENERATED, e o "setor" é inserido como valor comum. Isso
não muda o que o SELECT via ORM lê: o Computed() só afeta INSERT/UPDATE
pelo lado do SQLAlchemy, nunca o SELECT.

Usa TestClient de ponta a ponta (mesmo padrão de test_ocorrencias_smoke.py
e test_ocorrencias_colecoes_filhas.py) — RE e documentos usados são
fictícios (regra de LGPD do SISTEMA-EM-PRODUCAO.md).
"""
import typing
from datetime import date, datetime, time, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.ocorrencia import Ocorrencia, TipoOcorrencia
from app.routers import ocorrencias as router_mod
from app.routers.ocorrencias import normalizar_prefixo

_TABELAS = [
    Funcionario.__table__, Funcao.__table__, FuncionarioFuncao.__table__,
    TipoOcorrencia.__table__, Ocorrencia.__table__,
]

# Sem a cláusula GENERATED — ver docstring do módulo. Colunas equivalentes
# às de app/models/frota.py::Onibus (id, numero_frota, placa, setor,
# status, codigo_externo + AuditoriaMixin).
_DDL_ONIBUS = """
CREATE TABLE onibus (
    id CHAR(36) PRIMARY KEY,
    numero_frota INTEGER NOT NULL UNIQUE,
    placa VARCHAR(10),
    setor VARCHAR(10),
    status VARCHAR(20) NOT NULL DEFAULT 'ATIVO',
    codigo_externo VARCHAR(50),
    criado_em DATETIME,
    criado_por CHAR(36),
    atualizado_em DATETIME,
    atualizado_por CHAR(36)
)
"""

_AUTOR = Funcionario(id=uuid4(), re="40001", nome="Coordenador Autopreenchimento Teste")


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")

    Base.metadata.create_all(engine, tables=_TABELAS)
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL_ONIBUS)

    with Session(engine) as setup:
        setup.add(Funcionario(id=_AUTOR.id, re=_AUTOR.re, nome=_AUTOR.nome))
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

    yield TestClient(app), engine, leitura_dep

    app.dependency_overrides.pop(leitura_dep, None)
    app.dependency_overrides.pop(escrita_dep, None)
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _criar_onibus(engine, numero_frota, placa=None, setor=None):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO onibus (id, numero_frota, placa, setor, status) "
                "VALUES (:id, :numero_frota, :placa, :setor, 'ATIVO')"
            ),
            {"id": str(uuid4()), "numero_frota": numero_frota, "placa": placa, "setor": setor},
        )


def _criar_tipo(db) -> TipoOcorrencia:
    tipo = TipoOcorrencia(
        id=uuid4(), codigo="INCIDENTE", nome="Incidente",
        exige_vitima=False, exige_terceiro=False, exige_analise=False,
        ordem=1, ativo=True,
    )
    db.add(tipo)
    db.flush()
    return tipo


def _criar_ocorrencia(db, tipo, numero=1, **kwargs) -> Ocorrencia:
    base = dict(
        id=uuid4(), numero=numero, tipo_ocorrencia_id=tipo.id, status="RASCUNHO",
        data_ocorrencia=date(2026, 7, 12), hora_ocorrencia=time(9, 0),
        prefixo="0000", cidade="São Paulo",
        via_urbana=False, via_rodoviaria=False, area_interna=False, corredor=False,
        tem_fotos=False, monitoramento=False, ocorrencia_policial=False,
        houve_policia_tecnica=False, registrado_por=_AUTOR.id,
        criado_em=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    oc = Ocorrencia(**base)
    db.add(oc)
    db.flush()
    return oc


# ============================================================================
# normalizar_prefixo() — em unidade, sem banco nem cliente
# ============================================================================

@pytest.mark.parametrize("entrada,esperado", [
    ("21721", 1721),   # área 2 + carro 1721 (E2) — o caso que um parse ingênuo erra
    ("22721", 2721),   # área 2 + carro 2721 (AR2)
    ("1721", 1721),    # 4 dígitos, já no formato do Pátio
    ("2172", 2172),    # 4 dígitos, carro AR2 legítimo — não é "21" + "72"
    ("999", None),     # 3 dígitos — nem 4 nem 5
    ("31721", None),   # 5 dígitos mas área "3" não existe
    ("", None),        # vazio
])
def test_normalizar_prefixo(entrada, esperado):
    assert normalizar_prefixo(entrada) == esperado


# ============================================================================
# /autopreencher/veiculo
# ============================================================================

def test_veiculo_prefixo_no_cadastro_volta_placa_do_cadastro(ctx):
    """Caso 6: prefixo existe no cadastro de ônibus → volta a placa do cadastro."""
    http, engine, _ = ctx
    _criar_onibus(engine, numero_frota=1500, placa="ABC-1234", setor="E2")

    resp = http.get("/ocorrencias/autopreencher/veiculo", params={"prefixo": "1500"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["placa"] == "ABC-1234"
    assert corpo["origem_placa"] == "cadastro"


def test_veiculo_prefixo_5_digitos_area_2_carro_e2(ctx):
    """Caso 7: prefixo=21721 → acha o ônibus 1721 e devolve setor == "E2".
    Se vier "AR2", a normalização está lendo o dígito errado."""
    http, engine, _ = ctx
    _criar_onibus(engine, numero_frota=1721, placa="FIC-1721", setor="E2")

    resp = http.get("/ocorrencias/autopreencher/veiculo", params={"prefixo": "21721"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["numero_frota"] == 1721
    assert corpo["setor"] == "E2"
    assert corpo["placa"] == "FIC-1721"
    assert corpo["prefixo_informado"] == "21721"


def test_veiculo_prefixo_5_digitos_area_2_carro_ar2(ctx):
    """Caso 8: prefixo=22721 → acha o ônibus 2721, setor == "AR2"."""
    http, engine, _ = ctx
    _criar_onibus(engine, numero_frota=2721, placa="FIC-2721", setor="AR2")

    resp = http.get("/ocorrencias/autopreencher/veiculo", params={"prefixo": "22721"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["numero_frota"] == 2721
    assert corpo["setor"] == "AR2"
    assert corpo["placa"] == "FIC-2721"


def test_veiculo_prefixo_4_digitos_converge_com_5_digitos(ctx):
    """Caso 9: prefixo=1721 (4 dígitos, do pátio) → mesmo resultado do
    caso 7 — os dois formatos precisam convergir para o mesmo carro."""
    http, engine, _ = ctx
    _criar_onibus(engine, numero_frota=1721, placa="FIC-1721", setor="E2")

    resp = http.get("/ocorrencias/autopreencher/veiculo", params={"prefixo": "1721"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["numero_frota"] == 1721
    assert corpo["setor"] == "E2"
    assert corpo["placa"] == "FIC-1721"


# ============================================================================
# /autopreencher/pessoa
# ============================================================================

def test_pessoa_re_no_cadastro_com_cpf_volta_do_cadastro(ctx):
    """Caso 1: RE existe no cadastro com CPF → volta CPF do cadastro,
    origem.cpf == "cadastro"."""
    http, engine, _ = ctx
    with Session(engine) as db:
        db.add(Funcionario(
            id=uuid4(), re="50001", nome="Motorista Fictício",
            cpf="11122233344", telefone="(11) 90000-0000",
        ))
        db.commit()

    resp = http.get("/ocorrencias/autopreencher/pessoa", params={"re": "50001", "papel": "condutor"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["cpf"] == "11122233344"
    assert corpo["origem"]["cpf"] == "cadastro"
    assert corpo["nome"] == "Motorista Fictício"
    assert corpo["origem"]["nome"] == "cadastro"


def test_pessoa_sem_rg_no_cadastro_cai_para_ocorrencia_antiga(ctx):
    """Caso 2: RE existe no cadastro sem RG, mas há ocorrência antiga com
    RG → volta o RG da ocorrência, origem.rg == "ocorrencia"."""
    http, engine, _ = ctx
    with Session(engine) as db:
        db.add(Funcionario(
            id=uuid4(), re="50002", nome="Motorista Sem RG",
            cpf="22233344455", telefone="(11) 91111-1111",
        ))
        tipo = _criar_tipo(db)
        _criar_ocorrencia(
            db, tipo, numero=1,
            condutor_re="50002", condutor_nome="Motorista Sem RG",
            condutor_rg="000000001", condutor_cnh="00000000001",
        )
        db.commit()

    resp = http.get("/ocorrencias/autopreencher/pessoa", params={"re": "50002", "papel": "condutor"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["rg"] == "000000001"
    assert corpo["origem"]["rg"] == "ocorrencia"
    # CPF já veio do cadastro — não deve ser sobrescrito pela ocorrência.
    assert corpo["cpf"] == "22233344455"
    assert corpo["origem"]["cpf"] == "cadastro"


def test_pessoa_re_inexistente_retorna_200_com_nulos(ctx):
    """Caso 3: RE não existe em lugar nenhum → 200 com campos nulos, nunca
    404 — quem digita RE errado na rua precisa continuar preenchendo à mão."""
    http, engine, _ = ctx

    resp = http.get("/ocorrencias/autopreencher/pessoa", params={"re": "99999999", "papel": "condutor"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["nome"] is None
    assert corpo["cpf"] is None
    assert corpo["rg"] is None
    assert corpo["cnh"] is None
    assert all(v is None for v in corpo["origem"].values())


def test_pessoa_ignora_ocorrencia_excluida(ctx):
    """Caso 4: ocorrência com excluida_em preenchido (soft delete) não pode
    ser fonte de dado. Confirma primeiro que a linha existe de verdade na
    tabela (prova que o filtro é quem está barrando, não a ausência do
    dado) — é o mesmo espírito de test_ocorrencias_colecoes_filhas.py:
    reintroduzir a falha de propósito prova que o teste pega."""
    http, engine, _ = ctx
    with Session(engine) as db:
        tipo = _criar_tipo(db)
        oc = _criar_ocorrencia(
            db, tipo, numero=1,
            condutor_re="50003", condutor_nome="Fantasma Excluído",
            condutor_cpf="33344455566",
            excluida_em=datetime.now(timezone.utc),
        )
        db.commit()
        oc_id = oc.id

    with Session(engine) as db:
        # Sem o filtro excluida_em (select(Ocorrencia) puro, igual ao db.get)
        # — prova que a linha existe de verdade, não que faltou dado.
        assert db.get(Ocorrencia, oc_id) is not None

    resp = http.get("/ocorrencias/autopreencher/pessoa", params={"re": "50003", "papel": "condutor"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["nome"] is None
    assert corpo["cpf"] is None
    assert all(v is None for v in corpo["origem"].values())


def test_pessoa_cobrador_sem_documento_na_ocorrencia_so_recebe_nome(ctx):
    """Ocorrencia não tem cobrador_funcao/cnh/rg/cpf — só cobrador_nome.
    Pra papel="cobrador" sem cadastro, só o nome pode vir da ocorrência."""
    http, engine, _ = ctx
    with Session(engine) as db:
        tipo = _criar_tipo(db)
        _criar_ocorrencia(db, tipo, numero=1, cobrador_re="50004", cobrador_nome="Cobrador Fictício")
        db.commit()

    resp = http.get("/ocorrencias/autopreencher/pessoa", params={"re": "50004", "papel": "cobrador"})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["nome"] == "Cobrador Fictício"
    assert corpo["origem"]["nome"] == "ocorrencia"
    assert corpo["cpf"] is None
    assert corpo["rg"] is None
    assert corpo["cnh"] is None
    assert corpo["funcao"] is None


def test_pessoa_sem_permissao_retorna_403(ctx):
    """Caso 5: sem permissão 'ocorrencia' → 403."""
    http, engine, leitura_dep = ctx

    def _negar_leitura():
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Sem permissão para ler 'ocorrencia'")

    app.dependency_overrides[leitura_dep] = _negar_leitura

    resp = http.get("/ocorrencias/autopreencher/pessoa", params={"re": "50001", "papel": "condutor"})

    assert resp.status_code == 403, resp.text
