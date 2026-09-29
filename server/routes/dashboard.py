# server/routes/dashboard.py — Serves the web dashboard
from flask import Blueprint, render_template
from server.config import API_KEY

dash_bp = Blueprint("dashboard", __name__,
                    template_folder="../templates")


@dash_bp.route("/")
def index():
    return render_template("dashboard.html", api_key=API_KEY)


@dash_bp.route("/favicon.ico")
def favicon():
    return "", 204
