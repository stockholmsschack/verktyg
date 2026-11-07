"""Stage and publish generated artifacts under `<output>/runs/<run id>/`.

Publication renames the staging directory, records the run input and updates
`<output>/public-standings.json`. If a commit step fails, earlier steps are
rolled back. Existing run directories are rejected.

PDF rendering uses staged copies of the bundled Typst files, image and fonts,
with system fonts disabled. An uploaded `rules.typ` replaces the bundled rules.
Rendering must succeed before publication starts.

Download filenames share a UTC `YYYYMMDDTHHMMSSZ` suffix. Internal Typst files
keep their plain names. Runs with an explicit tournament list are partial:
their filenames include `-partial`, they skip run-input replay and persistence,
and they leave the public snapshot unchanged. `clear_downloads` removes runs.
"""
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .active_configuration import ActiveConfiguration
from .generation_core import GeneratedArtifacts, GenerationResult
from .generation_orchestrator import GenerationStageError, run_generation
from .generation_progress import ProgressReporter, operation
from .run_record import RunInput, write_run_record
from .typst_rules import effective_rules_path
from .upstream_client import Fetch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TYPST_ASSETS = PROJECT_ROOT / "typst"
RUNS_DIRECTORY = "runs"
RUN_RECORD_FILE = "run_input.json"
PUBLIC_JSON_LINK = "public-standings.json"
TYPST_SOURCE_FILE = "standings.typ"
DIAGNOSTICS_DIRECTORY = "diagnostics"
_TYPST_SUPPORT_FILES = ("lib.typ", "details.typ", "rules.typ", "stsf.png")
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DELIVERABLE_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


class Renderer(Protocol):
    def render(
        self,
        source: Path,
        destination: Path,
        *,
        root: Path,
        font_directory: Path,
    ) -> None: ...


@dataclass(frozen=True)
class PublishedGeneration:
    result: GenerationResult
    run_id: str
    run_directory: Path
    pdf_path: Path


