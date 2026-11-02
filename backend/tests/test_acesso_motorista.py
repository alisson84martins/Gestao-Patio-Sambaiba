"""Serviço de acesso do motorista (app/services/acesso_motorista.py) —
conferências de D5 e criação do login PROVISORIA.

Padrão do projeto: prova o ERRADO (não cria) tanto quanto o certo.
⛔ Nenhum dado pessoal real — ver tests/ambiente_acesso.py.
"""
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.auditoria import LogAcesso
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao, UsuarioLogin
from app.models.pessoas import Motorista, Usuario
from app.models.pre_cadastro import PessoaPreCadastro
from app.services import acesso_motorista as svc
from tests.ambiente_acesso import (
    CPF_A, CPF_B, CPF_C, criar_engine, inserir_fonte, inserir_ocorrencia,
    login_de, pre_cadastro_de,
)


@pytest.fixture
def engine():
    eng = criar_engine()
    yield eng
    eng.dispose()


def _criar(engine, *, re="30001", nome="Joaquim Pereira", cpf=CPF_A, funcao="MOTORISTA"):
    with Session(engine) as db:
        r = svc.criar_acesso(db, re=re, nome=nome, cpf=cpf, funcao_codigo=funcao,
                             origem="OCORRENCIA", criado_por=None)
        db.commit()
        return r


# ─── validar_cpf / mascarar_cpf ──────────────────────────────────────────────

@pytest.mark.parametrize("cpf", [CPF_A, "529.982.247-25", CPF_B])
def test_validar_cpf_aceita_validos(cpf):
    assert svc.validar_cpf(cpf)


@pytest.mark.parametrize("cpf", [
    "52998224724",       # 2º dígito errado
    "52998224715",       # 1º dígito errado
    "11111111111", "00000000000",  # todos iguais passam na conta, não são CPF
    "5299822472", "", None, "abc",
])
def test_validar_cpf_recusa_invalidos(cpf):
    assert not svc.validar_cpf(cpf)


def test_mascarar_cpf_so_mostra_os_4_ultimos():
    assert svc.mascarar_cpf(CPF_A) == "***.***.*47-25"
    assert "529982" not in svc.mascarar_cpf(CPF_A)
    assert svc.mascarar_cpf("123") == "***"


# ─── Caminho feliz ───────────────────────────────────────────────────────────

def test_cria_funcionario_funcao_login_provisorio_espelho_e_log(engine):
    r = _criar(engine)
    assert r.situacao == "CRIADO", r.motivos

    login = login_de(engine, "30001")
    assert login is not None
    assert login.politica_senha == "PROVISORIA"
    assert login.ativo
    assert verify_password(CPF_A[-4:], login.senha_hash)

    with Session(engine) as db:
        f = db.execute(select(Funcionario).where(Funcionario.re == "30001")).scalar_one()
        assert f.cpf == CPF_A and f.nome == "Joaquim Pereira"
        codigos = db.execute(
            select(Funcao.codigo).join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
            .where(FuncionarioFuncao.funcionario_id == f.id, FuncionarioFuncao.ativo.is_(True))
        ).scalars().all()
        assert codigos == ["MOTORISTA"]
        espelho = db.execute(select(Usuario).where(Usuario.re == "30001")).scalar_one()
        assert espelho.ativo and espelho.senha_hash == login.senha_hash
        logs = db.execute(select(LogAcesso).where(LogAcesso.evento == "ACESSO_CRIADO")).scalars().all()
        assert [(l.funcionario_id, l.recurso) for l in logs] == [(f.id, "OCORRENCIA")]


def test_ja_tem_login_nao_faz_nada(engine):
    _criar(engine)
    r = _criar(engine)
    assert r.situacao == "JA_TEM_LOGIN"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(UsuarioLogin)) == 1
        assert db.scalar(select(func.count()).select_from(LogAcesso)) == 1


def test_funcionario_existente_sem_login_com_mesmo_cpf_ganha_funcao_e_login(engine):
    with Session(engine) as db:
        db.add(Funcionario(id=uuid4(), re="30002", nome="Maria Souza", cpf=CPF_B, status="ATIVO"))
        db.commit()
    r = _criar(engine, re="30002", nome="Maria Souza", cpf=CPF_B)
    assert r.situacao == "CRIADO", r.motivos
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Funcionario).where(Funcionario.re == "30002")) == 1
    assert login_de(engine, "30002").politica_senha == "PROVISORIA"


