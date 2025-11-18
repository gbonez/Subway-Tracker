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
    """Extract transit information from Google Maps URL"""
    rides = []
    
    try:
        print(f"🔍 Step 1/4: Following redirects for URL: {url}")
        # Follow redirects to get the full URL
        response = requests.get(url, allow_redirects=True, timeout=10)
        full_url = response.url
        html_content = response.text
        
        print(f"📄 Step 2/4: Processing expanded URL: {full_url[:100]}...")
        
        # Parse URL parameters first
        parsed_url = urlparse(full_url)
        decoded_url = unquote(full_url)
        
        print(f"🔎 Step 3/4: Attempting URL pattern matching...")
        # Enhanced patterns to catch subway info in mixed-mode directions
        transit_patterns = [
            # Direct subway patterns
            r'subway[^,]*?([0-9A-Z]+)[^,]*?(?:from|at)\s*([^,]+?)\s*(?:to|until)\s*([^,]+)',
            r'([0-9A-Z]+)\s*(?:train|line)[^,]*?(?:from|at)\s*([^,]+?)\s*(?:to|until)\s*([^,]+)',
            r'Take\s+(?:the\s+)?([0-9A-Z]+).*?from\s+([^,]+?)\s+to\s+([^,]+)',
            # Patterns for mixed directions with walking + subway
            r'(?:walk|walking).*?subway.*?([0-9A-Z]+).*?(?:from|at)\s*([^,]+?)\s*(?:to|until)\s*([^,]+)',
            r'(?:walk|walking).*?([0-9A-Z]+)\s*(?:train|line).*?(?:from|at)\s*([^,]+?)\s*(?:to|until)\s*([^,]+)',
        ]
        
        # Search in URL first
        for i, pattern in enumerate(transit_patterns):
            print(f"   🔍 Trying URL pattern {i+1}/{len(transit_patterns)}: {pattern[:50]}...")
            matches = re.finditer(pattern, decoded_url, re.IGNORECASE)
            for match in matches:
                line = match.group(1).upper()
                board_stop = normalize_stop_name(match.group(2))
                depart_stop = normalize_stop_name(match.group(3))
                
                print(f"   ✅ Found match: {line} from {board_stop} to {depart_stop}")
                ride = ParsedRide(
                    line=line,
                    board_stop=board_stop,
                    depart_stop=depart_stop,
                    transferred=False,
                    confidence=70
                )
                rides.append(ride)
        
        # Enhanced HTML parsing for mixed-mode directions
        if not rides:
            print(f"🔎 Step 3/4: No URL matches found, analyzing HTML content...")
            rides = extract_from_maps_page(html_content, full_url)
        
        # If still no rides, try alternative parsing methods
        if not rides:
            print(f"🔎 Step 3/4: Trying JSON data extraction...")
            rides = extract_from_directions_data(html_content)
            
        # Last resort: try to find any NYC subway station names in the content
        if not rides:
            print(f"🔎 Step 3/4: Attempting station name matching (last resort)...")
            rides = extract_by_station_matching(html_content)
            
        print(f"✅ Step 4/4: Extraction complete. Found {len(rides)} rides.")
        
    except Exception as e:
        print(f"❌ Error extracting transit info: {e}")
        raise HTTPException(status_code=400, detail=f"Could not parse Google Maps URL: {str(e)}")
    
    return rides

