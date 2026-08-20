"""Modelos SQLAlchemy 2.0 + ENUMs Python."""
from app.models.auditoria import LogAcesso
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
from app.models.portaria import (
    EmpresaTerceira, MovimentoPortaria, PortariaLocal, VeiculoPortaria,
    VeiculoSituacaoHist,
)
from app.models.pre_ocorrencia import PreOcorrencia, PreOcorrenciaAnexo, PreOcorrenciaAutorizacao

__all__ = [
    # Auditoria
    "LogAcesso",
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
    "PreOcorrenciaAutorizacao", "PreOcorrencia", "PreOcorrenciaAnexo",
    # Portaria — controle de acesso veicular
    "PortariaLocal", "EmpresaTerceira", "VeiculoPortaria",
    "VeiculoSituacaoHist", "MovimentoPortaria",
]
