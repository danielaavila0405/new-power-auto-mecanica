import sqlite3
from pathlib import Path


# ==================================================
# LOCAL DO BANCO
# ==================================================

pasta_projeto = Path(__file__).parent
banco = pasta_projeto / "oficina.db"


conexao = sqlite3.connect(banco)

# Ativar integridade das relações entre tabelas
conexao.execute("PRAGMA foreign_keys = ON")

cursor = conexao.cursor()


# ==================================================
# TABELA DE CLIENTES
# ==================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS clientes (
    id_cliente INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    telefone TEXT NOT NULL,
    cpf_cnpj TEXT
)
""")


# ==================================================
# TABELA DE VEÍCULOS
# ==================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS veiculos (
    id_veiculo INTEGER PRIMARY KEY AUTOINCREMENT,
    id_cliente INTEGER NOT NULL,
    placa TEXT NOT NULL,
    marca TEXT,
    modelo TEXT,
    ano INTEGER,
    quilometragem INTEGER,

    FOREIGN KEY (id_cliente)
        REFERENCES clientes(id_cliente)
)
""")


# ==================================================
# TABELA DE ORDENS DE SERVIÇO
# ==================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS ordens_servico (
    id_os INTEGER PRIMARY KEY AUTOINCREMENT,
    id_cliente INTEGER NOT NULL,
    id_veiculo INTEGER NOT NULL,
    data TEXT NOT NULL,
    responsavel TEXT NOT NULL,
    forma_pagamento TEXT NOT NULL,
    valor_mao_obra REAL NOT NULL,
    valor_pecas REAL NOT NULL,
    valor_total REAL NOT NULL,
    valor_repasse REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Aberta',

    FOREIGN KEY (id_cliente)
        REFERENCES clientes(id_cliente),

    FOREIGN KEY (id_veiculo)
        REFERENCES veiculos(id_veiculo)
)
""")


# ==================================================
# ADICIONAR STATUS EM BANCO JÁ EXISTENTE
# ==================================================

cursor.execute("""
PRAGMA table_info(ordens_servico)
""")

colunas = cursor.fetchall()

nomes_colunas = [
    coluna[1]
    for coluna in colunas
]


if "status" not in nomes_colunas:

    cursor.execute("""
        ALTER TABLE ordens_servico
        ADD COLUMN status TEXT NOT NULL
        DEFAULT 'Aberta'
    """)


# ==================================================
# TABELA DE PEÇAS DA OS
# ==================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS pecas_os (
    id_peca_os INTEGER PRIMARY KEY AUTOINCREMENT,
    id_os INTEGER NOT NULL,
    descricao TEXT NOT NULL,
    quantidade INTEGER NOT NULL,
    valor_unitario REAL NOT NULL,
    valor_total REAL NOT NULL,

    FOREIGN KEY (id_os)
        REFERENCES ordens_servico(id_os)
)
""")


# ==================================================
# TABELA DE SERVIÇOS DA OS
# ==================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS servicos_os (
    id_servico INTEGER PRIMARY KEY AUTOINCREMENT,
    id_os INTEGER NOT NULL,
    descricao TEXT NOT NULL,
    valor REAL NOT NULL,

    FOREIGN KEY (id_os)
        REFERENCES ordens_servico(id_os)
)
""")

# ==================================================
# TABELA DE USUÁRIOS
# ==================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS usuarios (
    id_usuario INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    usuario TEXT NOT NULL UNIQUE,
    senha TEXT NOT NULL,
    perfil TEXT NOT NULL DEFAULT 'usuario'
)
""")

# ==================================================
# SALVAR ALTERAÇÕES
# ==================================================

conexao.commit()

conexao.close()


print("Banco de dados atualizado com sucesso!")