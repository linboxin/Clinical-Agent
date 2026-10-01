"""Verify the OpenAI setup: key present, model reachable, one structured plan round-trip.

uv run python -m scripts.check_openai
"""

import asyncio
import sys

import openai

from app.config import get_settings
from app.contracts.request import VisualizationRequest
from app.factory import create_planner


async def main() -> int:
    settings = get_settings()
    planner = create_planner(settings)
    if planner is None:
        print("✗ OPENAI_API_KEY is not set (copy .env.example to .env and fill it in).")
        return 1
    print(f"model: {settings.planner_model}  base_url: {settings.openai_base_url or 'default'}")

    gateway = planner.gateway
    client: openai.AsyncOpenAI = gateway.client  # type: ignore[attr-defined]
    try:
        model = await client.models.retrieve(settings.planner_model)
        print(f"✓ model available: {model.id}")
    except openai.APIError as exc:
        print(f"✗ model lookup failed: {type(exc).__name__}: {exc}")
        return 1

    request = VisualizationRequest(query="How are lung cancer trials distributed across phases?")
    try:
        outcome = await planner.plan(request)
    except Exception as exc:  # report any provider incompatibility plainly
        print(f"✗ structured plan call failed: {type(exc).__name__}: {exc}")
        return 1
    print(
        f"✓ planner status={outcome.status} attempts={outcome.attempts} "
        f"tokens in/out={outcome.input_tokens}/{outcome.output_tokens}"
    )
    if outcome.plan:
        print(outcome.plan.model_dump_json(indent=2))
    return 0 if outcome.status == "accepted" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
