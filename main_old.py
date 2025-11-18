from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import date
from sqlalchemy import create_engine, Column, Integer, String, Date, Boolean, text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from io import StringIO
import csv
import os
import requests
import re
from urllib.parse import urlparse, unquote
from typing import List
import asyncio
from playwright.async_api import async_playwright

# -------------------------------
# DATABASE setup
# -------------------------------
# Railway provides DATABASE_URL, but we can also construct it from individual vars
DATABASE_URL = os.getenv("DATABASE_URL")

# If DATABASE_URL is not available, construct it from Railway's PostgreSQL environment variables
if not DATABASE_URL:
    pg_user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER")
    pg_password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD")
    pg_host = os.getenv("PGHOST") or os.getenv("RAILWAY_PRIVATE_DOMAIN")
    pg_port = os.getenv("PGPORT", "5432")
    pg_database = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB")
    
    if all([pg_user, pg_password, pg_host, pg_database]):
        DATABASE_URL = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_database}"
        print(f"🔧 Constructed DATABASE_URL from Railway PostgreSQL variables")
    else:
        # For local development, fall back to SQLite
        DATABASE_URL = "sqlite:///./subway_rides.db"
        print("⚠️  Using SQLite for local development")

# Log which database we're using
if DATABASE_URL.startswith("postgresql"):
    print("🐘 Using PostgreSQL database from Railway")
    # For PostgreSQL, we need to handle SSL properly
    engine = create_engine(DATABASE_URL, connect_args={"sslmode": "require"})
elif DATABASE_URL.startswith("sqlite"):
    print("📁 Using SQLite database for local development")
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    print(f"🔗 Using database: {DATABASE_URL}")
    engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class SubwayRide(Base):
    __tablename__ = "rides"

    id = Column(Integer, primary_key=True, index=True)
    ride_number = Column(Integer, nullable=False)
    line = Column(String, nullable=False)
    board_stop = Column(String, nullable=False)
    depart_stop = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    transferred = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)

