# 🤖 Job Application Autofill Bot

An intelligent Telegram bot that automates job application form filling using AI-powered resume parsing, cover letter generation, and smart form completion.

## ✨ Features

### 🎯 Core Functionality
- **Automated Form Filling**: Automatically detects and fills job application forms using Playwright
- **AI-Powered Resume Parsing**: Extracts information from PDF resumes using Google Gemini AI
- **Smart Cover Letter Generation**: Creates tailored, concise cover letters (120-150 words) based on job description
- **Intelligent Form Detection**: Analyzes web pages to determine if they contain application forms
- **Multi-Step Pipeline**: Orchestrated workflow from job link to submitted application

### 💬 Telegram Bot Interface
- **Interactive Conversation Flow**: User-friendly step-by-step guidance
- **Real-time Progress Updates**: Live feedback during each pipeline stage
- **Screenshot Sharing**: Visual confirmation of filled forms before submission
- **Smart Approval System**: Multiple approval checkpoints for user control

### 🔄 Advanced Workflow Control

#### 1. **Post-Generation Approval**
After generating cover letter and job summary, bot asks for approval to continue:
- Review AI-generated content before proceeding
- Option to stop the process if job isn't suitable
- Saves time by not processing unwanted applications

#### 2. **Comprehensive Q&A Review**
Before form submission, displays ALL form fields and answers:
- Shows both auto-generated AND user-provided answers
- Numbered list format for easy reference
- Clear distinction between filled and unfilled fields

#### 3. **Natural Language Modifications**
Edit answers using intuitive commands:
- **Single change**: `"change question 2 to yes"`
- **Multiple changes**: `"question 2 to yes, question 3 to no, question 4 to maybe"`
- **Flexible formats**:
  - `"modify <old answer> to <new answer>"`
  - `"update <field name> to <new value>"`
  - `"change q1 to abc and q5 to xyz"`

#### 4. **Automatic Submission**
After Q&A approval, form is automatically filled and submitted:
- No additional confirmation needed
- Streamlined workflow
- After-submit screenshot sent for verification

### 🧠 Smart Field Handling
- **Automatic Answer Generation**: AI fills most fields using resume data
- **Skipped Field Detection**: Identifies fields that need user input
- **Interactive Question Flow**: Asks user for missing information one by one
- **Answer Merging**: Seamlessly combines AI-generated and user-provided answers
- **Context-Aware Responses**: Uses supplemental context (work authorization, location preferences, etc.)

### 🎨 User Experience
- **Short, Concise Cover Letters**: 120-150 words max, direct and impactful
- **Progress Indicators**: Real-time status updates during processing
- **Error Handling**: Graceful failure recovery with helpful messages
- **Session Management**: Maintains user state across conversation
- **Flexible Input**: Accepts various answer formats and natural language

## 🏗️ Architecture

### Pipeline Stages

```
Job URL → Page Analysis → Resume Parsing → Cover Letter Generation
    ↓
[Approval Checkpoint #1]
    ↓
Form Extraction → Answer Generation → User Questions (if needed)
    ↓
[Approval Checkpoint #2 - Review ALL Answers]
    ↓
Form Filling → Auto-Submit → Screenshot Confirmation
```

### Key Components

| Script | Purpose |
|--------|---------|
| `telegram_bot.py` | Main bot interface and conversation handler |
| `a1_page_judger.py` | Analyzes if URL contains job application form |
| `a2_resume_parser_gemini.py` | Extracts structured data from resume PDF |
| `a3_cover_letter_and_summary.py` | Generates cover letter and job summary |
| `a4_enhanced_form_extractor.py` | Extracts form fields using Playwright |
| `a5_form_answer_gemini.py` | AI-powered answer generation for forms |
| `a7_fill_form_resume.py` | Fills and submits forms using Playwright |
| `llm_parser.py` | Natural language parsing for answer modifications |
| `output_config.py` | Centralized output paths and configuration |
| `utils.py` | Shared utility functions |

## 🚀 Getting Started

### Prerequisites
- Python 3.11+
- Telegram Bot Token (from @BotFather)
- Google Gemini API Key
- macOS/Linux/Windows

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/geetansshh/Job-Autofill.git
cd Job-Autofill
```

2. **Create virtual environment**
```bash
python3 -m venv autofill
source autofill/bin/activate  # On Windows: autofill\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Set up environment variables**
```bash
export GEMINI_API_KEY="your-gemini-api-key"
export TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
```

