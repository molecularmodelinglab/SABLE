"""Deployment requirements for this local HEALER integration."""
import os


def available():
    if os.getenv('HEALER_EXECUTION_MODE', 'internal') != 'internal':
        return False
    from healer.domain import resolve_bb_path
    resolve_bb_path(os.getenv('HEALER_STOCK', 'US_stock'))
    return True
