#!/usr/bin/env python3
"""
Debug script to identify exact warnings
"""
import os
import sys

# Temporarily remove suppression to see warnings
if 'GRPC_VERBOSITY' in os.environ:
    del os.environ['GRPC_VERBOSITY']
if 'GRPC_TRACE' in os.environ:
    del os.environ['GRPC_TRACE']
if 'TF_CPP_MIN_LOG_LEVEL' in os.environ:
    del os.environ['TF_CPP_MIN_LOG_LEVEL']

print("=== Testing imports that might cause warnings ===")

print("1. Testing dotenv...")
try:
    from dotenv import load_dotenv
    print("✅ dotenv imported successfully")
except Exception as e:
    print(f"❌ dotenv error: {e}")

print("\n2. Testing google.generativeai...")
try:
    import google.generativeai as genai
    print("✅ google.generativeai imported successfully")
except Exception as e:
    print(f"❌ google.generativeai error: {e}")

print("\n3. Testing crawl4ai...")
try:
    from crawl4ai import AsyncWebCrawler
    print("✅ crawl4ai imported successfully")
except Exception as e:
    print(f"❌ crawl4ai error: {e}")

print("\n4. Testing playwright...")
try:
    from playwright.sync_api import sync_playwright
    print("✅ playwright imported successfully")
except Exception as e:
    print(f"❌ playwright error: {e}")

print("\n=== Testing actual API call ===")
try:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        print("✅ Gemini model initialized successfully")
    else:
        print("⚠️ No GEMINI_API_KEY found")
except Exception as e:
    print(f"❌ Gemini initialization error: {e}")