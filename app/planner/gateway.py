"""Model gateway: the only code that talks to the LLM provider."""

from dataclasses import dataclass
from typing import Any, Protocol

import openai

from app.contracts.plan import QueryPlan


class PlannerError(Exception):
    """The model call failed or returned no usable plan (refusal, truncation, API error)."""


@dataclass
class Proposal:
    plan: QueryPlan
    input_tokens: int | None = None
    output_tokens: int | None = None


Message = dict[str, Any]


class PlannerGateway(Protocol):
    model: str

    async def propose(self, instructions: str, messages: list[Message]) -> Proposal: ...


class OpenAIPlannerGateway:
    """OpenAI Responses API with Structured Outputs: the reply is decoded against the
    QueryPlan JSON schema, so it cannot contain unknown fields or enum values."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=2
        )

    async def propose(self, instructions: str, messages: list[Message]) -> Proposal:
        extra: dict[str, Any] = {}
        if self.reasoning_effort:
            extra["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = await self.client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=messages,  # type: ignore[arg-type]
                text_format=QueryPlan,
                **extra,
            )
        except openai.APIError as exc:
            raise PlannerError(f"{type(exc).__name__}: {exc}") from exc

        plan = response.output_parsed
        if plan is None:
            detail = getattr(response, "incomplete_details", None) or "refusal or empty output"
            raise PlannerError(f"model returned no plan ({detail})")
        usage = response.usage
        return Proposal(
            plan=plan,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
        )


class ReplayGateway:
    """Returns a fixed, hand-written plan instead of calling a model. The plan still goes
    through validation, request-field merging and grounding, so this exercises every
    deterministic stage end to end (debugging, live smoke tests without an API key)."""

    model = "replay"

    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan

    async def propose(self, instructions: str, messages: list[Message]) -> Proposal:
        return Proposal(plan=self.plan, input_tokens=0, output_tokens=0)
