"""Testes do gerador da mensagem do grupo do sinistro.

Os dados aqui são inteiramente fictícios (nomes, RE, RG, endereços e a
ocorrência em si não existem) — nunca usar um caso real, mesmo mascarado,
neste arquivo (ver R3 em PROMPT-EXECUCAO-COMPLETA.md).
"""
from dataclasses import dataclass, field
from datetime import date, time
from typing import Optional

from app.services.mensagem_sinistro import gerar_mensagem_sinistro


@dataclass
class _Tipo:
    nome: str
    codigo: str = "INCIDENTE"


@dataclass
class _Orgao:
    nome: str


@dataclass
class _Autoridade:
    orgao: _Orgao
    identificacao: str = ""
    responsavel: str = ""
    observacao: str = ""


@dataclass
class _Testemunha:
    nome: str
    fone1: str = ""
    fone2: Optional[str] = None


@dataclass
class _VeiculoTerceiro:
    marca: str = ""
    modelo: str = ""
    placa: str = ""
    cidade: str = ""
    proprietario: str = ""
    fones: str = ""


@dataclass
class _Vitima:
    nome: str = ""
    rg: str = ""
    endereco: str = ""
    numero: str = ""
    bairro: str = ""
    cidade: str = ""
    contato_parentesco: str = ""
    contato_nome: str = ""
    contato_fone: str = ""
    fone: str = ""
    destino_socorro: str = ""


@dataclass
class _Ocorrencia:
    tipo_ocorrencia: _Tipo
    data_ocorrencia: date
    hora_ocorrencia: time
    prefixo: str
    tipo_outros_descricao: str = ""
    linha_codigo: str = ""
    condutor_re: str = ""
    cobrador_re: str = ""
    local_ocorrido: str = ""
    numero_local: str = ""
    bairro: str = ""
    sentido: str = ""
    descricao_coordenador: str = ""
    descricao_motorista: str = ""
    numero_bo: str = ""
    numero_bo_sptrans: str = ""
    protocolo: str = ""
    controlador_acesso: str = ""
    veiculos_terceiro: list = field(default_factory=list)
    vitimas: list = field(default_factory=list)
    autoridades: list = field(default_factory=list)
    testemunhas: list = field(default_factory=list)


def _ocorrencia_minima() -> _Ocorrencia:
    return _Ocorrencia(
        tipo_ocorrencia=_Tipo("Incidente"),
        data_ocorrencia=date(2026, 1, 15),
        hora_ocorrencia=time(9, 5),
        prefixo="1234",
        linha_codigo="8500-10",
        condutor_re="11111",
        cobrador_re="22222",
        local_ocorrido="Rua Fictícia",
        numero_local="100",
        bairro="Bairro Teste",
        sentido="TP",
        descricao_motorista="Descrição fictícia do motorista.",
    )


def test_cabecalho_sempre_presente_com_re_nunca_nome():
    oc = _ocorrencia_minima()
    texto = gerar_mensagem_sinistro(oc, "Coordenador Fictício", "9999")

    assert "* *Sambaíba G3 ❤️*" in texto
    assert "*Ocorrência - Incidente*" in texto
    assert "Data: 15/01/2026" in texto
    assert "Horário: 09h05" in texto
    assert "Motorista: 11111" in texto
    assert "Cobrador: 22222" in texto
    # Motorista e cobrador saem só pelo RE — nome não pode vazar na mensagem.
    assert "João" not in texto


def test_blocos_condicionais_omitidos_quando_sem_dado():
    oc = _ocorrencia_minima()
    texto = gerar_mensagem_sinistro(oc)

    assert "*Dados do terceiro*" not in texto
    assert "*Dados da Vítima*" not in texto
    assert "*OBS:*" not in texto
    assert "*Autoridades No Local*" not in texto
    assert "*TESTEMUNHAS*" not in texto
    # B.O / Protocolo aparecem sempre, mesmo vazios
    assert "*B.O: N°*" in texto
    assert "*Protocolo:*" in texto


