"""Lote retroativo: cria o acesso dos condutores que já estão nas ocorrências.

Uso (com venv ativo, da pasta backend/):
    python -m scripts.criar_acessos_ocorrencias              # SIMULAÇÃO (padrão)
    python -m scripts.criar_acessos_ocorrencias --executar   # grava de verdade

(também roda como `python scripts/criar_acessos_ocorrencias.py`, igual a
set_password.py.)

Um condutor por RE distinto entre todas as ocorrências não excluídas —
preferindo a ocorrência com CPF de 11 dígitos e, no empate, a de maior
número. Cada um passa por app/services/acesso_motorista.py (as mesmas
conferências do automático e do Promover).

- Simulação: roda tudo e faz ROLLBACK no fim — nada é gravado.
- Idempotente: quem já tem login sai como JA_TEM_LOGIN. Pode rodar de novo
  depois de corrigir as pendências.
- ⛔ CPF sai sempre mascarado.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

# Adiciona backend/ ao sys.path para imports (mesmo padrão de set_password.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.registro import normalizar_re  # noqa: E402
from app.models.ocorrencia import Ocorrencia  # noqa: E402
from app.services.acesso_motorista import (  # noqa: E402
    Resultado, criar_acesso, mascarar_cpf, so_digitos,
)


def selecionar_condutores(db: Session) -> list[tuple[str, str | None, str | None]]:
    """(re, nome, cpf) — um por RE, na ordem do RE."""
    linhas = db.execute(
        select(Ocorrencia.numero, Ocorrencia.condutor_re, Ocorrencia.condutor_nome, Ocorrencia.condutor_cpf)
        .where(Ocorrencia.condutor_re.is_not(None), Ocorrencia.excluida_em.is_(None))
    ).all()
    melhor: dict[str, tuple] = {}
    for numero, re, nome, cpf in linhas:
        re_norm = normalizar_re(re)
        if not re_norm:
            continue
        chave = (len(so_digitos(cpf)) == 11, numero)
        if re_norm not in melhor or chave > melhor[re_norm][0]:
            melhor[re_norm] = (chave, nome, cpf)
    return [(re, nome, cpf) for re, (_, nome, cpf) in sorted(melhor.items())]


def processar(db: Session, *, executar: bool) -> list[tuple[Resultado, str | None]]:
    """Roda o serviço para cada condutor. Cada um num SAVEPOINT: um erro
    inesperado num RE não derruba o lote. Commit só com executar=True."""
    resultados = []
    for re, nome, cpf in selecionar_condutores(db):
        try:
            with db.begin_nested():
                r = criar_acesso(db, re=re, nome=nome, cpf=cpf, funcao_codigo="MOTORISTA",
                                 origem="LOTE_OCORRENCIAS", criado_por=None)
        except Exception as exc:  # noqa: BLE001 — segue para o próximo RE
            r = Resultado(situacao="PENDENTE", re=re, nome=nome,
                          motivos=[f"Erro inesperado: {type(exc).__name__}"])
        resultados.append((r, cpf))
    if executar:
        db.commit()
    else:
        db.rollback()
    return resultados


def imprimir(resultados: list[tuple[Resultado, str | None]], *, executar: bool) -> None:
    print("MODO:", "EXECUÇÃO (gravado)" if executar else "SIMULAÇÃO (nada foi gravado)")
    print()
    print(f"{'RE':<8} | {'NOME':<32} | {'CPF':<15} | {'SITUAÇÃO':<12} | MOTIVOS")
    print("-" * 110)
    for r, cpf in resultados:
        print(f"{r.re or '':<8} | {(r.nome or '')[:32]:<32} | {mascarar_cpf(cpf):<15} | "
              f"{r.situacao:<12} | {'; '.join(r.motivos)}")
    print()
    contagem = Counter(r.situacao for r, _ in resultados)
    print("RESUMO:", ", ".join(f"{s}={contagem.get(s, 0)}" for s in ("CRIADO", "JA_TEM_LOGIN", "PENDENTE")),
          f"(total {len(resultados)})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--executar", action="store_true",
                        help="grava de verdade (sem isto é só simulação)")
    args = parser.parse_args(argv)

    from app.core.database import SessionLocal
    with SessionLocal() as db:
        resultados = processar(db, executar=args.executar)
    imprimir(resultados, executar=args.executar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
