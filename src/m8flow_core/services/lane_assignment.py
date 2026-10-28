"""Potential owners of human tasks, derived from BPMN lanes.

A work item's potential owners come from three sources, each recorded with
its own ``WorkItemUserAddedBy`` label:

* the process initiator, for tasks with no lane or in a lane listed in the
  definition's ``properties_json["initiator_lanes"]``;
* users named for the lane in ``lane_owners`` (task data first, else the
  definition);
* members of the tenant's lane group.

Lane names are compared after trimming outer whitespace and ignoring case.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from m8flow_core.errors import NotFoundError, ValidationError
from m8flow_core.models.group import GroupModel
from m8flow_core.models.process_instance import ProcessInstanceModel
from m8flow_core.models.user import UserModel
from m8flow_core.models.user_group_assignment import UserGroupAssignmentModel
from m8flow_core.models.work_item import WorkItemModel
from m8flow_core.models.work_item_user import (
    WorkItemUserAddedBy,
    WorkItemUserModel,
)
from m8flow_core.services.tenant_users import (
    tenant_identifiers_for,
    user_belongs_to_tenant,
)

logger = logging.getLogger(__name__)

LANE_OWNERS_KEY = "lane_owners"
INITIATOR_LANES_KEY = "initiator_lanes"


def resolve_lane_assignment_id(
    lane_name: str,
    tenant_id: str | None = None,
) -> int:
    """Return a stable identifier for a tenant-qualified lane group.

    ``tenant_id`` is optional for compatibility with callers that only need a
    deterministic legacy identifier.  Workflow runtime paths always provide
    it so identically named lanes in different tenants cannot share a group.
    The corresponding group identifier is ``{tenant_id}:{lane_name}``.
    """
    normalized_lane = lane_name.strip().lower()
    normalized_tenant = tenant_id.strip().lower() if tenant_id else None
    hash_input = (
        f"{normalized_tenant}:{normalized_lane}"
        if normalized_tenant
        else normalized_lane
    )
    digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
    stable_int = int(digest[:8], 16) & 0x7FFFFFFF
    return stable_int or 1


def normalize_lane_name(lane_name: object) -> str | None:
    """Return the comparison key for a lane name, or ``None`` for no lane."""
    if not isinstance(lane_name, str):
        return None
    trimmed = lane_name.strip()
    if not trimmed:
        return None
    return trimmed.lower()


# ---------------------------------------------------------------------------
# Definition configuration
# ---------------------------------------------------------------------------


def lane_owner_identifiers(lane_owners: object) -> dict[str, list[str]]:
    """Group a ``lane_owners`` mapping by normalized lane name.

    Keys that normalize to the same lane are merged. Malformed entries
    (non-string keys, non-list values, non-string identifiers) are ignored.
    """
    grouped: dict[str, list[str]] = {}
    if not isinstance(lane_owners, Mapping):
        return grouped
    for raw_lane, raw_identifiers in lane_owners.items():
        lane = normalize_lane_name(raw_lane)
        if lane is None or not isinstance(raw_identifiers, list):
            continue
        identifiers = grouped.setdefault(lane, [])
        identifiers.extend(value for value in raw_identifiers if isinstance(value, str))
    return grouped


def configured_initiator_lanes(properties_json: object) -> frozenset[str]:
    """Return the normalized initiator lanes of a stored definition.

    Import validation rejects malformed values; at run time anything that is
    not a usable lane name is ignored.
    """
    if not isinstance(properties_json, Mapping):
        return frozenset()
    raw_lanes = properties_json.get(INITIATOR_LANES_KEY)
    if not isinstance(raw_lanes, list):
        return frozenset()
    return frozenset(
        lane
        for lane in (normalize_lane_name(value) for value in raw_lanes)
        if lane is not None
    )


def validate_initiator_lanes(
    properties_json: Mapping[str, Any],
    *,
    bpmn_lane_names: Iterable[str],
) -> None:
    """Raise ``ValidationError`` when ``initiator_lanes`` is unusable."""
    raw_lanes = properties_json.get(INITIATOR_LANES_KEY)
    if raw_lanes is None:
        return
    if not isinstance(raw_lanes, list):
        raise ValidationError(
            f"properties_json['{INITIATOR_LANES_KEY}'] must be a list of lane "
            f"names, got {type(raw_lanes).__name__}"
        )

    requested: dict[str, str] = {}
    for value in raw_lanes:
        lane = normalize_lane_name(value)
        if lane is None:
            raise ValidationError(
                f"properties_json['{INITIATOR_LANES_KEY}'] entries must be "
                f"non-empty strings, got {value!r}"
            )
        requested.setdefault(lane, value.strip())

    known_lanes = {
        lane
        for lane in (normalize_lane_name(name) for name in bpmn_lane_names)
        if lane is not None
    }
    unknown = [
        original for lane, original in requested.items() if lane not in known_lanes
    ]
    if unknown:
        raise ValidationError(
            f"properties_json['{INITIATOR_LANES_KEY}'] names lanes that do not "
            f"exist in the BPMN source: {', '.join(sorted(unknown))}"
        )

    owner_lanes = lane_owner_identifiers(properties_json.get(LANE_OWNERS_KEY))
    conflicting = [
        original for lane, original in requested.items() if lane in owner_lanes
    ]
    if conflicting:
        raise ValidationError(
            f"Lanes cannot be listed in both '{INITIATOR_LANES_KEY}' and "
            f"'{LANE_OWNERS_KEY}': {', '.join(sorted(conflicting))}"
        )


# ---------------------------------------------------------------------------
# Lane groups
# ---------------------------------------------------------------------------


def lane_group_identifier(lane: str, tenant_id: str) -> str:
    """Return the canonical identifier of a tenant's lane group."""
    return f"{tenant_id.strip().lower()}:{lane.strip().lower()}"


