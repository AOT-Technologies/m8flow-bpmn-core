# Configuration

This page lists the environment variables that m8flow-core, its tests,
and its examples read. [`sample.env`](../../sample.env) has a commented copy
of most of them.

When your application passes its own `Session` or `Connection`, the library
doesn't read any of these variables. They apply only to the helpers in
`m8flow_core.db`, the Alembic migrations, the tests, and the examples.

## Library

The library reads these variables, and a `.env` file in the working
directory, through `m8flow_core.settings`:

| Variable | Default | Description |
|---|---|---|
| `M8FLOW_DATABASE_URL` | `sqlite+pysqlite:///./m8flow.db` | SQLAlchemy URL used by `build_engine()` and by `alembic upgrade` |
| `M8FLOW_DATABASE_ECHO` | `false` | Logs every SQL statement when `true` |

To use PostgreSQL or MySQL, install the `postgresql` or `mysql` extra and set
a matching URL, such as
`postgresql+psycopg://<user>:<password>@localhost:5432/<database>`.

## Tests

The test suites read the following variables:

| Variable | Default | Description |
|---|---|---|
| `M8FLOW_TEST_DATABASE_URL` | A temporary SQLite database | Runs the unit tests against this database instead. Use a dedicated test database. |
| `M8FLOW_TEST_POSTGRES_IMAGE` | `postgres:16` | Docker image for the PostgreSQL integration test |
| `M8FLOW_TEST_POSTGRES_USER` | `postgres` | User for the integration test container |
| `M8FLOW_TEST_POSTGRES_PASSWORD` | `postgres` | Password for the integration test container |
| `M8FLOW_TEST_POSTGRES_DB` | `m8flow_core_test` | Database name for the integration test container |
| `M8FLOW_POSTGRES_UPGRADE_DATABASE_URL` | Unset | A dedicated, empty PostgreSQL database for the baseline migration tests, which also check post-2038 timestamps. The PostgreSQL cases are skipped when this isn't set. |

## Examples

The example scripts and their launchers read the following variables:

| Variable | Default | Description |
|---|---|---|
| `M8FLOW_EXAMPLE_DATABASE_URL` | The local m8flow database, `localhost:6843/postgres` | Database for the interactive examples, ignored when you pass `--sqlite` |
| `M8FLOW_EXAMPLE_POSTGRES_IMAGE` | `postgres:16` | Docker image when a launcher starts a temporary database |
| `M8FLOW_EXAMPLE_KEYCLOAK_PASSWORD` | `poc-demo-password` | Password for demo users the examples create in Keycloak |
| `M8FLOW_EXAMPLE_PROCESS_MODELS_DIR` | Discovered | Local path of the m8flow backend's process model catalog, for shared mode |
| `M8FLOW_EXAMPLE_BACKEND_CONTAINER` | Discovered | Docker container of the m8flow backend, used to find the catalog |
| `M8FLOW_EXAMPLE_BACKEND_TENANT_ROOT` | Discovered | Tenant folder used when publishing models to the catalog |
| `M8FLOW_SCHEDULER_EXECUTION_MODE` | `inline` | Set to `external` to make `scheduled_timer.py` wait for an external scheduler, as the Celery example does |
| `M8FLOW_CONNECTOR_PROXY_BASE_URL` | `http://localhost:6844` | Base URL of `m8flow-connector-proxy` for the service task example |
| `M8FLOW_CONNECTOR_POC_HOST_ALIAS` | `host.docker.internal` | Host name the connector proxy uses to reach the example's HTTP server |
| `M8FLOW_CORE_CELERY_BROKER_URL` | `redis://localhost:6848/0` | Celery broker for the Celery scheduler example. Falls back to `M8FLOW_BACKEND_CELERY_BROKER_URL`. |
| `M8FLOW_CORE_CELERY_RESULT_BACKEND` | The broker URL | Celery result backend. Falls back to `M8FLOW_BACKEND_CELERY_RESULT_BACKEND`. |
| `M8FLOW_CORE_CELERY_QUEUE` | `m8flow-core-poc` | Queue the Celery worker listens on |
| `M8FLOW_CORE_CELERY_POLL_SECONDS` | `1` | How often Celery beat runs the scheduler |
| `M8FLOW_CORE_CELERY_DATABASE_URL` | `M8FLOW_EXAMPLE_DATABASE_URL`, then `M8FLOW_DATABASE_URL`, then the local m8flow database | Database the Celery worker uses |
| `M8FLOW_CORE_CELERY_TENANT_ID` | All tenants | Limits the Celery worker to one tenant |
| `M8FLOW_CORE_CELERY_WORKER_ID` | `m8flow-core-celery` | Prefix of the worker ID stored on claimed jobs. The worker adds its host name. |

## Keycloak helpers

The Keycloak helpers in `m8flow_core.utils.keycloak`, which the examples and
the sample app use in shared m8flow mode, read the following variables. Each
of the first three also accepts the name without the `M8FLOW_` prefix, which
takes precedence.

| Variable | Default | Description |
|---|---|---|
| `M8FLOW_KEYCLOAK_URL` | `http://localhost:6842` | Keycloak base URL |
| `M8FLOW_KEYCLOAK_ADMIN_USER` | `admin` | Keycloak admin user |
| `M8FLOW_KEYCLOAK_ADMIN_PASSWORD` | `admin` | Keycloak admin password. The default is for a local m8flow stack only; set a real value anywhere else. |
| `M8FLOW_KEYCLOAK_SHARED_REALM` | `m8flow` | The shared realm that holds tenant organizations and users |
| `M8FLOW_KEYCLOAK_MASTER_REALM` | `master` | The realm used to authenticate the admin |

## Sample app

The sample app has its own settings, all prefixed with `M8FLOW_SAMPLE_APP_`.
The [sample app README](../../sample_app/README.md#configuration) lists them.

## Related pages

- [Develop and test](../how-to/develop-and-test.md)
- [Run the examples](../how-to/run-the-examples.md)
