"""Modelos SQLAlchemy 2.0 + ENUMs Python."""
from app.models.cadastro import (
    Funcao, FuncaoPermissao, Funcionario, FuncionarioFuncao,
    Modulo, Recurso, UsuarioLogin,
)
from app.models.catalogos import Linha, Permissao, TipoDefeito
from app.models.enums import (
    OrigemEscalaEnum,
    PerfilUsuarioEnum,
    SetorEnum,
    StatusFichaEnum,
    StatusImportacaoEnum,
    StatusMotoristaEnum,
    StatusOnibusEnum,
    TipoAlertaEnum,
    TipoEscalaEnum,
    TipoFilaEnum,
)
from app.models.frota import AlocacaoPatio, Fila, Onibus
from app.models.ocorrencia import (
    Ocorrencia, OcorrenciaAnalise, OcorrenciaAnexo, OcorrenciaAutoridade,
    OcorrenciaAvaria, OcorrenciaTestemunha, OcorrenciaVeiculoTerceiro,
    OcorrenciaVitima, OrgaoAutoridade, TipoOcorrencia,
)
from app.models.operacoes import Alerta, Escala, FichaManutencao, ImportacaoEscala
from app.models.pessoas import Motorista, Usuario

__all__ = [
    # Cadastro central
    "Funcionario", "Funcao", "FuncionarioFuncao", "UsuarioLogin",
    "FuncaoPermissao", "Recurso", "Modulo",
    # Legado
    "Usuario", "Motorista", "Onibus", "Fila", "AlocacaoPatio",
    "Escala", "Alerta", "FichaManutencao", "Linha", "TipoDefeito",
    "Permissao", "ImportacaoEscala",
    "SetorEnum", "StatusOnibusEnum", "StatusMotoristaEnum",
    "PerfilUsuarioEnum", "TipoFilaEnum", "TipoAlertaEnum",
    "StatusFichaEnum", "TipoEscalaEnum", "OrigemEscalaEnum",
    "StatusImportacaoEnum",
    # Coordenadoria — ocorrências
    "TipoOcorrencia", "OrgaoAutoridade", "Ocorrencia", "OcorrenciaAnalise",
    "OcorrenciaVeiculoTerceiro", "OcorrenciaAvaria", "OcorrenciaVitima",
    "OcorrenciaTestemunha", "OcorrenciaAutoridade", "OcorrenciaAnexo",
]
