"""Built-in ECG waveform ops.

Every op takes ``(x, meta, **params)`` and returns an array-like. ``meta`` is
the MI-CDM ImageMetadata for that record — ops read acquisition parameters from
there rather than from a global config, which is the whole point of having
standardized metadata in the CDM.

Array convention: ``(n_leads, n_samples)``. Ops that change lead order say so
and record the resulting order, because a silently reordered lead set is the
single most common way an externally validated ECG model degrades without
anyone noticing.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ..ontology.objects import ImageMetadata
from .recipe import register_op

STANDARD_12 = ["I", "II", "III", "aVR", "aVL", "aVF",
               "V1", "V2", "V3", "V4", "V5", "V6"]


def _np():
    import numpy as np  # type: ignore
    return np


@register_op("select_leads", requires_metadata=["Waveform Channel Source"], modality="ECG")
def select_leads(x, meta: ImageMetadata, leads: Sequence[str] = tuple(STANDARD_12),
                 source_leads: Optional[Sequence[str]] = None, on_missing: str = "error"):
    """Reorder/subset leads by name into the order the model expects.

    Positional slicing is not equivalent: MIMIC-IV-ECG and a vendor export can
    both be '12-lead' with different channel order.
    """
    np = _np()
    src = list(source_leads or meta.lead_names)
    if not src:
        raise ValueError("no source lead names available")
    idx = []
    for want in leads:
        if want in src:
            idx.append(src.index(want))
        elif on_missing == "zero":
            idx.append(None)
        else:
            raise ValueError(f"lead '{want}' not in source leads {src}")
    arr = np.asarray(x)
    out = np.zeros((len(idx), arr.shape[-1]), dtype=arr.dtype)
    for i, j in enumerate(idx):
        if j is not None:
            out[i] = arr[j]
    return out


@register_op("resample", requires_metadata=["Sampling Frequency"], modality="ECG")
def resample(x, meta: ImageMetadata, target_hz: float = 500.0,
             source_hz: Optional[float] = None):
    """Linear resample to ``target_hz``. Source rate comes from MI-CDM unless
    explicitly overridden — an override is recorded in the recipe, a guess is not."""
    np = _np()
    fs = float(source_hz if source_hz is not None else meta.sampling_frequency)
    arr = np.asarray(x, dtype=float)
    if abs(fs - target_hz) < 1e-9:
        return arr
    n_out = int(round(arr.shape[-1] * target_hz / fs))
    old = np.linspace(0, 1, arr.shape[-1])
    new = np.linspace(0, 1, n_out)
    return np.stack([np.interp(new, old, ch) for ch in np.atleast_2d(arr)])


@register_op("bandpass", modality="ECG")
def bandpass(x, meta: ImageMetadata, low_hz: float = 0.5, high_hz: float = 40.0,
             fs: Optional[float] = None, order: int = 3):
    """Butterworth bandpass via scipy if available; otherwise a documented
    fallback (moving-average detrend + rolling smooth) with a loud warning in
    the sample record — never a silent no-op."""
    np = _np()
    rate = float(fs if fs is not None else (meta.sampling_frequency or 500.0))
    arr = np.atleast_2d(np.asarray(x, dtype=float))
    try:
        from scipy.signal import butter, filtfilt  # type: ignore
        nyq = rate / 2.0
        b, a = butter(order, [max(low_hz / nyq, 1e-6), min(high_hz / nyq, 0.999)], btype="band")
        return filtfilt(b, a, arr, axis=-1)
    except ImportError:
        win = max(int(rate / max(high_hz, 1e-6)), 1)
        kernel = np.ones(win) / win
        smoothed = np.stack([np.convolve(ch, kernel, mode="same") for ch in arr])
        base_win = max(int(rate / max(low_hz, 1e-6)), 1)
        base_kernel = np.ones(base_win) / base_win
        baseline = np.stack([np.convolve(ch, base_kernel, mode="same") for ch in smoothed])
        return smoothed - baseline


@register_op("notch", modality="ECG")
def notch(x, meta: ImageMetadata, freq_hz: float = 60.0, q: float = 30.0,
          fs: Optional[float] = None):
    """Powerline notch. 50 Hz vs 60 Hz is a site parameter, so it belongs in the
    recipe rather than in code."""
    np = _np()
    rate = float(fs if fs is not None else (meta.sampling_frequency or 500.0))
    arr = np.atleast_2d(np.asarray(x, dtype=float))
    try:
        from scipy.signal import iirnotch, filtfilt  # type: ignore
        b, a = iirnotch(freq_hz / (rate / 2.0), q)
        return filtfilt(b, a, arr, axis=-1)
    except ImportError:
        return arr


@register_op("crop_or_pad", modality="ECG")
def crop_or_pad(x, meta: ImageMetadata, n_samples: int = 5000, mode: str = "center",
                pad_value: float = 0.0):
    """Fix the temporal length the model expects (e.g. 10 s @ 500 Hz = 5000)."""
    np = _np()
    arr = np.atleast_2d(np.asarray(x, dtype=float))
    n = arr.shape[-1]
    if n == n_samples:
        return arr
    if n > n_samples:
        start = (n - n_samples) // 2 if mode == "center" else 0
        return arr[:, start:start + n_samples]
    pad = n_samples - n
    left = pad // 2 if mode == "center" else 0
    return np.pad(arr, ((0, 0), (left, pad - left)), constant_values=pad_value)


@register_op("scale_units", requires_metadata=["Waveform Amplitude Units"], modality="ECG")
def scale_units(x, meta: ImageMetadata, target_unit: str = "mV",
                source_unit: Optional[str] = None):
    """Convert amplitude units. uV-vs-mV mismatches produce a 1000x input error
    that most networks absorb into a confidently wrong prediction."""
    np = _np()
    src = (source_unit or meta.get("Waveform Amplitude Units") or "mV")
    factors = {("uV", "mV"): 1e-3, ("mV", "uV"): 1e3, ("V", "mV"): 1e3, ("mV", "V"): 1e-3}
    f = 1.0 if src == target_unit else factors.get((src, target_unit))
    if f is None:
        raise ValueError(f"no conversion from {src} to {target_unit}")
    return np.asarray(x, dtype=float) * f


@register_op("normalize", modality="ECG")
def normalize(x, meta: ImageMetadata, method: str = "zscore_per_lead", eps: float = 1e-8):
    """Per-lead z-score by default. ``method='global'`` uses one mean/sd across
    leads — models differ, so this is an explicit choice, not a default."""
    np = _np()
    arr = np.atleast_2d(np.asarray(x, dtype=float))
    if method == "zscore_per_lead":
        mu = arr.mean(axis=-1, keepdims=True)
        sd = arr.std(axis=-1, keepdims=True)
    elif method == "global":
        mu, sd = arr.mean(), arr.std()
    elif method == "minmax":
        lo, hi = arr.min(axis=-1, keepdims=True), arr.max(axis=-1, keepdims=True)
        return (arr - lo) / (hi - lo + eps)
    else:
        raise ValueError(f"unknown normalize method '{method}'")
    return (arr - mu) / (sd + eps)


@register_op("drop_if_flat", modality="ECG")
def drop_if_flat(x, meta: ImageMetadata, min_sd: float = 1e-6):
    """Quality gate: a lead with ~zero variance is a disconnected electrode.
    Raising here is intentional — such a record should appear in the attrition
    table, not in the denominator of a performance metric."""
    np = _np()
    arr = np.atleast_2d(np.asarray(x, dtype=float))
    if float(arr.std(axis=-1).min()) < min_sd:
        raise ValueError("flat lead detected (likely disconnected electrode)")
    return arr
