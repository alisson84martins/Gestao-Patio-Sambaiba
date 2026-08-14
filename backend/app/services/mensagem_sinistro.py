"""Gerador do texto para o grupo do sinistro no WhatsApp.

Segue a seção 4 de _handoff-claude/MODULO-OCORRENCIAS-MENSAGEM-SINISTRO.md
ao pé da letra — inclusive os espaços duplos e os asteriscos de negrito do
WhatsApp, que não podem ser "corrigidos" ou a formatação quebra no app.

A fidelidade foi conferida manualmente contra o caso real regerado da seção 5
do documento (não reproduzido aqui nem em teste — é ocorrência real com dados
de vítima, mesmo mascarados, e não pertence ao repositório). O teste
automatizado em backend/tests/test_mensagem_sinistro.py cobre a mesma
estrutura de blocos com dados fictícios.
"""
from datetime import date, time
from typing import Optional


def _fmt_data(d: Optional[date]) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _fmt_hora(t: Optional[time]) -> str:
    """'16h55' — é assim que os coordenadores escrevem no grupo, não '16:55'."""
    return t.strftime("%Hh%M") if t else ""


def _linha(rotulo: str, valor) -> Optional[str]:
    """Uma linha 'Rótulo: valor' — None se vazio, pra a linha ser omitida do bloco."""
    if valor is None or valor == "":
        return None
    return f"{rotulo}: {valor}"


def _nome_tipo_exibicao(ocorrencia) -> str:
    """'Outros — <descrição>' quando o tipo é OUTROS e o coordenador
    escreveu qual; senão só o nome do tipo do catálogo."""
    tipo = ocorrencia.tipo_ocorrencia
    if not tipo:
        return ""
    if tipo.codigo == "OUTROS" and ocorrencia.tipo_outros_descricao:
        return f"{tipo.nome} — {ocorrencia.tipo_outros_descricao}"
    return tipo.nome


def _bloco_cabecalho(ocorrencia) -> str:
    """Bloco fixo: identificação da Sambaíba + tipo + coletivo/linha + local.

    Sempre aparece por completo (linhas em branco quando o dado não existe) —
    é a moldura que o grupo do sinistro espera ver em toda mensagem.
    """
    tipo_nome = _nome_tipo_exibicao(ocorrencia)
    local = ocorrencia.local_ocorrido or ""
    if ocorrencia.numero_local:
        local_linha = f"Local do Ocorrido: {local}  N° {ocorrencia.numero_local}"
    else:
        local_linha = f"Local do Ocorrido: {local}"

    linhas = [
        "* *Sambaíba G3 ❤️*",
        f"*Ocorrência - {tipo_nome}*",
        f"Data: {_fmt_data(ocorrencia.data_ocorrencia)}",
        f"Horário: {_fmt_hora(ocorrencia.hora_ocorrencia)}",
        f"Coletivo: {ocorrencia.prefixo or ''}",
        f"Linha: {ocorrencia.linha_codigo or ''}",
        # Motorista e cobrador saem só pelo RE, nunca pelo nome — privacidade
        # já praticada pelo Alisson hoje (ver MODULO-OCORRENCIAS-MENSAGEM-SINISTRO.md §2).
        f"Motorista: {ocorrencia.condutor_re or ''}",
        f"Cobrador: {ocorrencia.cobrador_re or ''}",
        local_linha,
        f"Bairro: {ocorrencia.bairro or ''}",
        f"Sentido: {ocorrencia.sentido or ''}",
    ]
    return "\n".join(linhas)


def _bloco_terceiro(veiculo) -> str:
    veiculo_nome = " ".join(p for p in [veiculo.marca, veiculo.modelo] if p) or None
    linhas = ["*Dados do terceiro*"]
    for linha_campo in (
        _linha("Veículo", veiculo_nome),
        _linha("Placa", veiculo.placa),
        _linha("Cidade", veiculo.cidade),
        _linha("Condutor", veiculo.proprietario),
        _linha("Tel", veiculo.fones),
    ):
        if linha_campo:
            linhas.append(linha_campo)
    return "\n".join(linhas)


def _linha_contato_vitima(vitima) -> Optional[str]:
    """Linha do contato da vítima — parentesco: nome, com o telefone do
    contato anexado na mesma linha (' — telefone') quando preenchido.

    Não vira uma segunda linha 'Tel:' própria: a vítima já tem a dela, e
    duas linhas 'Tel:' seguidas ficam indistinguíveis pra quem lê no grupo
    do WhatsApp sob pressão.
    """
    if not vitima.contato_nome:
        return None
    linha = _linha(vitima.contato_parentesco, vitima.contato_nome)
    if vitima.contato_fone:
        linha = f"{linha} — {vitima.contato_fone}"
    return linha


