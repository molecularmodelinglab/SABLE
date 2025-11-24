"""Prewarm the MoleculeHEALER instance and write it to disk cache.
"""
from pathlib import Path
import logging
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from tools.enumerator_tool import get_enumerator, _CACHE_PATH

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("prewarm_enumerator")

if __name__ == "__main__":
    LOGGER.info("Prewarming MoleculeHEALER and caching to %s", _CACHE_PATH)
    enum = get_enumerator()
    LOGGER.info("Done prewarming. Instance: %s", type(enum))