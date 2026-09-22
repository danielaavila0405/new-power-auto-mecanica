from flask import (
    Flask,
    render_template,
    request,
    redirect,
    jsonify,
    send_file,
    send_from_directory,
    session,
    g,
    Response
)
import sqlite3
import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
import json
import re
import csv
import io
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime, date, timedelta
from io import BytesIO

from werkzeug.security import generate_password_hash, check_password_hash

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image
)
from reportlab.pdfgen import canvas

app = Flask(__name__)

# ============================================================
# CHAVE DE SEGURANÇA DA SESSÃO
# ============================================================

app.secret_key = os.environ.get(
    "NEW_POWER_SECRET_KEY",
    "chave-temporaria-new-power-2026"
)

pasta_projeto = Path(__file__).parent

# Em desenvolvimento/local: usa oficina.db ao lado deste arquivo.
# Em produção (Railway): use NEW_POWER_DATABASE para apontar para o
# caminho do banco dentro do volume persistente, por exemplo:
# /app/data/oficina.db
CAMINHO_BANCO = os.environ.get(
    "NEW_POWER_DATABASE",
    str(pasta_projeto / "oficina.db")
)

banco = Path(CAMINHO_BANCO)
banco.parent.mkdir(parents=True, exist_ok=True)

# ============================================================
# DIRETÓRIOS DE LOGS E BACKUPS
# ============================================================
LOGS_DIR = pasta_projeto / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "app.log"

BACKUPS_DIR = pasta_projeto / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# LOGGING ESTRUTURADO E OBSERVABILIDADE
# ============================================================
logger = logging.getLogger("new_power")
logger.setLevel(logging.INFO)

if not logger.handlers:
    log_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Console (stdout) para Railway / Docker streaming
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(log_formatter)
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)

    # 2. Arquivo rotativo (5MB por arquivo, 5 rotações preservadas)
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)
    except Exception as e_log:
        sys.stderr.write(f"Aviso: Não foi possível iniciar RotatingFileHandler: {e_log}\n")

# ============================================================
# CONFIGURAÇÃO DA API DE CONSULTA DE PLACAS
# ============================================================
caminho_env = pasta_projeto / ".env"
if caminho_env.exists():
    try:
        with open(caminho_env, encoding="utf-8") as f_env:
            for linha_env in f_env:
                linha_env = linha_env.strip()
                if linha_env and not linha_env.startswith("#") and "=" in linha_env:
                    chave_env, valor_env = linha_env.split("=", 1)
                    chave_env = chave_env.strip()
                    valor_env = valor_env.strip().strip("'").strip('"')
                    if chave_env and chave_env not in os.environ:
                        os.environ[chave_env] = valor_env
    except Exception:
        pass

PLACA_API_TOKEN = os.environ.get("PLACA_API_TOKEN", "333fb39054a3024b0d77f9b390389ef5")
PLACA_API_PROVIDER = os.environ.get("PLACA_API_PROVIDER", "wdapi").lower()


def conectar_banco():
    conexao = sqlite3.connect(banco, timeout=10.0)
    conexao.execute("PRAGMA journal_mode = WAL;")
    conexao.execute("PRAGMA busy_timeout = 10000;")
    conexao.execute("PRAGMA synchronous = NORMAL;")
    conexao.execute("PRAGMA foreign_keys = ON;")
    return conexao


def converter_para_float(valor):
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).replace("R$", "").replace(" ", "").strip()
    if not texto:
        return 0.0
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def garantir_tabelas_sistema():
    conexao = conectar_banco()
    cursor = conexao.cursor()

    # Clientes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id_cliente INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT NOT NULL,
            cpf_cnpj TEXT
        )
    """)

    cursor.execute("PRAGMA table_info(clientes)")
    colunas_cli = [linha[1] for linha in cursor.fetchall()]
    if "cpf_cnpj" not in colunas_cli:
        cursor.execute("ALTER TABLE clientes ADD COLUMN cpf_cnpj TEXT")

    # Veículos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS veiculos (
            id_veiculo INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            placa TEXT NOT NULL,
            modelo TEXT,
            marca TEXT,
            ano TEXT,
            cor TEXT,
            ativo INTEGER DEFAULT 1,
            FOREIGN KEY (id_cliente) REFERENCES clientes(id_cliente)
        )
    """)

    # Ordens de Serviço
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ordens_servico (
            id_os INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            id_veiculo INTEGER NOT NULL,
            data TEXT NOT NULL,
            km TEXT,
            defeito_reclamado TEXT,
            diagnostico TEXT,
            observacoes TEXT,
            forma_pagamento TEXT,
            status TEXT DEFAULT 'Aberta',
            responsavel TEXT,
            valor_mao_obra REAL DEFAULT 0,
            valor_pecas REAL DEFAULT 0,
            valor_custo_pecas REAL DEFAULT 0,
            valor_repasse REAL DEFAULT 0,
            valor_total REAL DEFAULT 0,
            FOREIGN KEY (id_cliente) REFERENCES clientes(id_cliente),
            FOREIGN KEY (id_veiculo) REFERENCES veiculos(id_veiculo)
        )
    """)

    # Peças da OS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pecas_os (
            id_peca_os INTEGER PRIMARY KEY AUTOINCREMENT,
            id_os INTEGER NOT NULL,
            descricao TEXT NOT NULL,
            quantidade INTEGER NOT NULL DEFAULT 1,
            valor_custo_unitario REAL NOT NULL DEFAULT 0,
            valor_unitario REAL NOT NULL DEFAULT 0,
            valor_total REAL NOT NULL DEFAULT 0,
            id_peca INTEGER,
            FOREIGN KEY (id_os) REFERENCES ordens_servico(id_os)
        )
    """)

    # Serviços da OS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS servicos_os (
            id_servico INTEGER PRIMARY KEY AUTOINCREMENT,
            id_os INTEGER NOT NULL,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (id_os) REFERENCES ordens_servico(id_os)
        )
    """)

    # Usuários
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id_usuario INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            usuario TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            perfil TEXT NOT NULL DEFAULT 'usuario',
            senha_temporaria INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Peças (Catálogo / Estoque)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pecas (
            id_peca INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            nome TEXT NOT NULL,
            marca TEXT,
            preco_custo REAL NOT NULL,
            preco_venda REAL NOT NULL,
            estoque INTEGER DEFAULT 0,
            ativo INTEGER DEFAULT 1
        )
    """)

    # Despesas / Saídas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS despesas (
            id_despesa INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            descricao TEXT NOT NULL,
            categoria TEXT NOT NULL,
            valor REAL NOT NULL,
            forma_pagamento TEXT,
            status TEXT DEFAULT 'Pago',
            observacoes TEXT
        )
    """)

    # Categorias de Despesas / Saídas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categorias_despesas (
            id_categoria INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            ativo INTEGER DEFAULT 1
        )
    """)

    categorias_padrao = [
        "Empréstimo",
        "Pró-labore",
        "Aluguel",
        "Energia Elétrica",
        "Água e Esgoto",
        "Impostos",
        "Peças Avulsas",
        "Ferramentas",
        "Internet e Telefone",
        "Manutenção",
        "Outros"
    ]
    for cat in categorias_padrao:
        cursor.execute("INSERT OR IGNORE INTO categorias_despesas (nome, ativo) VALUES (?, 1)", (cat,))

    # Sincronizar categorias já existentes na tabela despesas
    cursor.execute("SELECT DISTINCT categoria FROM despesas WHERE categoria IS NOT NULL AND TRIM(categoria) != ''")
    for linha in cursor.fetchall():
        cat_existente = linha[0].strip()
        if cat_existente:
            cursor.execute("INSERT OR IGNORE INTO categorias_despesas (nome, ativo) VALUES (?, 1)", (cat_existente,))


    # Tabela de Prestadores / Equipe e Terceirizados
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prestadores (
            id_prestador INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'Funcionário',
            funcao TEXT,
            telefone TEXT,
            porcentagem_repasse REAL DEFAULT 50.0,
            ativo INTEGER DEFAULT 1
        )
    """)

    cursor.execute("INSERT OR IGNORE INTO prestadores (nome, tipo, funcao, telefone, porcentagem_repasse, ativo) VALUES ('Henrique', 'Funcionário', 'Mecânico Responsável', '', 50.0, 1)")
    cursor.execute("INSERT OR IGNORE INTO prestadores (nome, tipo, funcao, telefone, porcentagem_repasse, ativo) VALUES ('Empresa Terceirizada', 'Terceirizado', 'Serviços Externos / Parcerias', '', 0.0, 1)")

    # Migrações graduais de colunas para bancos existentes
    cursor.execute("PRAGMA table_info(servicos_os)")
    colunas_servicos_os = [c[1] for c in cursor.fetchall()]
    if "executante" not in colunas_servicos_os:
        cursor.execute("ALTER TABLE servicos_os ADD COLUMN executante TEXT")
    if "valor_repasse" not in colunas_servicos_os:
        cursor.execute("ALTER TABLE servicos_os ADD COLUMN valor_repasse REAL NOT NULL DEFAULT 0")

    # Retrocompatibilidade: preencher executante e valor_repasse em serviços antigos
    try:
        cursor.execute("""
            UPDATE servicos_os
            SET executante = (
                SELECT COALESCE(ordens_servico.responsavel, 'Henrique')
                FROM ordens_servico
                WHERE ordens_servico.id_os = servicos_os.id_os
            )
            WHERE executante IS NULL OR TRIM(executante) = ''
        """)
        cursor.execute("""
            UPDATE servicos_os
            SET valor_repasse = valor * 0.5
            WHERE (valor_repasse IS NULL OR valor_repasse = 0)
              AND LOWER(executante) = 'henrique'
        """)
    except Exception:
        pass

    cursor.execute("PRAGMA table_info(usuarios)")
    colunas_usr = [linha[1] for linha in cursor.fetchall()]
    if "senha_temporaria" not in colunas_usr:
        cursor.execute("ALTER TABLE usuarios ADD COLUMN senha_temporaria INTEGER NOT NULL DEFAULT 0")

    cursor.execute("PRAGMA table_info(pecas_os)")
    colunas_pecas_os = [linha[1] for linha in cursor.fetchall()]
    if "valor_custo_unitario" not in colunas_pecas_os:
        cursor.execute("ALTER TABLE pecas_os ADD COLUMN valor_custo_unitario REAL NOT NULL DEFAULT 0")
    if "id_peca" not in colunas_pecas_os:
        cursor.execute("ALTER TABLE pecas_os ADD COLUMN id_peca INTEGER")

    cursor.execute("PRAGMA table_info(ordens_servico)")
    colunas_os = [linha[1] for linha in cursor.fetchall()]
    if "valor_custo_pecas" not in colunas_os:
        cursor.execute("ALTER TABLE ordens_servico ADD COLUMN valor_custo_pecas REAL NOT NULL DEFAULT 0")

    cursor.execute("PRAGMA table_info(veiculos)")
    colunas_veic = [linha[1] for linha in cursor.fetchall()]
    if "cor" not in colunas_veic:
        cursor.execute("ALTER TABLE veiculos ADD COLUMN cor TEXT")

    conexao.commit()
    conexao.close()


# ============================================================
# BACKUP AUTOMATIZADO DO SQLITE
# ============================================================

def executar_backup_sqlite():
    """
    Executa backup online, atômico e não-bloqueante utilizando a API
    nativa sqlite3.Connection.backup().
    Aplica política de retenção mantendo os 15 backups mais recentes.
    """
    try:
        if not banco.exists():
            logger.warning("Tentativa de backup falhou: arquivo do banco de dados não encontrado.")
            return False, "Arquivo do banco de dados não encontrado."

        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        agora = datetime.now()
        nome_arquivo = f"backup_oficina_{agora.strftime('%Y%m%d_%H%M%S')}.db"
        caminho_destino = BACKUPS_DIR / nome_arquivo

        conexao_origem = sqlite3.connect(banco, timeout=15.0)
        conexao_destino = sqlite3.connect(caminho_destino)

        try:
            conexao_origem.backup(conexao_destino)
        finally:
            conexao_destino.close()
            conexao_origem.close()

        # Política de retenção: manter os 15 mais recentes
        arquivos_backup = sorted(
            [f for f in BACKUPS_DIR.glob("backup_oficina_*.db") if f.is_file()],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        for backup_antigo in arquivos_backup[15:]:
            try:
                backup_antigo.unlink()
                logger.info(f"Backup antigo removido pela política de retenção: {backup_antigo.name}")
            except Exception as ex_limpeza:
                logger.warning(f"Erro ao remover backup antigo {backup_antigo.name}: {ex_limpeza}")

        tamanho_kb = caminho_destino.stat().st_size / 1024
        logger.info(f"Backup realizado com sucesso: {nome_arquivo} ({tamanho_kb:.1f} KB)")
        return True, nome_arquivo

    except Exception as e:
        logger.error(f"Erro crítico ao executar backup do SQLite: {e}", exc_info=True)
        return False, str(e)


def verificar_backup_diario_inicial():
    """Verifica e executa backup diário preventivo ao iniciar a aplicação."""
    try:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        hoje_str = datetime.now().strftime("%Y%m%d")
        backups_hoje = list(BACKUPS_DIR.glob(f"backup_oficina_{hoje_str}_*.db"))
        if not backups_hoje and banco.exists():
            logger.info("Nenhum backup encontrado para o dia de hoje. Executando backup diário preventivo de inicialização...")
            sucesso, res = executar_backup_sqlite()
            if sucesso:
                logger.info(f"Backup diário de inicialização gerado com sucesso: {res}")
            else:
                logger.warning(f"Falha ao gerar backup diário de inicialização: {res}")
    except Exception as e:
        logger.warning(f"Não foi possível verificar/executar backup diário inicial: {e}")


garantir_tabelas_sistema()
verificar_backup_diario_inicial()


# ============================================================
# AUDITORIA DE REQUISIÇÕES & CONTROLE DE ACESSO
# ============================================================

@app.before_request
def monitorar_inicio_requisicao():
    g.start_time = time.time()


@app.after_request
def registrar_log_requisicao(response):
    if not request.path.startswith("/static") and request.endpoint != "favicon":
        duracao_ms = (time.time() - getattr(g, "start_time", time.time())) * 1000
        usuario = session.get("usuario", "anonimo")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        logger.info(
            f"{request.method} {request.path} {response.status_code} - {duracao_ms:.1f}ms - user:{usuario} ip:{ip}"
        )
    return response


@app.before_request
def verificar_login():

    # Se o endpoint não foi encontrado (404), deixa o manipulador de erro 404 responder
    if request.endpoint is None:
        return

    # Rotas que podem ser acessadas sem login.
    if request.endpoint in ("login", "esqueci_senha", "gerar_pdf_os", "favicon"):
        return

    if request.endpoint == "static":
        return

    # Qualquer outra rota exige usuário autenticado.
    if "usuario_id" not in session:
        return redirect("/login")

    # Se a senha foi marcada como temporária, o usuário precisa
    # trocá-la antes de continuar no sistema.
    if session.get("trocar_senha") and request.endpoint not in (
        "alterar_senha",
        "logout",
    ):
        return redirect("/alterar-senha")


# ============================================================
# MANIPULADORES GLOBAIS DE ERRO (404 & 500)
# ============================================================

@app.errorhandler(404)
def pagina_nao_encontrada(e):
    usuario = session.get("usuario", "anonimo")
    logger.warning(f"404 Não Encontrado: {request.method} {request.path} - user:{usuario}")
    return render_template("404.html"), 404


@app.errorhandler(500)
def erro_interno_servidor(e):
    usuario = session.get("usuario", "anonimo")
    logger.error(f"500 Erro Interno na rota {request.method} {request.path} - user:{usuario} - {e}", exc_info=True)
    return render_template("500.html"), 500


@app.errorhandler(Exception)
def tratar_excecao_global(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        if e.code == 404:
            return pagina_nao_encontrada(e)
        return e
    usuario = session.get("usuario", "anonimo")
    logger.error(f"Exceção não tratada na rota {request.method} {request.path} - user:{usuario}: {e}", exc_info=True)
    return render_template("500.html"), 500


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(pasta_projeto / "static", "favicon.ico", mimetype="image/vnd.microsoft.icon")


# ============================================================
# LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    mensagem = ""

    if request.method == "POST":

        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")

        if not usuario or not senha:

            mensagem = "Informe o usuário e a senha."

            return render_template("login.html", mensagem=mensagem)

        conexao = conectar_banco()
        cursor = conexao.cursor()

        cursor.execute("""
            SELECT
                id_usuario,
                nome,
                usuario,
                senha,
                perfil,
                COALESCE(senha_temporaria, 0)
            FROM usuarios
            WHERE usuario = ?
        """, (usuario,))

        usuario_dados = cursor.fetchone()
        conexao.close()

        if usuario_dados is None:

            mensagem = "Usuário ou senha inválidos."

            return render_template("login.html", mensagem=mensagem)

        if not check_password_hash(usuario_dados[3], senha):

            mensagem = "Usuário ou senha inválidos."

            return render_template("login.html", mensagem=mensagem)

        session.clear()
        session["usuario_id"] = usuario_dados[0]
        session["nome_usuario"] = usuario_dados[1]
        session["usuario"] = usuario_dados[2]
        session["perfil"] = usuario_dados[4]

        if int(usuario_dados[5] or 0) == 1:
            session["trocar_senha"] = True
            return redirect("/alterar-senha")

        return redirect("/")

    return render_template("login.html", mensagem=mensagem)


# ============================================================
# SAIR DO SISTEMA
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


# ============================================================
# ALTERAR A PRÓPRIA SENHA
# ============================================================

@app.route("/alterar-senha", methods=["GET", "POST"])
def alterar_senha():

    if "usuario_id" not in session:
        return redirect("/login")

    mensagem = ""
    sucesso = ""
    obrigatoria = bool(session.get("trocar_senha"))

    if request.method == "POST":

        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        if not senha_atual or not nova_senha or not confirmar_senha:
            mensagem = "Preencha todos os campos."

        elif len(nova_senha) < 8:
            mensagem = "A nova senha deve ter pelo menos 8 caracteres."

        elif nova_senha != confirmar_senha:
            mensagem = "A confirmação da nova senha não confere."

        elif nova_senha == senha_atual:
            mensagem = "A nova senha deve ser diferente da senha atual."

        else:
            conexao = conectar_banco()
            cursor = conexao.cursor()

            cursor.execute(
                "SELECT senha FROM usuarios WHERE id_usuario = ?",
                (session["usuario_id"],)
            )

            dados = cursor.fetchone()

            if dados is None or not check_password_hash(dados[0], senha_atual):
                mensagem = "A senha atual está incorreta."
                conexao.close()
            else:
                cursor.execute(
                    """
                    UPDATE usuarios
                    SET senha = ?, senha_temporaria = 0
                    WHERE id_usuario = ?
                    """,
                    (generate_password_hash(nova_senha), session["usuario_id"])
                )

                conexao.commit()
                conexao.close()

                session.pop("trocar_senha", None)

                return redirect("/")

    return render_template(
        "alterar_senha.html",
        mensagem=mensagem,
        sucesso=sucesso,
        obrigatoria=obrigatoria
    )


# ============================================================
# VERIFICAÇÃO DE ADMINISTRADOR
# ============================================================

def usuario_e_admin():
    return session.get("perfil") == "admin"


# ============================================================
# GERENCIAMENTO DE USUÁRIOS
# ============================================================

@app.route("/usuarios", methods=["GET", "POST"])
def usuarios():

    if not usuario_e_admin():
        return "Acesso negado. Apenas administradores podem gerenciar usuários.", 403

    mensagem = request.args.get("mensagem", "") if request.method == "GET" else ""
    sucesso = ""

    if request.args.get("tipo") == "sucesso":
        sucesso = mensagem
        mensagem = ""

    if request.method == "POST":

        nome = request.form.get("nome", "").strip()
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")
        perfil = request.form.get("perfil", "usuario").strip().lower()

        if perfil not in ("usuario", "admin"):
            perfil = "usuario"

        if not nome or not usuario or not senha or not confirmar_senha:
            mensagem = "Preencha todos os campos do novo usuário."

        elif len(senha) < 8:
            mensagem = "A senha inicial deve ter pelo menos 8 caracteres."

        elif senha != confirmar_senha:
            mensagem = "A confirmação da senha não confere."

        else:
            conexao = conectar_banco()
            cursor = conexao.cursor()

            try:
                cursor.execute(
                    """
                    INSERT INTO usuarios
                    (nome, usuario, senha, perfil, senha_temporaria)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (nome, usuario, generate_password_hash(senha), perfil)
                )

                conexao.commit()
                sucesso = "Usuário criado com sucesso."

            except sqlite3.IntegrityError:
                mensagem = "Esse nome de usuário já está cadastrado."

            finally:
                conexao.close()

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute(
        """
        SELECT
            id_usuario,
            nome,
            usuario,
            perfil,
            COALESCE(senha_temporaria, 0)
        FROM usuarios
        ORDER BY nome
        """
    )

    lista_usuarios = cursor.fetchall()
    conexao.close()

    return render_template(
        "usuarios.html",
        usuarios=lista_usuarios,
        mensagem=mensagem,
        sucesso=sucesso
    )