def test_blocos_condicionais_aparecem_quando_ha_dado():
    oc = _ocorrencia_minima()
    oc.descricao_coordenador = "Observação fictícia do coordenador."
    oc.veiculos_terceiro = [_VeiculoTerceiro(
        marca="Marca X", modelo="Modelo Y", placa="FIC-1234",
        cidade="Cidade Fictícia", proprietario="Fulano de Tal", fones="(11) 90000-0000",
    )]
    oc.vitimas = [_Vitima(
        nome="Vítima Fictícia", rg="000000", endereco="Rua da Vítima", numero="50",
        bairro="Bairro X", cidade="Cidade Fictícia", contato_parentesco="Filho",
        contato_nome="Contato Fictício", fone="(11) 91111-1111", destino_socorro="Hospital Fictício",
    )]
    oc.autoridades = [_Autoridade(_Orgao("SAMU"), "AMB-01", "Responsável Fictício")]
    oc.testemunhas = [_Testemunha("Testemunha 1", "(11) 92222-2222")]
    oc.numero_bo = "999999"
    oc.protocolo = "PROT-001"
    oc.controlador_acesso = "Fulano"

    texto = gerar_mensagem_sinistro(oc, "Coordenador Fictício", "9999")

    assert "*Dados do terceiro*\nVeículo: Marca X Modelo Y" in texto
    assert "*Dados da Vítima*\nNome: Vítima Fictícia" in texto
    assert "Filho: Contato Fictício" in texto
    assert "*OBS:* Observação fictícia do coordenador." in texto
    assert "*Autoridades No Local*\nSAMU\nAMB-01\nResponsável: Responsável Fictício" in texto
    assert "*TESTEMUNHAS*\nTestemunha 1 — (11) 92222-2222" in texto
    assert "*B.O: N°* 999999" in texto
    assert "*Protocolo:* PROT-001" in texto
    assert texto.endswith("*Coordenador: Coordenador Fictício  9999*")
    # Autoridade sem observação preenchida não deve imprimir "Obs:" órfão.
    assert "Obs:" not in texto


def test_autoridade_com_observacao_aparece_na_mensagem():
    """Item 7: a observação da autoridade (ex.: número de B.O. de outro
    órgão anotado ali) precisa sair na mensagem — antes era lida e nunca
    impressa."""
    oc = _ocorrencia_minima()
    oc.autoridades = [_Autoridade(
        _Orgao("SPTrans"), "VTR 2114", "Agente Platini / Agente Fulano",
        observacao="BO SPTRANS 45510/26",
    )]

    texto = gerar_mensagem_sinistro(oc)

    assert (
        "*Autoridades No Local*\nSPTrans\nVTR 2114\n"
        "Responsável: Agente Platini / Agente Fulano\nObs: BO SPTRANS 45510/26"
    ) in texto


def test_autoridade_sem_observacao_nao_imprime_linha_orfa():
    oc = _ocorrencia_minima()
    oc.autoridades = [_Autoridade(_Orgao("SAMU"), "AMB-01", "Responsável Fictício")]

    texto = gerar_mensagem_sinistro(oc)

    assert "*Autoridades No Local*\nSAMU\nAMB-01\nResponsável: Responsável Fictício" in texto
    assert "Obs:" not in texto


def test_local_sem_numero_nao_imprime_marcador_n():
    oc = _ocorrencia_minima()
    oc.numero_local = ""
    texto = gerar_mensagem_sinistro(oc)
    assert "Local do Ocorrido: Rua Fictícia\n" in texto
    assert "N°" not in texto.split("\n\n")[0]


def test_tipo_outros_mostra_descricao_no_cabecalho():
    """Item 3: tipo OUTROS com descrição escrita pelo coordenador aparece
    como 'Outros — <descrição>' em vez de só 'Outros'."""
    oc = _ocorrencia_minima()
    oc.tipo_ocorrencia = _Tipo("Outros", codigo="OUTROS")
    oc.tipo_outros_descricao = "Vazamento de óleo na garagem"

    texto = gerar_mensagem_sinistro(oc)

    assert "*Ocorrência - Outros — Vazamento de óleo na garagem*" in texto


