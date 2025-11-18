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
import subprocess
import sys
from playwright.async_api import async_playwright

# -------------------------------
# PLAYWRIGHT SETUP
# -------------------------------
def install_playwright_browsers():
    """Install Playwright browsers if not already installed"""
    try:
        # Skip installation if running in Docker (browsers pre-installed)
        if os.getenv("RAILWAY_ENVIRONMENT") or os.path.exists("/.dockerenv"):
            print("� Running in containerized environment - browsers should be pre-installed")
            return
            
        print("�🔍 Checking if Playwright browsers are installed...")
        # Try to run playwright install
        result = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], 
                              capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            print("✅ Playwright browsers installed successfully")
        else:
            print(f"⚠️ Playwright install warning: {result.stderr}")
    except subprocess.TimeoutExpired:
        print("⏰ Playwright install timeout - continuing anyway")
    except Exception as e:
        print(f"⚠️ Could not install Playwright browsers: {e}")

# Install browsers on startup (unless in Docker)
install_playwright_browsers()

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

async def extract_transit_info_from_url(url: str) -> List[ParsedRide]:
    """Extract transit information from Google Maps URL using headless browser"""
    return await extract_transit_info_async(url)

async def extract_transit_info_async(url: str) -> List[ParsedRide]:
    """Async function to extract transit info using Playwright headless browser"""
    rides = []
    
    try:
        print(f"🔍 Starting headless browser extraction for URL: {url}")
        
        async with async_playwright() as p:
            # Launch browser in headless mode
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox', 
                    '--disable-dev-shm-usage', 
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor'
                ]  # Optimized for Railway/Docker deployment
            )
            
            try:
                page = await browser.new_page()
                
                # Set a realistic user agent to avoid detection
                await page.set_user_agent(
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                
                print("📄 Navigating to Google Maps URL...")
                
                # Navigate to the URL with timeout
                await page.goto(url, wait_until='networkidle', timeout=30000)
                
                # Wait for transit directions to load
                print("⏳ Waiting for transit directions to load...")
                
                # Try multiple selectors that Google Maps might use for transit directions
                transit_selectors = [
                    '[data-value="Transit"]',
                    '[aria-label*="transit"]',
                    '[data-trip-index]',
                    '.transit-route-segment',
                    '[jsaction*="directions"]',
                    '.directions-step',
                    '.transit-line',
                ]
                
                # Wait for any transit-related element to appear
                try:
                    await page.wait_for_selector(','.join(transit_selectors), timeout=10000)
                    print("✅ Transit directions loaded successfully")
                except:
                    print("⚠️ Transit directions may not have loaded, proceeding with extraction...")
                
                # Extract transit information using multiple strategies
                print("🔍 Extracting transit route information...")
                
                # Strategy 1: Look for transit route segments
                route_segments = await page.evaluate("""
                    () => {
                        const segments = [];
                        
                        // Look for transit route information in various formats
                        const transitElements = document.querySelectorAll(
                            '[data-trip-index], .transit-route-segment, .directions-step, [aria-label*="subway"], [aria-label*="train"], [aria-label*="transit"]'
                        );
                        
                        transitElements.forEach(element => {
                            const text = element.innerText || element.textContent || '';
                            const ariaLabel = element.getAttribute('aria-label') || '';
                            
                            // Look for subway line patterns (letters/numbers)
                            const lineMatches = text.match(/\\b([0-9A-Z])\\s*(?:line|train|subway)/i) || 
                                               ariaLabel.match(/\\b([0-9A-Z])\\s*(?:line|train|subway)/i);
                            
                            // Look for station names (containing Street, Avenue, etc.)
                            const stationMatches = text.match(/([^\\n]*(?:St|Street|Ave|Avenue|Sq|Square|Plaza|Station|Terminal|Bridge|Park)[^\\n]*)/gi) ||
                                                  ariaLabel.match(/([^\\n]*(?:St|Street|Ave|Avenue|Sq|Square|Plaza|Station|Terminal|Bridge|Park)[^\\n]*)/gi);
                            
                            if (lineMatches || stationMatches) {
                                segments.push({
                                    text: text.trim(),
                                    ariaLabel: ariaLabel.trim(),
                                    line: lineMatches ? lineMatches[1] : null,
                                    stations: stationMatches || []
                                });
                            }
                        });
                        
                        return segments;
                    }
                """)
                
                print(f"📊 Found {len(route_segments)} potential route segments")
                
                # Strategy 2: Look for structured transit data in the page
                structured_data = await page.evaluate("""
                    () => {
                        const data = [];
                        
                        // Look for JSON-LD structured data
                        const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                        scripts.forEach(script => {
                            try {
                                const json = JSON.parse(script.textContent);
                                if (json && json.potentialAction) {
                                    data.push(json);
                                }
                            } catch (e) {}
                        });
                        
                        // Look for window data that might contain route information
                        const windowDataKeys = ['APP_INITIALIZATION_STATE', 'APP_OPTIONS'];
                        windowDataKeys.forEach(key => {
                            if (window[key] && typeof window[key] === 'object') {
                                try {
                                    const str = JSON.stringify(window[key]);
                                    const transitMatches = str.match(/"([0-9A-Z]).*?(?:St|Ave|Street|Avenue|Station).*?"/gi);
                                    if (transitMatches) {
                                        data.push({ windowData: key, matches: transitMatches });
                                    }
                                } catch (e) {}
                            }
                        });
                        
                        return data;
                    }
                """)
                
                print(f"📋 Found {len(structured_data)} structured data elements")
                
                # Process the extracted data into ParsedRide objects
                rides = await process_extracted_data(route_segments, structured_data)
                
            finally:
                await browser.close()
                
    except Exception as e:
        print(f"❌ Browser extraction error: {str(e)}")
        
        # Fallback to the old URL-based extraction if browser fails
        print("🔄 Falling back to URL-based extraction...")
        try:
            rides = extract_transit_info_from_url_fallback(url)
        except Exception as fallback_error:
            print(f"❌ Fallback extraction also failed: {fallback_error}")
    
    print(f"✅ Extracted {len(rides)} rides using headless browser")
    return rides

async def process_extracted_data(route_segments: List[dict], structured_data: List[dict]) -> List[ParsedRide]:
    """Process the extracted data into ParsedRide objects"""
    rides = []
    
    print("🔄 Processing extracted transit data...")
    
    # Process route segments
    current_stations = []
    current_line = None
    
    for segment in route_segments:
        print(f"   📍 Processing segment: {segment.get('text', '')[:50]}...")
        
        # Extract line information
        line = segment.get('line')
        if line and re.match(r'^[0-9A-Z]$', line):
            current_line = line
            print(f"      🚇 Found line: {line}")
        
        # Extract station information
        stations = segment.get('stations', [])
        for station in stations:
            # Clean and normalize station name
            clean_station = normalize_stop_name(station.strip())
            if len(clean_station) > 3 and clean_station not in current_stations:
                current_stations.append(clean_station)
                print(f"      🏢 Found station: {clean_station}")
    
    # Create rides from consecutive station pairs
    if current_line and len(current_stations) >= 2:
        for i in range(len(current_stations) - 1):
            ride = ParsedRide(
                line=current_line,
                board_stop=current_stations[i],
                depart_stop=current_stations[i + 1],
                transferred=False,
                confidence=85  # High confidence from browser extraction
            )
            rides.append(ride)
            print(f"   ✅ Created ride: {current_line} from {current_stations[i]} to {current_stations[i + 1]}")
    
    # Process structured data if no rides found
    if not rides and structured_data:
        print("   🔍 Processing structured data as fallback...")
        
        for data in structured_data:
            if 'matches' in data:
                # Extract potential transit information from window data
                matches = data['matches']
                for match in matches[:5]:  # Limit to prevent noise
                    # Try to extract line and station from the match
                    line_match = re.search(r'([0-9A-Z])', match)
                    station_match = re.search(r'([^"]*(?:St|Ave|Station)[^"]*)', match)
                    
                    if line_match and station_match:
                        line = line_match.group(1)
                        station = normalize_stop_name(station_match.group(1))
                        
                        # This is a basic extraction - in practice you'd need more sophisticated pairing
                        if len(station) > 3:
                            print(f"      📊 Found structured data: {line} - {station}")
    
    return rides

def extract_transit_info_from_url_fallback(url: str) -> List[ParsedRide]:
    """Fallback URL-based extraction for when browser method fails"""
    print("🔄 Using fallback URL extraction...")
    
    # Simple URL pattern matching as fallback
    try:
        parsed_url = urlparse(url)
        url_text = unquote(str(parsed_url)).lower()
        
        # Look for origin and destination in URL structure
        dir_match = re.search(r'/dir/([^/]+)/([^/]+)', url_text)
        if dir_match:
            origin = dir_match.group(1).replace('+', ' ').replace('%20', ' ')
            destination = dir_match.group(2).replace('+', ' ').replace('%20', ' ')
            
            print(f"   📍 URL fallback found route: {origin} → {destination}")
            
            # Return basic ride with placeholders for user editing
            return [ParsedRide(
                line="?",  # User will need to specify
                board_stop=normalize_stop_name(' '.join(origin.replace(',', '').split()[:3])),  # First few words
                depart_stop=normalize_stop_name(' '.join(destination.replace(',', '').split()[:3])),
                transferred=False,
                confidence=30  # Low confidence, needs user verification
            )]
    
    except Exception as e:
        print(f"❌ Fallback extraction error: {e}")
    
    return []

def extract_from_maps_page(html_content: str, full_url: str = "") -> List[ParsedRide]:
    """Simplified fallback parsing - mainly for backwards compatibility"""
    print("   📝 Simplified HTML parsing (fallback only)...")
    return []

def extract_from_directions_data(html_content: str) -> List[ParsedRide]:
    """Simplified fallback parsing - mainly for backwards compatibility"""
    print("   📊 Simplified JSON analysis (fallback only)...")
    return []

def extract_by_station_matching(html_content: str) -> List[ParsedRide]:
    """Disabled to prevent CSS/JS false positives"""
    print("   🗺️  Station matching disabled to prevent false positives...")
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
    """Test database connectivity and return some basic info"""
    try:
        # Test query
        result = db.execute(text("SELECT COUNT(*) as count FROM rides")).fetchone()
        return {"status": "connected", "ride_count": result.count if result else 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {str(e)}")

@app.post("/add-test-data")
def add_test_data(db: Session = Depends(get_db)):
    """Add test data to verify database functionality"""
    try:
        # Get the next ride number
        max_ride = db.query(func.max(SubwayRide.ride_number)).scalar()
        next_ride_number = (max_ride or 0) + 1
        
        # Create test ride
        test_ride = SubwayRide(
            ride_number=next_ride_number,
            line="TEST",
            board_stop="Test Station A",
            depart_stop="Test Station B",
            date=date.today(),
            transferred=False
        )
        
        db.add(test_ride)
        db.commit()
        db.refresh(test_ride)
        
        return {"message": "Test data added", "ride_id": test_ride.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add test data: {str(e)}")

@app.post("/rides/")
def create_ride(ride: RideCreate, db: Session = Depends(get_db)):
    # Get the next ride number
    max_ride = db.query(func.max(SubwayRide.ride_number)).scalar()
    next_ride_number = (max_ride or 0) + 1
    
    try:
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
        
        return {"message": "Ride created successfully", "ride_id": db_ride.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating ride: {str(e)}")

@app.post("/parse-url/")
async def parse_google_maps_url(request: UrlParseRequest):
    """Parse Google Maps URL to extract subway route information"""
    try:
        print(f"Received URL: {request.url}")
        
        # Extract transit information using await instead of asyncio.run
        rides = await extract_transit_info_from_url(request.url)
        
        if not rides:
            raise HTTPException(status_code=400, detail="No transit information found in the provided URL. Please ensure the URL contains subway/transit directions.")
        
        return {"rides": [ride.dict() for ride in rides]}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse Google Maps URL: {str(e)}")

@app.get("/rides/")
def get_all_rides(db: Session = Depends(get_db)):
    """Get all rides with enhanced debugging"""
    try:
        print("🔍 Fetching rides from database...")
        
        rides = db.query(SubwayRide).order_by(SubwayRide.ride_number.desc()).all()
        
        print(f"📊 Found {len(rides)} rides in database")
        
        rides_data = []
        for ride in rides:
            rides_data.append({
                "id": ride.id,
                "ride_number": ride.ride_number,
                "line": ride.line,
                "board_stop": ride.board_stop,
                "depart_stop": ride.depart_stop,
                "date": ride.date.isoformat(),
                "transferred": ride.transferred
            })
        
        print("✅ Returning rides to frontend")
        return {"rides": rides_data}
        
    except Exception as e:
        print(f"❌ Error fetching rides: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching rides: {str(e)}")

@app.get("/rides/export")
def export_rides_csv(db: Session = Depends(get_db)):
    """Export all rides as CSV"""
    rides = db.query(SubwayRide).order_by(SubwayRide.ride_number.desc()).all()
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Ride #", "Line", "Board Stop", "Depart Stop", "Date", "Transferred"])
    
    for ride in rides:
        writer.writerow([ride.ride_number, ride.line, ride.board_stop, ride.depart_stop, ride.date, ride.transferred])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subway_rides.csv"}
    )

@app.get("/rides/{ride_id}")
def get_ride(ride_id: int, db: Session = Depends(get_db)):
    ride = db.query(SubwayRide).filter(SubwayRide.id == ride_id).first()
    if ride is None:
        raise HTTPException(status_code=404, detail="Ride not found")
    return ride

@app.delete("/rides/{ride_id}")
def delete_ride(ride_id: int, db: Session = Depends(get_db)):
    ride = db.query(SubwayRide).filter(SubwayRide.id == ride_id).first()
    if ride is None:
        raise HTTPException(status_code=404, detail="Ride not found")
    
    try:
        db.delete(ride)
        db.commit()
        return {"message": f"Ride {ride_id} deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting ride: {str(e)}")

@app.delete("/rides/")
def clear_all_rides(db: Session = Depends(get_db)):
    try:
        deleted_count = db.query(SubwayRide).delete()
        db.commit()
        return {"message": f"Deleted {deleted_count} rides"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error clearing rides: {str(e)}")