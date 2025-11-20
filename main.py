"""
NYC Subway Tracker API
A FastAPI application for tracking subway rides and parsing Google Maps transit routes
"""
import uvicorn
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# Import modules
from models import get_db
from utils.helpers import get_app_port
from controllers.ride_controller import (
    get_root,
    debug_url_parsing,
    suggest_stations,
    add_test_data,
    create_ride,
    parse_url,
    get_rides,
    delete_ride,
    delete_all_rides,
    export_rides_csv,
    get_visited_stops_stats,
    get_transfer_stops_stats,
    get_popular_lines_stats,
    RideCreate,
    UrlParseRequest,
    SuggestStationsRequest
)

# -------------------------------
# APP INITIALIZATION
# -------------------------------
def create_app() -> FastAPI:
    """Create and configure the FastAPI application"""
    
    # Create FastAPI app
    app = FastAPI(title="NYC Subway Tracker API")
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://subway-tracker-production.up.railway.app",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "*"  # Allow all origins for development
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    
    # Register routes
    register_routes(app)
    
    return app

def register_routes(app: FastAPI):
    """Register all API routes"""
    
    # Basic routes
    app.get("/")(get_root)
    app.get("/debug-url-parsing")(debug_url_parsing)
    
    # Ride management routes
    app.post("/rides/")(create_ride)
    app.get("/rides/")(get_rides)
    app.delete("/rides/{ride_id}")(delete_ride)
    app.delete("/rides/")(delete_all_rides)
    app.get("/export-csv/")(export_rides_csv)
    
    # URL parsing and station suggestion routes
    app.post("/parse-url/")(parse_url)
    app.post("/suggest-stations/")(suggest_stations)
    
    # Statistics routes
    app.get("/stats/visited-stops")(get_visited_stops_stats)
    app.get("/stats/transfer-stops")(get_transfer_stops_stats)
    app.get("/stats/popular-lines")(get_popular_lines_stats)
    
    # Utility routes
    app.post("/add-test-data")(add_test_data)

# Create the app instance
app = create_app()

# -------------------------------
# APPLICATION STARTUP
# -------------------------------
if __name__ == "__main__":
    port = get_app_port()
    print(f"🚇 Starting NYC Subway Tracker on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)