5. **Configure your resume**
Place your resume PDF in `data/` folder and update `output_config.py`:
```python
RESUME_PATH = OUTPUT_BASE.parent / "data" / "Your_Resume.pdf"
```

6. **Set up supplemental context**
Edit `Supplemental-context.json` with your details:
```json
{
  "candidate_status": "Current student; seeking an internship",
  "availability_start": "Immediate",
  "work_authorization": {
    "country": "India",
    "has_valid_permit": true
  },
  "preferred_locations_ordered": ["Bengaluru", "Pune", "Hyderabad"],
  "willing_to_relocate": true,
  "Country_code": "+91"
}
```

### Running the Bot

```bash
python telegram_bot.py
```

Or run individual scripts:
```bash
# Run full pipeline
python pipeline_runner.py

# Run specific steps
python a1_page_judger.py
python a2_resume_parser_gemini.py
# ... etc
```

## 📱 Using the Telegram Bot

1. **Start the bot**: Send `/start` to your bot
2. **Send job URL**: Paste the job application link
3. **Review cover letter**: Bot generates and shows cover letter/summary
4. **Approve to continue**: Reply `yes` to proceed or `no` to stop
5. **Answer questions**: Provide information for fields bot couldn't fill
6. **Review all answers**: Bot shows complete Q&A list
7. **Make modifications** (optional):
   - `"question 2 to yes"`
   - `"question 2 to yes, question 3 to no"`
8. **Approve final answers**: Reply `yes` to auto-submit
9. **Done!**: Bot fills, submits, and sends confirmation screenshot

## 🎯 Example Interactions

### Modifying Single Answer
```
User: change question 2 to yes
Bot: ✅ Updated!
     Question: Are you authorized to work?
     Old answer: Not filled
     New answer: yes
```

### Modifying Multiple Answers
```
User: question 2 to yes, question 3 to no, question 4 to maybe
Bot: ✅ Updated 3 field(s)!
     • Are you authorized to work?: Not filled → yes
     • Will you require sponsorship?: Not filled → no
     • Available for relocation?: Not filled → maybe
```

## 📊 Output Structure

```
outputs/
├── data/
│   ├── filled_answers.json          # All form answers (single source of truth)
│   ├── parsed_resume.json           # Extracted resume data
│   ├── form_fields_enhanced.json    # Extracted form fields
│   ├── skipped_fields.json          # Fields needing user input
│   └── user_completed_answers.json  # User-provided answers
├── documents/
│   ├── cover_letter.txt             # Generated cover letter
│   ├── job_summary.txt              # Job description summary
│   └── job_page.md                  # Scraped job page content
├── logs/
│   ├── page_judger_out.json         # Page analysis results
│   ├── resolved_form_url.txt        # Final form URL
│   └── form_page_reached.txt        # Navigation confirmation
└── screenshots/
    ├── before_submit_*.png          # Form preview
    └── after_submit_*.png           # Submission confirmation
```

## 🔧 Command-Line Flags

### a7_fill_form_resume.py
```bash
# Fill form but don't submit
python a7_fill_form_resume.py --no-submit

# Fill and auto-submit (no approval prompt)
python a7_fill_form_resume.py --no-approval

# Fill only, no submission, no approval prompt
python a7_fill_form_resume.py --no-submit --no-approval
```

## 🛠️ Technologies Used

- **Telegram Bot API**: python-telegram-bot 20.0b0
- **AI/ML**: Google Gemini 2.5 Flash Lite
- **Web Automation**: Playwright (Chromium)
- **Web Scraping**: Crawl4AI 0.7.4
- **PDF Processing**: pdfplumber
- **Language**: Python 3.11

## 📈 Key Improvements (Recent Updates)

### October 12, 2025
- ✅ **Shortened cover letters** to 120-150 words (previously ~200)
- ✅ **Added approval checkpoint** after cover letter generation
- ✅ **Show ALL Q&A for review** (not just user-answered fields)
- ✅ **Natural language modifications** with multi-field support
- ✅ **Auto-submit after Q&A approval** (removed redundant submission prompt)
- ✅ **Multiple field modifications** in single message
- ✅ **Fixed AttributeError** with form field parsing
- ✅ **Improved error handling** and user feedback

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- Google Gemini API for AI-powered content generation
- Playwright team for robust browser automation
- python-telegram-bot for excellent Telegram integration
- Crawl4AI for efficient web scraping

## 📧 Contact

For questions or feedback, please open an issue on GitHub.

---

**Made with ❤️ for automating the tedious parts of job hunting**