# ============================================================
# REDEFINIR SENHA DE UM USUÁRIO — ADMINISTRADOR
# ============================================================

@app.route("/usuarios/<int:id_usuario>/resetar-senha", methods=["POST"])
def resetar_senha(id_usuario):

    if not usuario_e_admin():
        return "Acesso negado. Apenas administradores podem redefinir senhas.", 403

    nova_senha = request.form.get("nova_senha", "")
    confirmar_senha = request.form.get("confirmar_senha", "")

    if len(nova_senha) < 8 or nova_senha != confirmar_senha:
        return redirect("/usuarios?mensagem=Senha+inválida+ou+confirmação+incorreta.&tipo=erro")

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute(
        """
        UPDATE usuarios
        SET senha = ?, senha_temporaria = 1
        WHERE id_usuario = ?
        """,
        (generate_password_hash(nova_senha), id_usuario)
    )

    alterados = cursor.rowcount
    conexao.commit()
    conexao.close()

    if alterados == 0:
        return redirect("/usuarios?mensagem=Usuário+não+encontrado.&tipo=erro")

    return redirect("/usuarios?mensagem=Senha+redefinida+com+sucesso.&tipo=sucesso")


# ============================================================
# EXCLUIR USUÁRIO — ADMINISTRADOR
# ============================================================

@app.route("/usuarios/<int:id_usuario>/excluir", methods=["POST"])
def excluir_usuario(id_usuario):

    if not usuario_e_admin():
        return "Acesso negado. Apenas administradores podem excluir usuários.", 403

    if id_usuario == session.get("usuario_id"):
        return redirect("/usuarios?mensagem=Você+não+pode+excluir+seu+próprio+usuário+conectado.&tipo=erro")

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute("SELECT usuario FROM usuarios WHERE id_usuario = ?", (id_usuario,))
    dados = cursor.fetchone()

    if dados is None:
        conexao.close()
        return redirect("/usuarios?mensagem=Usuário+não+encontrado.&tipo=erro")

    cursor.execute("DELETE FROM usuarios WHERE id_usuario = ?", (id_usuario,))
    conexao.commit()
    conexao.close()

    return redirect(f"/usuarios?mensagem=Usuário+{dados[0]}+excluído+com+sucesso.&tipo=sucesso")


# ============================================================
# MENU PRINCIPAL
# ============================================================

@app.route("/")
@app.route("/menu")
def menu():
    return render_template("menu.html")


# ============================================================
# PEÇAS E ESTOQUE
# ============================================================

@app.route("/pecas", methods=["GET", "POST"])
def gerenciar_pecas():
    mensagem = request.args.get("mensagem", "")
    sucesso = request.args.get("sucesso", "")

    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip()
        nome = request.form.get("nome", "").strip()
        marca = request.form.get("marca", "").strip()
        preco_custo = converter_para_float(request.form.get("preco_custo", 0))
        preco_venda = converter_para_float(request.form.get("preco_venda", 0))
        try:
            estoque = int(request.form.get("estoque", "0") or 0)
        except ValueError:
            estoque = 0

        if not nome:
            mensagem = "A descrição da peça é obrigatória."
        elif preco_custo <= 0:
            mensagem = "O preço de custo deve ser maior que zero."
        elif preco_venda <= 0:
            mensagem = "O preço de venda deve ser maior que zero."
        else:
            conexao = conectar_banco()
            cursor = conexao.cursor()
            cursor.execute("""
                INSERT INTO pecas (codigo, nome, marca, preco_custo, preco_venda, estoque, ativo)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (codigo, nome, marca, preco_custo, preco_venda, estoque))
            conexao.commit()
            conexao.close()
            return redirect("/pecas?sucesso=Peça+cadastrada+com+sucesso!")

    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("""
        SELECT id_peca, codigo, nome, marca, preco_custo, preco_venda, estoque
        FROM pecas
        WHERE ativo = 1
        ORDER BY nome COLLATE NOCASE
    """)
    lista_pecas = cursor.fetchall()
    conexao.close()

    return render_template(
        "pecas.html",
        pecas=lista_pecas,
        mensagem=mensagem,
        sucesso=sucesso
    )


@app.route("/pecas/<int:id_peca>/excluir", methods=["POST"])
def excluir_peca(id_peca):
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("UPDATE pecas SET ativo = 0 WHERE id_peca = ?", (id_peca,))
    conexao.commit()
    conexao.close()
    return redirect("/pecas?sucesso=Peça+removida+com+sucesso!")


@app.route("/api/pecas", methods=["GET"])
def api_pecas():
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("""
        SELECT id_peca, codigo, nome, marca, preco_custo, preco_venda, estoque
        FROM pecas
        WHERE ativo = 1
        ORDER BY nome COLLATE NOCASE
    """)
    dados = [
        {
            "id": row[0],
            "codigo": row[1] or "",
            "nome": row[2],
            "marca": row[3] or "",
            "preco_custo": row[4],
            "preco_venda": row[5],
            "estoque": row[6]
        }
        for row in cursor.fetchall()
    ]
    conexao.close()
    return jsonify(dados)


# ============================================================
# EQUIPE E PRESTADORES DE SERVIÇO (FUNCIONÁRIOS E TERCEIRIZADOS)
# ============================================================

@app.route("/prestadores", methods=["GET", "POST"])
def gerenciar_prestadores():
    mensagem = request.args.get("mensagem", "")
    sucesso = request.args.get("sucesso", "")

    if request.method == "POST":
        id_prestador = request.form.get("id_prestador", "").strip()
        nome = request.form.get("nome", "").strip()
        tipo = request.form.get("tipo", "Funcionário").strip()
        funcao = request.form.get("funcao", "").strip()
        telefone = request.form.get("telefone", "").strip()
        porcentagem = converter_para_float(request.form.get("porcentagem_repasse", 50))

        if not nome:
            mensagem = "O nome do funcionário ou empresa terceirizada é obrigatório."
        else:
            conexao = conectar_banco()
            cursor = conexao.cursor()
            if id_prestador:
                try:
                    cursor.execute("""
                        UPDATE prestadores
                        SET nome = ?, tipo = ?, funcao = ?, telefone = ?, porcentagem_repasse = ?
                        WHERE id_prestador = ?
                    """, (nome, tipo, funcao, telefone, porcentagem, id_prestador))
                    conexao.commit()
                    conexao.close()
                    return redirect("/prestadores?sucesso=Dados+do+prestador+atualizados+com+sucesso!")
                except sqlite3.IntegrityError:
                    conexao.close()
                    mensagem = "Já existe outro funcionário ou empresa cadastrada com este nome."
            else:
                try:
                    cursor.execute("""
                        INSERT INTO prestadores (nome, tipo, funcao, telefone, porcentagem_repasse, ativo)
                        VALUES (?, ?, ?, ?, ?, 1)
                    """, (nome, tipo, funcao, telefone, porcentagem))
                    conexao.commit()
                    conexao.close()
                    return redirect("/prestadores?sucesso=Prestador+cadastrado+com+sucesso!")
                except sqlite3.IntegrityError:
                    cursor.execute("""
                        UPDATE prestadores
                        SET ativo = 1, tipo = ?, funcao = ?, telefone = ?, porcentagem_repasse = ?
                        WHERE LOWER(nome) = LOWER(?)
                    """, (tipo, funcao, telefone, porcentagem, nome))
                    conexao.commit()
                    conexao.close()
                    return redirect("/prestadores?sucesso=Prestador+reativado+com+sucesso!")

    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("""
        SELECT id_prestador, nome, tipo, funcao, telefone, porcentagem_repasse, ativo
        FROM prestadores
        ORDER BY ativo DESC, nome COLLATE NOCASE ASC
    """)
    lista_prestadores = cursor.fetchall()
    conexao.close()

    return render_template(
        "prestadores.html",
        prestadores=lista_prestadores,
        mensagem=mensagem,
        sucesso=sucesso
    )


@app.route("/prestadores/<int:id_prestador>/status", methods=["POST"])
def alternar_status_prestador(id_prestador):
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("SELECT ativo FROM prestadores WHERE id_prestador = ?", (id_prestador,))
    linha = cursor.fetchone()
    if linha:
        novo_status = 0 if linha[0] == 1 else 1
        cursor.execute("UPDATE prestadores SET ativo = ? WHERE id_prestador = ?", (novo_status, id_prestador))
        conexao.commit()
    conexao.close()
    return redirect("/prestadores?sucesso=Status+atualizado+com+sucesso!")


@app.route("/api/prestadores", methods=["GET", "POST"])
def api_prestadores():
    conexao = conectar_banco()
    cursor = conexao.cursor()

    if request.method == "POST":
        dados = request.get_json(silent=True) or {}
        nome = (dados.get("nome") or request.form.get("nome", "")).strip()
        tipo = (dados.get("tipo") or request.form.get("tipo", "Funcionário")).strip()
        funcao = (dados.get("funcao") or request.form.get("funcao", "")).strip()
        telefone = (dados.get("telefone") or request.form.get("telefone", "")).strip()
        porcentagem = converter_para_float(dados.get("porcentagem_repasse") or request.form.get("porcentagem_repasse", 50))

        if not nome:
            conexao.close()
            return jsonify({"sucesso": False, "erro": "O nome é obrigatório."}), 400

        try:
            cursor.execute("""
                INSERT INTO prestadores (nome, tipo, funcao, telefone, porcentagem_repasse, ativo)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (nome, tipo, funcao, telefone, porcentagem))
            conexao.commit()
            id_p = cursor.lastrowid
            conexao.close()
            return jsonify({
                "sucesso": True,
                "id": id_p,
                "nome": nome,
                "tipo": tipo,
                "funcao": funcao,
                "porcentagem_repasse": porcentagem,
                "mensagem": "Prestador cadastrado com sucesso!"
            })
        except sqlite3.IntegrityError:
            cursor.execute("""
                UPDATE prestadores SET ativo = 1, tipo = ?, funcao = ?, telefone = ?, porcentagem_repasse = ?
                WHERE LOWER(nome) = LOWER(?)
            """, (tipo, funcao, telefone, porcentagem, nome))
            conexao.commit()
            cursor.execute("SELECT id_prestador FROM prestadores WHERE LOWER(nome) = LOWER(?)", (nome,))
            r = cursor.fetchone()
            id_p = r[0] if r else None
            conexao.close()
            return jsonify({
                "sucesso": True,
                "id": id_p,
                "nome": nome,
                "tipo": tipo,
                "funcao": funcao,
                "porcentagem_repasse": porcentagem,
                "mensagem": "Prestador já cadastrado e ativado!"
            })
        except Exception as e:
            conexao.close()
            return jsonify({"sucesso": False, "erro": str(e)}), 500

    cursor.execute("""
        SELECT id_prestador, nome, tipo, funcao, telefone, porcentagem_repasse
        FROM prestadores
        WHERE ativo = 1
        ORDER BY nome COLLATE NOCASE ASC
    """)
    linhas = cursor.fetchall()
    conexao.close()
    resultado = [
        {
            "id": r[0],
            "nome": r[1],
            "tipo": r[2],
            "funcao": r[3],
            "telefone": r[4],
            "porcentagem_repasse": r[5]
        }
        for r in linhas
    ]
    return jsonify(resultado)


# ============================================================
# API DE CATEGORIAS DE DESPESAS
# ============================================================

@app.route("/api/categorias-despesas", methods=["GET", "POST"])
def api_categorias_despesas():
    conexao = conectar_banco()
    cursor = conexao.cursor()

    if request.method == "POST":
        dados = request.get_json(silent=True) or {}
        nome = (dados.get("nome") or request.form.get("nome", "")).strip()

        if not nome:
            conexao.close()
            return jsonify({"sucesso": False, "erro": "O nome da categoria não pode estar em branco."}), 400

        try:
            cursor.execute("INSERT INTO categorias_despesas (nome, ativo) VALUES (?, 1)", (nome,))
            conexao.commit()
            id_cat = cursor.lastrowid
            conexao.close()
            return jsonify({"sucesso": True, "id": id_cat, "nome": nome, "mensagem": "Categoria criada com sucesso!"})
        except sqlite3.IntegrityError:
            # Reativa caso estivesse desativada
            cursor.execute("UPDATE categorias_despesas SET ativo = 1 WHERE LOWER(nome) = LOWER(?)", (nome,))
            conexao.commit()
            conexao.close()
            return jsonify({"sucesso": True, "nome": nome, "mensagem": "Categoria já existe e está ativa!"})
        except Exception as e:
            conexao.close()
            return jsonify({"sucesso": False, "erro": str(e)}), 500

    cursor.execute("SELECT id_categoria, nome FROM categorias_despesas WHERE ativo = 1 ORDER BY nome COLLATE NOCASE ASC")
    categorias = [{"id": r[0], "nome": r[1]} for r in cursor.fetchall()]
    conexao.close()
    return jsonify(categorias)


# ============================================================
# SAÍDAS E DESPESAS DA OFICINA
# ============================================================

