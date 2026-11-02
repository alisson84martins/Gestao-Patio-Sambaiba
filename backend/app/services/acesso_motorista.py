"""Acesso ao sistema para motorista (e cobrador) a partir da operação.

"esses motoristas devem ganhar login no sistema: RE + 4 últimos dígitos do
CPF" — Alisson, 02/11/2026 (_handoff-claude/PROMPT-acesso-motoristas-2026-11-02.md).

Uma peça só, usada por três caminhos:
  • automático, ao FINALIZAR a ocorrência (routers/ocorrencias.py, chave
    AUTO_ACESSO_MOTORISTA_OCORRENCIA no .env);
  • lote retroativo (scripts/criar_acessos_ocorrencias.py);
  • Promover do pré-cadastro com "já criar acesso" (routers/pre_cadastro.py).

D5 — conferências antes de criar QUALQUER acesso (conferir_identidade):
  1. CPF com 11 dígitos, dígito verificador válido, não todos iguais.
  2. CPF não é de outro funcionário (funcionario.cpf com outro RE).
  3. CPF não aparece em outra ocorrência com outro condutor_re.
  4. Segunda fonte: onde o mesmo RE aparece digitado com nome
     (pré-ocorrência, recolhida anormal, avaria na saída, constatação de
     avaria), o primeiro nome tem que bater; na pré-ocorrência, se houver
     CPF, tem que ser igual. RE sem outra fonte passa.
  Falhou qualquer uma → não cria nada de acesso; o motivo vai para
  pessoa_pre_cadastro.pendencia.

O login nasce PROVISORIA: no 1º acesso a pessoa confirma "Você é NOME, RE
X?" e troca a senha (routers/auth.py, /auth/confirmar-identidade).

⛔ CPF completo NUNCA vai para log, print, exceção ou motivo — só mascarado
(mascarar_cpf).
⛔ Nada aqui commita: quem chama decide (savepoint no automático, rollback
na simulação do lote, tudo-ou-nada no Promover). Por isso o log
ACESSO_CRIADO é gravado direto na sessão, não por registrar_log_acesso()
(que commita na hora e quebraria a simulação e o savepoint).
"""
import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal, Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.registro import normalizar_re
from app.core.security import hash_password
from app.models.auditoria import LogAcesso
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao, UsuarioLogin
from app.models.ocorrencia import Ocorrencia
from app.models.portaria import AvariaConstatacao, AvariaSaida, RecolhidaAnormal
from app.models.pre_cadastro import PessoaPreCadastro
from app.models.pre_ocorrencia import PreOcorrencia
from app.services.espelho_usuario import criar_ou_atualizar_espelho_usuario
from app.services.portaria import ligar_veiculos_por_re
from app.services.pre_cadastro import RETENCAO_PADRAO, registrar_pessoa_vista

logger = logging.getLogger(__name__)

Situacao = Literal["CRIADO", "JA_TEM_LOGIN", "PENDENTE"]


@dataclass
class Resultado:
    situacao: Situacao
    re: Optional[str]
    nome: Optional[str] = None
    motivos: list[str] = field(default_factory=list)
    funcionario_id: Optional[UUID] = None


# ─── CPF ──────────────────────────────────────────────────────────────────────

def so_digitos(cpf: Optional[str]) -> str:
    return "".join(c for c in (cpf or "") if c.isdigit())


def validar_cpf(cpf: Optional[str]) -> bool:
    """11 dígitos + os dois dígitos verificadores. 11 dígitos iguais
    (000.000.000-00, 111…) passam na conta mas não são CPF — recusados."""
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(d[i]) * (tamanho + 1 - i) for i in range(tamanho))
        dv = (soma * 10) % 11 % 10
        if dv != int(d[tamanho]):
            return False
    return True


