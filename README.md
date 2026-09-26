# PHD2 Session Analyzer

A lightweight Python tool for analysing PHD2 guide logs, initially focused on dither behaviour, settling performance, and guiding-session diagnostics.

The tool reads the latest PHD2 guide log from a configurable path, extracts information from the last guiding session, generates a dither summary table, exports data to CSV, and produces an initial technical conclusion with proposed improvements when applicable.

## Features

- Finds and analyses the last guiding session in a PHD2 guide log.
- Supports a single guide-log file, a folder of logs, or a glob pattern.
- Selects the most recently modified matching log when the input path contains multiple logs.
- Extracts relevant session configuration when present in the log:
  - Equipment profile.
  - Guide camera.
  - Guide exposure.
  - Guide scope focal length.
  - Guide pixel scale.
  - Mount.
  - Dither axes and scale.
  - RA and DEC guiding algorithms.
  - RA and DEC aggressiveness.
  - RA and DEC minimum movement values.
  - DEC backlash-compensation status.
- Detects dither commands from PHD2 logs.
- Calculates dither vectors and amplitudes in guide-camera pixels.
- Converts dither amplitude to arcseconds when the guide scale is available.
- Identifies settle outcomes:
  - `complete`
  - `failed`
  - `no result`
- Estimates settling duration from PHD2 guide-frame timestamps.
- Creates:
  - A human-readable text report.
  - A CSV file with dither data.
- Generates preliminary recommendations based on the dither and settling results.

## Requirements

- Python 3.10 or later.
- No external Python dependencies.

## Repository layout

```text
phd2-session-analyzer/
├── README.md
├── LICENSE
├── .gitignore
├── phd2_dither_analyzer.py
└── phd2_dither_analyzer.properties.example
```

## Installation

Clone the repository:

```bash
git clone [https://github.com/davidglt/phd2-session-analyzer.git](https://github.com/davidglt/phd2-session-analyzer.git)
cd phd2-session-analyzer
```

No package installation is required.

## Configuration

Copy the example configuration file:

### Windows

```bat
copy phd2_dither_analyzer.properties.example phd2_dither_analyzer.properties
```

### Linux or macOS

```bash
cp phd2_dither_analyzer.properties.example phd2_dither_analyzer.properties
```

Edit `phd2_dither_analyzer.properties` and configure the following values:

```properties
# A single PHD2 guide log, a directory containing guide logs,
# or a glob pattern.
phd2.logs.path=C:/Users/YourUser/Documents/PHD2/Guidelogs

# Directory where reports and CSV files will be saved.
output.directory=C:/Users/YourUser/Documents/PHD2/Guidelogs/analysis
```

The local configuration file is intentionally ignored by Git:

```text
phd2_dither_analyzer.properties
```

Only the example file is versioned:

```text
phd2_dither_analyzer.properties.example
```

### Supported log paths

The `phd2.logs.path` property accepts:

```properties
# Directory containing PHD2 guide logs
phd2.logs.path=C:/Users/YourUser/Documents/PHD2/Guidelogs

# One specific PHD2 guide log
phd2.logs.path=C:/Users/YourUser/Documents/PHD2/Guidelogs/PHD2_GuideLog_2026-09-26.txt

# Glob pattern
phd2.logs.path=C:/Users/YourUser/Documents/PHD2/Guidelogs/*GuideLog*.txt
```

When more than one log is found, the analyzer processes the most recently modified file.

## Usage

Run the analyzer with the local configuration file:

```bash
python phd2_dither_analyzer.py phd2_dither_analyzer.properties
```

Example on Windows:

```bat
python .\phd2_dither_analyzer.py .\phd2_dither_analyzer.properties
```

Example with an activated virtual environment:

```bat
.venv\Scripts\activate
python .\phd2_dither_analyzer.py .\phd2_dither_analyzer.properties
```

## Output

The script prints a report to the terminal and writes two files to the configured `output.directory`:

```text
<guide-log-name>_dither_report.txt
<guide-log-name>_dithers.csv
```

### Text report

The text report includes:

1. Configuration detected in the last PHD2 guiding session.
2. Dither count and settle-result summary.
3. A table of dither vectors, amplitudes, settling times, residual errors, and outcomes.
4. An automated conclusion and suggested improvements.

