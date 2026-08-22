"""registrar_pessoa_vista — alimenta o pré-cadastro de pessoas (Bloco H) a
partir de eventos operacionais.

"a cada cadastro vai entrando em uma tabela que existe no banco (...) mas
em uma ocorrência pegamos dados valiosos que podem virar o cadastro do
motorista (...) para acesso ao sistema apenas com a sua devida permissão,
o mesmo vale para o cobrador." — Alisson, 21/08/2026.

As cinco regras (§5.2 do prompt):
1. RE já existe em funcionario OU motorista -> não cria nada (encerra em
   silêncio). Usa o resolvedor de identidade (§5.3) que já checa as duas
   tabelas — nunca olhar só uma, ou todo motorista digitado no portão
   viraria pré-cadastro mesmo já sendo cadastrado.
2. Já existe pré-cadastro PENDENTE com esse RE -> enriquece (só campos
   vazios, nunca sobrescreve o que já estava preenchido).
3. Não existe -> cria com o que vier.
4. Falha nunca propaga — SAVEPOINT (begin_nested), mesmo padrão de
   services/manutencao_recolhida.py. Um problema aqui jamais pode impedir
   o registro do evento que chamou (recolhida, pré-ocorrência).
5. Criar pré-cadastro NÃO cria acesso ao sistema — nenhuma linha em
   usuario_login, nenhuma função, nenhuma permissão.

⛔ Terceiros de pré-ocorrência (terceiro_nome/placa/telefone/seguradora)
JAMAIS chamam esta função — são pessoas de fora da empresa, capturadas
num acidente. Só motorista e cobrador da Sambaíba (quem chama decide
isso, este serviço não filtra papel nenhum além de MOTORISTA/COBRADOR).
"""
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pre_cadastro import PessoaPreCadastro
from app.schemas.portaria import normalizar_re
from app.services.identidade import resolver_por_re

# LGPD (§5.2 do prompt): prazo de retenção sugerido pra um pré-cadastro
# PENDENTE sem promoção. ⛔ Sem job de expurgo ainda — só o prazo gravado.
RETENCAO_PADRAO = timedelta(days=365)


def registrar_pessoa_vista(
    db: Session,
    *,
    re: Optional[str],
    papel: Literal["MOTORISTA", "COBRADOR"],
    origem: str,
    nome: Optional[str] = None,
    cpf: Optional[str] = None,
    rg: Optional[str] = None,
    cnh: Optional[str] = None,
    telefone: Optional[str] = None,
) -> None:
    """Nunca propaga exceção — regra número um do módulo que chama vale
    também aqui: um pré-cadastro que não pôde nascer não pode derrubar o
    registro da recolhida ou da pré-ocorrência."""
    re_norm = normalizar_re(re)
    if not re_norm:
        return
    try:
        with db.begin_nested():
            _registrar(db, re_norm=re_norm, papel=papel, origem=origem,
                       nome=nome, cpf=cpf, rg=rg, cnh=cnh, telefone=telefone)
    except Exception:  # noqa: BLE001 — regra número um: nunca propaga
        pass


def _registrar(
    db: Session, *, re_norm: str, papel: str, origem: str,
    nome: Optional[str], cpf: Optional[str], rg: Optional[str],
    cnh: Optional[str], telefone: Optional[str],
) -> None:
    # 1. RE já cadastrado (funcionario OU motorista, via §5.3) -> encerra.
    if resolver_por_re(db, re_norm) is not None:
        return

    existente = db.execute(
        select(PessoaPreCadastro).where(
            PessoaPreCadastro.re == re_norm, PessoaPreCadastro.status == "PENDENTE"
        )
    ).scalar_one_or_none()

    agora = datetime.now(timezone.utc)

    if existente is not None:
        # 2. Enriquece — só campos vazios, ⛔ nunca sobrescreve. A fonte
        # pobre (portaria, só RE) pode chegar depois da rica
        # (pré-ocorrência); enriquecer é sempre seguro, sobrescrever pode
        # piorar o dado.
        existente.nome = existente.nome or nome
        existente.cpf = existente.cpf or cpf
        existente.rg = existente.rg or rg
        existente.cnh = existente.cnh or cnh
        existente.telefone = existente.telefone or telefone
        if existente.papel_sugerido == "INDEFINIDO" and papel:
            existente.papel_sugerido = papel
        existente.vezes_visto += 1
        existente.ultima_vez_em = agora
        existente.ultima_origem = origem
        db.flush()
        return

    # 3. Não existe -> cria com o que vier.
    novo = PessoaPreCadastro(
        re=re_norm, nome=nome, cpf=cpf, rg=rg, cnh=cnh, telefone=telefone,
        papel_sugerido=papel or "INDEFINIDO",
        vezes_visto=1,
        primeira_vez_em=agora, ultima_vez_em=agora, ultima_origem=origem,
        status="PENDENTE",
        retencao_expira_em=agora + RETENCAO_PADRAO,
    )
    db.add(novo)
    db.flush()