def extract_from_maps_page(html_content: str, full_url: str = "") -> List[ParsedRide]:
    """Extract transit information from Google Maps page HTML with enhanced parsing"""
    rides = []
    
    print("   📝 Parsing HTML content for transit patterns...")
    
    # Enhanced patterns for mixed-mode directions
    transit_patterns = [
        # JSON-like data patterns
        r'"transit"[^}]*?"short_name":"([^"]+)"[^}]*?"departure_stop"[^}]*?"name":"([^"]+)"[^}]*?"arrival_stop"[^}]*?"name":"([^"]+)"',
        r'"vehicle"[^}]*?"name":"([^"]+)"[^}]*?"stops"[^}]*?"departure_stop"[^}]*?"name":"([^"]+)"[^}]*?"arrival_stop"[^}]*?"name":"([^"]+)"',
        
        # Text patterns for mixed directions
        r'(?:walk|walking)[^.]*?(?:subway|train)[^.]*?([0-9A-Z]+)[^.]*?(?:from|at)\s*([^,.]+?)(?:\s+(?:to|until)\s*([^,.]+))?',
        r'(?:subway|train)[^.]*?([0-9A-Z]+)[^.]*?(?:from|at)\s*([^,.]+?)(?:\s+(?:to|until)\s*([^,.]+))?',
        
        # Step-by-step direction patterns
        r'board\s+(?:the\s+)?([0-9A-Z]+)[^.]*?at\s+([^,.]+)',
        r'take\s+(?:the\s+)?([0-9A-Z]+)[^.]*?(?:from|at)\s+([^,.]+?)(?:\s+(?:to|until)\s+([^,.]+))?',
        
        # Station patterns
        r'([0-9A-Z]+)\s*(?:line|train)[^.]*?station[^.]*?([^,.]+?)(?:[^.]*?to[^.]*?([^,.]+?))?',
        
        # Alternative patterns for embedded data
        r'subway.*?line["\s]*:.*?["\s]*([0-9A-Z]+).*?from["\s]*:.*?["\s]*([^"]+)["\s]*.*?to["\s]*:.*?["\s]*([^"]+)',
    ]
    
    # Also look for station names that might indicate subway routes
    station_patterns = [
        r'([0-9A-Z]+)\s+(?:line|train)[^.]*?([A-Z][^,.]*?(?:St|Ave|Pkwy|Plaza|Square|Center|Junction|Terminal))[^.]*?(?:to|until)[^.]*?([A-Z][^,.]*?(?:St|Ave|Pkwy|Plaza|Square|Center|Junction|Terminal))',
        r'(?:from|at)\s+([A-Z][^,.]*?(?:St|Ave|Pkwy|Plaza|Square|Center|Junction|Terminal))[^.]*?([0-9A-Z]+)\s+(?:line|train)[^.]*?(?:to|until)[^.]*?([A-Z][^,.]*?(?:St|Ave|Pkwy|Plaza|Square|Center|Junction|Terminal))',
    ]
    
    # Search through all patterns
    for i, pattern in enumerate(transit_patterns):
        print(f"      🔍 Trying HTML pattern {i+1}/{len(transit_patterns)}")
        matches = re.finditer(pattern, html_content, re.IGNORECASE | re.DOTALL)
        for match in matches:
            try:
                groups = match.groups()
                if len(groups) >= 2:
                    line = groups[0].upper() if groups[0] else None
                    board_stop = groups[1] if len(groups) > 1 else None
                    depart_stop = groups[2] if len(groups) > 2 and groups[2] else None
                    
                    # Clean and validate
                    if line and line.strip() and board_stop and board_stop.strip():
                        line = re.sub(r'[^0-9A-Z]', '', line.upper())[:3]  # Limit to 3 chars max
                        board_stop = normalize_stop_name(board_stop)
                        
                        if depart_stop:
                            depart_stop = normalize_stop_name(depart_stop)
                            
                            print(f"      ✅ Found HTML match: {line} from {board_stop} to {depart_stop}")
                            ride = ParsedRide(
                                line=line,
                                board_stop=board_stop,
                                depart_stop=depart_stop,
                                transferred=False,
                                confidence=60
                            )
                            rides.append(ride)
                        else:
                            # Store for potential pairing with next stop
                            pass
                            
            except (IndexError, AttributeError):
                continue
    
    # Try station patterns as backup
    if not rides:
        print("   📍 Trying station name patterns...")
        for i, pattern in enumerate(station_patterns):
            print(f"      🔍 Trying station pattern {i+1}/{len(station_patterns)}")
            matches = re.finditer(pattern, html_content, re.IGNORECASE)
            for match in matches:
                try:
                    groups = match.groups()
                    if len(groups) >= 3:
                        if groups[0].isdigit() or len(groups[0]) <= 3:  # First group is line
                            line = groups[0].upper()
                            board_stop = normalize_stop_name(groups[1])
                            depart_stop = normalize_stop_name(groups[2])
                        else:  # Station names first, then line
                            line = groups[1].upper()
                            board_stop = normalize_stop_name(groups[0])
                            depart_stop = normalize_stop_name(groups[2])
                        
                        print(f"      ✅ Found station match: {line} from {board_stop} to {depart_stop}")
                        ride = ParsedRide(
                            line=line,
                            board_stop=board_stop,
                            depart_stop=depart_stop,
                            transferred=False,
                            confidence=50
                        )
                        rides.append(ride)
                except (IndexError, AttributeError):
                    continue
    
    print(f"   📝 HTML parsing complete. Found {len(rides)} rides.")
    return rides