def test_funcionario_existente_com_cpf_diferente_nao_cria(engine):
    with Session(engine) as db:
        db.add(Funcionario(id=uuid4(), re="30003", nome="Paulo Lima", cpf=CPF_B, status="ATIVO"))
        db.commit()
    r = _criar(engine, re="30003", nome="Paulo Lima", cpf=CPF_A)
    assert r.situacao == "PENDENTE"
    assert any("outro CPF" in m for m in r.motivos)
    assert login_de(engine, "30003") is None


def test_pre_cadastro_pendente_vira_promovido(engine):
    with Session(engine) as db:
        db.add(PessoaPreCadastro(re="30004", nome="Ana Clara", papel_sugerido="MOTORISTA",
                                 status="PENDENTE", pendencia="motivo antigo"))
        db.commit()
    r = _criar(engine, re="30004", nome="Ana Clara")
    assert r.situacao == "CRIADO"
    pre = pre_cadastro_de(engine, "30004")
    assert pre.status == "PROMOVIDO"
    assert pre.funcionario_id == r.funcionario_id
    assert pre.pendencia is None


# ─── Conferências: provar o ERRADO ───────────────────────────────────────────

@pytest.mark.parametrize("cpf_ruim, motivo", [
    ("52998224724", "dígito verificador"),
    ("11111111111", "dígito verificador"),
    (None, "Sem CPF"),
])
def test_cpf_invalido_nao_cria_e_grava_pendencia(engine, cpf_ruim, motivo):
    r = _criar(engine, re="31001", cpf=cpf_ruim)
    assert r.situacao == "PENDENTE"
    assert any(motivo in m for m in r.motivos)
    assert login_de(engine, "31001") is None
    with Session(engine) as db:
        assert db.execute(select(Funcionario).where(Funcionario.re == "31001")).first() is None
    pre = pre_cadastro_de(engine, "31001")
    assert pre is not None and pre.status == "PENDENTE"
    assert motivo in pre.pendencia


def test_cpf_de_outro_funcionario_nao_cria(engine):
    with Session(engine) as db:
        db.add(Funcionario(id=uuid4(), re="15652", nome="Outra Pessoa", cpf=CPF_A, status="ATIVO"))
        db.commit()
    r = _criar(engine, re="31002", cpf=CPF_A)
    assert r.situacao == "PENDENTE"
    assert "CPF já é do funcionário RE 15652" in r.motivos
    assert login_de(engine, "31002") is None
    assert "15652" in pre_cadastro_de(engine, "31002").pendencia


def test_cpf_repetido_em_ocorrencias_com_res_diferentes_nenhum_dos_dois_cria(engine):
    inserir_ocorrencia(engine, re="16023", nome="Carlos Alberto", cpf=CPF_C)
    inserir_ocorrencia(engine, re="16026", nome="Reginaldo Dias", cpf="390.533.447-05")
    r1 = _criar(engine, re="16023", nome="Carlos Alberto", cpf=CPF_C)
    r2 = _criar(engine, re="16026", nome="Reginaldo Dias", cpf=CPF_C)
    assert r1.situacao == r2.situacao == "PENDENTE"
    assert "CPF aparece em outra ocorrência com o RE 16026" in r1.motivos
    assert "CPF aparece em outra ocorrência com o RE 16023" in r2.motivos
    assert login_de(engine, "16023") is None and login_de(engine, "16026") is None


def test_ocorrencia_excluida_nao_conta_como_repeticao(engine):
    inserir_ocorrencia(engine, re="16099", cpf=CPF_C, excluida=True)
    r = _criar(engine, re="16023", nome="Carlos Alberto", cpf=CPF_C)
    assert r.situacao == "CRIADO", r.motivos


