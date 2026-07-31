"""Testes de app.routers.alocacoes._renumerar_fila.

Usa um engine SQLite em memória com o MESMO índice único parcial que
existe em produção (uq_alocacao_fila_posicao_ativa, migration 005) — é a
colisão contra esse índice que _renumerar_fila precisa evitar durante o
deslocamento, então o teste só prova algo de verdade se o índice estiver
mesmo lá (sem ele, qualquer implementação ingênua passaria).
"""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.enums import TipoFilaEnum
from app.models.frota import AlocacaoPatio, Fila
from app.routers.alocacoes import _renumerar_fila


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    # Só as duas tabelas necessárias — Onibus tem coluna GERADA com sintaxe
    # específica do Postgres (CASE ... ::setor_enum) que o SQLite não entende,
    # e não é preciso pra testar a renumeração.
    Base.metadata.create_all(engine, tables=[Fila.__table__, AlocacaoPatio.__table__])
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_alocacao_fila_posicao_ativa "
            "ON alocacao_patio (fila_id, posicao) WHERE ativa = 1"
        )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


def _criar_fila(db: Session) -> Fila:
    fila = Fila(tipo=TipoFilaEnum.NUMERICA, numero=1, nome="Fila 1")
    db.add(fila)
    db.flush()
    return fila


def _criar_alocacao(db: Session, fila_id, posicao: int, ativa: bool = True) -> AlocacaoPatio:
    aloc = AlocacaoPatio(onibus_id=uuid4(), fila_id=fila_id, posicao=posicao, ativa=ativa)
    db.add(aloc)
    db.flush()
    return aloc


def _ativas_ordenadas(db: Session, fila_id) -> list[AlocacaoPatio]:
    return db.execute(
        select(AlocacaoPatio)
        .where(AlocacaoPatio.fila_id == fila_id, AlocacaoPatio.ativa.is_(True))
        .order_by(AlocacaoPatio.posicao)
    ).scalars().all()


def test_renumera_sem_buracos_preservando_ordem(db):
    fila = _criar_fila(db)
    a1 = _criar_alocacao(db, fila.id, 1)
    a3 = _criar_alocacao(db, fila.id, 3)
    a7 = _criar_alocacao(db, fila.id, 7)
    db.commit()

    _renumerar_fila(db, fila.id)
    db.commit()

    ativas = _ativas_ordenadas(db, fila.id)
    assert [a.posicao for a in ativas] == [1, 2, 3]
    assert [a.id for a in ativas] == [a1.id, a3.id, a7.id]


def test_renumera_ignora_inativas(db):
    fila = _criar_fila(db)
    _criar_alocacao(db, fila.id, 1, ativa=False)
    a2 = _criar_alocacao(db, fila.id, 2)
    db.commit()

    _renumerar_fila(db, fila.id)
    db.commit()

    ativas = _ativas_ordenadas(db, fila.id)
    assert [a.posicao for a in ativas] == [1]
    assert ativas[0].id == a2.id


def test_fila_vazia_nao_lanca_erro(db):
    fila = _criar_fila(db)
    _renumerar_fila(db, fila.id)  # não deve lançar
    db.commit()
    assert _ativas_ordenadas(db, fila.id) == []


def test_ja_renumerada_e_idempotente(db):
    fila = _criar_fila(db)
    a1 = _criar_alocacao(db, fila.id, 1)
    a2 = _criar_alocacao(db, fila.id, 2)
    db.commit()

    _renumerar_fila(db, fila.id)
    _renumerar_fila(db, fila.id)
    db.commit()

    ativas = _ativas_ordenadas(db, fila.id)
    assert [a.posicao for a in ativas] == [1, 2]
    assert [a.id for a in ativas] == [a1.id, a2.id]


def test_sentido_volta_preserva_carro_novo_na_posicao_1_e_ordem_relativa(db):
    """Teste obrigatório do PROMPT-CORRECOES-PATIO.md, seção 1.

    1) Fila com carros nas posições 1, 3 e 7 (com buracos, como o bug real).
    2) Simula uma marcação no sentido VOLTA: empurra tudo +1 (da maior pra
       menor posição, igual a alocar_bloco faz) e insere o carro novo na
       posição 1.
    3) Chama _renumerar_fila.
    4) O carro novo tem que ficar na posição 1, e os três antigos têm que
       manter a ordem relativa entre si, agora em 2, 3, 4 sem lacuna.

    Se algum dia alguém mexer em _renumerar_fila e trocar o critério de
    ordenação (por número do carro, por id, por data), este teste falha.
    """
    fila = _criar_fila(db)
    antigo_1 = _criar_alocacao(db, fila.id, 1)
    antigo_3 = _criar_alocacao(db, fila.id, 3)
    antigo_7 = _criar_alocacao(db, fila.id, 7)
    db.commit()

    existentes = db.execute(
        select(AlocacaoPatio)
        .where(AlocacaoPatio.fila_id == fila.id, AlocacaoPatio.ativa.is_(True))
        .order_by(AlocacaoPatio.posicao.desc())
    ).scalars().all()
    for aloc in existentes:
        aloc.posicao += 1
        db.flush()
    carro_novo = _criar_alocacao(db, fila.id, 1)
    db.commit()

    _renumerar_fila(db, fila.id)
    db.commit()

    ativas = _ativas_ordenadas(db, fila.id)
    assert [a.posicao for a in ativas] == [1, 2, 3, 4]
    assert ativas[0].id == carro_novo.id
    assert [a.id for a in ativas[1:]] == [antigo_1.id, antigo_3.id, antigo_7.id]
