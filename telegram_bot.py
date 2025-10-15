#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot for Job Application Autofill Pipeline

- Accepts job link via chat
- Runs the pipeline for that link
- Asks for user input via chat (for skipped fields)
- Sends screenshots and outputs back to the user

Instructions:
1. Set your Telegram bot token in the TELEGRAM_BOT_TOKEN environment variable or .env file.
2. Install dependencies: pip install python-telegram-bot==20.0b0
3. Run this script: python telegram_bot.py
"""

import os
import logging
import json
import asyncio
import subprocess
from pathlib import Path
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from output_config import OutputPaths, OUTPUT_BASE
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

TELEGRAM_BOT_TOKEN = "8412902281:AAEOidJIU1Nw1pHFa-e9JAQSZgCnhTGG8y0"

# States for ConversationHandler
WAITING_FOR_LINK, WAITING_FOR_A3_APPROVAL, WAITING_FOR_FIELD_ANSWER, WAITING_FOR_QA_APPROVAL = range(4)
# WAITING_FOR_SUBMIT_APPROVAL = 4  # 🗑️ Commented out - no longer needed after Q&A approval

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Store user sessions in memory (for demo)
user_sessions = {}

# Files for interactive communication
TELEGRAM_QUESTIONS_FILE = OUTPUT_BASE / "telegram_questions.json"
TELEGRAM_ANSWERS_FILE = OUTPUT_BASE / "telegram_answers.json"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Welcome to Job Application Autofill Bot!**\n\n"
        "I can help you automatically fill job application forms.\n\n"
        "📝 **How to use:**\n"
        "1. Send me a job application link\n"
        "2. I'll analyze the page and parse your resume\n"
        "3. Review the generated cover letter\n"
        "4. Answer any questions I can't fill automatically\n"
        "5. Review all answers and make changes if needed\n"
        "6. I'll fill and submit the form!\n\n"
        "💡 Use /help for more details or just send a job link to start!"
    )
    return WAITING_FOR_LINK

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **Help & Usage Guide**\n\n"
        "**Commands:**\n"
        "• /start - Begin the application process\n"
        "• /help - Show this help message\n"
        "• /cancel - Cancel current operation\n\n"
        "**How it works:**\n"
        "1️⃣ Send a job application URL\n"
        "2️⃣ Review AI-generated cover letter (approve/stop)\n"
        "3️⃣ Answer questions for fields I couldn't fill\n"
        "4️⃣ Review ALL answers (auto + yours)\n"
        "5️⃣ Make changes using natural language:\n"
        "   • 'question 2 to yes'\n"
        "   • 'q2 to yes, q3 to no, q4 to maybe'\n"
        "6️⃣ Approve and I'll auto-submit!\n\n"
        "**Features:**\n"
        "✅ AI resume parsing\n"
        "✅ Auto cover letter generation (120-150 words)\n"
        "✅ Smart form filling\n"
        "✅ Multiple answer modifications at once\n"
        "✅ Screenshot confirmations\n\n"
        "Ready? Just send me a job link! 🚀"
    )
    return WAITING_FOR_LINK

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
    await update.message.reply_text(
        "❌ Operation cancelled.\n\n"
        "Use /start to begin a new application."
    )
    return ConversationHandler.END

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from output_config import RESUME_PATH
    import subprocess
    
    job_url = update.message.text.strip()
    user_id = update.effective_user.id
    resume_path = str(RESUME_PATH)  # Use centralized resume path
    
    # Find python executable
    python_executable = "python3"
    venv_python = Path("autofill/bin/python")
    if venv_python.exists():
        python_executable = str(venv_python.absolute())
    
    # Store session
    user_sessions[user_id] = {
        "job_url": job_url, 
        "resume_path": resume_path,
        "python_executable": python_executable,
        "current_question_index": 0,
        "questions": []
    }
    
    await update.message.reply_text(f"🔗 Got your link! Running the pipeline for: {job_url}\nPlease wait...")
    
    # Clear previous question/answer files
    if TELEGRAM_QUESTIONS_FILE.exists():
        TELEGRAM_QUESTIONS_FILE.unlink()
    if TELEGRAM_ANSWERS_FILE.exists():
        TELEGRAM_ANSWERS_FILE.unlink()
    
    try:
        # Set up environment variables
        env = os.environ.copy()
        env["RESUME_FILE"] = resume_path
        env["JOB_URL"] = job_url
        
        # Run steps 1-3 first (page judge, resume parse, cover letter)
        initial_scripts = [
            ("a1_page_judger.py", "Analyzing job page"),
            ("a2_resume_parser_gemini.py", "Parsing resume"),
            ("a3_cover_letter_and_summary.py", "Generating cover letter"),
        ]
        
        for script, description in initial_scripts:
            await update.message.reply_text(f"⚙️ {description}...")
            result = subprocess.run(
                [python_executable, script],
                env=env,
                capture_output=True,
                text=True,
                timeout=180
            )
            
            if result.returncode != 0:
                await update.message.reply_text(f"❌ Failed at {description}:\n{result.stderr[:500]}")
                return ConversationHandler.END
        
        # 🆕 SEND COVER LETTER & JOB SUMMARY AND ASK FOR APPROVAL
        await send_cover_letter_and_summary(update)
        await update.message.reply_text(
            "✅ Cover letter and job summary generated!\n\n"
            "Would you like to proceed with form extraction and filling?\n"
            "Reply with 'yes' to continue or 'no' to end the process."
        )
        
        return WAITING_FOR_A3_APPROVAL
        
    except Exception as e:
        await update.message.reply_text("❌ Sorry, an unexpected error occurred. Please try again later.")
        logging.exception("Error during pipeline execution")
        return ConversationHandler.END

async def handle_a3_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's approval after cover letter generation"""
    user_id = update.effective_user.id
    session = user_sessions.get(user_id)
    
    if not session:
        await update.message.reply_text("❌ Session expired. Please start over with /start")
        return ConversationHandler.END
    
    response = update.message.text.strip().lower()
    
    if response in ['no', 'n', 'stop', 'cancel']:
        await update.message.reply_text("❌ Process ended. Use /start to begin a new application.")
        return ConversationHandler.END
    
    if response not in ['yes', 'y', 'ok', 'proceed', 'continue']:
        await update.message.reply_text("Please reply with 'yes' to continue or 'no' to end.")
        return WAITING_FOR_A3_APPROVAL
    
    # User approved, continue with steps 4-5
    await update.message.reply_text("🚀 Continuing with form extraction and filling...")
    
    python_executable = session["python_executable"]
    job_url = session["job_url"]
    resume_path = session["resume_path"]
    
    try:
        # Set up environment variables
        env = os.environ.copy()
        env["RESUME_FILE"] = resume_path
        env["JOB_URL"] = job_url
        
        # Run steps 4-5 (form extraction and answer generation)
        remaining_scripts = [
            ("a4_enhanced_form_extractor.py", "Extracting form fields"),
            ("a5_form_answer_gemini.py", "Generating answers"),
        ]
        
        for script, description in remaining_scripts:
            await update.message.reply_text(f"⚙️ {description}...")
            result = subprocess.run(
                [python_executable, script],
                env=env,
                capture_output=True,
                text=True,
                timeout=180
            )
            
            if result.returncode != 0:
                await update.message.reply_text(f"❌ Failed at {description}:\n{result.stderr[:500]}")
                return ConversationHandler.END
        
        # Check if there are skipped fields that need user input
        skipped_fields = []
        if OutputPaths.SKIPPED_FIELDS.exists():
            with open(OutputPaths.SKIPPED_FIELDS, 'r') as f:
                skipped_fields = json.load(f)
        
        if not skipped_fields:
            # No skipped fields, proceed to form filling with approval
            await update.message.reply_text("✅ No additional information needed! Proceeding to fill the form...")
            return await fill_form_and_ask_approval(update, context, user_id, None)
        
        # Prepare questions for the user
        user_sessions[user_id]["questions"] = skipped_fields
        user_sessions[user_id]["answers"] = {}
        
        await update.message.reply_text(
            f"📝 I need some additional information from you.\n"
            f"Found {len(skipped_fields)} fields that need your input.\n\n"
            f"Let's start! (Type 'skip' to skip any field)"
        )
        
        # Ask first question
        return await ask_next_question(update, context, user_id)
        
    except Exception as e:
        await update.message.reply_text("❌ Sorry, an unexpected error occurred. Please try again later.")
        logging.exception("Error during pipeline execution")
        return ConversationHandler.END

