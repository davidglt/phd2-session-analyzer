#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 David Gonzalez Lopez-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
PHD2 Dither, Settling, and Sky-Condition Log Analyzer.

Analyze PHD2 guide logs, extract guiding-session configuration, identify
dither commands and settling outcomes, calculate dither amplitudes, estimate
settling intervals from guide-frame timestamps, measure SNR behaviour during
each dither, identify possible cloud-related interruptions, estimate a
settling timeout candidate, export results to CSV, and generate a detailed
plain-text report.

Configuration
-------------
The required phd2_dither_analyzer.properties file must be located beside this
script unless an alternative path is supplied as the sole command-line
argument.

Set the PHD2 guide-log source with:

    phd2.logs.path=C:/Users/davidglt/Documents/PHD2/*GuideLog*.txt

The source may be a single PHD2 guide-log file, a directory containing guide
logs, or a glob pattern. When multiple files match, the script selects the
newest log containing a Guiding Begins marker or a recognized PHD2 dither
command.

Reports
-------
Reports are written to the reports directory beside this script:

    reports/
        PHD2_GuideLog_YYYY-MM-DD_HHMMSS_dither_report.txt
        PHD2_GuideLog_YYYY-MM-DD_HHMMSS_dithers.csv

The directory is created automatically if it does not already exist.

Usage
-----
Run with the default configuration file:

    python phd2_dither_analyzer.py

Run with a different configuration file:

    python phd2_dither_analyzer.py path/to/phd2_dither_analyzer.properties

Features
--------
- Parses PHD2 guide logs written with INFO DITHER by or INFO: DITHER by.
- Detects Settling complete and Settling failed state changes.
- Extracts guiding configuration and dither settings.
- Calculates pixel and angular dither amplitudes.
- Reads PHD2 guide-frame CSV records and final RA/DEC errors.
- Calculates initial, mean, minimum, and final SNR per dither.
- Counts dropped guide frames and guide-star loss messages.
- Labels clear, possible-cloud, likely-cloud, and star-lost conditions.
- Estimates a settling timeout candidate from failed dither durations.
- Writes a detailed text report and a machine-readable CSV file to reports/.
"""

from __future__ import annotations

import configparser
import csv
import glob
import math
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROPERTIES = SCRIPT_DIR / "phd2_dither_analyzer.properties"
REPORTS_DIR = SCRIPT_DIR / "reports"

NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"

GUIDING_BEGINS_RE = re.compile(
    r"Guiding Begins at\s+(?P<timestamp>.+)",
    re.IGNORECASE,
)

GUIDING_ENDS_RE = re.compile(
    r"Guiding Ends at\s+(?P<timestamp>.+)",
    re.IGNORECASE,
)

PROFILE_RE = re.compile(
    r"^\s*Equipment Profile\s*=\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)

CAMERA_RE = re.compile(
    r"^\s*(?:Guide Camera|Camera)\s*=\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)

MOUNT_RE = re.compile(
    r"^\s*Mount\s*=\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)

GUIDE_EXPOSURE_RE = re.compile(
    rf"\b(?:Guide\s+)?Exposure\s*[:=]?\s*"
    rf"(?P<value>{NUMBER})\s*"
    r"(?P<unit>ms|msec|milliseconds|s|sec|seconds?)\b",
    re.IGNORECASE,
)

FOCAL_LENGTH_RE = re.compile(
    rf"\b(?:Guide\s+)?Focal\s+Length\s*[:=]?\s*"
    rf"(?P<value>{NUMBER})\s*mm\b",
    re.IGNORECASE,
)

PIXEL_SCALE_RE = re.compile(
    rf"\b(?:Pixel\s+Scale|Image\s+Scale)\s*[:=]?\s*"
    rf"(?P<value>{NUMBER})\s*"
    r"(?:arc[-\s]?sec(?:onds?)?\s*(?:/|per)?\s*(?:px|pixel)|"
    r"arcsec\s*/\s*px)",
    re.IGNORECASE,
)

DITHER_CONFIGURATION_RE = re.compile(
    rf"\bDither\s*=\s*"
    r"(?P<axes>both(?:\s+axes)?|ra(?:\s+only)?|dec(?:\s+only)?)"
    r".*?"
    rf"Dither\s+scale\s*=\s*(?P<scale>{NUMBER})",
    re.IGNORECASE,
)

DITHER_AXES_RE = re.compile(
    r"\bDither\s*=\s*"
    r"(?P<value>both(?:\s+axes)?|ra(?:\s+only)?|dec(?:\s+only)?)",
    re.IGNORECASE,
)

