# Event-Triggered Automation Agent

Streamlit app that checks Bitcoin price/news on a schedule and maintains a paper-trading book.

Utilises both System One and System Two thinking with:
- `SYSTEM_ONE_MODEL=gemma4:e2b` (ala: `jev`)
- `SYSTEM_TWO_MODEL=gemma4:12b`

Screenshot: [View dashboard](./Screenshot.png)

## Prerequisites

- Docker + Docker Compose
- Optional: `OPENROUTER_API_KEY` in your shell env for live LLM predictions via OpenRouter (uses `SYSTEM_ONE_MODEL` and `SYSTEM_TWO_MODEL`)
- Optional: `BTC_PRICE_PROVIDERS` to control quote source fallback order (comma-separated), e.g. `coingecko,binance,coinbase,kraken`
- Optional: `BTC_HISTORY_PROVIDERS` to control startup history fallback order (comma-separated), e.g. `coingecko,binance,kraken`

## Environment setup

Copy example env and adjust as needed:

```bash
cp .env.example .env
```

Defaults in `.env.example` enable Ollama with:

- `SYSTEM_ONE_MODEL=gemma4:e2b`
- `SYSTEM_TWO_MODEL=gemma4:12b`
- `OLLAMA_HOST=http://host.docker.internal:11434`

Model selection behavior:

- System One always reads `SYSTEM_ONE_MODEL`; System Two always reads `SYSTEM_TWO_MODEL`.
- When `USE_OLLAMA=1`, those model values are used with Ollama (`OLLAMA_MODEL` remains a fallback).
- When `USE_OLLAMA=0` and `OPENROUTER_API_KEY` is set, those same model values are used with OpenRouter.
- When `USE_OLLAMA=0` and no `OPENROUTER_API_KEY` is set, heuristic mode is used.

If running Ollama outside Docker networking, keep `OLLAMA_HOST` routable from the container.

## Run (app mode)

```bash
make app
```

Open: `http://localhost:8501`

## Run (debug mode)

```bash
make debug
```

Then use VS Code launch config: `Attach to Docker Debug App`.

## Local QA

```bash
uv sync
make test
make acceptance
make lint
make type
```
