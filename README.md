# Event-Triggered Automation Agent

Streamlit app that checks Bitcoin price/news on a schedule and maintains a paper-trading book.

## Prerequisites

- Docker + Docker Compose
- Optional: `OPENROUTER_API_KEY` in your shell env for live LLM predictions via OpenRouter (`openai/gpt-4.1-mini`)
- Optional: `BTC_PRICE_PROVIDERS` to control quote source fallback order (comma-separated), e.g. `coingecko,binance,coinbase,kraken`
- Optional: `BTC_HISTORY_PROVIDERS` to control startup history fallback order (comma-separated), e.g. `coingecko,binance,kraken`

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
