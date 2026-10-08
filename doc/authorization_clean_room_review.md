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
- Authorization targets use explicit `resource_type` and `resource_id` pairs.
  URI matching and URI-only fallback are not part of the current authorization
  implementation; historical URI records must be converted by the breaking
  migration before they can be used.
- Authorization row creation is centralized in the shared conflict-safe helper.
- No current authorization source file contains the legacy constraint names
  listed in the migration history.
- Structural and behavioral checks are enforced by
  `tests/test_authorization_clean_room.py`.

This document records an engineering review of the repository structure. It is
not a legal opinion or a substitute for the organization's formal license and
provenance review.

## Phase 10 verification record

The affected current source was manually compared with the available local
reference repositories:

- `spiffworkflow-backend`: model and authorization counterparts were compared
  by normalized source structure and then reviewed manually for constants,
  enum ordering, constraint names, relationship declarations, and method
  bodies.
- `spiff-arena-common`: the repository was inspected separately. It contains
  shared runner/Jinja utility code, but no corresponding authorization or ORM
  model implementation in the reviewed scope.

The comparison was a provenance-review aid, not a legal similarity threshold.
The highest normalized same-file comparison signals were below 0.40, with
the largest signal in `group.py`; authorization-service comparisons were below
0.02. These signals were reviewed manually and are consistent with shared
SQLAlchemy/domain vocabulary rather than copied method bodies. No legacy
authorization compatibility structures are retained in the current models or
services: the canonical group table is `m8f_group`, permission targets are
explicit resource pairs, and tenant identity fields use their named columns.
Old names may appear only in immutable historical migrations or in migration
validation/documentation that identifies the schema being transformed.

The current source was also checked for the known copied-looking task-type
expression and historical authorization constraint names. The task-type
classification now uses the independent `HUMAN_TASK_TYPENAMES` constant, and
current authorization source contains only the `m8f_*` constraint names.

Formal legal review and attribution/licensing sign-off remain organizational
release activities; this engineering record does not replace them. No CI
similarity workflow is required for this release because the reference
repositories are not part of the public build environment.
