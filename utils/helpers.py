"""
Utility functions for the NYC Subway Tracker
Contains general helpers and utilities
"""
import os

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