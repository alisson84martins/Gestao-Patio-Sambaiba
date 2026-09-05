"""Endpoints de recolhida anormal (Blocos F + G + H) — ônibus que recolhe
fora de hora.

🔴 REGRA NÚMERO UM (mesma do resto do módulo): o sistema NUNCA impede um
registro. Prefixo não cadastrado, escala não encontrada, ficha que não pôde
nascer — registra assim mesmo e sinaliza. POST /recolhidas sempre responde
201 quando o payload é válido.

🔴 A REGRA DO MOTORISTA (corrigida em 21/08 — §2.9-0): o controlador DIGITA
motorista_re/cobrador_re — ele está com o carro na frente, é a melhor fonte
do dado. A separação da regra número um não é sobre o campo, é sobre o
ACUMULADO: esta tela nunca devolve histórico, agregado ou ranking pra quem
só tem `recolhida_anormal` — isso é gerencial (`recolhida_gerencial`). A
escala entra só como SUGESTÃO de pré-preenchimento pro RE do motorista
(nunca cobrador — a tabela escala não tem esse campo) — nunca fonte única,
nunca trava o registro.

🔧 BLOCO G: motivo é mais amplo que "defeito" — colisão e falta de
motorista/cobrador também são recolhida anormal. Só motivo=DEFEITO abre
ficha de manutenção automaticamente.

FINALIDADE DO DADO: melhoria de processo e de frota. A associação
motorista↔defeito serve pra encontrar padrão de operação e necessidade de
treinamento — o dado é do VEÍCULO, não da pessoa.

🔧 BLOCO H: todo RE motorista/cobrador digitado alimenta o pré-cadastro
de pessoas (services/pre_cadastro.py) — nunca cria acesso ao sistema,
nunca bloqueia o registro da recolhida.

🔧 MIGRATION 037 (Bloco I, 24/08) — REGISTRAR × TRATAR: avaliação e
encerramento passam a exigir `recolhida_tratativa` (não mais `manutencao`,
que é recurso do Pátio). `recolhida_anormal` continua sendo só quem
REGISTRA (controlador) — GET /recolhidas aceita as duas (ver
LeituraRecolhidaOuTratativa) porque a aba RA da manutenção usa o mesmo
endpoint pra "Encerradas hoje".

🔧 MIGRATION 032 — ENCERRAMENTO: fecha o ciclo que a avaliação deixava em
aberto pra sempre. Dois passos de propósito (avaliar != encerrar, são
momentos diferentes na operação real): a avaliação diz se o carro volta,
o encerramento diz se havia defeito de verdade. PATCH /encerramento exige
status=AVALIADA (409 fora disso) e espelha o desfecho na ficha_manutencao
que a própria recolhida abriu, quando existe (services/manutencao_recolhida.py
— regra número um: ficha_id nulo não impede o encerramento).
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.core.database import get_db
from app.core.deps import exige, exige_qualquer
from app.core.registro import normalizar_re
from app.models.cadastro import Funcionario
from app.models.frota import Onibus
from app.models.operacoes import Escala
from app.models.pessoas import Motorista
from app.models.portaria import RecolhidaAnormal
from app.schemas.portaria import (
    ContagemPendentesResponse, RecolhidaAnaliseItem, RecolhidaAnaliseResponse,
    RecolhidaAvaliacaoRequest, RecolhidaCreate, RecolhidaEncerramentoRequest,
    RecolhidaGerencialRead, RecolhidaRead, ResolverPrefixoResponse, StatusRecolhida,
)
from app.services.manutencao_recolhida import abrir_ficha_de_recolhida, encerrar_ficha_de_recolhida
from app.services.pre_cadastro import registrar_pessoa_vista

router = APIRouter(prefix="/portaria", tags=["portaria"])

LeituraRecolhida = Annotated[Funcionario, Depends(exige("recolhida_anormal"))]
EscritaRecolhida = Annotated[Funcionario, Depends(exige("recolhida_anormal", escrever=True))]
LeituraGerencial = Annotated[Funcionario, Depends(exige("recolhida_gerencial"))]
# 🔴 Migration 037 (Bloco I) — recolhida_tratativa é quem TRATA (mecânico
# avalia/encerra), separado de recolhida_anormal, que é quem REGISTRA
# (controlador). Antes disto a avaliação/encerramento exigiam `manutencao`
# — um recurso do Pátio que também dava ao mecânico o card PORTARIA
# inteiro na tela de seleção (bug corrigido pela mesma migration).
LeituraTratativa = Annotated[Funcionario, Depends(exige("recolhida_tratativa"))]
EscritaTratativa = Annotated[Funcionario, Depends(exige("recolhida_tratativa", escrever=True))]
# GET /recolhidas (histórico bruto) é lido pelas DUAS pontas: o controlador
# vê o que registrou (recolhida_anormal) e a aba RA da manutenção usa este
# mesmo endpoint pra "Encerradas hoje" (recolhida_tratativa) — sem o OR
# aqui, a separação REGISTRAR×TRATAR quebra essa lista pro mecânico, que
# não tem mais recolhida_anormal depois da 037.
LeituraRecolhidaOuTratativa = Annotated[
    Funcionario, Depends(exige_qualquer("recolhida_anormal", "recolhida_tratativa"))
]

# §2.9-A: teto de sanidade pra sugestão de motorista pela escala — não
# pescar escala velha de um ônibus que ficou parado.
_TETO_SUGESTAO_ESCALA = timedelta(hours=20)


# ============================================================================
# Resolução de prefixo -> ônibus e de escala -> sugestão de motorista.
# Privadas deste router de propósito — regra de fronteira: portaria não
# importa lógica de outro router (mesma convenção duplicada, não
# compartilhada, de routers/ocorrencias.py:normalizar_prefixo).
# ============================================================================

def _resolver_onibus_por_prefixo(db: Session, prefixo: str) -> Optional[Onibus]:
    digitos = prefixo.strip()
    if not digitos.isdigit():
        return None
    if len(digitos) == 5 and digitos[0] == "2":
        numero = int(digitos[1:])
    elif len(digitos) == 4:
        numero = int(digitos)
    else:
        return None
    if not (1000 <= numero <= 2999):
        return None
    return db.execute(select(Onibus).where(Onibus.numero_frota == numero)).scalar_one_or_none()


def _sugerir_motorista_pela_escala(
    db: Session, onibus_id: UUID, momento_local: datetime
) -> Optional[Motorista]:
    """Escala em curso no INSTANTE da recolhida — só pra pré-preencher o
    campo, nunca resolve sozinha (§2.9-0).

    ⚠️ §2.9-A: nunca compara só hora de relógio solta. Ônibus que sai às
    23:00 e recolhe às 00:30 tem a escala em curso no dia ANTERIOR —
    comparar `horario_saida <= hora` sem considerar o dia descartava
    exatamente essa janela (a virada da madrugada, quando mais importa).
    Busca escalas do dia da recolhida E do dia anterior, monta o instante
    real de cada partida (Escala.data já é a data real da planilha, não
    passa pela regra das 20h do Pátio), e escolhe a mais recente que seja
    <= o momento da recolhida, com teto de 20h. Sem candidato -> não
    resolve (não chuta). Empate de horário -> mantém a ordenação da query
    (mais recente primeiro), nunca vira exceção.
    """
    data_referencia = momento_local.date()
    escalas = db.execute(
        select(Escala)
        .where(
            Escala.onibus_id == onibus_id,
            Escala.data.in_([data_referencia, data_referencia - timedelta(days=1)]),
            Escala.deletado_em.is_(None),
        )
        .order_by(Escala.data.desc(), Escala.horario_saida.desc())
    ).scalars().all()

    melhor: Optional[Escala] = None
    melhor_instante: Optional[datetime] = None
    for escala in escalas:
        instante = datetime.combine(escala.data, escala.horario_saida, tzinfo=FUSO_OPERACAO)
        if instante > momento_local:
            continue
        if momento_local - instante > _TETO_SUGESTAO_ESCALA:
            continue
        if melhor_instante is None or instante > melhor_instante:
            melhor, melhor_instante = escala, instante

    if melhor is None or melhor.motorista_id is None:
        return None
    return db.get(Motorista, melhor.motorista_id)


def _decidir_origem_identificacao(
    motorista_re: Optional[str], cobrador_re: Optional[str], sugestao: Optional[Motorista]
) -> str:
    """PORTARIA/ESCALA/NAO_INFORMADO (§2.9-0) — um único campo cobre
    motorista+cobrador; a leitura documentada é sobre o MOTORISTA (é o que
    tem sugestão possível). NAO_INFORMADO só quando os dois campos vieram
    em branco; ESCALA quando o motorista digitado bate com a sugestão da
    escala (confirmou sem alterar); PORTARIA no resto (digitou por conta
    própria, ou alterou o que a escala sugeriu)."""
    if not motorista_re and not cobrador_re:
        return "NAO_INFORMADO"
    if motorista_re and sugestao is not None and motorista_re == normalizar_re(sugestao.re):
        return "ESCALA"
    return "PORTARIA"


# ============================================================================
# FILA/CONTAGEM/GERENCIAL/ANÁLISE — registrados ANTES de qualquer rota com
# {recolhida_id}, mesma convenção do resto do módulo.
# ============================================================================

@router.get(
    "/recolhidas/resolver-prefixo",
    response_model=ResolverPrefixoResponse,
    summary="Mostra 'cadastrado'/'não cadastrado' e sugere o RE do motorista pela escala (§2.6, §2.9-0)",
)
def resolver_prefixo(
    usuario: LeituraRecolhida,
    db: Annotated[Session, Depends(get_db)],
    prefixo: str = Query(..., min_length=1, max_length=10),
):
    onibus = _resolver_onibus_por_prefixo(db, prefixo)
    if onibus is None:
        return ResolverPrefixoResponse(encontrado=False)
    sugestao = _sugerir_motorista_pela_escala(db, onibus.id, datetime.now(FUSO_OPERACAO))
    return ResolverPrefixoResponse(
        encontrado=True,
        placa=onibus.placa,
        motorista_re_sugerido=sugestao.re if sugestao else None,
        motorista_nome_sugerido=sugestao.nome if sugestao else None,
    )


#  AGUARDANDO (falta avaliar) e AVALIADA (falta encerrar) — migration 032:
# as duas ainda exigem ação da manutenção, então pendentes/contagem passam
# a somar as duas. Antes só existia AGUARDANDO; ampliar aqui é seguro
# porque nenhuma outra tela do sistema consome estes dois endpoints (só a
# aba RA de manutencao.html) — não há leitor esperando a semântica antiga.
_STATUS_PENDENTES = ("AGUARDANDO", "AVALIADA")


@router.get(
    "/recolhidas/pendentes",
    response_model=list[RecolhidaRead],
    summary="Fila da manutenção — AGUARDANDO (falta avaliar) + AVALIADA (falta encerrar), mais recente no topo",
)
def listar_pendentes(usuario: LeituraTratativa, db: Annotated[Session, Depends(get_db)]):
    return db.execute(
        select(RecolhidaAnormal)
        .where(RecolhidaAnormal.status.in_(_STATUS_PENDENTES))
        .order_by(RecolhidaAnormal.momento.desc())
    ).scalars().all()


@router.get(
    "/recolhidas/contagem-pendentes",
    response_model=ContagemPendentesResponse,
    summary="Total de AGUARDANDO + AVALIADA — o alerta da fila",
)
def contar_pendentes(usuario: LeituraTratativa, db: Annotated[Session, Depends(get_db)]):
    total = len(db.execute(
        select(RecolhidaAnormal.id).where(RecolhidaAnormal.status.in_(_STATUS_PENDENTES))
    ).scalars().all())
    return ContagemPendentesResponse(total=total)


@router.get(
    "/recolhidas/gerencial",
    response_model=list[RecolhidaGerencialRead],
    summary="Visão gerencial — com motorista/cobrador/histórico. exige recolhida_gerencial",
)
def listar_gerencial(
    usuario: LeituraGerencial,
    db: Annotated[Session, Depends(get_db)],
    status_filtro: Optional[StatusRecolhida] = Query(None, alias="status"),
    data: Optional[date] = None,
):
    stmt = select(RecolhidaAnormal)
    if status_filtro:
        stmt = stmt.where(RecolhidaAnormal.status == status_filtro)
    if data:
        stmt = stmt.where(RecolhidaAnormal.data_referencia == data)
    stmt = stmt.order_by(RecolhidaAnormal.momento.desc())
    return db.execute(stmt).scalars().all()


@router.get(
    "/recolhidas/analise",
    response_model=RecolhidaAnaliseResponse,
    summary="Agregados por período — melhoria de processo e frota, nunca avaliação de pessoa (§2.7)",
)
def analise_recolhidas(
    usuario: LeituraGerencial,
    db: Annotated[Session, Depends(get_db)],
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
):
    stmt = select(RecolhidaAnormal)
    if data_inicio:
        stmt = stmt.where(RecolhidaAnormal.data_referencia >= data_inicio)
    if data_fim:
        stmt = stmt.where(RecolhidaAnormal.data_referencia <= data_fim)
    linhas = db.execute(stmt).scalars().all()

    def _ordenado(contador: Counter) -> list[RecolhidaAnaliseItem]:
        return [RecolhidaAnaliseItem(chave=chave, total=total) for chave, total in contador.most_common()]

    def _faixa_horario(momento: datetime) -> str:
        m = momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
        hora = m.astimezone(FUSO_OPERACAO).hour
        inicio = (hora // 4) * 4
        return f"{inicio:02d}h–{(inicio + 4) % 24:02d}h"

    por_prefixo = Counter(r.prefixo for r in linhas)
    por_linha = Counter(r.linha_codigo or "—" for r in linhas)
    por_motorista = Counter(
        f"{r.motorista_re} · {r.motorista_nome}" for r in linhas if r.motorista_re
    )
    por_tipo_defeito = Counter(r.tipo_defeito_codigo for r in linhas if r.tipo_defeito_codigo)
    por_faixa_horario = Counter(_faixa_horario(r.momento) for r in linhas)
    # Bloco G — separa problema de frota (DEFEITO/COLISAO) de problema de
    # escala (FALTA_MOTORISTA/FALTA_COBRADOR).
    por_motivo = Counter(r.motivo for r in linhas)

    avaliadas = [r for r in linhas if r.avaliado_em is not None]
    tempo_medio: Optional[float] = None
    if avaliadas:
        total_segundos = 0.0
        for r in avaliadas:
            momento = r.momento if r.momento.tzinfo else r.momento.replace(tzinfo=timezone.utc)
            avaliado_em = r.avaliado_em if r.avaliado_em.tzinfo else r.avaliado_em.replace(tzinfo=timezone.utc)
            total_segundos += (avaliado_em - momento).total_seconds()
        tempo_medio = round(total_segundos / len(avaliadas) / 60, 1)

    return RecolhidaAnaliseResponse(
        por_prefixo=_ordenado(por_prefixo),
        por_linha=_ordenado(por_linha),
        por_motorista=_ordenado(por_motorista),
        por_tipo_defeito=_ordenado(por_tipo_defeito),
        por_faixa_horario=_ordenado(por_faixa_horario),
        por_motivo=_ordenado(por_motivo),
        tempo_medio_avaliacao_minutos=tempo_medio,
    )


# ============================================================================
# REGISTRO — o coração da regra número um deste bloco.
# ============================================================================

@router.post(
    "/recolhidas",
    response_model=RecolhidaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registra recolhida anormal — nunca recusa por prefixo/escala/ficha ausentes",
)
def registrar_recolhida(
    payload: RecolhidaCreate, usuario: EscritaRecolhida, db: Annotated[Session, Depends(get_db)]
):
    agora_local = datetime.now(FUSO_OPERACAO)
    data_referencia = agora_local.date()

    onibus = _resolver_onibus_por_prefixo(db, payload.prefixo)

    sugestao = _sugerir_motorista_pela_escala(db, onibus.id, agora_local) if onibus is not None else None
    origem_identificacao = _decidir_origem_identificacao(
        payload.motorista_re, payload.cobrador_re, sugestao
    )
    motorista_nome = payload.motorista_nome
    if origem_identificacao == "ESCALA" and not motorista_nome:
        motorista_nome = sugestao.nome

    # Ficha só recebe motorista_id quando a identificação bateu com a
    # escala — é o único caso em que temos uma linha verificada na tabela
    # legada `motorista` por trás do RE (Bloco G: só quando motivo=DEFEITO).
    motorista_id_para_ficha = sugestao.id if origem_identificacao == "ESCALA" else None

    # 🔧 Bloco G: só motivo=DEFEITO abre ficha de manutenção automática —
    # os demais (colisão, falta de motorista/cobrador, outro) não são
    # ordem de serviço; isso não é falha, é o comportamento correto.
    if payload.motivo == "DEFEITO":
        ficha_id, ficha_falhou_motivo = abrir_ficha_de_recolhida(
            db,
            onibus_id=onibus.id if onibus is not None else None,
            motorista_id=motorista_id_para_ficha,
            tipo_defeito_codigo=payload.tipo_defeito_codigo,
            relato=payload.relato,
        )
    else:
        ficha_id, ficha_falhou_motivo = None, f"motivo {payload.motivo} — não gera ordem de serviço."

    nova = RecolhidaAnormal(
        data_referencia=data_referencia,
        prefixo=payload.prefixo,
        onibus_id=onibus.id if onibus is not None else None,
        linha_codigo=payload.linha_codigo,
        motivo=payload.motivo,
        tipo_defeito_codigo=payload.tipo_defeito_codigo,
        relato=payload.relato,
        motorista_re=payload.motorista_re,
        motorista_nome=motorista_nome,
        cobrador_re=payload.cobrador_re,
        cobrador_nome=payload.cobrador_nome,
        origem_identificacao=origem_identificacao,
        ficha_id=ficha_id,
        ficha_falhou_motivo=ficha_falhou_motivo,
        registrado_por=usuario.id,
    )
    db.add(nova)

    # Bloco H (§5.2): todo RE digitado alimenta o pré-cadastro — nunca
    # bloqueia (registrar_pessoa_vista nunca propaga exceção). RE em
    # branco é ignorado silenciosamente dentro do próprio serviço.
    registrar_pessoa_vista(
        db, re=payload.motorista_re, papel="MOTORISTA", origem="PORTARIA_RECOLHIDA",
        nome=payload.motorista_nome,
    )
    registrar_pessoa_vista(
        db, re=payload.cobrador_re, papel="COBRADOR", origem="PORTARIA_RECOLHIDA",
        nome=payload.cobrador_nome,
    )

    db.commit()
    db.refresh(nova)
    return nova


@router.get(
    "/recolhidas",
    response_model=list[RecolhidaRead],
    summary="Histórico com filtros (status, data)",
)
def listar_recolhidas(
    usuario: LeituraRecolhidaOuTratativa,
    db: Annotated[Session, Depends(get_db)],
    status_filtro: Optional[StatusRecolhida] = Query(None, alias="status"),
    data: Optional[date] = None,
):
    stmt = select(RecolhidaAnormal)
    if status_filtro:
        stmt = stmt.where(RecolhidaAnormal.status == status_filtro)
    if data:
        stmt = stmt.where(RecolhidaAnormal.data_referencia == data)
    stmt = stmt.order_by(RecolhidaAnormal.momento.desc())
    return db.execute(stmt).scalars().all()


@router.patch(
    "/recolhidas/{recolhida_id}/avaliacao",
    response_model=RecolhidaRead,
    summary="Mecânico avalia: LIBERADO (com prazo) ou RETIDO",
)
def avaliar_recolhida(
    recolhida_id: UUID,
    payload: RecolhidaAvaliacaoRequest,
    usuario: EscritaTratativa,
    db: Annotated[Session, Depends(get_db)],
):
    recolhida = db.get(RecolhidaAnormal, recolhida_id)
    if recolhida is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recolhida não encontrada")

    recolhida.avaliacao = payload.avaliacao
    recolhida.prazo_minutos = payload.prazo_minutos if payload.avaliacao == "LIBERADO" else None
    recolhida.avaliacao_relato = payload.avaliacao_relato
    recolhida.avaliado_por = usuario.id
    recolhida.avaliado_em = datetime.now(timezone.utc)
    recolhida.status = "AVALIADA"

    db.commit()
    db.refresh(recolhida)
    return recolhida


@router.patch(
    "/recolhidas/{recolhida_id}/encerramento",
    response_model=RecolhidaRead,
    summary="Mecânico encerra: SEM_DEFEITO ou SERVICO_EXECUTADO — fecha o ciclo",
)
def encerrar_recolhida(
    recolhida_id: UUID,
    payload: RecolhidaEncerramentoRequest,
    usuario: EscritaTratativa,
    db: Annotated[Session, Depends(get_db)],
):
    recolhida = db.get(RecolhidaAnormal, recolhida_id)
    if recolhida is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recolhida não encontrada")

    # Dois passos de propósito (§3.3 do prompt de 23/08): triagem
    # (avaliação) e encerramento são momentos diferentes na operação real.
    # Exige AVALIADA — cobre não só AGUARDANDO/ENCERRADA (as duas mensagens
    # do prompt) como também DESCARTADA, valor previsto no CHECK da 026 mas
    # sem endpoint nenhum que o grave hoje (nenhum leitor real a esperar).
    if recolhida.status != "AVALIADA":
        if recolhida.status == "AGUARDANDO":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Avalie a recolhida antes de encerrar.")
        if recolhida.status == "ENCERRADA":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Recolhida já foi encerrada.")
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Recolhida está {recolhida.status}, não pode ser encerrada.",
        )

    ficha_falhou_motivo = encerrar_ficha_de_recolhida(
        db, ficha_id=recolhida.ficha_id, desfecho=payload.desfecho,
    )
    # Regra número um: nada do lado da ficha impede o encerramento da RA —
    # só sobrescreve ficha_falhou_motivo quando a atualização falhou de
    # verdade; sucesso ou ficha_id nulo (encerrar_ficha_de_recolhida
    # devolve None nos dois casos) preserva o motivo que já existia desde o
    # registro (ex.: "motivo COLISAO — não gera ordem de serviço").
    if ficha_falhou_motivo is not None:
        recolhida.ficha_falhou_motivo = ficha_falhou_motivo

    recolhida.desfecho = payload.desfecho
    recolhida.encerramento_relato = payload.encerramento_relato
    recolhida.encerrado_por = usuario.id
    recolhida.encerrado_em = datetime.now(timezone.utc)
    recolhida.status = "ENCERRADA"

    db.commit()
    db.refresh(recolhida)
    return recolhida
