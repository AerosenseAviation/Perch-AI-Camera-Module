"""The command line.

    debrief probe <file>
    debrief run <file> [--modules a,b] [--no-audio] [--max-cost 0.50] [--dry-run]
    debrief batch <folder> [--max-cost-total 20.00]
    debrief stage <name> <run-dir>
    debrief eval export <run-dir> -o grades.csv
    debrief eval report grades.csv
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from . import __version__, evaluate
from .config import Config, load_config
from .cost import CostLimitExceeded, CostTracker
from .models import RunManifest
from .modules import MODULE_NAMES
from .pipeline import build_context, run_pipeline, run_stage
from .runs import find_videos, load_manifest, resolve_video
from .stages import STAGE_ORDER
from .stages.probe import probe_file

app = typer.Typer(
    add_completion=False,
    help="Turn a flight video into a post-flight debrief.",
)
eval_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Grading harness.")
app.add_typer(eval_app, name="eval")


def _config(path: Optional[Path]) -> Config:
    cfg = load_config(path)
    return cfg


def _split_modules(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    names = [m.strip() for m in value.split(",") if m.strip()]
    unknown = set(names) - set(MODULE_NAMES)
    if unknown:
        raise typer.BadParameter(
            f"unknown module(s): {', '.join(sorted(unknown))}. "
            f"Known modules: {', '.join(MODULE_NAMES)}"
        )
    return names


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Print the version and exit."),
) -> None:
    if version:
        typer.echo(f"flight-debrief {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


# --- probe -------------------------------------------------------------------


@app.command()
def probe(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Video file."),
    as_json: bool = typer.Option(False, "--json", help="Print the raw probe.json."),
) -> None:
    """Inspect a file: duration, resolution, audio, and telemetry track."""
    result = probe_file(file)
    if as_json:
        typer.echo(json.dumps(result.model_dump(), indent=2))
        raise typer.Exit()

    typer.echo(f"{result.filename}")
    typer.echo(f"  duration    {result.duration:.1f}s")
    typer.echo(f"  resolution  {result.width}x{result.height} @ {result.fps:.2f} fps")
    if result.rotation:
        typer.echo(f"  rotation    {result.rotation}°")
    typer.echo(f"  container   {result.container}")
    typer.echo(f"  size        {result.size_bytes / 1e6:.1f} MB")
    if result.audio:
        typer.echo(
            f"  audio       {result.audio.codec}, {result.audio.channels}ch, "
            f"{result.audio.sample_rate} Hz"
        )
    else:
        typer.echo("  audio       none")
    typer.echo(
        f"  telemetry   {'gpmd stream present' if result.has_telemetry else 'none'}"
    )


# --- run ---------------------------------------------------------------------


@app.command()
def run(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Video file."),
    modules: Optional[str] = typer.Option(
        None, "--modules", help=f"Comma-separated subset of: {', '.join(MODULE_NAMES)}"
    ),
    no_audio: bool = typer.Option(False, "--no-audio", help="Skip audio extraction entirely."),
    max_cost: Optional[float] = typer.Option(
        None, "--max-cost", help="Stop before spending more than this many dollars."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Run every local stage and skip all model calls."
    ),
    config: Optional[Path] = typer.Option(None, "--config", help="Path to a debrief.toml."),
) -> None:
    """Run the whole pipeline over one video."""
    cfg = _config(config)
    ctx = run_pipeline(
        file,
        cfg,
        dry_run=dry_run,
        no_audio=no_audio,
        modules=_split_modules(modules),
        max_cost=max_cost,
    )
    _print_summary(ctx.manifest, ctx.tracker, ctx.run_dir)
    if any(s.status == "failed" for s in ctx.manifest.stages):
        raise typer.Exit(code=1)


def _print_summary(manifest: RunManifest, tracker: CostTracker, run_dir: Path) -> None:
    typer.echo("\n  cost")
    typer.echo(tracker.render())
    if manifest.dry_run:
        typer.echo(f"  (dry run — estimated ${manifest.estimated_cost:.4f} if run for real)")
    debrief = run_dir / "debrief.html"
    if debrief.is_file():
        typer.echo(f"\n  debrief: {debrief}")
    typer.echo(f"  run:     {run_dir}")


# --- batch -------------------------------------------------------------------


@app.command()
def batch(
    folder: Path = typer.Argument(..., exists=True, file_okay=False, help="Folder of videos."),
    max_cost_total: Optional[float] = typer.Option(
        None, "--max-cost-total", help="Ceiling across the whole batch, in dollars."
    ),
    max_cost: Optional[float] = typer.Option(
        None, "--max-cost", help="Per-flight ceiling, in dollars."
    ),
    modules: Optional[str] = typer.Option(None, "--modules", help="Comma-separated subset."),
    no_audio: bool = typer.Option(False, "--no-audio"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Run the pipeline over every video in a folder."""
    cfg = _config(config)
    videos = find_videos(folder)
    if not videos:
        typer.echo(f"No .mp4/.mov/.m4v files under {folder}")
        raise typer.Exit(code=1)

    typer.echo(f"{len(videos)} video(s) under {folder}")
    shared = CostTracker(cfg, max_cost=max_cost_total)
    done: list[Path] = []
    failed: list[tuple[Path, str]] = []

    for video in videos:
        if shared.would_exceed():
            typer.echo(
                f"\nStopping: the batch has reached the ${max_cost_total:.2f} ceiling."
            )
            break
        try:
            ctx = run_pipeline(
                video,
                cfg,
                dry_run=dry_run,
                no_audio=no_audio,
                modules=_split_modules(modules),
                max_cost=max_cost,
                tracker=shared if max_cost_total is not None else None,
            )
        except CostLimitExceeded as exc:
            typer.echo(f"  stopped: {exc}")
            break
        except Exception as exc:  # keep going through the library
            failed.append((video, str(exc)))
            typer.echo(f"  failed: {exc}")
            continue
        if max_cost_total is None:
            # Each run kept its own tracker; fold the spend into the batch total.
            for entry in ctx.tracker.spend():
                shared.record(entry.model, entry.input_tokens, entry.output_tokens)
        done.append(ctx.run_dir)

    typer.echo(f"\n{len(done)} run(s) completed, {len(failed)} failed.")
    typer.echo(f"Batch cost: ${shared.total:.4f}")
    for video, error in failed:
        typer.echo(f"  {video.name}: {error}")