def _bloco_vitima(vitima) -> str:
    endereco = None
    if vitima.endereco:
        endereco = f"{vitima.endereco} N° {vitima.numero}" if vitima.numero else vitima.endereco

    linhas = ["*Dados da Vítima*"]
    for linha_campo in (
        _linha("Nome", vitima.nome),
        _linha("RG", vitima.rg),
        _linha("Endereço", endereco),
        _linha("Bairro", vitima.bairro),
        _linha("Cidade", vitima.cidade),
        _linha_contato_vitima(vitima),
        _linha("Tel", vitima.fone),
        _linha("Socorrida para", vitima.destino_socorro),
    ):
        if linha_campo:
            linhas.append(linha_campo)
    return "\n".join(linhas)


def _bloco_autoridades(autoridades) -> str:
    entradas = []
    for a in autoridades:
        partes = [a.orgao.nome if a.orgao else ""]
        if a.identificacao:
            partes.append(a.identificacao)
        partes.append(f"Responsável: {a.responsavel or ''}")
        obs_linha = _linha("Obs", a.observacao)
        if obs_linha:
            partes.append(obs_linha)
        entradas.append("\n".join(partes))
    return "*Autoridades No Local*\n" + "\n\n".join(entradas)


def _bloco_testemunhas(testemunhas) -> str:
    linhas = ["*TESTEMUNHAS*"]
    for t in testemunhas:
        fone = t.fone1 or ""
        if t.fone2:
            fone = f"{fone} / {t.fone2}" if fone else t.fone2
        linhas.append(f"{t.nome} — {fone}")
    return "\n".join(linhas)


def gerar_mensagem_sinistro(ocorrencia, coordenador_nome: str = "", coordenador_re: str = "") -> str:
    """Monta o texto pronto para o grupo do sinistro a partir da ocorrência completa.

    `ocorrencia` precisa trazer as relações carregadas: tipo_ocorrencia,
    veiculos_terceiro, vitimas, autoridades (com .orgao) e testemunhas —
    o router monta esse objeto via joinedload antes de chamar esta função.
    """
    blocos = [_bloco_cabecalho(ocorrencia)]

    # ⚠️ Decisão de projeto (não confirmada com o Alisson): o caso real do
    # documento de referência só mostra um veículo de terceiro e uma vítima
    # por mensagem. O schema aceita vários (engavetamento, múltiplas
    # vítimas) — na ausência de um modelo real com mais de um item, a
    # opção conservadora foi repetir o bloco por item, preservando todo
    # dado em vez de descartar. Confirmar formato com o Alisson quando
    # houver um caso real de engavetamento ou múltiplas vítimas.
    for veiculo in ocorrencia.veiculos_terceiro or []:
        blocos.append(_bloco_terceiro(veiculo))

    for vitima in ocorrencia.vitimas or []:
        blocos.append(_bloco_vitima(vitima))

    if ocorrencia.descricao_coordenador:
        blocos.append(f"*OBS:* {ocorrencia.descricao_coordenador}")

    blocos.append(f"*DESCRIÇÃO DO NOSSO MOTORISTA*\n{ocorrencia.descricao_motorista or ''}")

    if ocorrencia.autoridades:
        blocos.append(_bloco_autoridades(ocorrencia.autoridades))

    if ocorrencia.testemunhas:
        blocos.append(_bloco_testemunhas(ocorrencia.testemunhas))

    bo_linha = f"*B.O: N°* {ocorrencia.numero_bo}" if ocorrencia.numero_bo else "*B.O: N°*"
    protocolo_linha = f"*Protocolo:* {ocorrencia.protocolo}" if ocorrencia.protocolo else "*Protocolo:*"
    if ocorrencia.numero_bo_sptrans:
        blocos.append(f"{bo_linha}\n*B.O. SPTrans:* {ocorrencia.numero_bo_sptrans}\n{protocolo_linha}")
    else:
        blocos.append(f"{bo_linha}\n{protocolo_linha}")

    blocos.append(
        f"* *Relatórios entregue Controlador de acesso: {ocorrencia.controlador_acesso or ''}*\n"
        f"*Coordenador: {coordenador_nome}  {coordenador_re}*"
    )

    return "\n\n".join(blocos)
