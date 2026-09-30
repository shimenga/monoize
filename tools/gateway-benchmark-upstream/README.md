# Gateway benchmark upstream

Use this tool only through `scripts/gateway_benchmark.py`.
The runner builds this crate with its lockfile and starts it on a random loopback port.
It provides fixed Chat Completions SSE responses and JSON timing records on stdout.
It does not access application storage or external upstreams.

Run from the repository root:

```sh
python scripts/gateway_benchmark.py --profile smoke
python scripts/gateway_benchmark.py --profile qualification
```

Use `--binary target/debug/monoize` for a smoke run with an existing executable.
Use `target/debug/monoize.exe` on Windows.
Qualification always builds the release executable.

Build the helper before running the complete runner tests:

```sh
cargo build --locked --release --manifest-path tools/gateway-benchmark-upstream/Cargo.toml --target-dir target/benchmark-upstream
python tests/gateway_benchmark_runner.py
```

The tests skip native-helper verification if the helper executable is absent.
Install Python 3.11 or later. CPU sampling supports Windows and Linux.
Windows load generators request a 1 ms timer period and restore it on exit.

Inspect the JSON `failures` list even when every HTTP response has status 200.
The verifier requires terminal usage, `[DONE]`, correct token counts, and request conservation.
Reports identify the measured host and binary hashes. Local results do not establish production capacity.

Read `evidence/README.md` for the recorded Windows qualification failure.
