# midas_mailin

MIDAS calibration + integration for mail-in XRD frames, maintained
independently of any acquisition program.

**How it fits together.** The mail-in acquisition program does *not* import
this package. It launches it as a separate process, using the Python
interpreter of this package's **own conda environment**, and hands it explicit
file lists on the command line. That keeps the heavy MIDAS stack (torch,
`midas-suite`, ...) out of the acquisition program's environment. The only
coupling between the two is:

```
mail-in program --(command line + YAML config)--> midas_mailin
mail-in program <--(output files + run_summary.json + exit code)-- midas_mailin
```

It is the mail-in developers' job to decide *which* files are the calibrant
and *which* are to be integrated, and to pass them in.

Contents: [1. Get the package](#1-get-the-package-onto-the-machine) -
[2. Create the environment](#2-create-the-midas_mailin-environment) -
[3. Test it](#3-test-it-by-hand-before-touching-any-code) -
[4. Add it to the mail-in program](#4-add-it-to-the-mail-in-program) -
[5. Reference](#5-reference-inputs-outputs-exit-codes-config)

---

## 1. Get the package onto the machine

Put this folder anywhere **outside** the mail-in program's repo, on the same
machine that runs the mail-in program (the beamline control PC). Example
location used below: `C:\Users\17bmuser\midas_mailin` (Windows) or
`~/midas_mailin` (Linux/macOS).

```bash
git clone <URL-of-the-midas_mailin-repository> C:\Users\17bmuser\midas_mailin
# or, with no git access: copy the whole folder over (it must contain
# pyproject.toml, environment.yml, the midas_mailin/ folder and configs/)
```

To update later: `git -C C:\Users\17bmuser\midas_mailin pull`. Because the
package is installed in *editable* mode (next step), a pull takes effect
immediately -- nothing needs reinstalling unless `environment.yml` changed.

## 2. Create the `midas_mailin` environment

This is a **dedicated** conda environment. Do **not** install it into, or
merge it with, the mail-in program's environment.

```bash
cd C:\Users\17bmuser\midas_mailin
conda env create -f environment.yml      # creates env "midas_mailin" and installs this package into it (-e .)
```

This pulls `torch` and `midas-suite`, so it takes a while and needs internet
access. Run it from inside the package folder (the last line of
`environment.yml`, `-e .`, installs the package from the current directory).

Without conda, in any Python 3.12 virtualenv:

```bash
cd C:\Users\17bmuser\midas_mailin
pip install -e .
```

Then find the interpreter path -- **this path is the one thing the mail-in
program needs**:

```bash
conda activate midas_mailin
python -c "import sys; print(sys.executable)"
# e.g. C:\Users\17bmuser\AppData\Local\miniconda3\envs\midas_mailin\python.exe
```

Updating the environment later (e.g. after `environment.yml` changes):

```bash
conda env update -f environment.yml --prune
```

## 3. Test it by hand before touching any code

Run it once from a terminal, **using the interpreter path from step 2 -- not
from an activated mail-in environment**, to prove the hand-off will work:

```bash
C:\Users\17bmuser\AppData\Local\miniconda3\envs\midas_mailin\python.exe -m midas_mailin ^
    --calib  Y:\mail_in\2026\Nov\ANLXRD00012_pos0.tif ^
    --integ  Y:\mail_in\2026\Nov\ANLXRD00012_pos1.tif Y:\mail_in\2026\Nov\ANLXRD00012_pos2.tif ^
    --outdir Y:\mail_in\2026\Nov ^
    --config C:\Users\17bmuser\midas_mailin\configs\beamline_17bm.yaml
echo exit code: %ERRORLEVEL%
```

(`^` is the Windows line continuation; use `\` on Linux/macOS.) Expect exit
code `0`, `<stem>_calib.*` / `<stem>_integ.*` files in `--outdir`, and a
`ANLXRD00012_pos0_run_summary.json`. The calibrant's `.metadata` sidecar must
sit next to the calibrant `.tif` -- wavelength and pixel pitch are read from it.

## 4. Add it to the mail-in program

Three things to configure, and one function to add. The mail-in program needs
**no** MIDAS packages for any of this -- only the standard library.

### 4.1 Settings (two paths)

Near the top of the module that launches analysis (in this repo, `mailin.py`):

```python
import os
import re
import subprocess
from pathlib import Path

# Interpreter of the dedicated midas_mailin conda env (step 2) and the
# beamline YAML config. Environment variables take precedence, so a
# deployment can change them without editing code.
MIDAS_MAILIN_PYTHON = os.environ.get(
    "MIDAS_MAILIN_PYTHON",
    r"C:\Users\17bmuser\AppData\Local\miniconda3\envs\midas_mailin\python.exe")
MIDAS_MAILIN_CONFIG = os.environ.get(
    "MIDAS_MAILIN_CONFIG",
    r"C:\Users\17bmuser\midas_mailin\configs\beamline_17bm.yaml")
```

### 4.2 The launch function

This is self-contained. It takes the calibrant frame, the frames to
integrate, and an output directory, and starts `midas_mailin` as a detached
background process. It returns immediately and **never raises** into the
acquisition flow -- a failure only means analysis did not start, not that data
collection is affected.

```python
def launch_midas_mailin(calib_tif, integ_tifs, outdir, log_path=None):
    """Start midas_mailin (calibrate calib_tif, integrate integ_tifs) in a
    detached background process.

    calib_tif  : the ONE calibrant frame (.tif; its .metadata sidecar must be
                 next to it).
    integ_tifs : iterable of frames to integrate (may be empty -- the calibrant
                 is always integrated too).
    outdir     : directory for all outputs (created if missing). Input file
                 stems must be unique within one call.
    log_path   : where the child's stdout/stderr go (default:
                 <outdir>/<calib stem>_midas_mailin.log).

    Returns the Popen object on success, or None if launching failed.
    """
    try:
        calib_tif = Path(calib_tif)
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        log_path = Path(log_path) if log_path else outdir / f"{calib_tif.stem}_midas_mailin.log"

        # Pass the frame list as a file, not as argv: a batch can hold many
        # frames and Windows caps the command-line length.
        integ_list = outdir / f"{calib_tif.stem}_integ_list.txt"
        integ_list.write_text(
            "\n".join(str(Path(p)) for p in integ_tifs) + "\n", encoding="utf-8")

        cmd = [
            MIDAS_MAILIN_PYTHON, "-m", "midas_mailin",
            "--calib", str(calib_tif),
            "--integ-list", str(integ_list),
            "--outdir", str(outdir),
            "--config", MIDAS_MAILIN_CONFIG,
        ]

        # Force UTF-8 in the child: with stdout redirected to a file, Windows
        # Python otherwise uses the system code page and MIDAS's own prints of
        # non-Latin-1 symbols crash the run with UnicodeEncodeError.
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        popen_kwargs = dict(cwd=str(outdir), env=env)
        if os.name == "nt":
            popen_kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            popen_kwargs["start_new_session"] = True

        with open(log_path, "a", encoding="utf-8") as logf:
            return subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, **popen_kwargs)
    except Exception as exc:
        print(f"[WARN] Failed to launch midas_mailin: {exc}")
        return None
```

### 4.3 Call it when a batch finishes acquiring

You decide which frame is the calibrant and which are samples. In this repo's
convention a batch is `<barcode>_pos0.tif` (LaB6 calibrant) plus
`<barcode>_pos1.tif`, `<barcode>_pos2.tif`, ... (samples):

```python
def on_batch_finished(barcode, batch_dir):
    batch_dir = Path(batch_dir)
    pos_re = re.compile(rf"^{re.escape(barcode)}_pos(\d+)\.tif$")

    frames = {}
    for tif in batch_dir.glob(f"{barcode}_pos*.tif"):
        m = pos_re.match(tif.name)
        if m:
            frames[int(m.group(1))] = tif

    calib = frames.pop(0, None)
    if calib is None:
        print(f"[WARN] no calibrant frame for {barcode}; analysis not started")
        return
    samples = [frames[k] for k in sorted(frames)]      # numeric order

    launch_midas_mailin(calib, samples, outdir=batch_dir)
```

Any other rule works too (a different calibrant file name, a calibrant from an
earlier batch, outputs in a separate folder) -- `midas_mailin` only needs
the five inputs in section 5.

### 4.4 Optional: check how the run went

The launch is fire-and-forget. To find out whether it succeeded, read the
summary file `midas_mailin` writes when it finishes (it does not exist until
then; while running, the log file shows progress):

```python
import json

def midas_mailin_result(calib_tif, outdir):
    """Return the run summary dict, or None if the run has not finished."""
    p = Path(outdir) / f"{Path(calib_tif).stem}_run_summary.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as fh:
        s = json.load(fh)
    # s["status"]: "ok" | "partial_failure" | "failed"
    # s["calibration"]["status"], s["integrations"][i]["status"/"error"/"outputs"]
    return s
```

Or keep the `Popen` returned by `launch_midas_mailin` and check
`proc.poll()` / `proc.returncode` (`None` = still running, `0` = success).
The summary file is the better signal if the mail-in program might restart in
between.

### 4.5 Checklist for the beamline PC

- [ ] `midas_mailin` env created on **this** machine (section 2) and
  ```
  `MIDAS_MAILIN_PYTHON` points at its `python.exe`.
  ```
- [ ] `MIDAS_MAILIN_CONFIG` points at the config YAML (and `mask_file` inside
  ```
  it, if used, points at the current bad-pixel mask on this machine).
  ```
- [ ] The hand test in section 3 gives exit code 0.
- [ ] Output directory is writable by the account running the mail-in program.

---

## 5. Reference: inputs, outputs, exit codes, config

### Inputs

| Argument | Meaning |
|---|---|
| `--calib FILE` | exactly **one** calibrant `.tif`; its `.metadata` sidecar must be next to it (wavelength + pixel pitch are read from it; beam centre and detector distance are always found from the image itself) |
| `--integ FILE ...` | frames to integrate (zero or more) |
| `--integ-list FILE` | alternative/addition to `--integ`: text file, one path per line (`#` comments allowed). Use for long lists |
| `--outdir DIR` | output directory (created if missing) |
| `--config FILE` | YAML config; omit for built-in defaults |
| `--overwrite` / `--no-overwrite` | override the config's `overwrite` (redo vs skip existing outputs) |

The calibrant frame is always integrated too. Input file stems must be unique
within one call, because all outputs share `--outdir`.

### Outputs (original area-detector stem kept, suffix added)

- `<stem>_calib.json`, `<stem>_calib.png` (ring overlay), `<stem>_calib_residual_corr.bin`
- `<stem>_integ.<csv|xye|fxye|dat|esg|2d.csv>` (formats chosen in the config),
`<stem>_integ_lineout.png`
- `<calib_stem>_run_summary.json` -- config used, plus per-file status, outputs
and error messages

### GSAS-II compatibility of `.xye` / `.fxye`

The two writers (`write_gsas_xye`, `write_gsas_fxye` in `midas_mailin/lib.py`)
follow GSAS-II's own exporters (`GSASII/exports/G2export_pwdr.py`) and are
checked against its importers: `.xye` = 2theta in degrees inside a `/* ... */`
header block; `.fxye` = title line, `#` metadata, then
`BANK 1 N N CONST start step 0 0 FXYE` and rows in centidegrees. Non-finite
intensities become 0 and zero/NaN sigmas are replaced by the smallest positive
sigma (otherwise GSAS-II's `.xye` reader silently truncates the file). The
wavelength is not stored in either format -- set it in GSAS-II's instrument
parameters. To re-verify against a GSAS-II install:
`~/g2main/bin/python tests/test_gsas_compat.py` (set `GSAS2_ROOT` if elsewhere).

### Exit codes

| Code | Meaning |
|---|---|
| `0` | everything succeeded |
| `1` | calibration failed (nothing integrated), or one or more integrations failed -- see the summary JSON |
| `2` | bad inputs or config (missing file, unknown config key, duplicate stems); nothing was run |

### Configuration

`configs/beamline_17bm.yaml` is fully commented. Every key is optional, but an
unknown key is an error, so a typo can never be silently ignored. Copy it to
create a config for another beamline.

### Layout

```
midas_mailin/
  pyproject.toml  environment.yml  README.md
  configs/beamline_17bm.yaml
  midas_mailin/   __main__.py  pipeline.py (CLI)  config.py  lib.py
```
