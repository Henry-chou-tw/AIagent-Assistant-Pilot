"""CLI entrypoint (Pilot Step 7): this is the ONLY thing the Discord ->
Channels -> live Claude Code session should call when a Discord message
comes in, per CLAUDE.md's instructions. The live session's job is
transport relay only (receive from Discord, invoke this, relay the
printed response back to Discord via the discord plugin) -- the actual
classification, model invocation, and data persistence all happen inside
this deterministic script, not inside the live session's own free-form
reasoning. This is what keeps data capture reliable even though the live
session itself is not fully deterministic (see the Pilot kickoff
discussion's point on recording reliability).

Usage:
    python -m pilot_agent.main handle --source discord --channel-ref <id> --input "..."
    python -m pilot_agent.main correct --id <interaction_id> --correction "..." [--final-action-type ...] [--final-domain ...]
    python -m pilot_agent.main close --id <interaction_id> --status done --outcome "..."
    python -m pilot_agent.main list-open --action-type todo
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from .config import database_path
from .ids import generate_id
from .intake_classifier import classify
from .models import ACTION_TYPES, TASK_STATUSES, Interaction
from .providers.claude_provider import ClaudeProvider
from .storage import db as db_module
from .storage.sqlite_interaction_repository import SqliteInteractionRepository


def _repository() -> SqliteInteractionRepository:
    conn = db_module.connect(database_path())
    db_module.initialize_schema(conn)
    return SqliteInteractionRepository(conn)


def cmd_handle(args: argparse.Namespace) -> None:
    repo = _repository()
    model = ClaudeProvider()

    result = classify(model, args.input)

    interaction = Interaction(
        id=generate_id("interaction"),
        created_at=dt.datetime.now(dt.timezone.utc),
        source=args.source,
        channel_ref=args.channel_ref,
        raw_input=args.input,
        action_type=result.action_type,
        domain=result.domain,
        agent_response=result.response_text,
        model_used=result.model_used,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        estimated_cost_usd=result.estimated_cost_usd,
        purpose="intake_classification",
        task_status="open" if result.action_type != "unknown" else "unknown",
    )
    repo.save(interaction)

    # Machine-readable line first (for programmatic callers), then the
    # plain response text a relaying session should actually send back.
    print(json.dumps({"id": interaction.id, "action_type": interaction.action_type, "domain": interaction.domain}))
    print("---")
    print(interaction.agent_response)


def cmd_correct(args: argparse.Namespace) -> None:
    repo = _repository()
    interaction = repo.get(args.id)
    if interaction is None:
        print(f"error: no interaction with id {args.id}", file=sys.stderr)
        sys.exit(1)

    interaction.henry_correction = args.correction
    if args.final_action_type:
        if args.final_action_type not in ACTION_TYPES:
            print(f"error: final-action-type must be one of {ACTION_TYPES}", file=sys.stderr)
            sys.exit(1)
        interaction.final_action_type = args.final_action_type
    if args.final_domain:
        interaction.final_domain = args.final_domain
    repo.save(interaction)
    print(f"corrected {args.id}")


def cmd_close(args: argparse.Namespace) -> None:
    repo = _repository()
    interaction = repo.get(args.id)
    if interaction is None:
        print(f"error: no interaction with id {args.id}", file=sys.stderr)
        sys.exit(1)
    if args.status not in TASK_STATUSES:
        print(f"error: status must be one of {TASK_STATUSES}", file=sys.stderr)
        sys.exit(1)

    interaction.task_status = args.status
    interaction.closure_outcome = args.outcome
    if args.status in ("done", "cancelled"):
        interaction.closed_at = dt.datetime.now(dt.timezone.utc)
    repo.save(interaction)
    print(f"closed {args.id} as {args.status}")


def cmd_list_open(args: argparse.Namespace) -> None:
    repo = _repository()
    for interaction in repo.open_by_action_type(args.action_type):
        print(f"{interaction.id}\t{interaction.created_at.isoformat()}\t{interaction.raw_input[:80]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pilot_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_handle = sub.add_parser("handle")
    p_handle.add_argument("--source", required=True)
    p_handle.add_argument("--channel-ref", default=None)
    p_handle.add_argument("--input", required=True)
    p_handle.set_defaults(func=cmd_handle)

    p_correct = sub.add_parser("correct")
    p_correct.add_argument("--id", required=True)
    p_correct.add_argument("--correction", required=True)
    p_correct.add_argument("--final-action-type", default=None)
    p_correct.add_argument("--final-domain", default=None)
    p_correct.set_defaults(func=cmd_correct)

    p_close = sub.add_parser("close")
    p_close.add_argument("--id", required=True)
    p_close.add_argument("--status", required=True)
    p_close.add_argument("--outcome", default=None)
    p_close.set_defaults(func=cmd_close)

    p_list = sub.add_parser("list-open")
    p_list.add_argument("--action-type", required=True)
    p_list.set_defaults(func=cmd_list_open)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