DITHER_SCALE_RE = re.compile(
    rf"\bDither\s+scale\s*=\s*(?P<value>{NUMBER})",
    re.IGNORECASE,
)

RA_ALGORITHM_RE = re.compile(
    r"\bX guide algorithm\s*=\s*(?P<value>[^,;]+)",
    re.IGNORECASE,
)

RA_HYSTERESIS_RE = re.compile(
    rf"\bX guide algorithm.*?\bHysteresis\s*=\s*"
    rf"(?P<value>{NUMBER})",
    re.IGNORECASE,
)

RA_AGGRESSION_RE = re.compile(
    rf"\bX guide algorithm.*?\bAggression\s*=\s*"
    rf"(?P<value>{NUMBER})",
    re.IGNORECASE,
)

RA_MIN_MOVE_RE = re.compile(
    rf"\bX guide algorithm.*?\bMinimum move\s*=\s*"
    rf"(?P<value>{NUMBER})",
    re.IGNORECASE,
)

DEC_ALGORITHM_RE = re.compile(
    r"\bY guide algorithm\s*=\s*(?P<value>[^,;]+)",
    re.IGNORECASE,
)

DEC_AGGRESSION_RE = re.compile(
    rf"\bY guide algorithm.*?\bAggression\s*=\s*"
    rf"(?P<value>{NUMBER})",
    re.IGNORECASE,
)

DEC_MIN_MOVE_RE = re.compile(
    rf"\bY guide algorithm.*?\bMinimum move\s*=\s*"
    rf"(?P<value>{NUMBER})",
    re.IGNORECASE,
)

BACKLASH_RE = re.compile(
    r"\bBacklash comp\s*=\s*(?P<value>enabled|disabled)",
    re.IGNORECASE,
)

