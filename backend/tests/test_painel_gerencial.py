"""Painel Gerencial (migration 046) — só leitura sobre vw_painel_evento.

Sem PostgreSQL, como o resto da suíte: o banco é um falso que só responde
ao que exige() e get_current_funcionario() perguntam (Funcionario +
vw_acesso_efetivo), e as consultas do painel (_consultar_*) são trocadas por
monkeypatch — o SQL delas foi conferido contra o banco, não aqui.

O que se prova:
  1. A trava: 403 para quem não tem `painel_gerencial` — inclusive o
     COORDENADOR_TRAFEGO, que tem `relatorios` (por isso o recurso é novo);
     200 para quem tem, nas três rotas.
  2. Dia do relógio em São Paulo: 25/09 = [03:00Z do dia 25, 03:00Z do 26);
     23h30 de SP (02:30Z do dia seguinte) ENTRA no dia 25.
  3. Período: de > ate → 422; mais de 400 dias → 422; ate no futuro trava
     em hoje; "inicio" usa o primeiro registro.
  4. CSV: BOM + ';' + data/hora em SP; retirada sem autor sai como
     "Limpeza geral do pátio".

⛔ REs e nomes fictícios (repositório público).
"""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.cadastro import Funcionario
from app.routers import painel_gerencial as pg

_ROTAS = ("/painel-gerencial/eventos", "/painel-gerencial/resumo", "/painel-gerencial/exportar.csv")


class _DBFalso:
    """Responde só ao que a autenticação e o RBAC perguntam. `recursos` é o
    acesso efetivo da pessoa (o que vw_acesso_efetivo devolveria)."""

    def __init__(self, funcionario, recursos):
        self._funcionario = funcionario
        self._recursos = set(recursos)

    def get(self, model, id_):
        if model is Funcionario and id_ == self._funcionario.id:
            return self._funcionario
        return None

    def add(self, *_a, **_k):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, stmt, params=None, *a, **k):
        recursos = self._recursos

        class _Linha:
            pode_ler = True
            pode_escrever = False

        class _R:
            def scalar_one_or_none(self):
                return None  # sem UsuarioLogin — não bloqueia get_current_funcionario

            def fetchone(self):
                if "vw_acesso_efetivo" in str(stmt) and params and params.get("rec") in recursos:
                    return _Linha()
                return None
        return _R()


_EVENTO = {
    "id": uuid4(),
    "momento": datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc),  # 23h30 de 25/09 em SP
    "modulo": "PATIO", "tipo": "RETIRADA", "categoria": "Fila 3", "identificacao": "2140",
    "detalhe": "Saiu de Fila 3 · pos 1", "pessoa_re": None, "pessoa_nome": None,
    "autor_re": None, "autor_nome": None,
}


@pytest.fixture
def painel_sem_banco(monkeypatch):
    """Troca as consultas SQL do painel por respostas fixas."""
    monkeypatch.setattr(pg, "_consultar_eventos", lambda *a, **k: [dict(_EVENTO)])
    monkeypatch.setattr(pg, "_consultar_contagens", lambda *a, **k: {
        "ENTRADA": 12, "SAIDA": 10, "RECOLHIDA": 3, "MOVIMENTACAO": 40,
    })
    monkeypatch.setattr(pg, "_primeiro_registro", lambda db: {
        "portaria": datetime(2026, 8, 22, 20, 49, tzinfo=timezone.utc),
        "patio": datetime(2026, 6, 13, 15, 35, tzinfo=timezone.utc),
    })
    monkeypatch.setattr(pg, "_contar_dentro_agora", lambda db: 7)


def _chamar(rota, recursos, params=None):
    func = Funcionario(id=uuid4(), re="70001", nome="Pessoa Teste")
    token = create_access_token(subject=func.id)
    app.dependency_overrides[get_db] = lambda: _DBFalso(func, recursos)
    try:
        return TestClient(app).get(rota, params=params, headers={"Authorization": f"Bearer {token}"})
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─── 1 · A trava ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rota", _ROTAS)
def test_coordenador_de_trafego_nao_ve_mesmo_tendo_relatorios(rota, painel_sem_banco):
    """🔴 O motivo do recurso ser novo: `relatorios` não abre o painel."""
    resp = _chamar(rota, {"relatorios", "ocorrencia", "coordenacao"})
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("rota", _ROTAS)
def test_operador_de_patio_nao_ve(rota, painel_sem_banco):
    resp = _chamar(rota, {"alocacao", "escala", "alerta"})
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("rota", _ROTAS)
def test_quem_tem_painel_gerencial_ve(rota, painel_sem_banco):
    resp = _chamar(rota, {"painel_gerencial"})
    assert resp.status_code == 200, resp.text


def test_sem_token_da_401():
    assert TestClient(app).get("/painel-gerencial/resumo").status_code == 401


# ─── 2 · Dia do relógio em São Paulo ─────────────────────────────────────────

def test_intervalo_do_dia_25_em_utc():
    inicio, fim = pg.intervalo_utc(date(2026, 9, 25), date(2026, 9, 25))
    assert inicio == datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
    assert fim == datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)


def test_evento_as_23h30_de_sp_entra_no_dia_25():
    inicio, fim = pg.intervalo_utc(date(2026, 9, 25), date(2026, 9, 25))
    momento = datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc)  # 23h30 de 25/09 em SP
    assert inicio <= momento < fim


