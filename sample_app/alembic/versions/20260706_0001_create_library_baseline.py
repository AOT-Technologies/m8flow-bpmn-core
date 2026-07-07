"""create library baseline"""

from __future__ import annotations

from alembic import op

from m8flow_bpmn_core.models import Base as BpmnCoreBase
revision = "20260706_0001"
down_revision = None
branch_labels = None
depends_on = None

LIBRARY_TABLE_NAMES = (
    "m8flow_tenant",
    "user",
    "group",
    "user_group_assignment",
    "principal",
    "permission_target",
    "permission_assignment",
    "bpmn_process_definition",
    "bpmn_process",
    "task_definition",
    "process_model_bpmn_version",
    "process_instance",
    "json_data",
    "task",
    "future_task",
    "human_task",
    "human_task_user",
    "process_instance_event",
    "process_instance_metadata",
    "scheduler_job",
)


def _library_tables():
    selected_names = set(LIBRARY_TABLE_NAMES)
    return [
        table
        for table in BpmnCoreBase.metadata.sorted_tables
        if table.name in selected_names
    ]


def _sample_app_tables():
    return []


def upgrade() -> None:
    bind = op.get_bind()
    for table in _library_tables():
        table.create(bind=bind, checkfirst=True)
    for table in _sample_app_tables():
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_sample_app_tables()):
        table.drop(bind=bind, checkfirst=True)
    for table in reversed(_library_tables()):
        table.drop(bind=bind, checkfirst=True)
