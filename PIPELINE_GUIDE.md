# Job Application Pipeline Runner - Quick Reference

## Overview
The `pipeline_runner.py` orchestrates the complete job application automation process from job URL to form submission.

## Pipeline Flow
```
1. a1_page_judger.py          → Analyze job page & find application form
2. a2_resume_parser_gemini.py → Parse resume using Gemini AI  
3. a3_cover_letter_and_summary.py → Generate cover letter & job summary
4. a4_enhanced_form_extractor.py → Extract form fields (technical + AI)
5. a5_form_answer_gemini.py   → Generate form answers using AI
6. a6_complete_skipped_fields.py → Interactive completion of skipped fields
7. a7_fill_form_resume.py     → Automated form filling & submission
```

## Usage

### Basic Usage
```bash
# Run with default settings (will prompt for manual approval)
python pipeline_runner.py --url "https://company.com/jobs/12345"
```

### Advanced Usage
```bash
# Run headless (no browser GUI) with auto-submit
python pipeline_runner.py --url "https://company.com/jobs/12345" --headless --auto-submit

# Use custom resume file
python pipeline_runner.py --url "https://company.com/jobs/12345" --resume "/path/to/my-resume.pdf"

# Combined options
python pipeline_runner.py \
  --url "https://company.com/jobs/12345" \
  --resume "/path/to/resume.pdf" \
  --headless \
  --auto-submit
```

## Prerequisites

### Required Files
- ✅ All pipeline scripts (a1-a7) in same directory
- ✅ Resume file at `./data/resume.pdf` (or custom path)
- ✅ Virtual environment with dependencies installed
- ✅ `.env` file with `GEMINI_API_KEY` (or environment variable)

### Optional Files
- 📄 `Supplemental-context.json` - Additional context for AI responses
- 🔧 Custom configuration in individual scripts

## Outputs Structure
```
outputs/
├── data/
│   ├── parsed_resume.json          # AI-parsed resume data
│   ├── form_fields_enhanced.json   # Extracted form fields
│   ├── filled_answers.json         # AI-generated answers
│   ├── user_completed_answers.json # Final answers (AI + manual)
│   └── skipped_fields.json         # Fields that were skipped
├── documents/
│   ├── job_page.md                 # Job page content
│   ├── job_summary.txt             # Job description summary
│   └── cover_letter.txt            # Generated cover letter
├── screenshots/
│   ├── before_submit_YYYYMMDD_HHMMSS.png
│   └── after_submit_YYYYMMDD_HHMMSS.png
├── videos/
│   └── *.webm                      # Screen recordings
└── logs/
    ├── page_judger_out.json        # Page analysis results
    ├── resolved_form_url.txt       # Final form URL
    └── form_page_reached.txt       # Success status
```

## Configuration Options

### Command Line Arguments
- `--url`: Job application URL (required)
- `--resume`: Path to resume PDF file
- `--headless`: Run browser without GUI
- `--auto-submit`: Skip manual approval for form submission

### Environment Variables
- `GEMINI_API_KEY`: Required for AI processing
- `RESUME_FILE`: Override resume path for a7_fill_form_resume.py

## Error Handling

### Common Issues
1. **Missing .env file**: Set `GEMINI_API_KEY` in environment
2. **Script not found**: Ensure all a1-a7 scripts are present
3. **Resume not found**: Check path or use `--resume` parameter
4. **Permission denied**: Ensure Python executable is accessible

### Pipeline Recovery
- Pipeline stops at first failed step
- Outputs from successful steps are preserved
- Can manually run individual scripts from failure point
- Check logs in `outputs/logs/` for debugging

## Manual Intervention Points

### Interactive Steps
1. **a6_complete_skipped_fields.py**: 
   - Prompts for manual input on fields AI couldn't fill
   - Can press Enter to skip fields
   - Previous answers are remembered

2. **a7_fill_form_resume.py**:
   - Shows form before submission (unless `--auto-submit`)
   - Requires 'y' confirmation to submit
   - Takes screenshots before/after submission

### Monitoring Progress
- Real-time output shows current step and status
- Each step shows ✅ success or ❌ failure
- Final summary shows all generated outputs
- Duration tracking for performance monitoring

## Examples

### Development/Testing
```bash
# Run with visible browser for debugging
python pipeline_runner.py --url "https://company.com/jobs/12345"
```

### Production/Automated
```bash
# Run completely automated
python pipeline_runner.py \
  --url "https://company.com/jobs/12345" \
  --headless \
  --auto-submit
```

### Custom Resume
```bash
# Use different resume for specific application
python pipeline_runner.py \
  --url "https://company.com/jobs/12345" \
  --resume "./resumes/software-engineer-resume.pdf"
```

## Troubleshooting

### Debugging Individual Steps
```bash
# Run individual script manually for debugging
source autofill/bin/activate
python a1_page_judger.py  # etc.
```

### Checking Outputs
```bash
# View generated data
cat outputs/data/parsed_resume.json | jq .
cat outputs/documents/job_summary.txt
ls -la outputs/screenshots/
```

### Log Analysis
```bash
# Check pipeline logs
cat outputs/logs/page_judger_out.json | jq .
cat outputs/logs/resolved_form_url.txt
```