#!/usr/bin/env python3
"""
Universal warning suppression for the entire pipeline
"""
import os
import sys
import warnings

# Comprehensive warning suppression
os.environ['PYTHONWARNINGS'] = 'ignore'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['GRPC_VERBOSITY'] = 'ERROR'
os.environ['GRPC_TRACE'] = ''
os.environ['GOOGLE_CLOUD_DISABLE_GRPC_FOR_REST'] = 'true'
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = ''

# Python warnings
warnings.filterwarnings('ignore')

# Suppress specific library warnings
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning)

# Extreme solution: capture stderr temporarily
class WarningFilter:
    def __init__(self):
        self.original_stderr = sys.stderr
        
    def __enter__(self):
        sys.stderr = open(os.devnull, 'w')
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stderr.close()
        sys.stderr = self.original_stderr

print("✅ Warning suppression configured")