@app.route("/despesas", methods=["GET", "POST"])
def gerenciar_despesas():
    mensagem = request.args.get("mensagem", "")
    sucesso = request.args.get("sucesso", "")

    if request.method == "POST":
        data = request.form.get("data", "").strip() or datetime.now().strftime("%Y-%m-%d")
        descricao = request.form.get("descricao", "").strip()
        categoria = request.form.get("categoria", "").strip()
        nova_categoria = request.form.get("nova_categoria", "").strip()
        if nova_categoria:
            categoria = nova_categoria
        if not categoria:
            categoria = "Outros"

        valor = converter_para_float(request.form.get("valor", 0))
        forma_pagamento = request.form.get("forma_pagamento", "PIX").strip()
        observacoes = request.form.get("observacoes", "").strip()

        if not descricao:
            mensagem = "A descrição da despesa é obrigatória."
        elif valor <= 0:
            mensagem = "O valor da despesa deve ser maior que zero."
        else:
            conexao = conectar_banco()
            cursor = conexao.cursor()
            # Garante que a categoria está salva no catálogo de categorias
            cursor.execute("INSERT OR IGNORE INTO categorias_despesas (nome, ativo) VALUES (?, 1)", (categoria,))
            cursor.execute("""
                INSERT INTO despesas (data, descricao, categoria, valor, forma_pagamento, status, observacoes)
                VALUES (?, ?, ?, ?, ?, 'Pago', ?)
            """, (data, descricao, categoria, valor, forma_pagamento, observacoes))
            conexao.commit()
            conexao.close()
            return redirect("/despesas?sucesso=Saída+registrada+com+sucesso!")

    data_inicio = request.args.get("data_inicio", "").strip()
    data_fim = request.args.get("data_fim", "").strip()
    categoria_filtro = request.args.get("categoria_filtro", "").strip()
    busca = request.args.get("busca", "").strip()

    conexao = conectar_banco()
    cursor = conexao.cursor()

    # Buscar todas as categorias ativas
    cursor.execute("SELECT nome FROM categorias_despesas WHERE ativo = 1 ORDER BY nome COLLATE NOCASE ASC")
    categorias_lista = [r[0] for r in cursor.fetchall()]

    # Montar filtros dinâmicos
    condicoes = []
    parametros = []

    if data_inicio and data_fim:
        condicoes.append("date(data) BETWEEN date(?) AND date(?)")
        parametros.extend([data_inicio, data_fim])
    elif data_inicio:
        condicoes.append("date(data) >= date(?)")
        parametros.append(data_inicio)
    elif data_fim:
        condicoes.append("date(data) <= date(?)")
        parametros.append(data_fim)

    if categoria_filtro:
        condicoes.append("categoria = ?")
        parametros.append(categoria_filtro)

    if busca:
        condicoes.append("(descricao LIKE ? OR observacoes LIKE ?)")
        parametros.extend([f"%{busca}%", f"%{busca}%"])

    filtro_sql = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""

    cursor.execute(f"""
        SELECT id_despesa, data, descricao, categoria, valor, forma_pagamento, status, observacoes
        FROM despesas
        {filtro_sql}
        ORDER BY date(data) DESC, id_despesa DESC
    """, tuple(parametros))
    lista_despesas = cursor.fetchall()

    cursor.execute(f"""
        SELECT COALESCE(SUM(valor), 0)
        FROM despesas
        {filtro_sql}
    """, tuple(parametros))
    total_despesas = cursor.fetchone()[0]

    # Resumo agregado por categoria no período consultado (independente do filtro de categoria atual, para mostrar os chips)
    condicoes_resumo = []
    params_resumo = []
    if data_inicio and data_fim:
        condicoes_resumo.append("date(data) BETWEEN date(?) AND date(?)")
        params_resumo.extend([data_inicio, data_fim])
    elif data_inicio:
        condicoes_resumo.append("date(data) >= date(?)")
        params_resumo.append(data_inicio)
    elif data_fim:
        condicoes_resumo.append("date(data) <= date(?)")
        params_resumo.append(data_fim)
    if busca:
        condicoes_resumo.append("(descricao LIKE ? OR observacoes LIKE ?)")
        params_resumo.extend([f"%{busca}%", f"%{busca}%"])

    filtro_resumo_sql = ("WHERE " + " AND ".join(condicoes_resumo)) if condicoes_resumo else ""

    cursor.execute(f"""
        SELECT categoria, COUNT(*), COALESCE(SUM(valor), 0)
        FROM despesas
        {filtro_resumo_sql}
        GROUP BY categoria
        ORDER BY SUM(valor) DESC
    """, tuple(params_resumo))
    resumo_categorias = cursor.fetchall()

    conexao.close()

    return render_template(
        "despesas.html",
        despesas=lista_despesas,
        total_despesas=total_despesas,
        categorias=categorias_lista,
        categoria_filtro=categoria_filtro,
        busca=busca,
        resumo_categorias=resumo_categorias,
        data_inicio=data_inicio,
        data_fim=data_fim,
        data_atual=datetime.now().strftime("%Y-%m-%d"),
        mensagem=mensagem,
        sucesso=sucesso
    )


@app.route("/despesas/<int:id_despesa>/excluir", methods=["POST"])
def excluir_despesa(id_despesa):
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("DELETE FROM despesas WHERE id_despesa = ?", (id_despesa,))
    conexao.commit()
    conexao.close()
    return redirect("/despesas?sucesso=Saída+excluída+com+sucesso!")


# ============================================================
# CADASTRO DE CLIENTE E VEÍCULO
# ============================================================

@app.route("/cadastro", methods=["GET", "POST"])
def inicio():

    mensagem = ""

    if request.method == "POST":

        nome = request.form["nome"]
        telefone = request.form["telefone"]
        cpf = request.form["cpf"]

        placa = request.form["placa"]
        marca = request.form["marca"]
        modelo = request.form["modelo"]
        ano = request.form["ano"]
        quilometragem = request.form["quilometragem"]

        conexao = conectar_banco()
        cursor = conexao.cursor()

        cursor.execute("""
            INSERT INTO clientes
            (nome, telefone, cpf_cnpj)
            VALUES (?, ?, ?)
        """, (
            nome,
            telefone,
            cpf
        ))

        id_cliente = cursor.lastrowid

        cursor.execute("""
            INSERT INTO veiculos
            (
                id_cliente,
                placa,
                marca,
                modelo,
                ano,
                quilometragem
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            id_cliente,
            placa,
            marca,
            modelo,
            ano,
            quilometragem
        ))

        conexao.commit()
        conexao.close()

        mensagem = "Cliente e veículo cadastrados com sucesso!"

    return render_template(
        "cadastro.html",
        mensagem=mensagem
    )


# ============================================================
# CLIENTES
# ============================================================

@app.route("/clientes")
def clientes():

    busca = request.args.get("busca", "").strip()

    conexao = conectar_banco()
    cursor = conexao.cursor()

    if busca:
        parametro_busca = f"%{busca}%"
        cursor.execute("""
            SELECT
                clientes.id_cliente,
                clientes.nome,
                clientes.telefone,
                veiculos.id_veiculo,
                veiculos.placa,
                veiculos.marca,
                veiculos.modelo
            FROM clientes
            LEFT JOIN veiculos
                ON clientes.id_cliente = veiculos.id_cliente
            WHERE clientes.nome LIKE ?
               OR clientes.telefone LIKE ?
               OR veiculos.placa LIKE ?
               OR veiculos.modelo LIKE ?
               OR veiculos.marca LIKE ?
               OR clientes.cpf_cnpj LIKE ?
            ORDER BY clientes.nome
        """, (
            parametro_busca,
            parametro_busca,
            parametro_busca,
            parametro_busca,
            parametro_busca,
            parametro_busca
        ))
    else:
        cursor.execute("""
            SELECT
                clientes.id_cliente,
                clientes.nome,
                clientes.telefone,
                veiculos.id_veiculo,
                veiculos.placa,
                veiculos.marca,
                veiculos.modelo
            FROM clientes
            LEFT JOIN veiculos
                ON clientes.id_cliente = veiculos.id_cliente
            ORDER BY clientes.nome
        """)

    lista_clientes = cursor.fetchall()

    conexao.close()

    mensagem = request.args.get("mensagem", "")
    tipo_mensagem = request.args.get("tipo", "")

    return render_template(
        "clientes.html",
        clientes=lista_clientes,
        mensagem=mensagem,
        tipo_mensagem=tipo_mensagem,
        busca=busca
    )


# ============================================================
# EXCLUIR CLIENTE
# ============================================================

@app.route(
    "/cliente/<int:id_cliente>/excluir",
    methods=["POST"]
)
def excluir_cliente(id_cliente):

    conexao = conectar_banco()
    cursor = conexao.cursor()

    # Verifica se o cliente existe
    cursor.execute("""
        SELECT id_cliente, nome
        FROM clientes
        WHERE id_cliente = ?
    """, (id_cliente,))

    cliente = cursor.fetchone()

    if cliente is None:

        conexao.close()

        return redirect(
            "/clientes?mensagem=Cliente não encontrado.&tipo=erro"
        )

    # Verifica se o cliente possui OS
    cursor.execute("""
        SELECT COUNT(*)
        FROM ordens_servico
        WHERE id_cliente = ?
    """, (id_cliente,))

    quantidade_os = cursor.fetchone()[0]

    if quantidade_os > 0:

        conexao.close()

        return redirect(
            "/clientes?"
            "mensagem=Não foi possível excluir o cliente. "
            "Este cliente possui Ordens de Serviço vinculadas e o histórico da oficina foi preservado."
            "&tipo=erro"
        )

    # Exclui os veículos do cliente
    cursor.execute("""
        DELETE FROM veiculos
        WHERE id_cliente = ?
    """, (id_cliente,))

    # Exclui o cliente
    cursor.execute("""
        DELETE FROM clientes
        WHERE id_cliente = ?
    """, (id_cliente,))

    conexao.commit()
    conexao.close()

    return redirect(
        "/clientes?"
        "mensagem=Cliente excluído com sucesso!"
        "&tipo=sucesso"
    )


# ============================================================
# EXCLUIR VEÍCULO
# ============================================================

@app.route(
    "/veiculo/<int:id_veiculo>/excluir",
    methods=["POST"]
)
def excluir_veiculo(id_veiculo):

    conexao = conectar_banco()
    cursor = conexao.cursor()

    # Verifica se o veículo existe
    cursor.execute("""
        SELECT id_veiculo, placa
        FROM veiculos
        WHERE id_veiculo = ?
    """, (id_veiculo,))

    veiculo = cursor.fetchone()

    if veiculo is None:

        conexao.close()

        return redirect(
            "/clientes?"
            "mensagem=Veículo não encontrado."
            "&tipo=erro"
        )

    # Verifica se o veículo possui OS
    cursor.execute("""
        SELECT COUNT(*)
        FROM ordens_servico
        WHERE id_veiculo = ?
    """, (id_veiculo,))

    quantidade_os = cursor.fetchone()[0]

    if quantidade_os > 0:

        conexao.close()

        return redirect(
            "/clientes?"
            "mensagem=Não foi possível excluir o veículo. "
            "Este veículo possui Ordens de Serviço vinculadas e o histórico foi preservado."
            "&tipo=erro"
        )

    # Exclui o veículo
    cursor.execute("""
        DELETE FROM veiculos
        WHERE id_veiculo = ?
    """, (id_veiculo,))

    conexao.commit()
    conexao.close()

    return redirect(
        "/clientes?"
        "mensagem=Veículo excluído com sucesso!"
        "&tipo=sucesso"
    )


# ============================================================
# NOVO CLIENTE - USADO DENTRO DA OS
# ============================================================

@app.route("/os/novo-cliente", methods=["POST"])
def novo_cliente_os():

    nome = request.form.get("nome", "").strip()
    telefone = request.form.get("telefone", "").strip()
    cpf = request.form.get("cpf", "").strip()

    if not nome:
        return jsonify({
            "sucesso": False,
            "mensagem": "Informe o nome do cliente."
        })

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute("""
        INSERT INTO clientes
        (
            nome,
            telefone,
            cpf_cnpj
        )
        VALUES (?, ?, ?)
    """, (
        nome,
        telefone,
        cpf
    ))

    id_cliente = cursor.lastrowid

    conexao.commit()
    conexao.close()

    return jsonify({
        "sucesso": True,
        "id_cliente": id_cliente,
        "nome": nome,
        "telefone": telefone
    })


# ============================================================
# CONSULTA DE PLACA VEICULAR (API NACIONAL E BANCO LOCAL)
# ============================================================

@app.route("/api/consulta-placa/<placa>", methods=["GET"])
def consulta_placa(placa):

    if "usuario_id" not in session:
        return jsonify({"sucesso": False, "mensagem": "Usuário não autenticado."}), 401

    placa_limpa = re.sub(r"[^a-zA-Z0-9]", "", placa).strip().upper()

    if len(placa_limpa) != 7:
        return jsonify({
            "sucesso": False,
            "mensagem": "Placa inválida. Deve conter 7 caracteres alfanuméricos."
        }), 400

    # 1. Verificar primeiro no banco de dados local (veículos já cadastrados na oficina)
    try:
        conexao = conectar_banco()
        cursor = conexao.cursor()
        cursor.execute("""
            SELECT marca, modelo, ano, cor
            FROM veiculos
            WHERE UPPER(REPLACE(REPLACE(placa, '-', ''), ' ', '')) = ?
            ORDER BY id_veiculo DESC
            LIMIT 1
        """, (placa_limpa,))
        veiculo_local = cursor.fetchone()
        conexao.close()

        if veiculo_local and veiculo_local[0] and veiculo_local[1]:
            return jsonify({
                "sucesso": True,
                "origem": "banco_local",
                "dados": {
                    "placa": placa_limpa,
                    "marca": veiculo_local[0],
                    "modelo": veiculo_local[1],
                    "ano": str(veiculo_local[2]) if veiculo_local[2] else "",
                    "cor": str(veiculo_local[3]) if veiculo_local[3] else ""
                }
            })
    except Exception as erro_banco:
        print(f"Aviso ao consultar placa no banco local: {erro_banco}")

    # 2. Requisição à API externa (Api Placas / WDAPI)
    token_atual = os.environ.get("PLACA_API_TOKEN", PLACA_API_TOKEN)
    if not token_atual:
        return jsonify({
            "sucesso": False,
            "sem_token": True,
            "mensagem": "Token da API de placas não configurado."
        })

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        if PLACA_API_PROVIDER == "wdapi":
            url = f"https://wdapi2.com.br/consulta/{placa_limpa}/{token_atual}"
        else:
            url = f"https://placa-fipe.apicarros.com/v1/consulta/{placa_limpa}/{token_atual}"

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=7) as resposta:
            corpo = resposta.read().decode("utf-8")
            dados_api = json.loads(corpo)

        marca = ""
        modelo = ""
        ano = ""
        cor = ""

        if PLACA_API_PROVIDER == "wdapi":
            marca = str(dados_api.get("MARCA") or dados_api.get("marca") or "").strip()
            modelo = str(dados_api.get("MODELO") or dados_api.get("modelo") or "").strip()
            ano = str(dados_api.get("anoModelo") or dados_api.get("ANO") or dados_api.get("ano") or "").strip()
            cor = str(dados_api.get("COR") or dados_api.get("cor") or "").strip()
        else:
            marca = str(dados_api.get("marca") or "").strip()
            modelo = str(dados_api.get("modelo") or "").strip()
            ano = str(dados_api.get("anoModelo") or dados_api.get("ano") or "").strip()
            cor = str(dados_api.get("cor") or "").strip()

        if not marca and not modelo:
            msg = dados_api.get("mensagemRetorno") or dados_api.get("message") or "Placa não localizada na base nacional."
            return jsonify({
                "sucesso": False,
                "mensagem": msg
            })

        return jsonify({
            "sucesso": True,
            "origem": "api",
            "dados": {
                "placa": placa_limpa,
                "marca": marca.title(),
                "modelo": modelo.title(),
                "ano": ano,
                "cor": cor.title() if cor else ""
            }
        })

    except urllib.error.HTTPError as erro_http:
        if erro_http.code in (401, 403):
            return jsonify({
                "sucesso": False,
                "mensagem": "Token da API expirado ou limite atingido."
            })
        elif erro_http.code == 404:
            return jsonify({
                "sucesso": False,
                "mensagem": "Placa não encontrada no cadastro nacional."
            })
        else:
            return jsonify({
                "sucesso": False,
                "mensagem": f"Erro no serviço de placas (Código {erro_http.code})."
            })
    except Exception as erro_conexao:
        print(f"Erro ao consultar API de placa: {erro_conexao}")
        return jsonify({
            "sucesso": False,
            "mensagem": "Não foi possível conectar ao serviço de placas no momento. Preencha manualmente."
        })


# ============================================================
# NOVO VEÍCULO - USADO DENTRO DA OS
# ============================================================

@app.route("/os/novo-veiculo", methods=["POST"])
def novo_veiculo_os():

    id_cliente = request.form.get("id_cliente")
    placa = request.form.get("placa", "").strip()
    marca = request.form.get("marca", "").strip()
    modelo = request.form.get("modelo", "").strip()
    ano = request.form.get("ano", "").strip()
    quilometragem = request.form.get("quilometragem", "").strip()

    if not id_cliente:
        return jsonify({
            "sucesso": False,
            "mensagem": "Selecione um cliente."
        })

    if not placa:
        return jsonify({
            "sucesso": False,
            "mensagem": "Informe a placa."
        })

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute("""
        INSERT INTO veiculos
        (
            id_cliente,
            placa,
            marca,
            modelo,
            ano,
            quilometragem
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        id_cliente,
        placa,
        marca,
        modelo,
        ano if ano else None,
        quilometragem if quilometragem else None
    ))

    id_veiculo = cursor.lastrowid

    conexao.commit()
    conexao.close()

    return jsonify({
        "sucesso": True,
        "id_veiculo": id_veiculo,
        "id_cliente": id_cliente,
        "placa": placa,
        "marca": marca,
        "modelo": modelo
    })


# ============================================================
# NOVA ORDEM DE SERVIÇO
# ============================================================