# -------------------------------
# API setup
# -------------------------------
app = FastAPI(title="NYC Subway Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://subway-tracker-production.up.railway.app",
        "http://localhost:3000",  # For local development
        "http://127.0.0.1:3000",   # For local development
        "*"  # Temporary for debugging
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

class RideCreate(BaseModel):
    line: str
    board_stop: str
    depart_stop: str
    date: date
    transferred: bool = False

class UrlParseRequest(BaseModel):
    url: str

class ParsedRide(BaseModel):
    line: str
    board_stop: str
    depart_stop: str
    transferred: bool
    confidence: int

# -------------------------------
# Helper Functions
# -------------------------------
def normalize_stop_name(stop_name: str) -> str:
    """Normalize stop names to match database format"""
    return (stop_name
            .replace('-', '-')
            .replace('  ', ' ')
            .strip()
            .title())

def extract_transit_info_from_url(url: str) -> List[ParsedRide]:
    """Extract transit information from Google Maps URL using headless browser"""
    return asyncio.run(extract_transit_info_async(url))
            # Major stations
            "times square": ["N", "Q", "R", "W", "S", "1", "2", "3", "7"],
            "union square": ["4", "5", "6", "L", "N", "Q", "R", "W"],
            "grand central": ["4", "5", "6", "7", "S"],
            "penn station": ["1", "2", "3", "A", "C", "E"],
            "atlantic terminal": ["B", "D", "N", "Q", "R", "W", "2", "3", "4", "5"],
            "jay st metrotech": ["A", "C", "F", "R"],
            "brooklyn bridge": ["4", "5", "6"],
            "canal st": ["J", "M", "Z", "N", "Q", "R", "W", "6", "A", "C", "E"],
            "14th st": ["1", "2", "3", "F", "M", "L"],
            "42nd st": ["N", "Q", "R", "W", "S", "1", "2", "3", "7"],
            "59th st": ["N", "Q", "R", "W", "A", "B", "C", "D"],
            "125th st": ["A", "B", "C", "D", "4", "5", "6"],
            "fulton st": ["A", "C", "J", "M", "Z", "2", "3", "4", "5"],
            
            # Brooklyn stations
            "prospect park": ["B", "Q"],
            "park slope": ["F", "G", "R"],
            "williamsburg": ["J", "M", "Z", "L"],
            "bedford ave": ["L"],
            "lorimer st": ["J", "M", "G"],
            "graham ave": ["L"],
            "grand st": ["J", "M", "Z", "B", "D"],
            "montrose ave": ["L"],
            "bushwick": ["J", "M", "Z", "L"],
            "east new york": ["A", "C", "J", "Z", "L"],
            "broadway junction": ["A", "C", "J", "Z", "L"],
            "new lots ave": ["3"],
            "flatbush ave": ["B", "Q"],
            "church ave": ["B", "Q"],
            "bay ridge": ["R"],
            "coney island": ["D", "F", "N", "Q"],
            
            # Manhattan stations  
            "wall st": ["4", "5"],
            "city hall": ["4", "5", "6", "R", "W"],
            "houston st": ["1"],
            "spring st": ["4", "5", "6", "N", "Q", "R", "W"],
            "bleecker st": ["4", "5", "6"],
            "astor pl": ["4", "5", "6"],
            "8th st nyu": ["N", "Q", "R", "W"],
            "23rd st": ["4", "5", "6", "N", "Q", "R", "W", "F", "M"],
            "28th st": ["4", "5", "6", "N", "Q", "R", "W"],
            "34th st": ["A", "C", "E", "B", "D", "F", "M", "N", "Q", "R", "W"],
            "42nd st port authority": ["A", "C", "E", "N", "Q", "R", "S", "W", "1", "2", "3", "7"],
            "50th st": ["A", "C", "E", "1"],
            "57th st": ["N", "Q", "R", "W", "F"],
            "72nd st": ["1", "2", "3", "B", "C"],
            "86th st": ["4", "5", "6", "1"],
            "96th st": ["1", "2", "3", "4", "5", "6", "B", "C"],
            "103rd st": ["1", "6"],
            "110th st": ["1", "2", "3", "B", "C"],
            "116th st": ["1", "4", "5", "6", "B", "C"],
            "135th st": ["2", "3", "B", "C"],
            "145th st": ["1", "3", "A", "B", "C", "D"],
            "155th st": ["A", "B", "C", "D"],
            "175th st": ["A"],
            "181st st": ["A"],
            "190th st": ["A"],
            "207th st": ["A"],
            
            # Queens stations
            "queensboro plaza": ["7", "N", "Q", "R", "W"],
            "court sq": ["7", "E", "M", "G"],
            "jackson heights": ["7", "E", "F", "M", "R"],
            "roosevelt ave": ["7", "E", "F", "M", "R"],
            "74th st broadway": ["7", "E", "F", "M", "R"],
            "flushing": ["7"],
            "astoria": ["N", "Q", "R", "W"],
            "long island city": ["7", "E", "M", "G"],
            "lic": ["7", "E", "M", "G"],  # Abbreviation for Long Island City
            
            # Specific stations from your area (Brooklyn)
            "humboldt st": ["G"],
            "nassau ave": ["G"],
            "greenpoint ave": ["G"],
            "21st st queensbridge": ["F"],
            "lexington ave 59th st": ["4", "5", "6", "N", "Q", "R", "W"],
            "herald sq": ["B", "D", "F", "M", "N", "Q", "R", "W"],
            "empire state building": ["B", "D", "F", "M", "N", "Q", "R", "W"],  # Near Herald Sq/34th St
        }
        
        parsed_url = urlparse(url)
        
        # If it's a short link, follow redirects
        if 'goo.gl' in parsed_url.netloc or 'maps.app.goo.gl' in parsed_url.netloc:
            print("🔍 Step 1/3: Following redirects for URL:", url)
            response = requests.get(url, allow_redirects=True, timeout=10)
            final_url = response.url
            print(f"� Step 2/3: Processing expanded URL: {final_url[:100]}...")
            parsed_url = urlparse(final_url)
        
        print("🔎 Step 3/3: Extracting route information...")
        
        # Extract location names from URL
        url_text = unquote(str(parsed_url)).lower()
        
        # Look for origin and destination in URL structure
        # Google Maps URLs typically have /dir/ORIGIN/DESTINATION/ format
        dir_match = re.search(r'/dir/([^/]+)/([^/]+)', url_text)
        if dir_match:
            origin = dir_match.group(1).replace('+', ' ').replace('%20', ' ')
            destination = dir_match.group(2).replace('+', ' ').replace('%20', ' ')
            
            print(f"   📍 Found route: {origin} → {destination}")
            
            # Try to match station names to subway lines
            origin_clean = re.sub(r'\d+\s+', '', origin).strip()  # Remove street numbers
            destination_clean = re.sub(r'\d+\s+', '', destination).strip()
            
            # Look for station matches
            origin_station = None
            destination_station = None
            possible_lines = []
            
            for station_name, lines in nyc_subway_stations.items():
                if station_name in origin_clean.lower():
                    origin_station = station_name.title()
                    possible_lines.extend(lines)
                    print(f"   ✅ Matched origin '{origin}' to station '{station_name}' (lines: {', '.join(lines)})")
                
                if station_name in destination_clean.lower():
                    destination_station = station_name.title()
                    possible_lines.extend(lines)
                    print(f"   ✅ Matched destination '{destination}' to station '{station_name}' (lines: {', '.join(lines)})")
            
            # If we found station matches, create rides
            if origin_station and destination_station and possible_lines:
                # Find common lines between origin and destination
                origin_lines = set()
                destination_lines = set()
                
                for station_name, lines in nyc_subway_stations.items():
                    if station_name in origin_clean.lower():
                        origin_lines.update(lines)
                    if station_name in destination_clean.lower():
                        destination_lines.update(lines)
                
                common_lines = origin_lines.intersection(destination_lines)
                
                if common_lines:
                    # Use the first common line (could be improved with user preference)
                    best_line = sorted(list(common_lines))[0]
                    rides.append(ParsedRide(
                        line=best_line,
                        board_stop=origin_station,
                        depart_stop=destination_station,
                        transferred=False,
                        confidence=90
                    ))
                    print(f"   🚇 Created ride: {best_line} line from {origin_station} to {destination_station}")
                else:
                    # Different lines, might require transfer
                    if origin_lines and destination_lines:
                        origin_line = sorted(list(origin_lines))[0]
                        destination_line = sorted(list(destination_lines))[0]
                        
                        # Find a common transfer station (simplified)
                        transfer_stations = ["times square", "union square", "42nd st", "14th st"]
                        transfer_station = None
                        
                        for ts in transfer_stations:
                            if ts in nyc_subway_stations:
                                ts_lines = set(nyc_subway_stations[ts])
                                if origin_line in ts_lines and destination_line in ts_lines:
                                    transfer_station = ts.title()
                                    break
                        
                        if transfer_station:
                            # First ride to transfer station
                            rides.append(ParsedRide(
                                line=origin_line,
                                board_stop=origin_station,
                                depart_stop=transfer_station,
                                transferred=True,
                                confidence=85
                            ))
                            # Second ride from transfer station
                            rides.append(ParsedRide(
                                line=destination_line,
                                board_stop=transfer_station,
                                depart_stop=destination_station,
                                transferred=False,
                                confidence=85
                            ))
                            print(f"   � Created transfer route: {origin_line} to {transfer_station}, then {destination_line} to {destination_station}")
                        else:
                            # Just create one ride with best guess
                            best_line = sorted(list(origin_lines))[0]
                            rides.append(ParsedRide(
                                line=best_line,
                                board_stop=origin_station,
                                depart_stop=destination_station,
                                transferred=False,
                                confidence=70
                            ))
                            print(f"   🚇 Created best-guess ride: {best_line} line from {origin_station} to {destination_station}")
            
            # Fallback: try to extract any subway line mentions from the URL
            if not rides:
                print("   � No station matches, looking for line mentions in URL...")
                line_pattern = r'(?:line|train)[\s\-_]*([1-7]|[A-Z])(?:\s|%20|$)'
                line_matches = re.findall(line_pattern, url_text, re.IGNORECASE)
                
                if line_matches and origin_station and destination_station:
                    for line in line_matches:
                        rides.append(ParsedRide(
                            line=line.upper(),
                            board_stop=origin_station,
                            depart_stop=destination_station,
                            transferred=False,
                            confidence=60
                        ))
                        print(f"   🚇 Created line-based ride: {line.upper()} from {origin_station} to {destination_station}")
                        
    except Exception as e:
        print(f"❌ URL parsing error: {str(e)}")
        
    print(f"✅ Extracted {len(rides)} rides from URL")
                        
    print(f"✅ Extracted {len(rides)} rides from URL")
    return rides