def extract_from_directions_data(html_content: str) -> List[ParsedRide]:
    """Extract transit info from Google Maps directions data and embedded JSON"""
    rides = []
    
    print("   📊 Analyzing embedded JSON data...")
    
    try:
        # Look for embedded JSON data that might contain route information
        json_patterns = [
            r'window\.APP_INITIALIZATION_STATE[^;]*;',
            r'window\.APP_FLAGS[^;]*;',
            r'\[\[null,null,null,null,\[.*?\]\]\]',
        ]
        
        for i, pattern in enumerate(json_patterns):
            print(f"      🔍 Trying JSON pattern {i+1}/{len(json_patterns)}")
            matches = re.finditer(pattern, html_content, re.DOTALL)
            for match in matches:
                data_str = match.group(0)
                
                # Look for transit/subway references in the data
                subway_refs = re.finditer(r'"([0-9A-Z]{1,3})"[^"]*"([^"]*(?:St|Ave|Station|Plaza|Square|Center|Junction|Terminal)[^"]*)"', data_str)
                
                stations = []
                for ref in subway_refs:
                    line_candidate = ref.group(1)
                    station_candidate = ref.group(2)
                    
                    # Validate line format (1-3 alphanumeric characters)
                    if re.match(r'^[0-9A-Z]{1,3}$', line_candidate) and len(station_candidate) > 3:
                        stations.append((line_candidate, normalize_stop_name(station_candidate)))
                        print(f"         📍 Found potential station: {line_candidate} - {station_candidate}")
                
                # Create rides from consecutive station pairs
                for i in range(len(stations) - 1):
                    line1, station1 = stations[i]
                    line2, station2 = stations[i + 1]
                    
                    # Use the first line found, assuming it's the main route
                    print(f"      ✅ Created JSON ride: {line1} from {station1} to {station2}")
                    ride = ParsedRide(
                        line=line1,
                        board_stop=station1,
                        depart_stop=station2,
                        transferred=line1 != line2,
                        confidence=40
                    )
                    rides.append(ride)
                    
    except Exception as e:
        print(f"   ❌ Error parsing JSON data: {e}")
    
    print(f"   📊 JSON analysis complete. Found {len(rides)} rides.")
    return rides

def extract_by_station_matching(html_content: str) -> List[ParsedRide]:
    """Last resort: match against known NYC subway station names"""
    rides = []
    
    print("   🗺️  Attempting NYC station name matching (last resort)...")
    
    # Common NYC subway station keywords and patterns
    nyc_station_keywords = [
        "St", "Ave", "Pkwy", "Plaza", "Square", "Center", "Junction", "Terminal",
        "Penn Station", "Union Sq", "Times Sq", "Columbus Circle", "Grand Central",
        "Herald Sq", "14th St", "42nd St", "59th St", "86th St", "96th St",
        "125th St", "Canal St", "Houston St", "Spring St", "Bleecker St",
        "Brooklyn Bridge", "City Hall", "Wall St", "Fulton St", "Atlantic Ave",
        "Jay St", "DeKalb Ave", "Fort Hamilton", "Bay Ridge", "Coney Island"
    ]
    
    # Find potential station names in the HTML
    potential_stations = []
    print(f"      🔍 Searching for {len(nyc_station_keywords)} NYC station keywords...")
    
    for keyword in nyc_station_keywords:
        pattern = rf'([^<>"]*{re.escape(keyword)}[^<>"]*)'
        matches = re.finditer(pattern, html_content, re.IGNORECASE)
        for match in matches:
            station_text = match.group(1).strip()
            if 5 <= len(station_text) <= 50:  # Reasonable station name length
                cleaned = normalize_stop_name(station_text)
                if cleaned not in [ps[1] for ps in potential_stations]:
                    potential_stations.append(("Unknown", cleaned))
                    print(f"         📍 Found potential station: {cleaned}")
    
    print(f"      📍 Found {len(potential_stations)} potential stations")
    
    # Look for line numbers/letters near station names
    enhanced_stations = []
    for line, station in potential_stations:
        # Look for nearby line indicators
        station_index = html_content.lower().find(station.lower())
        if station_index > 0:
            # Check 200 characters before and after the station name
            context = html_content[max(0, station_index-200):station_index+200]
            
            # Look for line indicators
            line_patterns = [
                r'([0-9A-Z]{1,2})\s*(?:line|train)',
                r'(?:line|train)\s*([0-9A-Z]{1,2})',
                r'subway\s*([0-9A-Z]{1,2})',
                r'([0-9A-Z])\s*express',
                r'([0-9A-Z])\s*local'
            ]
            
            found_line = None
            for pattern in line_patterns:
                match = re.search(pattern, context, re.IGNORECASE)
                if match:
                    found_line = match.group(1).upper()
                    print(f"         🚇 Found line {found_line} near {station}")
                    break
            
            enhanced_stations.append((found_line or "Unknown", station))
    
    # Create rides from pairs of stations
    if len(enhanced_stations) >= 2:
        print(f"      🔗 Creating rides from {len(enhanced_stations)} enhanced stations...")
        for i in range(len(enhanced_stations) - 1):
            line1, station1 = enhanced_stations[i]
            line2, station2 = enhanced_stations[i + 1]
            
            print(f"      ✅ Created station match ride: {line1 if line1 != 'Unknown' else line2} from {station1} to {station2}")
            ride = ParsedRide(
                line=line1 if line1 != "Unknown" else line2,
                board_stop=station1,
                depart_stop=station2,
                transferred=line1 != line2 and line1 != "Unknown" and line2 != "Unknown",
                confidence=25  # Low confidence for this method
            )
            rides.append(ride)
    
    print(f"   🗺️  Station matching complete. Found {len(rides)} rides.")
    return rides

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
