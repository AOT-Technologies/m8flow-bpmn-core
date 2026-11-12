<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Performance baseline

This page records the parsing and execution timings captured for the 1.0.0
release. Use it as the reference point when you check a later change for a
performance regression. The numbers describe one machine; compare runs from
the same machine, not absolute values across machines.

## What is measured

`tools/benchmark.py` drives the public API with the parallel-review fixture,
`tests/fixtures/parallel_review_poc.bpmn`. Each run uses a fresh SQLite
database file, so commits are included. Schema creation and seeding aren't
timed.

| Operation | What it times |
|---|---|
| `import_definition` | Parsing, validating, and storing the BPMN definition with `ImportBpmnProcessDefinitionCommand` |
| `start_instance` | Creating a process instance and running it to its first human task with `InitializeProcessInstanceFromDefinitionCommand` |
| `full_workflow` | Starting an instance, then claiming and completing all four human tasks, including the parallel gateway, until the instance is `complete` |

## 1.0.0 baseline

Captured with 30 runs after 3 warmup runs, on m8flow-core 1.0.0, Python
3.12.2, macOS on an Apple M3 Pro, with SQLite.

| Operation | Runs | Median (ms) | Mean (ms) | p95 (ms) | Min (ms) | Max (ms) |
|---|---|---|---|---|---|---|
| `import_definition` | 30 | 12.5 | 13.6 | 13.9 | 11.7 | 33.0 |
| `start_instance` | 30 | 33.7 | 35.0 | 35.9 | 32.9 | 62.8 |
| `full_workflow` | 30 | 132.1 | 139.3 | 174.3 | 127.6 | 180.4 |

## Run the benchmark

To run it, use the following command:

```bash
uv run python tools/benchmark.py
```

To change the sample size, pass `--iterations` and `--warmup`. To time a
different BPMN file, pass `--bpmn`; the file must define the
`Process_parallel_review_poc` process with the same lanes. The script prints
a Markdown table in the format above.

## Related pages

- [Develop and test](../how-to/develop-and-test.md)
- [Limitations](limitations.md)
