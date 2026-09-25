"""Run the real SDK worker with a memory transport; no cloud claim or upload.

Only the server transport is substituted. The upstream WorkerRuntime performs
selection, evaluator invocation, result validation, and result submission.
"""

from datetime import datetime, timedelta, timezone

from failproof_checks import app
from failproofai_sdk.evaluator import (
    Assignment,
    HeartbeatResponse,
    PlannedRun,
    PlanResponse,
    SessionTranscript,
    TranscriptEvent,
    WorkerConfig,
    WorkerRuntime,
)


def transcript(case_id, events):
    start = datetime.now(timezone.utc)
    return SessionTranscript(
        assignment_id=case_id,
        session_id=case_id,
        session_revision_id="1",
        agent_id="scripted-refund-policy",
        environment="offline-comparison",
        started_at=start.isoformat(),
        ended_at=(start + timedelta(seconds=1)).isoformat(),
        event_count=len(events),
        events=tuple(
            TranscriptEvent(
                id=e["id"],
                ts=(start + timedelta(milliseconds=i)).isoformat(),
                event_type=e["type"],
                payload=e["payload"],
            )
            for i, e in enumerate(events)
        ),
    )


class MemoryTransport:
    def __init__(self, session):
        self.session = session
        self.submissions = []

    def transcript(self, assignment, *, worker_id):
        return self.session

    def plan(self, assignment_id, request):
        return PlanResponse(
            assignment_id=assignment_id,
            assignment_status="planned",
            runs=tuple(
                PlannedRun(f"run-{s.eval_key}", s.eval_key, s.eval_version)
                for s in request.selected
            ),
        )

    def submit_result(self, run_id, request):
        self.submissions.append(request)

    def heartbeat(self, request):
        return HeartbeatResponse(
            lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            accepted_run_ids=tuple(run.evaluation_run_id for run in request.runs),
        )


async def evaluate(case_id, events):
    session = transcript(case_id, events)
    transport = MemoryTransport(session)
    runtime = WorkerRuntime(
        app,
        WorkerConfig(
            server_url="https://unused.invalid",
            credential="unused-local-transport",
            worker_id="comparison",
            max_concurrency=1,
        ),
        client=transport,
    )
    assignment = Assignment(
        assignment_id=case_id,
        lease_generation=1,
        lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        session_id=case_id,
        session_revision_id="1",
        agent_id=session.agent_id,
        environment=session.environment,
        trigger_reason="manual",
        event_count=len(events),
        transcript_url="/unused",
    )
    await runtime.process_assignment(assignment)
    if len(transport.submissions) != 1:
        raise RuntimeError("Expected exactly one SDK result submission")
    result = transport.submissions[0]
    if result.status.value != "succeeded":
        return {"status": "error", "error_code": result.error_code, "error": result.error_message}
    item = next(r for r in result.results if r.result_key == "refund_contract")
    return {
        "status": "pass" if item.bool_value else "fail",
        "reasoning": item.reasoning,
        "result": result.to_wire(),
    }