class TypstRenderer:
    """Compile Typst while explicitly excluding every system font."""

    def __init__(self, executable: str = "typst") -> None:
        self.executable = executable

    def render(
        self,
        source: Path,
        destination: Path,
        *,
        root: Path,
        font_directory: Path,
    ) -> None:
        try:
            completed = subprocess.run(
                [
                    self.executable,
                    "compile",
                    "--root",
                    str(root),
                    "--font-path",
                    str(font_directory),
                    "--ignore-system-fonts",
                    "--no-pdf-tags",
                    str(source),
                    str(destination),
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            raise RuntimeError(f"could not start Typst renderer: {error}") from error
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Typst exited with status {completed.returncode}: {detail}")
        if not destination.is_file() or not destination.stat().st_size:
            raise RuntimeError("Typst did not produce a PDF")


def run_record_path(state_directory: Path) -> Path:
    return Path(state_directory) / RUN_RECORD_FILE


def clear_downloads(output_directory: Path) -> None:
    """Remove generated runs and the public snapshot.

    Preserve configuration, run-input and operation records in the state directory.
    """
    output_directory = Path(output_directory)
    (output_directory / PUBLIC_JSON_LINK).unlink(missing_ok=True)
    runs = output_directory / RUNS_DIRECTORY
    if runs.exists():
        shutil.rmtree(runs)


def read_public_snapshot(output_directory: Path) -> bytes | None:
    """Read the published JSON snapshot, or return not found explicitly."""
    path = Path(output_directory) / PUBLIC_JSON_LINK
    if not path.is_symlink():
        return None
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def generate_and_publish(
    configuration: ActiveConfiguration,
    output_directory: Path,
    *,
    fetch: Fetch,
    state_directory: Path,
    today: date,
    run_id: str | None = None,
    override_year: int | None = None,
    override_tournament_ids: list[int] | None = None,
    typst_assets: Path = DEFAULT_TYPST_ASSETS,
    renderer: Renderer | None = None,
    progress: ProgressReporter | None = None,
) -> PublishedGeneration:
    """
    Run the complete generation and persist it. `today` is the only clock read that affects
    standings content; publishing reads the clock once more, for the run id and the file names.
    A run given its own tournament list is partial. Every member-system request goes through `fetch`.
    """
    partial = override_tournament_ids is not None
    result = run_generation(
        configuration,
        fetch=fetch,
        run_record_path=None if partial else run_record_path(state_directory),
        today=today,
        override_year=override_year,
        override_tournament_ids=override_tournament_ids,
        progress=progress,
    )
    return publish_generation(
        result, output_directory, state_directory=state_directory, partial=partial, run_id=run_id,
        typst_assets=typst_assets, renderer=renderer, progress=progress,
    )


def publish_generation(
    result: GenerationResult,
    output_directory: Path,
    *,
    state_directory: Path,
    partial: bool = False,
    run_id: str | None = None,
    typst_assets: Path = DEFAULT_TYPST_ASSETS,
    renderer: Renderer | None = None,
    progress: ProgressReporter | None = None,
) -> PublishedGeneration:
    output_directory = Path(output_directory)
    generated_at = datetime.now(UTC)
    resolved_run_id = run_id or _new_run_id(generated_at)
    deliverable_timestamp = generated_at.strftime(_DELIVERABLE_TIMESTAMP_FORMAT)
    pdf_filename = _deliverable_name("jgp-standings.pdf", partial, deliverable_timestamp)
    json_filename = _deliverable_name("standings.json", partial, deliverable_timestamp)
    typst_source = _artifact_name(TYPST_SOURCE_FILE, partial)
    try:
        _validate_run_id(resolved_run_id)
    except ValueError as error:
        raise GenerationStageError("committing", str(error)) from error
    target = output_directory / RUNS_DIRECTORY / resolved_run_id
    if target.exists():
        raise GenerationStageError("committing", f"run {resolved_run_id!r} already exists")

    output_directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{resolved_run_id}-", dir=output_directory))
    try:
        try:
            with operation(progress, "Staging artifacts"):
                _stage_generation_artifacts(
                    result.artifacts, staging, Path(typst_assets),
                    rules_source=effective_rules_path(state_directory, Path(typst_assets) / "rules.typ"),
                    partial=partial, deliverable_timestamp=deliverable_timestamp,
                )
        except Exception as error:
            raise GenerationStageError("artifact staging", str(error)) from error

        try:
            with operation(progress, "Rendering PDF"):
                (renderer or TypstRenderer()).render(
                    staging / typst_source,
                    staging / pdf_filename,
                    root=staging,
                    font_directory=staging / "fonts",
                )
        except Exception as error:
            raise GenerationStageError("rendering", str(error)) from error

        with operation(progress, "Publishing generation"):
            if partial:
                _commit_partial(staging, target)
            else:
                _commit(
                    staging,
                    target,
                    run_record_path(state_directory),
                    result.standings.run_input,
                    output_directory,
                    json_filename=json_filename,
                )
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return PublishedGeneration(
        result=result, run_id=resolved_run_id, run_directory=target, pdf_path=target / pdf_filename
    )


def _stage_generation_artifacts(
    artifacts: GeneratedArtifacts,
    staging: Path,
    typst_assets: Path,
    *,
    rules_source: Path,
    partial: bool,
    deliverable_timestamp: str,
) -> None:
    def deliverable(filename: str) -> str:
        return _deliverable_name(filename, partial, deliverable_timestamp)

    (staging / deliverable("jgp-validations.xlsx")).write_bytes(artifacts.validation_workbook)
    (staging / deliverable("jgp-standings.xlsx")).write_bytes(artifacts.standings_workbook)
    (staging / deliverable("Stats.xlsx")).write_bytes(artifacts.stats_workbook)
    (staging / deliverable("standings.json")).write_text(artifacts.standings_json, encoding="utf-8")
    (staging / _artifact_name(TYPST_SOURCE_FILE, partial)).write_text(artifacts.typst_source, encoding="utf-8")
    diagnostics = staging / DIAGNOSTICS_DIRECTORY
    diagnostics.mkdir()
    for filename, report in artifacts.diagnostic_reports.items():
        (diagnostics / deliverable(filename)).write_text(report, encoding="utf-8", newline="")
    _stage_typst_assets(typst_assets, staging, rules_source)


def _stage_typst_assets(typst_assets: Path, staging: Path, rules_source: Path) -> None:
    """
    Copy the bundled files the source imports, with the uploaded rules page in place of the bundled
    one, and the fonts it is rendered with, beside it.
    """
    for filename in _TYPST_SUPPORT_FILES:
        source = rules_source if filename == "rules.typ" else typst_assets / filename
        if not source.is_file():
            raise FileNotFoundError(f"missing bundled Typst asset: {source}")
        shutil.copy2(source, staging / filename)

    fonts = typst_assets / "fonts"
    has_fonts = fonts.is_dir() and any(
        path.is_file() and path.suffix.lower() in {".otf", ".ttf"} for path in fonts.iterdir()
    )
    if not has_fonts:
        raise FileNotFoundError(f"no bundled Typst fonts found in: {fonts}")
    shutil.copytree(fonts, staging / "fonts")


def _artifact_name(filename: str, partial: bool) -> str:
    if not partial:
        return filename
    path = Path(filename)
    return f"{path.stem}-partial{path.suffix}"


def _deliverable_name(filename: str, partial: bool, timestamp: str) -> str:
    path = Path(_artifact_name(filename, partial))
    return f"{path.stem}-{timestamp}{path.suffix}"


def _commit_partial(staging: Path, target: Path) -> None:
    """Move a partial run into place; it records no run input and advances no snapshot."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
    except Exception as error:
        raise GenerationStageError("committing", str(error)) from error


def _commit(
    staging: Path,
    target: Path,
    record_path: Path,
    run_input: RunInput,
    output_directory: Path,
    *,
    json_filename: str,
) -> None:
    """
    Move the run into place, record its input, and advance the public snapshot, undoing every
    completed step if a later one fails.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    changed_links: list[tuple[Path, str | None]] = []
    committed = False
    previous_record: bytes | None = None
    record_touched = False
    try:
        os.replace(staging, target)
        committed = True
        record_path.parent.mkdir(parents=True, exist_ok=True)
        previous_record = record_path.read_bytes() if record_path.is_file() else None
        record_touched = True
        write_run_record(run_input, record_path)
        changed_links.append(
            _replace_link(
                output_directory / PUBLIC_JSON_LINK,
                Path(RUNS_DIRECTORY) / target.name / json_filename,
            )
        )
    except Exception as error:
        for path, old_target in reversed(changed_links):
            _restore_link(path, old_target)
        if record_touched:
            if previous_record is None:
                record_path.unlink(missing_ok=True)
            else:
                record_path.write_bytes(previous_record)
        if committed and target.exists():
            shutil.rmtree(target)
        raise GenerationStageError("committing", str(error)) from error


def _replace_link(path: Path, target: Path) -> tuple[Path, str | None]:
    if path.exists() and not path.is_symlink():
        raise FileExistsError(f"publication reference is not a symlink: {path}")
    old_target = os.readlink(path) if path.is_symlink() else None
    temporary = path.parent / f".{path.name}-{uuid4().hex}"
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
    finally:
        if temporary.is_symlink():
            temporary.unlink()
    return path, old_target


def _restore_link(path: Path, old_target: str | None) -> None:
    if old_target is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.parent / f".{path.name}-rollback-{uuid4().hex}"
    os.symlink(old_target, temporary)
    os.replace(temporary, path)


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run_id must contain only letters, digits, dots, underscores, and hyphens")


def _new_run_id(generated_at: datetime) -> str:
    return f"{generated_at.strftime(_DELIVERABLE_TIMESTAMP_FORMAT)}-{uuid4().hex[:8]}"
