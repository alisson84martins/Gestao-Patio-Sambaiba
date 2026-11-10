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
from app.models.consulta_placa import ConsultaPlaca
from app.models.frota import AlocacaoPatio, Fila, Onibus
from app.models.monitoramento import Coleta, LeituraVeiculo
from app.models.ocorrencia import (
    Ocorrencia, OcorrenciaAnalise, OcorrenciaAnexo, OcorrenciaAutoridade,
    OcorrenciaAvaria, OcorrenciaTestemunha, OcorrenciaVeiculoTerceiro,
    OcorrenciaVitima, OrgaoAutoridade, TipoOcorrencia,
)
from app.models.operacoes import Alerta, Escala, FichaManutencao, ImportacaoEscala
from app.models.pessoas import Motorista, Usuario
from app.models.portaria import (
    Credencial, EmpresaTerceira, MovimentoPortaria, PortariaLocal, PortariaSetor,
    RecolhidaAnormal, RecolhidaDefeito, VeiculoPortaria, VeiculoSituacaoHist,
)
from app.models.pre_cadastro import PessoaPreCadastro
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
    "OcorrenciaTestemunha", "OcorrenciaAutoridade", "OcorrenciaAnexo", "ConsultaPlaca",
    "PreOcorrenciaAutorizacao", "PreOcorrencia", "PreOcorrenciaAnexo",
    # Portaria — controle de acesso veicular
    "PortariaLocal", "PortariaSetor", "EmpresaTerceira", "VeiculoPortaria",
    "VeiculoSituacaoHist", "MovimentoPortaria", "Credencial", "RecolhidaAnormal", "RecolhidaDefeito",
    # Pré-cadastro de pessoas (Bloco H)
    "PessoaPreCadastro",
    # Monitoramento ao vivo da linha (migration 053)
    "LeituraVeiculo", "Coleta",
]