def find_or_create_lane_group(
    session: Session,
    *,
    tenant_id: str,
    lane: str,
) -> GroupModel | None:
    """Return the tenant's group for ``lane``, creating it when missing.

    Returns ``None`` when the group stored at the computed ID belongs to a
    different tenant or lane; such a group is never used.
    """
    canonical = lane_group_identifier(lane, tenant_id)
    group_id = resolve_lane_assignment_id(lane, tenant_id)

    group = session.get(GroupModel, group_id)
    if group is not None:
        stored = group.identifier
        if stored == canonical:
            return group
        if stored is not None and stored.strip().lower() == canonical:
            group.name = canonical
            group.identifier = canonical
            session.flush()
            return group
        logger.warning(
            "Lane group %s for lane %r in tenant %s is already used by %r; "
            "lane group assignment skipped",
            group_id,
            lane,
            tenant_id,
            stored,
        )
        return None

    legacy_group = session.scalars(
        select(GroupModel)
        .where(func.lower(func.trim(GroupModel.identifier)) == lane)
        .order_by(GroupModel.id)
        .limit(1)
    ).first()
    if legacy_group is not None:
        return legacy_group

    group = GroupModel(id=group_id, name=canonical, identifier=canonical)
    session.add(group)
    session.flush()
    return group


def _add_group_members(
    session: Session,
    *,
    group: GroupModel,
    users: Iterable[UserModel],
) -> None:
    user_ids = {user.id for user in users}
    if not user_ids:
        return
    existing = set(
        session.scalars(
            select(UserGroupAssignmentModel.user_id).where(
                UserGroupAssignmentModel.group_id == group.id,
                UserGroupAssignmentModel.user_id.in_(user_ids),
            )
        ).all()
    )
    for user_id in sorted(user_ids - existing):
        session.add(UserGroupAssignmentModel(user_id=user_id, group_id=group.id))
    session.flush()


def _tenant_group_members(
    session: Session,
    *,
    group: GroupModel,
    tenant_identifiers: set[str],
) -> list[UserModel]:
    members = session.scalars(
        select(UserModel)
        .join(
            UserGroupAssignmentModel,
            UserGroupAssignmentModel.user_id == UserModel.id,
        )
        .where(UserGroupAssignmentModel.group_id == group.id)
        .order_by(UserModel.id)
    ).all()
    return [
        user for user in members if user_belongs_to_tenant(user, tenant_identifiers)
    ]


# ---------------------------------------------------------------------------
# Lane owners
# ---------------------------------------------------------------------------


@dataclass
class _OwnerResolver:
    """Resolve ``lane_owners`` identifiers to tenant members."""

    session: Session
    tenant_id: str
    tenant_identifiers: set[str]
    _cache: dict[str, list[UserModel]] = field(default_factory=dict)

    def users_for(self, identifier: str) -> list[UserModel]:
        key = identifier.strip()
        if not key:
            return []
        if key not in self._cache:
            self._cache[key] = self._lookup(key)
        return self._cache[key]

    def lane_owners(self, lane: str, identifiers: Iterable[str]) -> list[UserModel]:
        owners: dict[int, UserModel] = {}
        for identifier in identifiers:
            matched = self.users_for(identifier)
            if not matched and identifier.strip():
                logger.warning(
                    "Lane owner %r for lane %r matches no user in tenant %s; skipped",
                    identifier.strip(),
                    lane,
                    self.tenant_id,
                )
            for user in matched:
                owners.setdefault(user.id, user)
        return list(owners.values())

    def _lookup(self, identifier: str) -> list[UserModel]:
        exact = self._tenant_members(
            or_(
                UserModel.username == identifier,
                UserModel.email == identifier,
                UserModel.service_id == identifier,
            )
        )
        if exact or "@" not in identifier:
            return exact
        local_part = identifier.split("@", 1)[0]
        if not local_part:
            return []
        return self._tenant_members(UserModel.username == local_part)

    def _tenant_members(self, condition: Any) -> list[UserModel]:
        candidates = self.session.scalars(
            select(UserModel).where(condition).order_by(UserModel.id)
        ).all()
        return [
            user
            for user in candidates
            if user_belongs_to_tenant(user, self.tenant_identifiers)
        ]


