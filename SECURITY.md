# Security policy

This document explains which versions of m8flow-core receive security
fixes and how to report a vulnerability.

## Supported versions

Security fixes go to the `main` branch and to the latest 1.x release, as the
following table shows:

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |
| < 1.0   | :x:                |

Versions before 1.0 were named `m8flow-bpmn-core`. To get fixes,
[upgrade to 1.0](docs/how-to/upgrade-to-1.0.md).

## Reporting a vulnerability

**Don't open a public GitHub issue for security vulnerabilities.**

Report suspected vulnerabilities privately through either of the following:

- **GitHub Security Advisories**: use the
  [private vulnerability reporting](https://github.com/AOT-Technologies/m8flow-core/security/advisories/new)
  form for this repository (preferred).
- **Email**: send details to **opensource@aot-technologies.com**.

Include as much of the following as you can:

- A description of the vulnerability and its potential impact.
- Whether it crosses a tenant boundary or bypasses a command permission check.
  Tenant isolation and command authorization are core guarantees of the
  library, so say so explicitly.
- Steps to reproduce, including a minimal proof of concept if available.
- The version or commit affected.

## What to expect

After you report a vulnerability, you can expect the following:

- **Acknowledgement** within 3 business days of your report.
- An initial assessment and severity triage within 10 business days.
- Coordinated disclosure: the maintainers work with you on a fix and a public
  disclosure timeline, and credit you in the advisory unless you prefer to
  remain anonymous.

Give the maintainers a reasonable opportunity to fix the issue before any
public disclosure.

## Scope

This policy covers the code in this repository. Report vulnerabilities in
third-party dependencies, such as SpiffWorkflow or SQLAlchemy, to the upstream
project. If a dependency issue affects m8flow-core users, a heads-up is
still welcome so the dependency can be pinned or patched.

The examples, `sample.env`, and the sample app use well-known local
development credentials, such as `postgres:postgres` and demo users whose
password matches their username. They aren't secrets, and they must never be
used outside local development. Reports that only show these defaults work
against a local setup are out of scope.