def test_intervalo_de_varios_dias_fecha_na_meia_noite_do_dia_seguinte():
    inicio, fim = pg.intervalo_utc(date(2026, 9, 1), date(2026, 9, 30))
    assert inicio == datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
    assert fim == datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)


def test_hoje_depois_das_21h_ainda_e_o_mesmo_dia_em_sp():
    """toISOString()/UTC já viraram o dia às 21h de SP; o relógio da garagem não."""
    agora = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)  # 22h de 25/09 em SP
    assert pg.hoje_sp(agora) == date(2026, 9, 25)


# ─── 3 · Período ─────────────────────────────────────────────────────────────

_HOJE = date(2026, 9, 25)


def _sem_primeiro():
    return None


def test_padrao_e_hoje():
    assert pg.resolver_periodo(None, None, _HOJE, _sem_primeiro) == (_HOJE, _HOJE)


def test_de_depois_de_ate_da_422():
    with pytest.raises(HTTPException) as exc:
        pg.resolver_periodo("2026-09-20", "2026-09-10", _HOJE, _sem_primeiro)
    assert exc.value.status_code == 422


def test_periodo_maior_que_400_dias_da_422():
    with pytest.raises(HTTPException) as exc:
        pg.resolver_periodo("2025-08-01", "2026-09-25", _HOJE, _sem_primeiro)
    assert exc.value.status_code == 422


def test_400_dias_exatos_passa():
    de, ate = pg.resolver_periodo("2025-08-22", "2026-09-25", _HOJE, _sem_primeiro)
    assert (ate - de).days + 1 == 400


def test_ate_no_futuro_trava_em_hoje():
    assert pg.resolver_periodo("2026-09-20", "2026-12-31", _HOJE, _sem_primeiro) == (date(2026, 9, 20), _HOJE)


def test_data_invalida_da_422():
    with pytest.raises(HTTPException) as exc:
        pg.resolver_periodo("25/09/2026", None, _HOJE, _sem_primeiro)
    assert exc.value.status_code == 422


def test_inicio_usa_o_primeiro_registro():
    assert pg.resolver_periodo("inicio", None, _HOJE, lambda: date(2026, 6, 13)) == (date(2026, 6, 13), _HOJE)


def test_de_maior_que_ate_pela_api_da_422(painel_sem_banco):
    resp = _chamar("/painel-gerencial/eventos", {"painel_gerencial"}, {"de": "2026-09-20", "ate": "2026-09-10"})
    assert resp.status_code == 422


def test_tipo_desconhecido_da_422(painel_sem_banco):
    resp = _chamar("/painel-gerencial/eventos", {"painel_gerencial"}, {"tipo": "DROP TABLE"})
    assert resp.status_code == 422


def test_antes_chave_com_id_invalido_da_422(painel_sem_banco):
    resp = _chamar("/painel-gerencial/eventos", {"painel_gerencial"},
                   {"antes": "2026-09-25T12:00:00Z", "antes_chave": "ENTRADA:nao-e-uuid"})
    assert resp.status_code == 422


def test_busca_escapa_curinga_do_ilike():
    _where, params = pg._where_eventos(None, None, "50%_", None, None, None)
    assert params["busca"] == "%50\\%\\_%"


# ─── Resumo ──────────────────────────────────────────────────────────────────

def test_resumo_so_devolve_contadores_simples(painel_sem_banco):
    """Visualização, não análise: número do período, sem "anterior", sem %,
    sem ranking nem agregação."""
    corpo = _chamar("/painel-gerencial/resumo", {"painel_gerencial"}).json()
    assert set(corpo) == {"periodo", "contadores", "dentro_agora", "primeiro_registro"}
    assert corpo["contadores"] == {
        "entradas": 12, "saidas": 10, "recolhidas": 3, "avarias": 0,
        "alocacoes": 0, "movimentacoes": 40, "retiradas": 0,
    }
    assert corpo["periodo"]["inclui_hoje"] is True
    assert corpo["dentro_agora"] == 7
    assert corpo["primeiro_registro"]["patio"].startswith("2026-06-13")


def test_resumo_de_periodo_passado_nao_calcula_dentro_agora(painel_sem_banco):
    corpo = _chamar("/painel-gerencial/resumo", {"painel_gerencial"},
                    {"de": "2026-09-01", "ate": "2026-09-02"}).json()
    assert corpo["periodo"]["inclui_hoje"] is False
    assert corpo["dentro_agora"] is None


# ─── 4 · CSV ─────────────────────────────────────────────────────────────────

def test_csv_tem_bom_ponto_e_virgula_e_hora_de_sp(painel_sem_banco):
    resp = _chamar("/painel-gerencial/exportar.csv", {"painel_gerencial"})
    assert resp.status_code == 200
    assert resp.content.startswith(b"\xef\xbb\xbf")
    texto = resp.content.decode("utf-8-sig")
    cabecalho, linha = texto.splitlines()[:2]
    assert cabecalho.startswith("Data e hora;Módulo;Tipo;")
    assert linha.startswith("25/09/2026 23:30;PATIO;RETIRADA;")
    assert linha.endswith(";Limpeza geral do pátio")
    assert "attachment" in resp.headers["content-disposition"]
