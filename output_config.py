#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Centralized output configuration for Job-Autofill pipeline.
All scripts import from this file to ensure consistent output paths.
"""

from pathlib import Path

# Base output directory
OUTPUT_BASE = Path("outputs")

# Subdirectories
OUTPUT_DATA = OUTPUT_BASE / "data"
OUTPUT_DOCUMENTS = OUTPUT_BASE / "documents" 
OUTPUT_SCREENSHOTS = OUTPUT_BASE / "screenshots"
OUTPUT_VIDEOS = OUTPUT_BASE / "videos"
OUTPUT_LOGS = OUTPUT_BASE / "logs"

# Ensure all directories exist
def ensure_output_dirs():
    """Create all output directories if they don't exist."""
    for dir_path in [OUTPUT_DATA, OUTPUT_DOCUMENTS, OUTPUT_SCREENSHOTS, OUTPUT_VIDEOS, OUTPUT_LOGS]:
        dir_path.mkdir(parents=True, exist_ok=True)

# File paths for each script
class OutputPaths:
    """Centralized output file paths for all scripts."""
    
    # a1_page_judger.py outputs
    PAGE_JUDGER_OUT = OUTPUT_LOGS / "page_judger_out.json"
    RESOLVED_FORM_URL = OUTPUT_LOGS / "resolved_form_url.txt"
    FORM_PAGE_REACHED = OUTPUT_LOGS / "form_page_reached.txt"
    
    # a2_resume_parser_gemini.py outputs
    PARSED_RESUME = OUTPUT_DATA / "parsed_resume.json"
    
    # a3_cover_letter_and_summary.py outputs
    JOB_PAGE_MD = OUTPUT_DOCUMENTS / "job_page.md"
    JOB_SUMMARY = OUTPUT_DOCUMENTS / "job_summary.txt"
    COVER_LETTER = OUTPUT_DOCUMENTS / "cover_letter.txt"
    
    # a4_form_extractor_updated.py outputs
    FORM_FIELDS_CLEANED = OUTPUT_DATA / "form_fields_cleaned.json"
    
    # a4_enhanced_form_extractor.py outputs
    FORM_FIELDS_ENHANCED = OUTPUT_DATA / "form_fields_enhanced.json"
    FORM_EXTRACTION_DEBUG = OUTPUT_DATA / "form_extraction_debug.json"
    
    # a5_form_answer_gemini.py outputs
    FILLED_ANSWERS = OUTPUT_DATA / "filled_answers.json"
    SKIPPED_FIELDS = OUTPUT_DATA / "skipped_fields.json"
    
    # a6_complete_skipped_fields.py outputs
    USER_COMPLETED_ANSWERS = OUTPUT_DATA / "user_completed_answers.json"
    STILL_SKIPPED = OUTPUT_DATA / "still_skipped.json"
    
    # a7_fill_form_resume.py inputs (these files are created by other scripts)
    # No new outputs, just uses existing files
    
    # Directory paths for dynamic file creation
    SCREENSHOTS_DIR = OUTPUT_SCREENSHOTS
    VIDEOS_DIR = OUTPUT_VIDEOS

# Initialize directories when module is imported
ensure_output_dirs()