async def ask_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Ask the next skipped field question"""
    session = user_sessions.get(user_id)
    if not session:
        await update.message.reply_text("❌ Session expired. Please start over with /start")
        return ConversationHandler.END
    
    questions = session["questions"]
    index = session["current_question_index"]
    
    if index >= len(questions):
        # All questions answered, save answers and merge into filled_answers.json
        await update.message.reply_text("✅ All questions answered! Merging with auto-filled answers...")
        
        # Load existing filled_answers.json
        filled_answers = {}
        if OutputPaths.FILLED_ANSWERS.exists():
            with open(OutputPaths.FILLED_ANSWERS, 'r') as f:
                filled_answers = json.load(f)
        
        # Merge user answers (convert from wrapped to flat format)
        for q in questions:
            field_id = q.get("id")
            if field_id in session["answers"]:
                # Store in flat format: {id: value}
                filled_answers[field_id] = session["answers"][field_id]
        
        # Write merged answers back to filled_answers.json
        with open(OutputPaths.FILLED_ANSWERS, 'w') as f:
            json.dump(filled_answers, f, indent=2)
        
        # Also save to user_completed_answers.json for reference (in wrapped format)
        answers_data = {}
        for q in questions:
            field_id = q.get("id")
            if field_id in session["answers"]:
                answers_data[field_id] = {
                    "question": q.get("question", ""),
                    "answer": session["answers"][field_id]
                }
        
        with open(OutputPaths.USER_COMPLETED_ANSWERS, 'w') as f:
            json.dump(answers_data, f, indent=2)
        
        await update.message.reply_text(f"✅ Merged! Total fields: {len(filled_answers)}")
        
        # 🆕 Show all Q&A to user and ask for approval
        await show_all_qa_and_ask_approval(update, context, user_id)
        return WAITING_FOR_QA_APPROVAL
    
    # Ask current question
    question = questions[index]
    question_text = question.get("question", "Unknown field")
    field_id = question.get("id", "")
    
    message = f"❓ Question {index + 1}/{len(questions)}:\n\n"
    message += f"**{question_text}**\n\n"
    
    # Show options if available
    if question.get("options"):
        message += "Options:\n"
        for i, opt in enumerate(question.get("options", [])[:10], 1):
            option_label = opt.get("label", opt) if isinstance(opt, dict) else opt
            message += f"{i}. {option_label}\n"
        message += "\nYou can reply with the number or type your answer.\n"
    
    message += "\n💡 Reply with 'skip' to skip this field."
    
    await update.message.reply_text(message)
    return WAITING_FOR_FIELD_ANSWER

async def show_all_qa_and_ask_approval(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Display all Q&A pairs (both auto-generated AND user-answered) and ask for approval"""
    session = user_sessions.get(user_id)
    if not session:
        await update.message.reply_text("❌ Session expired. Please start over with /start")
        return ConversationHandler.END
    
    # Load ALL answers from filled_answers.json (includes both auto-generated + user answers)
    all_answers = {}
    if OutputPaths.FILLED_ANSWERS.exists():
        with open(OutputPaths.FILLED_ANSWERS, 'r') as f:
            all_answers = json.load(f)
    
    # Load form fields to get the question text for each field
    all_form_fields = []
    if OutputPaths.FORM_FIELDS_ENHANCED.exists():
        with open(OutputPaths.FORM_FIELDS_ENHANCED, 'r') as f:
            data = json.load(f)
            # Check if it's a dict with "fields" key or a direct list
            if isinstance(data, dict) and "fields" in data:
                all_form_fields = data["fields"]
            elif isinstance(data, list):
                all_form_fields = data
            else:
                all_form_fields = []
    
    # Build Q&A summary message showing ALL fields
    message = "📋 **Review ALL Answers (Auto-generated + Your Answers):**\n\n"
    
    for i, field in enumerate(all_form_fields, 1):
        # Handle both dict and string field formats
        if isinstance(field, str):
            field_id = field
            question_text = field
        else:
            field_id = field.get("question_id", "") or field.get("id", "")
            question_text = field.get("question", "") or field.get("label", "") or field_id
        
        answer = all_answers.get(field_id, "Not filled")
        
        # Truncate long answers for readability
        answer_display = str(answer)[:100] + "..." if len(str(answer)) > 100 else str(answer)
        
        message += f"{i}. **Q:** {question_text}\n"
        message += f"   **A:** {answer_display}\n\n"
        
        # Telegram has message limit, split if too long
        if len(message) > 3500:
            await update.message.reply_text(message)
            message = ""
    
    if message:
        await update.message.reply_text(message)
    
    # Store all form fields in session for modification parsing
    session["all_form_fields"] = all_form_fields
    session["all_answers"] = all_answers
    
    await update.message.reply_text(
        "✅ Do these answers look correct?\n\n"
        "Reply with:\n"
        "• 'yes' to proceed with form filling\n"
        "• 'change question 2 to new value' to modify an answer\n"
        "• 'no change <old answer> to <new answer>' to update"
    )

