#!/usr/bin/env python3
"""
Simple example of running the complete job application pipeline.

This demonstrates the most common usage patterns for the pipeline runner.
"""

from pipeline_runner import PipelineRunner

def example_basic_usage():
    """Basic usage - run with manual approval"""
    print("=== EXAMPLE 1: Basic Usage ===")
    
    job_url = "https://job-boards.greenhouse.io/gomotive/jobs/8137073002"
    
    pipeline = PipelineRunner(job_url=job_url)
    success = pipeline.run_pipeline()
    
    if success:
        print("✅ Job application completed successfully!")
    else:
        print("❌ Job application failed")

def example_automated_usage():
    """Automated usage - headless with auto-submit"""
    print("=== EXAMPLE 2: Automated Usage ===")
    
    job_url = "https://company.com/careers/software-engineer"
    resume_path = "/path/to/custom-resume.pdf"
    
    pipeline = PipelineRunner(
        job_url=job_url,
        resume_path=resume_path,
        headless=True,      # No browser GUI
        auto_submit=True    # Skip manual approval
    )
    
    success = pipeline.run_pipeline()
    
    if success:
        print("✅ Automated job application completed!")
    else:
        print("❌ Automated job application failed")

def example_custom_configuration():
    """Custom configuration for specific needs"""
    print("=== EXAMPLE 3: Custom Configuration ===")
    
    # Multiple job applications with different resumes
    applications = [
        {
            "url": "https://startup.com/jobs/frontend-developer",
            "resume": "./resumes/frontend-resume.pdf",
            "headless": False  # Debug mode
        },
        {
            "url": "https://bigtech.com/jobs/backend-engineer", 
            "resume": "./resumes/backend-resume.pdf",
            "headless": True   # Production mode
        }
    ]
    
    for i, app in enumerate(applications, 1):
        print(f"\n--- Application {i}/{len(applications)} ---")
        
        pipeline = PipelineRunner(
            job_url=app["url"],
            resume_path=app["resume"],
            headless=app["headless"]
        )
        
        success = pipeline.run_pipeline()
        
        if success:
            print(f"✅ Application {i} completed successfully!")
        else:
            print(f"❌ Application {i} failed")
            # Continue with next application

def main():
    """Run examples - uncomment the one you want to test"""
    
    # Basic usage with manual approval
    example_basic_usage()
    
    # Automated usage (uncomment to test)
    # example_automated_usage()
    
    # Multiple applications (uncomment to test)
    # example_custom_configuration()

if __name__ == "__main__":
    main()