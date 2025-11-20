"""
FastAPI route controllers for the NYC Subway Tracker
Handles HTTP requests and responses for ride management and URL parsing
"""
from fastapi import HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import func
from io import StringIO
import csv
from typing import List, Optional

from models import SubwayRide, get_db
from services.transit_service import extract_transit_info_with_api, ParsedRide

# -------------------------------
# REQUEST/RESPONSE MODELS
# -------------------------------
class RideCreate(BaseModel):
    line: str
    board_stop: str
    depart_stop: str
    date: date
    transferred: bool = False

class UrlParseRequest(BaseModel):
    url: str

class SuggestStationsRequest(BaseModel):
    extracted_name: str
    user_feedback: str = ""

class PasswordValidationRequest(BaseModel):
    password: str

# -------------------------------
# ROUTE HANDLERS
# -------------------------------
async def validate_password(request: PasswordValidationRequest):
    """Validate user password against DELETE_PASSWORD environment variable"""
    import os
    
    expected_password = os.getenv("DELETE_PASSWORD")
    
    if expected_password is None:
        raise HTTPException(status_code=500, detail="Password protection not configured on server")
    
    if request.password != expected_password:
        raise HTTPException(status_code=401, detail="Invalid password")
    
    return {"valid": True, "message": "Password validated successfully"}

async def get_root():
    """Root endpoint"""
    return {"message": "🚇 NYC Subway Tracker API is running!"}

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
    # Get the next ride numbers
    max_ride = db.query(func.max(SubwayRide.ride_number)).scalar()
    next_ride_number = (max_ride or 0) + 1
    
    test_rides = [
        SubwayRide(
            ride_number=next_ride_number,
            line="1",
            board_stop="Times Sq-42 St",
            depart_stop="14 St",
            date=date.today(),
            transferred=False
        ),
        SubwayRide(
            ride_number=next_ride_number + 1,
            line="N",
            board_stop="14 St-Union Sq",
            depart_stop="Canal St",
            date=date.today(),
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
        # Get the next ride number
        max_ride = db.query(func.max(SubwayRide.ride_number)).scalar()
        next_ride_number = (max_ride or 0) + 1
        
        db_ride = SubwayRide(
            ride_number=next_ride_number,
            line=ride.line,
            board_stop=ride.board_stop,
            depart_stop=ride.depart_stop,
            date=ride.date,
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
                "ride_number": db_ride.ride_number,
                "line": db_ride.line,
                "board_stop": db_ride.board_stop,
                "depart_stop": db_ride.depart_stop,
                "date": db_ride.date.isoformat(),
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
                    "board_stop": ride.boarding_stop,  # Convert from boarding_stop to board_stop
                    "depart_stop": ride.departing_stop,  # Convert from departing_stop to depart_stop
                    "date": ride.ride_date.isoformat(),
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
                 .order_by(SubwayRide.ride_number.desc(), SubwayRide.id.desc())\
                 .offset(offset)\
                 .limit(per_page)\
                 .all()
        
        return {
            "rides": [
                {
                    "id": ride.id,
                    "ride_number": ride.ride_number,
                    "line": ride.line,
                    "board_stop": ride.board_stop,
                    "depart_stop": ride.depart_stop,
                    "date": ride.date.isoformat(),
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

async def delete_ride(ride_id: int, db: Session = Depends(get_db)):
    """Delete a specific ride by ID"""
    try:
        ride = db.query(SubwayRide).filter(SubwayRide.id == ride_id).first()
        
        if not ride:
            raise HTTPException(status_code=404, detail=f"Ride with ID {ride_id} not found")
        
        db.delete(ride)
        db.commit()
        
        return {"message": f"🗑️ Deleted ride #{ride.ride_number} successfully!"}
        
    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete ride: {str(e)}")

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
        rides = db.query(SubwayRide).order_by(SubwayRide.ride_number.desc()).all()
        
        # Create CSV in memory
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['Ride #', 'Line', 'Boarding Stop', 'Departing Stop', 'Date', 'Transferred'])
        
        # Write data
        for ride in rides:
            writer.writerow([
                ride.ride_number,
                ride.line,
                ride.board_stop,
                ride.depart_stop,
                ride.date.isoformat(),
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

# -------------------------------
# STATISTICS ENDPOINTS
# -------------------------------
async def get_visited_stops_stats(since: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get most visited stops statistics"""
    try:
        query = db.query(
            SubwayRide.board_stop.label('stop_name'),
            func.count(SubwayRide.board_stop).label('visit_count')
        )
        
        # Apply date filter if provided
        if since:
            query = query.filter(SubwayRide.date >= since)
            
        # Group by stop and count visits (boarding stops)
        board_stops = query.group_by(SubwayRide.board_stop).all()
        
        # Also count departing stops
        depart_query = db.query(
            SubwayRide.depart_stop.label('stop_name'),
            func.count(SubwayRide.depart_stop).label('visit_count')
        )
        
        if since:
            depart_query = depart_query.filter(SubwayRide.date >= since)
            
        depart_stops = depart_query.group_by(SubwayRide.depart_stop).all()
        
        # Combine and aggregate stop counts
        stop_counts = {}
        for stop in board_stops:
            stop_counts[stop.stop_name] = stop_counts.get(stop.stop_name, 0) + stop.visit_count
            
        for stop in depart_stops:
            stop_counts[stop.stop_name] = stop_counts.get(stop.stop_name, 0) + stop.visit_count
        
        # Convert to list and sort by count
        result = [
            {"stop_name": stop, "visit_count": count}
            for stop, count in stop_counts.items()
        ]
        result.sort(key=lambda x: x['visit_count'], reverse=True)
        
        return result[:10]  # Top 10
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get visited stops stats: {str(e)}")

async def get_transfer_stops_stats(since: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get most transferred at stops statistics"""
    try:
        query = db.query(
            SubwayRide.depart_stop.label('stop_name'),
            func.count(SubwayRide.depart_stop).label('transfer_count')
        ).filter(SubwayRide.transferred == True)
        
        # Apply date filter if provided
        if since:
            query = query.filter(SubwayRide.date >= since)
            
        result = query.group_by(SubwayRide.depart_stop).order_by(
            func.count(SubwayRide.depart_stop).desc()
        ).limit(10).all()
        
        return [
            {"stop_name": row.stop_name, "transfer_count": row.transfer_count}
            for row in result
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get transfer stops stats: {str(e)}")

async def get_popular_lines_stats(since: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get most popular lines statistics"""
    try:
        query = db.query(
            SubwayRide.line,
            func.count(SubwayRide.line).label('ride_count')
        )
        
        # Apply date filter if provided
        if since:
            query = query.filter(SubwayRide.date >= since)
            
        result = query.group_by(SubwayRide.line).order_by(
            func.count(SubwayRide.line).desc()
        ).limit(10).all()
        
        return [
            {"line": row.line, "ride_count": row.ride_count}
            for row in result
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get popular lines stats: {str(e)}")