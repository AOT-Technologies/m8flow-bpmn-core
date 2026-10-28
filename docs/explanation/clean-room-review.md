# About the clean-room review

The model, authorization, and task-assignment code in m8flow-core 0.x
contained structures adapted from spiff-arena: authorization patterns,
constraint names, URI matching, timestamp storage, human-task persistence,
process status helpers, the future task upsert, the event type list, and lane
assignment rules. Before 1.0, that code was redesigned independently, and the
0.x migration history was replaced by a single baseline. This page records the
engineering review of the result.

This is an engineering record of the repository's structure. It isn't a legal
opinion, and it doesn't replace the organization's formal license and
provenance review.

## What the review covered

The review covered the services, ORM models, and migrations the redesign
changed:

- `src/m8flow_core/services/authorization.py`
- `src/m8flow_core/services/lane_assignment.py`, and the work item
  assignment code in `src/m8flow_core/services/workflow_runtime.py`
- `src/m8flow_core/models/group.py`
- `src/m8flow_core/models/user.py`
- `src/m8flow_core/models/user_group_assignment.py`
- `src/m8flow_core/models/principal.py`
- `src/m8flow_core/models/permission_target.py`
- `src/m8flow_core/models/permission_assignment.py`
- `src/m8flow_core/models/process_instance.py`
- `src/m8flow_core/models/process_instance_event.py`
- `src/m8flow_core/models/future_task.py`
- `src/m8flow_core/models/work_item_user.py`
- `alembic/versions/k2l3m4n5o6p7_baseline_1_0_schema.py`

The 0.x Alembic revisions aren't part of 1.0. The single baseline creates the
1.0 schema directly from the current models, so no migration in this
repository carries the legacy table or constraint names.

## What was checked

The review confirmed the following:

- Current authorization constraints use the `m8f_*` namespace, and no current
  authorization source file contains the legacy constraint names from the
  migration history.
- Authorization relationships use explicit `back_populates` pairs, with
  `overlaps=` only where the association-table views need it.
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
- `ProcessInstanceModel` keeps two status helpers: `has_terminal_status()`,
  which the library uses, and `active_statuses()`, which hosts use to filter
  live instances. Their names and the terminal and active status groups are
  kept as an API and database contract, because hosts already depend on them.
  The stored status strings are unchanged. The other status helpers were
  removed.
- `FutureTaskModel` has no upsert helper; rows are maintained through the
  ORM.
- The event vocabulary contains only the event types the library records.
- Lane assignment follows the documented rules in
  [Assign tasks through lanes](../how-to/embed-in-your-application.md#assign-tasks-through-lanes).
  The initiator's lanes are named explicitly in `initiator_lanes`, not
  matched by lane-name pattern, and `added_by` has three documented values.

`tests/test_authorization_structure.py` enforces the structural checks for
the authorization models, and the model, event, lane-assignment, and
migration tests lock the rest, so a regression fails the test suite.

## How the comparison was done

The current source, including the files added to the review for 1.0, was
compared with the reference repositories available locally:

- **spiffworkflow-backend**: the model, authorization, event, future task,
  and lane-assignment counterparts were compared by normalized source
  structure, then reviewed manually for constants, enum ordering, constraint
  names, relationship declarations, regular expressions, decision order, and
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
