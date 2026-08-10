"""Modelos do cadastro central: funcionario, funcao, vinculos, login e RBAC."""
from datetime import date, datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Modulo(Base):
    __tablename__ = "modulo"

    codigo: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str] = mapped_column(String(40), nullable=False)
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=99)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Recurso(Base):
    __tablename__ = "recurso"

    codigo: Mapped[str] = mapped_column(String(50), primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    modulo_codigo: Mapped[Optional[str]] = mapped_column(
        String(20), ForeignKey("modulo.codigo"), nullable=True
    )
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=99)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Funcao(Base):
    __tablename__ = "funcao"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    categoria: Mapped[str] = mapped_column(String(20), nullable=False)
    nivel: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    permissoes: Mapped[list["FuncaoPermissao"]] = relationship(
        "FuncaoPermissao", back_populates="funcao", cascade="all, delete-orphan"
    )


class Funcionario(Base):
    __tablename__ = "funcionario"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    re: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    # Armazenado como 11 dígitos (só números). CPF único na tabela.
    cpf: Mapped[Optional[str]] = mapped_column(String(14), unique=True, nullable=True)
    telefone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # RG sem máscara/formato — varia por estado, alguns terminam em letra.
    rg: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cnh: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="ATIVO")
    data_admissao: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    codigo_externo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criado_por: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    atualizado_por: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    vinculos: Mapped[list["FuncionarioFuncao"]] = relationship(
        "FuncionarioFuncao", back_populates="funcionario", cascade="all, delete-orphan"
    )
    login: Mapped[Optional["UsuarioLogin"]] = relationship(
        "UsuarioLogin", back_populates="funcionario", uselist=False
    )


class FuncionarioFuncao(Base):
    __tablename__ = "funcionario_funcao"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("funcionario.id", ondelete="CASCADE"),
        nullable=False,
    )
    funcao_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("funcao.id", ondelete="RESTRICT"),
        nullable=False,
    )
    principal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_inicio: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    data_fim: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criado_por: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    funcionario: Mapped["Funcionario"] = relationship("Funcionario", back_populates="vinculos")
    funcao: Mapped["Funcao"] = relationship("Funcao")


class UsuarioLogin(Base):
    __tablename__ = "usuario_login"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("funcionario.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    politica_senha: Mapped[str] = mapped_column(String(10), nullable=False, default="CPF")
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ultimo_acesso: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criado_por: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    atualizado_por: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    funcionario: Mapped["Funcionario"] = relationship("Funcionario", back_populates="login")


class FuncaoPermissao(Base):
    __tablename__ = "funcao_permissao"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    funcao_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("funcao.id", ondelete="CASCADE"),
        nullable=False,
    )
    recurso: Mapped[str] = mapped_column(String(50), nullable=False)
    pode_ler: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pode_escrever: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    funcao: Mapped["Funcao"] = relationship("Funcao", back_populates="permissoes")