DITHER_RE = re.compile(
    r"\bINFO\s*:?\s*DITHER\s+by\s+"
    r"(?P<dx>[+-]?\d+(?:\.\d+)?),\s*"
    r"(?P<dy>[+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

SETTLING_STARTED_RE = re.compile(
    r"\bINFO\s*:?\s*SETTLING\s+STATE\s+CHANGE,\s*"
    r"Settling\s+started\b",
    re.IGNORECASE,
)

SETTLING_COMPLETE_RE = re.compile(
    r"\bINFO\s*:?\s*SETTLING\s+STATE\s+CHANGE,\s*"
    r"Settling\s+complete\b",
    re.IGNORECASE,
)

SETTLING_FAILED_RE = re.compile(
    r"\bINFO\s*:?\s*SETTLING\s+STATE\s+CHANGE,\s*"
    r"Settling\s+failed\b",
    re.IGNORECASE,
)

STAR_LOST_RE = re.compile(
    r"star\s+lost",
    re.IGNORECASE,
)


@dataclass
class SessionConfiguration:
    """Relevant PHD2 configuration extracted from a guide log."""

    start_timestamp: str | None = None
    end_timestamp: str | None = None
    equipment_profile: str | None = None
    mount: str | None = None
    guide_camera: str | None = None
    guide_exposure_ms: float | None = None
    guide_focal_length_mm: float | None = None
    pixel_scale_arcsec_px: float | None = None
    dither_axes: str | None = None
    dither_scale: float | None = None
    ra_algorithm: str | None = None
    ra_hysteresis: float | None = None
    ra_aggression: float | None = None
    ra_min_move_px: float | None = None
    dec_algorithm: str | None = None
    dec_aggression: float | None = None
    dec_min_move_px: float | None = None
    dec_backlash_compensation: str | None = None


@dataclass
class GuideFrame:
    """Parsed PHD2 guide-frame telemetry."""

    elapsed_seconds: float
    raw_ra_error_px: float
    raw_dec_error_px: float
    snr: float | None
    star_mass: float | None
    error_code: int | None


@dataclass
class DitherEvent:
    """A PHD2 dither command and its settling and sky-quality metrics."""

    number: int
    line_number: int
    dx_px: float
    dy_px: float
    magnitude_px: float
    magnitude_arcsec: float | None
    status: str
    start_time_s: float | None
    end_time_s: float | None
    settle_time_s: float | None
    final_ra_error_px: float | None
    final_dec_error_px: float | None
    final_total_error_px: float | None
    initial_snr: float | None
    mean_snr: float | None
    minimum_snr: float | None
    final_snr: float | None
    snr_drop_percent: float | None
    drop_frames: int
    star_lost_events: int
    cloud_indicator: str
    timeout_suspected: bool = False


def read_properties(properties_path: Path) -> dict[str, str]:
    """Read a simple Java-style key=value properties file."""

    content = properties_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    parser = configparser.ConfigParser(
        interpolation=None,
        delimiters=("=", ":"),
    )
    parser.optionxform = str
    parser.read_string("[DEFAULT]\n" + content)

    return {
        key.strip().lower(): value.strip()
        for key, value in parser.defaults().items()
    }


def discover_logs(log_source: str) -> list[Path]:
    """
    Discover PHD2 guide logs from a file, directory, or glob pattern.

    Returned paths are ordered from oldest to newest by modification time.
    """

    glob_matches = [
        Path(match)
        for match in glob.glob(log_source, recursive=True)
        if Path(match).is_file()
    ]

    if glob_matches:
        return sorted(
            glob_matches,
            key=lambda item: item.stat().st_mtime,
        )

    source_path = Path(log_source).expanduser()

    if source_path.is_file():
        return [source_path]

    if source_path.is_dir():
        guide_logs = list(source_path.glob("*GuideLog*.txt"))

        if not guide_logs:
            guide_logs = list(
                source_path.glob("*guidelog*.txt")
            )

        return sorted(
            (
                item
                for item in guide_logs
                if item.is_file()
            ),
            key=lambda item: item.stat().st_mtime,
        )

    return []


def parse_optional_float(value: str) -> float | None:
    """Return a float or None for an empty or invalid field."""

    cleaned = value.strip()

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_optional_int(value: str) -> int | None:
    """Return an integer or None for an empty or invalid field."""

    cleaned = value.strip()

    if not cleaned:
        return None

    try:
        return int(cleaned)
    except ValueError:
        return None


def find_guide_frame_fields(
    line: str,
) -> list[str] | None:
    """
    Parse a PHD2 CSV guide-frame record.

    Expected field order:

        Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,
        RAGuideDistance,DECGuideDistance,RADuration,RADirection,
        DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode
    """

    cleaned = line.lstrip("\ufeff").strip()

    if not cleaned:
        return None

    fields = next(
        csv.reader(
            StringIO(cleaned),
            skipinitialspace=True,
        ),
        [],
    )

    if len(fields) < 9:
        return None

    if not fields[0].strip().isdigit():
        return None

    if fields[2].strip().lower() != "mount":
        return None

    try:
        float(fields[1])
        float(fields[3])
        float(fields[4])
        float(fields[5])
        float(fields[6])
    except ValueError:
        return None

    return fields


def is_guide_frame(line: str) -> bool:
    """Return True when a line is a valid PHD2 guide-frame CSV record."""

    return find_guide_frame_fields(line) is not None


def metadata_lines(lines: Iterable[str]) -> list[str]:
    """
    Remove guide-frame rows before reading configuration metadata.

    This prevents the Mount field inside a guide-frame record from being
    mistaken for the actual mount configuration field.
    """

    return [
        line
        for line in lines
        if not is_guide_frame(line)
    ]


def last_match(
    pattern: re.Pattern[str],
    lines: Iterable[str],
    group_name: str = "value",
) -> str | None:
    """Return the last named regular-expression match."""

    result: str | None = None

    for line in lines:
        match = pattern.search(line)

        if match:
            result = match.group(group_name).strip()

    return result


def last_float(
    pattern: re.Pattern[str],
    lines: Iterable[str],
    group_name: str = "value",
) -> float | None:
    """Return the last numeric regular-expression match."""

    value = last_match(
        pattern,
        lines,
        group_name,
    )

    if value is None:
        return None

    try:
        return float(value)
    except ValueError:
        return None


def normalize_timestamp(value: str | None) -> str | None:
    """Remove harmless delimiters around PHD2 timestamp values."""

    if value is None:
        return None

    cleaned = value.strip(" ,#")

    return cleaned or None


def parse_full_log_configuration(
    lines: list[str],
) -> SessionConfiguration:
    """Extract the latest available configuration from the complete log."""

    metadata = metadata_lines(lines)
    configuration = SessionConfiguration()

    configuration.start_timestamp = normalize_timestamp(
        last_match(
            GUIDING_BEGINS_RE,
            metadata,
            "timestamp",
        )
    )

    configuration.end_timestamp = normalize_timestamp(
        last_match(
            GUIDING_ENDS_RE,
            metadata,
            "timestamp",
        )
    )

    configuration.equipment_profile = last_match(
        PROFILE_RE,
        metadata,
    )

    configuration.mount = last_match(
        MOUNT_RE,
        metadata,
    )

    configuration.guide_camera = last_match(
        CAMERA_RE,
        metadata,
    )

    exposure_match: re.Match[str] | None = None

    for line in metadata:
        match = GUIDE_EXPOSURE_RE.search(line)

        if match:
            exposure_match = match

    if exposure_match:
        exposure_value = float(
            exposure_match.group("value")
        )

        exposure_unit = exposure_match.group(
            "unit"
        ).lower()

        if exposure_unit in {
            "s",
            "sec",
            "second",
            "seconds",
        }:
            configuration.guide_exposure_ms = (
                exposure_value * 1000.0
            )
        else:
            configuration.guide_exposure_ms = exposure_value

    configuration.guide_focal_length_mm = last_float(
        FOCAL_LENGTH_RE,
        metadata,
    )

    configuration.pixel_scale_arcsec_px = last_float(
        PIXEL_SCALE_RE,
        metadata,
    )

    combined_dither_match: re.Match[str] | None = None

    for line in metadata:
        match = DITHER_CONFIGURATION_RE.search(line)

        if match:
            combined_dither_match = match

    if combined_dither_match:
        configuration.dither_axes = (
            combined_dither_match.group("axes").strip()
        )

        configuration.dither_scale = float(
            combined_dither_match.group("scale")
        )
    else:
        configuration.dither_axes = last_match(
            DITHER_AXES_RE,
            metadata,
        )

        configuration.dither_scale = last_float(
            DITHER_SCALE_RE,
            metadata,
        )

    configuration.ra_algorithm = last_match(
        RA_ALGORITHM_RE,
        metadata,
    )

    configuration.ra_hysteresis = last_float(
        RA_HYSTERESIS_RE,
        metadata,
    )

    configuration.ra_aggression = last_float(
        RA_AGGRESSION_RE,
        metadata,
    )

    configuration.ra_min_move_px = last_float(
        RA_MIN_MOVE_RE,
        metadata,
    )

    configuration.dec_algorithm = last_match(
        DEC_ALGORITHM_RE,
        metadata,
    )

    configuration.dec_aggression = last_float(
        DEC_AGGRESSION_RE,
        metadata,
    )

    configuration.dec_min_move_px = last_float(
        DEC_MIN_MOVE_RE,
        metadata,
    )

    configuration.dec_backlash_compensation = last_match(
        BACKLASH_RE,
        metadata,
    )

    return configuration


def parse_guide_frame(line: str) -> GuideFrame | None:
    """
    Parse a PHD2 guide frame including RA/DEC errors and SNR.

    PHD2 normally stores star mass, SNR, and error code in fields 15, 16,
    and 17. Missing optional values are returned as None.
    """

    fields = find_guide_frame_fields(line)

    if fields is None:
        return None

    return GuideFrame(
        elapsed_seconds=float(fields[1]),
        raw_ra_error_px=float(fields[5]),
        raw_dec_error_px=float(fields[6]),
        star_mass=(
            parse_optional_float(fields[15])
            if len(fields) > 15
            else None
        ),
        snr=(
            parse_optional_float(fields[16])
            if len(fields) > 16
            else None
        ),
        error_code=(
            parse_optional_int(fields[17])
            if len(fields) > 17
            else None
        ),
    )


def classify_sky_condition(
    snr_values: list[float],
    drop_frames: int,
    star_lost_events: int,
) -> tuple[str, float | None]:
    """
    Classify possible guide-star attenuation during a dither.

    The labels are indicators only. Low SNR may be caused by clouds, haze,
    dew, focus change, seeing, star contamination, or other conditions.
    """

    if star_lost_events > 0:
        return "star lost", None

    if drop_frames > 0:
        return "likely cloud", None

    if len(snr_values) < 2:
        return "insufficient data", None

    initial_snr = snr_values[0]
    minimum_snr = min(snr_values)

    if initial_snr <= 0:
        return "insufficient data", None

    snr_drop_percent = (
        100.0
        * (initial_snr - minimum_snr)
        / initial_snr
    )

    if snr_drop_percent >= 50.0:
        return "likely cloud", snr_drop_percent

    if snr_drop_percent >= 30.0:
        return "possible cloud", snr_drop_percent

    return "clear", snr_drop_percent


def analyze_all_dithers(
    lines: list[str],
    configuration: SessionConfiguration,
) -> list[DitherEvent]:
    """
    Analyze every dither command found in the selected guide log.

    A dither block ends at Settling complete, Settling failed, the next
    dither command, or the end of the file.
    """

    dithers: list[DitherEvent] = []

    for line_index, line in enumerate(lines):
        dither_match = DITHER_RE.search(line)

        if dither_match is None:
            continue

        dx_px = float(dither_match.group("dx"))
        dy_px = float(dither_match.group("dy"))

        frames: list[GuideFrame] = []
        drop_frames = 0
        star_lost_events = 0
        status = "no result"

        for next_line in lines[line_index + 1:]:
            if DITHER_RE.search(next_line):
                break

            frame = parse_guide_frame(next_line)

            if frame is not None:
                frames.append(frame)

            if next_line.lstrip().upper().startswith("DROP"):
                drop_frames += 1

            if STAR_LOST_RE.search(next_line):
                star_lost_events += 1

            if SETTLING_COMPLETE_RE.search(next_line):
                status = "complete"
                break

            if SETTLING_FAILED_RE.search(next_line):
                status = "failed"
                break

        first_frame = frames[0] if frames else None
        final_frame = frames[-1] if frames else None

        snr_values = [
            frame.snr
            for frame in frames
            if frame.snr is not None
        ]

        cloud_indicator, snr_drop_percent = classify_sky_condition(
            snr_values,
            drop_frames,
            star_lost_events,
        )

        magnitude_px = math.hypot(dx_px, dy_px)

        magnitude_arcsec: float | None = None

        if configuration.pixel_scale_arcsec_px is not None:
            magnitude_arcsec = (
                magnitude_px
                * configuration.pixel_scale_arcsec_px
            )

        settle_time_s: float | None = None

        if first_frame is not None and final_frame is not None:
            settle_time_s = (
                final_frame.elapsed_seconds
                - first_frame.elapsed_seconds
            )

        final_total_error_px: float | None = None

        if final_frame is not None:
            final_total_error_px = math.hypot(
                final_frame.raw_ra_error_px,
                final_frame.raw_dec_error_px,
            )

        dithers.append(
            DitherEvent(
                number=len(dithers) + 1,
                line_number=line_index + 1,
                dx_px=dx_px,
                dy_px=dy_px,
                magnitude_px=magnitude_px,
                magnitude_arcsec=magnitude_arcsec,
                status=status,
                start_time_s=(
                    first_frame.elapsed_seconds
                    if first_frame
                    else None
                ),
                end_time_s=(
                    final_frame.elapsed_seconds
                    if final_frame
                    else None
                ),
                settle_time_s=settle_time_s,
                final_ra_error_px=(
                    final_frame.raw_ra_error_px
                    if final_frame
                    else None
                ),
                final_dec_error_px=(
                    final_frame.raw_dec_error_px
                    if final_frame
                    else None
                ),
                final_total_error_px=final_total_error_px,
                initial_snr=(
                    snr_values[0]
                    if snr_values
                    else None
                ),
                mean_snr=(
                    statistics.mean(snr_values)
                    if snr_values
                    else None
                ),
                minimum_snr=(
                    min(snr_values)
                    if snr_values
                    else None
                ),
                final_snr=(
                    snr_values[-1]
                    if snr_values
                    else None
                ),
                snr_drop_percent=snr_drop_percent,
                drop_frames=drop_frames,
                star_lost_events=star_lost_events,
                cloud_indicator=cloud_indicator,
            )
        )

    return dithers


def infer_timeout_candidate(
    dithers: list[DitherEvent],
) -> float | None:
    """
    Estimate a settle timeout using the median duration of failed dithers.

    This is a diagnostic estimate and not a direct reading of the SharpCap
    or PHD2 configuration.
    """

    failed_durations = [
        item.settle_time_s
        for item in dithers
        if item.status == "failed"
        and item.settle_time_s is not None
    ]

    if len(failed_durations) < 3:
        return None

    return statistics.median(failed_durations)


def mark_timeout_suspects(
    dithers: list[DitherEvent],
    timeout_candidate_s: float | None,
) -> None:
    """Mark failed dithers ending close to the inferred timeout."""

    if timeout_candidate_s is None:
        return

    tolerance_s = max(timeout_candidate_s * 0.10, 2.0)

    for dither in dithers:
        if (
            dither.status == "failed"
            and dither.settle_time_s is not None
            and abs(dither.settle_time_s - timeout_candidate_s)
            <= tolerance_s
        ):
            dither.timeout_suspected = True


def format_value(
    value: float | None,
    decimal_places: int = 2,
) -> str:
    """Format a numeric value or use a dash for missing data."""

    if value is None:
        return "—"

    return f"{value:.{decimal_places}f}"


def mean(values: list[float]) -> float | None:
    """Return arithmetic mean or None for an empty sequence."""

    if not values:
        return None

    return sum(values) / len(values)


def make_recommendations(
    dithers: list[DitherEvent],
    timeout_candidate_s: float | None,
) -> list[str]:
    """Build conservative recommendations from settling outcomes."""

    if not dithers:
        return [
            "No recognized PHD2 dither commands were found in the "
            "selected guide log."
        ]

    completed = [
        item
        for item in dithers
        if item.status == "complete"
    ]

    failed = [
        item
        for item in dithers
        if item.status == "failed"
    ]

    no_result = [
        item
        for item in dithers
        if item.status == "no result"
    ]

    likely_cloud = [
        item
        for item in dithers
        if item.cloud_indicator in {
            "likely cloud",
            "star lost",
        }
    ]

    timeout_suspects = [
        item
        for item in failed
        if item.timeout_suspected
    ]

    recommendations = [
        f"Detected dithers: {len(dithers)}. "
        f"Complete: {len(completed)}. "
        f"Failed: {len(failed)}. "
        f"No result: {len(no_result)}."
    ]

    if failed:
        failed_percent = (
            100.0 * len(failed) / len(dithers)
        )

        recommendations.append(
            f"Settling failure rate: {failed_percent:.1f}%."
        )

    if timeout_candidate_s is not None:
        recommendations.append(
            "Inferred settling timeout candidate from failed dithers: "
            f"{timeout_candidate_s:.1f} seconds."
        )

    if timeout_suspects:
        recommendations.append(
            f"{len(timeout_suspects)} failed dithers ended near the "
            "inferred timeout. This pattern is consistent with the "
            "requesting application reaching its settling timeout rather "
            "than an immediate guide-star loss."
        )

    if likely_cloud:
        recommendations.append(
            f"{len(likely_cloud)} dithers show likely cloud or guide-star "
            "loss indicators. Inspect SNR, DROP counts, and star-loss "
            "messages before attributing those cases to mount mechanics."
        )

    if failed:
        recommendations.append(
            "Before increasing aggression or changing guide algorithms, "
            "check DEC balance, backlash, mechanical stiction, cable drag, "
            "and PHD2 settling parameters."
        )

    successful_settle_times = [
        item.settle_time_s
        for item in completed
        if item.settle_time_s is not None
    ]

    average_settle_time = mean(successful_settle_times)

    if average_settle_time is not None:
        recommendations.append(
            "Mean estimated settling interval for completed dithers: "
            f"{average_settle_time:.1f} seconds."
        )

    return recommendations


def make_report(
    log_path: Path,
    configuration: SessionConfiguration,
    dithers: list[DitherEvent],
    timeout_candidate_s: float | None,
) -> str:
    """Build the plain-text PHD2 dither analysis report."""

    lines: list[str] = []

    lines.append(
        f"PHD2 DITHER ANALYSIS — {log_path.name}"
    )
    lines.append("=" * 148)
    lines.append("")

    lines.append("LAST GUIDING SESSION CONFIGURATION")
    lines.append("-" * 148)

    configuration_rows = [
        ("Guiding begins", configuration.start_timestamp),
        ("Guiding ends", configuration.end_timestamp),
        ("Equipment profile", configuration.equipment_profile),
        ("Mount", configuration.mount),
        ("Guide camera", configuration.guide_camera),
        (
            "Guide exposure",
            (
                f"{format_value(configuration.guide_exposure_ms, 0)} ms"
                if configuration.guide_exposure_ms is not None
                else None
            ),
        ),
        (
            "Guide focal length",
            (
                f"{format_value(configuration.guide_focal_length_mm, 0)} mm"
                if configuration.guide_focal_length_mm is not None
                else None
            ),
        ),
        (
            "Guide pixel scale",
            (
                f"{format_value(configuration.pixel_scale_arcsec_px)} "
                "arcsec/px"
                if configuration.pixel_scale_arcsec_px is not None
                else None
            ),
        ),
        ("Dither axes", configuration.dither_axes),
        (
            "Dither scale",
            (
                format_value(configuration.dither_scale)
                if configuration.dither_scale is not None
                else "—"
            ),
        ),
        ("RA algorithm", configuration.ra_algorithm),
        (
            "RA hysteresis",
            format_value(configuration.ra_hysteresis),
        ),
        (
            "RA aggression",
            format_value(configuration.ra_aggression),
        ),
        (
            "RA min-move",
            (
                f"{format_value(configuration.ra_min_move_px, 3)} px"
                if configuration.ra_min_move_px is not None
                else "—"
            ),
        ),
        ("DEC algorithm", configuration.dec_algorithm),
        (
            "DEC aggression",
            format_value(configuration.dec_aggression),
        ),
        (
            "DEC min-move",
            (
                f"{format_value(configuration.dec_min_move_px, 3)} px"
                if configuration.dec_min_move_px is not None
                else "—"
            ),
        ),
        (
            "DEC backlash compensation",
            configuration.dec_backlash_compensation,
        ),
    ]

    for label, value in configuration_rows:
        display_value = value or "not found"
        lines.append(f"{label:28}: {display_value}")

    complete_count = sum(
        item.status == "complete"
        for item in dithers
    )

    failed_count = sum(
        item.status == "failed"
        for item in dithers
    )

    no_result_count = sum(
        item.status == "no result"
        for item in dithers
    )

    lines.append("")
    lines.append("DITHER SUMMARY")
    lines.append("-" * 148)
    lines.append(f"Total dithers: {len(dithers)}")
    lines.append(
        f"Complete: {complete_count} | "
        f"Failed: {failed_count} | "
        f"No result: {no_result_count}"
    )

    if timeout_candidate_s is not None:
        lines.append(
            "Inferred settling timeout candidate: "
            f"{timeout_candidate_s:.1f} seconds"
        )

    lines.append("")

    header = (
        f"{'#':>3} "
        f"{'dx(px)':>8} "
        f"{'dy(px)':>8} "
        f"{'mod(px)':>8} "
        f"{'arcsec':>8} "
        f"{'settle(s)':>9} "
        f"{'RA err':>8} "
        f"{'DEC err':>8} "
        f"{'final err':>9} "
        f"{'mean SNR':>9} "
        f"{'min SNR':>8} "
        f"{'drops':>6} "
        f"{'sky condition':>17} "
        f"{'timeout':>8} "
        f"status"
    )

    lines.append(header)
    lines.append("-" * len(header))

    for item in dithers:
        timeout_text = "yes" if item.timeout_suspected else "no"

        lines.append(
            f"{item.number:>3} "
            f"{item.dx_px:>8.3f} "
            f"{item.dy_px:>8.3f} "
            f"{item.magnitude_px:>8.3f} "
            f"{format_value(item.magnitude_arcsec):>8} "
            f"{format_value(item.settle_time_s, 1):>9} "
            f"{format_value(item.final_ra_error_px, 3):>8} "
            f"{format_value(item.final_dec_error_px, 3):>8} "
            f"{format_value(item.final_total_error_px, 3):>9} "
            f"{format_value(item.mean_snr, 1):>9} "
            f"{format_value(item.minimum_snr, 1):>8} "
            f"{item.drop_frames:>6} "
            f"{item.cloud_indicator:>17} "
            f"{timeout_text:>8} "
            f"{item.status}"
        )

    lines.append("")
    lines.append("CONCLUSIONS AND RECOMMENDATIONS")
    lines.append("-" * 148)

    for recommendation in make_recommendations(
        dithers,
        timeout_candidate_s,
    ):
        lines.append(f"- {recommendation}")

    lines.append("")
    lines.append("NOTES")
    lines.append("-" * 148)
    lines.append(
        "- Dither commands are extracted from INFO DITHER by or "
        "INFO: DITHER by log messages."
    )
    lines.append(
        "- Settling outcomes are extracted from INFO SETTLING STATE CHANGE "
        "or INFO: SETTLING STATE CHANGE log messages."
    )
    lines.append(
        "- The timeout candidate is the median settling interval of failed "
        "dithers and is not a direct reading of an application setting."
    )
    lines.append(
        "- Cloud labels are indicators based on SNR changes, dropped frames, "
        "and guide-star loss messages; they do not prove cloud coverage."
    )
    lines.append(
        "- Final errors are raw RA and DEC values from the last guide frame "
        "before the settling outcome."
    )

    return "\n".join(lines) + "\n"


def write_csv(
    dithers: list[DitherEvent],
    csv_path: Path,
) -> None:
    """Write dither events and telemetry summaries to a UTF-8 CSV file."""

    fieldnames = list(
        DitherEvent.__dataclass_fields__.keys()
    )

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for item in dithers:
            writer.writerow(asdict(item))


def choose_latest_useful_log(logs: list[Path]) -> Path:
    """Select the newest guide log containing guiding or dither evidence."""

    for candidate in reversed(logs):
        candidate_lines = candidate.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()

        has_guiding = any(
            GUIDING_BEGINS_RE.search(line)
            for line in candidate_lines
        )

        has_dither = any(
            DITHER_RE.search(line)
            for line in candidate_lines
        )

        if has_guiding or has_dither:
            return candidate

    return logs[-1]


def print_dither_diagnostics(
    lines: list[str],
) -> None:
    """Print dither and settling parser diagnostics."""

    dither_lines = [
        line
        for line in lines
        if "DITHER" in line.upper()
    ]

    dither_matches = [
        line
        for line in lines
        if DITHER_RE.search(line)
    ]

    settling_lines = [
        line
        for line in lines
        if "SETTLING STATE CHANGE" in line.upper()
    ]

    print(
        f"Diagnostic: lines containing DITHER: "
        f"{len(dither_lines)}"
    )

    print(
        f"Diagnostic: recognized PHD2 dither commands: "
        f"{len(dither_matches)}"
    )

    print(
        f"Diagnostic: settling state-change lines: "
        f"{len(settling_lines)}"
    )


def print_frame_diagnostics(
    lines: list[str],
) -> None:
    """Print PHD2 guide-frame parser diagnostics."""

    candidate_lines = [
        line
        for line in lines
        if re.match(r"^\s*\d+[,;]", line)
    ]

    frame_lines = [
        line
        for line in lines
        if is_guide_frame(line)
    ]

    parsed_frames = [
        parse_guide_frame(line)
        for line in frame_lines
    ]

    valid_frames = [
        frame
        for frame in parsed_frames
        if frame is not None
    ]

    print(
        f"Diagnostic: numeric CSV candidate lines: "
        f"{len(candidate_lines)}"
    )

    print(
        f"Diagnostic: recognized PHD2 guide-frame lines: "
        f"{len(frame_lines)}"
    )

    print(
        f"Diagnostic: parseable guide frames: "
        f"{len(valid_frames)}"
    )

    if valid_frames:
        first_frame = valid_frames[0]

        print(
            "Diagnostic: first parsed guide frame: "
            f"time={first_frame.elapsed_seconds:.3f}s, "
            f"raw_ra={first_frame.raw_ra_error_px:.3f}px, "
            f"raw_dec={first_frame.raw_dec_error_px:.3f}px, "
            f"snr={format_value(first_frame.snr, 2)}"
        )


def parse_arguments() -> Path:
    """Return the properties-file path from command-line arguments."""

    if len(sys.argv) == 1:
        return DEFAULT_PROPERTIES

    if len(sys.argv) == 2:
        return Path(sys.argv[1]).expanduser()

    raise SystemExit(
        "Usage: python phd2_dither_analyzer.py "
        "[phd2_dither_analyzer.properties]"
    )


def main() -> int:
    """Run PHD2 dither, settling, and sky-condition analysis."""

    properties_path = parse_arguments()

    if not properties_path.is_file():
        print(
            f"Properties file not found: {properties_path}"
        )
        return 2

    properties = read_properties(properties_path)

    log_source = properties.get("phd2.logs.path")

    if not log_source:
        print(
            "Missing required property: phd2.logs.path"
        )
        return 2

    logs = discover_logs(log_source)

    if not logs:
        print(
            f"No PHD2 guide logs found for: {log_source}"
        )
        return 2

    selected_log = choose_latest_useful_log(logs)

    log_lines = selected_log.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    print_dither_diagnostics(log_lines)
    print_frame_diagnostics(log_lines)

    configuration = parse_full_log_configuration(
        log_lines
    )

    dithers = analyze_all_dithers(
        log_lines,
        configuration,
    )

    timeout_candidate_s = infer_timeout_candidate(dithers)

    mark_timeout_suspects(
        dithers,
        timeout_candidate_s,
    )

    report = make_report(
        selected_log,
        configuration,
        dithers,
        timeout_candidate_s,
    )

    REPORTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_path = (
        REPORTS_DIR
        / f"{selected_log.stem}_dither_report.txt"
    )

    csv_path = (
        REPORTS_DIR
        / f"{selected_log.stem}_dithers.csv"
    )

    report_path.write_text(
        report,
        encoding="utf-8",
    )

    write_csv(
        dithers,
        csv_path,
    )

    print("")
    print(report)
    print(f"Report saved: {report_path}")
    print(f"CSV saved:    {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())