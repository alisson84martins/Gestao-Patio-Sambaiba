"""Item 4 — lote retroativo scripts/criar_acessos_ocorrencias.py.

⛔ Nenhum dado pessoal real — ver tests/ambiente_acesso.py.
"""
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.auditoria import LogAcesso
from app.models.cadastro import Funcionario, FuncionarioFuncao, UsuarioLogin
from app.models.pessoas import Usuario
from app.models.pre_cadastro import PessoaPreCadastro
from app.core.security import hash_password
from scripts import criar_acessos_ocorrencias as lote
from tests.ambiente_acesso import CPF_A, CPF_B, CPF_C, CPF_D, criar_engine, inserir_ocorrencia


@pytest.fixture
def engine():
    eng = criar_engine()
    # 60001: duas ocorrências — a mais nova sem CPF, a mais antiga com CPF
    #        válido (a com CPF de 11 dígitos tem que ganhar).
    inserir_ocorrencia(eng, re="60001", nome="Joaquim Pereira", cpf=CPF_A)
    inserir_ocorrencia(eng, re="60001", nome="Joaquim Pereira", cpf=None)
    # 60002 e 60003: mesmo CPF em REs diferentes → os dois pendentes.
    inserir_ocorrencia(eng, re="60002", nome="Carlos Alberto", cpf=CPF_C)
    inserir_ocorrencia(eng, re="60003", nome="Reginaldo Dias", cpf=CPF_C)
    # 60004: sem CPF → pendente.
    inserir_ocorrencia(eng, re="60004", nome="Sem Cpf", cpf=None)
    # 60005: já tem login (CPF antigo) → JA_TEM_LOGIN, intocado.
    inserir_ocorrencia(eng, re="60005", nome="Ja Logado", cpf=CPF_D)
    # 60006: válido.
    inserir_ocorrencia(eng, re="60006", nome="Maria Souza", cpf=CPF_B)
    with Session(eng) as db:
        fid = uuid4()
        db.add(Funcionario(id=fid, re="60005", nome="Ja Logado", cpf=CPF_D, status="ATIVO"))
        db.add(UsuarioLogin(funcionario_id=fid, senha_hash=hash_password("6757"), politica_senha="CPF"))
        db.commit()
    yield eng
    eng.dispose()


def _contagens(engine):
    with Session(engine) as db:
        return tuple(
            db.scalar(select(func.count()).select_from(t))
            for t in (Funcionario, UsuarioLogin, FuncionarioFuncao, Usuario, LogAcesso, PessoaPreCadastro)
        )


def _situacoes(resultados):
    return {r.re: r.situacao for r, _ in resultados}


def test_selecao_prefere_cpf_de_11_digitos(engine):
    with Session(engine) as db:
        condutores = {re: cpf for re, _, cpf in lote.selecionar_condutores(db)}
    assert condutores["60001"] == CPF_A
    assert len(condutores) == 6


def test_simulacao_nao_grava_nada(engine):
    antes = _contagens(engine)
    with Session(engine) as db:
        resultados = lote.processar(db, executar=False)
    assert _situacoes(resultados) == {
        "60001": "CRIADO", "60002": "PENDENTE", "60003": "PENDENTE",
        "60004": "PENDENTE", "60005": "JA_TEM_LOGIN", "60006": "CRIADO",
    }
    assert _contagens(engine) == antes


def test_executar_duas_vezes_nao_duplica(engine):
    with Session(engine) as db:
        primeira = lote.processar(db, executar=True)
    depois_1 = _contagens(engine)
    with Session(engine) as db:
        segunda = lote.processar(db, executar=True)
    assert _contagens(engine) == depois_1

    assert sum(r.situacao == "CRIADO" for r, _ in primeira) == 2
    assert _situacoes(segunda)["60001"] == "JA_TEM_LOGIN"
    assert _situacoes(segunda)["60006"] == "JA_TEM_LOGIN"
    assert sum(r.situacao == "CRIADO" for r, _ in segunda) == 0

    with Session(engine) as db:
        politicas = dict(db.execute(
            select(Funcionario.re, UsuarioLogin.politica_senha)
            .join(UsuarioLogin, UsuarioLogin.funcionario_id == Funcionario.id)
        ).all())
    assert politicas == {"60001": "PROVISORIA", "60006": "PROVISORIA", "60005": "CPF"}


def test_saida_mascara_cpf(engine, capsys):
    with Session(engine) as db:
        resultados = lote.processar(db, executar=False)
    lote.imprimir(resultados, executar=False)
    saida = capsys.readouterr().out
    for cpf in (CPF_A, CPF_B, CPF_C, CPF_D):
        assert cpf not in saida
    assert "***.***.*47-25" in saida
    assert "SIMULAÇÃO" in saida
    assert "CRIADO=2, JA_TEM_LOGIN=1, PENDENTE=3" in saida


def test_erro_inesperado_num_re_nao_derruba_o_lote(engine, monkeypatch):
    original = lote.criar_acesso

    def _talvez_explode(db, **kw):
        if kw["re"] == "60001":
            raise RuntimeError("falha simulada")
        return original(db, **kw)

    monkeypatch.setattr(lote, "criar_acesso", _talvez_explode)
    with Session(engine) as db:
        resultados = lote.processar(db, executar=True)
    sit = _situacoes(resultados)
    assert sit["60001"] == "PENDENTE" and sit["60006"] == "CRIADO"
