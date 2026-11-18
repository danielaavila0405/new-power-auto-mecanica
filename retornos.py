import sqlite3
from contextlib import closing
from datetime import date, datetime
from secrets import compare_digest, token_urlsafe

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from agenda import FUSO, link_whatsapp


TIPOS = (
    "Revisão periódica", "Óleo e filtro", "Filtro de ar do motor", "Filtro de combustível",
    "Correia dentada", "Correia de acessórios", "Velas", "Freios", "Direção e suspensão",
    "Ar-condicionado e filtro de cabine", "Higienização do ar-condicionado",
    "Arrefecimento", "Transmissão", "Pós-serviço", "Serviço adiado", "Orçamento pendente",
    "Check-up antes de viagem", "Feriado ou campanha sazonal", "Retorno personalizado",
)
CAMPANHA = "Feriado ou campanha sazonal"


def criar_tabela_retornos(conexao):
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS retornos_whatsapp (
            id INTEGER PRIMARY KEY,
            id_cliente INTEGER NOT NULL REFERENCES clientes(id_cliente) ON DELETE CASCADE,
            id_veiculo INTEGER REFERENCES veiculos(id_veiculo) ON DELETE CASCADE,
            id_os INTEGER REFERENCES ordens_servico(id_os) ON DELETE SET NULL,
            tipo TEXT NOT NULL,
            data_aviso TEXT NOT NULL,
            km_alvo INTEGER,
            texto TEXT NOT NULL,
            enviado_em TEXT,
            cancelado_em TEXT
        )
    """)


def consultar_os(db, id_os):
    if id_os is None:
        return None
    ordem = db.execute("""
        SELECT o.id_os, o.id_cliente, o.id_veiculo, c.nome, v.placa
        FROM ordens_servico o JOIN clientes c ON c.id_cliente = o.id_cliente
        JOIN veiculos v ON v.id_veiculo = o.id_veiculo AND v.id_cliente = o.id_cliente
        WHERE o.id_os = ?
    """, (id_os,)).fetchone()
    if ordem is None:
        abort(404)
    return ordem


def programar(db, hoje, ordem):
    tipo = request.form.get("tipo")
    texto = request.form.get("texto", "").strip()
    if tipo not in TIPOS:
        raise ValueError("Escolha um motivo da mensagem.")
    if request.form.get("autorizado") != "1":
        raise ValueError("Confirme que os destinatários aceitaram receber este tipo de mensagem.")
    try:
        data_aviso = date.fromisoformat(request.form.get("data_aviso", ""))
        km_alvo = int(request.form["km_alvo"]) if request.form.get("km_alvo") else None
    except ValueError:
        raise ValueError("Confira a data e a quilometragem.") from None
    if data_aviso < hoje or (km_alvo is not None and not 1 <= km_alvo <= 9_999_999):
        raise ValueError("Informe uma data a partir de hoje e uma quilometragem válida.")
    if len(texto) > 600 or (tipo == CAMPANHA and not texto):
        raise ValueError("Escreva o convite da campanha em até 600 caracteres.")

    if ordem:
        if tipo == CAMPANHA:
            raise ValueError("Programe campanhas pela tela Retornos e campanhas.")
        destinatarios = [(ordem["id_cliente"], ordem["id_veiculo"], ordem["id_os"])]
    else:
        destinatarios = []
        for valor in dict.fromkeys(request.form.getlist("destinatarios")):
            categoria, _, identificador = valor.partition(":")
            if categoria == "veiculo" and tipo != CAMPANHA:
                registro = db.execute("""
                    SELECT id_cliente, id_veiculo FROM veiculos
                    WHERE id_veiculo = ? AND ativo = 1
                """, (identificador,)).fetchone()
                if registro:
                    destinatarios.append((*registro, None))
            elif categoria == "cliente":
                registro = db.execute("SELECT id_cliente FROM clientes WHERE id_cliente = ?",
                                      (identificador,)).fetchone()
                if registro:
                    destinatarios.append((registro["id_cliente"], None, None))
        if not destinatarios or (tipo != CAMPANHA and len(destinatarios) != 1):
            raise ValueError("Selecione os destinatários da campanha ou um contato para o lembrete.")
    db.executemany("""
        INSERT INTO retornos_whatsapp (id_cliente, id_veiculo, id_os, tipo, data_aviso, km_alvo, texto)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [(*destinatario, tipo, data_aviso.isoformat(), km_alvo, texto) for destinatario in destinatarios])


