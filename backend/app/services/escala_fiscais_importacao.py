"""Importação dos modelos da Escala de Fiscais a partir do JSON da planilha.

Dois passos, mesmo código de análise nos dois:
  · SIMULAR  — analisar() lê o JSON, resolve os nomes pelos REs e devolve um
               relatório. ⛔ Não grava nada.
  · CONFIRMAR — gravar() recebe o plano de uma análise SEM problema
               bloqueante e grava postos, linhas, modelos e modelo_posto numa
               transação só.

⛔ GENÉRICO: este módulo não contém nenhum RE, nome, linha ou posto real.
Tudo vem do arquivo que a tela envia. A correção do que só a pessoa pode
decidir é feita na tela, no próprio JSON (a tela edita o campo, ou marca a
linha com "_descartar": true, e simula de novo).

Decisões do Alisson (24/09/2026) que este módulo aplica:
  · D-A  RE em TEXTO, sem cadastro obrigatório, normalizado pela MESMA função
         do cadastro de Pessoas (app/core/registro.py — trim + maiúsculas,
         nunca vira número, zero à esquerda preservado). RE sem cadastro em
         Pessoas é AVISO, não bloqueio.
  · D-B  Fidelidade ao Excel: quando a célula do RE não é RE, o texto original
         vai para `marcador` ('' vazio, '****', 'xxx', 'G1', 'DIRETO', '-').
         Posto sem RE continua existindo, em branco (descoberto).
  · D-C  G1/G2/G4 guardados como estão (outra_garagem + marcador). G3 (a
         própria garagem) continua BLOQUEADO.
  · D-D  Lote: só E2 e AR2; A2/Ar2 viram AR2. Valor que não é lote fica como
         está e vira aviso.
  · D-E  Horário que EXISTE mas é inválido, invertido ou incompleto NÃO
         bloqueia: entra o padrão do período (configurável — ver
         Settings.escala_fiscal_padrao_*) e o caso vai para o relatório como
         AJUSTADO PARA O PADRÃO. Célula SEM horário (G1, DIRETO, ****, em
         branco...) fica SEM horário — fidelidade ao Excel (resposta de 24/09).
  · D-F  "TS"/"TP" sobrando no campo de linhas é anotação de que a linha é
         marcada na outra ponta: sai do campo, a linha fica, e vira aviso.

Formato esperado (o que o extrator da planilha gera):
  { "<NOME DA ABA>": { "postos": [ {bloco, L, linha_planilha, cod_jb, lote,
        linhas_1p, re_1p, inicio_1p, termino_1p,
        linhas_2p, re_2p, inicio_2p, termino_2p, ...}, ... ],
      "rodape_bruto": [ {celula, valor}, ... ] }, ... }
Abas reconhecidas pelo nome: ÚTIL, SÁBADO ÍMPAR, DOMINGO ÍMPAR, SÁBADO PAR,
DOMINGO PAR. Qualquer outra chave é listada em abas_ignoradas.

O que NÃO é feito aqui, de propósito:
  · ponto final — a importação não cria nem liga ponto final (cadastro na tela);
  · folga base — só SUGERE (a partir das listas de folga do SÁBADO ÍMPAR e do
    DOMINGO ÍMPAR); quem grava é a tela, com confirmação;
  · quadro de fiscais, ausências, trocas, plantão — fora do escopo.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Callable, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import registro
from app.models.escala_fiscais import (
    EscalaFiscalModelo,
    EscalaFiscalModeloPosto,
    EscalaFiscalPosto,
    EscalaFiscalPostoLinha,
)

# ─── Modelos reconhecidos ─────────────────────────────────────────────────────
# (prefixo do nome da aba normalizado) → (tipo_dia, paridade, nome do modelo)
_ABAS = [
    ("SABADO IMPAR", ("sabado", "impar", "Sábado ímpar")),
    ("DOMINGO IMPAR", ("domingo", "impar", "Domingo ímpar")),
    ("SABADO PAR", ("sabado", "par", "Sábado par")),
    ("DOMINGO PAR", ("domingo", "par", "Domingo par")),
    ("UTIL", ("util", None, "Útil")),
]

# Código de linha como a planilha escreve: 4 caracteres + "/" + 2 dígitos
# (ex.: 9001/10, 900A/21). Guardado exatamente assim em posto_linha.linha.
_RE_LINHA = re.compile(r"[0-9A-Z]{4}/\d{2}")
_RE_HORA = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
_RE_GARAGEM = re.compile(r"^G\d+$")
_RE_DESCOBERTO = re.compile(r"^(\*+|X+)$")
_RE_COD_JB = re.compile(r"^[0-9 /]*$")
_RE_TROCA = re.compile(r"FOLGA\s+(2X2|1X1)\s+([0-9A-Z]+)\s+([0-9A-Z]+)", re.IGNORECASE)
_MARCADOR_PERIODO = {"1°": 1, "1º": 1, "2°": 2, "2º": 2}
_VAZIOS = (None, "", "-")
# D-F: anotação de ponta que pode sobrar no campo de linhas.
_ANOTACAO_PONTA = {"TS", "TP"}

# D-D: lotes que existem (mesma regra dos ônibus) e grafias que viram lote.
LOTES_VALIDOS = ("E2", "AR2")
APELIDOS_LOTE = {"A2": "AR2"}

# G3 é a própria garagem — "outra garagem" nunca pode ser ela (CHECK da 045).
GARAGEM_PROPRIA = "G3"
TAMANHO_MARCADOR = 10

_PERIODOS = (1, 2)

Horario = tuple[time, time]


def padrao_de_texto(texto: str) -> Horario:
    """'05:00-14:00' → (05:00, 14:00)."""
    ini, fim = texto.split("-")
    return _parse_hora(ini)[0], _parse_hora(fim)[0]


def _normalizar_nome_aba(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.upper().split())


def identificar_aba(nome: str) -> Optional[tuple[str, Optional[str], str]]:
    normal = _normalizar_nome_aba(nome)
    for prefixo, info in _ABAS:
        if normal.startswith(prefixo):
            return info
    return None


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).split())


def normalizar_re(valor: Any) -> str:
    """RE da planilha → texto, pela MESMA regra do cadastro de Pessoas
    (app/core/registro.py: trim + maiúsculas, nunca vira número, zero à
    esquerda preservado). A planilha traz RE como número; vira texto antes."""
    if valor is None:
        return ""
    return registro.normalizar_re(str(valor)) or ""


def _parse_hora(valor: Any) -> tuple[Optional[time], bool]:
    """(hora, valida). Vazio/'-' é válido e vira None."""
    if valor in _VAZIOS:
        return None, True
    m = _RE_HORA.match(str(valor).strip())
    if not m:
        return None, False
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None, False
    return time(h, mi), True


def _hora_str(t: Optional[time]) -> Optional[str]:
    return t.strftime("%H:%M") if t else None


def _parse_celula(celula: str) -> Optional[tuple[int, int]]:
    """'N45' → (coluna 14, linha 45)."""
    m = re.fullmatch(r"([A-Z]+)(\d+)", str(celula or "").strip().upper())
    if not m:
        return None
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return col, int(m.group(2))


def _eh_numero(valor: Any) -> bool:
    return isinstance(valor, int) or (isinstance(valor, str) and valor.strip().isdigit())


def normalizar_lote(texto: str) -> tuple[str, bool, list[str]]:
    """D-D. Devolve (lote a gravar, mudou?, pedaços que não são lote).
    Só normaliza quando TODOS os pedaços viram lote válido; se sobrar
    pedaço que não é lote, grava o texto como estava."""
    pedacos = [p.strip() for p in texto.split("/") if p.strip()]
    normais, invalidos = [], []
    for p in pedacos:
        n = APELIDOS_LOTE.get(p.upper(), p.upper())
        if n in LOTES_VALIDOS:
            normais.append(n)
        else:
            invalidos.append(p)
    if invalidos or not pedacos:
        return texto, False, invalidos
    novo = " / ".join(normais)
    return novo, novo != texto, []


# ─── Estruturas de saída ──────────────────────────────────────────────────────

@dataclass
class Cobertura:
    """Uma linha de modelo_posto a gravar (um posto num período de um modelo)."""
    aba: str
    indice: int
    ordem: int
    periodo: int
    lado: str
    cod_jb: Optional[str]
    lote: Optional[str]
    linhas: list[str]
    hora_inicio: Optional[time]
    hora_termino: Optional[time]
    situacao: str
    re: Optional[str]
    outra_garagem: Optional[str]
    marcador: Optional[str]

    @property
    def chave_posto(self) -> tuple[str, tuple[str, ...]]:
        return posto_chave(self.lado, self.linhas)


def posto_chave(lado: str, linhas: list[str]) -> tuple[str, tuple[str, ...]]:
    """Identidade do posto: a ponta + o conjunto de linhas. cod_jb e lote NÃO
    entram (a planilha repete o mesmo cod_jb em postos diferentes)."""
    return lado, tuple(sorted(linhas))


@dataclass
class Analise:
    relatorio: dict
    coberturas: list[Cobertura] = field(default_factory=list)
    modelos: list[dict] = field(default_factory=list)
    # chave do posto → (cod_jb, lote) escolhidos entre os modelos
    dados_postos: dict = field(default_factory=dict)

    @property
    def pode_confirmar(self) -> bool:
        return self.relatorio["pode_confirmar"]


# ─── Análise ──────────────────────────────────────────────────────────────────

# RE (texto) → nome, só dos REs que têm cadastro em funcionario.
ResolverNomes = Callable[[list[str]], dict[str, str]]


def analisar(
    dados: Any,
    resolver_nomes: ResolverNomes,
    padroes: dict[int, Horario],
    modelos_existentes: Optional[dict[str, int]] = None,
    postos_existentes: Optional[set] = None,
) -> Analise:
    """Lê o JSON e devolve relatório + plano. ⛔ Não toca no banco — quem
    chama passa `resolver_nomes`, o horário padrão de cada período e o que
    já existe, para o relatório dizer o que seria criado e o que seria
    reaproveitado.
    """
    modelos_existentes = modelos_existentes or {}
    postos_existentes = postos_existentes or set()
    problemas: list[dict] = []
    abas_ignoradas: list[str] = []
    descartadas: list[dict] = []
    coberturas: list[Cobertura] = []
    modelos: list[dict] = []
    res_vistos: dict[str, list[dict]] = {}
    rodapes: dict[str, list] = {}

    def problema(tipo, bloqueia, mensagem, **extra):
        problemas.append({"tipo": tipo, "bloqueia": bloqueia, "mensagem": mensagem, **extra})

    if not isinstance(dados, dict):
        problema("arquivo_invalido", True, "O arquivo não é um objeto JSON com as abas da planilha.")
        return _fechar(problemas, [], [], {}, abas_ignoradas, descartadas, {}, resolver_nomes,
                       modelos_existentes, postos_existentes)

    for nome_aba, aba in dados.items():
        info = identificar_aba(nome_aba) if isinstance(aba, dict) and "postos" in aba else None
        if info is None:
            if not str(nome_aba).startswith("_"):
                abas_ignoradas.append(str(nome_aba))
            continue
        tipo_dia, paridade, nome_modelo = info
        if any(m["nome"] == nome_modelo for m in modelos):
            problema("aba_repetida", True,
                     f"Duas abas viram o mesmo modelo \"{nome_modelo}\" ({nome_aba}). Deixe só uma.",
                     modelo=nome_aba)
            continue
        modelos.append({
            "aba": nome_aba, "nome": nome_modelo, "tipo_dia": tipo_dia, "paridade": paridade,
            "ja_existe_com_postos": modelos_existentes.get(nome_modelo, 0) > 0,
        })
        rodapes[nome_aba] = aba.get("rodape_bruto") or []
        postos = aba.get("postos") or []
        if not isinstance(postos, list):
            problema("arquivo_invalido", True, f"Aba {nome_aba}: 'postos' não é uma lista.", modelo=nome_aba)
            continue

        for indice, linha in enumerate(postos):
            if not isinstance(linha, dict):
                continue
            loc = {
                "modelo": nome_aba, "indice": indice,
                "linha_planilha": linha.get("linha_planilha"), "bloco": linha.get("bloco"),
            }
            if linha.get("_descartar"):
                descartadas.append({**loc, "cod_jb": _texto(linha.get("cod_jb"))})
                continue

            # Texto do rodapé que caiu dentro da linha de posto: a linha inteira
            # não é posto (ex.: "FÉRIAS 1°" no cod_jb, lista de REs nas linhas).
            motivo_rodape = _linha_parece_rodape(linha)
            if motivo_rodape:
                problema(
                    "texto_rodape", True,
                    f"Linha {linha.get('linha_planilha')} ({linha.get('bloco')}) parece texto do "
                    f"rodapé, não posto: {motivo_rodape}. Descarte a linha ou corrija os campos.",
                    campo=None, valor=_resumo_linha(linha), acoes=["descartar", "editar"], **loc,
                )
                continue

            lado = _texto(linha.get("bloco")).upper()
            if lado not in ("TP", "TS"):
                problema("lado_invalido", True,
                         f"Bloco \"{linha.get('bloco')}\" não é TP nem TS.",
                         campo="bloco", valor=linha.get("bloco"), acoes=["editar"], **loc)
                continue
            coluna_l = _texto(linha.get("L")).upper()
            if coluna_l and coluna_l != lado:
                problema("lado_divergente", False,
                         f"Coluna L diz \"{linha.get('L')}\", mas a linha está no bloco {lado}. "
                         f"Importado como {lado} (o bloco da planilha).",
                         campo="L", valor=linha.get("L"), **loc)

            cod_jb = _texto(linha.get("cod_jb")) or None
            lote_bruto = _texto(linha.get("lote"))
            lote = lote_bruto or None
            if lote_bruto:
                lote, mudou, invalidos = normalizar_lote(lote_bruto)
                if mudou:
                    problema("lote_normalizado", False,
                             f"Lote \"{lote_bruto}\" gravado como \"{lote}\" (só existem E2 e AR2).",
                             campo="lote", valor=lote_bruto, **loc)
                if invalidos:
                    problema("lote_invalido", False,
                             f"Lote \"{lote_bruto}\": \"{' / '.join(invalidos)}\" não é lote "
                             f"(só existem E2 e AR2). Entra como está escrito.",
                             campo="lote", valor=lote_bruto, acoes=["editar"], **loc)

            for periodo in _PERIODOS:
                sufixo = f"_{periodo}p"
                campo_linhas = "linhas" + sufixo
                texto_linhas = _texto(linha.get(campo_linhas)).upper()
                linhas = _RE_LINHA.findall(texto_linhas)
                sobra = _RE_LINHA.sub(" ", texto_linhas).replace("/", " ").split()
                re_bruto = linha.get("re" + sufixo)
                ini_bruto = linha.get("inicio" + sufixo)
                fim_bruto = linha.get("termino" + sufixo)

                if not linhas:
                    if re_bruto in _VAZIOS and ini_bruto in _VAZIOS and fim_bruto in _VAZIOS:
                        continue  # período sem nada — não há cobertura
                    problema("posto_sem_linha", True,
                             f"Período {periodo} tem RE/horário mas nenhuma linha reconhecível "
                             f"em \"{linha.get(campo_linhas)}\".",
                             campo=campo_linhas, valor=linha.get(campo_linhas), acoes=["editar", "descartar"], **loc)
                    continue
                if sobra and all(s in _ANOTACAO_PONTA for s in sobra):
                    # D-F: "TS" é a anotação de que a linha é marcada na outra ponta.
                    problema("ponta_anotada", False,
                             f"Campo de linhas do período {periodo} tinha \"{' '.join(sobra)}\" "
                             f"(\"{linha.get(campo_linhas)}\"): anotação de que a linha é marcada na "
                             f"outra ponta. O texto saiu, as linhas ficaram no posto.",
                             campo=campo_linhas, valor=linha.get(campo_linhas), **loc)
                elif sobra:
                    problema("texto_rodape", True,
                             f"Campo de linhas do período {periodo} tem texto sobrando: "
                             f"\"{' '.join(sobra)}\" em \"{linha.get(campo_linhas)}\".",
                             campo=campo_linhas, valor=linha.get(campo_linhas), acoes=["editar"], **loc)
                    continue

                situacao, re_txt, garagem, marcador, erro = _classificar_re(re_bruto)
                if erro == "g3":
                    problema("g3_no_re", True,
                             f"\"{GARAGEM_PROPRIA}\" no lugar do RE no período {periodo}: G3 é a própria "
                             f"garagem. Coloque o RE do fiscal, a garagem que cobre, **** ou DIRETO.",
                             campo="re" + sufixo, valor=re_bruto, acoes=["editar"], **loc)
                    continue
                if erro == "desconhecido":
                    problema("re_desconhecido", True,
                             f"Valor \"{re_bruto}\" no RE do período {periodo} não é RE, garagem, "
                             f"****, xxx nem DIRETO.",
                             campo="re" + sufixo, valor=re_bruto, acoes=["editar"], **loc)
                    continue
                if marcador == "":
                    problema("em_branco", False,
                             f"Período {periodo} sem RE: o posto entra EM BRANCO (descoberto), "
                             f"pronto para preencher na tela.",
                             campo="re" + sufixo, valor=None, **loc)

                h_ini, h_fim = _horario_ou_padrao(periodo, ini_bruto, fim_bruto, padroes, problema, loc)

                if re_txt:
                    res_vistos.setdefault(re_txt, []).append(
                        {"modelo": nome_aba, "linha_planilha": linha.get("linha_planilha"),
                         "bloco": lado, "periodo": periodo}
                    )

                coberturas.append(Cobertura(
                    aba=nome_aba, indice=indice, ordem=indice + 1, periodo=periodo, lado=lado,
                    cod_jb=cod_jb, lote=lote, linhas=linhas, hora_inicio=h_ini, hora_termino=h_fim,
                    situacao=situacao, re=re_txt, outra_garagem=garagem, marcador=marcador,
                ))

        for achado in _postos_no_rodape(rodapes[nome_aba]):
            problema("posto_no_rodape", False,
                     f"A linha {achado['linha_planilha']} da planilha está no rodapé, mas tem cara de "
                     f"posto ({achado['resumo']}). Ela NÃO será importada: adicione o posto na aba "
                     f"Modelos depois, ou corrija o arquivo.",
                     modelo=nome_aba, linha_planilha=achado["linha_planilha"], valor=achado["resumo"])

    dados_postos, completados = _resolver_dados_postos(coberturas, problemas)
    analise = _fechar(problemas, coberturas, modelos, res_vistos, abas_ignoradas,
                      descartadas, rodapes, resolver_nomes, modelos_existentes, postos_existentes)
    analise.dados_postos = dados_postos
    analise.relatorio["resumo"]["postos_completados_por_outro_modelo"] = completados
    return analise


def _horario_ou_padrao(periodo, ini_bruto, fim_bruto, padroes, problema, loc):
    """D-E, com a resposta de 24/09. Horário válido entra como está. Célula
    SEM horário (vazia ou '-') fica sem horário — fidelidade ao Excel. Só o
    horário que EXISTE mas é inválido, invertido ou incompleto recebe o
    PADRÃO do período, e vai para o relatório."""
    sufixo = f"_{periodo}p"
    h_ini, ok_ini = _parse_hora(ini_bruto)
    h_fim, ok_fim = _parse_hora(fim_bruto)
    if ok_ini and ok_fim and not h_ini and not h_fim:
        return None, None
    if ok_ini and ok_fim and h_ini and h_fim and h_fim > h_ini:
        return h_ini, h_fim

    p_ini, p_fim = padroes[periodo]
    padrao_txt = f"{_hora_str(p_ini)}–{_hora_str(p_fim)}"
    original = f"\"{ini_bruto if ini_bruto is not None else ''}\" a \"{fim_bruto if fim_bruto is not None else ''}\""
    if not ok_ini or not ok_fim:
        motivo = "horário inválido"
    elif h_ini and h_fim:
        motivo = "término não é depois do início"
    else:
        motivo = "horário incompleto"
    problema("ajustado_padrao", False,
             f"Período {periodo}: {motivo} ({original}). Entrou o padrão {padrao_txt}; confira "
             f"e altere na tela se precisar.",
             campo=("inicio" if not ok_ini else "termino") + sufixo,
             valor=fim_bruto if ok_ini else ini_bruto, padrao=padrao_txt, **loc)
    return p_ini, p_fim


def _classificar_re(valor: Any) -> tuple[str, Optional[str], Optional[str], Optional[str], Optional[str]]:
    """(situacao, re, outra_garagem, marcador, erro). RE sempre como TEXTO —
    nunca converte para número (RE com letra existe). marcador = texto
    original quando NÃO é RE (D-B), None quando é RE."""
    if valor is None or (isinstance(valor, str) and valor.strip() == ""):
        return "descoberto", None, None, "", None
    if _eh_numero(valor):
        # RE: mesma normalização do cadastro de Pessoas (app/core/registro.py).
        return "escalado", normalizar_re(valor), None, None, None
    # Marcador: o texto COMO ESTÁ ESCRITO na planilha (só sem espaço nas
    # pontas) — "xxx" continua "xxx" na impressão (D-B).
    original = str(valor).strip()
    texto = original.upper()
    if texto == GARAGEM_PROPRIA:
        return "", None, None, None, "g3"
    if len(original) > TAMANHO_MARCADOR:
        return "", None, None, None, "desconhecido"
    if _RE_GARAGEM.match(texto):
        return "outra_garagem", None, texto, original, None
    if _RE_DESCOBERTO.match(texto) or texto == "-":
        return "descoberto", None, None, original, None
    if texto == "DIRETO":
        return "direto", None, None, original, None
    return "", None, None, None, "desconhecido"


# Montagem (Fase 4): a célula digitada na tela passa pela MESMA classificação
# da importação — marcador fiel ao que foi escrito (D-B).
classificar_celula = _classificar_re


def _linha_parece_rodape(linha: dict) -> Optional[str]:
    cod_jb = _texto(linha.get("cod_jb"))
    if cod_jb and not _RE_COD_JB.match(cod_jb):
        return f"cod_jb = \"{cod_jb}\""
    lote = _texto(linha.get("lote"))
    if lote and lote.replace(" ", "").isdigit():
        return f"lote = \"{lote}\" (número, não lote)"
    campos_linha = [_texto(linha.get(f"linhas_{p}p")) for p in _PERIODOS]
    if any(campos_linha) and not any(_RE_LINHA.search(c.upper()) for c in campos_linha):
        return f"linhas = \"{' | '.join(c for c in campos_linha if c)}\""
    return None


def _resumo_linha(linha: dict) -> str:
    partes = [_texto(linha.get(c)) for c in ("cod_jb", "lote", "linhas_1p", "re_1p")]
    return " | ".join(p for p in partes if p)


def _postos_no_rodape(rodape: list) -> list[dict]:
    """Linhas do rodapé_bruto com código de linha: linhas de posto que o
    extrator não pôs na lista de postos."""
    por_linha: dict[int, list[tuple[int, Any]]] = {}
    for cel in rodape:
        pos = _parse_celula(cel.get("celula") if isinstance(cel, dict) else None)
        if pos is None:
            continue
        por_linha.setdefault(pos[1], []).append((pos[0], cel.get("valor")))
    achados: list[dict] = []
    for n_linha in sorted(por_linha):
        valores = [v for _, v in sorted(por_linha[n_linha]) if v not in (None, "")]
        tem_linha = any(_RE_LINHA.search(str(v).upper()) for v in valores)
        resumo = " | ".join(_texto(v) for v in valores)
        # Posto ocupa duas linhas na planilha (Início em cima, Término
        # embaixo): a de baixo entra no mesmo achado.
        if achados and achados[-1]["_ultima"] == n_linha - 1 and any(
            _texto(v).upper().startswith("T") and "RMINO" in _texto(v).upper() for v in valores
        ):
            achados[-1]["resumo"] += " / " + resumo
            achados[-1]["_ultima"] = n_linha
        elif tem_linha:
            achados.append({"linha_planilha": n_linha, "resumo": resumo, "_ultima": n_linha})
    return achados


def _resolver_dados_postos(coberturas: list[Cobertura], problemas: list[dict]) -> tuple[dict, int]:
    """cod_jb e lote de cada posto (ponta + linhas), que aparece em vários
    modelos. Fica o PRIMEIRO valor preenchido, na ordem do arquivo; vazio
    num modelo e preenchido em outro não é conflito. Dois valores
    preenchidos e diferentes viram aviso."""
    escolhido: dict[tuple, dict[str, tuple[str, str]]] = {}
    completados: set = set()
    avisados: set = set()
    for c in coberturas:
        atual = escolhido.setdefault(c.chave_posto, {})
        for campo in ("cod_jb", "lote"):
            valor = getattr(c, campo)
            if not valor:
                continue
            if campo not in atual:
                if any(o.chave_posto == c.chave_posto and o.aba != c.aba and not getattr(o, campo)
                       for o in coberturas):
                    completados.add(c.chave_posto)
                atual[campo] = (valor, c.aba)
            elif atual[campo][0] != valor and (c.chave_posto, campo, valor) not in avisados:
                avisados.add((c.chave_posto, campo, valor))
                problemas.append({
                    "tipo": "posto_divergente", "bloqueia": False,
                    "mensagem": f"Posto {c.lado} {' '.join(c.linhas)}: {campo} \"{valor}\" em {c.aba}, "
                                f"mas \"{atual[campo][0]}\" em {atual[campo][1]}. Fica \"{atual[campo][0]}\".",
                    "modelo": c.aba, "indice": c.indice, "linha_planilha": None,
                    "campo": campo, "valor": valor,
                })
    dados = {
        chave: (v.get("cod_jb", (None,))[0], v.get("lote", (None,))[0]) for chave, v in escolhido.items()
    }
    return dados, len(completados)


def sugerir_folgas(rodapes: dict[str, list], modelos: list[dict]) -> tuple[list[dict], list[dict]]:
    """Sugestão de folga base a partir das listas "de folga" do SÁBADO
    ÍMPAR e do DOMINGO ÍMPAR (mês ímpar: folga base = o próprio dia).
    Devolve (sugestões, trocas encontradas). ⛔ Não grava nada."""
    por_re: dict[str, dict] = {}
    trocas: list[dict] = []
    for m in modelos:
        if m["paridade"] != "impar" or m["tipo_dia"] not in ("sabado", "domingo"):
            continue
        rodape = rodapes.get(m["aba"]) or []
        for periodo, re_txt in _lista_de_folga(rodape):
            s = por_re.setdefault(re_txt, {"re": re_txt, "dias": set(), "periodos": set(), "fontes": []})
            s["dias"].add(m["tipo_dia"])
            s["periodos"].add(periodo)
            s["fontes"].append(m["aba"])
        for cel in rodape:
            for tipo, a, b in _RE_TROCA.findall(_texto(cel.get("valor") if isinstance(cel, dict) else "")):
                troca = {"modelo": m["aba"], "tipo": tipo.lower(), "re_a": normalizar_re(a), "re_b": normalizar_re(b)}
                if troca not in trocas:
                    trocas.append(troca)

    em_troca = {t["re_a"] for t in trocas} | {t["re_b"] for t in trocas}
    sugestoes = []
    for re_txt, s in sorted(por_re.items(), key=lambda kv: (len(kv[0]), kv[0])):
        if len(s["dias"]) == 1:
            folga = next(iter(s["dias"]))
            obs = None
        else:
            folga = None
            obs = "Aparece de folga no sábado E no domingo — decida na mão."
        if re_txt in em_troca:
            obs = ((obs + " ") if obs else "") + "Está numa troca neste fim de semana: a folga pode ser da troca, não a base."
        sugestoes.append({
            "re": re_txt,
            "folga_sugerida": folga,
            "periodo_sugerido": next(iter(s["periodos"])) if len(s["periodos"]) == 1 else None,
            "fontes": sorted(set(s["fontes"])),
            "observacao": obs,
            "a_confirmar": True,
        })
    return sugestoes, trocas


def _lista_de_folga(rodape: list) -> list[tuple[int, str]]:
    """REs das listas de folga do rodapé. Cada lista começa numa célula
    '1°'/'2°' (o período) e segue à direita e para baixo, até: o próximo
    marcador, uma linha com texto (ex.: 'FOLGA 2X2 ...') ou o começo de outra
    seção à direita (ex.: 'ATESTADO MÉDICO', 'FÉRIAS 2°')."""
    celulas = []
    for cel in rodape:
        if not isinstance(cel, dict):
            continue
        pos = _parse_celula(cel.get("celula"))
        if pos is not None and cel.get("valor") not in (None, ""):
            celulas.append((pos[0], pos[1], cel.get("valor")))
    marcadores = sorted(
        (lin, col, _MARCADOR_PERIODO[_texto(v)]) for col, lin, v in celulas if _texto(v) in _MARCADOR_PERIODO
    )
    resultado: list[tuple[int, str]] = []
    limite_anterior: Optional[int] = None
    for i, (lin_m, col_m, periodo) in enumerate(marcadores):
        proximo = marcadores[i + 1][0] if i + 1 < len(marcadores) else None
        cabecalhos = [
            col for col, lin, v in celulas
            # Cabeçalho de outra seção fica na linha do marcador ou na de
            # cima — texto ABAIXO (ex.: "Folga 1x1 ...") encerra a lista,
            # não limita a coluna.
            if col > col_m and lin_m - 1 <= lin <= lin_m and not _eh_numero(v)
            and _texto(v) not in _MARCADOR_PERIODO
        ]
        limite = min(cabecalhos) if cabecalhos else limite_anterior
        limite_anterior = limite
        linha = lin_m
        vazias = 0
        while proximo is None or linha < proximo:
            na_linha = [(col, v) for col, lin, v in celulas
                        if lin == linha and col >= col_m and (limite is None or col < limite)]
            # Encerra a lista um texto que começa na coluna do marcador ou na
            # seguinte (ex.: "FOLGA 2X2 ..."). Rótulo solto mais à direita
            # (ex.: "TS" perdido no meio da lista) não encerra nada.
            if linha > lin_m and any(not _eh_numero(v) and col <= col_m + 1 for col, v in na_linha):
                break
            numeros = [v for col, v in na_linha if col > col_m and _eh_numero(v)]
            if not numeros:
                vazias += 1
                if vazias > 2:
                    break
            else:
                vazias = 0
            resultado.extend((periodo, normalizar_re(v)) for v in numeros)
            linha += 1
    return resultado


def _fechar(problemas, coberturas, modelos, res_vistos, abas_ignoradas,
            descartadas, rodapes, resolver_nomes, modelos_existentes, postos_existentes) -> Analise:
    sugestoes, trocas = sugerir_folgas(rodapes, modelos) if modelos else ([], [])
    todos = sorted(set(res_vistos) | {s["re"] for s in sugestoes})
    nomes = resolver_nomes(todos) if todos else {}

    # D-A: RE sem cadastro é aceito — a lista só avisa quem ainda não tem nome.
    sem_cadastro = [
        {"re": r, "onde": res_vistos[r]}
        for r in sorted(res_vistos, key=lambda r: (len(r), r)) if r not in nomes
    ]
    for s in sugestoes:
        s["nome"] = nomes.get(s["re"])
        s["cadastrado"] = s["re"] in nomes

    for m in modelos:
        if m["ja_existe_com_postos"]:
            problemas.append({
                "tipo": "modelo_ja_importado", "bloqueia": True, "modelo": m["aba"],
                "mensagem": f"O modelo \"{m['nome']}\" já tem postos gravados. A importação não "
                            f"sobrescreve: tire esta aba do arquivo ou esvazie o modelo antes.",
            })

    chaves = {c.chave_posto for c in coberturas}
    bloqueantes = sum(1 for p in problemas if p["bloqueia"])
    escalados = [c for c in coberturas if c.situacao == "escalado"]
    relatorio = {
        "pode_confirmar": bloqueantes == 0 and bool(coberturas),
        "resumo": {
            "modelos": len(modelos),
            "coberturas": len(coberturas),
            "postos_distintos": len(chaves),
            "postos_novos": len(chaves - postos_existentes),
            "postos_ja_cadastrados": len(chaves & postos_existentes),
            "escalado": len(escalados),
            "escalado_com_cadastro": sum(1 for c in escalados if c.re in nomes),
            "escalado_sem_cadastro": sum(1 for c in escalados if c.re not in nomes),
            "outra_garagem": sum(1 for c in coberturas if c.situacao == "outra_garagem"),
            "descoberto": sum(1 for c in coberturas if c.situacao == "descoberto"),
            "em_branco": sum(1 for c in coberturas if c.marcador == ""),
            "direto": sum(1 for c in coberturas if c.situacao == "direto"),
            "ajustados_para_o_padrao": sum(1 for p in problemas if p["tipo"] == "ajustado_padrao"),
            "sem_horario_na_planilha": sum(1 for c in coberturas if c.hora_inicio is None),
            "problemas_bloqueantes": bloqueantes,
            "avisos": len(problemas) - bloqueantes,
            "linhas_descartadas": len(descartadas),
        },
        "modelos": [
            {**m, "coberturas": sum(1 for c in coberturas if c.aba == m["aba"])} for m in modelos
        ],
        "problemas": problemas,
        "re_sem_cadastro": sem_cadastro,
        "sugestao_folga": sugestoes,
        "trocas_encontradas": trocas,
        "linhas_descartadas": descartadas,
        "abas_ignoradas": abas_ignoradas,
    }
    return Analise(relatorio=relatorio, coberturas=coberturas, modelos=modelos)


# ─── Gravação ────────────────────────────────────────────────────────────────

def gravar(db: Session, analise: Analise) -> dict:
    """Grava o plano de uma análise sem problema bloqueante. Não faz commit
    — quem chama commita (tudo ou nada). Reaproveita posto já cadastrado com
    a mesma ponta + linhas; nunca cria ponto final."""
    if not analise.pode_confirmar:
        raise ValueError("Análise com problema bloqueante — não grava.")

    postos_por_chave = _postos_por_chave(db)
    criados = 0
    for c in analise.coberturas:
        if c.chave_posto in postos_por_chave:
            continue
        cod_jb, lote = analise.dados_postos.get(c.chave_posto, (c.cod_jb, c.lote))
        posto = EscalaFiscalPosto(lado=c.lado, cod_jb=cod_jb, lote=lote, ativo=True)
        posto.linhas = [EscalaFiscalPostoLinha(linha=ln, ordem=i + 1) for i, ln in enumerate(c.linhas)]
        db.add(posto)
        db.flush()
        postos_por_chave[c.chave_posto] = posto.id
        criados += 1

    modelos_por_nome = {
        m.nome: m for m in db.execute(select(EscalaFiscalModelo)).scalars().all()
    }
    ids_modelo = {}
    for m in analise.modelos:
        modelo = modelos_por_nome.get(m["nome"])
        if modelo is None:
            modelo = EscalaFiscalModelo(tipo_dia=m["tipo_dia"], paridade=m["paridade"], nome=m["nome"])
            db.add(modelo)
            db.flush()
            modelos_por_nome[m["nome"]] = modelo
        ids_modelo[m["aba"]] = modelo.id

    for c in analise.coberturas:
        db.add(EscalaFiscalModeloPosto(
            modelo_id=ids_modelo[c.aba], posto_id=postos_por_chave[c.chave_posto],
            ordem=c.ordem, periodo=c.periodo, hora_inicio=c.hora_inicio, hora_termino=c.hora_termino,
            situacao_padrao=c.situacao, re_padrao=c.re, outra_garagem=c.outra_garagem,
            marcador=c.marcador,
        ))
    db.flush()
    return {
        "modelos": len(analise.modelos),
        "postos_criados": criados,
        "coberturas_gravadas": len(analise.coberturas),
    }


def _postos_por_chave(db: Session) -> dict[tuple, UUID]:
    return {
        posto_chave(p.lado, [pl.linha for pl in p.linhas]): p.id
        for p in db.execute(select(EscalaFiscalPosto)).scalars().all()
    }


def chaves_postos_existentes(db: Session) -> set:
    return set(_postos_por_chave(db))


def modelos_com_postos(db: Session) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for modelo in db.execute(select(EscalaFiscalModelo)).scalars().all():
        qtd = db.execute(
            select(EscalaFiscalModeloPosto.id).where(EscalaFiscalModeloPosto.modelo_id == modelo.id)
        ).first()
        contagem[modelo.nome] = 1 if qtd else 0
    return contagem