def test_tipo_outros_sem_descricao_mostra_so_o_nome():
    oc = _ocorrencia_minima()
    oc.tipo_ocorrencia = _Tipo("Outros", codigo="OUTROS")
    oc.tipo_outros_descricao = ""

    texto = gerar_mensagem_sinistro(oc)

    assert "*Ocorrência - Outros*" in texto


def test_bo_sptrans_aparece_entre_bo_e_protocolo():
    """Item 8: quando preenchido, o B.O. da SPTrans sai numa linha própria
    entre o B.O. da polícia e o Protocolo."""
    oc = _ocorrencia_minima()
    oc.numero_bo = "1234/2026"
    oc.numero_bo_sptrans = "45510/26"
    oc.protocolo = "PROT-9"

    texto = gerar_mensagem_sinistro(oc)

    assert (
        "*B.O: N°* 1234/2026\n*B.O. SPTrans:* 45510/26\n*Protocolo:* PROT-9"
    ) in texto


def test_bo_sptrans_vazio_nao_imprime_linha_orfa():
    """Sem numero_bo_sptrans preenchido, a moldura fixa de B.O./Protocolo
    sai exatamente como antes — nenhuma linha nova."""
    oc = _ocorrencia_minima()
    oc.numero_bo = "1234/2026"
    oc.protocolo = "PROT-9"

    texto = gerar_mensagem_sinistro(oc)

    assert "*B.O: N°* 1234/2026\n*Protocolo:* PROT-9" in texto
    assert "B.O. SPTrans" not in texto


def test_contato_da_vitima_com_nome_e_telefone_sai_na_mesma_linha():
    """Item C do lote 2: achado de uso real — o telefone do contato
    (contato_fone) nunca era lido pelo gerador, mesmo gravado no banco.
    Entra na mesma linha do contato, com travessão, nunca numa segunda
    linha 'Tel:' própria (a vítima já tem a dela)."""
    oc = _ocorrencia_minima()
    oc.vitimas = [_Vitima(
        nome="Vítima Fictícia", contato_parentesco="Amigo",
        contato_nome="Contato Fictício", contato_fone="(11) 98888-7777",
        fone="(11) 97777-6666",
    )]

    texto = gerar_mensagem_sinistro(oc)

    assert "Amigo: Contato Fictício — (11) 98888-7777" in texto
    assert "Tel: (11) 97777-6666" in texto
    # Nunca duas linhas "Tel:" seguidas — ficam indistinguíveis no grupo.
    assert texto.count("Tel:") == 1


def test_contato_da_vitima_sem_telefone_sai_so_o_nome():
    oc = _ocorrencia_minima()
    oc.vitimas = [_Vitima(
        nome="Vítima Fictícia", contato_parentesco="Amigo",
        contato_nome="Contato Fictício", contato_fone="",
    )]

    texto = gerar_mensagem_sinistro(oc)
    bloco_vitima = texto.split("*Dados da Vítima*")[1].split("\n\n")[0]

    assert "Amigo: Contato Fictício" in bloco_vitima
    assert "—" not in bloco_vitima


def test_vitima_sem_contato_nao_imprime_linha_de_contato():
    oc = _ocorrencia_minima()
    oc.vitimas = [_Vitima(nome="Vítima Fictícia", fone="(11) 91111-1111")]

    texto = gerar_mensagem_sinistro(oc)

    assert "*Dados da Vítima*\nNome: Vítima Fictícia\nTel: (11) 91111-1111" in texto
    assert "—" not in texto.split("*Dados da Vítima*")[1].split("\n\n")[0]


def test_tipo_diferente_de_outros_ignora_descricao_residual():
    """Se o front mandar tipo_outros_descricao "sobrando" (não deveria,
    ver ocorrencia.form.js), o back não mistura no nome de um tipo comum."""
    oc = _ocorrencia_minima()
    oc.tipo_outros_descricao = "Texto que não devia aparecer"

    texto = gerar_mensagem_sinistro(oc)

    assert "*Ocorrência - Incidente*" in texto
    assert "Texto que não devia aparecer" not in texto
