"""
utils.py - Shared utility functions for Job-Autofill pipeline
"""
import re
from typing import List, Optional


def ci_match_label(val: str, labels: List[str]) -> Optional[str]:
    """
    Case-insensitive exact match to one of the labels.
    Returns the canonical label if found, else None.
    Args:
        val: The value to match.
        labels: List of possible label strings.
    Returns:
        The matched label from labels, or None if not found.
    """
    v = val.strip().casefold()
    for lab in labels:
        if v == lab.casefold():
            return lab
    return None


def normalize(s: str) -> str:
    """
    Normalize whitespace in a string to single spaces and trim.
    Args:
        s: Input string.
    Returns:
        Normalized string with single spaces and no leading/trailing whitespace.
    """
    return re.sub(r"\s+", " ", (s or "").strip())