async def handle_qa_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's approval/modification request for Q&A"""
    from llm_parser import parse_modification_request, parse_multiple_modifications
    
    user_id = update.effective_user.id
    session = user_sessions.get(user_id)
    
    if not session:
        await update.message.reply_text("❌ Session expired. Please start over with /start")
        return ConversationHandler.END
    
    response = update.message.text.strip()
    response_lower = response.lower()
    
    # Check if user approves
    if response_lower in ['yes', 'y', 'ok', 'approve', 'looks good', 'correct']:
        await update.message.reply_text("✅ Great! Proceeding to fill the form...")
        return await fill_form_and_ask_approval(update, context, user_id, None)
    
    # Get all form fields and answers for modification
    all_form_fields = session.get("all_form_fields", [])
    all_answers = session.get("all_answers", {})
    
    # 🆕 Try to parse as MULTIPLE modifications first (e.g., "question 2 to yes, question 3 to no")
    modifications = parse_multiple_modifications(
        response,
        all_form_fields,
        all_answers
    )
    
    if modifications:
        # Apply all modifications
        changes_summary = []
        
        for mod in modifications:
            field_id = mod["field_id"]
            old_value = mod["old_value"]
            new_value = mod["new_value"]
            question_text = mod["question"]
            
            # Update filled_answers.json
            if OutputPaths.FILLED_ANSWERS.exists():
                with open(OutputPaths.FILLED_ANSWERS, 'r') as f:
                    filled_answers = json.load(f)
                filled_answers[field_id] = new_value
                with open(OutputPaths.FILLED_ANSWERS, 'w') as f:
                    json.dump(filled_answers, f, indent=2)
            
            # Update session
            session["all_answers"][field_id] = new_value
            if "answers" in session and field_id in session["answers"]:
                session["answers"][field_id] = new_value
            
            changes_summary.append(f"• {question_text}: {old_value} → {new_value}")
        
        # Confirm all changes
        summary_text = "\n".join(changes_summary)
        await update.message.reply_text(
            f"✅ Updated {len(modifications)} field(s)!\n\n{summary_text}\n\n"
            f"Would you like to make more changes, or approve and proceed?"
        )
        
        # Show updated Q&A again
        await show_all_qa_and_ask_approval(update, context, user_id)
        return WAITING_FOR_QA_APPROVAL
    
    # If multiple parsing didn't work, try SINGLE modification
    modification = parse_modification_request(
        response,
        all_form_fields,
        all_answers
    )
    
    if modification:
        # Apply the modification
        field_id = modification["field_id"]
        old_value = modification["old_value"]
        new_value = modification["new_value"]
        question_text = modification["question"]
        
        # Update filled_answers.json
        if OutputPaths.FILLED_ANSWERS.exists():
            with open(OutputPaths.FILLED_ANSWERS, 'r') as f:
                filled_answers = json.load(f)
            filled_answers[field_id] = new_value
            with open(OutputPaths.FILLED_ANSWERS, 'w') as f:
                json.dump(filled_answers, f, indent=2)
        
        # Update session
        session["all_answers"][field_id] = new_value
        if "answers" in session and field_id in session["answers"]:
            session["answers"][field_id] = new_value
        
        # Confirm the change
        await update.message.reply_text(
            f"✅ Updated!\n\n"
            f"**Question:** {question_text}\n"
            f"**Old answer:** {old_value}\n"
            f"**New answer:** {new_value}\n\n"
            f"Would you like to make more changes, or approve and proceed?"
        )
        
        # Show updated Q&A again
        await show_all_qa_and_ask_approval(update, context, user_id)
        return WAITING_FOR_QA_APPROVAL
    
    # Check if user wants to make changes but format wasn't recognized
    if any(word in response_lower for word in ['no', 'change', 'modify', 'edit', 'update']):
        await update.message.reply_text(
            "❓ I didn't understand the modification request.\n\n"
            "Try one of these formats:\n"
            "• 'change question 2 to new value'\n"
            "• 'question 2 to yes, question 3 to no' (multiple changes)\n"
            "• 'modify <old answer> to <new answer>'\n"
            "• 'update <field name> to <new value>'\n\n"
            "Or reply 'yes' to proceed with the current answers."
        )
        return WAITING_FOR_QA_APPROVAL
    
    # Invalid response
    await update.message.reply_text("Please reply with 'yes' to continue or use the format 'change X to Y' to modify answers.")
    return WAITING_FOR_QA_APPROVAL

