"""DICOM / waveform loaders injected into the preprocessing engine.

MI-CDM points at files; the framework must not assume how they are encoded.
Three loaders cover the ECG cases in practice, and a registry lets a site add
a fourth without touching recipe or engine code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

LOADERS: dict[str, Callable[[Path], Any]] = {}


def register_loader(name: str) -> Callable:
    def deco(fn: Callable[[Path], Any]) -> Callable:
        LOADERS[name] = fn
        return fn
    return deco


@register_loader("dicom_waveform")
def load_dicom_waveform(path: Path):
    """DICOM Waveform IOD (12-lead ECG). Returns (n_leads, n_samples).

    Note the multiplier: raw waveform samples are integers; without
    ChannelSensitivity the amplitudes are in device units, not mV.
    """
    import numpy as np  # type: ignore
    import pydicom  # type: ignore

    ds = pydicom.dcmread(str(path))
    seq = ds.WaveformSequence[0]
    arr = np.asarray(seq.waveform_array(0) if hasattr(seq, "waveform_array")
                     else ds.waveform_array(0), dtype=float)
    if arr.ndim == 2 and arr.shape[0] > arr.shape[1]:
        arr = arr.T  # -> (n_leads, n_samples)
    return arr


@register_loader("dicom_image")
def load_dicom_image(path: Path):
    """DICOM image IOD with rescale slope/intercept applied."""
    import numpy as np  # type: ignore
    import pydicom  # type: ignore

    ds = pydicom.dcmread(str(path))
    arr = ds.pixel_array.astype(float)
    slope = float(getattr(ds, "RescaleSlope", 1) or 1)
    intercept = float(getattr(ds, "RescaleIntercept", 0) or 0)
    return arr * slope + intercept


@register_loader("wfdb")
def load_wfdb(path: Path):
    """MIMIC-IV-ECG native format, for comparing against the DICOM-converted
    version. Having both loaders is what makes the ETL itself falsifiable."""
    import numpy as np  # type: ignore
    import wfdb  # type: ignore

    rec = wfdb.rdrecord(str(Path(path).with_suffix("")))
    return np.asarray(rec.p_signal, dtype=float).T


@register_loader("npy")
def load_npy(path: Path):
    import numpy as np  # type: ignore
    return np.load(str(path))


def get_loader(name: str) -> Callable[[Path], Any]:
    if name not in LOADERS:
        raise KeyError(f"unknown loader '{name}'. registered: {sorted(LOADERS)}")
    return LOADERS[name]
