"""Built-in 2D image ops (CR/DX/CT slice).

Kept minimal on purpose: the framework's first target is ECG. What matters here
is that image ops obey the same contract as ECG ops — metadata-declared,
registered, hashable — so extending to CXR is a recipe change, not a rewrite.
"""

from __future__ import annotations

from typing import Optional

from ..ontology.objects import ImageMetadata
from .recipe import register_op


def _np():
    import numpy as np  # type: ignore
    return np


@register_op("apply_voi_lut", requires_metadata=["Photometric Interpretation"], modality="CR")
def apply_voi_lut(x, meta: ImageMetadata, window_center: Optional[float] = None,
                  window_width: Optional[float] = None, invert_if_monochrome1: bool = True):
    """Windowing + MONOCHROME1 inversion.

    Skipping the inversion silently flips contrast for a subset of studies, and
    a model trained on MONOCHROME2 will keep producing plausible scores on it.
    """
    np = _np()
    arr = np.asarray(x, dtype=float)
    wc = window_center if window_center is not None else meta.get("Window Center")
    ww = window_width if window_width is not None else meta.get("Window Width")
    if wc is not None and ww:
        lo, hi = float(wc) - float(ww) / 2, float(wc) + float(ww) / 2
        arr = np.clip(arr, lo, hi)
    if invert_if_monochrome1 and meta.get("Photometric Interpretation") == "MONOCHROME1":
        arr = arr.max() - arr
    return arr


@register_op("resize", modality="CR")
def resize(x, meta: ImageMetadata, size: tuple = (224, 224), keep_aspect: bool = True):
    """Nearest-neighbour resize with no external deps. Swap for a site-preferred
    interpolator by registering your own op if resampling quality matters."""
    np = _np()
    arr = np.asarray(x, dtype=float)
    th, tw = size
    if keep_aspect:
        h, w = arr.shape[-2:]
        scale = min(th / h, tw / w)
        nh, nw = max(int(h * scale), 1), max(int(w * scale), 1)
    else:
        nh, nw = th, tw
    yi = (np.arange(nh) * (arr.shape[-2] / nh)).astype(int)
    xi = (np.arange(nw) * (arr.shape[-1] / nw)).astype(int)
    out = arr[..., yi, :][..., :, xi]
    if keep_aspect and (nh, nw) != (th, tw):
        canvas = np.zeros(arr.shape[:-2] + (th, tw), dtype=out.dtype)
        y0, x0 = (th - nh) // 2, (tw - nw) // 2
        canvas[..., y0:y0 + nh, x0:x0 + nw] = out
        out = canvas
    return out


@register_op("rescale_intensity", modality="CR")
def rescale_intensity(x, meta: ImageMetadata, out_range: tuple = (0.0, 1.0),
                      percentile_clip: Optional[tuple] = (0.5, 99.5)):
    np = _np()
    arr = np.asarray(x, dtype=float)
    if percentile_clip:
        lo, hi = np.percentile(arr, percentile_clip[0]), np.percentile(arr, percentile_clip[1])
        arr = np.clip(arr, lo, hi)
    lo, hi = arr.min(), arr.max()
    a, b = out_range
    return (arr - lo) / (hi - lo + 1e-8) * (b - a) + a
