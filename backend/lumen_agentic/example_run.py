"""Small CLI example for invoking the LangGraph workflow.

Requires local model serving and dependencies from requirements.txt.
"""

from __future__ import annotations

from uuid import uuid4

from .agents import build_agent_runtime
from .graph import build_lumen_graph


def main() -> None:
    runtime = build_agent_runtime()
    graph = build_lumen_graph(runtime)
    result = graph.invoke(
        {
            "workflow_type": "new_referral",
            "tenant_id": str(uuid4()),
            "raw_input": {
                "source_channel": "email",
                "raw_text": "Referral for an adult patient requesting online therapy in Portuguese.",
            },
            "approvals": {},
            "audit_events": [],
            "errors": [],
            "human_review_queue": [],
        }
    )
    print(result)


if __name__ == "__main__":
    main()

