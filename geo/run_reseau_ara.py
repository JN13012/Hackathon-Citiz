"""Lance le constructeur ARA en libérant les buffers inutilisés avant osmium.

Même CLI et mêmes règles que build_reseau_ara.py. Le nettoyage mémoire évite
de conserver un ancien graphe pendant l'extraction suivante dans un conteneur.
"""

import ctypes
import gc
from pathlib import Path
import subprocess
import sys

import pyarrow as pa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo import build_reseau_ara as regional


def release_unused_memory():
    gc.collect()
    pa.default_memory_pool().release_unused()
    trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
    if trim is not None:
        trim(0)


def main():
    original = regional.run_osmium

    def run_osmium(*args):
        release_unused_memory()
        return original(*args)

    regional.run_osmium = run_osmium
    ap = regional.parser()
    try:
        return regional.build(ap.parse_args())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError, regional.nx.NetworkXException) as exc:
        ap.exit(1, f"Erreur construction réseau ARA : {exc}\n")
    finally:
        regional.run_osmium = original


if __name__ == "__main__":
    main()
