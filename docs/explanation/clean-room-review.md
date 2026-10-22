# About the clean-room review

The model and authorization code in m8flow-core 0.x contained structures
adapted from spiff-arena: authorization patterns, constraint names, URI
matching, timestamp storage, and human-task persistence. Before 1.0, that code
was redesigned independently. This page records the engineering review of the
result.

This is an engineering record of the repository's structure. It isn't a legal
opinion, and it doesn't replace the organization's formal license and
provenance review.

## What the review covered

The review covered the authorization service and the ORM models the redesign
changed:

- `src/m8flow_core/services/authorization.py`
- `src/m8flow_core/models/group.py`
- `src/m8flow_core/models/user.py`
- `src/m8flow_core/models/user_group_assignment.py`
- `src/m8flow_core/models/principal.py`
- `src/m8flow_core/models/permission_target.py`
- `src/m8flow_core/models/permission_assignment.py`

Historical Alembic migrations keep the old names where they describe the
schema that existed before the redesign. They're inputs to the migration, not
current model or service code, and they can't change after release.

## What was checked

The review confirmed the following:

- Current authorization constraints use the `m8f_*` namespace, and no current
  authorization source file contains the legacy constraint names from the
  migration history.
- Relationships use explicit `back_populates` pairs, with `overlaps=` only
  where the association-table views need it.
- Permission targets are explicit `resource_type` and `resource_id` pairs.
  URI matching and URI-only fallback aren't part of the current code;
  historical URI records must be converted by the migration before they can
  be used.
- Authorization rows are created through one shared, conflict-safe helper.
- The group table is `m8f_group`, and tenant identity uses the named fields
  `realm_identifier`, `external_org_id`, and `external_user_id`.
- Task-type classification uses an independent `USER_TASK_TYPENAMES`
  constant, in `models/task_types.py`, in place of a copied-looking
  expression.

`tests/test_authorization_clean_room.py` enforces the structural and
behavioral checks, so a regression fails the test suite.

## How the comparison was done

The current source was compared by hand with the reference repositories
available locally:

- **spiffworkflow-backend**: the model and authorization counterparts were
  compared by normalized source structure, then reviewed manually for
  constants, enum ordering, constraint names, relationship declarations, and
  method bodies.
- **spiff-arena-common**: inspected separately. It contains shared runner and
  Jinja utility code, but no authorization or ORM model code in the reviewed
  scope.

The highest normalized same-file similarity was below 0.40, in `group.py`.
Authorization service comparisons were below 0.02. These signals were reviewed
manually and are consistent with shared SQLAlchemy and domain vocabulary
rather than copied method bodies. The comparison was an aid to the provenance
review, not a legal similarity threshold.

There's no similarity check in CI, because the reference repositories aren't
part of the public build environment.

## What remains

Formal legal review and attribution and licensing sign-off are organizational
release activities. This engineering record doesn't replace them.

## Related pages

- [About the workflow data model](workflow-data-model.md)
- [About authorization and tenancy](authorization-and-tenancy.md)
- [NOTICE](../../NOTICE): third-party attributions