@pytest.mark.parametrize("tabela, rotulo", [
    ("coordenadoria.pre_ocorrencia", "pré-ocorrência"),
    ("portaria.recolhida_anormal", "Portaria"),
    ("portaria.avaria_saida", "Portaria"),
    ("portaria.avaria_constatacao", "Portaria"),
])
def test_nome_divergente_em_outra_fonte_nao_cria(engine, tabela, rotulo):
    inserir_fonte(engine, tabela, re="32001", nome="Reginaldo Souza")
    r = _criar(engine, re="32001", nome="Carlos Souza")
    assert r.situacao == "PENDENTE"
    assert f"Nome diferente na {rotulo}: 'Reginaldo' x 'Carlos'" in r.motivos
    assert login_de(engine, "32001") is None


def test_nome_igual_sem_acento_e_caixa_passa(engine):
    inserir_fonte(engine, "portaria.recolhida_anormal", re="32002", nome="JOSÉ R")
    r = _criar(engine, re="32002", nome="Jose Ricardo Alves")
    assert r.situacao == "CRIADO", r.motivos


def test_re_sem_outra_fonte_cria(engine):
    inserir_fonte(engine, "portaria.recolhida_anormal", re="99999", nome="Outro Nome")
    r = _criar(engine, re="32003", nome="Carlos Souza")
    assert r.situacao == "CRIADO", r.motivos


def test_pre_ocorrencia_com_cpf_diferente_nao_cria(engine):
    inserir_fonte(engine, "coordenadoria.pre_ocorrencia", re="32004", nome="Carlos Souza", cpf=CPF_B)
    r = _criar(engine, re="32004", nome="Carlos Souza", cpf=CPF_A)
    assert r.situacao == "PENDENTE"
    assert any(m.startswith("CPF diferente na pré-ocorrência") for m in r.motivos)
    assert login_de(engine, "32004") is None


def test_pre_ocorrencia_com_mesmo_cpf_mascarado_passa(engine):
    inserir_fonte(engine, "coordenadoria.pre_ocorrencia", re="32005", nome="Carlos", cpf="529.982.247-25")
    r = _criar(engine, re="32005", nome="Carlos Souza", cpf=CPF_A)
    assert r.situacao == "CRIADO", r.motivos


def test_funcionario_desligado_nao_ganha_login(engine):
    with Session(engine) as db:
        db.add(Funcionario(id=uuid4(), re="32006", nome="Carlos Souza", cpf=CPF_A, status="DESLIGADO"))
        db.commit()
    r = _criar(engine, re="32006", nome="Carlos Souza", cpf=CPF_A)
    assert r.situacao == "PENDENTE"
    assert login_de(engine, "32006") is None


# ─── Pendência quando o RE já está no cadastro legado `motorista` ────────────

def test_pendencia_de_motorista_legado_ainda_aparece_no_pre_cadastro(engine):
    """registrar_pessoa_vista não cria pré-cadastro para RE que já está em
    `motorista` (legado) — o serviço cria direto, senão a pendência da
    maioria dos motoristas sumiria."""
    with Session(engine) as db:
        db.add(Motorista(id=uuid4(), re="33001", nome="Legado Teste"))
        db.commit()
    r = _criar(engine, re="33001", nome="Legado Teste", cpf="52998224724")
    assert r.situacao == "PENDENTE"
    pre = pre_cadastro_de(engine, "33001")
    assert pre is not None and "dígito verificador" in pre.pendencia


def test_motorista_legado_valido_ganha_acesso(engine):
    with Session(engine) as db:
        db.add(Motorista(id=uuid4(), re="33002", nome="Legado Bom"))
        db.commit()
    r = _criar(engine, re="33002", nome="Legado Bom")
    assert r.situacao == "CRIADO", r.motivos


# ─── ⛔ CPF completo nunca aparece ────────────────────────────────────────────

def test_cpf_completo_nunca_aparece_em_motivo_nem_log(engine, caplog):
    inserir_fonte(engine, "coordenadoria.pre_ocorrencia", re="34001", nome="Carlos", cpf=CPF_B)
    with Session(engine) as db:
        db.add(Funcionario(id=uuid4(), re="15652", nome="Outra", cpf=CPF_A, status="ATIVO"))
        db.commit()
    caplog.set_level("DEBUG")
    r = _criar(engine, re="34001", nome="Carlos", cpf=CPF_A)
    assert r.situacao == "PENDENTE"
    texto = "\n".join(r.motivos) + caplog.text + (pre_cadastro_de(engine, "34001").pendencia or "")
    assert CPF_A not in texto and CPF_B not in texto
    assert "529.982.247-25" not in texto