@app.route("/os", methods=["GET", "POST"])
def nova_os():

    mensagem = ""

    pecas_salvas = []
    servicos_salvos = []

    cliente_selecionado = ""
    veiculo_selecionado = ""
    responsavel_selecionado = ""
    pagamento_selecionado = ""

    conexao = conectar_banco()
    cursor = conexao.cursor()

    if request.method == "POST":

        cliente_selecionado = request.form.get("cliente", "")
        veiculo_selecionado = request.form.get("veiculo", "")
        responsavel_selecionado = request.form.get("responsavel", "")
        pagamento_selecionado = request.form.get("pagamento", "")

        data = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        pecas = request.form.getlist(
            "peca_descricao"
        )

        quantidades = request.form.getlist(
            "peca_quantidade"
        )

        valores_pecas = request.form.getlist(
            "peca_valor"
        )

        valores_custo = request.form.getlist(
            "peca_custo"
        )

        servicos = request.form.getlist(
            "servico_descricao"
        )

        valores_servicos = request.form.getlist(
            "servico_valor"
        )

        executantes_servicos = request.form.getlist(
            "servico_executante"
        )

        repasses_servicos = request.form.getlist(
            "servico_repasse"
        )

        cursor.execute("""
            SELECT id_prestador, nome, tipo, funcao, telefone, porcentagem_repasse
            FROM prestadores
            WHERE ativo = 1
            ORDER BY nome COLLATE NOCASE ASC
        """)
        prestadores_db = cursor.fetchall()
        mapa_comissoes = {p[1].lower(): (p[5] if p[5] is not None else 0.0) for p in prestadores_db}

        total_pecas = 0
        total_custo_pecas = 0
        total_servicos = 0
        total_repasse_os = 0

        for i in range(len(pecas)):

            if not pecas[i].strip():
                continue

            quantidade = converter_para_float(
                quantidades[i] if i < len(quantidades) else 1
            )
            if quantidade <= 0:
                quantidade = 1.0

            valor_unitario = converter_para_float(
                valores_pecas[i] if i < len(valores_pecas) else 0
            )

            valor_custo = converter_para_float(
                valores_custo[i] if i < len(valores_custo) else 0
            )

            total = quantidade * valor_unitario
            custo_total = quantidade * valor_custo

            total_pecas += total
            total_custo_pecas += custo_total

            pecas_salvas.append({
                "descricao": pecas[i].strip(),
                "quantidade": quantidade,
                "valor": valor_unitario,
                "custo": valor_custo,
                "total": total
            })

        for i in range(len(servicos)):

            if not servicos[i].strip():
                continue

            valor = converter_para_float(
                valores_servicos[i] if i < len(valores_servicos) else 0
            )

            total_servicos += valor

            executante = executantes_servicos[i].strip() if i < len(executantes_servicos) else ""
            if not executante:
                executante = responsavel_selecionado.strip() if responsavel_selecionado else "Henrique"

            repasse_digitado = repasses_servicos[i] if i < len(repasses_servicos) else ""
            if repasse_digitado != "" and repasse_digitado is not None:
                repasse_val = converter_para_float(repasse_digitado)
            else:
                pct = mapa_comissoes.get(executante.lower(), 50.0 if executante.lower() == "henrique" else 0.0)
                repasse_val = valor * (pct / 100.0)

            total_repasse_os += repasse_val

            servicos_salvos.append({
                "descricao": servicos[i].strip(),
                "valor": valor,
                "executante": executante,
                "valor_repasse": repasse_val
            })

        valor_total = (
            total_pecas +
            total_servicos
        )

        valor_repasse = total_repasse_os

        cursor.execute("""
            INSERT INTO ordens_servico
            (
                id_cliente,
                id_veiculo,
                data,
                responsavel,
                forma_pagamento,
                valor_mao_obra,
                valor_pecas,
                valor_total,
                valor_repasse,
                valor_custo_pecas
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cliente_selecionado,
            veiculo_selecionado,
            data,
            responsavel_selecionado,
            pagamento_selecionado,
            total_servicos,
            total_pecas,
            valor_total,
            valor_repasse,
            total_custo_pecas
        ))

        id_os = cursor.lastrowid

        for peca in pecas_salvas:

            cursor.execute("""
                INSERT INTO pecas_os
                (
                    id_os,
                    descricao,
                    quantidade,
                    valor_unitario,
                    valor_total,
                    valor_custo_unitario
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                id_os,
                peca["descricao"],
                peca["quantidade"],
                peca["valor"],
                peca["total"],
                peca["custo"]
            ))

        for servico in servicos_salvos:

            cursor.execute("""
                INSERT INTO servicos_os
                (
                    id_os,
                    descricao,
                    valor,
                    executante,
                    valor_repasse
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                id_os,
                servico["descricao"],
                servico["valor"],
                servico.get("executante", "Henrique"),
                servico.get("valor_repasse", 0.0)
            ))

        conexao.commit()
        conexao.close()

        return redirect(
            f"/os/{id_os}?mensagem="
            f"Ordem de Serviço nº {id_os} cadastrada com sucesso!"
        )

    cursor.execute("""
        SELECT
            id_cliente,
            nome,
            telefone
        FROM clientes
        ORDER BY nome
    """)

    lista_clientes = cursor.fetchall()

    cursor.execute("""
        SELECT
            id_veiculo,
            placa,
            marca,
            modelo,
            id_cliente
        FROM veiculos
        ORDER BY placa
    """)

    lista_veiculos = cursor.fetchall()

    cursor.execute("""
        SELECT id_peca, codigo, nome, marca, preco_custo, preco_venda, estoque
        FROM pecas
        WHERE ativo = 1
        ORDER BY nome COLLATE NOCASE
    """)

    lista_pecas_catalogo = cursor.fetchall()

    cursor.execute("""
        SELECT id_prestador, nome, tipo, funcao, telefone, porcentagem_repasse
        FROM prestadores
        WHERE ativo = 1
        ORDER BY nome COLLATE NOCASE
    """)
    lista_prestadores = cursor.fetchall()

    conexao.close()

    return render_template(
        "os.html",
        clientes=lista_clientes,
        veiculos=lista_veiculos,
        pecas_catalogo=lista_pecas_catalogo,
        prestadores=lista_prestadores,
        mensagem=mensagem,
        pecas_salvas=pecas_salvas,
        servicos_salvos=servicos_salvos,
        cliente_selecionado=cliente_selecionado,
        veiculo_selecionado=veiculo_selecionado,
        responsavel_selecionado=responsavel_selecionado,
        pagamento_selecionado=pagamento_selecionado
    )


# ============================================================
# LISTA E PESQUISA DE ORDENS DE SERVIÇO
# ============================================================

@app.route("/ordens")
def ordens():

    # ========================================================
    # RECEBER FILTROS
    # ========================================================

    numero_os = request.args.get(
        "numero_os",
        ""
    ).strip()

    cliente = request.args.get(
        "cliente",
        ""
    ).strip()

    data_inicio = request.args.get(
        "data_inicio",
        ""
    ).strip()

    data_fim = request.args.get(
        "data_fim",
        ""
    ).strip()

    status = request.args.get(
        "status",
        ""
    ).strip()


    # ========================================================
    # CONEXÃO COM O BANCO
    # ========================================================

    conexao = conectar_banco()
    cursor = conexao.cursor()


    # ========================================================
    # CONSULTA BASE
    # ========================================================

    consulta = """
        SELECT
            ordens_servico.id_os,
            ordens_servico.data,
            clientes.nome,
            veiculos.placa,
            veiculos.marca,
            veiculos.modelo,
            ordens_servico.valor_total,
            ordens_servico.forma_pagamento,
            ordens_servico.responsavel,
            ordens_servico.status

        FROM ordens_servico

        INNER JOIN clientes
            ON ordens_servico.id_cliente =
               clientes.id_cliente

        INNER JOIN veiculos
            ON ordens_servico.id_veiculo =
               veiculos.id_veiculo

        WHERE 1 = 1
    """


    parametros = []


    # ========================================================
    # FILTRO POR NÚMERO DA OS
    # ========================================================

    if numero_os:

        consulta += """
            AND ordens_servico.id_os = ?
        """

        parametros.append(numero_os)


    # ========================================================
    # FILTRO POR CLIENTE
    # ========================================================

    if cliente:

        consulta += """
            AND clientes.nome LIKE ?
        """

        parametros.append(
            f"%{cliente}%"
        )


    # ========================================================
    # FILTRO POR DATA INICIAL
    # ========================================================

    if data_inicio:

        consulta += """
            AND date(ordens_servico.data) >= date(?)
        """

        parametros.append(data_inicio)


    # ========================================================
    # FILTRO POR DATA FINAL
    # ========================================================

    if data_fim:

        consulta += """
            AND date(ordens_servico.data) <= date(?)
        """

        parametros.append(data_fim)


    # ========================================================
    # FILTRO POR STATUS
    # ========================================================

    if status:

        consulta += """
            AND ordens_servico.status = ?
        """

        parametros.append(status)


    # ========================================================
    # ORDENAR
    # ========================================================

    consulta += """
        ORDER BY ordens_servico.id_os DESC
    """


    # ========================================================
    # EXECUTAR
    # ========================================================

    cursor.execute(
        consulta,
        parametros
    )

    lista_os = cursor.fetchall()


    conexao.close()


    # ========================================================
    # RETORNAR PARA A PÁGINA
    # ========================================================

    return render_template(
        "ordens.html",
        ordens=lista_os,
        numero_os=numero_os,
        cliente=cliente,
        data_inicio=data_inicio,
        data_fim=data_fim,
        status=status
    )

# ============================================================
# EXCLUIR ORDEM DE SERVIÇO
# ============================================================

@app.route(
    "/os/<int:id_os>/excluir",
    methods=["POST"]
)
def excluir_os(id_os):

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT id_os
        FROM ordens_servico
        WHERE id_os = ?
    """, (id_os,))

    os_dados = cursor.fetchone()

    if os_dados is None:

        conexao.close()

        return "Ordem de Serviço não encontrada."

    cursor.execute("""
        DELETE FROM pecas_os
        WHERE id_os = ?
    """, (id_os,))

    cursor.execute("""
        DELETE FROM servicos_os
        WHERE id_os = ?
    """, (id_os,))

    cursor.execute("""
        DELETE FROM ordens_servico
        WHERE id_os = ?
    """, (id_os,))

    conexao.commit()
    conexao.close()

    return redirect("/ordens")


# ============================================================
# HISTÓRICO DE VEÍCULOS
# ============================================================

@app.route("/historico")
def historico():

    placa = request.args.get(
        "placa",
        ""
    ).strip().upper()

    conexao = conectar_banco()
    cursor = conexao.cursor()

    veiculo = None
    historico_os = []

    cursor.execute("""
        SELECT
            veiculos.id_veiculo,
            veiculos.placa,
            veiculos.marca,
            veiculos.modelo,
            veiculos.ano,
            clientes.nome
        FROM veiculos

        INNER JOIN clientes
            ON veiculos.id_cliente =
               clientes.id_cliente

        ORDER BY veiculos.placa
    """)

    lista_veiculos = cursor.fetchall()

    if placa:

        placa_limpa = re.sub(r"[^A-Z0-9]", "", placa)

        cursor.execute("""
            SELECT
                veiculos.id_veiculo,
                veiculos.placa,
                veiculos.marca,
                veiculos.modelo,
                veiculos.ano,
                clientes.nome,
                clientes.telefone
            FROM veiculos

            INNER JOIN clientes
                ON veiculos.id_cliente =
                   clientes.id_cliente

            WHERE UPPER(REPLACE(REPLACE(veiculos.placa, '-', ''), ' ', '')) = ?
               OR UPPER(veiculos.placa) = ?

            LIMIT 1
        """, (placa_limpa, placa))

        veiculo = cursor.fetchone()

        # Se não encontrou por placa exata, busca por aproximação (modelo, marca ou cliente)
        if not veiculo:
            termo_aproximado = f"%{placa}%"
            cursor.execute("""
                SELECT
                    veiculos.id_veiculo,
                    veiculos.placa,
                    veiculos.marca,
                    veiculos.modelo,
                    veiculos.ano,
                    clientes.nome,
                    clientes.telefone
                FROM veiculos

                INNER JOIN clientes
                    ON veiculos.id_cliente =
                       clientes.id_cliente

                WHERE clientes.nome LIKE ?
                   OR veiculos.modelo LIKE ?
                   OR veiculos.marca LIKE ?
                   OR veiculos.placa LIKE ?

                ORDER BY veiculos.id_veiculo DESC
                LIMIT 1
            """, (
                termo_aproximado,
                termo_aproximado,
                termo_aproximado,
                termo_aproximado
            ))

            veiculo = cursor.fetchone()

        if veiculo:

            cursor.execute("""
                SELECT
                    ordens_servico.id_os,
                    ordens_servico.data,
                    ordens_servico.valor_pecas,
                    ordens_servico.valor_mao_obra,
                    ordens_servico.valor_total,
                    ordens_servico.forma_pagamento,
                    ordens_servico.responsavel,
                    ordens_servico.status
                FROM ordens_servico

                WHERE ordens_servico.id_veiculo = ?

                ORDER BY ordens_servico.id_os DESC
            """, (veiculo[0],))

            historico_os = cursor.fetchall()

    conexao.close()

    return render_template(
        "historico.html",
        placa=placa,
        veiculo=veiculo,
        historico_os=historico_os,
        veiculos=lista_veiculos
    )


# ============================================================
# VISUALIZAR OS
# ============================================================

