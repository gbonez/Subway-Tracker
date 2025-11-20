"""
Utility functions for the NYC Subway Tracker
Contains Playwright setup, browser management, and general helpers
"""
import os
import subprocess
import sys

def install_playwright_browsers():
    """Install Playwright browsers if not in Docker environment"""
    if os.getenv("DOCKER_ENV"):
        print("🐳 Running in Docker, skipping Playwright browser installation")
        return
    
    try:
        print("🎭 Installing Playwright browsers...")
        subprocess.run([
            sys.executable, "-m", "playwright", "install", "chromium"
        ], check=True, capture_output=True, text=True)
        print("✅ Playwright browsers installed successfully")
        
        # Also install system dependencies if needed
        subprocess.run([
            sys.executable, "-m", "playwright", "install-deps"
        ], capture_output=True, text=True)
        
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Failed to install Playwright browsers: {e}")
        print("🔧 You may need to run: python -m playwright install chromium")
    except Exception as e:
        print(f"⚠️ Error during Playwright installation: {e}")

def get_app_port() -> int:
    """Get the port for the application from environment variables"""
    try:
        return int(os.getenv("PORT", "8000"))
    except ValueError:
        print("⚠️ Invalid PORT environment variable, using default 8000")
        return 8000

def log_database_info(database_url: str):
    """Log information about the database being used"""
    if database_url.startswith("postgresql"):
        print("🐘 Using PostgreSQL database from Railway")
    elif database_url.startswith("sqlite"):
        print("🗄️ Using SQLite database for local development")
    else:
        print(f"🤔 Using unknown database: {database_url}")

def format_error_response(error_message: str, status_code: int = 500) -> dict:
    """Format error responses consistently"""
    return {
        "success": False,
        "error": error_message,
        "status_code": status_code
    }

def format_success_response(message: str, data: dict = None) -> dict:
    """Format success responses consistently"""
    response = {
        "success": True,
        "message": message
    }
    
    if data:
        response.update(data)
    
    return response