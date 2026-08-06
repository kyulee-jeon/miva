"""MI-VAL step (4b) — model acquisition and inference.

Four source types, one interface. A model arrives as a local .pth, a GitHub
repo, a Hugging Face id, or a Zenodo DOI; after :meth:`ModelRegistry.fetch` it
is a local path with a recorded checksum either way. Everything downstream sees
the same object, which is what lets step (5) compare STEMI-style supervised
models against a foundation model like ECGFounder inside one run.

Safety note: fetching and executing third-party model code is a real trust
decision. ``allow_remote_code`` defaults to False and the resolved checksum
goes into the RunManifest, so a site can pin exactly what it executed.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Optional

from ..ontology.objects import ModelCard


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while blob := fh.read(chunk):
            h.update(blob)
    return h.hexdigest()


class ModelRegistry:
    """Resolve a ModelCard to executable weights + a callable."""

    def __init__(self, cache_dir: Path | str = "~/.cache/mival/models",
                 allow_remote_code: bool = False):
        self.cache_dir = Path(os.path.expanduser(str(cache_dir)))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.allow_remote_code = allow_remote_code

    # -- acquisition -------------------------------------------------------

    def fetch(self, card: ModelCard) -> Path:
        dispatch = {
            "local": self._fetch_local,
            "github": self._fetch_github,
            "huggingface": self._fetch_hf,
            "zenodo": self._fetch_url,
            "url": self._fetch_url,
        }
        if card.source_type not in dispatch:
            raise ValueError(f"unknown source_type '{card.source_type}'")
        path = dispatch[card.source_type](card)
        self._verify(card, path)
        return path

    def _fetch_local(self, card: ModelCard) -> Path:
        p = Path(os.path.expanduser(card.source_uri))
        if not p.exists():
            raise FileNotFoundError(f"weights not found: {p}")
        return p

    def _fetch_github(self, card: ModelCard) -> Path:
        dest = self.cache_dir / f"{card.id}-{card.version}"
        if not dest.exists():
            subprocess.run(["git", "clone", "--depth", "1", card.source_uri, str(dest)],
                           check=True)
        if self.allow_remote_code and str(dest) not in sys.path:
            sys.path.insert(0, str(dest))
        weights = card.hyperparameters.get("weights_relpath")
        return dest / weights if weights else dest

    def _fetch_hf(self, card: ModelCard) -> Path:
        from huggingface_hub import snapshot_download  # type: ignore
        return Path(snapshot_download(repo_id=card.source_uri,
                                      revision=card.hyperparameters.get("revision"),
                                      cache_dir=str(self.cache_dir)))

    def _fetch_url(self, card: ModelCard) -> Path:
        import urllib.request
        dest = self.cache_dir / f"{card.id}-{card.version}.{card.weights_format}"
        if not dest.exists():
            urllib.request.urlretrieve(card.source_uri, dest)
        return dest

    def _verify(self, card: ModelCard, path: Path) -> None:
        if not card.checksum_sha256 or not path.is_file():
            return
        got = sha256_file(path)
        if got != card.checksum_sha256:
            raise RuntimeError(
                f"checksum mismatch for {card.object_id}: "
                f"expected {card.checksum_sha256}, got {got}")

    # -- instantiation -----------------------------------------------------

    def build(self, card: ModelCard, weights_path: Optional[Path] = None) -> Callable:
        """Return a callable ``f(batch) -> scores``.

        ``entrypoint`` ("pkg.module:build_model") is the escape hatch that keeps
        MI-VAL from having to know each model's architecture. A card without one
        is assumed to be a TorchScript / full-pickle artifact.
        """
        weights_path = weights_path or self.fetch(card)
        if card.weights_format == "onnx":
            return self._build_onnx(card, weights_path)
        return self._build_torch(card, weights_path)

    def _build_torch(self, card: ModelCard, weights_path: Path) -> Callable:
        import torch  # type: ignore

        device = torch.device(card.device)
        if card.entrypoint:
            mod_name, _, fn_name = card.entrypoint.partition(":")
            builder = getattr(importlib.import_module(mod_name), fn_name)
            model = builder(**card.hyperparameters.get("build_kwargs", {}))
            state = torch.load(str(weights_path), map_location=device)
            state = state.get("state_dict", state) if isinstance(state, dict) else state
            model.load_state_dict(state, strict=card.hyperparameters.get("strict_load", True))
        else:
            model = torch.load(str(weights_path), map_location=device)
        model.to(device).eval()

        @torch.no_grad()
        def infer(batch):
            x = batch if hasattr(batch, "to") else torch.as_tensor(batch, dtype=torch.float32)
            out = model(x.to(device))
            return out.detach().cpu().numpy()

        return infer

    def _build_onnx(self, card: ModelCard, weights_path: Path) -> Callable:
        import onnxruntime as ort  # type: ignore

        sess = ort.InferenceSession(str(weights_path))
        name = sess.get_inputs()[0].name

        def infer(batch):
            import numpy as np  # type: ignore
            return sess.run(None, {name: np.asarray(batch, dtype="float32")})[0]

        return infer

    # -- ensembles ---------------------------------------------------------

    def build_ensemble(self, cards: list[ModelCard]) -> Callable:
        """Mean of member outputs. Ensemble membership is recorded per card so
        the manifest says which N models produced the reported number."""
        members = [self.build(c) for c in cards]

        def infer(batch):
            import numpy as np  # type: ignore
            return np.mean([m(batch) for m in members], axis=0)

        return infer

    def provenance(self, card: ModelCard, weights_path: Path) -> dict:
        return {
            "model": asdict(card),
            "resolved_path": str(weights_path),
            "resolved_sha256": sha256_file(weights_path) if weights_path.is_file() else None,
        }
