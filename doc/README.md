# M8Flow BPMN Core Documentation

This folder explains the public Python API and the common ways to use it.

Current highlights:

- The API includes a minimal V1 RBAC layer. Workflow command permissions are
  resolved through `permission_target.command`.
- The library can execute synchronous BPMN `ServiceTask` nodes through the
  current m8flow connector-proxy direction, and the repo now includes a real
  connector-proxy POC.
- The interactive conditional-approval example can reuse a shared local
  m8flow database, so the running process instance can be inspected and
  audited in the m8flow UI while the example is executing.

Start here:

- [API overview](api.md)
- [Usage guide](usage.md)
- [Example workflows](examples.md)
- [Packaging and dependency use](package.md)
- [Scheduling architecture](scheduling.md)
- [Service task architecture](service_tasks.md)
- [Current gaps](gaps.md)

The library is imported directly from Python. It is not exposed as an HTTP
service.