def mensagem_retorno(registro):
    mensagem = f"Olá, {registro['nome']}! Aqui é da New Power Auto Mecânica. "
    if registro["texto"]:
        mensagem += registro["texto"]
    else:
        veiculo = f" do veículo {registro['placa']}" if registro["placa"] else ""
        mensagem += f"Estamos entrando em contato sobre {registro['tipo'].lower()}{veiculo}. "
        if registro["km_alvo"]:
            km = f"{registro['km_alvo']:,}".replace(",", ".")
            mensagem += f"Se puder, confira se o veículo já chegou a {km} km. "
        mensagem += "Quer combinar um horário? É só responder por aqui."
    return link_whatsapp(registro["telefone"], mensagem)


def criar_blueprint_retornos(conectar_banco):
    retornos = Blueprint("retornos", __name__)

    @retornos.route("/agenda/retornos", methods=["GET", "POST"])
    def painel():
        agora = datetime.now(FUSO)
        hoje = agora.date()
        token = session.setdefault("csrf_agenda", token_urlsafe(32))
        with closing(conectar_banco()) as db, db:
            db.row_factory = sqlite3.Row
            ordem = consultar_os(db, request.args.get("os", type=int))
            if request.method == "POST":
                if not compare_digest(token.encode(), request.form.get("csrf", "").encode()):
                    abort(403)
                acao = request.form.get("acao")
                try:
                    if acao == "programar":
                        programar(db, hoje, ordem)
                    elif acao in ("enviar", "cancelar"):
                        campo = "enviado_em" if acao == "enviar" else "cancelado_em"
                        alterados = db.execute(f"""
                            UPDATE retornos_whatsapp SET {campo} = ?
                            WHERE id = ? AND enviado_em IS NULL AND cancelado_em IS NULL
                              AND (? = 'cancelar' OR data_aviso <= ?)
                        """, (agora.isoformat(timespec="minutes"), request.form.get("id"),
                              acao, hoje.isoformat())).rowcount
                        if not alterados:
                            raise ValueError("O lembrete já foi encerrado ou ainda não está disponível.")
                    else:
                        abort(400)
                except ValueError as erro:
                    flash(str(erro), "erro")
                    return redirect(url_for("retornos.painel", os=ordem["id_os"] if ordem else None))
                flash("Lembretes atualizados.", "sucesso")
                return redirect(url_for("retornos.painel"))

            clientes = db.execute("SELECT id_cliente, nome, telefone FROM clientes ORDER BY nome").fetchall()
            veiculos = db.execute("""
                SELECT v.id_veiculo, v.placa, v.modelo, c.nome
                FROM veiculos v JOIN clientes c USING (id_cliente)
                WHERE v.ativo = 1 ORDER BY c.nome, v.placa
            """).fetchall()
            lembretes = []
            for registro in db.execute("""
                SELECT r.*, c.nome, c.telefone, v.placa
                FROM retornos_whatsapp r JOIN clientes c USING (id_cliente)
                LEFT JOIN veiculos v USING (id_veiculo)
                ORDER BY (r.enviado_em IS NOT NULL OR r.cancelado_em IS NOT NULL), r.data_aviso, r.id
            """):
                disponivel = not registro["enviado_em"] and not registro["cancelado_em"] and registro["data_aviso"] <= hoje.isoformat()
                lembretes.append(dict(registro, disponivel=disponivel,
                                      link=mensagem_retorno(registro) if disponivel else None))
        return render_template("retornos.html", tipos=TIPOS, campanha=CAMPANHA,
                               ordem=ordem, csrf=token, hoje=hoje.isoformat(),
                               clientes=clientes, veiculos=veiculos, lembretes=lembretes)

    return retornos
