"""Running one pipeline stage on demand, for the dashboard's per-stage retry button.

The stages are minutes to hours long (enrichment measures about 52s per document), so a retry
cannot answer inside its own request. It starts a background thread and reports what happened
through the pipeline view the page already polls.

There is no job table. A run's result lives in this process only, so a restart forgets it. That
is on purpose: the durable state is the pipeline itself, which every stage reads back off the
documents, their hashes and their memberships.
"""

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

logger = logging.getLogger(__name__)

Stage = Literal["fetch", "embed", "enrich", "extract"]


@dataclass(frozen=True)
class StageRun:
    """One attempt at one stage. `detail` carries the summary, or the error that ended it."""

    stage: Stage
    state: Literal["running", "done", "error"]
    started_at: str
    finished_at: str | None = None
    detail: str | None = None


class StageBusy(Exception):
    """Raised when a run is asked for while another is still going."""

    def __init__(self, current: StageRun) -> None:
        super().__init__(f"{current.stage} is already running")
        self.current = current


class StageRunner:
    """Runs a stage in the background, one at a time.

    Single-flight across all four stages rather than one slot per stage: they share one ollama
    and write the same rows, so two runs at once would spend the model twice on one document.
    """

    def __init__(
        self,
        stages: Mapping[Stage, Callable[[], str]],
        *,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._stages = dict(stages)
        self._spawn = spawn if spawn is not None else _spawn_thread
        self._lock = threading.Lock()
        self._current: StageRun | None = None
        self._last: StageRun | None = None

    @property
    def latest(self) -> StageRun | None:
        """The run in flight, or the last one to finish. None before anything has run."""
        with self._lock:
            return self._current if self._current is not None else self._last

    def start(self, stage: Stage) -> StageRun:
        """Start `stage` in the background. Raises `StageBusy` if a run is already in flight."""
        with self._lock:
            if self._current is not None:
                raise StageBusy(self._current)
            run = StageRun(stage=stage, state="running", started_at=_now())
            self._current = run
        self._spawn(lambda: self._execute(run))
        return run

    def _execute(self, run: StageRun) -> None:
        try:
            detail = self._stages[run.stage]()
            finished = replace(run, state="done", finished_at=_now(), detail=detail)
        except Exception as error:
            logger.exception("stage %s failed", run.stage)
            finished = replace(
                run, state="error", finished_at=_now(), detail=f"{type(error).__name__}: {error}"
            )
        with self._lock:
            self._last = finished
            self._current = None


def _spawn_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


def _now() -> str:
    return datetime.now(UTC).isoformat()
