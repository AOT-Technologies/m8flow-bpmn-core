from __future__ import annotations

from flask import Flask

from m8flow_sample_app.db import run_migrations, session_scope
from m8flow_sample_app.keycloak_login import (
    ensure_shared_realm_browser_client,
    shared_login_client_redirect_uris,
    shared_login_client_web_origins,
)
from m8flow_sample_app.scheduler import SampleAppSchedulerPoller
from m8flow_sample_app.seed import seed_static_reference_data
from m8flow_sample_app.shared_m8flow import (
    SHARED_M8FLOW_AUDIT_CONTEXT_KEY,
    discover_shared_m8flow_audit_context,
)
from m8flow_sample_app.settings import get_settings
from m8flow_sample_app.web import register_web_routes


def create_app() -> Flask:
    settings = get_settings()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key

    run_migrations()
    app.extensions[SHARED_M8FLOW_AUDIT_CONTEXT_KEY] = (
        discover_shared_m8flow_audit_context(
            database_url=settings.database_url,
            settings=settings,
        )
    )
    audit_context = app.extensions[SHARED_M8FLOW_AUDIT_CONTEXT_KEY]
    if audit_context.uses_shared_m8flow:
        ensure_shared_realm_browser_client(
            client_id=settings.keycloak_login_client_id,
            redirect_uris=shared_login_client_redirect_uris(settings=settings),
            web_origins=shared_login_client_web_origins(settings=settings),
        )
    with session_scope() as db_session:
        seed_static_reference_data(db_session, audit_context=audit_context)

    if settings.scheduler_enabled:
        scheduler_poller = SampleAppSchedulerPoller(
            database_url=settings.database_url,
            poll_seconds=settings.scheduler_poll_seconds,
            batch_limit=settings.scheduler_batch_limit,
            worker_id=settings.scheduler_worker_id,
        )
        scheduler_poller.start()
        app.extensions["sample_app_scheduler_poller"] = scheduler_poller

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
