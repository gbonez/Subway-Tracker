"""
FastAPI route controllers for the NYC Subway Tracker
Handles HTTP requests and responses for ride management and URL parsing
"""
from fastapi import HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import func
from io import StringIO
import csv
from typing import List

from models import SubwayRide, get_db
from services.transit_service import extract_transit_info_with_api, ParsedRide

# -------------------------------
# REQUEST/RESPONSE MODELS
# -------------------------------
class RideCreate(BaseModel):
    line: str
    boarding_stop: str
    departing_stop: str
    ride_date: date
    transferred: bool = False

class UrlParseRequest(BaseModel):
    url: str

class SuggestStationsRequest(BaseModel):
    extracted_name: str
    user_feedback: str = ""

# -------------------------------
# ROUTE HANDLERS
# -------------------------------
async def get_root():
    """Root endpoint"""
    return {"message": "🚇 NYC Subway Tracker API is running!"}

async def test_db_connection(db: Session = Depends(get_db)):
    """Test database connection"""
    try:
        # Test query
        result = db.execute("SELECT 1").fetchone()
        
        # Count rides
        ride_count = db.query(func.count(SubwayRide.id)).scalar()
        
        return {
            "status": "connected",
            "message": "Database connection successful! 🐘",
            "total_rides": ride_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {str(e)}")

async def debug_url_parsing():
    """Debug endpoint for URL parsing"""
    test_urls = [
        "https://maps.app.goo.gl/fQSTGxTWg3XSzZoZ7",
        "https://www.google.com/maps/dir/Empire+State+Building,+20+W+34th+St,+New+York,+NY+10001/@40.7484405,-73.9856644,17z"
    ]
    
    results = {}
    for i, url in enumerate(test_urls):
        try:
            parsed_rides = await extract_transit_info_with_api(url)
            results[f"url_{i+1}"] = {
                "url": url,
                "rides_found": len(parsed_rides),
                "rides": [ride.dict() for ride in parsed_rides]
            }
        except Exception as e:
            results[f"url_{i+1}"] = {
                "url": url,
                "error": str(e)
            }
    
    return results

async def suggest_stations(request: SuggestStationsRequest):
    """Suggest subway stations based on extracted names"""
    from services.transit_service import load_subway_stations, find_matching_stations
    
    all_stations_data = load_subway_stations()
    all_stations = []
    for line_stations in all_stations_data.values():
        all_stations.extend(line_stations)
    
    matches = find_matching_stations(request.extracted_name, all_stations)
    
    return {
        "extracted_name": request.extracted_name,
        "suggestions": [
            {
                "station_name": match[0],
                "confidence": match[1],
                "lines": [line for line, stations in all_stations_data.items() if match[0] in stations]
            }
            for match in matches
        ],
        "user_feedback": request.user_feedback
    }

async def add_test_data(db: Session = Depends(get_db)):
    """Add test data to the database"""
    test_rides = [
        SubwayRide(
            line="1",
            boarding_stop="Times Sq-42 St",
            departing_stop="14 St",
            ride_date=date.today(),
            transferred=False
        ),
        SubwayRide(
            line="N",
            boarding_stop="14 St-Union Sq",
            departing_stop="Canal St",
            ride_date=date.today(),
            transferred=True
        )
    ]
    
    try:
        for ride in test_rides:
            db.add(ride)
        db.commit()
        return {"message": f"✅ Added {len(test_rides)} test rides successfully!"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add test data: {str(e)}")

async def create_ride(ride: RideCreate, db: Session = Depends(get_db)):
    """Create a new subway ride"""
    try:
        db_ride = SubwayRide(
            line=ride.line,
            boarding_stop=ride.boarding_stop,
            departing_stop=ride.departing_stop,
            ride_date=ride.ride_date,
            transferred=ride.transferred
        )
        db.add(db_ride)
        db.commit()
        db.refresh(db_ride)
        
        return {
            "message": "Ride added successfully! 🚇",
            "ride_id": db_ride.id,
            "ride": {
                "id": db_ride.id,
                "line": db_ride.line,
                "boarding_stop": db_ride.boarding_stop,
                "departing_stop": db_ride.departing_stop,
                "ride_date": db_ride.ride_date.isoformat(),
                "transferred": db_ride.transferred
            }
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create ride: {str(e)}")

async def parse_url(request: UrlParseRequest):
    """Parse Google Maps URL to extract transit routes"""
    try:
        print(f"🔗 Parsing URL: {request.url}")
        
        parsed_rides = await extract_transit_info_with_api(request.url)
        
        if not parsed_rides:
            return {
                "success": False,
                "message": "No subway routes found in the provided URL. Make sure it's a Google Maps transit route.",
                "rides": []
            }
        
        return {
            "success": True,
            "message": f"Found {len(parsed_rides)} subway rides in the route!",
            "rides": [
                {
                    "line": ride.line,
                    "boarding_stop": ride.boarding_stop,
                    "departing_stop": ride.departing_stop,
                    "ride_date": ride.ride_date.isoformat(),
                    "transferred": ride.transferred
                }
                for ride in parsed_rides
            ]
        }
        
    except Exception as e:
        print(f"❌ Error parsing URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse URL: {str(e)}")

async def get_rides(
    page: int = 1,
    per_page: int = 20,
    db: Session = Depends(get_db)
):
    """Get paginated list of rides"""
    try:
        # Calculate offset
        offset = (page - 1) * per_page
        
        # Get total count
        total = db.query(func.count(SubwayRide.id)).scalar()
        
        # Get rides with pagination
        rides = db.query(SubwayRide)\
                 .order_by(SubwayRide.ride_date.desc(), SubwayRide.id.desc())\
                 .offset(offset)\
                 .limit(per_page)\
                 .all()
        
        return {
            "rides": [
                {
                    "id": ride.id,
                    "line": ride.line,
                    "boarding_stop": ride.boarding_stop,
                    "departing_stop": ride.departing_stop,
                    "ride_date": ride.ride_date.isoformat(),
                    "transferred": ride.transferred
                }
                for ride in rides
            ],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch rides: {str(e)}")

async def delete_all_rides(db: Session = Depends(get_db)):
    """Delete all rides from the database"""
    try:
        deleted_count = db.query(SubwayRide).delete()
        db.commit()
        return {"message": f"🗑️ Deleted {deleted_count} rides successfully!"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete rides: {str(e)}")

async def export_rides_csv(db: Session = Depends(get_db)):
    """Export all rides to CSV"""
    try:
        rides = db.query(SubwayRide).order_by(SubwayRide.ride_date.desc()).all()
        
        # Create CSV in memory
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['ID', 'Line', 'Boarding Stop', 'Departing Stop', 'Date', 'Transferred'])
        
        # Write data
        for ride in rides:
            writer.writerow([
                ride.id,
                ride.line,
                ride.boarding_stop,
                ride.departing_stop,
                ride.ride_date.isoformat(),
                'Yes' if ride.transferred else 'No'
            ])
        
        # Create response
        output.seek(0)
        
        def iter_csv():
            yield output.getvalue()
        
        return StreamingResponse(
            iter_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=rides.csv"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export CSV: {str(e)}")