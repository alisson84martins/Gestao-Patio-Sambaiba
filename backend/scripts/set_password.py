"""Script utilitário para definir senha de um usuário pelo RE.

Uso (com venv ativo, da pasta backend/):
    python scripts/set_password.py ADMIN001 minha_senha_nova
"""
import sys
from pathlib import Path

# Adiciona backend/ ao sys.path para imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import Usuario


def main():
    if len(sys.argv) != 3:
        print("Uso: python scripts/set_password.py <RE> <nova_senha>")
        sys.exit(1)

    re = sys.argv[1]
    nova_senha = sys.argv[2]

    if len(nova_senha) < 6:
        print("ERRO: senha deve ter no mínimo 6 caracteres")
        sys.exit(1)

    with SessionLocal() as db:
        user = db.execute(select(Usuario).where(Usuario.re == re)).scalar_one_or_none()
        if user is None:
            print(f"ERRO: usuário com RE='{re}' não encontrado")
            sys.exit(1)

        user.senha_hash = hash_password(nova_senha)
        db.commit()
        print(f"Senha de '{user.nome}' (RE={user.re}, perfil={user.perfil.value}) atualizada com sucesso.")


if __name__ == "__main__":
    main()