def mascarar_cpf(cpf: Optional[str]) -> str:
    """'52998224725' → '***.***.*47-25'. Só os 4 últimos aparecem — os
    mesmos que já são a senha inicial, então não revelam nada novo a quem
    administra o acesso."""
    d = so_digitos(cpf)
    if len(d) != 11:
        return "***"
    return f"***.***.*{d[7:9]}-{d[9:]}"


def _primeiro_nome(nome: Optional[str]) -> str:
    partes = (nome or "").split()
    if not partes:
        return ""
    sem_acento = unicodedata.normalize("NFKD", partes[0])
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).lower()


def _cpf_sem_mascara(coluna):
    """Mesmo CPF gravado com ou sem pontuação (as colunas são VARCHAR(14)).
    replace() existe no Postgres e no SQLite dos testes."""
    return func.replace(func.replace(func.replace(coluna, ".", ""), "-", ""), " ", "")


# ─── Conferências (D5) ───────────────────────────────────────────────────────

# Segunda fonte: (rótulo no motivo, modelo, tem coluna de CPF?)
_FONTES_NOME = (
    ("pré-ocorrência", PreOcorrencia, True),
    ("Portaria", RecolhidaAnormal, False),
    ("Portaria", AvariaSaida, False),
    ("Portaria", AvariaConstatacao, False),
)


def conferir_identidade(
    db: Session, *, re: Optional[str], nome: Optional[str], cpf: Optional[str]
) -> list[str]:
    """Roda as conferências de D5. Lista vazia = passou. Cada motivo é uma
    frase em português pronta para a tela e para a pendência do
    pré-cadastro — ⛔ nunca com CPF completo."""
    re_norm = normalizar_re(re)
    problemas: list[str] = []
    if not re_norm:
        return ["Sem RE"]
    if not (nome or "").strip():
        problemas.append("Sem nome")

    digitos = so_digitos(cpf)
    if not digitos:
        problemas.append("Sem CPF")
    elif not validar_cpf(digitos):
        problemas.append("CPF com dígito verificador inválido")
    else:
        # 2. CPF de outro funcionário.
        outro_func = db.execute(
            select(Funcionario.re).where(
                _cpf_sem_mascara(Funcionario.cpf) == digitos, Funcionario.re != re_norm,
            )
        ).scalars().first()
        if outro_func is not None:
            problemas.append(f"CPF já é do funcionário RE {outro_func}")

        # 3. CPF em outra ocorrência com outro condutor.
        res_outras = db.execute(
            select(Ocorrencia.condutor_re).where(
                _cpf_sem_mascara(Ocorrencia.condutor_cpf) == digitos,
                Ocorrencia.excluida_em.is_(None),
                Ocorrencia.condutor_re.is_not(None),
            ).distinct()
        ).scalars().all()
        for outro_re in sorted({normalizar_re(r) for r in res_outras} - {re_norm, None}):
            problemas.append(f"CPF aparece em outra ocorrência com o RE {outro_re}")

    # 4. Segunda fonte — nome (e CPF, na pré-ocorrência).
    primeiro = _primeiro_nome(nome)
    for rotulo, modelo, tem_cpf in _FONTES_NOME:
        colunas = [modelo.motorista_nome] + ([modelo.motorista_cpf] if tem_cpf else [])
        linhas = db.execute(
            select(*colunas).where(modelo.motorista_re == re_norm)
        ).all()
        for linha in linhas:
            nome_fonte = linha[0]
            if primeiro and nome_fonte and _primeiro_nome(nome_fonte) != primeiro:
                motivo = (
                    f"Nome diferente na {rotulo}: "
                    f"'{nome_fonte.split()[0].title()}' x '{nome.split()[0].title()}'"
                )
                if motivo not in problemas:
                    problemas.append(motivo)
            if tem_cpf:
                cpf_fonte = so_digitos(linha[1])
                if cpf_fonte and digitos and cpf_fonte != digitos:
                    motivo = f"CPF diferente na {rotulo} ({mascarar_cpf(cpf_fonte)})"
                    if motivo not in problemas:
                        problemas.append(motivo)
    return problemas


