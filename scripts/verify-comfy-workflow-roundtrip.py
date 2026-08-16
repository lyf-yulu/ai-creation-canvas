#!/usr/bin/env python3
"""Verify supplied ComfyUI workflow JSON files without emitting their contents."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "server"))

from ai_creation_canvas.comfy.models import WorkflowValidationError  # noqa: E402
from ai_creation_canvas.comfy.workflow_json import export_workflow, parse_workflow_json  # noqa: E402


def _safe_basename(path: Path) -> str:
    return path.name.encode("unicode_escape").decode("ascii")


def _verify(path: Path) -> str:
    try:
        parsed = parse_workflow_json(path.read_bytes())
        for workflow_format in sorted(parsed.formats):
            reloaded = parse_workflow_json(export_workflow(parsed, workflow_format))
            if reloaded.checksum != parsed.checksum:
                raise RuntimeError("WORKFLOW_ROUNDTRIP_MISMATCH")
    except WorkflowValidationError as error:
        raise RuntimeError(error.code) from None
    except OSError:
        raise RuntimeError("WORKFLOW_FILE_UNREADABLE") from None

    formats = ",".join(sorted(parsed.formats))
    return (
        f"{_safe_basename(path)}: format={formats} checksum={parsed.checksum[:12]} "
        f"nodes={parsed.node_count} links={parsed.link_count}"
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify ComfyUI JSON round trips and print only safe summaries."
    )
    parser.add_argument("paths", type=Path, nargs="+", help="workflow JSON files to verify")
    parsed_arguments = parser.parse_args(arguments)

    for path in parsed_arguments.paths:
        try:
            print(_verify(path))
        except RuntimeError as error:
            print(f"{_safe_basename(path)}: error={error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
