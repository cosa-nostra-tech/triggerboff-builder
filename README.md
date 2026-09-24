# TriggerBOFF Builder Agent

A dedicated Hermes AI agent for building, maintaining, and continuously improving TriggerBOFF.

## What This Agent Does

- **Pixel-perfect UI** — runs visual diff loops against Figma to ensure exact fidelity
- **Response quality benchmarks** — scores Railway TriggerBOFF against native Hermes Telegram gold standard
- **Platform stability** — monitors uptime, cold starts, tool failures, Supabase health
- **Marketing cron** — generates and schedules property content for X/Twitter
- **Weekly quality reports** — WACU, response scores, platform health, 3 action items

## Stack

- **Product being built:** TriggerBOFF (cosa-nostra-tech/triggerboff)
- **Product backend:** cosa-nostra-tech/sydney-property-harness (Railway)
- **This builder:** cosa-nostra-tech/triggerboff-builder (Railway)

## Required Environment Variables

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Builder bot token from BotFather |
| `OPENROUTER_API_KEY` | LLM provider |
| `HERMES_HOME` | Set to `/data/.hermes` |
| `ADMIN_PASSWORD` | Admin dashboard password |
| `GITHUB_TOKEN` | Push code to triggerboff + sydney-property-harness repos |
| `FIGMA_API_KEY` | Read Figma designs for pixel loop |
| `TAVILY_API_KEY` | Web search for research tasks |
| `HERMES_API_KEY` | Call Railway TriggerBOFF API for quality benchmarks |

## Tools

- `pixel_loop.py` — Figma vs live URL visual diff (Playwright + PIL)
- `quality_benchmark.py` — 20 golden questions benchmark suite