# ─── Criação ──────────────────────────────────────────────────────────────────

def criar_acesso(
    db: Session,
    *,
    re: Optional[str],
    nome: Optional[str],
    cpf: Optional[str],
    funcao_codigo: Literal["MOTORISTA", "COBRADOR"],
    origem: str,
    criado_por: Optional[UUID],
) -> Resultado:
    """Cria (se passar nas conferências) funcionário + função + login
    PROVISORIA + espelho em `usuario` + log ACESSO_CRIADO, e promove o
    pré-cadastro PENDENTE do mesmo RE. Não commita.

    Situações:
      JA_TEM_LOGIN — já existe usuario_login (ativo ou não): nada muda.
      PENDENTE     — alguma conferência falhou: nada de acesso; motivos
                     gravados em pessoa_pre_cadastro.pendencia.
      CRIADO       — login criado.
    """
    re_norm = normalizar_re(re)
    nome = (nome or "").strip() or None
    digitos = so_digitos(cpf)
    resultado = Resultado(situacao="PENDENTE", re=re_norm, nome=nome)
    if not re_norm:
        resultado.motivos = ["Sem RE"]
        return resultado

    funcionario = db.execute(
        select(Funcionario).where(Funcionario.re == re_norm)
    ).scalar_one_or_none()
    if funcionario is not None:
        resultado.funcionario_id = funcionario.id
        tem_login = db.execute(
            select(UsuarioLogin.id).where(UsuarioLogin.funcionario_id == funcionario.id)
        ).first()
        if tem_login is not None:
            resultado.situacao = "JA_TEM_LOGIN"
            resultado.nome = funcionario.nome
            return resultado

    motivos = conferir_identidade(db, re=re_norm, nome=nome, cpf=digitos)
    if funcionario is not None and funcionario.cpf and so_digitos(funcionario.cpf) != digitos:
        motivos.append(
            f"Funcionário RE {re_norm} já cadastrado com outro CPF ({mascarar_cpf(funcionario.cpf)})"
        )
    if funcionario is not None and funcionario.status == "DESLIGADO":
        motivos.append(f"Funcionário RE {re_norm} está DESLIGADO")
    if motivos:
        resultado.motivos = motivos
        _gravar_pendencia(db, re=re_norm, nome=nome, cpf=digitos,
                          papel=funcao_codigo, origem=origem, motivos=motivos)
        logger.info("Acesso não criado para RE %s: %s", re_norm, "; ".join(motivos))
        return resultado

    agora = datetime.now(timezone.utc)
    if funcionario is None:
        funcionario = Funcionario(re=re_norm, nome=nome, cpf=digitos, criado_por=criado_por)
        db.add(funcionario)
        db.flush()
    elif not funcionario.cpf:
        # Funcionário sem CPF: o da ocorrência já passou nas conferências
        # (inclusive "não é de outro funcionário") — vira o CPF do cadastro,
        # senão a senha (4 últimos) não teria de onde sair numa troca futura.
        funcionario.cpf = digitos
        funcionario.atualizado_em = agora
        funcionario.atualizado_por = criado_por
    resultado.funcionario_id = funcionario.id
    resultado.nome = funcionario.nome

    _atribuir_funcao(db, funcionario, funcao_codigo, criado_por)

    senha_hash = hash_password(digitos[-4:])
    db.add(UsuarioLogin(
        funcionario_id=funcionario.id,
        senha_hash=senha_hash,
        politica_senha="PROVISORIA",
        criado_por=criado_por,
    ))
    criar_ou_atualizar_espelho_usuario(db, funcionario, senha_hash)

    pre = db.execute(
        select(PessoaPreCadastro).where(
            PessoaPreCadastro.re == re_norm, PessoaPreCadastro.status == "PENDENTE"
        )
    ).scalar_one_or_none()
    if pre is not None:
        pre.status = "PROMOVIDO"
        pre.funcionario_id = funcionario.id
        pre.promovido_por = criado_por
        pre.promovido_em = agora
        pre.pendencia = None
        ligar_veiculos_por_re(db, re=re_norm, funcionario_id=funcionario.id, usuario_id=criado_por)

    db.add(LogAcesso(evento="ACESSO_CRIADO", funcionario_id=funcionario.id,
                     re_tentativa=re_norm, recurso=origem[:50]))
    db.flush()
    resultado.situacao = "CRIADO"
    logger.info("Acesso criado para RE %s (origem %s)", re_norm, origem)
    return resultado