def extract_from_maps_page(html_content: str, full_url: str = "") -> List[ParsedRide]:
    """Simplified fallback parsing - mainly for backwards compatibility"""
    print("   � Simplified HTML parsing (fallback only)...")
    return []
    return rides

def extract_from_directions_data(html_content: str) -> List[ParsedRide]:
    """Simplified fallback parsing - mainly for backwards compatibility"""
    print("   � Simplified JSON analysis (fallback only)...")
    return []

def extract_by_station_matching(html_content: str) -> List[ParsedRide]:
    """Disabled to prevent CSS/JS false positives"""
    print("   �️  Station matching disabled to prevent false positives...")
    return []

# -------------------------------
# API Routes
# -------------------------------
@app.get("/")
def health_check():
    """Health check endpoint for Railway deployment"""
    return {"status": "healthy", "message": "NYC Subway Tracker API is running"}

@app.get("/test-db")
def test_database(db: Session = Depends(get_db)):
    """Test database connection"""
    try:
        ride_count = db.query(SubwayRide).count()
        return {"status": "database connected", "total_rides": ride_count}
    except Exception as e:
        return {"status": "database error", "error": str(e)}

@app.post("/add-test-data")
def add_test_data(db: Session = Depends(get_db)):
    """Add some test rides to the database"""
    try:
        # Check if we already have rides
        existing_count = db.query(SubwayRide).count()
        if existing_count > 0:
            return {"message": f"Database already has {existing_count} rides"}
        
        # Add test rides
        test_rides = [
            SubwayRide(ride_number=1, line="6", board_stop="Union Sq-14th St", depart_stop="Grand Central-42nd St", date=date.today(), transferred=False),
            SubwayRide(ride_number=2, line="4", board_stop="14th St-Union Sq", depart_stop="59th St-Columbus Circle", date=date.today(), transferred=True),
            SubwayRide(ride_number=3, line="L", board_stop="14th St-8th Ave", depart_stop="Bedford Ave", date=date.today(), transferred=False),
        ]
        
        for ride in test_rides:
            db.add(ride)
        
        db.commit()
        
        new_count = db.query(SubwayRide).count()
        return {"message": f"Added test data. Database now has {new_count} rides"}
        
    except Exception as e:
        db.rollback()
        return {"error": f"Failed to add test data: {str(e)}"}

