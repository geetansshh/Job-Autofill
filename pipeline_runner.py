#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Complete Job Application Pipeline Runner

This script orchestrates the entire job application automation process:
1. a1_page_judger.py - Analyze job page and find application form
2. a2_resume_parser_gemini.py - Parse resume using Gemini AI
3. a3_cover_letter_and_summary.py - Generate cover letter and job summary
4. a4_enhanced_form_extractor.py - Extract form fields with technical + AI analysis
5. a5_form_answer_gemini.py - Generate form answers using AI
6. a6_complete_skipped_fields.py - Interactive completion of skipped fields
7. a7_fill_form_resume.py - Automated form filling and submission

Usage:
    python pipeline_runner.py --url "https://job-url-here"
    
    # Or set URL in the script and run:
    python pipeline_runner.py

Requirements:
    - All pipeline scripts (a1-a7) must be in the same directory
    - Virtual environment with all dependencies installed
    - .env file with GEMINI_API_KEY
    - Resume file at ./data/Geetansh_resume.pdf
    - Supplemental context at ./Supplemental-context.json (optional)
"""

import subprocess
import sys
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
import tempfile
import shutil
from datetime import datetime

# Import comprehensive warning suppression
try:
    from warning_suppressor import *
except ImportError:
    # Fallback suppression
    os.environ['GRPC_VERBOSITY'] = 'ERROR'
    os.environ['GRPC_TRACE'] = ''
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    os.environ['GOOGLE_CLOUD_DISABLE_GRPC_FOR_REST'] = 'true'

from output_config import OutputPaths, ensure_output_dirs, OUTPUT_BASE

class PipelineRunner:
    def __init__(self, job_url: str, resume_path: str = "./data/Geetansh_resume.pdf", 
                 headless: bool = True, auto_submit: bool = False):
        self.job_url = job_url
        self.resume_path = Path(resume_path)
        self.headless = headless
        self.auto_submit = auto_submit
        self.python_executable = self._get_python_executable()
        
        # Ensure outputs directory exists
        ensure_output_dirs()
        
    def _get_python_executable(self) -> str:
        """Get the correct Python executable (prefer venv if exists)"""
        venv_python = Path("./autofill/bin/python")
        if venv_python.exists():
            return str(venv_python.absolute())
        return sys.executable
    
    def _run_script(self, script_name: str, description: str, 
                   env_vars: Optional[Dict[str, str]] = None) -> bool:
        """Run a pipeline script and return success status"""
        print(f"\n{'='*60}")
        print(f"🚀 STEP: {description}")
        print(f"📄 Script: {script_name}")
        print(f"{'='*60}")
        
        script_path = Path(script_name)
        if not script_path.exists():
            print(f"❌ ERROR: Script not found: {script_name}")
            return False
            
        # Prepare environment variables
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
            
        try:
            # Run the script
            cmd = [self.python_executable, str(script_path)]
            print(f"🔧 Command: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                cwd=Path.cwd(),
                env=env,
                text=True,
                capture_output=False,  # Show output in real-time
                timeout=300  # 5 minute timeout per script
            )
            
            if result.returncode == 0:
                print(f"✅ SUCCESS: {description} completed")
                return True
            else:
                print(f"❌ ERROR: {description} failed with exit code {result.returncode}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"⏰ TIMEOUT: {description} took too long (>5 minutes)")
            return False
        except Exception as e:
            print(f"💥 EXCEPTION: {description} failed with error: {e}")
            return False
    
    def _update_script_config(self, script_path: str, replacements: Dict[str, str]) -> bool:
        """Temporarily update script configuration"""
        try:
            with open(script_path, 'r') as f:
                content = f.read()
            
            # Create backup
            backup_path = f"{script_path}.backup"
            shutil.copy2(script_path, backup_path)
            
            # Apply replacements
            modified_content = content
            for old_pattern, new_value in replacements.items():
                modified_content = modified_content.replace(old_pattern, new_value)
            
            # Write modified content
            with open(script_path, 'w') as f:
                f.write(modified_content)
                
            return True
            
        except Exception as e:
            print(f"❌ Failed to update {script_path}: {e}")
            return False
    
    def _restore_script_config(self, script_path: str):
        """Restore original script configuration"""
        backup_path = f"{script_path}.backup"
        if Path(backup_path).exists():
            try:
                shutil.move(backup_path, script_path)
            except Exception as e:
                print(f"⚠️ Warning: Failed to restore {script_path}: {e}")
    
    def _check_prerequisites(self) -> bool:
        """Check if all required files and dependencies exist"""
        print("🔍 Checking prerequisites...")
        
        # Check resume file
        if not self.resume_path.exists():
            print(f"❌ Resume file not found: {self.resume_path}")
            return False
        print(f"✅ Resume found: {self.resume_path}")
        
        # Check pipeline scripts
        scripts = [
            "a1_page_judger.py",
            "a2_resume_parser_gemini.py", 
            "a3_cover_letter_and_summary.py",
            "a4_enhanced_form_extractor.py",
            "a5_form_answer_gemini.py",
            "a6_complete_skipped_fields.py",
            "a7_fill_form_resume.py"
        ]
        
        for script in scripts:
            if not Path(script).exists():
                print(f"❌ Pipeline script not found: {script}")
                return False
        print(f"✅ All {len(scripts)} pipeline scripts found")
        
        # Check Python executable
        if not Path(self.python_executable).exists():
            print(f"❌ Python executable not found: {self.python_executable}")
            return False
        print(f"✅ Python executable: {self.python_executable}")
        
        # Check for .env file (optional but recommended)
        if not Path(".env").exists():
            print("⚠️ Warning: .env file not found. Make sure GEMINI_API_KEY is set in environment")
        else:
            print("✅ .env file found")
            
        return True
    
    def run_pipeline(self) -> bool:
        """Execute the complete pipeline"""
        start_time = datetime.now()
        print(f"\n🎯 Starting Job Application Pipeline")
        print(f"🔗 Target URL: {self.job_url}")
        print(f"📄 Resume: {self.resume_path}")
        print(f"📅 Started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        if not self._check_prerequisites():
            print("❌ Prerequisites check failed. Pipeline aborted.")
            return False
        
        success = True
        
        # Step 1: Page Judger - Analyze job page and navigate to form
        if success:
            # Update URL in a1_page_judger.py
            replacements = {
                'START_URL = "https://job-boards.greenhouse.io/gomotive/jobs/8137073002?gh_src=my.greenhouse.search"': 
                f'START_URL = "{self.job_url}"'
            }
            self._update_script_config("a1_page_judger.py", replacements)
            
            success = self._run_script(
                "a1_page_judger.py",
                "Analyzing job page and finding application form"
            )
            self._restore_script_config("a1_page_judger.py")
        
        # Step 2: Resume Parser - Parse resume with Gemini AI
        if success:
            success = self._run_script(
                "a2_resume_parser_gemini.py", 
                "Parsing resume using Gemini AI"
            )
        
        # Step 3: Cover Letter Generator - Create cover letter and job summary
        if success:
            # Update URL in a3_cover_letter_and_summary.py
            replacements = {
                'JOB_URL        = "https://job-boards.greenhouse.io/gomotive/jobs/8137073002?gh_src=my.greenhouse.search"':
                f'JOB_URL        = "{self.job_url}"'
            }
            self._update_script_config("a3_cover_letter_and_summary.py", replacements)
            
            success = self._run_script(
                "a3_cover_letter_and_summary.py",
                "Generating cover letter and job summary"
            )
            self._restore_script_config("a3_cover_letter_and_summary.py")
        
        # Step 4: Enhanced Form Extractor - Extract form fields with AI analysis
        if success:
            # Update URL in a4_enhanced_form_extractor.py
            replacements = {
                'JOB_URL = "https://job-boards.greenhouse.io/gomotive/jobs/8137073002?gh_src=my.greenhouse.search"':
                f'JOB_URL = "{self.job_url}"'
            }
            self._update_script_config("a4_enhanced_form_extractor.py", replacements)
            
            success = self._run_script(
                "a4_enhanced_form_extractor.py",
                "Extracting form fields with technical + AI analysis"
            )
            self._restore_script_config("a4_enhanced_form_extractor.py")
        
        # Step 5: Form Answer Generator - Generate answers using AI
        if success:
            success = self._run_script(
                "a5_form_answer_gemini.py",
                "Generating form answers using Gemini AI"
            )
        
        # Step 6: Complete Skipped Fields - Interactive completion
        if success:
            success = self._run_script(
                "a6_complete_skipped_fields.py",
                "Interactive completion of skipped fields"
            )
        
        # Step 7: Form Filler - Automated form filling and submission
        if success:
            # Update URL and settings in a7_fill_form_resume.py
            replacements = {
                'JOB_URL = "https://job-boards.greenhouse.io/gomotive/jobs/8137073002?gh_src=my.greenhouse.search"':
                f'JOB_URL = "{self.job_url}"',
                'HEADLESS = False': f'HEADLESS = {self.headless}',
                'REQUIRE_APPROVAL = True': f'REQUIRE_APPROVAL = {not self.auto_submit}'
            }
            self._update_script_config("a7_fill_form_resume.py", replacements)
            
            env_vars = {
                "RESUME_FILE": str(self.resume_path.absolute())
            }
            
            success = self._run_script(
                "a7_fill_form_resume.py",
                "Automated form filling and submission",
                env_vars
            )
            self._restore_script_config("a7_fill_form_resume.py")
        
        # Pipeline completion summary
        end_time = datetime.now()
        duration = end_time - start_time
        
        print(f"\n{'='*60}")
        if success:
            print("🎉 PIPELINE COMPLETED SUCCESSFULLY!")
            print(f"✅ Job application process completed for: {self.job_url}")
        else:
            print("💥 PIPELINE FAILED!")
            print("❌ Job application process incomplete")
        
        print(f"⏱️ Total duration: {duration}")
        print(f"📁 All outputs saved to: {OUTPUT_BASE}")
        print(f"📅 Completed: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        # Show output summary
        self._show_output_summary()
        
        return success
    
    def _show_output_summary(self):
        """Show summary of generated outputs"""
        print(f"\n📋 OUTPUT SUMMARY:")
        print(f"📁 Base directory: {OUTPUT_BASE}")
        
        output_files = [
            ("📊 Data Files:", [
                ("Parsed Resume", OutputPaths.PARSED_RESUME),
                ("Form Fields", OutputPaths.FORM_FIELDS_ENHANCED),
                ("AI Answers", OutputPaths.FILLED_ANSWERS),
                ("User Answers", OutputPaths.USER_COMPLETED_ANSWERS),
                ("Skipped Fields", OutputPaths.SKIPPED_FIELDS),
            ]),
            ("📄 Documents:", [
                ("Job Summary", OutputPaths.JOB_SUMMARY),
                ("Cover Letter", OutputPaths.COVER_LETTER),
                ("Job Page", OutputPaths.JOB_PAGE_MD),
            ]),
            ("📸 Media:", [
                ("Screenshots", OutputPaths.SCREENSHOTS_DIR),
                ("Videos", OutputPaths.VIDEOS_DIR),
            ]),
            ("📝 Logs:", [
                ("Page Analysis", OutputPaths.PAGE_JUDGER_OUT),
                ("Form URL", OutputPaths.RESOLVED_FORM_URL),
            ])
        ]
        
        for section_name, files in output_files:
            print(f"\n{section_name}")
            for desc, path in files:
                if Path(path).exists():
                    if Path(path).is_file():
                        size = Path(path).stat().st_size
                        print(f"  ✅ {desc}: {path} ({size} bytes)")
                    else:
                        count = len(list(Path(path).iterdir())) if Path(path).is_dir() else 0
                        print(f"  ✅ {desc}: {path} ({count} files)")
                else:
                    print(f"  ❌ {desc}: {path} (not found)")


def main():
    """
    Hardcoded configuration - edit these values as needed:
    """
    # ========== CONFIGURATION ==========
    job_url = "https://job-boards.greenhouse.io/hackerrank/jobs/7211528?gh_jid=7211528&gh_src=1836e8621us"
    resume_path = "./data/Geetansh_resume.pdf"
    headless_mode = True   # Set to False to see browser GUI
    auto_submit = False   # Set to True to auto-submit without confirmation
    # ===================================
    
    print("🚀 Starting Job Application Pipeline")
    print(f"📋 Job URL: {job_url}")
    print(f"📄 Resume: {resume_path}")
    print(f"🖥️  Headless Mode: {headless_mode}")
    print(f"⚡ Auto Submit: {auto_submit}")
    print("=" * 60)
    
    # Create and run pipeline
    pipeline = PipelineRunner(
        job_url=job_url,
        resume_path=resume_path,
        headless=headless_mode,
        auto_submit=auto_submit
    )
    
    success = pipeline.run_pipeline()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()