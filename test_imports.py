# Test if imports work correctly
try:
    from flask import Flask, jsonify, send_from_directory
    from flask_cors import CORS
    from kubernetes import client, config
    
    print("All imports successful!")
    print(f"Flask version: {Flask.__version__}")
    print(f"Werkzeug version: {Flask.werkzeug_version}")
    
except ImportError as e:
    print(f"Import error: {e}")