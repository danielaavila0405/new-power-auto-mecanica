import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta
from secrets import compare_digest, token_urlsafe
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for


STATUS = {
    "marcado": "Marcado",
    "compareceu": "Compareceu",
    "faltou": "Não compareceu",
    "cancelado": "Cancelado",
    "remarcado": "Remarcado",
}
FUSO = ZoneInfo("America/Sao_Paulo")


def criar_tabela_agenda(conexao):
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS agendamentos (
            id INTEGER PRIMARY KEY,
            id_veiculo INTEGER NOT NULL REFERENCES veiculos(id_veiculo) ON DELETE CASCADE,
            id_cliente INTEGER NOT NULL REFERENCES clientes(id_cliente) ON DELETE CASCADE,
            id_os INTEGER REFERENCES ordens_servico(id_os) ON DELETE SET NULL,
            anterior INTEGER UNIQUE REFERENCES agendamentos(id) ON DELETE SET NULL,
            horario TEXT NOT NULL,
            servico TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'marcado',
            autorizado INTEGER NOT NULL DEFAULT 0,
            aviso_em TEXT
        )
    """)
    conexao.execute("CREATE INDEX IF NOT EXISTS agenda_horario ON agendamentos(horario)")


def proximo_aviso(agora):
    dia = agora.date() + timedelta(days=1)
    while dia.weekday() >= 5:
        dia += timedelta(days=1)
    return datetime.combine(dia, time(9)).isoformat(timespec="minutes")


def criar_blueprint(conectar_banco):
    agenda = Blueprint("agenda", __name__)

    @agenda.route("/agenda", methods=["GET", "POST"])
    def painel():
        agora = datetime.now(FUSO).replace(tzinfo=None)
        token = session.setdefault("csrf_agenda", token_urlsafe(32))
        try:
            dia = date.fromisoformat(request.values.get("dia", agora.date().isoformat()))
            anterior = dia - timedelta(days=1)
            seguinte = dia + timedelta(days=1)
        except (ValueError, OverflowError):
            abort(400)

        with closing(conectar_banco()) as conexao, conexao:
            conexao.row_factory = sqlite3.Row
            if request.method == "POST":
                if not compare_digest(token.encode(), request.form.get("csrf", "").encode()):
                    abort(403)
                conexao.execute("BEGIN IMMEDIATE")
                try:
                    novo_horario = alterar_agenda(conexao, agora)
                except ValueError as erro:
                    flash(str(erro), "erro")
                else:
                    flash("Agenda atualizada.", "sucesso")
                    if novo_horario:
                        dia = novo_horario.date()
                return redirect(url_for("agenda.painel", dia=dia.isoformat()))

            veiculos = conexao.execute("""
                SELECT v.id_veiculo, v.placa, v.modelo, c.nome
                FROM veiculos v JOIN clientes c USING (id_cliente)
                WHERE v.ativo = 1 ORDER BY c.nome, v.placa
            """).fetchall()
            visitas = conexao.execute("""
                SELECT a.*, v.placa, v.modelo, c.nome
                FROM agendamentos a
                JOIN veiculos v USING (id_veiculo)
                JOIN clientes c ON c.id_cliente = a.id_cliente
                WHERE a.horario >= ? AND a.horario < ?
                ORDER BY a.horario, a.id
            """, (dia.isoformat(), seguinte.isoformat())).fetchall()
            pendentes, primeira_pendencia = conexao.execute("""
                SELECT COUNT(*), substr(MIN(horario), 1, 10) FROM agendamentos
                WHERE status = 'marcado' AND horario < ?
            """, (agora.isoformat(timespec="minutes"),)).fetchone()

        return render_template("agenda.html", dia=dia, anterior=anterior,
                               seguinte=seguinte, agora=agora.isoformat(timespec="minutes"),
                               hoje=agora.date(), visitas=visitas, veiculos=veiculos, status=STATUS,
                               csrf=token, pendentes=pendentes, dia_pendente=primeira_pendencia)

    return agenda


def alterar_agenda(conexao, agora):
    acao = request.form.get("acao")
    visita = None
    if acao != "marcar":
        visita = conexao.execute("SELECT * FROM agendamentos WHERE id = ?",
                                 (request.form.get("id"),)).fetchone()
        if visita is None:
            raise ValueError("Agendamento não encontrado.")

    if acao in ("marcar", "remarcar"):
        if visita and visita["status"] not in ("marcado", "faltou"):
            raise ValueError("Este agendamento já foi encerrado.")
        try:
            horario = datetime.strptime(request.form.get("horario", ""), "%Y-%m-%dT%H:%M")
        except ValueError:
            raise ValueError("Informe a data e o horário.") from None
        if horario <= agora:
            raise ValueError("Escolha um horário futuro.")
        veiculo = conexao.execute("""
            SELECT id_veiculo, id_cliente FROM veiculos WHERE id_veiculo = ? AND ativo = 1
        """, (visita["id_veiculo"] if visita else request.form.get("veiculo"),)).fetchone()
        if veiculo is None or (visita and veiculo["id_cliente"] != visita["id_cliente"]):
            raise ValueError("Selecione um veículo ativo do cliente.")
        servico = visita["servico"] if visita else request.form.get("servico", "").strip()
        if not 1 <= len(servico) <= 180:
            raise ValueError("Informe o serviço em até 180 caracteres.")
        autorizado = visita["autorizado"] if visita else int(request.form.get("autorizado") == "1")
        conexao.execute("""
            INSERT INTO agendamentos (id_veiculo, id_cliente, horario, servico, autorizado, anterior)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (veiculo["id_veiculo"], veiculo["id_cliente"], horario.isoformat(timespec="minutes"),
              servico, autorizado, visita["id"] if visita else None))
        if visita:
            conexao.execute("UPDATE agendamentos SET status = 'remarcado', aviso_em = NULL WHERE id = ?",
                            (visita["id"],))
        return horario

    if acao not in ("compareceu", "faltou", "cancelado", "marcado"):
        raise ValueError("Ação inválida.")
    if visita["id_os"] or visita["status"] == "remarcado":
        raise ValueError("Este agendamento já está vinculado a outra visita ou OS.")
    if acao == "faltou" and visita["status"] != "marcado":
        raise ValueError("Somente um agendamento marcado pode ser registrado como falta.")
    if acao == "faltou" and visita["horario"] > agora.isoformat(timespec="minutes"):
        raise ValueError("O horário ainda não passou.")
    aviso = proximo_aviso(agora) if acao == "faltou" and visita["autorizado"] else None
    conexao.execute("UPDATE agendamentos SET status = ?, aviso_em = ? WHERE id = ?",
                    (acao, aviso, visita["id"]))