@app.post("/rides/")
def create_ride(ride: RideCreate, db: Session = Depends(get_db)):
    # Get all existing ride numbers as a set
    existing_numbers = set(num[0] for num in db.query(SubwayRide.ride_number).all())

    # Find the next available ride number not in use
    next_ride_number = 1
    while next_ride_number in existing_numbers:
        next_ride_number += 1

    # Create and save the new ride
    new_ride = SubwayRide(
        ride_number=next_ride_number,
        line=ride.line,
        board_stop=ride.board_stop,
        depart_stop=ride.depart_stop,
        date=ride.date,
        transferred=ride.transferred,
    )

    db.add(new_ride)
    db.commit()
    db.refresh(new_ride)

    return {"message": "Ride recorded!", "ride_id": new_ride.id}

@app.post("/parse-url/")
async def parse_google_maps_url(request: UrlParseRequest):
    """Parse Google Maps URL to extract transit route information"""
    try:
        print(f"Received URL: {request.url}")
        rides = extract_transit_info_from_url(request.url)
        
        print(f"Extracted {len(rides)} rides: {[ride.dict() for ride in rides]}")
        
        if not rides:
            # Provide more detailed feedback about what we tried
            raise HTTPException(
                status_code=404, 
                detail="No transit information found in the provided URL. This could mean: 1) The link doesn't contain subway/transit directions, 2) The route only contains walking/driving, or 3) The transit data is in a format we don't recognize yet. Try ensuring your Google Maps link includes subway/train segments."
            )
        
        return {
            "rides": [ride.dict() for ride in rides],
            "count": len(rides),
            "message": f"Successfully extracted {len(rides)} ride(s) from the URL"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=400, 
            detail=f"Error parsing URL: {str(e)}"
        )