# --- stage -------------------------------------------------------------------


@app.command()
def stage(
    name: str = typer.Argument(..., help=f"One of: {', '.join(STAGE_ORDER)}"),
    run_dir: Path = typer.Argument(..., exists=True, file_okay=False, help="An existing run."),
    max_cost: Optional[float] = typer.Option(None, "--max-cost"),
    modules: Optional[str] = typer.Option(None, "--modules"),
    no_audio: bool = typer.Option(False, "--no-audio"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Re-run one stage against an existing run directory."""
    if name not in STAGE_ORDER:
        raise typer.BadParameter(f"unknown stage {name!r}. Stages: {', '.join(STAGE_ORDER)}")

    cfg = _config(config)
    video = resolve_video(run_dir)
    manifest = load_manifest(run_dir) or RunManifest(
        flight_id=run_dir.name, video=str(video), created=""
    )
    ctx = build_context(
        video,
        run_dir,
        cfg,
        no_audio=no_audio,
        modules=_split_modules(modules),
        max_cost=max_cost,
        manifest=manifest,
    )
    typer.echo(f"{name} → {run_dir}")
    try:
        record = run_stage(name, ctx)
    except Exception as exc:
        typer.echo(f"  failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    manifest.record(record)
    ctx.save_manifest()
    typer.echo(f"  {record.status} in {record.seconds:.1f}s — {record.detail or ''}")
    typer.echo("\n  cost")
    typer.echo(ctx.tracker.render())
    if record.status == "failed":
        raise typer.Exit(code=1)


# --- eval --------------------------------------------------------------------


@eval_app.command("export")
def eval_export(
    run_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    out: Path = typer.Option(Path("grades.csv"), "-o", "--out", help="CSV to write."),
) -> None:
    """Write one row per observation for hand grading."""
    runs, rows = evaluate.export(run_dir, out)
    typer.echo(f"Wrote {rows} rows from {runs} run(s) to {out}")
    typer.echo("Fill the `verdict` column with: useful, obvious, or wrong.")


@eval_app.command("report")
def eval_report(
    grades: Path = typer.Argument(..., exists=True, dir_okay=False),
) -> None:
    """Read a graded CSV and report the verdict rates."""
    typer.echo(evaluate.report(grades))


@eval_app.command("rejections")
def eval_rejections(
    run_dir: Path = typer.Argument(..., exists=True, file_okay=False),
) -> None:
    """Report the validator's rejection rate across one or many runs."""
    typer.echo(evaluate.rejection_summary(run_dir))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