@app.route("/os/<int:id_os>")
def visualizar_os(id_os):

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT
            ordens_servico.id_os,
            ordens_servico.data,
            clientes.nome,
            clientes.telefone,
            clientes.cpf_cnpj,
            veiculos.placa,
            veiculos.marca,
            veiculos.modelo,
            veiculos.ano,
            ordens_servico.responsavel,
            ordens_servico.forma_pagamento,
            ordens_servico.valor_mao_obra,
            ordens_servico.valor_pecas,
            ordens_servico.valor_total,
            ordens_servico.valor_repasse,
            ordens_servico.status,
            COALESCE(ordens_servico.valor_custo_pecas, 0)
        FROM ordens_servico

        INNER JOIN clientes
            ON ordens_servico.id_cliente =
               clientes.id_cliente

        INNER JOIN veiculos
            ON ordens_servico.id_veiculo =
               veiculos.id_veiculo

        WHERE ordens_servico.id_os = ?
    """, (id_os,))

    os_dados = cursor.fetchone()

    if os_dados is None:

        conexao.close()

        return "Ordem de Serviço não encontrada."

    cursor.execute("""
        SELECT
            descricao,
            quantidade,
            valor_unitario,
            valor_total,
            COALESCE(valor_custo_unitario, 0)
        FROM pecas_os

        WHERE id_os = ?

        ORDER BY id_peca_os
    """, (id_os,))

    pecas = cursor.fetchall()

    cursor.execute("""
        SELECT
            descricao,
            valor,
            COALESCE(executante, ''),
            COALESCE(valor_repasse, 0)
        FROM servicos_os

        WHERE id_os = ?

        ORDER BY id_servico
    """, (id_os,))

    servicos = cursor.fetchall()

    conexao.close()

    custo_pecas = float(os_dados[16] or 0)
    total_pecas = float(os_dados[12] or 0)
    lucro_pecas = total_pecas - custo_pecas
    total_os = float(os_dados[13] or 0)
    repasse = float(os_dados[14] or 0)
    lucro_oficina_os = total_os - repasse - custo_pecas

    repasses_por_executante = {}
    for s in servicos:
        executante = s[2].strip() if s[2] else (os_dados[9] or "Henrique")
        rep = float(s[3] or 0.0)
        repasses_por_executante[executante] = repasses_por_executante.get(executante, 0.0) + rep

    if not repasses_por_executante and repasse > 0:
        repasses_por_executante[os_dados[9] or "Henrique"] = repasse

    mensagem = request.args.get(
        "mensagem",
        ""
    )

    return render_template(
        "visualizar_os.html",
        os=os_dados,
        pecas=pecas,
        servicos=servicos,
        custo_pecas=custo_pecas,
        lucro_pecas=lucro_pecas,
        lucro_oficina_os=lucro_oficina_os,
        repasses_por_executante=repasses_por_executante,
        mensagem=mensagem
    )

# ============================================================
# GERAR PDF DA ORDEM DE SERVIÇO
# ============================================================

@app.route("/os/<int:id_os>/pdf")
def gerar_pdf_os(id_os):

    conexao = conectar_banco()
    cursor = conexao.cursor()

    # ========================================================
    # DADOS DA OS
    # ========================================================

    cursor.execute("""
        SELECT
            ordens_servico.id_os,
            ordens_servico.data,
            clientes.nome,
            clientes.telefone,
            clientes.cpf_cnpj,
            veiculos.placa,
            veiculos.marca,
            veiculos.modelo,
            veiculos.ano,
            ordens_servico.responsavel,
            ordens_servico.forma_pagamento,
            ordens_servico.valor_mao_obra,
            ordens_servico.valor_pecas,
            ordens_servico.valor_total,
            ordens_servico.status
        FROM ordens_servico

        INNER JOIN clientes
            ON ordens_servico.id_cliente =
               clientes.id_cliente

        INNER JOIN veiculos
            ON ordens_servico.id_veiculo =
               veiculos.id_veiculo

        WHERE ordens_servico.id_os = ?
    """, (id_os,))

    os_dados = cursor.fetchone()

    if os_dados is None:

        conexao.close()

        return "Ordem de Serviço não encontrada."

    # ========================================================
    # PEÇAS
    # ========================================================

    cursor.execute("""
        SELECT
            descricao,
            quantidade,
            valor_unitario,
            valor_total
        FROM pecas_os

        WHERE id_os = ?

        ORDER BY id_peca_os
    """, (id_os,))

    pecas = cursor.fetchall()

    # ========================================================
    # SERVIÇOS
    # ========================================================

    cursor.execute("""
        SELECT
            descricao,
            valor
        FROM servicos_os

        WHERE id_os = ?

        ORDER BY id_servico
    """, (id_os,))

    servicos = cursor.fetchall()

    conexao.close()

    # ========================================================
    # FORMATAÇÃO DE MOEDA
    # ========================================================

    def moeda(valor):

        return (
            f"R$ {valor:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    # ========================================================
    # CRIAR PDF NA MEMÓRIA
    # ========================================================

    arquivo = BytesIO()

    documento = SimpleDocTemplate(
        arquivo,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm
    )

    # ========================================================
    # ESTILOS
    # ========================================================

    estilos = getSampleStyleSheet()

    estilo_titulo = ParagraphStyle(
        "Titulo",
        parent=estilos["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#222222"),
        spaceAfter=6
    )

    estilo_numero_os = ParagraphStyle(
        "NumeroOS",
        parent=estilo_titulo,
        alignment=2,
        textColor=colors.HexColor("#d71920")
    )

    estilo_subtitulo = ParagraphStyle(
        "Subtitulo",
        parent=estilos["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#555555")
    )

    estilo_secao = ParagraphStyle(
        "Secao",
        parent=estilos["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#222222"),
        spaceBefore=10,
        spaceAfter=6
    )

    estilo_normal = ParagraphStyle(
        "NormalPersonalizado",
        parent=estilos["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#222222")
    )

    elementos = []

    # ========================================================
    # CABEÇALHO COM LOGO
    # ========================================================

    caminho_logo = pasta_projeto / "static" / "logo.jpg"

    logo = Image(
        str(caminho_logo),
        width=35 * mm,
        height=22 * mm
    )

    informacoes_oficina = [
        Paragraph(
            "NEW POWER AUTO MECÂNICA",
            estilo_titulo
        ),
        Paragraph(
            "Rua V-3, Qd. V3, Lt. 18 - Vila Rezende - Goiânia/GO",
            estilo_subtitulo
        ),
        Paragraph(
            "Telefone: (62) 99117-5451",
            estilo_subtitulo
        )
    ]

    cabecalho_pdf = Table(
        [[logo, informacoes_oficina]],
        colWidths=[40 * mm, 140 * mm]
    )

    cabecalho_pdf.setStyle(
        TableStyle([
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                0
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                0
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                0
            )
        ])
    )

    elementos.append(cabecalho_pdf)

    elementos.append(
        Spacer(1, 5)
    )

    # ========================================================
    # LINHA VERMELHA
    # ========================================================

    linha = Table(
        [[""]],
        colWidths=[180 * mm],
        rowHeights=[2 * mm]
    )

    linha.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                colors.HexColor("#d71920")
            )
        ])
    )

    elementos.append(linha)

    elementos.append(
        Spacer(1, 10)
    )

    # ========================================================
    # TÍTULO DA OS
    # ========================================================

    titulo_os = Table(
        [[
            Paragraph(
                "ORDEM DE SERVIÇO",
                estilo_titulo
            ),
            Paragraph(
                f"Nº {os_dados[0]}",
                estilo_numero_os
            )
        ]],
        colWidths=[110 * mm, 70 * mm]
    )

    titulo_os.setStyle(
        TableStyle([
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                0
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                0
            )
        ])
    )

    elementos.append(titulo_os)

    elementos.append(
        Spacer(1, 8)
    )

    # ========================================================
    # DADOS DO CLIENTE
    # ========================================================

    cliente_texto = (
        f"<b>Nome:</b> {os_dados[2]}<br/>"
        f"<b>Telefone:</b> {os_dados[3]}"
    )

    if os_dados[4]:

        cliente_texto += (
            f"<br/><b>CPF/CNPJ:</b> {os_dados[4]}"
        )

    # ========================================================
    # DADOS DO VEÍCULO
    # ========================================================

    veiculo_texto = (
        f"<b>Placa:</b> {os_dados[5]}<br/>"
        f"<b>Marca:</b> {os_dados[6]}<br/>"
        f"<b>Modelo:</b> {os_dados[7]}"
    )

    if os_dados[8]:

        veiculo_texto += (
            f"<br/><b>Ano:</b> {os_dados[8]}"
        )

    dados_cliente_veiculo = Table(
        [[
            Paragraph(
                f"<b>CLIENTE</b><br/>{cliente_texto}",
                estilo_normal
            ),
            Paragraph(
                f"<b>VEÍCULO</b><br/>{veiculo_texto}",
                estilo_normal
            )
        ]],
        colWidths=[90 * mm, 90 * mm]
    )

    dados_cliente_veiculo.setStyle(
        TableStyle([
            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#dddddd")
            ),
            (
                "INNERGRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#dddddd")
            ),
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                colors.HexColor("#f8f8f8")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                8
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                8
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                8
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                8
            )
        ])
    )

    elementos.append(
        dados_cliente_veiculo
    )

    elementos.append(
        Spacer(1, 8)
    )

    # ========================================================
    # DATA E STATUS
    # ========================================================

    data_formatada = (
        f"{os_dados[1][8:10]}/"
        f"{os_dados[1][5:7]}/"
        f"{os_dados[1][0:4]}"
        f" às "
        f"{os_dados[1][11:16]}"
    )

    elementos.append(
        Paragraph(
            f"<b>Data da OS:</b> {data_formatada}",
            estilo_normal
        )
    )

    elementos.append(
        Paragraph(
            f"<b>Status:</b> {os_dados[14]}",
            estilo_normal
        )
    )

    elementos.append(
        Spacer(1, 8)
    )

    # ========================================================
    # PEÇAS
    # ========================================================

    elementos.append(
        Paragraph(
            "PEÇAS",
            estilo_secao
        )
    )

    if pecas:

        dados_tabela_pecas = [
            [
                Paragraph("<b>Peça</b>", estilo_normal),
                Paragraph("<b>Qtd.</b>", estilo_normal),
                Paragraph("<b>Valor unitário</b>", estilo_normal),
                Paragraph("<b>Total</b>", estilo_normal)
            ]
        ]

        for peca in pecas:

            dados_tabela_pecas.append([
                Paragraph(
                    str(peca[0]),
                    estilo_normal
                ),
                Paragraph(
                    str(peca[1]),
                    estilo_normal
                ),
                Paragraph(
                    moeda(peca[2]),
                    estilo_normal
                ),
                Paragraph(
                    moeda(peca[3]),
                    estilo_normal
                )
            ])

        tabela_pecas = Table(
            dados_tabela_pecas,
            colWidths=[
                85 * mm,
                20 * mm,
                37 * mm,
                38 * mm
            ],
            repeatRows=1
        )

        tabela_pecas.setStyle(
            TableStyle([
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#222222")
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.HexColor("#dddddd")
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE"
                ),
                (
                    "ALIGN",
                    (1, 1),
                    (1, -1),
                    "CENTER"
                ),
                (
                    "ALIGN",
                    (2, 1),
                    (-1, -1),
                    "RIGHT"
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    6
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    6
                )
            ])
        )

        elementos.append(
            tabela_pecas
        )

    else:

        elementos.append(
            Paragraph(
                "Nenhuma peça cadastrada.",
                estilo_normal
            )
        )

    # ========================================================
    # SERVIÇOS
    # ========================================================

    elementos.append(
        Paragraph(
            "SERVIÇOS",
            estilo_secao
        )
    )

    if servicos:

        dados_tabela_servicos = [
            [
                Paragraph(
                    "<b>Serviço</b>",
                    estilo_normal
                ),
                Paragraph(
                    "<b>Valor</b>",
                    estilo_normal
                )
            ]
        ]

        for servico in servicos:

            dados_tabela_servicos.append([
                Paragraph(
                    str(servico[0]),
                    estilo_normal
                ),
                Paragraph(
                    moeda(servico[1]),
                    estilo_normal
                )
            ])

        tabela_servicos = Table(
            dados_tabela_servicos,
            colWidths=[135 * mm, 45 * mm],
            repeatRows=1
        )

        tabela_servicos.setStyle(
            TableStyle([
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#222222")
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.HexColor("#dddddd")
                ),
                (
                    "ALIGN",
                    (1, 1),
                    (1, -1),
                    "RIGHT"
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    6
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    6
                )
            ])
        )

        elementos.append(
            tabela_servicos
        )

    else:

        elementos.append(
            Paragraph(
                "Nenhum serviço cadastrado.",
                estilo_normal
            )
        )

    elementos.append(
        Spacer(1, 10)
    )

    # ========================================================
    # RESUMO
    # ========================================================

    resumo = Table(
        [
            [
                "Peças:",
                moeda(os_dados[12])
            ],
            [
                "Mão de obra:",
                moeda(os_dados[11])
            ],
            [
                "TOTAL:",
                moeda(os_dados[13])
            ],
            [
                "Pagamento:",
                os_dados[10]
            ]
        ],
        colWidths=[120 * mm, 60 * mm]
    )

    resumo.setStyle(
        TableStyle([
            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#dddddd")
            ),
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                colors.HexColor("#eeeeee")
            ),
            (
                "LINEABOVE",
                (0, 2),
                (-1, 2),
                1,
                colors.HexColor("#222222")
            ),
            (
                "ALIGN",
                (1, 0),
                (1, -1),
                "RIGHT"
            ),
            (
                "FONTNAME",
                (0, 2),
                (-1, 2),
                "Helvetica-Bold"
            ),
            (
                "TEXTCOLOR",
                (1, 2),
                (1, 2),
                colors.HexColor("#d71920")
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                7
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                7
            )
        ])
    )

    elementos.append(
        resumo
    )

    elementos.append(
        Spacer(1, 15)
    )

    # ========================================================
    # RODAPÉ
    # ========================================================

    elementos.append(
        Paragraph(
            "Obrigado pela preferência!",
            ParagraphStyle(
                "Rodape",
                parent=estilo_normal,
                textColor=colors.HexColor("#555555")
            )
        )
    )

    # ========================================================
    # GERAR PDF
    # ========================================================

    documento.build(
        elementos
    )

    arquivo.seek(0)

    return send_file(
        arquivo,
        as_attachment=False,
        download_name=f"OS_{id_os}.pdf",
        mimetype="application/pdf"
    )

# ============================================================
# EDITAR ORDEM DE SERVIÇO
# ============================================================

@app.route(
    "/os/<int:id_os>/editar",
    methods=["GET", "POST"]
)
def editar_os(id_os):

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT
            id_os,
            id_cliente,
            id_veiculo,
            data,
            responsavel,
            forma_pagamento,
            status
        FROM ordens_servico
        WHERE id_os = ?
    """, (id_os,))

    os_dados = cursor.fetchone()

    if os_dados is None:

        conexao.close()

        return "Ordem de Serviço não encontrada."

    if request.method == "POST":

        id_cliente = request.form.get(
            "cliente"
        )

        id_veiculo = request.form.get(
            "veiculo"
        )

        responsavel = request.form.get(
            "responsavel"
        )

        pagamento = request.form.get(
            "pagamento"
        )

        status = request.form.get(
            "status"
        )

        pecas = request.form.getlist(
            "peca_descricao"
        )

        quantidades = request.form.getlist(
            "peca_quantidade"
        )

        valores_pecas = request.form.getlist(
            "peca_valor"
        )

        valores_custo = request.form.getlist(
            "peca_custo"
        )

        servicos = request.form.getlist(
            "servico_descricao"
        )

        valores_servicos = request.form.getlist(
            "servico_valor"
        )

        executantes_servicos = request.form.getlist(
            "servico_executante"
        )

        repasses_servicos = request.form.getlist(
            "servico_repasse"
        )

        cursor.execute("""
            SELECT id_prestador, nome, tipo, funcao, telefone, porcentagem_repasse
            FROM prestadores
            WHERE ativo = 1
            ORDER BY nome COLLATE NOCASE ASC
        """)
        prestadores_db = cursor.fetchall()
        mapa_comissoes = {p[1].lower(): (p[5] if p[5] is not None else 0.0) for p in prestadores_db}

        total_pecas = 0
        total_custo_pecas = 0
        total_servicos = 0
        total_repasse_os = 0

        pecas_salvas = []
        servicos_salvos = []

        for i in range(len(pecas)):

            descricao = pecas[i].strip()

            if not descricao:
                continue

            quantidade = converter_para_float(
                quantidades[i] if i < len(quantidades) else 1
            )
            if quantidade <= 0:
                quantidade = 1.0

            valor = converter_para_float(
                valores_pecas[i] if i < len(valores_pecas) else 0
            )

            custo = converter_para_float(
                valores_custo[i] if i < len(valores_custo) else 0
            )

            total = quantidade * valor
            custo_total = quantidade * custo

            total_pecas += total
            total_custo_pecas += custo_total

            pecas_salvas.append({
                "descricao": descricao,
                "quantidade": quantidade,
                "valor": valor,
                "custo": custo,
                "total": total
            })

        for i in range(len(servicos)):

            descricao = servicos[i].strip()

            if not descricao:
                continue

            valor = converter_para_float(
                valores_servicos[i] if i < len(valores_servicos) else 0
            )

            total_servicos += valor

            executante = executantes_servicos[i].strip() if i < len(executantes_servicos) else ""
            if not executante:
                executante = responsavel.strip() if responsavel else "Henrique"

            repasse_digitado = repasses_servicos[i] if i < len(repasses_servicos) else ""
            if repasse_digitado != "" and repasse_digitado is not None:
                repasse_val = converter_para_float(repasse_digitado)
            else:
                pct = mapa_comissoes.get(executante.lower(), 50.0 if executante.lower() == "henrique" else 0.0)
                repasse_val = valor * (pct / 100.0)

            total_repasse_os += repasse_val

            servicos_salvos.append({
                "descricao": descricao,
                "valor": valor,
                "executante": executante,
                "valor_repasse": repasse_val
            })

        valor_total = (
            total_pecas +
            total_servicos
        )

        valor_repasse = total_repasse_os

        cursor.execute("""
            UPDATE ordens_servico

            SET
                id_cliente = ?,
                id_veiculo = ?,
                responsavel = ?,
                forma_pagamento = ?,
                valor_mao_obra = ?,
                valor_pecas = ?,
                valor_total = ?,
                valor_repasse = ?,
                status = ?,
                valor_custo_pecas = ?

            WHERE id_os = ?
        """, (
            id_cliente,
            id_veiculo,
            responsavel,
            pagamento,
            total_servicos,
            total_pecas,
            valor_total,
            valor_repasse,
            status,
            total_custo_pecas,
            id_os
        ))

        cursor.execute("""
            DELETE FROM pecas_os
            WHERE id_os = ?
        """, (id_os,))

        cursor.execute("""
            DELETE FROM servicos_os
            WHERE id_os = ?
        """, (id_os,))

        for peca in pecas_salvas:

            cursor.execute("""
                INSERT INTO pecas_os
                (
                    id_os,
                    descricao,
                    quantidade,
                    valor_unitario,
                    valor_total,
                    valor_custo_unitario
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                id_os,
                peca["descricao"],
                peca["quantidade"],
                peca["valor"],
                peca["total"],
                peca["custo"]
            ))

        for servico in servicos_salvos:

            cursor.execute("""
                INSERT INTO servicos_os
                (
                    id_os,
                    descricao,
                    valor,
                    executante,
                    valor_repasse
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                id_os,
                servico["descricao"],
                servico["valor"],
                servico["executante"],
                servico["valor_repasse"]
            ))

        conexao.commit()
        conexao.close()

        return redirect(
            f"/os/{id_os}?mensagem="
            f"Ordem de Serviço atualizada com sucesso!"
        )

    cursor.execute("""
        SELECT
            id_cliente,
            nome,
            telefone
        FROM clientes
        ORDER BY nome
    """)

    lista_clientes = cursor.fetchall()

    cursor.execute("""
        SELECT
            id_veiculo,
            placa,
            marca,
            modelo,
            id_cliente
        FROM veiculos
        ORDER BY placa
    """)

    lista_veiculos = cursor.fetchall()

    cursor.execute("""
        SELECT
            descricao,
            quantidade,
            valor_unitario,
            valor_total,
            COALESCE(valor_custo_unitario, 0)
        FROM pecas_os
        WHERE id_os = ?
        ORDER BY id_peca_os
    """, (id_os,))

    pecas = cursor.fetchall()

    cursor.execute("""
        SELECT
            descricao,
            valor,
            COALESCE(executante, ''),
            COALESCE(valor_repasse, 0)
        FROM servicos_os
        WHERE id_os = ?
        ORDER BY id_servico
    """, (id_os,))

    servicos = cursor.fetchall()

    cursor.execute("""
        SELECT id_peca, codigo, nome, marca, preco_custo, preco_venda, estoque
        FROM pecas
        WHERE ativo = 1
        ORDER BY nome COLLATE NOCASE
    """)
    lista_pecas_catalogo = cursor.fetchall()

    cursor.execute("""
        SELECT id_prestador, nome, tipo, funcao, telefone, porcentagem_repasse
        FROM prestadores
        WHERE ativo = 1
        ORDER BY nome COLLATE NOCASE
    """)
    lista_prestadores = cursor.fetchall()

    conexao.close()

    return render_template(
        "editar_os.html",
        os=os_dados,
        clientes=lista_clientes,
        veiculos=lista_veiculos,
        pecas=pecas,
        servicos=servicos,
        pecas_catalogo=lista_pecas_catalogo,
        prestadores=lista_prestadores
    )


# ============================================================
# ATUALIZAR STATUS
# ============================================================

@app.route(
    "/os/<int:id_os>/status",
    methods=["POST"]
)
def atualizar_status(id_os):

    novo_status = request.form.get(
        "status"
    )

    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute("""
        UPDATE ordens_servico
        SET status = ?
        WHERE id_os = ?
    """, (
        novo_status,
        id_os
    ))

    conexao.commit()
    conexao.close()

    return redirect(
        f"/os/{id_os}"
    )


# ============================================================
# FINANCEIRO
# ============================================================

