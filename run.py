#!/usr/bin/env python
"""Entry point for every bot:  python run.py <bot> [options]

  sale       daily sale report            python run.py sale --help
  vms        Myntra VMS report            python run.py vms --help
  ajio-tat   AJIO dispatch TAT            python run.py ajio-tat --help
  ajio-spf   AJIO SPF claims              python run.py ajio-spf --help
"""
import importlib
import sys

BOTS = {"sale": "bindal.sale.pipeline", "vms": "bindal.vms.pipeline",
        "ajio-tat": "bindal.ajio_tat.pipeline", "ajio-spf": "bindal.ajio_spf.pipeline"}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in BOTS:
        print(__doc__)
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    return importlib.import_module(BOTS[argv[0]]).main(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
