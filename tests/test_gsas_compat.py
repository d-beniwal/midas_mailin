"""Round-trip our .xye/.fxye writers through GSAS-II's OWN importers.

Needs a GSAS-II install: set GSAS2_ROOT (dir containing the `GSASII` package,
default ~/g2main/GSAS-II) and run with GSAS-II's python, which must also be
able to import midas_mailin (PYTHONPATH=<repo>) -- or the test is skipped.
    PYTHONPATH=. ~/g2main/bin/python -m pytest tests/test_gsas_compat.py   (or run the file directly)
Only numpy is needed from midas_mailin's side: the writers are imported from lib
lazily-safe copies (see _load_writers).
"""
import os, sys, tempfile, types, importlib.util
from pathlib import Path
import numpy as np

ROOT = Path(os.environ.get('GSAS2_ROOT', Path.home() / 'g2main' / 'GSAS-II'))


def _load_writers():
    """Exec only the writer helpers out of lib.py (lib's top-level imports pull in torch/midas)."""
    src = (Path(__file__).resolve().parents[1] / 'midas_mailin' / 'lib.py').read_text()
    a = src.index('def sanitize_profile'); b = src.index('FORMAT_WRITERS = {')
    ns = {'np': np, 'Path': Path}
    exec(src[a:b], ns)
    return ns


def run():
    if not (ROOT / 'GSASII').is_dir():
        print('SKIP: GSAS-II not found at', ROOT); return 0
    sys.path.insert(0, str(ROOT))
    from GSASII.imports import G2pwd_xye, G2pwd_fxye
    W = _load_writers()
    n = 500
    tth = np.linspace(1.0, 12.0, n) ** 1.0
    tth = 1.0 + np.cumsum(np.full(n, 0.0022) * (1 + 0.04 * np.linspace(0, 1, n)))  # non-uniform, like R-uniform bins
    y = 500 + 100 * np.sin(tth)
    s = np.full(n, 0.45)
    # hostile values the importers handle badly if passed through raw
    y[10] = np.nan; y[11] = np.inf; y[12] = -3.0; y[13] = 0.0
    s[20] = 0.0; s[21] = np.nan; s[22] = -1.0           # y>0 with bad sigma -> would truncate the xye read
    meta = types.SimpleNamespace(to_header_lines=lambda prefix='# ': [prefix + '{', prefix + '  "wavelength_A": 0.2522', prefix + '}'])
    fails = 0
    with tempfile.TemporaryDirectory() as d:
        for ext, mod, cls, W_ in (('xye', G2pwd_xye, 'xye_ReaderClass', 'write_gsas_xye'),
                                  ('fxye', G2pwd_fxye, 'GSAS_ReaderClass', 'write_gsas_fxye')):
            f = str(Path(d) / f'T_integ.{ext}')
            W[W_](f, tth, y, s, metadata=meta)
            r = getattr(mod, cls)()
            ok = r.ContentsValidator(f) and r.Reader(f)
            x_, y_, w_ = r.powderdata[:3]
            checks = {
                'validator+reader ok': bool(ok),
                'all rows read (no truncation)': len(x_) == n,
                'x matches 2theta (deg)': np.allclose(x_, tth, atol=1e-5),
                'no NaN/inf in y,w': bool(np.isfinite(y_).all() and np.isfinite(w_).all()),
                'good point: w == 1/sigma^2': bool(np.isclose(w_[100], 1 / 0.45 ** 2, rtol=1e-5)),
                'y<=0 / non-finite points get weight 0': bool((w_[[10, 11, 12, 13]] == 0).all()),
                'bad-sigma points kept with finite weight': bool((w_[[20, 21, 22]] > 0).all()),
                'metadata kept as comments': len(r.comments) >= 3,
            }
            for k, v in checks.items():
                print(f'  [{ext}] {"PASS" if v else "FAIL"}  {k}')
                fails += (not v)
    print('RESULT:', 'all passed' if not fails else f'{fails} FAILED')
    return 1 if fails else 0


def test_gsas_compat():
    assert run() == 0


if __name__ == '__main__':
    sys.exit(run())
