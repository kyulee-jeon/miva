"""MI-VAL command line.

    mival ontology                      show the object graph
    mival sql       -c config.yaml      render the standardized SQL only
    mival dry-run   -c config.yaml      full protocol check, no DB / no PHI
    mival profile   -c config.yaml      step (2) data-spec report
    mival verify    -m run_manifest.json  is this run portable to another site?
    mival ops                           list registered preprocessing ops
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cfg(args) -> dict:
    from .run.pipeline import load_config
    return load_config(args.config)


def cmd_ontology(args) -> int:
    from .ontology.registry import describe
    print(describe())
    return 0


def cmd_ops(args) -> int:
    from .preprocess import ops_ecg, ops_image  # noqa: F401
    from .preprocess.recipe import OP_REGISTRY
    for name, d in sorted(OP_REGISTRY.items()):
        req = f" requires={d.requires_metadata}" if d.requires_metadata else ""
        print(f"{name:18s} [{d.modality}]{req}\n    {d.doc.splitlines()[0] if d.doc else ''}")
    return 0


def cmd_sql(args) -> int:
    from .retrieve.resolver import CohortImageResolver
    from .run.pipeline import build_objects
    obj = build_objects(_cfg(args))
    r = CohortImageResolver(obj["retrieval"], None, args.workspace)
    print("-- occurrence query --\n" + r.render_occurrence_sql())
    print("\n-- metadata query --\n" + r.render_metadata_sql())
    print("\n-- params --\n" + json.dumps(r.params(), indent=2, default=str))
    return 0


def cmd_dry_run(args) -> int:
    from .run.pipeline import ValidationRun
    run = ValidationRun(_cfg(args), None, args.workspace)
    out = run.dry_run()
    print(json.dumps(out, indent=2, default=str))
    p = out["portability"]
    if not p["portable"]:
        print("\nNot yet portable:", file=sys.stderr)
        for issue in p["issues"]:
            print(f"  - {issue}", file=sys.stderr)
    return 0


def cmd_profile(args) -> int:
    from .run.pipeline import ValidationRun
    run = ValidationRun(_cfg(args), None, args.workspace)
    retrieval = run.step1_retrieve()
    report = run.step2_profile(retrieval)
    print(json.dumps(report["coverage"], indent=2, default=str))
    print(f"HTML: {run.artifacts.get('02_data_spec_html')}")
    return 0


def cmd_verify(args) -> int:
    from .run.manifest import load, verify
    res = verify(load(args.manifest))
    print(json.dumps(res, indent=2))
    return 0 if res["portable"] else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mival", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_cfg(sp):
        sp.add_argument("-c", "--config", required=True, type=Path)
        sp.add_argument("-w", "--workspace", default="_workspace", type=Path)

    sub.add_parser("ontology").set_defaults(fn=cmd_ontology)
    sub.add_parser("ops").set_defaults(fn=cmd_ops)
    for name, fn in (("sql", cmd_sql), ("dry-run", cmd_dry_run), ("profile", cmd_profile)):
        sp = sub.add_parser(name)
        add_cfg(sp)
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("verify")
    sp.add_argument("-m", "--manifest", required=True, type=Path)
    sp.set_defaults(fn=cmd_verify)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
