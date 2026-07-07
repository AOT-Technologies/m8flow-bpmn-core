# Vendor Wheel Staging

This directory is reserved for the locally built `m8flow-bpmn-core` wheel that
the sample app depends on.

The wheel itself is not committed. Stage it with:

- `.\sample_app\scripts\stage_local_wheel.ps1`
- `bash sample_app/scripts/stage_local_wheel.sh`

The staged filename keeps the original wheel version, for example:

`sample_app/vendor/m8flow_bpmn_core-0.1.0-py3-none-any.whl`

The staging helper also updates `sample_app/pyproject.toml` so
`tool.uv.sources.m8flow-bpmn-core` points at the currently staged wheel.
