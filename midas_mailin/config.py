"""YAML configuration loader for midas_mailin.

The config file is the ONLY place beamline-specific settings live (calibrant,
binning, masks, output formats, ...). Every key has a default below, so a
config file only has to list what it changes -- but unknown keys are an
error, so a typo can never be silently ignored.
"""
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Optional, List, Union

import yaml


@dataclass
class Config:
    # --- calibrant -------------------------------------------------------------
    calibrant: Union[str, dict] = 'LaB6'
    default_px_um: float = 150.0
    calibrate_kwargs: dict = field(default_factory=lambda: dict(
        n_iter=4, lm_max_iter=200, refine_tilts=True, refine_distortion='radial',
        build_residual_corr=False, max_2theta_deg=28.0, verbose=True))
    # --- integration binning ---------------------------------------------------
    r_bin_size_px: float = 0.25
    eta_bin_size_deg: float = 5.0
    r_min_px: float = 10.0
    r_min_2theta_deg: Optional[float] = None
    r_max_px: Optional[float] = None
    eta_min_deg: float = -180.0
    eta_max_deg: float = 180.0
    binning_method: str = 'subpixel'
    subpixel_k: int = 4
    polygon_n_jobs: int = -1
    error_model: str = 'poisson'
    pixel_weighted_averaging: bool = True
    # --- masking ---------------------------------------------------------------
    mask_file: Optional[str] = None
    eta_exclude_deg: Optional[list] = None
    # --- display ---------------------------------------------------------------
    ring_two_theta_max_deg: float = 25.0
    # --- corrections -----------------------------------------------------------
    polarization_correction: bool = True
    polarization_fraction: float = 0.99
    polarization_plane_eta_deg: float = 0.0
    divide_by_distance_sq: bool = False
    # --- outputs / run behaviour -----------------------------------------------
    integration_output_formats: List[str] = field(
        default_factory=lambda: ['csv', 'xye', 'fxye', 'dat', 'esg', '2d_csv'])
    overwrite: bool = True
    only_full_rings: bool = True


_BINNING = ('hard', 'soft', 'subpixel', 'polygon')
_ERROR = ('poisson', 'azimuthal', 'hybrid')
_FORMATS = ('csv', 'xye', 'fxye', 'dat', 'esg', '2d_csv')


def load_config(path: Optional[Union[str, Path]]) -> Config:
    """Load a YAML config (None -> all defaults). Raises ValueError on any
    unknown key or invalid value."""
    raw = {}
    base = Path.cwd()
    if path is not None:
        path = Path(path)
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f'{path}: top level must be a mapping of key: value')
        base = path.resolve().parent

    known = {f.name for f in fields(Config)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f'{path}: unknown config key(s): {unknown}. Valid keys: {sorted(known)}')

    cfg = Config(**raw)
    if cfg.binning_method not in _BINNING:
        raise ValueError(f'binning_method must be one of {_BINNING}, got {cfg.binning_method!r}')
    if cfg.error_model not in _ERROR:
        raise ValueError(f'error_model must be one of {_ERROR}, got {cfg.error_model!r}')
    bad = [f for f in cfg.integration_output_formats if f not in _FORMATS]
    if bad:
        raise ValueError(f'integration_output_formats: unknown format(s) {bad}; valid: {_FORMATS}')
    if cfg.eta_exclude_deg is not None:
        cfg.eta_exclude_deg = [tuple(w) for w in cfg.eta_exclude_deg]
    if cfg.mask_file is not None:
        p = Path(cfg.mask_file)
        cfg.mask_file = str(p if p.is_absolute() else base / p)   # relative -> relative to the config file
    return cfg


def dump_config(cfg: Config) -> dict:
    return asdict(cfg)