@app.route("/financeiro")
def financeiro():

    data_inicio = request.args.get(
        "data_inicio",
        ""
    ).strip()

    data_fim = request.args.get(
        "data_fim",
        ""
    ).strip()

    conexao = conectar_banco()
    cursor = conexao.cursor()

    # ========================================================
    # FILTRO PRINCIPAL
    # ========================================================

    if not data_inicio and not data_fim:

        filtro_data = ""
        parametros = ()

    else:

        if not data_inicio:
            data_inicio = data_fim

        if not data_fim:
            data_fim = data_inicio

        filtro_data = """
            AND date(data) BETWEEN date(?) AND date(?)
        """

        parametros = (
            data_inicio,
            data_fim
        )

    # ========================================================
    # RESUMO FINANCEIRO
    # ========================================================

    cursor.execute(f"""
        SELECT
            COUNT(*),
            COALESCE(SUM(valor_total), 0),
            COALESCE(SUM(valor_pecas), 0),
            COALESCE(SUM(valor_mao_obra), 0),
            COALESCE(SUM(valor_repasse), 0),
            COALESCE(SUM(valor_custo_pecas), 0)

        FROM ordens_servico

        WHERE status = 'Finalizada'

        {filtro_data}
    """, parametros)

    dados = cursor.fetchone()

    quantidade_os = dados[0]
    faturamento = dados[1]
    total_pecas = dados[2]
    total_mao_obra = dados[3]
    repasse_henrique = dados[4]
    total_custo_pecas = dados[5]

    lucro_pecas = total_pecas - total_custo_pecas
    lucro_bruto_operacional = (
        faturamento -
        repasse_henrique -
        total_custo_pecas
    )

    # ========================================================
    # DESPESAS / SAÍDAS DO PERÍODO
    # ========================================================

    filtro_despesas = ""
    parametros_desp = ()
    if data_inicio and data_fim:
        filtro_despesas = "WHERE date(data) BETWEEN date(?) AND date(?)"
        parametros_desp = (data_inicio, data_fim)

    cursor.execute(f"""
        SELECT COALESCE(SUM(valor), 0)
        FROM despesas
        {filtro_despesas}
    """, parametros_desp)
    total_despesas = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT
            categoria,
            COUNT(*),
            COALESCE(SUM(valor), 0)
        FROM despesas
        {filtro_despesas}
        GROUP BY categoria
        ORDER BY SUM(valor) DESC
    """, parametros_desp)
    despesas_por_categoria = cursor.fetchall()

    lucro_liquido_real = lucro_bruto_operacional - total_despesas

    # ========================================================
    # FATURAMENTO POR FORMA DE PAGAMENTO
    # ========================================================

    cursor.execute(f"""
        SELECT
            forma_pagamento,
            COUNT(*),
            COALESCE(SUM(valor_total), 0)

        FROM ordens_servico

        WHERE status = 'Finalizada'

        {filtro_data}

        GROUP BY forma_pagamento

        ORDER BY SUM(valor_total) DESC
    """, parametros)

    pagamentos = cursor.fetchall()

    # ========================================================
    # FATURAMENTO POR RESPONSÁVEL
    # ========================================================

    cursor.execute(f"""
        SELECT
            responsavel,
            COUNT(*),
            COALESCE(SUM(valor_total), 0),
            COALESCE(SUM(valor_mao_obra), 0),
            COALESCE(SUM(valor_repasse), 0)

        FROM ordens_servico

        WHERE status = 'Finalizada'

        {filtro_data}

        GROUP BY responsavel

        ORDER BY SUM(valor_total) DESC
    """, parametros)

    responsaveis = cursor.fetchall()

    # ========================================================
    # REPASSES POR EXECUTANTE / PRESTADOR
    # ========================================================

    filtro_data_servicos = ""
    if data_inicio and data_fim:
        filtro_data_servicos = "AND date(ordens_servico.data) BETWEEN date(?) AND date(?)"

    cursor.execute(f"""
        SELECT
            COALESCE(NULLIF(TRIM(servicos_os.executante), ''), ordens_servico.responsavel, 'Henrique') as prestador,
            COUNT(servicos_os.id_servico),
            COALESCE(SUM(servicos_os.valor), 0),
            COALESCE(SUM(servicos_os.valor_repasse), 0)
        FROM servicos_os
        INNER JOIN ordens_servico ON servicos_os.id_os = ordens_servico.id_os
        WHERE ordens_servico.status = 'Finalizada'
        {filtro_data_servicos}
        GROUP BY prestador
        ORDER BY SUM(servicos_os.valor_repasse) DESC
    """, parametros)
    repasses_por_executante = cursor.fetchall()

    conexao.close()

    return render_template(
        "financeiro.html",
        quantidade_os=quantidade_os,
        faturamento=faturamento,
        total_pecas=total_pecas,
        total_custo_pecas=total_custo_pecas,
        lucro_pecas=lucro_pecas,
        total_mao_obra=total_mao_obra,
        repasse_henrique=repasse_henrique,
        lucro_bruto_operacional=lucro_bruto_operacional,
        total_despesas=total_despesas,
        lucro_liquido_real=lucro_liquido_real,
        despesas_por_categoria=despesas_por_categoria,
        resultado_oficina=lucro_liquido_real,
        data_inicio=data_inicio,
        data_fim=data_fim,
        pagamentos=pagamentos,
        responsaveis=responsaveis,
        repasses_por_executante=repasses_por_executante
    )

# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
def dashboard():

    conexao = conectar_banco()
    cursor = conexao.cursor()

    # ========================================================
    # RESUMO GERAL
    # ========================================================

    cursor.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(valor_total), 0),
            COALESCE(SUM(valor_pecas), 0),
            COALESCE(SUM(valor_mao_obra), 0),
            COALESCE(SUM(valor_repasse), 0),
            COALESCE(SUM(valor_custo_pecas), 0)
        FROM ordens_servico
        WHERE status = 'Finalizada'
    """)

    dados = cursor.fetchone()

    quantidade_os = dados[0]
    faturamento = dados[1]
    total_pecas = dados[2]
    total_mao_obra = dados[3]
    repasse_henrique = dados[4]
    total_custo_pecas = dados[5]

    lucro_pecas = total_pecas - total_custo_pecas
    lucro_bruto_operacional = (
        faturamento -
        repasse_henrique -
        total_custo_pecas
    )

    # Total de Despesas gerais
    cursor.execute("SELECT COALESCE(SUM(valor), 0) FROM despesas")
    total_despesas = cursor.fetchone()[0]

    cursor.execute("""
        SELECT
            categoria,
            COUNT(*),
            COALESCE(SUM(valor), 0)
        FROM despesas
        GROUP BY categoria
        ORDER BY SUM(valor) DESC
    """)
    despesas_por_categoria = cursor.fetchall()

    lucro_liquido_real = lucro_bruto_operacional - total_despesas

    # ========================================================
    # FATURAMENTO POR FORMA DE PAGAMENTO
    # ========================================================

    cursor.execute("""
        SELECT
            COALESCE(forma_pagamento, 'Não informado'),
            COALESCE(SUM(valor_total), 0)
        FROM ordens_servico
        WHERE status = 'Finalizada'
        GROUP BY forma_pagamento
        ORDER BY SUM(valor_total) DESC
    """)

    pagamentos = cursor.fetchall()

    # ========================================================
    # FATURAMENTO POR RESPONSÁVEL
    # ========================================================

    cursor.execute("""
        SELECT
            COALESCE(responsavel, 'Não informado'),
            COALESCE(SUM(valor_total), 0)
        FROM ordens_servico
        WHERE status = 'Finalizada'
        GROUP BY responsavel
        ORDER BY SUM(valor_total) DESC
    """)

    responsaveis = cursor.fetchall()

    # ========================================================
    # EVOLUÇÃO DO FATURAMENTO POR MÊS
    # ========================================================

    cursor.execute("""
        SELECT
            strftime('%Y-%m', data) AS mes,
            COALESCE(SUM(valor_total), 0)
        FROM ordens_servico
        WHERE status = 'Finalizada'
        GROUP BY strftime('%Y-%m', data)
        ORDER BY mes
    """)

    faturamento_mensal = cursor.fetchall()

    conexao.close()

    return render_template(
        "dashboard.html",

        quantidade_os=quantidade_os,
        faturamento=faturamento,
        total_pecas=total_pecas,
        total_custo_pecas=total_custo_pecas,
        lucro_pecas=lucro_pecas,
        total_mao_obra=total_mao_obra,
        repasse_henrique=repasse_henrique,
        lucro_bruto_operacional=lucro_bruto_operacional,
        total_despesas=total_despesas,
        lucro_liquido_real=lucro_liquido_real,
        despesas_por_categoria=despesas_por_categoria,
        resultado_oficina=lucro_liquido_real,

        pagamentos=pagamentos,
        responsaveis=responsaveis,
        faturamento_mensal=faturamento_mensal
    )


# ============================================================
# ESQUECI A SENHA (REDEFINIÇÃO COM CHAVE MESTRA DA OFICINA)
# ============================================================

CHAVE_MESTRA_RECUPERACAO = os.environ.get("NEW_POWER_CHAVE_MESTRA", "NEWPOWER2026")
WHATSAPP_ADMIN = "5562991175451"

@app.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():

    mensagem = ""
    sucesso = ""

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        chave = request.form.get("chave_mestra", "").strip()
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        if not usuario or not chave or not nova_senha or not confirmar_senha:
            mensagem = "Preencha todos os campos."
        elif chave != CHAVE_MESTRA_RECUPERACAO:
            mensagem = "Chave de segurança da oficina incorreta."
        elif len(nova_senha) < 8:
            mensagem = "A nova senha deve ter no mínimo 8 caracteres."
        elif nova_senha != confirmar_senha:
            mensagem = "A confirmação da nova senha não confere."
        else:
            conexao = conectar_banco()
            cursor = conexao.cursor()
            cursor.execute("SELECT id_usuario FROM usuarios WHERE usuario = ?", (usuario,))
            dados = cursor.fetchone()

            if dados is None:
                mensagem = "Usuário não encontrado no sistema."
                conexao.close()
            else:
                cursor.execute(
                    """
                    UPDATE usuarios
                    SET senha = ?, senha_temporaria = 0
                    WHERE id_usuario = ?
                    """,
                    (generate_password_hash(nova_senha), dados[0])
                )
                conexao.commit()
                conexao.close()
                sucesso = "Sua senha foi redefinida com sucesso! Você já pode fazer login."

    return render_template(
        "esqueci_senha.html",
        mensagem=mensagem,
        sucesso=sucesso,
        whatsapp_admin=WHATSAPP_ADMIN
    )


# ============================================================
# GERENCIAMENTO DE BACKUPS (ADMIN)
# ============================================================

@app.route("/backups", methods=["GET"])
def backups():
    if not usuario_e_admin():
        return "Acesso negado. Apenas administradores podem gerenciar backups.", 403

    mensagem = request.args.get("mensagem", "")
    tipo = request.args.get("tipo", "")

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    arquivos = sorted(
        [f for f in BACKUPS_DIR.glob("backup_oficina_*.db") if f.is_file()],
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )

    hoje_str = datetime.now().strftime("%Y%m%d")
    lista_backups = []
    tamanho_total = 0

    for arq in arquivos:
        st = arq.stat()
        tamanho_bytes = st.st_size
        tamanho_total += tamanho_bytes

        if tamanho_bytes >= 1024 * 1024:
            tamanho_fmt = f"{tamanho_bytes / (1024 * 1024):.2f} MB"
        else:
            tamanho_fmt = f"{tamanho_bytes / 1024:.1f} KB"

        dt_mod = datetime.fromtimestamp(st.st_mtime)
        data_fmt = dt_mod.strftime("%d/%m/%Y às %H:%M:%S")
        eh_hoje = dt_mod.strftime("%Y%m%d") == hoje_str

        lista_backups.append({
            "nome": arq.name,
            "tamanho_formatado": tamanho_fmt,
            "data_formatada": data_fmt,
            "eh_hoje": eh_hoje,
            "mtime": st.st_mtime
        })

    if tamanho_total >= 1024 * 1024:
        total_fmt = f"{tamanho_total / (1024 * 1024):.2f} MB"
    else:
        total_fmt = f"{tamanho_total / 1024:.1f} KB"

    return render_template(
        "backup.html",
        backups=lista_backups,
        tamanho_total_formatado=total_fmt,
        mensagem=mensagem,
        tipo=tipo
    )


@app.route("/backups/criar", methods=["POST"])
def backups_criar():
    if not usuario_e_admin():
        return "Acesso negado. Apenas administradores podem gerenciar backups.", 403

    usuario = session.get("usuario", "admin")
    logger.info(f"Solicitação manual de backup iniciada pelo administrador: {usuario}")

    sucesso, res = executar_backup_sqlite()
    if sucesso:
        msg = f"Backup '{res}' gerado com sucesso!"
        return redirect(f"/backups?tipo=sucesso&mensagem={urllib.parse.quote(msg)}")
    else:
        msg = f"Falha ao gerar backup: {res}"
        return redirect(f"/backups?tipo=erro&mensagem={urllib.parse.quote(msg)}")


@app.route("/backups/<arquivo>/download", methods=["GET"])
def backups_download(arquivo):
    if not usuario_e_admin():
        return "Acesso negado. Apenas administradores podem baixar backups.", 403

    # Validação de segurança do nome do arquivo contra path traversal
    if not re.match(r"^backup_oficina_\d{8}_\d{6}\.db$", arquivo):
        logger.warning(f"Tentativa de download com nome inválido: {arquivo}")
        return "Nome de arquivo de backup inválido.", 400

    caminho = BACKUPS_DIR / arquivo
    if not caminho.is_file():
        return "Arquivo de backup não encontrado.", 404

    usuario = session.get("usuario", "admin")
    logger.info(f"Download do arquivo de backup '{arquivo}' realizado pelo administrador: {usuario}")
    return send_from_directory(BACKUPS_DIR, arquivo, as_attachment=True)


@app.route("/backups/<arquivo>/excluir", methods=["POST"])
def backups_excluir(arquivo):
    if not usuario_e_admin():
        return "Acesso negado. Apenas administradores podem excluir backups.", 403

    # Validação de segurança
    if not re.match(r"^backup_oficina_\d{8}_\d{6}\.db$", arquivo):
        return "Nome de arquivo inválido.", 400

    caminho = BACKUPS_DIR / arquivo
    if not caminho.is_file():
        return redirect(f"/backups?tipo=erro&mensagem={urllib.parse.quote('Arquivo não encontrado.')}")

    try:
        caminho.unlink()
        usuario = session.get("usuario", "admin")
        logger.info(f"Backup '{arquivo}' excluído pelo administrador: {usuario}")
        return redirect(f"/backups?tipo=sucesso&mensagem={urllib.parse.quote('Backup removido com sucesso.')}")
    except Exception as e:
        logger.error(f"Erro ao excluir backup {arquivo}: {e}", exc_info=True)
        return redirect(f"/backups?tipo=erro&mensagem={urllib.parse.quote('Erro ao excluir backup.')}")


@app.route("/backups/download-atual", methods=["GET"])
def backups_download_atual():
    """
    Gera um snapshot consistente instantâneo do banco atual via sqlite3.Connection.backup()
    e envia diretamente como download para o navegador do administrador.
    """
    if not usuario_e_admin():
        return "Acesso negado. Apenas administradores podem baixar o banco de dados.", 403

    if not banco.exists():
        return "Banco de dados não encontrado.", 404

    try:
        agora = datetime.now()
        nome_download = f"oficina_snapshot_{agora.strftime('%Y%m%d_%H%M%S')}.db"

        temp_dir = BACKUPS_DIR / ".temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        caminho_temp = temp_dir / nome_download

        conexao_origem = sqlite3.connect(banco, timeout=15.0)
        conexao_destino = sqlite3.connect(caminho_temp)
        try:
            conexao_origem.backup(conexao_destino)
        finally:
            conexao_destino.close()
            conexao_origem.close()

        usuario = session.get("usuario", "admin")
        logger.info(f"Snapshot instantâneo '{nome_download}' gerado e baixado pelo administrador: {usuario}")

        return send_file(
            caminho_temp,
            as_attachment=True,
            download_name=nome_download,
            mimetype="application/x-sqlite3"
        )
    except Exception as e:
        logger.error(f"Erro ao gerar snapshot atual do banco: {e}", exc_info=True)
        return f"Erro ao processar download do banco atual: {e}", 500


# ============================================================
# CENTRAL DE RELATÓRIOS E EXPORTAÇÃO (PDF, EXCEL/CSV, XML, JSON)
# ============================================================

def formatar_moeda_br(valor):
    if valor is None:
        return "R$ 0,00"
    try:
        val = float(valor)
    except (ValueError, TypeError):
        return "R$ 0,00"
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_data_br(data_iso):
    if not data_iso:
        return ""
    try:
        partes = str(data_iso).split(" ")[0].split("-")
        if len(partes) == 3:
            return f"{partes[2]}/{partes[1]}/{partes[0]}"
    except Exception:
        pass
    return str(data_iso)


