"""CLI: calibrate ONE calibrant frame, then integrate any number of frames.

    python -m midas_mailin --calib CAL.tif --integ A.tif B.tif ... \
        --outdir OUT --config CONFIG.yaml [--overwrite | --no-overwrite]

Inputs
  --calib FILE       exactly one calibrant frame (.tif; its .metadata sidecar
                     must sit next to it -- wavelength and pixel pitch are read
                     from there).
  --integ FILE ...   frames to integrate (zero or more). May also be given as
  --integ-list FILE  a text file with one path per line ('#' comments ok) --
                     use this for long lists (Windows command-line length).
  --outdir DIR       all outputs go here (created if missing).
  --config FILE      YAML config (see configs/); omit for built-in defaults.

The calibrant frame is always integrated too (with its own calibration), in
addition to the --integ frames; listing it in --integ is harmless.

Outputs (original area-detector stem preserved, suffix added)
  <stem>_calib.json, <stem>_calib.png, <stem>_calib_residual_corr.bin
  <stem>_integ.<fmt>..., <stem>_integ_lineout.png
  <calib_stem>_run_summary.json   machine-readable status of this run

Exit code: 0 all succeeded; 1 calibration or any integration failed;
2 bad inputs/config (nothing was run).
"""
import argparse
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import tifffile

from . import __version__
from .config import load_config, dump_config
from . import lib

CALIB_SUFFIX = '_calib'
INTEG_SUFFIX = '_integ'
MAX_MASK_BAD_FRACTION = 0.10   # sanity limit: a real bad-pixel mask flags far fewer pixels


def _calibrate(calib_tif: Path, outdir: Path, cfg, overwrite: bool, seed_kwargs=None):
    """Calibrate `calib_tif`; returns the path of <stem>_calib.json."""
    stem = calib_tif.stem
    final_json = outdir / f'{stem}{CALIB_SUFFIX}.json'
    final_png = outdir / f'{stem}{CALIB_SUFFIX}.png'
    final_bin = outdir / f'{stem}{CALIB_SUFFIX}_residual_corr.bin'

    if final_json.exists() and not overwrite:
        print(f'{final_json} already exists -- skipping calibration (overwrite is off)')
        return final_json

    scratch_dir = outdir / f'.{stem}_scratch'
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True)
    try:
        bundle = lib.calibrate_lab6(
            calib_tif, scratch_dir,
            calibrant=cfg.calibrant, overwrite=True, default_px_um=cfg.default_px_um,
            seed_kwargs=seed_kwargs, **cfg.calibrate_kwargs,
        )
        scratch_json = scratch_dir / 'calibration.json'
        scratch_bin = scratch_dir / 'residual_corr.bin'
        with open(scratch_json) as fh:
            summary = json.load(fh)
        if scratch_bin.exists():
            shutil.move(str(scratch_bin), str(final_bin))
            summary['residual_corr_bin'] = str(final_bin)
        else:
            summary['residual_corr_bin'] = None
        with open(scratch_json, 'w') as fh:
            json.dump(summary, fh, indent=2)
        shutil.move(str(scratch_json), str(final_json))
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    lib.render_calibration_overlay_png(
        bundle, final_json, final_png,
        calibrant=cfg.calibrant, two_theta_max_deg=cfg.ring_two_theta_max_deg,
    )
    r = bundle.result
    print(f'calibrated {calib_tif.name}:')
    print(f'  Lsd  = {r.Lsd / 1000:.4f} mm   (seed: {r.seed_Lsd / 1000:.4f} mm)')
    print(f'  BC   = ({r.BC_y:.3f}, {r.BC_z:.3f}) px   (seed: ({r.seed_BC_y:.3f}, {r.seed_BC_z:.3f}) px)')
    if r.post_residual_strain_uE is not None:
        print(f'  post-residual strain = {r.post_residual_strain_uE:.1f} microstrain')
    print(f'  wrote {final_json}\n  wrote {final_png}')
    if final_bin.exists():
        print(f'  wrote {final_bin}')
    return final_json