@app.get("/rides/")
def get_all_rides(db: Session = Depends(get_db)):
    try:
        print("🔍 Fetching rides from database...")
        rides = db.query(SubwayRide).all()
        print(f"📊 Found {len(rides)} rides in database")
        
        # Convert to list of dicts for JSON serialization
        rides_data = []
        for ride in rides:
            rides_data.append({
                "id": ride.id,
                "ride_number": ride.ride_number,
                "line": ride.line,
                "board_stop": ride.board_stop,
                "depart_stop": ride.depart_stop,
                "date": ride.date.isoformat() if ride.date else None,
                "transferred": ride.transferred
            })
        
        print(f"✅ Returning {len(rides_data)} rides to frontend")
        return rides_data
    except Exception as e:
        print(f"❌ Error fetching rides: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/rides/export")
def export_rides_csv(db: Session = Depends(get_db)):
    rides = db.query(SubwayRide).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "ride_number", "line", "board_stop", "depart_stop", "date", "transferred"])
    for ride in rides:
        writer.writerow([
            ride.id,
            ride.ride_number,
            ride.line,
            ride.board_stop,
            ride.depart_stop,
            ride.date,
            ride.transferred
        ])
    output.seek(0)
    return StreamingResponse(
        StringIO(output.getvalue()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rides.csv"}
    )


@app.get("/rides/{ride_id}")
def get_ride(ride_id: int, db: Session = Depends(get_db)):
    ride = db.query(SubwayRide).filter(SubwayRide.id == ride_id).first()
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    return ride

@app.delete("/rides/{ride_id}")
def delete_ride(ride_id: int, db: Session = Depends(get_db)):
    ride = db.query(SubwayRide).filter(SubwayRide.id == ride_id).first()
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    ride_number_to_delete = ride.ride_number
    db.delete(ride)

    db.query(SubwayRide).filter(SubwayRide.ride_number > ride_number_to_delete).update(
        {SubwayRide.ride_number: SubwayRide.ride_number - 1}, synchronize_session=False
    )

    db.commit()
    return {"message": f"Ride with ID {ride_id} deleted and ride numbers updated"}


@app.delete("/rides/")
def clear_all_rides(db: Session = Depends(get_db)):
    try:
        db.query(SubwayRide).delete()
        db.commit()
        return {"message": "All rides deleted successfully."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error clearing rides: {str(e)}")