class NumberedCanvasRelatorios(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#777777"))
        width, height = self._pagesize

        # Linha divisória do rodapé
        self.setStrokeColor(colors.HexColor("#dddddd"))
        self.setLineWidth(0.5)
        self.line(12 * mm, 11 * mm, width - 12 * mm, 11 * mm)

        agora = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")
        texto_esq = f"New Power Auto Mecânica • Relatório Gerencial Confidencial • Emitido em {agora}"
        texto_dir = f"Página {self._pageNumber} de {page_count}"

        self.drawString(12 * mm, 7 * mm, texto_esq)
        self.drawRightString(width - 12 * mm, 7 * mm, texto_dir)
        self.restoreState()


def obter_dados_relatorio_servicos(data_inicio="", data_fim=""):
    conexao = conectar_banco()
    cursor = conexao.cursor()

    filtro_data = ""
    parametros = []
    if data_inicio and data_fim:
        filtro_data = "AND date(os.data) BETWEEN date(?) AND date(?)"
        parametros = [data_inicio, data_fim]
    elif data_inicio:
        filtro_data = "AND date(os.data) >= date(?)"
        parametros = [data_inicio]
    elif data_fim:
        filtro_data = "AND date(os.data) <= date(?)"
        parametros = [data_fim]

    cursor.execute(f"""
        SELECT
            os.id_os,
            os.data,
            COALESCE(c.nome, 'Não informado') AS cliente,
            COALESCE(v.placa, 'S/ Placa') AS placa,
            COALESCE(v.modelo, '') AS modelo,
            s.descricao AS servico,
            COALESCE(s.executante, 'Não informado') AS executante,
            COALESCE(s.valor, 0) AS valor,
            COALESCE(s.valor_repasse, 0) AS valor_repasse
        FROM servicos_os s
        JOIN ordens_servico os ON s.id_os = os.id_os
        LEFT JOIN clientes c ON os.id_cliente = c.id_cliente
        LEFT JOIN veiculos v ON os.id_veiculo = v.id_veiculo
        WHERE os.status = 'Finalizada'
        {filtro_data}
        ORDER BY os.data DESC, os.id_os DESC
    """, parametros)

    linhas = cursor.fetchall()
    conexao.close()

    total_servicos = len(linhas)
    total_faturado = sum(float(r[7]) for r in linhas)
    total_repasses = sum(float(r[8]) for r in linhas)
    saldo_oficina = total_faturado - total_repasses

    return {
        "linhas": linhas,
        "totais": {
            "total_servicos": total_servicos,
            "total_faturado": total_faturado,
            "total_repasses": total_repasses,
            "saldo_oficina": saldo_oficina
        }
    }


def obter_dados_relatorio_pecas(data_inicio="", data_fim=""):
    conexao = conectar_banco()
    cursor = conexao.cursor()

    filtro_data = ""
    parametros = []
    if data_inicio and data_fim:
        filtro_data = "AND date(os.data) BETWEEN date(?) AND date(?)"
        parametros = [data_inicio, data_fim]
    elif data_inicio:
        filtro_data = "AND date(os.data) >= date(?)"
        parametros = [data_inicio]
    elif data_fim:
        filtro_data = "AND date(os.data) <= date(?)"
        parametros = [data_fim]

    cursor.execute(f"""
        SELECT
            os.id_os,
            os.data,
            p.descricao AS peca,
            p.quantidade,
            COALESCE(p.valor_unitario, 0) AS valor_unitario,
            COALESCE(p.valor_custo_unitario, 0) AS valor_custo_unitario,
            COALESCE(p.valor_total, 0) AS total_venda,
            (p.quantidade * COALESCE(p.valor_custo_unitario, 0)) AS total_custo,
            (COALESCE(p.valor_total, 0) - (p.quantidade * COALESCE(p.valor_custo_unitario, 0))) AS lucro_bruto
        FROM pecas_os p
        JOIN ordens_servico os ON p.id_os = os.id_os
        WHERE os.status = 'Finalizada'
        {filtro_data}
        ORDER BY os.data DESC, os.id_os DESC
    """, parametros)

    linhas = cursor.fetchall()
    conexao.close()

    total_itens = sum(float(r[3]) for r in linhas)
    total_faturado_pecas = sum(float(r[6]) for r in linhas)
    total_custo_pecas = sum(float(r[7]) for r in linhas)
    lucro_total_pecas = sum(float(r[8]) for r in linhas)
    margem_pecas = (lucro_total_pecas / total_faturado_pecas * 100) if total_faturado_pecas > 0 else 0.0

    return {
        "linhas": linhas,
        "totais": {
            "total_registros": len(linhas),
            "total_itens": total_itens,
            "total_faturado_pecas": total_faturado_pecas,
            "total_custo_pecas": total_custo_pecas,
            "lucro_total_pecas": lucro_total_pecas,
            "margem_pecas": margem_pecas
        }
    }


def obter_dados_relatorio_prestadores(data_inicio="", data_fim=""):
    conexao = conectar_banco()
    cursor = conexao.cursor()

    filtro_data = ""
    parametros = []
    if data_inicio and data_fim:
        filtro_data = "AND date(os.data) BETWEEN date(?) AND date(?)"
        parametros = [data_inicio, data_fim]
    elif data_inicio:
        filtro_data = "AND date(os.data) >= date(?)"
        parametros = [data_inicio]
    elif data_fim:
        filtro_data = "AND date(os.data) <= date(?)"
        parametros = [data_fim]

    cursor.execute(f"""
        SELECT
            COALESCE(s.executante, 'Não informado') AS prestador,
            COUNT(s.id_servico) AS qtd_servicos,
            COALESCE(SUM(s.valor), 0) AS total_faturado,
            COALESCE(SUM(s.valor_repasse), 0) AS total_repasse,
            COALESCE(SUM(s.valor) - SUM(s.valor_repasse), 0) AS retido_oficina
        FROM servicos_os s
        JOIN ordens_servico os ON s.id_os = os.id_os
        WHERE os.status = 'Finalizada'
        {filtro_data}
        GROUP BY s.executante
        ORDER BY total_faturado DESC
    """, parametros)

    linhas = cursor.fetchall()
    conexao.close()

    total_servicos = sum(int(r[1]) for r in linhas)
    total_faturado = sum(float(r[2]) for r in linhas)
    total_repasses = sum(float(r[3]) for r in linhas)
    total_retido = sum(float(r[4]) for r in linhas)

    return {
        "linhas": linhas,
        "totais": {
            "total_prestadores": len(linhas),
            "total_servicos": total_servicos,
            "total_faturado": total_faturado,
            "total_repasses": total_repasses,
            "total_retido": total_retido
        }
    }


def obter_dados_relatorio_financeiro(data_inicio="", data_fim=""):
    conexao = conectar_banco()
    cursor = conexao.cursor()

    filtro_os = ""
    filtro_desp = ""
    parametros = []
    if data_inicio and data_fim:
        filtro_os = "AND date(data) BETWEEN date(?) AND date(?)"
        filtro_desp = "WHERE date(data) BETWEEN date(?) AND date(?)"
        parametros = [data_inicio, data_fim]
    elif data_inicio:
        filtro_os = "AND date(data) >= date(?)"
        filtro_desp = "WHERE date(data) >= date(?)"
        parametros = [data_inicio]
    elif data_fim:
        filtro_os = "AND date(data) <= date(?)"
        filtro_desp = "WHERE date(data) <= date(?)"
        parametros = [data_fim]

    # Resumo das OSs
    cursor.execute(f"""
        SELECT
            COUNT(*),
            COALESCE(SUM(valor_total), 0),
            COALESCE(SUM(valor_pecas), 0),
            COALESCE(SUM(valor_mao_obra), 0),
            COALESCE(SUM(valor_repasse), 0),
            COALESCE(SUM(valor_custo_pecas), 0)
        FROM ordens_servico
        WHERE status = 'Finalizada'
        {filtro_os}
    """, parametros)
    dados_os = cursor.fetchone()

    qtd_os = dados_os[0]
    faturamento_total = dados_os[1]
    faturamento_pecas = dados_os[2]
    faturamento_mao_obra = dados_os[3]
    total_repasses = dados_os[4]
    custo_pecas = dados_os[5]

    lucro_pecas = faturamento_pecas - custo_pecas
    lucro_bruto_operacional = faturamento_total - total_repasses - custo_pecas

    # Despesas do período
    cursor.execute(f"""
        SELECT COALESCE(SUM(valor), 0)
        FROM despesas
        {filtro_desp}
    """, parametros)
    total_despesas = cursor.fetchone()[0]

    # Despesas agrupadas por categoria
    cursor.execute(f"""
        SELECT
            categoria,
            COUNT(*),
            COALESCE(SUM(valor), 0)
        FROM despesas
        {filtro_desp}
        GROUP BY categoria
        ORDER BY SUM(valor) DESC
    """, parametros)
    despesas_categorias = cursor.fetchall()

    lucro_liquido_real = lucro_bruto_operacional - total_despesas

    # Lista individual de despesas
    cursor.execute(f"""
        SELECT
            id_despesa,
            data,
            categoria,
            descricao,
            COALESCE(valor, 0)
        FROM despesas
        {filtro_desp}
        ORDER BY data DESC, id_despesa DESC
    """, parametros)
    lista_despesas = cursor.fetchall()

    conexao.close()

    return {
        "qtd_os": qtd_os,
        "faturamento_total": faturamento_total,
        "faturamento_pecas": faturamento_pecas,
        "faturamento_mao_obra": faturamento_mao_obra,
        "total_repasses": total_repasses,
        "custo_pecas": custo_pecas,
        "lucro_pecas": lucro_pecas,
        "lucro_bruto_operacional": lucro_bruto_operacional,
        "total_despesas": total_despesas,
        "lucro_liquido_real": lucro_liquido_real,
        "despesas_categorias": despesas_categorias,
        "lista_despesas": lista_despesas
    }


def gerar_csv_relatorio(colunas, linhas_dados, nome_arquivo):
    """
    Gera CSV no padrão brasileiro para Excel:
    - Delimitador ';'
    - UTF-8 com BOM (utf-8-sig)
    """
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(colunas)
    for linha in linhas_dados:
        writer.writerow(linha)

    conteudo_bytes = output.getvalue().encode("utf-8-sig")
    return Response(
        conteudo_bytes,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'}
    )


def gerar_xml_relatorio(tipo, periodo_str, colunas, linhas_dados, totais, nome_arquivo):
    """
    Gera XML estruturado para sistemas contábeis, ERPs ou pipelines de BI.
    """
    raiz = ET.Element("relatorio", tipo=tipo, gerado_em=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    oficina_el = ET.SubElement(raiz, "oficina")
    ET.SubElement(oficina_el, "nome").text = "New Power Auto Mecânica"
    ET.SubElement(oficina_el, "telefone").text = "(62) 99117-5451"
    ET.SubElement(oficina_el, "endereco").text = "Rua V-3, Qd. V3, Lt. 18 - Vila Rezende - Goiânia/GO"

    periodo_el = ET.SubElement(raiz, "periodo")
    periodo_el.text = periodo_str

    if totais:
        totais_el = ET.SubElement(raiz, "totais")
        for chave, val in totais.items():
            tag_limpa = re.sub(r"[^a-zA-Z0-9_]", "_", str(chave).lower()).strip("_")
            ET.SubElement(totais_el, tag_limpa).text = str(val)

    itens_el = ET.SubElement(raiz, "itens")
    tags_colunas = [re.sub(r"[^a-zA-Z0-9_]", "_", str(col).lower()).strip("_") for col in colunas]

    for linha in linhas_dados:
        item_el = ET.SubElement(itens_el, "item")
        for i, val in enumerate(linha):
            tag = tags_colunas[i] if i < len(tags_colunas) else f"coluna_{i+1}"
            ET.SubElement(item_el, tag).text = str(val) if val is not None else ""

    xml_bytes = ET.tostring(raiz, encoding="utf-8", xml_declaration=True)
    return Response(
        xml_bytes,
        mimetype="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'}
    )


def gerar_pdf_relatorio_gerencial(tipo, titulo, colunas, col_larguras, linhas_formatadas, totais_cards, periodo_str, nome_arquivo):
    """
    Gera PDF diagramado no padrão executivo da New Power usando ReportLab.
    """
    buffer = io.BytesIO()

    # Landscape para tabelas largas (servicos e pecas), portrait para prestadores e financeiro
    usar_landscape = tipo in ("mao-de-obra", "pecas")
    tamanho_pagina = landscape(A4) if usar_landscape else A4

    doc = SimpleDocTemplate(
        buffer,
        pagesize=tamanho_pagina,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm
    )

    estilos = getSampleStyleSheet()

    estilo_titulo_empresa = ParagraphStyle(
        "TituloEmpresaRel",
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        textColor=colors.HexColor("#222222")
    )
    estilo_sub_empresa = ParagraphStyle(
        "SubEmpresaRel",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#555555")
    )
    estilo_titulo_relatorio = ParagraphStyle(
        "TituloRelatorioDoc",
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#d71920"),
        alignment=2
    )
    estilo_periodo = ParagraphStyle(
        "PeriodoRelatorioDoc",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#444444"),
        alignment=2
    )

    elementos = []

    # Cabeçalho
    caminho_logo = pasta_projeto / "static" / "logo.jpg"
    bloco_logo = []
    if caminho_logo.exists():
        bloco_logo = [Image(str(caminho_logo), width=32 * mm, height=20 * mm)]

    info_oficina = [
        Paragraph("NEW POWER AUTO MECÂNICA", estilo_titulo_empresa),
        Paragraph("Rua V-3, Qd. V3, Lt. 18 - Vila Rezende - Goiânia/GO • Tel: (62) 99117-5451", estilo_sub_empresa)
    ]

    info_titulo = [
        Paragraph(titulo.upper(), estilo_titulo_relatorio),
        Paragraph(f"Período: {periodo_str}", estilo_periodo)
    ]

    largura_total = 273 * mm if usar_landscape else 186 * mm
    if bloco_logo:
        cabecalho_tab = Table([[bloco_logo[0], info_oficina, info_titulo]], colWidths=[36 * mm, largura_total - 116 * mm, 80 * mm])
    else:
        cabecalho_tab = Table([[info_oficina, info_titulo]], colWidths=[largura_total - 85 * mm, 85 * mm])

    cabecalho_tab.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elementos.append(cabecalho_tab)
    elementos.append(Spacer(1, 4 * mm))

    # Cards / Caixa de Totais do Relatório
    if totais_cards:
        linha_titulos = []
        linha_valores = []
        for c in totais_cards:
            linha_titulos.append(Paragraph(f"<b>{c['titulo'].upper()}</b>", ParagraphStyle("THKpiRel", fontName="Helvetica-Bold", fontSize=7.5, textColor=colors.HexColor("#555555"), alignment=1)))
            linha_valores.append(Paragraph(f"<b>{c['valor']}</b>", ParagraphStyle("TKpiRel", fontName="Helvetica-Bold", fontSize=10.5, textColor=colors.HexColor(c.get("cor", "#222222")), alignment=1)))
        
        tab_totais = Table([linha_titulos, linha_valores], colWidths=[largura_total / len(totais_cards)] * len(totais_cards))
        tab_totais.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f9fa")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#dddddd")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e5e5")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        elementos.append(tab_totais)
        elementos.append(Spacer(1, 5 * mm))

    # Tabela de Dados
    estilo_th = ParagraphStyle("THDRel", fontName="Helvetica-Bold", fontSize=7.5, textColor=colors.white, alignment=1)
    estilo_td = ParagraphStyle("TDDRel", fontName="Helvetica", fontSize=7, leading=9, textColor=colors.HexColor("#222222"))
    estilo_td_num = ParagraphStyle("TDDNumRel", fontName="Helvetica", fontSize=7, leading=9, textColor=colors.HexColor("#222222"), alignment=2)
    estilo_td_center = ParagraphStyle("TDDCenterRel", fontName="Helvetica", fontSize=7, leading=9, textColor=colors.HexColor("#222222"), alignment=1)

    headers_cells = [Paragraph(f"<b>{c}</b>", estilo_th) for c in colunas]
    linhas_tabela = [headers_cells]

    for row in linhas_formatadas:
        row_cells = []
        for i, val in enumerate(row):
            texto = str(val) if val is not None else ""
            if "R$" in texto or (isinstance(val, (int, float)) and not str(val).startswith("#")):
                row_cells.append(Paragraph(texto, estilo_td_num))
            elif texto.startswith("#") or (len(texto) <= 10 and "/" in texto):
                row_cells.append(Paragraph(texto, estilo_td_center))
            else:
                row_cells.append(Paragraph(texto, estilo_td))
        linhas_tabela.append(row_cells)

    larguras_pt = [l * mm for l in col_larguras]
    tab_dados = Table(linhas_tabela, colWidths=larguras_pt, repeatRows=1)
    tab_dados.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222222")),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfbfb")]),
    ]))
    elementos.append(tab_dados)

    doc.build(elementos, canvasmaker=NumberedCanvasRelatorios)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=nome_arquivo
    )


