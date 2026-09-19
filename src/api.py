"""Local-only support triage API. Does not send email or post customer replies."""
from contextlib import asynccontextmanager
from dataclasses import replace
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .config import CONFIG
from .decision_log import DecisionLog
from .models import Ticket
from .monitoring import Metrics
from .pipeline import Pipeline


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ticket_id: str = Field(min_length=1, max_length=128)
    channel: Literal["email", "chat", "docs_comment", "forum"]
    subject: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    received_at: str = Field(default="", max_length=64)
    customer_id: str = Field(default="", max_length=128)
    customer_tier: str = Field(default="", max_length=64)
    customer_region: str = Field(default="", max_length=64)
    language_fluency: str = Field(default="", max_length=64)


def create_app(*, config=CONFIG, pipeline=None, use_provider=False):
    serial = Lock()  # The model cache and pipeline are single-writer resources.

    @asynccontextmanager
    async def lifespan(app):
        if pipeline is not None:
            app.state.pipeline = pipeline
        else:
            from .providers import get_provider
            app.state.pipeline = Pipeline(config=config, metrics=Metrics(),
                                          provider=get_provider() if use_provider else None)
        yield

    app = FastAPI(title="Support triage (local demo)", lifespan=lifespan)

    @app.get("/health")
    def health():
        p = app.state.pipeline
        return {"status": "ready", "automatic_replies_paused": p.control.disabled,
                "generation": "provider" if p.provider else "extractive",
                "semantic_retrieval_available": bool(getattr(p.retriever, "semantic_available", False))}

    @app.get("/metrics")
    def metrics():
        return app.state.pipeline.metrics.snapshot()

    @app.get("/metrics/prometheus")
    def prometheus():
        from fastapi.responses import Response
        from .monitoring import PROMETHEUS_AVAILABLE
        if not PROMETHEUS_AVAILABLE:
            raise HTTPException(503, "Install prometheus-client to enable this exporter.")
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        return Response(generate_latest(app.state.pipeline.metrics._registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/tickets")
    def process(request: TicketRequest):
        ticket = Ticket(**request.model_dump())
        with serial:
            p = app.state.pipeline
            outcome = p.process(ticket)
            audit = None
            try:
                audit = DecisionLog(config.sqlite_path)
                run_id = audit.start_run(input_path="api", output_path="api",
                                         config={"confidence_threshold": config.confidence_threshold,
                                                 "retrieval_backend": config.retrieval_backend})
                decision_id = audit.record(outcome, ticket.text)
                audit.finish_run(1)  # Durable commit before releasing any response.
                if outcome.action == "answered" and p.control.disabled:
                    outcome.action = "escalated"
                    outcome.rule = "automation_paused"
                    outcome.reason = "Automatic replies are paused by the operator."
                    outcome.response = None
                    outcome.citations = []
                    audit.mark_paused(decision_id)
            except Exception:
                raise HTTPException(503, "Decision logging unavailable; no reply released.") from None
            finally:
                if audit is not None:
                    audit.conn.close()
            return {"run_id": run_id, "ticket_id": outcome.ticket_id,
                    "action": outcome.action, "rule": outcome.rule, "reason": outcome.reason,
                    "customer_response": outcome.response if outcome.action == "answered" else None,
                    "agent_draft": outcome.response if outcome.action == "escalated" else None,
                    "citations": outcome.citations, "degraded": outcome.degraded,
                    "guardrail_findings": outcome.guardrail_findings}

    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--backend", choices=["lexical", "hybrid"], default=CONFIG.retrieval_backend)
    parser.add_argument("--use-provider", action="store_true")
    args = parser.parse_args()
    uvicorn.run(create_app(config=replace(CONFIG, retrieval_backend=args.backend),
                           use_provider=args.use_provider), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
