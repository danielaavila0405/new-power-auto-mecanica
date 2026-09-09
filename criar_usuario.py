import sqlite3
from pathlib import Path
from getpass import getpass

from werkzeug.security import generate_password_hash


# ============================================================
# LOCAL DO BANCO
# ============================================================

pasta_projeto = Path(__file__).parent
banco = pasta_projeto / "oficina.db"


# ============================================================
# CONECTAR AO BANCO
# ============================================================

conexao = sqlite3.connect(banco)

cursor = conexao.cursor()


# ============================================================
# DADOS DO USUÁRIO
# ============================================================

print()
print("==========================================")
print("   CRIAR USUÁRIO ADMINISTRADOR")
print("==========================================")
print()

nome = input("Nome: ").strip()

usuario = input("Usuário para login: ").strip()

senha = getpass("Senha: ")

confirmacao = getpass("Confirme a senha: ")


# ============================================================
# VALIDAR DADOS
# ============================================================

if not nome:

    print()
    print("Erro: informe o nome.")

    conexao.close()

    raise SystemExit


if not usuario:

    print()
    print("Erro: informe o usuário.")

    conexao.close()

    raise SystemExit


if not senha:

    print()
    print("Erro: informe a senha.")

    conexao.close()

    raise SystemExit


if senha != confirmacao:

    print()
    print("Erro: as senhas não coincidem.")

    conexao.close()

    raise SystemExit


# ============================================================
# VERIFICAR SE USUÁRIO JÁ EXISTE
# ============================================================

cursor.execute("""
    SELECT id_usuario
    FROM usuarios
    WHERE usuario = ?
""", (usuario,))

usuario_existente = cursor.fetchone()


if usuario_existente:

    print()
    print("Erro: este usuário já existe.")

    conexao.close()

    raise SystemExit


# ============================================================
# GERAR HASH DA SENHA
# ============================================================

senha_hash = generate_password_hash(
    senha
)


# ============================================================
# INSERIR USUÁRIO
# ============================================================

cursor.execute("""
    INSERT INTO usuarios
    (
        nome,
        usuario,
        senha,
        perfil
    )
    VALUES (?, ?, ?, ?)
""", (
    nome,
    usuario,
    senha_hash,
    "admin"
))


conexao.commit()

conexao.close()


# ============================================================
# FINAL
# ============================================================

print()
print("Usuário administrador criado com sucesso!")
print()
print(f"Nome: {nome}")
print(f"Usuário: {usuario}")
print("Senha: cadastrada com segurança.")
print("Perfil: admin")
print()