# Current Gaps

This repository is an initial BPMN core library, not a full M8Flow or
SpiffArena clone. It covers process definition import, process instantiation,
human task worklists, task claim/complete, metadata, and event persistence.

The items below are intentionally not covered yet.

## Permissions And RBAC

M8Flow and SpiffArena model permissions through URIs that a user can access.
This library does not yet provide a permission layer on top of the BPMN API.

Planned direction:

- keep the existing URI-based RBAC model
- add command and function assignments to users alongside the URI grants
- make API calls check both URI access and workflow-command permissions
- support tenant-aware authorization rules for worklist reads, task actions,
  workflow administration, and definition import

Examples of missing permission features:

- no command-level authorization matrix
- no role-to-command mapping
- no workflow admin / tenant admin separation
- no policy engine for lane-specific or process-specific access rules

## Service Task Integrations

The library does not yet cover external service task execution.

Missing pieces include:

- worker or connector infrastructure
- outbound calls to external systems
- inbound callbacks and task completion notifications
- retry policies for failed service task executions
- dead-letter or manual intervention handling

## Timers And Scheduling

Timer events and schedulers are not yet implemented.

Missing pieces include:

- timer start events
- timer boundary events
- intermediate timer events
- reminder and escalation jobs
- delayed task creation and scheduled retries
- persisted scheduler state and job recovery after restart

## User Management

The library currently validates users against tenant membership, but it does not
yet provide full user management APIs.

Missing pieces include:

- user create, update, delete, and deactivate commands
- group and role management
- user profile synchronization from an identity provider
- account lifecycle hooks
- admin workflows for provisioning and offboarding

## Tenant Management

Tenant membership exists in the schema, but there are no public tenant
management commands yet.

Missing pieces include:

- tenant create and update commands
- tenant suspension and deletion
- tenant-level configuration management
- realm/bootstrap provisioning workflows
- tenant admin APIs for identity mapping and policy assignment

## Messaging And Correlation

The library does not yet cover BPMN messaging features.

Missing pieces include:

- message start events
- intermediate catch/throw message events
- signal events
- correlation keys and message subscriptions
- inbound message routing to running process instances
- outbound notifications or event fan-out

## Advanced BPMN Coverage

Some BPMN features are not yet exercised in the library tests or examples.

Missing or incomplete areas may include:

- call activities and reusable subprocess orchestration
- event subprocesses
- compensation behavior
- boundary error and escalation handling
- multi-instance activities
- non-interrupting intermediate event behavior
- BPMN message correlation against running instances

## Operations And Observability

The library does not yet expose a full operational control plane.

Missing pieces include:

- workflow history search and filtering APIs
- audit trails for admin actions
- metrics and tracing hooks
- job and process dashboards
- operational replay or recovery commands

## What Is In Scope Today

Current support focuses on:

- importing BPMN and DMN definitions
- starting process instances from stored definitions
- materializing human tasks
- querying pending tasks
- claiming and completing tasks
- persisting task payloads as process metadata
- reading process metadata and event history
- caller-owned transaction support through `Session` or `Connection`
