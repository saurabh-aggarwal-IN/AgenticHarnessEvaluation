from __future__ import annotations

HARNESS_IDS: tuple[str, ...] = ("h1", "h2", "h3")

# Which harnesses receive the structured history fixture. H1's contract is
# note + prompt only, so the evaluator must not report history as available
# for an H1 job even though the dataset case carries a fixture.
HARNESS_RECEIVES_HISTORY: dict[str, bool] = {
    "h1": False,
    "h2": True,
    "h3": True,
}

HARNESS_NAMES: dict[str, str] = {
    "h1": "H1 Direct Hermes CLI",
    "h2": "H2 Planner + Coder + Verifier",
    "h3": "H3 Multi-Agent Specialist Loop",
}

HARNESS_DESCRIPTIONS: dict[str, str] = {
    "h1": (
        "One real Hermes CLI run per encounter. Receives the note and task "
        "prompt only; no patient history fixture is passed."
    ),
    "h2": (
        "LangGraph planner, coder, then verifier with revision loop. "
        "Receives the note, task prompt, and the structured history fixture."
    ),
    "h3": (
        "Multi-step specialist agent: Clinical Extractor -> ICD-10 & CPT "
        "Specialist Sub-Agents -> Action-Oriented Verifier (Approve/Delete/Replace) "
        "with all inter-agent communication in Pydantic format."
    ),
}

TERMINAL_JOB_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}
)
TERMINAL_COMPARISON_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
