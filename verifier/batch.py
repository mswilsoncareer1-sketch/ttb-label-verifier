"""Run many labels concurrently. One bad file never sinks the batch."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .extract import DEFAULT_MODEL, ExtractionError, extract_label
from .models import ApplicationRecord, VerificationResult
from .rules import verify

# The work is network bound, so threads are enough.
MAX_WORKERS = 8


@dataclass
class LabelJob:
    """One application row paired with its label image."""

    application: ApplicationRecord
    image_bytes: bytes | None


def verify_one(job: LabelJob, client: Any, model: str = DEFAULT_MODEL) -> VerificationResult:
    """Extract and verify a single label, converting failures into a result."""
    start = time.perf_counter()
    label_id = job.application.label_id

    if job.image_bytes is None:
        result = VerificationResult(
            label_id, error=f"No image uploaded for '{job.application.image_file}'."
        )
    else:
        try:
            extracted = extract_label(job.image_bytes, client, model)
            result = verify(job.application, extracted)
        except ExtractionError as exc:
            result = VerificationResult(label_id, error=str(exc))

    result.seconds = time.perf_counter() - start
    return result


def verify_batch(
    jobs: list[LabelJob],
    client: Any,
    model: str = DEFAULT_MODEL,
    on_done: Callable[[int, int], None] | None = None,
) -> list[VerificationResult]:
    """Verify every job, preserving input order in the output."""
    results: list[VerificationResult | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(verify_one, job, client, model): i for i, job in enumerate(jobs)}
        done = 0
        for future in futures:
            results[futures[future]] = future.result()
            done += 1
            if on_done:
                on_done(done, len(jobs))
    return [r for r in results if r is not None]
