"""Plan / trace data types shared by the planner, executor, arms and lab."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

# Tools whose execution has an external side effect (booking, code issue, SMS).
SIDE_EFFECT_TOOLS = {"create_ai_call", "create_health_call", "check_loan_eligibility"}


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    confidence: float = 1.0
    source: str = "jev"  # jev | rule | default


@dataclass
class Plan:
    intent: str
    tool_calls: list[ToolCall]
    # Instructions for the compose model that the legacy first call used to encode
    # in its own text turn (ask for a technician, ask for species, decline, ...).
    compose_notes: list[str] = field(default_factory=list)
    clarification: Optional[str] = None
    escalate: bool = False           # hand this turn to the legacy LLM planner
    escalate_reason: Optional[str] = None
    confidence: float = 1.0          # weakest judgement behind the plan
    answers: dict[str, Any] = field(default_factory=dict)   # raw Jev answers (probabilities)
    questions: dict[str, Any] = field(default_factory=dict) # what was asked (for the lab)
    state: Any = None
    jev_ms: float = 0.0
    jev_input_tokens: int = 0
    jev_model: str = ""
    jev_request_id: Optional[str] = None
    moderation_category: Optional[str] = None   # when Jev did the safety check
    moderation_action: Optional[str] = None
    moderation_confidence: Optional[float] = None

    def tool_names(self) -> list[str]:
        return [c.name for c in self.tool_calls]


@dataclass
class ToolResult:
    name: str
    args: dict[str, Any]
    output: str
    ms: float
    ok: bool = True
    dry_run: bool = False


@dataclass
class StageRecorder:
    """Monotonic stage marks for one arm of one turn. All values in ms from t0."""

    t0: float = field(default_factory=time.monotonic)
    marks: dict[str, float] = field(default_factory=dict)
    spans: dict[str, float] = field(default_factory=dict)
    _open: dict[str, float] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, (time.monotonic() - self.t0) * 1000.0)

    def start(self, name: str) -> None:
        self._open[name] = time.monotonic()

    def end(self, name: str) -> None:
        t = self._open.pop(name, None)
        if t is not None:
            self.spans[name] = self.spans.get(name, 0.0) + (time.monotonic() - t) * 1000.0

    def tool(self, name: str, args: Any, ms: float, ok: bool = True, dry_run: bool = False, output_preview: str = "") -> None:
        self.tools.append({"name": name, "args": args, "ms": round(ms, 1), "ok": ok, "dry_run": dry_run, "output_preview": output_preview[:400]})

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.t0) * 1000.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "marks": {k: round(v, 1) for k, v in self.marks.items()},
            "spans": {k: round(v, 1) for k, v in self.spans.items()},
            "tools": self.tools,
            "meta": self.meta,
            "total_ms": round(self.elapsed_ms(), 1),
        }
