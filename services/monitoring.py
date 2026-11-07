"""Compatibility imports for the canonical monitoring service."""
from services.engine import detect_excursions, detect_gaps, iso_now

__all__ = ['detect_excursions', 'detect_gaps', 'iso_now']
