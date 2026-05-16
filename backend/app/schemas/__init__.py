"""Schemas Pydantic da API."""
from app.schemas.catalogos import (
    LinhaCreate, LinhaRead, LinhaUpdate,
    PermissaoCreate, PermissaoRead, PermissaoUpdate,
    TipoDefeitoCreate, TipoDefeitoRead, TipoDefeitoUpdate,
)
from app.schemas.frota import (
    AlocacaoBlocoCreate,
    AlocacaoPatioCreate, AlocacaoPatioRead, AlocacaoPatioUpdate,
    FilaCreate, FilaRead, FilaUpdate,
    OnibusCreate, OnibusRead, OnibusUpdate,
)
from app.schemas.importacao import (
    ErroLinha, ImportacaoUploadResponse,
)
from app.schemas.operacoes import (
    AlertaCreate, AlertaRead, AlertaResolver,
    EscalaCreate, EscalaRead, EscalaUpdate,
    FichaManutencaoCreate, FichaManutencaoRead, FichaManutencaoUpdate,
    ImportacaoEscalaCreate, ImportacaoEscalaRead,
)
from app.schemas.pessoas import (
    MotoristaCreate, MotoristaRead, MotoristaUpdate,
    UsuarioCreate, UsuarioRead, UsuarioUpdate,
)