@app.route("/relatorios")
def relatorios():
    tipo = request.args.get("tipo", "mao-de-obra").strip().lower()
    data_inicio = request.args.get("data_inicio", "").strip()
    data_fim = request.args.get("data_fim", "").strip()

    if data_inicio and data_fim:
        periodo_fmt = f"{formatar_data_br(data_inicio)} até {formatar_data_br(data_fim)}"
    elif data_inicio:
        periodo_fmt = f"A partir de {formatar_data_br(data_inicio)}"
    elif data_fim:
        periodo_fmt = f"Até {formatar_data_br(data_fim)}"
    else:
        periodo_fmt = "Todo o Período Cadastrado"

    if tipo == "pecas":
        titulo_relatorio = "Peças & Margem de Lucro"
        dados = obter_dados_relatorio_pecas(data_inicio, data_fim)
        tot = dados["totais"]
        kpis = [
            {"titulo": "Total Faturado em Peças", "valor": formatar_moeda_br(tot["total_faturado_pecas"]), "cor_classe": "kpi-azul"},
            {"titulo": "Custo Total das Peças", "valor": formatar_moeda_br(tot["total_custo_pecas"]), "cor_classe": "kpi-vermelho"},
            {"titulo": "Lucro Bruto em Peças", "valor": formatar_moeda_br(tot["lucro_total_pecas"]), "cor_classe": "kpi-verde"},
            {"titulo": "Margem de Lucro", "valor": f"{tot['margem_pecas']:.1f}%", "cor_classe": "kpi-verde", "subtitulo": f"{tot['total_itens']:.0f} itens vendidos"}
        ]
        colunas = [
            {"nome": "OS", "alinhamento": "center"},
            {"nome": "Data", "alinhamento": "center"},
            {"nome": "Peça", "alinhamento": "left"},
            {"nome": "Qtd", "alinhamento": "center"},
            {"nome": "Venda Unit.", "alinhamento": "right"},
            {"nome": "Custo Unit.", "alinhamento": "right"},
            {"nome": "Total Venda", "alinhamento": "right"},
            {"nome": "Total Custo", "alinhamento": "right"},
            {"nome": "Lucro Bruto", "alinhamento": "right"}
        ]
        colunas_direita = [4, 5, 6, 7, 8]
        colunas_centro = [0, 1, 3]
        itens_formatados = []
        for r in dados["linhas"]:
            itens_formatados.append([
                f"#{r[0]:04d}",
                formatar_data_br(r[1]),
                r[2],
                f"{r[3]:g}",
                formatar_moeda_br(r[4]),
                formatar_moeda_br(r[5]),
                formatar_moeda_br(r[6]),
                formatar_moeda_br(r[7]),
                formatar_moeda_br(r[8])
            ])

    elif tipo == "prestadores":
        titulo_relatorio = "Produtividade da Equipe & Parceiros"
        dados = obter_dados_relatorio_prestadores(data_inicio, data_fim)
        tot = dados["totais"]
        kpis = [
            {"titulo": "Mecânicos / Parceiros", "valor": str(tot["total_prestadores"]), "cor_classe": "kpi-azul"},
            {"titulo": "Serviços Executados", "valor": str(tot["total_servicos"]), "cor_classe": "kpi-azul"},
            {"titulo": "Faturamento Produzido", "valor": formatar_moeda_br(tot["total_faturado"]), "cor_classe": "kpi-verde"},
            {"titulo": "Repasses a Pagar", "valor": formatar_moeda_br(tot["total_repasses"]), "cor_classe": "kpi-vermelho", "subtitulo": f"Saldo oficina: {formatar_moeda_br(tot['total_retido'])}"}
        ]
        colunas = [
            {"nome": "Profissional / Parceiro", "alinhamento": "left"},
            {"nome": "Qtd Serviços", "alinhamento": "center"},
            {"nome": "Total Faturado", "alinhamento": "right"},
            {"nome": "Repasse Devido", "alinhamento": "right"},
            {"nome": "Retido pela Oficina", "alinhamento": "right"}
        ]
        colunas_direita = [2, 3, 4]
        colunas_centro = [1]
        itens_formatados = []
        for r in dados["linhas"]:
            itens_formatados.append([
                r[0],
                str(r[1]),
                formatar_moeda_br(r[2]),
                formatar_moeda_br(r[3]),
                formatar_moeda_br(r[4])
            ])

    elif tipo == "financeiro":
        titulo_relatorio = "Fechamento Financeiro / DRE Consolidado"
        dados = obter_dados_relatorio_financeiro(data_inicio, data_fim)
        kpis = [
            {"titulo": "Faturamento Bruto", "valor": formatar_moeda_br(dados["faturamento_total"]), "cor_classe": "kpi-azul", "subtitulo": f"{dados['qtd_os']} ordens de serviço"},
            {"titulo": "Custos (Peças + Repasses)", "valor": formatar_moeda_br(dados["custo_pecas"] + dados["total_repasses"]), "cor_classe": "kpi-vermelho"},
            {"titulo": "Despesas Operacionais", "valor": formatar_moeda_br(dados["total_despesas"]), "cor_classe": "kpi-vermelho"},
            {"titulo": "Lucro Líquido Real", "valor": formatar_moeda_br(dados["lucro_liquido_real"]), "cor_classe": "kpi-verde" if dados["lucro_liquido_real"] >= 0 else "kpi-vermelho"}
        ]
        colunas = [
            {"nome": "Data", "alinhamento": "center"},
            {"nome": "Tipo / Categoria", "alinhamento": "left"},
            {"nome": "Descrição do Lançamento", "alinhamento": "left"},
            {"nome": "Valor da Despesa", "alinhamento": "right"}
        ]
        colunas_direita = [3]
        colunas_centro = [0]
        itens_formatados = []
        for r in dados["lista_despesas"]:
            itens_formatados.append([
                formatar_data_br(r[1]),
                r[2],
                r[3],
                formatar_moeda_br(r[4])
            ])

    else:
        # Default: mão de obra
        tipo = "mao-de-obra"
        titulo_relatorio = "Mão de Obra & Serviços Executados"
        dados = obter_dados_relatorio_servicos(data_inicio, data_fim)
        tot = dados["totais"]
        kpis = [
            {"titulo": "Serviços Executados", "valor": str(tot["total_servicos"]), "cor_classe": "kpi-azul"},
            {"titulo": "Total em Mão de Obra", "valor": formatar_moeda_br(tot["total_faturado"]), "cor_classe": "kpi-azul"},
            {"titulo": "Repasses da Equipe", "valor": formatar_moeda_br(tot["total_repasses"]), "cor_classe": "kpi-vermelho"},
            {"titulo": "Retido pela Oficina", "valor": formatar_moeda_br(tot["saldo_oficina"]), "cor_classe": "kpi-verde"}
        ]
        colunas = [
            {"nome": "OS", "alinhamento": "center"},
            {"nome": "Data", "alinhamento": "center"},
            {"nome": "Cliente", "alinhamento": "left"},
            {"nome": "Veículo", "alinhamento": "left"},
            {"nome": "Serviço", "alinhamento": "left"},
            {"nome": "Executante", "alinhamento": "left"},
            {"nome": "Valor", "alinhamento": "right"},
            {"nome": "Repasse", "alinhamento": "right"}
        ]
        colunas_direita = [6, 7]
        colunas_centro = [0, 1]
        itens_formatados = []
        for r in dados["linhas"]:
            itens_formatados.append([
                f"#{r[0]:04d}",
                formatar_data_br(r[1]),
                r[2],
                f"{r[3]} {r[4]}".strip(),
                r[5],
                r[6],
                formatar_moeda_br(r[7]),
                formatar_moeda_br(r[8])
            ])

    return render_template(
        "relatorios.html",
        tipo=tipo,
        titulo_relatorio=titulo_relatorio,
        data_inicio=data_inicio,
        data_fim=data_fim,
        periodo_formatado=periodo_fmt,
        kpis=kpis,
        colunas=colunas,
        colunas_alinhadas_direita=colunas_direita,
        colunas_alinhadas_centro=colunas_centro,
        itens=itens_formatados
    )


@app.route("/relatorios/<tipo>/<formato>")
def exportar_relatorio(tipo, formato):
    tipo = tipo.strip().lower()
    formato = formato.strip().lower()

    if tipo not in ("mao-de-obra", "pecas", "prestadores", "financeiro"):
        return "Tipo de relatório inválido.", 400

    if formato not in ("pdf", "csv", "xml"):
        return "Formato de exportação inválido. Use 'pdf', 'csv' ou 'xml'.", 400

    data_inicio = request.args.get("data_inicio", "").strip()
    data_fim = request.args.get("data_fim", "").strip()

    if data_inicio and data_fim:
        periodo_fmt = f"{formatar_data_br(data_inicio)} até {formatar_data_br(data_fim)}"
        sufixo_data = f"{data_inicio}_{data_fim}"
    elif data_inicio:
        periodo_fmt = f"A partir de {formatar_data_br(data_inicio)}"
        sufixo_data = f"desde_{data_inicio}"
    elif data_fim:
        periodo_fmt = f"Até {formatar_data_br(data_fim)}"
        sufixo_data = f"ate_{data_fim}"
    else:
        periodo_fmt = "Todo o Período"
        sufixo_data = "completo"

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    if tipo == "mao-de-obra":
        titulo = "Relatório de Mão de Obra e Serviços"
        dados = obter_dados_relatorio_servicos(data_inicio, data_fim)
        tot = dados["totais"]
        colunas = ["OS", "Data", "Cliente", "Veículo", "Serviço", "Executante", "Valor (R$)", "Repasse (R$)"]
        col_larguras = [18, 22, 45, 40, 60, 40, 24, 24] # mm
        totais_cards = [
            {"titulo": "Total Serviços", "valor": str(tot["total_servicos"])},
            {"titulo": "Faturamento Mão de Obra", "valor": formatar_moeda_br(tot["total_faturado"]), "cor": "#0d6efd"},
            {"titulo": "Total Repasses Equipe", "valor": formatar_moeda_br(tot["total_repasses"]), "cor": "#dc3545"},
            {"titulo": "Saldo Oficina", "valor": formatar_moeda_br(tot["saldo_oficina"]), "cor": "#198754"}
        ]
        linhas_dados = []
        for r in dados["linhas"]:
            linhas_dados.append([
                f"#{r[0]:04d}",
                formatar_data_br(r[1]),
                r[2],
                f"{r[3]} {r[4]}".strip(),
                r[5],
                r[6],
                f"{float(r[7]):.2f}".replace(".", ","),
                f"{float(r[8]):.2f}".replace(".", ",")
            ])
        nome_base = f"relatorio_mao_de_obra_{sufixo_data}_{timestamp_str}"

    elif tipo == "pecas":
        titulo = "Relatório de Peças e Lucratividade"
        dados = obter_dados_relatorio_pecas(data_inicio, data_fim)
        tot = dados["totais"]
        colunas = ["OS", "Data", "Peça", "Qtd", "Venda Unit.", "Custo Unit.", "Total Venda", "Total Custo", "Lucro Bruto"]
        col_larguras = [16, 20, 52, 14, 26, 26, 28, 28, 28] # mm
        totais_cards = [
            {"titulo": "Total Faturado Peças", "valor": formatar_moeda_br(tot["total_faturado_pecas"]), "cor": "#0d6efd"},
            {"titulo": "Custo das Peças", "valor": formatar_moeda_br(tot["total_custo_pecas"]), "cor": "#dc3545"},
            {"titulo": "Lucro Bruto Peças", "valor": formatar_moeda_br(tot["lucro_total_pecas"]), "cor": "#198754"},
            {"titulo": "Margem de Lucro", "valor": f"{tot['margem_pecas']:.1f}%", "cor": "#198754"}
        ]
        linhas_dados = []
        for r in dados["linhas"]:
            linhas_dados.append([
                f"#{r[0]:04d}",
                formatar_data_br(r[1]),
                r[2],
                f"{r[3]:g}",
                f"{float(r[4]):.2f}".replace(".", ","),
                f"{float(r[5]):.2f}".replace(".", ","),
                f"{float(r[6]):.2f}".replace(".", ","),
                f"{float(r[7]):.2f}".replace(".", ","),
                f"{float(r[8]):.2f}".replace(".", ",")
            ])
        nome_base = f"relatorio_pecas_margem_{sufixo_data}_{timestamp_str}"

    elif tipo == "prestadores":
        titulo = "Relatório de Produtividade da Equipe e Parceiros"
        dados = obter_dados_relatorio_prestadores(data_inicio, data_fim)
        tot = dados["totais"]
        colunas = ["Profissional / Parceiro", "Qtd Serviços", "Total Faturado (R$)", "Repasse Devido (R$)", "Retido Oficina (R$)"]
        col_larguras = [65, 25, 32, 32, 32] # mm (portrait total 186mm)
        totais_cards = [
            {"titulo": "Profissionais Ativos", "valor": str(tot["total_prestadores"])},
            {"titulo": "Serviços Realizados", "valor": str(tot["total_servicos"])},
            {"titulo": "Faturamento Produzido", "valor": formatar_moeda_br(tot["total_faturado"]), "cor": "#0d6efd"},
            {"titulo": "Repasses a Pagar", "valor": formatar_moeda_br(tot["total_repasses"]), "cor": "#dc3545"}
        ]
        linhas_dados = []
        for r in dados["linhas"]:
            linhas_dados.append([
                r[0],
                str(r[1]),
                f"{float(r[2]):.2f}".replace(".", ","),
                f"{float(r[3]):.2f}".replace(".", ","),
                f"{float(r[4]):.2f}".replace(".", ",")
            ])
        nome_base = f"relatorio_equipe_repasses_{sufixo_data}_{timestamp_str}"

    elif tipo == "financeiro":
        titulo = "Extrato Financeiro e DRE da Oficina"
        dados = obter_dados_relatorio_financeiro(data_inicio, data_fim)
        colunas = ["Data", "Categoria da Despesa", "Descrição do Lançamento", "Valor (R$)"]
        col_larguras = [25, 45, 80, 36] # mm (portrait total 186mm)
        totais_cards = [
            {"titulo": "Faturamento Bruto", "valor": formatar_moeda_br(dados["faturamento_total"]), "cor": "#0d6efd"},
            {"titulo": "Custos (Peças + Repasses)", "valor": formatar_moeda_br(dados["custo_pecas"] + dados["total_repasses"]), "cor": "#dc3545"},
            {"titulo": "Despesas Gerais", "valor": formatar_moeda_br(dados["total_despesas"]), "cor": "#dc3545"},
            {"titulo": "Lucro Líquido Real", "valor": formatar_moeda_br(dados["lucro_liquido_real"]), "cor": "#198754" if dados["lucro_liquido_real"] >= 0 else "#dc3545"}
        ]
        linhas_dados = []
        for r in dados["lista_despesas"]:
            linhas_dados.append([
                formatar_data_br(r[1]),
                r[2],
                r[3],
                f"{float(r[4]):.2f}".replace(".", ",")
            ])
        nome_base = f"fechamento_financeiro_dre_{sufixo_data}_{timestamp_str}"

    usuario_logado = session.get("usuario", "anonimo")
    logger.info(f"Exportação de relatório gerada: tipo='{tipo}', formato='{formato}', periodo='{periodo_fmt}', user='{usuario_logado}'")

    if formato == "pdf":
        return gerar_pdf_relatorio_gerencial(
            tipo=tipo,
            titulo=titulo,
            colunas=colunas,
            col_larguras=col_larguras,
            linhas_formatadas=linhas_dados,
            totais_cards=totais_cards,
            periodo_str=periodo_fmt,
            nome_arquivo=f"{nome_base}.pdf"
        )
    elif formato == "csv":
        return gerar_csv_relatorio(
            colunas=colunas,
            linhas_dados=linhas_dados,
            nome_arquivo=f"{nome_base}.csv"
        )
    elif formato == "xml":
        totais_dict = {c["titulo"]: c["valor"] for c in totais_cards}
        return gerar_xml_relatorio(
            tipo=tipo,
            periodo_str=periodo_fmt,
            colunas=colunas,
            linhas_dados=linhas_dados,
            totais=totais_dict,
            nome_arquivo=f"{nome_base}.xml"
        )


@app.route("/api/relatorios/<tipo>")
def api_relatorios(tipo):
    """
    Endpoint JSON REST para consumo de dashboards externos, Power BI,
    mobile apps ou automações.
    """
    tipo = tipo.strip().lower()
    data_inicio = request.args.get("data_inicio", "").strip()
    data_fim = request.args.get("data_fim", "").strip()

    if tipo == "mao-de-obra":
        dados = obter_dados_relatorio_servicos(data_inicio, data_fim)
        linhas_json = [
            {
                "id_os": r[0],
                "data": r[1],
                "cliente": r[2],
                "placa": r[3],
                "modelo": r[4],
                "servico": r[5],
                "executante": r[6],
                "valor": float(r[7]),
                "valor_repasse": float(r[8])
            }
            for r in dados["linhas"]
        ]
        return jsonify({
            "status": "sucesso",
            "tipo": tipo,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "totais": dados["totais"],
            "registros": linhas_json
        })

    elif tipo == "pecas":
        dados = obter_dados_relatorio_pecas(data_inicio, data_fim)
        linhas_json = [
            {
                "id_os": r[0],
                "data": r[1],
                "peca": r[2],
                "quantidade": float(r[3]),
                "valor_unitario": float(r[4]),
                "valor_custo_unitario": float(r[5]),
                "valor_total_venda": float(r[6]),
                "valor_total_custo": float(r[7]),
                "lucro_bruto": float(r[8])
            }
            for r in dados["linhas"]
        ]
        return jsonify({
            "status": "sucesso",
            "tipo": tipo,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "totais": dados["totais"],
            "registros": linhas_json
        })

    elif tipo == "prestadores":
        dados = obter_dados_relatorio_prestadores(data_inicio, data_fim)
        linhas_json = [
            {
                "prestador": r[0],
                "qtd_servicos": int(r[1]),
                "total_faturado": float(r[2]),
                "total_repasse": float(r[3]),
                "retido_oficina": float(r[4])
            }
            for r in dados["linhas"]
        ]
        return jsonify({
            "status": "sucesso",
            "tipo": tipo,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "totais": dados["totais"],
            "registros": linhas_json
        })

    elif tipo == "financeiro":
        dados = obter_dados_relatorio_financeiro(data_inicio, data_fim)
        return jsonify({
            "status": "sucesso",
            "tipo": tipo,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "resumo": {
                "qtd_os": dados["qtd_os"],
                "faturamento_total": float(dados["faturamento_total"]),
                "faturamento_pecas": float(dados["faturamento_pecas"]),
                "faturamento_mao_obra": float(dados["faturamento_mao_obra"]),
                "total_repasses": float(dados["total_repasses"]),
                "custo_pecas": float(dados["custo_pecas"]),
                "lucro_pecas": float(dados["lucro_pecas"]),
                "lucro_bruto_operacional": float(dados["lucro_bruto_operacional"]),
                "total_despesas": float(dados["total_despesas"]),
                "lucro_liquido_real": float(dados["lucro_liquido_real"])
            },
            "despesas_por_categoria": [
                {"categoria": c[0], "qtd": c[1], "valor": float(c[2])}
                for c in dados["despesas_categorias"]
            ]
        })

    return jsonify({"status": "erro", "mensagem": "Tipo de relatório inválido."}), 400


# ============================================================
# INICIAR SISTEMA
# ============================================================

if __name__ == "__main__":
    app.run()