def _enroll_lane_owners(
    session: Session,
    *,
    tenant_id: str,
    owners_by_lane: Mapping[str, list[UserModel]],
) -> dict[str, GroupModel | None]:
    groups: dict[str, GroupModel | None] = {}
    for lane, owners in owners_by_lane.items():
        group = find_or_create_lane_group(session, tenant_id=tenant_id, lane=lane)
        groups[lane] = group
        if group is not None:
            _add_group_members(session, group=group, users=owners)
    return groups


def enroll_definition_lane_owners(
    session: Session,
    *,
    tenant_id: str,
    properties_json: Mapping[str, Any] | None,
) -> None:
    """Add every resolvable ``lane_owners`` user to their lane group."""
    if not properties_json:
        return
    identifiers_by_lane = lane_owner_identifiers(properties_json.get(LANE_OWNERS_KEY))
    if not identifiers_by_lane:
        return
    resolver = _OwnerResolver(
        session=session,
        tenant_id=tenant_id,
        tenant_identifiers=tenant_identifiers_for(session, tenant_id),
    )
    _enroll_lane_owners(
        session,
        tenant_id=tenant_id,
        owners_by_lane={
            lane: resolver.lane_owners(lane, identifiers)
            for lane, identifiers in identifiers_by_lane.items()
        },
    )


# ---------------------------------------------------------------------------
# Work item potential owners
# ---------------------------------------------------------------------------


def assign_work_item_potential_owners(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    work_item: WorkItemModel,
    lane_name: object,
    task_data: object,
    definition_properties: object,
) -> None:
    """Set the work item's lane group and add any missing potential owners.

    Existing ``work_item_user`` rows are kept as they are, so materializing a
    task again only adds users who now qualify.
    """
    tenant_id = process_instance.m8f_tenant_id
    lane = normalize_lane_name(lane_name)
    initiator_lanes = configured_initiator_lanes(definition_properties)

    owners: dict[int, WorkItemUserAddedBy] = {}
    work_item.lane_assignment_id = None
    if lane is None or lane in initiator_lanes:
        owners[_initiator(session, process_instance).id] = (
            WorkItemUserAddedBy.process_initiator
        )
    else:
        tenant_identifiers = tenant_identifiers_for(session, tenant_id)
        resolver = _OwnerResolver(
            session=session,
            tenant_id=tenant_id,
            tenant_identifiers=tenant_identifiers,
        )
        owners_by_lane = {
            owner_lane: resolver.lane_owners(owner_lane, identifiers)
            for owner_lane, identifiers in _effective_lane_owners(
                task_data, definition_properties
            ).items()
            if owner_lane not in initiator_lanes
        }
        groups = _enroll_lane_owners(
            session,
            tenant_id=tenant_id,
            owners_by_lane=owners_by_lane,
        )
        for user in owners_by_lane.get(lane, []):
            owners[user.id] = WorkItemUserAddedBy.lane_owner

        group = (
            groups[lane]
            if lane in groups
            else find_or_create_lane_group(session, tenant_id=tenant_id, lane=lane)
        )
        if group is not None:
            work_item.lane_assignment_id = group.id
            for member in _tenant_group_members(
                session,
                group=group,
                tenant_identifiers=tenant_identifiers,
            ):
                owners.setdefault(member.id, WorkItemUserAddedBy.lane_assignment)

    _add_missing_potential_owners(
        session,
        tenant_id=tenant_id,
        work_item=work_item,
        owners=owners,
    )


def _effective_lane_owners(
    task_data: object,
    definition_properties: object,
) -> dict[str, list[str]]:
    if isinstance(task_data, Mapping):
        from_task = task_data.get(LANE_OWNERS_KEY)
        if isinstance(from_task, Mapping):
            return lane_owner_identifiers(from_task)
    if isinstance(definition_properties, Mapping):
        return lane_owner_identifiers(definition_properties.get(LANE_OWNERS_KEY))
    return {}


def _initiator(session: Session, process_instance: ProcessInstanceModel) -> UserModel:
    initiator_id = process_instance.process_initiator_id
    initiator = (
        session.get(UserModel, initiator_id) if initiator_id is not None else None
    )
    if initiator is None:
        raise NotFoundError(
            f"Process initiator {initiator_id} was not found for process "
            f"instance {process_instance.id}"
        )
    return initiator


def _add_missing_potential_owners(
    session: Session,
    *,
    tenant_id: str,
    work_item: WorkItemModel,
    owners: Mapping[int, WorkItemUserAddedBy],
) -> None:
    present = set(
        session.scalars(
            select(WorkItemUserModel.user_id).where(
                WorkItemUserModel.work_item_id == work_item.id,
            )
        ).all()
    )
    for user_id in sorted(owners.keys() - present):
        session.add(
            WorkItemUserModel(
                m8f_tenant_id=tenant_id,
                work_item_id=work_item.id,
                user_id=user_id,
                added_by=owners[user_id],
            )
        )
    session.flush()
