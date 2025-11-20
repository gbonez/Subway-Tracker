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
import json
from urllib.parse import urlparse, unquote, parse_qs
from typing import List
import asyncio
import subprocess
import sys
from playwright.async_api import async_playwright

# Google Maps API Configuration
GOOGLE_MAPS_API_KEY = "AIzaSyDAHi8BNX3Fp3WxcOtAWg1fuzBWSBB7J4w"

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
        DATABASE_URL = "sqlite:///./rides.db"
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
    """Extract transit information from Google Maps URL using API or browser fallback"""
    # First try Google Maps API
    api_result = await extract_transit_info_with_api(url)
    if api_result:
        return api_result
    
    # Fallback to browser scraping if API fails
    print("🔄 API extraction failed, falling back to browser scraping...")
    return await extract_transit_info_async(url)

async def extract_transit_info_with_api(url: str) -> List[ParsedRide]:
    """Extract transit information using Google Maps Directions API"""
    rides = []
    
    try:
        print(f"🗺️ Using Google Maps API to extract transit info from: {url}")
        
        # Parse the Google Maps URL to extract origin and destination
        origin, destination = parse_google_maps_url(url)
        if not origin or not destination:
            print("❌ Could not parse origin/destination from URL")
            return []
        
        print(f"📍 Origin: {origin}")
        print(f"🎯 Destination: {destination}")
        
        # Call Google Maps Directions API
        directions_url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            'origin': origin,
            'destination': destination,
            'mode': 'transit',
            'transit_mode': 'subway',
            'alternatives': 'true',
            'key': GOOGLE_MAPS_API_KEY
        }
        
        print("🌐 Calling Google Maps Directions API...")
        response = requests.get(directions_url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if data['status'] != 'OK':
            print(f"❌ Google Maps API error: {data.get('status')} - {data.get('error_message', 'Unknown error')}")
            
            # Provide helpful error messages
            if data['status'] == 'REQUEST_DENIED':
                print("💡 To fix this:")
                print("   1. Go to Google Cloud Console: https://console.cloud.google.com/")
                print("   2. Enable the 'Directions API' for your project")
                print("   3. Make sure your API key has permissions for Directions API")
                print("   4. You may also need to enable 'Places API' and 'Geocoding API'")
            elif data['status'] == 'OVER_QUERY_LIMIT':
                print("💡 API quota exceeded. Check your usage limits in Google Cloud Console.")
            elif data['status'] == 'ZERO_RESULTS':
                print("💡 No transit routes found between the specified locations.")
            
            return []
        
        print(f"✅ Found {len(data.get('routes', []))} route(s)")
        
        # Process the routes to extract subway rides
        rides = process_api_routes(data.get('routes', []))
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error calling Google Maps API: {str(e)}")
    except Exception as e:
        print(f"❌ Error processing Google Maps API response: {str(e)}")
    
    return rides

def parse_google_maps_url(url: str) -> tuple:
    """Parse Google Maps URL to extract origin and destination"""
    try:
        # Handle different Google Maps URL formats
        parsed_url = urlparse(url)
        
        # Method 1: Check query parameters
        query_params = parse_qs(parsed_url.query)
        
        # Look for 'saddr' (source address) and 'daddr' (destination address)
        if 'saddr' in query_params and 'daddr' in query_params:
            origin = query_params['saddr'][0]
            destination = query_params['daddr'][0]
            return origin, destination
        
        # Method 2: Check for directions in path
        if '/dir/' in parsed_url.path:
            # Extract coordinates or addresses from the path
            path_parts = parsed_url.path.split('/dir/')[1].split('/')
            if len(path_parts) >= 2:
                origin = unquote(path_parts[0])
                destination = unquote(path_parts[1])
                return origin, destination
        
        # Method 3: Look for coordinates in fragment or query
        fragment = parsed_url.fragment or ""
        if '!1m' in fragment:
            # Extract coordinates from the complex fragment format
            coords = extract_coords_from_fragment(fragment)
            if coords:
                return coords
        
        # Method 4: Check for data parameter with coordinates
        if 'data' in query_params:
            coords = extract_coords_from_data(query_params['data'][0])
            if coords:
                return coords
        
        print("❌ Could not extract origin/destination from URL format")
        return None, None
        
    except Exception as e:
        print(f"❌ Error parsing Google Maps URL: {str(e)}")
        return None, None

def extract_coords_from_fragment(fragment: str) -> tuple:
    """Extract coordinates from Google Maps fragment"""
    try:
        # Look for coordinate patterns in the fragment
        import re
        coord_pattern = r'(-?\d+\.\d+),(-?\d+\.\d+)'
        matches = re.findall(coord_pattern, fragment)
        
        if len(matches) >= 2:
            # First match is typically origin, second is destination
            origin = f"{matches[0][0]},{matches[0][1]}"
            destination = f"{matches[1][0]},{matches[1][1]}"
            return origin, destination
        
        return None, None
    except:
        return None, None

def extract_coords_from_data(data_param: str) -> tuple:
    """Extract coordinates from data parameter"""
    try:
        # Decode and parse data parameter
        decoded = unquote(data_param)
        coord_pattern = r'(-?\d+\.\d+),(-?\d+\.\d+)'
        matches = re.findall(coord_pattern, decoded)
        
        if len(matches) >= 2:
            origin = f"{matches[0][0]},{matches[0][1]}"
            destination = f"{matches[1][0]},{matches[1][1]}"
            return origin, destination
        
        return None, None
    except:
        return None, None

def process_api_routes(routes: list) -> List[ParsedRide]:
    """Process Google Maps API route data to extract subway rides"""
    rides = []
    
    try:
        for route_idx, route in enumerate(routes):
            print(f"📍 Processing route {route_idx + 1}")
            
            legs = route.get('legs', [])
            for leg_idx, leg in enumerate(legs):
                print(f"🦵 Processing leg {leg_idx + 1}")
                
                steps = leg.get('steps', [])
                current_line = None
                current_stations = []
                
                for step_idx, step in enumerate(steps):
                    travel_mode = step.get('travel_mode', '')
                    
                    if travel_mode == 'TRANSIT':
                        transit_details = step.get('transit_details', {})
                        line_info = transit_details.get('line', {})
                        
                        # Extract line information
                        line_name = line_info.get('short_name') or line_info.get('name', '')
                        vehicle_type = line_info.get('vehicle', {}).get('type', '')
                        
                        # Only process subway/metro lines
                        if vehicle_type in ['SUBWAY', 'METRO_RAIL'] or 'subway' in line_name.lower():
                            departure_stop = transit_details.get('departure_stop', {}).get('name', '')
                            arrival_stop = transit_details.get('arrival_stop', {}).get('name', '')
                            
                            print(f"🚇 Found subway step: {line_name} from {departure_stop} to {arrival_stop}")
                            
                            # Clean up station names
                            departure_stop = normalize_stop_name(departure_stop)
                            arrival_stop = normalize_stop_name(arrival_stop)
                            
                            if departure_stop and arrival_stop and line_name:
                                ride = ParsedRide(
                                    line=line_name.upper(),
                                    board_stop=departure_stop,
                                    depart_stop=arrival_stop,
                                    transferred=False,  # We'll detect transfers later
                                    confidence=95  # High confidence from API data
                                )
                                rides.append(ride)
                
        # Post-process to detect transfers
        rides = detect_transfers_in_rides(rides)
        
        print(f"✅ Extracted {len(rides)} subway rides from API data")
        
    except Exception as e:
        print(f"❌ Error processing API routes: {str(e)}")
    
    return rides

def detect_transfers_in_rides(rides: List[ParsedRide]) -> List[ParsedRide]:
    """Detect transfers between consecutive rides"""
    for i in range(len(rides) - 1):
        current_ride = rides[i]
        next_ride = rides[i + 1]
        
        # If current ride's departure stop is the same as next ride's boarding stop,
        # it's likely a transfer
        if (current_ride.depart_stop.lower() == next_ride.board_stop.lower() or 
            similar_station_names(current_ride.depart_stop, next_ride.board_stop)):
            current_ride.transferred = True
    
    return rides

def similar_station_names(name1: str, name2: str) -> bool:
    """Check if two station names are similar (for transfer detection)"""
    name1 = name1.lower().replace('-', ' ').replace('/', ' ').strip()
    name2 = name2.lower().replace('-', ' ').replace('/', ' ').strip()
    
    # Remove common suffixes
    suffixes = ['station', 'st', 'ave', 'avenue', 'sq', 'square']
    for suffix in suffixes:
        name1 = name1.replace(f' {suffix}', '').replace(f'{suffix} ', '')
        name2 = name2.replace(f' {suffix}', '').replace(f'{suffix} ', '')
    
    # Check if names are very similar
    return name1 == name2 or name1 in name2 or name2 in name1

def load_subway_stations() -> dict:
    """Load all subway stations from stopsByLine.js data"""
    # This would normally load from the JS file, but we'll include the data here
    # for now since we can't easily parse JS from Python
    stops_by_line = {
        "1": ["103 St", "116 St-Columbia University", "125 St", "137 St-City College", "14 St", "145 St", "157 St", "168 St-Washington Hts", "18 St", "181 St", "191 St", "207 St", "215 St", "23 St", "231 St", "238 St", "28 St", "34 St-Penn Station", "50 St", "59 St-Columbus Circle", "66 St-Lincoln Center", "72 St", "79 St", "86 St", "96 St", "Canal St", "Cathedral Pkwy (110 St)", "Chambers St", "Christopher St-Stonewall", "Dyckman St", "Franklin St", "Houston St", "Marble Hill-225 St", "Rector St", "South Ferry", "Times Sq-42 St", "Van Cortlandt Park-242 St", "WTC Cortlandt"],
        "2": ["116 St", "125 St", "135 St", "14 St", "149 St-Grand Concourse", "174 St", "219 St", "225 St", "233 St", "3 Ave-149 St", "34 St-Penn Station", "72 St", "96 St", "Allerton Ave", "Atlantic Ave-Barclays Ctr", "Bergen St", "Beverly Rd", "Borough Hall", "Bronx Park East", "Burke Ave", "Central Park North (110 St)", "Chambers St", "Church Ave", "Clark St", "E 180 St", "Eastern Pkwy-Brooklyn Museum", "Flatbush Ave-Brooklyn College", "Franklin Ave-Medgar Evers College", "Freeman St", "Fulton St", "Grand Army Plaza", "Gun Hill Rd", "Hoyt St", "Intervale Ave", "Jackson Ave", "Nereid Ave", "Nevins St", "Newkirk Ave-Little Haiti", "Park Place", "Pelham Pkwy", "President St-Medgar Evers College", "Prospect Ave", "Simpson St", "Sterling St", "Times Sq-42 St", "Wakefield-241 St", "Wall St", "West Farms Sq-E Tremont Ave", "Winthrop St"],
        "3": ["116 St", "125 St", "135 St", "14 St", "145 St", "34 St-Penn Station", "72 St", "96 St", "Atlantic Ave-Barclays Ctr", "Bergen St", "Borough Hall", "Central Park North (110 St)", "Chambers St", "Clark St", "Crown Hts-Utica Ave", "Eastern Pkwy-Brooklyn Museum", "Franklin Ave-Medgar Evers College", "Fulton St", "Grand Army Plaza", "Harlem-148 St", "Hoyt St", "Junius St", "Kingston Ave", "Nevins St", "New Lots Ave", "Nostrand Ave", "Park Place", "Pennsylvania Ave", "Rockaway Ave", "Saratoga Ave", "Sutter Ave-Rutland Rd", "Times Sq-42 St", "Van Siclen Ave", "Wall St"],
        "4": ["125 St", "138 St-Grand Concourse", "14 St-Union Sq", "149 St-Grand Concourse", "161 St-Yankee Stadium", "167 St", "170 St", "176 St", "183 St", "59 St", "86 St", "Atlantic Ave-Barclays Ctr", "Bedford Park Blvd-Lehman College", "Borough Hall", "Bowling Green", "Brooklyn Bridge-City Hall", "Burnside Ave", "Crown Hts-Utica Ave", "Fordham Rd", "Franklin Ave-Medgar Evers College", "Fulton St", "Grand Central-42 St", "Kingsbridge Rd", "Mosholu Pkwy", "Mt Eden Ave", "Nevins St", "Wall St", "Woodlawn"],
        "5": ["125 St", "138 St-Grand Concourse", "14 St-Union Sq", "149 St-Grand Concourse", "174 St", "219 St", "225 St", "233 St", "3 Ave-149 St", "59 St", "86 St", "Allerton Ave", "Atlantic Ave-Barclays Ctr", "Baychester Ave", "Beverly Rd", "Borough Hall", "Bowling Green", "Bronx Park East", "Brooklyn Bridge-City Hall", "Burke Ave", "Church Ave", "E 180 St", "Eastchester-Dyre Ave", "Flatbush Ave-Brooklyn College", "Franklin Ave-Medgar Evers College", "Freeman St", "Fulton St", "Grand Central-42 St", "Gun Hill Rd", "Intervale Ave", "Jackson Ave", "Morris Park", "Nereid Ave", "Nevins St", "Newkirk Ave-Little Haiti", "Pelham Pkwy", "President St-Medgar Evers College", "Prospect Ave", "Simpson St", "Sterling St", "Wall St", "West Farms Sq-E Tremont Ave", "Winthrop St"],
        "6": ["103 St", "110 St", "116 St", "125 St", "14 St-Union Sq", "23 St", "28 St", "3 Ave-138 St", "33 St", "51 St", "59 St", "68 St-Hunter College", "77 St", "86 St", "96 St", "Astor Pl", "Bleecker St", "Brook Ave", "Brooklyn Bridge-City Hall", "Buhre Ave", "Canal St", "Castle Hill Ave", "Cypress Ave", "E 143 St-St Mary's St", "E 149 St", "Elder Ave", "Grand Central-42 St", "Hunts Point Ave", "Longwood Ave", "Middletown Rd", "Morrison Ave-Soundview", "Parkchester", "Pelham Bay Park", "Spring St", "St Lawrence Ave", "Westchester Sq-E Tremont Ave", "Whitlock Ave", "Zerega Ave"],
        "7": ["103 St-Corona Plaza", "111 St", "33 St-Rawson St", "34 St-Hudson Yards", "40 St-Lowery St", "46 St-Bliss St", "5 Ave", "52 St", "61 St-Woodside", "69 St", "74 St-Broadway", "82 St-Jackson Hts", "90 St-Elmhurst Ave", "Court Sq", "Flushing-Main St", "Grand Central-42 St", "Hunters Point Ave", "Junction Blvd", "Mets-Willets Point", "Queensboro Plaza", "Times Sq-42 St", "Vernon Blvd-Jackson Ave"],
        "N": ["14 St-Union Sq", "18 Ave", "20 Ave", "30 Ave", "34 St-Herald Sq", "36 Ave", "36 St", "39 Ave-Dutch Kills", "49 St", "5 Ave/59 St", "57 St-7 Ave", "59 St", "8 Ave", "86 St", "Astoria Blvd", "Astoria-Ditmars Blvd", "Atlantic Ave-Barclays Ctr", "Aveenue U", "Bay Pkwy", "Broadway", "Canal St", "Coney Island-Stillwell Ave", "Fort Hamilton Pkwy", "Kings Hwy", "Lexington Ave/59 St", "New Utrecht Ave", "Queensboro Plaza", "Times Sq-42 St"],
        "Q": ["14 St-Union Sq", "34 St-Herald Sq", "57 St-7 Ave", "7 Ave", "72 St", "86 St", "96 St", "Atlantic Ave-Barclays Ctr", "Aveenue H", "Aveenue J", "Aveenue M", "Aveenue U", "Beverley Rd", "Brighton Beach", "Canal St", "Church Ave", "Coney Island-Stillwell Ave", "Cortelyou Rd", "DeKalb Ave", "Kings Hwy", "Lexington Ave/63 St", "Neck Rd", "Newkirk Plaza", "Ocean Pkwy", "Parkside Ave", "Prospect Park", "Sheepshead Bay", "Times Sq-42 St", "W 8 St-NY Aquarium"],
        "R": ["14 St-Union Sq", "23 St", "25 St", "28 St", "34 St-Herald Sq", "36 St", "4 Ave-9 St", "45 St", "46 St", "49 St", "5 Ave/59 St", "53 St", "57 St-7 Ave", "59 St", "63 Dr-Rego Park", "65 St", "67 Ave", "77 St", "8 St-NYU", "86 St", "Atlantic Ave-Barclays Ctr", "Bay Ridge Ave", "Bay Ridge-95 St", "Canal St", "City Hall", "Cortlandt St", "Court St", "DeKalb Ave", "Elmhurst Ave", "Forest Hills-71 Ave", "Grand Ave-Newtown", "Jackson Hts-Roosevelt Ave", "Jay St-MetroTech", "Lexington Ave/59 St", "Northern Blvd", "Prince St", "Prospect Ave", "Queens Plaza", "Rector St", "Steinway St", "Times Sq-42 St", "Union St", "Whitehall St-South Ferry", "WoodhAveen Blvd"],
        "W": ["14 St-Union Sq", "23 St", "28 St", "30 Ave", "34 St-Herald Sq", "36 Ave", "39 Ave-Dutch Kills", "49 St", "5 Ave/59 St", "57 St-7 Ave", "8 St-NYU", "Astoria Blvd", "Astoria-Ditmars Blvd", "Broadway", "Canal St", "City Hall", "Cortlandt St", "Lexington Ave/59 St", "Prince St", "Queensboro Plaza", "Rector St", "Times Sq-42 St", "Whitehall St-South Ferry"],
        "B": ["103 St", "116 St", "125 St", "135 St", "145 St", "155 St", "161 St-Yankee Stadium", "167 St", "170 St", "174-175 Sts", "182-183 Sts", "34 St-Herald Sq", "42 St-Bryant Pk", "47-50 Sts-Rockefeller Ctr", "59 St-Columbus Circle", "7 Ave", "72 St", "81 St-Museum of Natural History", "86 St", "96 St", "Atlantic Ave-Barclays Ctr", "Bedford Park Blvd", "Brighton Beach", "Broadway-Lafayette St", "Cathedral Pkwy (110 St)", "Church Ave", "DeKalb Ave", "Fordham Rd", "Grand St", "Kings Hwy", "Kingsbridge Rd", "Newkirk Plaza", "Prospect Park", "Sheepshead Bay", "Tremont Ave", "W 4 St-Wash Sq"],
        "D": ["125 St", "145 St", "155 St", "161 St-Yankee Stadium", "167 St", "170 St", "174-175 Sts", "18 Ave", "182-183 Sts", "20 Ave", "25 Ave", "34 St-Herald Sq", "36 St", "42 St-Bryant Pk", "47-50 Sts-Rockefeller Ctr", "50 St", "55 St", "59 St-Columbus Circle", "62 St", "7 Ave", "71 St", "79 St", "9 Ave", "Atlantic Ave-Barclays Ctr", "Bay 50 St", "Bay Pkwy", "Bedford Park Blvd", "Broadway-Lafayette St", "Coney Island-Stillwell Ave", "Fordham Rd", "Fort Hamilton Pkwy", "Grand St", "Kingsbridge Rd", "Norwood-205 St", "Tremont Ave", "W 4 St-Wash Sq"],
        "F": ["14 St", "15 St-Prospect Park", "169 St", "18 Ave", "2 Ave", "21 St-Queensbridge", "23 St", "34 St-Herald Sq", "4 Ave-9 St", "42 St-Bryant Pk", "47-50 Sts-Rockefeller Ctr", "57 St", "7 Ave", "75 Ave", "Avenue I", "Avenue N", "Avenue P", "Avenue U", "Avenue X", "Bay Pkwy", "Bergen St", "Briarwood", "Broadway-Lafayette St", "Carroll St", "Church Ave", "Coney Island-Stillwell Ave", "Delancey St-Essex St", "Ditmas Ave", "East Broadway", "Forest Hills-71 Ave", "Fort Hamilton Pkwy", "Jackson Hts-Roosevelt Ave", "Jamaica-179 St", "Jay St-MetroTech", "Kew Gardens-Union Tpke", "Kings Hwy", "Lexington Ave/63 St", "Neptune Ave", "Parsons Blvd", "Roosevelt Island", "Smith-9 Sts", "Sutphin Blvd", "W 4 St-Wash Sq", "W 8 St-NY Aquarium", "York St"],
        "G": ["15 St-Prospect Park", "21 St", "4 Ave-9 St", "7 Ave", "Bedford-Nostrand Aves", "Bergen St", "Broadway", "Carroll St", "Church Ave", "Classon Ave", "Clinton-Washington Aves", "Court Sq", "Flushing Ave", "Fort Hamilton Pkwy", "Fulton St", "Greenpoint Ave", "Hoyt-Schermerhorn Sts", "Metropolitan Ave", "Myrtle-Willoughby Aves", "Nassau Ave", "Smith-9 Sts"],
        "L": ["1 Ave", "14 St-Union Sq", "3 Ave", "6 Ave", "8 Ave", "Atlantic Ave", "Bedford Ave", "Broadway Junction", "Bushwick Ave-Aberdeen St", "Canarsie-Rockaway Pkwy", "DeKalb Ave", "East 105 St", "Graham Ave", "Grand St", "Halsey St", "Jefferson St", "Livonia Ave", "Lorimer St", "Montrose Ave", "Morgan Ave", "Myrtle-Wyckoff Aves", "New Lots Ave", "Sutter Ave", "Wilson Ave"]
    }
    
    # Create a flat list of all stations with their lines
    all_stations = []
    for line, stations in stops_by_line.items():
        for station in stations:
            all_stations.append({'station': station, 'line': line})
    
    return all_stations

def find_matching_stations(extracted_name: str, all_stations: list) -> list:
    """Find matching subway stations for an extracted name"""
    matches = []
    extracted_clean = clean_station_name(extracted_name)
    
    print(f"🔍 Looking for matches for: '{extracted_name}' (cleaned: '{extracted_clean}')")
    
    # Exact matches first
    for station_info in all_stations:
        station_clean = clean_station_name(station_info['station'])
        if station_clean == extracted_clean:
            matches.append({'station': station_info['station'], 'line': station_info['line'], 'confidence': 100})
    
    # If no exact matches, look for partial matches
    if not matches:
        for station_info in all_stations:
            station_clean = clean_station_name(station_info['station'])
            
            # Check if extracted name is contained in station name or vice versa
            if extracted_clean in station_clean or station_clean in extracted_clean:
                confidence = 80 if len(extracted_clean) > 3 else 60
                matches.append({'station': station_info['station'], 'line': station_info['line'], 'confidence': confidence})
            
            # Check for word matches (for complex station names)
            extracted_words = set(extracted_clean.split())
            station_words = set(station_clean.split())
            common_words = extracted_words & station_words
            if common_words and len(common_words) >= max(1, len(extracted_words) // 2):
                confidence = 60
                matches.append({'station': station_info['station'], 'line': station_info['line'], 'confidence': confidence})
    
    # Remove duplicates and sort by confidence
    unique_matches = {}
    for match in matches:
        key = f"{match['station']}_{match['line']}"
        if key not in unique_matches or match['confidence'] > unique_matches[key]['confidence']:
            unique_matches[key] = match
    
    sorted_matches = sorted(unique_matches.values(), key=lambda x: x['confidence'], reverse=True)
    return sorted_matches[:5]  # Return top 5 matches

def clean_station_name(name: str) -> str:
    """Clean and normalize station name for matching"""
    # Remove common prefixes and suffixes
    clean_name = name.lower()
    
    # Remove common address components
    clean_name = re.sub(r'\b(street|st|avenue|ave|road|rd|boulevard|blvd|plaza|square|sq)\b', '', clean_name)
    clean_name = re.sub(r'\b(station|subway|stop)\b', '', clean_name)
    clean_name = re.sub(r'\b(new york|ny|brooklyn|manhattan|queens|bronx)\b', '', clean_name)
    clean_name = re.sub(r'\b(building|tower|center|centre)\b', '', clean_name)
    clean_name = re.sub(r'\d+\s*(w|e|n|s|west|east|north|south)\s*\d+\w*', '', clean_name)  # Remove addresses like "20 W 34th St"
    
    # Remove extra whitespace and punctuation
    clean_name = re.sub(r'[^\w\s]', ' ', clean_name)
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
    
    return clean_name

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
                # Create browser context with user agent
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                              '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = await context.new_page()
                
                print("📄 Navigating to Google Maps URL...")
                
                # Navigate to the URL with more lenient timeout and wait conditions
                try:
                    # First try with domcontentloaded (faster)
                    await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                    print("✅ Page DOM loaded, waiting for content...")
                    
                    # Give it a moment for JavaScript to initialize
                    await page.wait_for_timeout(3000)
                    
                except Exception as nav_error:
                    print(f"⚠️ DOM load failed ({nav_error}), trying with basic load...")
                    # Fallback: try with just 'load' event
                    await page.goto(url, wait_until='load', timeout=15000)
                
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
                    'div[role="main"]',  # Main content area
                    '[data-value]',      # Any data-value elements
                ]
                
                # Wait for any transit-related element to appear (with shorter timeout)
                page_ready = False
                for selector in transit_selectors:
                    try:
                        await page.wait_for_selector(selector, timeout=3000)
                        print(f"✅ Found element: {selector}")
                        page_ready = True
                        break
                    except:
                        continue
                
                if not page_ready:
                    print("⚠️ No specific transit elements found, but proceeding with extraction...")
                    # Give the page more time to load content
                    await page.wait_for_timeout(5000)
                
                # Extract transit information using multiple strategies
                print("🔍 Extracting transit route information...")
                
                # First, let's see what's actually on the page
                page_title = await page.title()
                page_url = page.url
                print(f"📋 Page title: {page_title}")
                print(f"🔗 Current URL: {page_url}")
                
                # Strategy 1: Look for transit route segments
                route_segments = await page.evaluate("""
                    () => {
                        const segments = [];
                        
                        // Log what elements we can find
                        console.log('=== Page Analysis ===');
                        console.log('Document ready state:', document.readyState);
                        console.log('Document title:', document.title);
                        
                        // Look for transit route information in various formats
                        const transitElements = document.querySelectorAll(
                            '[data-trip-index], .transit-route-segment, .directions-step, [aria-label*="subway"], [aria-label*="train"], [aria-label*="transit"], [data-value], div[role="main"]'
                        );
                        
                        console.log('Found transit-like elements:', transitElements.length);
                        
                        transitElements.forEach((element, index) => {
                            const text = element.innerText || element.textContent || '';
                            const ariaLabel = element.getAttribute('aria-label') || '';
                            const dataAttrs = Array.from(element.attributes)
                                .filter(attr => attr.name.startsWith('data-'))
                                .map(attr => `${attr.name}="${attr.value}"`).join(' ');
                            
                            // Log first few elements for debugging
                            if (index < 5) {
                                console.log(`Element ${index}:`, {
                                    tag: element.tagName,
                                    text: text.slice(0, 100),
                                    ariaLabel: ariaLabel.slice(0, 100),
                                    dataAttrs: dataAttrs
                                });
                            }
                            
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
                        
                        console.log('=== End Analysis ===');
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
    """Process the extracted data into ParsedRide objects with station matching"""
    rides = []
    
    print("🔄 Processing extracted transit data...")
    
    # Load subway station data for matching
    all_stations = load_subway_stations()
    
    # Process route segments
    potential_stations = []
    current_line = None
    
    for segment in route_segments:
        segment_text = segment.get('text', '')
        aria_label = segment.get('ariaLabel', '')
        line = segment.get('line')
        stations = segment.get('stations', [])
        
        print(f"   📍 Processing segment: {segment_text[:100]}")
        
        if line:
            current_line = line
            print(f"      🚇 Found line: {line}")
        
        # Extract potential station names from text and aria labels
        combined_text = f"{segment_text} {aria_label}"
        
        # Look for station patterns in the text
        station_patterns = [
            r'([^(\n]*(?:St|Street|Ave|Avenue|Sq|Square|Plaza|Station|Terminal|Bridge|Park)[^)\n]*)',
            r'(\d+\s*St)',  # numbered streets
            r'([A-Za-z\s]+(?:-[A-Za-z\s]+)*(?:\s+St|\s+Ave|\s+Square|\s+Plaza))',
        ]
        
        for pattern in station_patterns:
            matches = re.findall(pattern, combined_text, re.IGNORECASE)
            for match in matches:
                if len(match.strip()) > 3:  # Filter out very short matches
                    potential_stations.append(match.strip())
                    print(f"      🏢 Found station: {match.strip()}")
    
    # Process structured data for additional stations
    for data in structured_data:
        if isinstance(data, dict):
            print(f"   🔍 Processing structured data as fallback...")
            for key, value in data.items():
                if isinstance(value, (str, list)):
                    text = str(value) if isinstance(value, str) else ' '.join(map(str, value))
                    print(f"      📊 Found structured data: {key} - {text[:50]}")
                    
                    # Look for station names in structured data
                    station_matches = re.findall(r'([^,\n]*(?:St|Ave|Station|Square|Plaza)[^,\n]*)', text, re.IGNORECASE)
                    for match in station_matches:
                        if len(match.strip()) > 3:
                            potential_stations.append(match.strip())
    
    # Remove duplicates from potential stations
    unique_stations = list(set(potential_stations))
    print(f"📋 Found {len(unique_stations)} unique potential stations")
    
    # Match each potential station to actual subway stations
    matched_stations = []
    for station_name in unique_stations:
        matches = find_matching_stations(station_name, all_stations)
        if matches:
            best_match = matches[0]  # Take the highest confidence match
            if best_match['confidence'] >= 60:  # Only include confident matches
                matched_stations.append({
                    'original': station_name,
                    'matched': best_match['station'],
                    'line': best_match['line'],
                    'confidence': best_match['confidence']
                })
                print(f"✅ Matched '{station_name}' → '{best_match['station']}' (Line {best_match['line']}, {best_match['confidence']}% confidence)")
            else:
                print(f"❌ Low confidence match for '{station_name}': {matches[0]['station']} ({matches[0]['confidence']}%)")
        else:
            print(f"❌ No match found for '{station_name}'")
    
    # Group stations by line and create rides
    stations_by_line = {}
    for station in matched_stations:
        line = station['line']
        if line not in stations_by_line:
            stations_by_line[line] = []
        stations_by_line[line].append(station)
    
    # Create rides for each line
    for line, line_stations in stations_by_line.items():
        if len(line_stations) >= 2:
            # Sort stations by confidence and take the best ones
            line_stations.sort(key=lambda x: x['confidence'], reverse=True)
            
            # Create a ride from the first to last station on this line
            board_station = line_stations[0]['matched']
            depart_station = line_stations[-1]['matched'] if len(line_stations) > 1 else line_stations[0]['matched']
            
            if board_station != depart_station:  # Don't create rides with same start/end
                ride = ParsedRide(
                    line=line.upper(),
                    board_stop=normalize_stop_name(board_station),
                    depart_stop=normalize_stop_name(depart_station),
                    transferred=False,  # Will be set in post-processing
                    confidence=min([s['confidence'] for s in line_stations])
                )
                rides.append(ride)
                print(f"🚇 Created ride: {line} from {board_station} to {depart_station}")
    
    print(f"✅ Created {len(rides)} rides from extracted data")
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

@app.get("/debug-url-parsing")
def debug_url_parsing(url: str):
    """Debug endpoint to test Google Maps URL parsing"""
    try:
        origin, destination = parse_google_maps_url(url)
        return {
            "url": url,
            "parsed_origin": origin,
            "parsed_destination": destination,
            "success": origin is not None and destination is not None
        }
    except Exception as e:
        return {
            "url": url,
            "error": str(e),
            "success": False
        }

@app.post("/suggest-stations/")
def suggest_stations(request: dict):
    """Suggest matching subway stations for extracted text"""
    try:
        extracted_names = request.get('stations', [])
        all_stations = load_subway_stations()
        
        suggestions = {}
        for name in extracted_names:
            matches = find_matching_stations(name, all_stations)
            suggestions[name] = matches
        
        return {
            "success": True,
            "suggestions": suggestions
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "suggestions": {}
        }

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
        headers={"Content-Disposition": "attachment; filename=rides.csv"}
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

# -------------------------------
# SERVER STARTUP
# -------------------------------
if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment variable, default to 8000
    port = int(os.getenv("PORT", "8000"))
    
    print(f"🚀 Starting server on port {port}...")
    
    # Start the FastAPI server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )