"""List generated downloads and reports.

Downloads include the standings PDF and standings and validation workbooks.
Reports include statistics, standings data and diagnostics. Rendering assets
are excluded. Files are dated by their UTC `YYYYMMDDTHHMMSSZ` filename suffix,
or by modification time when the suffix is absent. Partial filenames include
`-partial` before the timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo


RUNS_DIRECTORY = "runs"
STOCKHOLM = ZoneInfo("Europe/Stockholm")


@dataclass(frozen=True)
class AvailableFile:
    label_key: str
    partial: bool
    generated_at: datetime
    download_url: str
    order: int


_DOWNLOAD_FILE_TYPES = (
    (("jgp-standings.pdf",), "available_files.names.pdf"),
    (("standings.pdf",), "available_files.names.pdf"),
    (("jgp-standings.xlsx",), "available_files.names.standings_workbook"),
    (("jgp-validations.xlsx",), "available_files.names.validation_workbook"),
)

_REPORT_FILE_TYPES = (
    (("Stats.xlsx",), "available_files.names.statistics_workbook"),
    (("standings.json",), "available_files.names.json"),
    (("diagnostics", "rejected_players.txt"), "available_files.names.rejected_players"),
    (("diagnostics", "walkovers.txt"), "available_files.names.walkovers"),
    (("diagnostics", "no_matches_played.txt"), "available_files.names.no_matches_played"),
    (("diagnostics", "penalties.txt"), "available_files.names.penalties"),
    (("diagnostics", "trojanska-7132-awards.json"), "available_files.names.trojanska_awards"),
    (("diagnostics", "calculation-inputs.json"), "available_files.names.calculation_inputs"),
)


def list_available_files(
    output_directory: Path,
    public_origin: str = "",
) -> tuple[AvailableFile, ...]:
    """Return standings deliverables, newest generation first."""
    return _list_files(
        output_directory,
        _DOWNLOAD_FILE_TYPES,
        public_file_url(public_origin, "/downloads"),
    )


def list_report_files(
    output_directory: Path,
    public_origin: str = "",
) -> tuple[AvailableFile, ...]:
    """Return generated data and diagnostic reports, newest first."""
    return _list_files(
        output_directory,
        _REPORT_FILE_TYPES,
        public_file_url(public_origin, "/reports"),
    )


def _list_files(
    output_directory: Path,
    file_types: tuple[tuple[tuple[str, ...], str], ...],
    url_prefix: str,
) -> tuple[AvailableFile, ...]:
    download_root = Path(output_directory) / RUNS_DIRECTORY
    if not download_root.is_dir() or download_root.is_symlink():
        return ()

    try:
        generation_directories = tuple(download_root.iterdir())
    except OSError:
        return ()

    files: list[AvailableFile] = []
    for run_directory in generation_directories:
        if not run_directory.is_dir() or run_directory.is_symlink():
            continue
        for order, (relative_parts, label_key) in enumerate(file_types):
            for candidate, partial, filename_timestamp in _candidate_files(run_directory, relative_parts):
                download_url = _download_url(download_root, candidate, url_prefix)
                if download_url is None:
                    continue
                try:
                    generated_at = _generated_at(candidate, filename_timestamp)
                except (OSError, ValueError):
                    continue
                files.append(
                    AvailableFile(
                        label_key=label_key,
                        partial=partial,
                        generated_at=generated_at,
                        download_url=download_url,
                        order=order,
                    )
                )

    return tuple(
        sorted(
            files,
            key=lambda item: (-item.generated_at.timestamp(), item.order),
        )
    )


def generation_download_links(
    run_id: str,
    pdf_filename: str,
    *,
    public_origin: str = "",
    url_prefix: str = "",
) -> dict[str, str]:
    """The finished run's PDF, served from the file origin, and JGP's own Available files page."""
    encoded_run_id = quote(run_id, safe="")
    encoded_filename = quote(pdf_filename, safe="")
    return {
        "files": f"{url_prefix}/downloads",
        "pdf": public_file_url(
            public_origin,
            f"/downloads/{encoded_run_id}/{encoded_filename}",
        ),
    }


def public_file_url(public_origin: str, path: str) -> str:
    return f"{public_origin.rstrip('/')}{path}"


def _candidate_files(
    run_directory: Path,
    relative_parts: tuple[str, ...],
) -> tuple[tuple[Path, bool, str | None], ...]:
    expected = Path(relative_parts[-1])
    directory = run_directory.joinpath(*relative_parts[:-1])
    pattern = re.compile(
        rf"^{re.escape(expected.stem)}"
        rf"(?P<partial>-partial)?"
        rf"(?:-(?P<timestamp>\d{{8}}T\d{{6}}Z))?"
        rf"{re.escape(expected.suffix)}$"
    )
    try:
        children = tuple(directory.iterdir())
    except OSError:
        return ()

    matches = []
    for candidate in children:
        match = pattern.fullmatch(candidate.name)
        if match is not None:
            matches.append(
                (
                    candidate,
                    match.group("partial") is not None,
                    match.group("timestamp"),
                )
            )
    return tuple(matches)


def _generated_at(candidate: Path, timestamp: str | None) -> datetime:
    if timestamp is not None:
        generated_at = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ")
        return generated_at.replace(tzinfo=UTC).astimezone(STOCKHOLM)
    return datetime.fromtimestamp(candidate.stat().st_mtime, tz=STOCKHOLM)


def _download_url(
    download_root: Path,
    candidate: Path,
    url_prefix: str,
) -> str | None:
    """Build a direct URL only for a regular file contained by the root."""
    if not candidate.is_file():
        return None

    try:
        relative = candidate.relative_to(download_root)
        resolved_root = download_root.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None

    current = download_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None

    encoded_path = "/".join(quote(part, safe="") for part in relative.parts)
    return f"{url_prefix}/{encoded_path}"