def _atribuir_funcao(
    db: Session, funcionario: Funcionario, funcao_codigo: str, criado_por: Optional[UUID]
) -> None:
    """Mesma sequência de atribuir_funcao() (routers/funcionarios.py):
    vínculo + fn_ajustar_funcao_principal. Já tem a função ativa → nada."""
    funcao = db.execute(select(Funcao).where(Funcao.codigo == funcao_codigo)).scalar_one()
    ja_tem = db.execute(
        select(FuncionarioFuncao.id).where(
            FuncionarioFuncao.funcionario_id == funcionario.id,
            FuncionarioFuncao.funcao_id == funcao.id,
            FuncionarioFuncao.ativo.is_(True),
        )
    ).first()
    if ja_tem is not None:
        return
    tem_outra_ativa = db.execute(
        select(FuncionarioFuncao.id).where(
            FuncionarioFuncao.funcionario_id == funcionario.id,
            FuncionarioFuncao.ativo.is_(True),
        )
    ).first()
    db.add(FuncionarioFuncao(
        funcionario_id=funcionario.id,
        funcao_id=funcao.id,
        principal=tem_outra_ativa is None,
        data_inicio=date.today(),
        criado_por=criado_por,
    ))
    db.flush()
    db.execute(text("SELECT fn_ajustar_funcao_principal(:fid)"), {"fid": funcionario.id})


def _gravar_pendencia(
    db: Session, *, re: str, nome: Optional[str], cpf: str, papel: str,
    origem: str, motivos: list[str],
) -> None:
    """Grava os motivos em pessoa_pre_cadastro.pendencia.

    registrar_pessoa_vista() não cria pré-cadastro para RE que já existe em
    `motorista` (legado) — e quase todo motorista de ônibus está lá. Sem o
    caminho direto abaixo, a pendência dele sumiria. Já existindo em
    `funcionario`, o pré-cadastro não nasce (Promover daria 409): a
    pendência fica só no retorno/log de quem chamou.
    """
    pre = _pre_cadastro_pendente(db, re)
    if pre is None:
        registrar_pessoa_vista(db, re=re, papel=papel, origem=origem, nome=nome, cpf=cpf or None)
        pre = _pre_cadastro_pendente(db, re)
    if pre is None:
        existe_funcionario = db.execute(
            select(Funcionario.id).where(Funcionario.re == re)
        ).first()
        if existe_funcionario is not None:
            return
        agora = datetime.now(timezone.utc)
        pre = PessoaPreCadastro(
            re=re, nome=nome, cpf=cpf or None, papel_sugerido=papel,
            vezes_visto=1, primeira_vez_em=agora, ultima_vez_em=agora,
            ultima_origem=origem[:30], status="PENDENTE",
            retencao_expira_em=agora + RETENCAO_PADRAO,
        )
        db.add(pre)
    pre.pendencia = "\n".join(motivos)
    db.flush()


def _pre_cadastro_pendente(db: Session, re: str) -> Optional[PessoaPreCadastro]:
    return db.execute(
        select(PessoaPreCadastro).where(
            PessoaPreCadastro.re == re, PessoaPreCadastro.status == "PENDENTE"
        )
    ).scalar_one_or_none()