async def handle_field_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's answer to a field question"""
    user_id = update.effective_user.id
    session = user_sessions.get(user_id)
    
    if not session:
        await update.message.reply_text("❌ Session expired. Please start over with /start")
        return ConversationHandler.END
    
    answer = update.message.text.strip()
    questions = session["questions"]
    index = session["current_question_index"]
    
    if index >= len(questions):
        return ConversationHandler.END
    
    question = questions[index]
    field_id = question.get("id")
    
    # Handle skip
    if answer.lower() == 'skip':
        await update.message.reply_text(f"⏭️ Skipped: {question.get('question', 'this field')}")
    else:
        # Handle numeric selection if options exist
        if question.get("options") and answer.isdigit():
            option_index = int(answer) - 1
            options = question.get("options", [])
            if 0 <= option_index < len(options):
                opt = options[option_index]
                answer = opt.get("label", opt) if isinstance(opt, dict) else opt
        
        # Store answer
        session["answers"][field_id] = answer
        await update.message.reply_text(f"✅ Saved: {answer}")
    
    # Move to next question
    session["current_question_index"] += 1
    
    # Ask next question
    return await ask_next_question(update, context, user_id)

async def send_cover_letter_and_summary(update: Update):
    """Send cover letter and job summary immediately after step 3"""
    if os.path.exists(OutputPaths.COVER_LETTER):
        with open(OutputPaths.COVER_LETTER, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            await update.message.reply_text(f"📄 **Cover Letter:**\n\n{text[:4000]}")  # Telegram limit
    
    if os.path.exists(OutputPaths.JOB_SUMMARY):
        with open(OutputPaths.JOB_SUMMARY, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            await update.message.reply_text(f"📝 **Job Summary:**\n\n{text[:4000]}")  # Telegram limit

async def fill_form_and_ask_approval(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, pipeline):
    """Fill the form and automatically submit (no approval needed after Q&A approval)"""
    session = user_sessions.get(user_id)
    if not session:
        await update.message.reply_text("❌ Session expired. Please start over with /start")
        return ConversationHandler.END
    
    # Store pipeline in session for later use
    session["pipeline"] = pipeline
    
    await update.message.reply_text("🚀 Filling and submitting the form now... Please wait...")
    
    # Run step 7 with --no-approval flag to fill form and auto-submit
    import subprocess
    from output_config import OutputPaths
    
    python_exec = session.get("python_executable", "python3")
    
    try:
        # 🆕 Run a7 with ONLY --no-approval flag (it will fill and auto-submit)
        result = subprocess.run(
            [python_exec, "a7_fill_form_resume.py", "--no-approval"],
            capture_output=True,
            text=True,
            timeout=180
        )
        
        if result.returncode != 0:
            await update.message.reply_text(f"❌ Failed to fill/submit the form:\n{result.stderr[:500]}")
            return ConversationHandler.END
            
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱️ Form filling timed out. Please try again.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Error filling form: {e}")
        return ConversationHandler.END
    
    # Send after_submit screenshot
    await update.message.reply_text("✅ Application submitted successfully!")
    await send_after_screenshot(update)
    
    return ConversationHandler.END

# 🗑️ COMMENTED OUT: Submission approval no longer needed after Q&A approval
# async def handle_submit_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Handle user's approval decision"""
#     user_id = update.effective_user.id
#     session = user_sessions.get(user_id)
#     
#     if not session or not session.get("awaiting_approval"):
#         await update.message.reply_text("❌ No pending approval found. Please start over with /start")
#         return ConversationHandler.END
#     
#     answer = update.message.text.strip().lower()
#     
#     if answer in ['yes', 'y', 'हां', 'हाँ']:
#         await update.message.reply_text("✅ Submitting the application...")
#         
#         # Run a7 again but this time with --no-approval to auto-submit
#         import subprocess
#         python_exec = session.get("python_executable", "python3")
#         
#         try:
#             result = subprocess.run(
#                 [python_exec, "a7_fill_form_resume.py", "--no-approval"],
#                 capture_output=True,
#                 text=True,
#                 timeout=180,
#                 env={**os.environ, "AUTO_APPROVE_SUBMIT": "yes"}
#             )
#             
#             if result.returncode != 0:
#                 await update.message.reply_text(f"⚠️ Submission may have failed:\n{result.stderr[:500]}")
#             else:
#                 await update.message.reply_text("✅ Application submitted successfully!")
#                 
#         except Exception as e:
#             await update.message.reply_text(f"❌ Error during submission: {e}")
#         
#         # Send after_submit screenshot
#         await send_after_screenshot(update)
#         
#         await update.message.reply_text("🎉 **Process Complete!** All done. Good luck with your application!")
#         return ConversationHandler.END
#         
#     elif answer in ['no', 'n', 'नहीं']:
#         await update.message.reply_text(
#             "❌ **Submission cancelled!**\n\n"
#             "Thanks for using the bot! 👋"
#         )
#         return ConversationHandler.END
#     else:
#         await update.message.reply_text(
#             "⚠️ Please reply with 'yes' or 'no'\n"
#             "Should I submit the application?"
#         )
#         return WAITING_FOR_SUBMIT_APPROVAL

async def send_before_screenshot(update: Update):
    """Send the before_submit screenshot"""
    import glob
    import imghdr
    
    def is_valid_image(path):
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0 and imghdr.what(path) is not None
        except Exception:
            return False
    
    screenshots_dir = OutputPaths.SCREENSHOTS_DIR
    if os.path.isdir(screenshots_dir):
        before_list = sorted(glob.glob(os.path.join(screenshots_dir, "before_submit*.png")), reverse=True)
        
        if before_list and is_valid_image(before_list[0]):
            try:
                await update.message.reply_text("📸 Sending form preview...")
                with open(before_list[0], 'rb') as photo:
                    await update.message.reply_photo(photo=photo, caption="📸 Form Preview (Before Submit)")
                logging.info(f"✅ Sent before screenshot: {before_list[0]}")
            except Exception as e:
                logging.error(f"❌ Failed to send before screenshot: {e}")
                await update.message.reply_text(f"⚠️ Could not send screenshot: {e}")

async def send_after_screenshot(update: Update):
    """Send the after_submit screenshot"""
    import glob
    import imghdr
    
    def is_valid_image(path):
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0 and imghdr.what(path) is not None
        except Exception:
            return False
    
    screenshots_dir = OutputPaths.SCREENSHOTS_DIR
    if os.path.isdir(screenshots_dir):
        after_list = sorted(glob.glob(os.path.join(screenshots_dir, "after_submit*.png")), reverse=True)
        
        if after_list and is_valid_image(after_list[0]):
            try:
                await update.message.reply_text("📸 Sending confirmation screenshot...")
                with open(after_list[0], 'rb') as photo:
                    await update.message.reply_photo(photo=photo, caption="📸 Submission Confirmed")
                logging.info(f"✅ Sent after screenshot: {after_list[0]}")
            except Exception as e:
                logging.error(f"❌ Failed to send after screenshot: {e}")
                await update.message.reply_text(f"⚠️ Could not send confirmation screenshot: {e}")

async def send_outputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Send cover letter and job summary as text
    if os.path.exists(OutputPaths.COVER_LETTER):
        with open(OutputPaths.COVER_LETTER, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            await update.message.reply_text(f"📄 Cover Letter:\n\n{text}")
    if os.path.exists(OutputPaths.JOB_SUMMARY):
        with open(OutputPaths.JOB_SUMMARY, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            await update.message.reply_text(f"📝 Job Summary:\n\n{text}")
    # Only send before/after screenshots if they exist (i.e., after a7_fill_form_resume.py runs)
    import glob
    import imghdr
    def is_valid_image(path):
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0 and imghdr.what(path) is not None
        except Exception:
            return False

    screenshots_dir = OutputPaths.SCREENSHOTS_DIR
    if os.path.isdir(screenshots_dir):
        before_list = sorted(glob.glob(os.path.join(screenshots_dir, "before_submit*.png")), reverse=True)
        after_list = sorted(glob.glob(os.path.join(screenshots_dir, "after_submit*.png")), reverse=True)
        after_skip_list = sorted(glob.glob(os.path.join(screenshots_dir, "after_skip*.png")), reverse=True)
        
        sent_any = False
        
        # Send before screenshot
        if before_list and is_valid_image(before_list[0]):
            try:
                await update.message.reply_text(f"📸 Sending before screenshot...")
                with open(before_list[0], 'rb') as photo:
                    await update.message.reply_photo(photo=photo, caption="📸 Before Submit")
                sent_any = True
                logging.info(f"✅ Sent before screenshot: {before_list[0]}")
            except Exception as e:
                logging.error(f"❌ Failed to send before screenshot: {e}")
                await update.message.reply_text(f"⚠️ Could not send before screenshot: {e}")
        
        # Send after screenshot
        if after_list and is_valid_image(after_list[0]):
            try:
                await update.message.reply_text(f"📸 Sending after screenshot...")
                with open(after_list[0], 'rb') as photo:
                    await update.message.reply_photo(photo=photo, caption="📸 After Submit")
                sent_any = True
                logging.info(f"✅ Sent after screenshot: {after_list[0]}")
            except Exception as e:
                logging.error(f"❌ Failed to send after screenshot: {e}")
                await update.message.reply_text(f"⚠️ Could not send after screenshot: {e}")
        elif after_skip_list and is_valid_image(after_skip_list[0]):
            try:
                await update.message.reply_text(f"📸 Sending skip screenshot...")
                with open(after_skip_list[0], 'rb') as photo:
                    await update.message.reply_photo(photo=photo, caption="📸 After Skip")
                sent_any = True
                logging.info(f"✅ Sent skip screenshot: {after_skip_list[0]}")
            except Exception as e:
                logging.error(f"❌ Failed to send skip screenshot: {e}")
                await update.message.reply_text(f"⚠️ Could not send skip screenshot: {e}")
        
        if not sent_any:
            await update.message.reply_text("📭 No valid before/after submission screenshots available.")
    await update.message.reply_text("✅ All outputs sent!")

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Set up bot commands menu (appears as button in chat)
    async def post_init(application):
        from telegram import BotCommand
        await application.bot.set_my_commands([
            BotCommand("start", "🚀 Start the bot and begin job application"),
            BotCommand("help", "❓ Show help and usage instructions"),
            BotCommand("cancel", "❌ Cancel current operation"),
        ])
    
    app.post_init = post_init
    
    # Add help and cancel command handlers
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('cancel', cancel_command))
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            WAITING_FOR_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
            WAITING_FOR_A3_APPROVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_a3_approval)],
            WAITING_FOR_FIELD_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_field_answer)],
            WAITING_FOR_QA_APPROVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_qa_approval)],
            # 🗑️ WAITING_FOR_SUBMIT_APPROVAL removed - auto-submit after Q&A approval
        },
        fallbacks=[
            CommandHandler('start', start),
            CommandHandler('cancel', cancel_command)
        ]
    )
    app.add_handler(conv_handler)
    print("🤖 Telegram bot running...")
    print("💡 Bot commands menu has been set up!")
    app.run_polling()

if __name__ == "__main__":
    main()
