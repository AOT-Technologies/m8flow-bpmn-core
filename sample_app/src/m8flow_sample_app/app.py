from __future__ import annotations

from flask import Flask

from m8flow_sample_app.db import run_migrations, session_scope
from m8flow_sample_app.seed import seed_static_reference_data
from m8flow_sample_app.settings import get_settings
from m8flow_sample_app.web import register_web_routes


def create_app() -> Flask:
    settings = get_settings()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key

    run_migrations()
    with session_scope() as db_session:
        seed_static_reference_data(db_session)

    register_web_routes(app)
    return app


def main() -> None:
    settings = get_settings()
    app = create_app()
    app.run(
        host=settings.host,
        port=settings.port,
        debug=settings.debug,
    )