def _integrate_frame(tif_path: Path, context, outdir: Path, cfg, overwrite: bool):
    """Integrate one frame -> <stem>_integ.<fmt> files + lineout png. Returns written paths."""
    out_stem = outdir / f'{tif_path.stem}{INTEG_SUFFIX}'
    lineout_png = outdir / f'{tif_path.stem}{INTEG_SUFFIX}_lineout.png'
    expected = [Path(str(out_stem) + '.' + lib.FORMAT_EXT[f]) for f in cfg.integration_output_formats]
    if not overwrite and lineout_png.exists() and all(p.exists() for p in expected):
        print(f'{tif_path.name}: outputs already exist -- skipping (overwrite is off)')
        return [str(p) for p in expected] + [str(lineout_png)]

    image = tifffile.imread(tif_path).astype(np.float32)
    ir = lib.integrate_with_context(
        image, context, error_model=cfg.error_model,
        pixel_weighted_averaging=cfg.pixel_weighted_averaging,
    )
    written = lib.write_integration_outputs(ir, out_stem, cfg.integration_output_formats, subpixel_k=cfg.subpixel_k)
    for fmt, path in written.items():
        print(f'  {fmt:8s} -> {path}  ({path.stat().st_size} bytes)')
    lib.render_intensity_plot_png(ir, lineout_png, title=f'{tif_path.name} -- integrated profile ({ir.method})')
    print(f'  lineout  -> {lineout_png}')
    return [str(p) for p in written.values()] + [str(lineout_png)]


def _read_list_file(path: Path):
    lines = [ln.strip() for ln in Path(path).read_text(encoding='utf-8').splitlines()]
    return [Path(ln) for ln in lines if ln and not ln.startswith('#')]