Example structure:

```text
PHD2 DITHER ANALYSIS — PHD2_GuideLog_2026-09-26.txt

LAST GUIDING SESSION CONFIGURATION
------------------------------------------------------------------------------
Equipment profile           : SkyWatcher 50ED
Guide camera                : ZWO ASI224MC
Guide exposure              : 2000 ms
Guide focal length          : 242 mm
Guide pixel scale           : 3.20 arcsec/px
Dither axes                 : both axes
Dither scale                : 1.00
RA algorithm                : Hysteresis
RA aggression               : 0.70
RA min-move                 : 0.200 px
DEC algorithm               : Resist Switch
DEC aggression              : 85.00
DEC min-move                : 0.300 px
DEC backlash compensation   : disabled

DITHER SUMMARY
------------------------------------------------------------------------------
Total dithers: 23
Complete: 21 | Failed: 2 | No result: 0

  #   dx(px)   dy(px)  mod(px)  mod(arcsec)  settle(s)  status
----------------------------------------------------------------
  1   -3.990    0.509    4.022        12.87        8.8  complete
  2   -2.454    2.470    3.482        11.14       13.0  complete
  3   -1.198    3.168    3.387        10.84       59.7  failed
```

### CSV output

The CSV output is designed for further analysis in:

- LibreOffice Calc.
- Microsoft Excel.
- Python and pandas.
- R.
- Grafana or custom observatory-monitoring workflows.

Typical columns include:

```text
number
dx_px
dy_px
magnitude_px
magnitude_arcsec
status
settle_seconds
```

## Interpretation

The analyzer provides diagnostic evidence, not absolute decisions. It is useful for detecting patterns such as:

- Dithers that systematically fail to settle.
- Excessive dither amplitudes for the guide scale.
- Long settling times.
- Problems occurring predominantly on the DEC axis.
- Possible cable drag, stiction, backlash, or poor DEC balance.
- Inconsistent guiding behavior after dither movements.

A `Settling failed` event does not automatically mean that the subsequent light frame is unusable. It should be correlated with star shape, guiding RMS, the PHD2 graph, and the state of the mount after the event.

## Recommendations generated by the tool

When dither settling fails, the report may recommend actions such as:

- Run PHD2 Guiding Assistant.
- Check DEC balance, friction, stiction, and cable routing.
- Recalibrate after substantial mechanical changes.
- Review whether DEC backlash compensation is appropriate.
- Reduce dither scale moderately.
- Increase settle timeout while investigating the root cause.
- Keep `Resist Switch` as an initial DEC strategy when backlash is present, unless measured data suggests otherwise.

The tool does not change PHD2 settings. It reports evidence from the log and proposes items to validate.

## Notes and limitations

- The current version estimates settle time from guide-frame timestamps. It does not replace PHD2's internal settling logic.
- PHD2 log formats can vary by version and platform. If a configuration field is absent or has a different format, it may be shown as `not found`.
- The analyzer currently focuses on the last guiding session contained in the selected log.
- Large logs can contain more than one guiding session due to reconnections, recalibration, meridian flips, or restarts. The last session is selected intentionally.
- Do not commit real logs or local configuration files unless you explicitly intend to publish them.

## Planned improvements

Potential future additions include:

- Full-session RA and DEC RMS statistics.
- Guiding-error charts.
- Per-axis dither-settling diagnostics.
- Analysis of calibration quality and orthogonality.
- Automatic detection of backlash signatures.
- Correlation of guiding behavior with meridian flips.
- Multiple-session comparison.
- JSON output.
- HTML reports and plots.
- Integration with N.I.N.A., SharpCap, or observatory telemetry logs.

## Contributing

Issues, suggestions, sample anonymized PHD2 logs, and pull requests are welcome.

When reporting a parsing problem, include:

- PHD2 version.
- Operating system.
- A small anonymized section of the guide log.
- Expected and actual behavior.

## License

This project is licensed under the GNU General Public License v3.0 or later.

See the [LICENSE](LICENSE) file for the full license text.

Copyright © 2026 David González López-Tercero.