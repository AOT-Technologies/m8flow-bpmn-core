# Authorization clean-room engineering review

## Scope

This review covers the authorization implementation and ORM models changed by
the authorization redesign:

- `src/m8flow_bpmn_core/services/authorization.py`
- `src/m8flow_bpmn_core/models/group.py`
- `src/m8flow_bpmn_core/models/user.py`
- `src/m8flow_bpmn_core/models/user_group_assignment.py`
- `src/m8flow_bpmn_core/models/principal.py`
- `src/m8flow_bpmn_core/models/permission_target.py`
- `src/m8flow_bpmn_core/models/permission_assignment.py`

Historical Alembic migrations retain old names where they describe the schema
that existed before the redesign. They are migration inputs, not current model
or service implementation.

## Review checks

- Authorization models use the `m8f_*` namespace for current constraints.
- Relationships use explicit `back_populates` pairs and only the `overlaps=`
  declarations required by the association-table views.
- Permission matching is based on exact resource pairs for new requests.
- URI targets are retained only as a documented compatibility path for the UI
  and existing authorization records.
- Authorization row creation is centralized in the shared conflict-safe helper.
- No current authorization source file contains the legacy constraint names
  listed in the migration history.
- Structural and behavioral checks are enforced by
  `tests/test_authorization_clean_room.py`.

This document records an engineering review of the repository structure. It is
not a legal opinion or a substitute for the organization's formal license and
provenance review.
