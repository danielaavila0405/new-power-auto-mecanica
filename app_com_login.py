from flask import (
    Flask,
    render_template,
    request,
    redirect,
    jsonify,
    send_file,
    session
)
import sqlite3
import os
import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from io import BytesIO

from werkzeug.security import generate_password_hash, check_password_hash

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
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
# CONFIGURAÇÃO DA API DE CONSULTA DE PLACAS
# ============================================================
# Sugestões de provedores no Brasil:
# 1. ApiCarros (https://apicarros.com.br/) - padrão
# 2. WDAPI (https://wdapi2.com.br/)
# Você pode cadastrar um token gratuito no site do provedor e colar abaixo:
PLACA_API_TOKEN = os.environ.get("PLACA_API_TOKEN", "")
PLACA_API_PROVIDER = os.environ.get("PLACA_API_PROVIDER", "apicarros").lower()


def conectar_banco():
    conexao = sqlite3.connect(banco)
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def garantir_tabela_usuarios():
    conexao = conectar_banco()
    cursor = conexao.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id_usuario INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            usuario TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            perfil TEXT NOT NULL DEFAULT 'usuario'
        )
    """)

    cursor.execute("PRAGMA table_info(usuarios)")
    colunas = [linha[1] for linha in cursor.fetchall()]

    if "senha_temporaria" not in colunas:
        cursor.execute("""
            ALTER TABLE usuarios
            ADD COLUMN senha_temporaria INTEGER NOT NULL DEFAULT 0
        """)

    conexao.commit()
    conexao.close()


garantir_tabela_usuarios()


# ============================================================
# CONTROLE DE LOGIN
# ============================================================

@app.before_request
def verificar_login():

    # Rotas que podem ser acessadas sem login.
    if request.endpoint in ("login", "esqueci_senha", "gerar_pdf_os"):
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
# MENU PRINCIPAL
# ============================================================

@app.route("/")
def menu():
    return render_template("menu.html")


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

    conexao = conectar_banco()
    cursor = conexao.cursor()

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
        tipo_mensagem=tipo_mensagem
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

    # 1. Verificar primeiro no banco de dados local (cache da oficina)
    try:
        conexao = conectar_banco()
        cursor = conexao.cursor()
        cursor.execute("""
            SELECT marca, modelo, ano
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
                    "ano": str(veiculo_local[2]) if veiculo_local[2] else ""
                }
            })
    except Exception as erro_banco:
        print(f"Aviso ao consultar placa no banco local: {erro_banco}")

    # 2. Placas de demonstração para testes antes de cadastrar token
    placas_demonstracao = {
        "BRA2E19": {"marca": "Volkswagen", "modelo": "Gol 1.6 MSI Totalflex", "ano": "2020"},
        "ABC1234": {"marca": "Fiat", "modelo": "Palio Fire 1.0", "ano": "2015"},
        "XYZ9999": {"marca": "Chevrolet", "modelo": "Onix LTZ 1.0 Turbo", "ano": "2023"},
        "NPW2026": {"marca": "Toyota", "modelo": "Corolla XEI 2.0", "ano": "2024"},
    }
    if placa_limpa in placas_demonstracao:
        dados_demo = placas_demonstracao[placa_limpa]
        return jsonify({
            "sucesso": True,
            "origem": "demonstracao",
            "dados": {
                "placa": placa_limpa,
                "marca": dados_demo["marca"],
                "modelo": dados_demo["modelo"],
                "ano": dados_demo["ano"]
            }
        })

    # 3. Se ainda não possui token de API externa configurado
    if not PLACA_API_TOKEN:
        return jsonify({
            "sucesso": False,
            "sem_token": True,
            "mensagem": "Token da API de placas não configurado. Para buscar na base nacional, informe seu token no app_com_login.py ou digite os dados manualmente."
        })

    # 4. Requisição à API externa
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        if PLACA_API_PROVIDER == "wdapi":
            url = f"https://wdapi2.com.br/consulta/{placa_limpa}/{PLACA_API_TOKEN}"
        else:
            url = f"https://placa-fipe.apicarros.com/v1/consulta/{placa_limpa}/{PLACA_API_TOKEN}"

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resposta:
            corpo = resposta.read().decode("utf-8")
            dados_api = json.loads(corpo)

        marca = ""
        modelo = ""
        ano = ""

        if PLACA_API_PROVIDER == "wdapi":
            marca = str(dados_api.get("MARCA") or dados_api.get("marca") or "").strip()
            modelo = str(dados_api.get("MODELO") or dados_api.get("modelo") or "").strip()
            ano = str(dados_api.get("anoModelo") or dados_api.get("ANO") or dados_api.get("ano") or "").strip()
        else:
            marca = str(dados_api.get("marca") or "").strip()
            modelo = str(dados_api.get("modelo") or "").strip()
            ano = str(dados_api.get("anoModelo") or dados_api.get("ano") or "").strip()

        if not marca and not modelo:
            msg = dados_api.get("mensagemRetorno") or dados_api.get("message") or "Placa não encontrada na base nacional."
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
                "ano": ano
            }
        })

    except urllib.error.HTTPError as erro_http:
        if erro_http.code in (401, 403):
            return jsonify({
                "sucesso": False,
                "mensagem": "Token da API de placas expirado ou limite atingido."
            })
        elif erro_http.code == 404:
            return jsonify({
                "sucesso": False,
                "mensagem": "Placa não encontrada no cadastro nacional."
            })
        else:
            return jsonify({
                "sucesso": False,
                "mensagem": f"Erro no serviço de consulta (Código {erro_http.code})."
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

        servicos = request.form.getlist(
            "servico_descricao"
        )

        valores_servicos = request.form.getlist(
            "servico_valor"
        )

        total_pecas = 0
        total_servicos = 0

        for i in range(len(pecas)):

            if not pecas[i].strip():
                continue

            quantidade = float(
                quantidades[i] or 0
            )

            valor_unitario = float(
                valores_pecas[i] or 0
            )

            total = quantidade * valor_unitario

            total_pecas += total

            pecas_salvas.append({
            "descricao": pecas[i],
            "quantidade": quantidade,
            "valor": valor_unitario,
            "total": total
        })

        for i in range(len(servicos)):

            if not servicos[i].strip():
                continue

            valor = float(
                valores_servicos[i] or 0
            )

            total_servicos += valor

            servicos_salvos.append({
                "descricao": servicos[i],
                "valor": valor
            })

        valor_total = (
            total_pecas +
            total_servicos
        )

        if responsavel_selecionado == "Henrique":

            valor_repasse = (
                total_servicos * 0.50
            )

        else:

            valor_repasse = 0

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
                valor_repasse
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cliente_selecionado,
            veiculo_selecionado,
            data,
            responsavel_selecionado,
            pagamento_selecionado,
            total_servicos,
            total_pecas,
            valor_total,
            valor_repasse
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
                    valor_total
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                id_os,
                peca["descricao"],
                int(peca["quantidade"]),
                peca["valor"],
                peca["quantidade"] * peca["valor"]
            ))

        for servico in servicos_salvos:

            cursor.execute("""
                INSERT INTO servicos_os
                (
                    id_os,
                    descricao,
                    valor
                )
                VALUES (?, ?, ?)
            """, (
                id_os,
                servico["descricao"],
                servico["valor"]
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

    conexao.close()

    return render_template(
        "os.html",
        clientes=lista_clientes,
        veiculos=lista_veiculos,
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

            WHERE UPPER(veiculos.placa) = ?

            LIMIT 1
        """, (placa,))

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

    mensagem = request.args.get(
        "mensagem",
        ""
    )

    return render_template(
        "visualizar_os.html",
        os=os_dados,
        pecas=pecas,
        servicos=servicos,
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

        servicos = request.form.getlist(
            "servico_descricao"
        )

        valores_servicos = request.form.getlist(
            "servico_valor"
        )

        total_pecas = 0
        total_servicos = 0

        pecas_salvas = []
        servicos_salvos = []

        for i in range(len(pecas)):

            descricao = pecas[i].strip()

            if not descricao:
                continue

            quantidade = float(
                quantidades[i] or 0
            )

            valor = float(
                valores_pecas[i] or 0
            )

            total = quantidade * valor

            total_pecas += total

            pecas_salvas.append({
                "descricao": descricao,
                "quantidade": quantidade,
                "valor": valor,
                "total": total
            })

        for i in range(len(servicos)):

            descricao = servicos[i].strip()

            if not descricao:
                continue

            valor = float(
                valores_servicos[i] or 0
            )

            total_servicos += valor

            servicos_salvos.append({
                "descricao": descricao,
                "valor": valor
            })

        valor_total = (
            total_pecas +
            total_servicos
        )

        if responsavel == "Henrique":

            valor_repasse = (
                total_servicos * 0.50
            )

        else:

            valor_repasse = 0

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
                status = ?

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
                    valor_total
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                id_os,
                peca["descricao"],
                int(peca["quantidade"]),
                peca["valor"],
                peca["total"]
            ))

        for servico in servicos_salvos:

            cursor.execute("""
                INSERT INTO servicos_os
                (
                    id_os,
                    descricao,
                    valor
                )
                VALUES (?, ?, ?)
            """, (
                id_os,
                servico["descricao"],
                servico["valor"]
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
            valor_unitario
        FROM pecas_os
        WHERE id_os = ?
        ORDER BY id_peca_os
    """, (id_os,))

    pecas = cursor.fetchall()

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

    return render_template(
        "editar_os.html",
        os=os_dados,
        clientes=lista_clientes,
        veiculos=lista_veiculos,
        pecas=pecas,
        servicos=servicos
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
            COALESCE(SUM(valor_repasse), 0)

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

    resultado_oficina = (
        faturamento -
        repasse_henrique
    )

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

    conexao.close()

    return render_template(
        "financeiro.html",
        quantidade_os=quantidade_os,
        faturamento=faturamento,
        total_pecas=total_pecas,
        total_mao_obra=total_mao_obra,
        repasse_henrique=repasse_henrique,
        resultado_oficina=resultado_oficina,
        data_inicio=data_inicio,
        data_fim=data_fim,
        pagamentos=pagamentos,
        responsaveis=responsaveis
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
            COALESCE(SUM(valor_repasse), 0)
        FROM ordens_servico
        WHERE status = 'Finalizada'
    """)

    dados = cursor.fetchone()

    quantidade_os = dados[0]
    faturamento = dados[1]
    total_pecas = dados[2]
    total_mao_obra = dados[3]
    repasse_henrique = dados[4]

    resultado_oficina = (
        faturamento -
        repasse_henrique
    )

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
        total_mao_obra=total_mao_obra,
        repasse_henrique=repasse_henrique,
        resultado_oficina=resultado_oficina,

        pagamentos=pagamentos,
        responsaveis=responsaveis,
        faturamento_mensal=faturamento_mensal
    )


# ============================================================
# ESQUECI A SENHA
# ============================================================

@app.route("/esqueci-senha")
def esqueci_senha():

    return render_template("esqueci_senha.html")


# ============================================================
# INICIAR SISTEMA
# ============================================================

if __name__ == "__main__":
    app.run()
