import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rag import answer

USE_GUARDRAILS = os.getenv("USE_GUARDRAILS", "false").lower() == "true"
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "unguarded")

if USE_GUARDRAILS:
    from guardrails import check_input, check_output

app = FastAPI(title=f"Acme RAG ({INSTANCE_NAME})")


class Query(BaseModel):
    question: str
    debug: bool = False


@app.get("/health")
def health():
    return {"status": "ok", "instance": INSTANCE_NAME, "guardrails": USE_GUARDRAILS}


@app.post("/ask")
def ask(q: Query):
    input_failed = []
    output_failed = []

    if USE_GUARDRAILS:
        _, input_failed, _ = check_input(q.question)
        if input_failed:
            return {
                "answer": None,
                "blocked": True,
                "blocked_by": "input_scanner",
                "scanners_failed": input_failed,
            }

    result = answer(q.question)

    if USE_GUARDRAILS:
        sanitized, output_failed, _ = check_output(q.question, result["answer"])
        if output_failed:
            return {
                "answer": sanitized,
                "blocked": True,
                "blocked_by": "output_scanner",
                "scanners_failed": output_failed,
                "sources": result["sources"],
            }
        result["answer"] = sanitized

    response = {
        "answer": result["answer"],
        "sources": result["sources"],
        "blocked": False,
        "instance": INSTANCE_NAME,
    }
    if q.debug:
        response["prompt_sent"] = result["prompt_sent"]
    return response
