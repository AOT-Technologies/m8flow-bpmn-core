# m8flow-core documentation

This folder holds the m8flow-core documentation: how to embed the library
in a Python application, the exact public API, and how the engine works. For
what the library is and what it can do, start with the
[project README](../README.md).

## Start here

Pick the path that matches what you came to do:

| You are… | Start with | Then read |
|---|---|---|
| New to the library | [Quick start](tutorials/quick-start.md) | [About the architecture](explanation/architecture.md) |
| Integrating it into an application | [Embed the library in your application](how-to/embed-in-your-application.md) | [Public API reference](reference/api.md) · [Build and install the package](how-to/build-and-install-the-package.md) |
| Upgrading from `m8flow-bpmn-core` 0.x | [Upgrade to 1.0](how-to/upgrade-to-1.0.md) | [Database schema](reference/database-schema.md) · [About the workflow data model](explanation/workflow-data-model.md) |
| Modeling workflows for it | [BPMN support](reference/bpmn-support.md) | [Limitations](reference/limitations.md) · [Run the examples](how-to/run-the-examples.md) |
| Contributing code | [Contributing guide](../CONTRIBUTING.md) | [Develop and test](how-to/develop-and-test.md) · [Repository layout](reference/repository-layout.md) |

## All pages

The pages follow the [Diátaxis](https://diataxis.fr/) framework. Each page is
one kind of document, so you know what to expect when you open it.

### Tutorials

Lessons that take you from nothing to a working result. The
[quick start](tutorials/quick-start.md) builds a script that imports a BPMN
process, starts it, and completes its task.

### How-to guides

Steps for one task, for readers who already know the basics:

- [Embed the library in your application](how-to/embed-in-your-application.md)
- [Run timers and scheduled retries](how-to/run-timers-and-retries.md)
- [Call external services from service tasks](how-to/call-external-services.md)
- [Customize authorization](how-to/customize-authorization.md)
- [Build and install the package](how-to/build-and-install-the-package.md)
- [Upgrade to 1.0](how-to/upgrade-to-1.0.md)
- [Publish a release](how-to/publish-a-release.md)
- [Run the examples](how-to/run-the-examples.md)
- [Run the sample app](../sample_app/README.md)
- [Develop and test](how-to/develop-and-test.md)

### Reference

Exact values to look up:

- [Public API reference](reference/api.md)
- [Database schema](reference/database-schema.md)
- [BPMN support](reference/bpmn-support.md)
- [Configuration](reference/configuration.md)
- [Repository layout](reference/repository-layout.md)
- [Limitations](reference/limitations.md)

### Explanation

Background on how the library works and why it's built that way:

- [About the architecture](explanation/architecture.md): commands and
  queries, transaction ownership, and where the library ends and your
  application begins.
- [About the workflow data model](explanation/workflow-data-model.md): work
  items, event categories, timestamps, and tenant-scoped payloads.
- [About authorization and tenancy](explanation/authorization-and-tenancy.md):
  tenant membership, roles, and command permissions on exact resources.
- [About scheduling](explanation/scheduling.md): the scheduler job table,
  timer and retry lifecycles, and execution modes.
- [About service task execution](explanation/service-task-execution.md):
  connectors, the connector proxy contract, and the failure transaction rule.
- [About the sample app integration](explanation/sample-app-integration.md):
  what a thin host application needs, and what it found.
- [About the clean-room review](explanation/clean-room-review.md): how the
  1.0 models and authorization were checked against spiff-arena.

## Change these docs

Keep each page to one Diátaxis type, write headings in sentence case, and
address the reader as "you". If you change the public API, update the
[API reference](reference/api.md) in the same pull request, as described in
the [contributing guide](../CONTRIBUTING.md#changing-the-public-api-or-schema).

To report a mistake in the docs, open an issue in the
[issue tracker](https://github.com/AOT-Technologies/m8flow-core/issues).
