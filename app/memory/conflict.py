"""
Deterministic conflict, update, and temporal analysis engine for agent memory.

Distinguishes:
1. Genuine incompatible claims (CONTRADICTS).
2. Updates / superseding values (SUPERSEDES).
3. Changes in goals or configurations.
4. Target vs. empirical measurement comparisons (e.g. benchmark vs latency target).
5. Compatible assertions that can both be true.
6. Ambiguous / uncertain claims where evidence is insufficient.

Never deletes or overwrites existing evidence. Preserves both sides with timestamps and provenance.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from app.memory.models import (
    ConflictDetail,
    StructuredIngestPayload,
    StructuredMemory,
    StructuredGoal,
    StructuredDecision,
    StructuredExperience,
    StructuredRelationship,
)

logger = logging.getLogger("mai.memory.conflict")


def parse_latency_threshold(text: str) -> Optional[float]:
    """Extract numeric latency target threshold in ms from goal or claim text."""
    t_lower = text.lower()
    # If the text explicitly states a benchmark or measurement, it is not a target threshold
    if "benchmark" in t_lower or "measured" in t_lower:
        return None
    pattern = r"(?:target|goal|below|under|<|<=|to\s+below|latency\s+(?:is|of|to)?)\s*(\d+(?:\.\d+)?)\s*(?:ms|milliseconds?)\b"
    match = re.search(pattern, t_lower)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def parse_benchmark_value(text: str) -> Optional[float]:
    """Extract numeric measured benchmark value in ms from experience or measurement text."""
    t_lower = text.lower()
    pattern = r"(?:measured|benchmark|execution|result|observed|outcome).*?(\d+(?:\.\d+)?)\s*(?:ms|milliseconds?)\b"
    match = re.search(pattern, t_lower)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    # Fallback to direct number if 'benchmark' or 'measured' in text
    if "benchmark" in t_lower or "measured" in t_lower:
        num_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:ms|milliseconds?)\b", t_lower)
        if num_match:
            try:
                return float(num_match.group(1))
            except ValueError:
                return None
    return None


def extract_technology_name(text: str) -> Optional[str]:
    """Extract known technology / database names from text."""
    known_techs = [
        "falkordb",
        "postgresql",
        "postgres",
        "redis",
        "neo4j",
        "mongodb",
        "sqlite",
        "mysql",
        "memcached",
    ]
    t_lower = text.lower()
    for tech in known_techs:
        # Match as word
        if re.search(rf"\b{tech}\b", t_lower):
            if tech == "postgres":
                return "postgresql"
            return tech
    return None


def is_migration_claim(text: str) -> bool:
    """Check if statement asserts an update, migration, or transition."""
    migration_terms = [
        "migrated",
        "migrate",
        "switched",
        "switch",
        "changed",
        "update",
        "updated",
        "replaced",
        "superseded",
        "moved to",
        "transitioned",
        "adopted",
    ]
    t_lower = text.lower()
    return any(re.search(rf"\b{term}\b", t_lower) for term in migration_terms)


def is_speculative_or_ambiguous(text: str) -> bool:
    """Check if statement is hypothetical, speculative, or uncertain."""
    spec_terms = [
        "might",
        "could",
        "maybe",
        "considering",
        "considering whether",
        "thinking about",
        "perhaps",
        "possibly",
        "tentatively",
        "not sure",
    ]
    t_lower = text.lower()
    return any(re.search(rf"\b{term}\b", t_lower) for term in spec_terms)


def evaluate_conflict_between_claims(
    older_claim: str,
    newer_claim: str,
    older_type: str = "fact",
    newer_type: str = "fact",
    older_id: Optional[str] = None,
    newer_id: Optional[str] = None,
    older_ts: Optional[str] = None,
    newer_ts: Optional[str] = None,
) -> Optional[ConflictDetail]:
    """
    Deterministically evaluates whether two claims conflict, supersede, or are compatible.
    Returns a ConflictDetail if a conflict, update, or mismatch is detected, else None.
    """
    text_old = older_claim.strip()
    text_new = newer_claim.strip()

    # 1. Speculative or ambiguous statements remain uncertain
    if is_speculative_or_ambiguous(text_old) or is_speculative_or_ambiguous(text_new):
        tech_old = extract_technology_name(text_old)
        tech_new = extract_technology_name(text_new)
        if tech_old and tech_new and tech_old != tech_new:
            return ConflictDetail(
                subject="technology_proposal",
                conflict_type="uncertain",
                original_claim=text_old,
                original_id=older_id,
                original_timestamp=older_ts,
                new_claim=text_new,
                new_id=newer_id,
                new_timestamp=newer_ts,
                reason="Proposals or conditional phrasing detected without definitive commitment. Insufficient evidence to establish a conflict or update.",
                classification_certainty="uncertain",
                source_memory_ids=[older_id, newer_id] if older_id and newer_id else [],
            )

    # 2. Latency Goal / Target comparison (e.g. 30ms -> 20ms)
    lat_old = parse_latency_threshold(text_old)
    lat_new = parse_latency_threshold(text_new)

    if lat_old is not None and lat_new is not None:
        if abs(lat_old - lat_new) > 0.01:
            # Different numerical targets for response latency
            is_update = is_migration_claim(text_new) or "target" in text_new.lower() or "goal" in text_new.lower()
            if is_update:
                return ConflictDetail(
                    subject="target_response_latency",
                    conflict_type="superseded",
                    original_claim=text_old,
                    original_id=older_id,
                    original_timestamp=older_ts,
                    new_claim=text_new,
                    new_id=newer_id,
                    new_timestamp=newer_ts,
                    reason=f"Goal target response latency was updated from below {lat_old:g} ms to below {lat_new:g} ms. The newer statement supersedes the previous goal.",
                    classification_certainty="deterministic",
                    source_memory_ids=[older_id, newer_id] if older_id and newer_id else [],
                )
            else:
                return ConflictDetail(
                    subject="target_response_latency",
                    conflict_type="contradiction",
                    original_claim=text_old,
                    original_id=older_id,
                    original_timestamp=older_ts,
                    new_claim=text_new,
                    new_id=newer_id,
                    new_timestamp=newer_ts,
                    reason=f"Incompatible latency target assertions without explicit update phrasing ({lat_old:g} ms vs {lat_new:g} ms).",
                    classification_certainty="deterministic",
                    source_memory_ids=[older_id, newer_id] if older_id and newer_id else [],
                )

    # 3. Latency Target vs. Benchmark Measurement Comparison (e.g. Target 20ms vs Benchmark 24ms)
    bench_val = parse_benchmark_value(text_new) or parse_benchmark_value(text_old)
    target_val = lat_new if bench_val == parse_benchmark_value(text_old) else lat_old

    if bench_val is not None and target_val is not None:
        if bench_val > target_val:
            return ConflictDetail(
                subject="latency_target_vs_benchmark",
                conflict_type="target_mismatch",
                original_claim=text_old,
                original_id=older_id,
                original_timestamp=older_ts,
                new_claim=text_new,
                new_id=newer_id,
                new_timestamp=newer_ts,
                reason=(
                    f"Measured benchmark ({bench_val:g} ms) does not meet the stated target latency (below {target_val:g} ms), "
                    "assuming identical evaluation metric and conditions. "
                    "Note: A single synthetic measurement observation does not establish overall system performance and is not independently verified."
                ),
                classification_certainty="deterministic",
                source_memory_ids=[older_id, newer_id] if older_id and newer_id else [],
            )
        else:
            return ConflictDetail(
                subject="latency_target_vs_benchmark",
                conflict_type="compatible",
                original_claim=text_old,
                original_id=older_id,
                original_timestamp=older_ts,
                new_claim=text_new,
                new_id=newer_id,
                new_timestamp=newer_ts,
                reason=f"Measured benchmark ({bench_val:g} ms) satisfies the stated target latency (below {target_val:g} ms).",
                classification_certainty="deterministic",
                source_memory_ids=[older_id, newer_id] if older_id and newer_id else [],
            )

    # 4. Database / Technology choice comparison (e.g. PostgreSQL -> FalkorDB)
    tech_old = extract_technology_name(text_old)
    tech_new = extract_technology_name(text_new)

    if tech_old and tech_new and tech_old != tech_new:
        # Check if subject is primary database or database engine
        is_db_subject = any(term in text_old.lower() for term in ["database", "db", "uses", "project"]) and any(
            term in text_new.lower() for term in ["database", "db", "uses", "project", "migrate", "migrated", "switched"]
        )

        if is_db_subject:
            if is_migration_claim(text_new):
                return ConflictDetail(
                    subject="primary_database",
                    conflict_type="superseded",
                    original_claim=text_old,
                    original_id=older_id,
                    original_timestamp=older_ts,
                    new_claim=text_new,
                    new_id=newer_id,
                    new_timestamp=newer_ts,
                    reason=f"Explicit migration claim indicates project transitioned from {tech_old.title()} to {tech_new.title()}. Newer claim supersedes prior technology state.",
                    classification_certainty="deterministic",
                    source_memory_ids=[older_id, newer_id] if older_id and newer_id else [],
                )
            else:
                return ConflictDetail(
                    subject="primary_database",
                    conflict_type="contradiction",
                    original_claim=text_old,
                    original_id=older_id,
                    original_timestamp=older_ts,
                    new_claim=text_new,
                    new_id=newer_id,
                    new_timestamp=newer_ts,
                    reason=f"Mutually exclusive primary database claims for the same component without temporal migration context ({tech_old.title()} vs {tech_new.title()}).",
                    classification_certainty="deterministic",
                    source_memory_ids=[older_id, newer_id] if older_id and newer_id else [],
                )

    return None
