# Staged library wheel

This folder holds the locally built `m8flow-core` wheel that the sample
app installs. The wheel isn't committed.

To copy the newest wheel from `dist/` into this folder, run the staging script
from the repository root:

```bash
bash sample_app/scripts/stage_local_wheel.sh
```

On Windows, run `.\sample_app\scripts\stage_local_wheel.ps1`.

The staged file keeps its versioned name, such as
`m8flow_core-1.0.0-py3-none-any.whl`, because uv rejects wheels without a
version in the name. The script also points `tool.uv.sources.m8flow-core`
in `sample_app/pyproject.toml` at the staged file.