def _write_summary(path: Path, summary: dict):
    summary['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    with open(path, 'w') as fh:
        json.dump(summary, fh, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog='midas_mailin', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--calib', required=True, type=Path, help='the single calibrant .tif')
    p.add_argument('--integ', nargs='*', default=[], type=Path, help='.tif frames to integrate')
    p.add_argument('--integ-list', type=Path, default=None, help='text file, one .tif path per line')
    p.add_argument('--outdir', required=True, type=Path, help='output directory')
    p.add_argument('--config', type=Path, default=None, help='YAML config (default: built-in defaults)')
    p.add_argument('--overwrite', dest='overwrite', action='store_true', default=None,
                   help='redo outputs that already exist (overrides config `overwrite`)')
    p.add_argument('--no-overwrite', dest='overwrite', action='store_false',
                   help='skip outputs that already exist (overrides config `overwrite`)')
    p.add_argument('--version', action='version', version=f'midas_mailin {__version__}')
    args = p.parse_args(argv)

    # ---- validate everything up front: exit 2 before doing any work ----------
    errors = []
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f'error: config: {exc}', file=sys.stderr)
        return 2
    overwrite = cfg.overwrite if args.overwrite is None else args.overwrite

    integ = list(args.integ)
    if args.integ_list is not None:
        try:
            integ += _read_list_file(args.integ_list)
        except OSError as exc:
            errors.append(f'cannot read --integ-list: {exc}')

    calib = args.calib
    if not calib.is_file():
        errors.append(f'calibration file not found: {calib}')
    for f in integ:
        if not f.is_file():
            errors.append(f'integration file not found: {f}')
    if cfg.mask_file is not None:
        if not Path(cfg.mask_file).is_file():
            errors.append(f'mask_file not found: {cfg.mask_file}')
        else:
            try:
                bad_frac = float(np.mean(lib.load_mask_file(cfg.mask_file) != 0))
                if bad_frac > MAX_MASK_BAD_FRACTION:
                    errors.append(
                        f'mask_file {cfg.mask_file} flags {bad_frac:.1%} of pixels as bad (limit '
                        f'{MAX_MASK_BAD_FRACTION:.0%}). A mask must be 0 = good / non-zero = bad; this '
                        f'looks like a detector IMAGE, not a mask (e.g. the BadPixel_*.tif files are '
                        f'images -- their sibling .json holds the bad-pixel list).')
            except Exception as exc:
                errors.append(f'cannot read mask_file {cfg.mask_file}: {exc}')

    # integrate list = calibrant first, then the rest; dedupe by resolved path
    seen, to_integrate = set(), []
    for f in [calib] + integ:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            to_integrate.append(f)
    # output names come from the bare stem only -> two inputs sharing a stem would clobber each other
    stems = {}
    for f in to_integrate:
        stems.setdefault(f.stem, []).append(str(f))
    for stem, files in stems.items():
        if len(files) > 1:
            errors.append(f'duplicate file stem {stem!r} (outputs would collide): {files}')
    if errors:
        for e in errors:
            print(f'error: {e}', file=sys.stderr)
        return 2

    outdir = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    summary_path = outdir / f'{calib.stem}_run_summary.json'
    summary = dict(
        midas_mailin_version=__version__, started=time.strftime('%Y-%m-%dT%H:%M:%S'),
        config_file=str(args.config) if args.config else None, config=dump_config(cfg),
        overwrite=overwrite, outdir=str(outdir), status='running',
        calibration=dict(file=str(calib), status='pending', outputs={}),
        integrations=[],
    )

    # ---- calibration ----------------------------------------------------------
    try:
        calib_json = _calibrate(calib, outdir, cfg, overwrite)
        summary['calibration'].update(
            status='ok', outputs=dict(
                json=str(calib_json), png=str(outdir / f'{calib.stem}{CALIB_SUFFIX}.png'),
                residual_corr_bin=str(outdir / f'{calib.stem}{CALIB_SUFFIX}_residual_corr.bin')))
    except Exception as exc:
        traceback.print_exc()
        summary['calibration'].update(status='failed', error=f'{type(exc).__name__}: {exc}')
        summary['status'] = 'failed'
        _write_summary(summary_path, summary)
        print(f'calibration FAILED -- skipping integration. Summary: {summary_path}', file=sys.stderr)
        return 1

    # ---- integration ----------------------------------------------------------
    r_min = lib.resolve_r_min_px(calib_json, cfg.r_min_px, cfg.r_min_2theta_deg)
    if cfg.only_full_rings:
        full_r_max = lib.full_ring_r_max_px(calib_json)
        r_max = full_r_max if cfg.r_max_px is None else min(cfg.r_max_px, full_r_max)
    else:
        r_max = cfg.r_max_px
    print(f'\nintegration range: r_min={r_min:.2f} px, r_max={r_max} px (only_full_rings={cfg.only_full_rings})')
    print(f'mask file: {cfg.mask_file or "(none)"}')
    print(f'eta exclude wedges (deg): {cfg.eta_exclude_deg or "(none)"}')
    print(f'integrating {len(to_integrate)} frame(s) against {calib_json.name} '
          f'(detector mapping built once, reused for all)...')

    try:
        context = lib.build_integration_context(
            calib_json, method=cfg.binning_method,
            r_bin_size=cfg.r_bin_size_px, eta_bin_size=cfg.eta_bin_size_deg,
            r_min=r_min, r_max=r_max, eta_min=cfg.eta_min_deg, eta_max=cfg.eta_max_deg,
            subpixel_k=cfg.subpixel_k, polygon_n_jobs=cfg.polygon_n_jobs,
            polarization=cfg.polarization_correction, pol_fraction=cfg.polarization_fraction,
            pol_plane_eta_deg=cfg.polarization_plane_eta_deg,
            divide_by_distance_sq=cfg.divide_by_distance_sq,
            mask=cfg.mask_file, eta_exclude_deg=cfg.eta_exclude_deg,
        )
    except Exception as exc:
        traceback.print_exc()
        summary['status'] = 'failed'
        summary['integration_setup_error'] = f'{type(exc).__name__}: {exc}'
        _write_summary(summary_path, summary)
        return 1

    n_fail = 0
    for i, tif in enumerate(to_integrate):
        print(f'\n[{i + 1}/{len(to_integrate)}] {tif.name}')
        rec = dict(file=str(tif), status='ok', outputs=[])
        try:
            rec['outputs'] = _integrate_frame(tif, context, outdir, cfg, overwrite)
        except Exception as exc:
            traceback.print_exc()
            rec.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            n_fail += 1
        summary['integrations'].append(rec)

    summary['status'] = 'ok' if n_fail == 0 else 'partial_failure'
    _write_summary(summary_path, summary)
    print(f'\ndone: {len(to_integrate) - n_fail}/{len(to_integrate)} frame(s) integrated. Summary: {summary_path}')
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
